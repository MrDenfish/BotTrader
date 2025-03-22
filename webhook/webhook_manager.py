
from decimal import ROUND_HALF_UP
import time
from decimal import Decimal
from datetime import datetime
from Api_manager.api_exceptions import (InsufficientFundsException, ProductIDException, SizeTooSmallException,
                                    MaintenanceException)
from Api_manager.api_exceptions import RateLimitException, BadRequestException, NotFoundException, InternalServerErrorException
from Api_manager.api_exceptions import UnknownException
from Config.config_manager import CentralConfig as Config
from inspect import stack # debugging

class WebHookManager:
    _instance = None

    @classmethod
    def get_instance(cls, logmanager, shared_utils_precision, trade_order_manager, alerts, session):
        """
        Singleton method to ensure only one instance of WebHookManager exists.
        """
        if cls._instance is None:
            cls._instance = cls(logmanager, shared_utils_precision, trade_order_manager, alerts, session)
        return cls._instance

    def __init__(self, logmanager, shared_utils_precision, trade_order_manager, alerts, session):
        """
        Initializes the WebHookManager.
        """
        self.config = Config()
        self.alerts = alerts
        self.shared_utils_precision = shared_utils_precision
        self.trade_order_manager = trade_order_manager
        self.log_manager = logmanager
        self.session = session

        # Trading parameters
        self._order_size = Decimal(self.config.order_size)
        self._taker_fee = Decimal(self.config.taker_fee)
        self._maker_fee = Decimal(self.config.maker_fee)

    @property
    def taker_fee(self):
        return self._taker_fee

    @property
    def maker_fee(self):
        return self._maker_fee

    @property
    def order_size(self):
        return self._order_size

    async def handle_action(self, order_details, precision_data):
        """ Handle the action from the webhook request. Place an order on Coinbase Pro. """
        try:
            order_success, response_msg = await self.trade_order_manager.place_order(order_details, precision_data)
            if order_success:
                return True, {"success": True, "message": "Order successfully placed"}
            else:
                return False, response_msg

        except InsufficientFundsException:
            self.log_manager.info(f'handle_action: Insufficient funds')
            self.alerts.callhome(
                'Insufficient funds', f'Insufficient funds {order_details["trading_pair"]} at '
                                      f'{order_details["formatted_time"]}'
                )
            return False, {"message": "Insufficient funds"}

        except ProductIDException:
            self.log_manager.info(f'handle_action: product id exception')
            self.alerts.callhome(
                'Product ID Exception', f'Product ID exception {order_details["trading_pair"]} at '
                                        f'{order_details["formatted_time"]}'
                )
            return False, {"message": "Invalid trading pair"}

        except SizeTooSmallException:
            return False, {"message": "Order too small"}

        except MaintenanceException:
            return False, {"message": "Exchange is under maintenance"}


        except Exception as e:

            self.log_manager.error(f"⚠️ Error in handle_action: {e}", exc_info=True)

            return False, {"error": f"Unexpected error: {e}"}

    def calculate_order_size(self, side, order_amount, usd_amount, base_amount, quote_price, base_price, quote_deci, base_deci):
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
            tuple: (base_order_size, order_amount, base_value)
        """
        try:
            taker_fee = float(self.taker_fee)
            maker_fee = float(self.maker_fee)

            base_order_size = None
            base_value = Decimal(0)  # Default to zero to avoid NoneType errors

            if side == 'buy':
                # Calculate how much base currency we can buy
                base_order_size = order_amount / (base_price * Decimal(1.001 + taker_fee))  # Adjust for fees
                base_value = base_order_size * base_price  # The cost in USD should match order_amount

            elif side == 'sell':
                # Ensure we don't sell more than available
                base_order_size = min(order_amount, base_amount)
                base_value = base_order_size * base_price  # Convert crypto amount to USD

            # Convert to proper decimal precision
            formatted_base_decimal = self.shared_utils_precision.get_decimal_format(base_deci)
            formatted_quote_decimal = self.shared_utils_precision.get_decimal_format(quote_deci)

            if base_order_size is not None:
                base_order_size = base_order_size.quantize(formatted_base_decimal, rounding=ROUND_HALF_UP)

            if base_value is not None:
                base_value = base_value.quantize(formatted_quote_decimal, rounding=ROUND_HALF_UP)

            # Debugging logs to confirm values
            self.log_manager.debug(
                f"Calculated order size: side={side}, base_order_size={base_order_size}, order_amount={order_amount}, base_value={base_value}"
            )

            return base_order_size, order_amount, base_value

        except Exception as e:
            caller_function_name = stack()[1].function  # Debugging
            print(f'{caller_function_name} - base_amount: {base_amount}, base_price: {base_price}')
            self.log_manager.error(f'calculate_order_size: An unexpected error occurred: {e}', exc_info=True)
            return None, None, None  # Return safe defaults on error

    def parse_webhook_request(self, request_json):
        """
        Parses incoming webhook request data and returns a formatted dictionary.

        Args:
            request_json (dict): Incoming webhook data.

        Returns:
            dict: Parsed order data.
        """
        try:
            return {
                # ✅ Extract trading pair safely
                'trading_pair': request_json.get('pair', 'UNKNOWN/UNKNOWN'),

                # ✅ Determine side safely
                'side': 'buy' if 'open' in request_json.get('action', '') or request_json.get('action') == 'buy' else 'sell',

                # ✅ Convert quote_amount safely to Decimal, default to 0 if missing
                'quote_avail_balance': Decimal(request_json.get('quote_avail_balance', '0')), # amount od USD available to buy crypto

                'order_amount': self._order_size if not request_json.get('order_amount') else Decimal(request_json.get('order_amount')),

                # ✅ Ensure base_amount is correctly assigned for buy/sell conditions
                'base_avail_balance': Decimal('0') if 'open' in request_json.get('action', '') else Decimal(request_json.get(
                    'base_avail_to_trade', '0')),

                # ✅ Extract base and quote currencies safely
                'base_currency': request_json.get('pair', 'UNKNOWN/UNKNOWN').split('/')[0],
                'quote_currency': request_json.get('pair', 'UNKNOWN/UNKNOWN').split('/')[1],

                # ✅ Extract other key values safely
                'action': request_json.get('action', 'UNKNOWN'),
                'origin': request_json.get('origin', 'UNKNOWN'),
                'uuid': request_json.get('uuid'),  # Default to a random UUID if missing

                # ✅ Ensure timestamp is always an integer (milliseconds)
                'time': int(request_json.get('timestamp', time.time() * 1000)),
            }

        except Exception as e:
            self.log_manager.error(f"❌ Error parsing webhook request: {e}")
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
            'buy_size': order_details['base_order_size'],
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
            self.log_manager.error(f'warning', 'handle_webhook_error: Rate limit hit. '
                                                  'Retrying in 60 seconds...')
            time.sleep(60)
            await self.handle_action(order_details, precision_data)

        except (BadRequestException, NotFoundException, InternalServerErrorException, UnknownException) as ex:
            self.log_manager.error(f'handle_webhook_error: {ex}. Additional info: {ex.errors}')

        except Exception as ex:
            self.log_manager.error(f'handle_webhook_error: An unhandled exception occurred: {ex}. '
                                                  f'Additional info: {getattr(ex, "errors", "N/A")}')
