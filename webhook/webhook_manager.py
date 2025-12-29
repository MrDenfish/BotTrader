
import time
from decimal import Decimal
from decimal import ROUND_HALF_UP
from inspect import stack  # debugging
from typing import Optional
from Api_manager.api_exceptions import (InsufficientFundsException, ProductIDException, SizeTooSmallException,
                                        MaintenanceException)
from Api_manager.api_exceptions import RateLimitException, BadRequestException, NotFoundException, InternalServerErrorException
from Api_manager.api_exceptions import UnknownException
from Config.config_manager import CentralConfig as Config
from webhook.webhook_validate_orders import OrderData
from Shared_Utils.logger import get_logger


class WebHookManager:
    _instance = None

    @classmethod
    def get_instance(cls, logger_manager, shared_utils_precision, trade_order_manager, alerts, session):
        """
        Singleton method to ensure only one instance of WebHookManager exists.
        """
        if cls._instance is None:
            cls._instance = cls(logger_manager, shared_utils_precision, trade_order_manager, alerts, session)
        return cls._instance

    def __init__(self, logger_manager, shared_utils_precision, trade_order_manager, alerts, session):
        """
        Initializes the WebHookManager.
        """
        self.config = Config()
        self.test_mode = self.config.test_mode
        self.alerts = alerts
        self.shared_utils_precision = shared_utils_precision
        self.trade_order_manager = trade_order_manager
        self.logger = logger_manager  # 🙂
        self.structured_logger = get_logger('webhook', context={'component': 'webhook_manager'})

        self.session = session

        # Trading parameters
        self._order_size_fiat = Decimal(self.config.order_size_fiat)

    @property
    def order_size(self):
        return self._order_size_fiat

    async def handle_action(self, order_details: OrderData, precision_data: tuple) -> dict:
        """
        Handle the action from the webhook request. Place an order on Coinbase.

        Returns:
            dict: Unified response dictionary (same format used by attempt_order_placement).
        """
        try:
            if order_details.side == 'buy':
                pass
            success, response = await self.trade_order_manager.place_order(order_details, precision_data)
            return response  # Already structured by attempt_order_placement
        except InsufficientFundsException:
            self.logger.warning("Insufficient funds error raised in handle_action.")
            return {
                "success": False,
                "code": "413",
                "message": "Insufficient funds",
                "error_response": {"error": "INSUFFICIENT_FUND"},
            }
        except ProductIDException:
            self.logger.warning("Invalid product ID in handle_action.")
            return {
                "success": False,
                "code": "412",
                "message": "Invalid trading pair",
                "error_response": {"error": "INVALID_PRODUCT_ID"},
            }
        except SizeTooSmallException:
            return {
                "success": False,
                "code": "414",
                "message": "Order size too small",
                "error_response": {"error": "SIZE_TOO_SMALL"},
            }
        except MaintenanceException:
            return {
                "success": False,
                "code": "503",
                "message": "Exchange under maintenance",
                "error_response": {"error": "EXCHANGE_MAINTENANCE"},
            }
        except Exception as e:
            self.logger.error(f"Unhandled error in handle_action: {e}", exc_info=True)
            return {
                "success": False,
                "code": "500",
                "message": f"Unexpected error: {e}",
                "error_response": {"error": str(e)},
            }

    def calculate_order_size_fiat(self, side, order_amount, usd_amount, base_amount, quote_price, base_price, quote_deci, base_deci, fee_info):
        """
        Calculates order size and converts base amount to its USD equivalent (base_value).

        Args:
            side (str): 'buy' or 'sell'
            order_amount (Decimal): Amount of asset to trade
            usd_amount (Decimal): Available USD balance
            base_amount (Decimal): Available crypto balance
            quote_price (Decimal): Price of the quote currency
            base_price (Decimal): Price of the base currency
            quote_deci (int): Precision of quote currency
            base_deci (int): Precision of base currency

        Returns:
            tuple: (base_order_size_fiat, order_amount, base_value)
        """
        try:

            taker_fee = float(fee_info.get('taker', 0.0))
            maker_fee = float(fee_info.get('maker', 0.0))

            base_order_size_fiat = None
            base_value = Decimal(0)  # Default to zero to avoid NoneType errors

            if side == 'buy':
                # Calculate how much base currency we can buy
                base_order_size_fiat = order_amount / (base_price * Decimal(1.001 + taker_fee))  # Adjust for fees
                base_value = base_order_size_fiat * base_price  # The cost in USD should match order_amount

            elif side == 'sell':
                # Ensure we don't sell more than available
                base_value = base_amount * base_price  # Convert crypto amount to USD

            # Convert to proper decimal precision
            formatted_base_decimal = self.shared_utils_precision.get_decimal_format(base_deci)
            formatted_quote_decimal = self.shared_utils_precision.get_decimal_format(quote_deci)

            if base_order_size_fiat is not None:
                base_order_size_fiat = base_order_size_fiat.quantize(formatted_base_decimal, rounding=ROUND_HALF_UP)

            if base_value is not None:
                base_value = base_value.quantize(formatted_quote_decimal, rounding=ROUND_HALF_UP)

            # Debugging logs to confirm values
            self.logger.debug(
                f"Calculated order size: side={side}, base_order_size_fiat={base_order_size_fiat}, order_amount={order_amount}, base_value={base_value}"
            )

            return base_order_size_fiat, order_amount, base_value

        except Exception as e:
            caller_function_name = stack()[1].function  # Debugging
            self.structured_logger.error(
                'calculate_order_size_fiat error',
                extra={
                    'caller_function': caller_function_name,
                    'base_amount': float(base_amount) if base_amount else None,
                    'base_price': float(base_price) if base_price else None,
                    'error': str(e)
                },
                exc_info=True
            )
            self.logger.error(f'calculate_order_size_fiat: An unexpected error occurred: {e}', exc_info=True)
            return None, None, None  # Return safe defaults on error

    def parse_webhook_request(self, request_json: dict) -> Optional[dict]:
        """
        Parses incoming webhook request data and returns a normalized trade data dictionary.

        Args:
            request_json (dict): Raw incoming webhook data.

        Returns:
            dict: Normalized trade data with safe defaults.
        """
        try:
            pair = request_json.get("pair", "UNKNOWN-UNKNOWN").replace("/", "-")
            base_currency, quote_currency = (
                pair.split("-")[0],
                pair.split("-")[1] if "-" in pair else "UNKNOWN"
            )

            # ✅ Normalize side detection (default to SELL if unclear)
            action = request_json.get("action", "").lower()
            side = "buy" if "open" in action or action == "buy" else "sell"

            # ✅ Safe conversion for balances and order size
            quote_avail_balance = Decimal(str(request_json.get("quote_avail_balance", 0)))
            base_avail_balance = (
                Decimal("0")
                if "open" in action
                else Decimal(str(request_json.get("base_avail_to_trade", 0)))
            )

            # ✅ Correct handling of order_amount_fiat
            raw_order_amount = request_json.get("order_amount_fiat")
            if raw_order_amount is not None:
                order_amount_fiat = Decimal(str(raw_order_amount))
            else:
                # Fallback to default bot-configured order size
                order_amount_fiat = getattr(self, "_order_size_fiat", Decimal("0"))

            # ✅ Test mode detection (centralized flag for downstream use)
            trigger = request_json.get("trigger")

            # ✅ Strategy linkage metadata extraction
            score = request_json.get("score", {})
            snapshot_id = request_json.get("snapshot_id")

            return {
                "trading_pair": pair,
                "side": side,
                "quote_avail_balance": quote_avail_balance,
                "order_amount_fiat": order_amount_fiat,
                "base_avail_balance": base_avail_balance,
                "base_currency": base_currency,
                "quote_currency": quote_currency,
                "action": request_json.get("action", "UNKNOWN"),
                "origin": request_json.get("origin", "UNKNOWN"),
                "source": request_json.get("source", "UNKNOWN"),
                "uuid": request_json.get("uuid"),
                "time": int(request_json.get("timestamp", time.time() * 1000)),
                "trigger": trigger,
                "score": score,  # ✅ Strategy linkage
                "snapshot_id": snapshot_id  # ✅ Strategy linkage
            }

        except Exception as e:
            self.logger.error(f"❌ Error parsing webhook request: {e}", exc_info=True)
            return None

    async def handle_webhook_error(self, e, order_details, precision_data):
        """Handle errors that occur while processing an old_webhook request."""
        exception_map = {
            429: RateLimitException,
            400: BadRequestException,
            404: NotFoundException,
            500: InternalServerErrorException,
        }
        extra_error_details = {
            'action': order_details['side'],
            'trading_pair': order_details['trading_pair'],
            'buy_size': order_details['base_order_size_fiat'],
            'formatted_time': order_details['formatted_time'],
        }
        # Map status_code to custom exceptions
        exception_to_raise = exception_map.get(getattr(e, 'status_code', None), UnknownException)

        # Raise the exception and handle it in the except block
        try:
            raise exception_to_raise(
                f"An error occurred with status code: {getattr(e, 'status_code', 'unknown')}, error: {e}",
                extra_error_details)
        except RateLimitException:
            self.logger.error(f'warning', 'handle_webhook_error: Rate limit hit. '
                                                  'Retrying in 60 seconds...')
            time.sleep(60)
            await self.handle_action(order_details, precision_data)

        except (BadRequestException, NotFoundException, InternalServerErrorException, UnknownException) as ex:
            self.logger.error(f'handle_webhook_error: {ex}. Additional info: {ex.errors}')

        except Exception as ex:
            self.logger.error(f'handle_webhook_error: An unhandled exception occurred: {ex}. '
                                                  f'Additional info: {getattr(ex, "errors", "N/A")}')
