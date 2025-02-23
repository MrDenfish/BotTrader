
from decimal import ROUND_HALF_UP
import time
from decimal import Decimal
from datetime import datetime
from Api_manager.api_exceptions import (InsufficientFundsException, ProductIDException, SizeTooSmallException,
                                    MaintenanceException)
from Api_manager.api_exceptions import RateLimitException, BadRequestException, NotFoundException, InternalServerErrorException
from Api_manager.api_exceptions import UnknownException
from Shared_Utils.config_manager import CentralConfig as config
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
        self.config = config()
        self.alerts = alerts
        self.shared_utils_precision = shared_utils_precision
        self.trade_order_manager = trade_order_manager
        self.log_manager = logmanager
        self.session = session

        # Trading parameters
        self._taker_fee = Decimal(self.config.taker_fee)
        self._maker_fee = Decimal(self.config.maker_fee)

    @property
    def taker_fee(self):
        return self._taker_fee

    @property
    def maker_fee(self):
        return self._maker_fee

    def calculate_order_size(self, side, usd_amount, base_amount, quote_price, base_price, base_decimal):
        # Convert USD to BTC
        quote_amount = None
        try:
            taker_fee = float(self.taker_fee)
            maker_fee = float(self.maker_fee)
            # Convert BTC(quote currency) to Base Currency (e.g., ETH)
            if side == 'buy':
                quote_amount = usd_amount / (quote_price * Decimal(1.001 + taker_fee))  # Adjust quote amount to cover fees
                base_order_size = quote_amount / base_price
                formatted_decimal = self.shared_utils_precision.get_decimal_format(base_decimal)
                base_order_size = base_order_size.quantize(formatted_decimal, rounding=ROUND_HALF_UP)
            elif side == 'sell':
                base_order_size = base_amount
            else:
                base_order_size = None
            return base_order_size, quote_amount
        except Exception as e:
            caller_function_name = stack()[1].function  # debugging
            print(f'{caller_function_name} -  quote amount:{quote_amount}, base price:{base_price}')
            self.log_manager.error(f'calculate_order_size: An unexpected error occurred: {e}', exc_info=True)

    @staticmethod
    def parse_webhook_data(request_json):
        """
        Extract relevant trade data from the webhook JSON.
        """

        return {
            'trading_pair': request_json['pair'][:-3] + '/' + request_json['pair'][-3:],
            'side': 'buy' if 'open' in request_json.get('action') else 'sell',
            'quote_amount': Decimal(request_json['order_size']),
            'base_amount': Decimal('0') if 'open' in request_json['action'] else  Decimal(request_json['order_size']),
            'base_currency': request_json['pair'][:-3], # Extract base currency,
            'quote_currency':  request_json['pair'][-3:],  # Extract quote currency will always be USD or BTC
            'action': request_json.get('action'),
            'origin': request_json.get('origin'),
            'uuid': request_json.get('uuid'),
            'time': request_json.get('time', datetime.now().isoformat())
        }

    async def handle_action(self, order_details, precision_data):
        """ Handle the action from the webhook request. Place an order on Coinbase Pro."""
        try:
            await self.trade_order_manager.place_order(order_details, precision_data)
        except InsufficientFundsException:
            self.log_manager.info(f'handle_action: Insufficient funds')
            self.alerts.callhome('Insufficient funds', f'Insufficient funds  {order_details["trading_pair"]} at '
                                                       f'{order_details["formatted_time"]}')
        except ProductIDException:
            self.log_manager.info(f'handle_action: product id exception')
            self.alerts.callhome('product id exception', f'product id  exception  {order_details["trading_pair"]} at '
                                                         f'{order_details["formatted_time"]}')
        except SizeTooSmallException:
            print('Order too small')
            # Handle this specific error differently
        except MaintenanceException:
            print('MaintenanceException')
            # Maybe implement a retry logic
        except Exception as e:
            # Catch-all for other exceptions
            await self.handle_webhook_error(e, order_details, precision_data)
            self.log_manager.error(f'Handle_action: An unexpected error occurred: {e}', exc_info=True)

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
