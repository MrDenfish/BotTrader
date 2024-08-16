
import asyncio
import json
from coinbase.websocket import WSClient
from coinbase.rest import RESTClient

from decimal import ROUND_HALF_UP
import time

from custom_exceptions import InsufficientFundsException, ProductIDException, SizeTooSmallException, MaintenanceException
from custom_exceptions import RateLimitException, BadRequestException, NotFoundException, InternalServerErrorException
from custom_exceptions import UnknownException


class WebHookManager:
    _instance_count = 0
    _instance = None

    @classmethod
    def get_instance(cls, logmanager, utility, trade_order_manager, alerts, session):
        if cls._instance is None:
            cls._instance = cls(logmanager, utility, trade_order_manager, alerts, session)
        return cls._instance

    def __init__(self, logmanager, utility, trade_order_manager, alerts, session):
        self.alerts = alerts
        self.utility = utility
        self.trade_order_manager = trade_order_manager
        self.log_manager = logmanager
        self.session = session

    def calculate_order_size(self, side, usd_amount, quote_price, base_price, base_decimal):
        # Convert USD to BTC
        quote_amount = None
        # Convert BTC(quote currency) to Base Currency (e.g., ETH)
        if side == 'buy':
            quote_amount = usd_amount / quote_price  # 100/37600
            base_order_size = quote_amount / base_price
            formatted_decimal = self.utility.get_decimal_format(base_decimal)
            base_order_size = base_order_size.quantize(formatted_decimal, rounding=ROUND_HALF_UP)
        else:
            base_order_size = None
        return base_order_size, quote_amount

    def parse_webhook_data(self, webhook_data):
        try:
            action = webhook_data['action']  # Extract order type (open or close)
            side = 'buy' if 'open' in action else 'sell'
            quote_currency = webhook_data['pair'][-3:]  # Extract quote currency
            base_currency = webhook_data['pair'][:-3]  # Extract base currency
            pair = webhook_data['pair'][:-3] + '/' + webhook_data['pair'][-3:]
            orig = webhook_data['origin']
            the_time = self.utility.convert_timestamp_to_datetime(webhook_data['timestamp'])
            if side == 'buy':
                quote_amount = webhook_data['order_size']  # Extract order size
                if quote_amount is not None:
                    quote_amount = self.utility.float_to_decimal(quote_amount, 2)  # dollar amount from tradingview strategy
                    # $100.00
                    self.log_manager.webhook_logger.debug(f'webhook: buy_size: {quote_amount}', exc_info=True)
            else:
                quote_amount = None

            trade_data = {'time': the_time, 'action': action, 'side': side, 'trading_pair': pair, 'quote_currency':
                          quote_currency, 'base_currency': base_currency, 'quote_amount': quote_amount, 'orig': orig}

            return trade_data
        except Exception as e:
            self.log_manager.webhook_logger.error(f'parse_webhook_data: {webhook_data}An error occurred: {e}', exc_info=True)

    async def handle_action(self, order_data, precision_data):
        """ Handle the action from the webhook request. Place an order on Coinbase Pro."""
        try:
            await self.trade_order_manager.place_order(order_data, precision_data)
        except InsufficientFundsException:
            self.log_manager.webhook_logger.info(f'handle_action: Insufficient funds')
            self.alerts.callhome('Insufficient funds', f'Insufficient funds  {order_data["trading_pair"]} at '
                                                       f'{order_data["formatted_time"]}')
        except ProductIDException:
            self.log_manager.webhook_logger.info(f'handle_action: product id exception')
            self.alerts.callhome('product id exception', f'product id  exception  {order_data["trading_pair"]} at '
                                                         f'{order_data["formatted_time"]}')
        except SizeTooSmallException:
            print('Order too small')
            # Handle this specific error differently
        except MaintenanceException:
            print('MaintenanceException')
            # Maybe implement a retry logic
        except Exception as e:
            # Catch-all for other exceptions
            await self.handle_webhook_error(e, order_data, precision_data)
            self.log_manager.webhook_logger.error(f'Handle_action: An unexpected error occurred: {e}', exc_info=True)

    async def handle_webhook_error(self, e, order_data, precision_data):
        """Handle errors that occur while processing an old_webhook request."""
        exception_map = {
            429: RateLimitException,
            400: BadRequestException,
            404: NotFoundException,
            500: InternalServerErrorException,
        }
        extra_error_details = {
            'action': order_data['side'],
            'trading_pair': order_data['trading_pair'],
            'buy_size': order_data['base_order_size'],
            'formatted_time': order_data['formatted_time'],
        }
        # Map status_code to custom exceptions
        exception_to_raise = exception_map.get(getattr(e, 'status_code', None), UnknownException)

        # Raise the exception and handle it in the except block
        try:
            raise exception_to_raise(
                f"An error occurred with status code: {getattr(e, 'status_code', 'unknown')}, error: {e}",
                extra_error_details)
        except RateLimitException:
            self.log_manager.webhook_logger.error(f'warning', 'handle_webhook_error: Rate limit hit. '
                                                  'Retrying in 60 seconds...')
            time.sleep(60)
            await self.handle_action(order_data, precision_data)

        except (BadRequestException, NotFoundException, InternalServerErrorException, UnknownException) as ex:
            self.log_manager.webhook_logger.error(f'handle_webhook_error: {ex}. Additional info: {ex.errors}')

        except Exception as ex:
            self.log_manager.webhook_logger.error(f'handle_webhook_error: An unhandled exception occurred: {ex}. '
                                                  f'Additional info: {getattr(ex, "errors", "N/A")}')
