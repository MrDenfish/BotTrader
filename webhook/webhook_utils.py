"""This module likely contains various utility functions that support different operations of the trade bot."""


from custom_exceptions import CoinbaseAPIError

from log_manager import LoggerManager

from decimal import Decimal, ROUND_DOWN, getcontext
# from .shared.api_related import retry_on_401  # shared module
from typing import Dict
import socket
import pandas as pd
import math
import traceback

# Define the TradeBotUtils class


class TradeBotUtils:
    _instance_count = 0
    _instance = None

    @classmethod
    def get_instance(cls, botconfig, logmanager, exchange_client, ccxt_api, webhook_listener=None):
        if cls._instance is None:
            cls._instance = cls(botconfig, logmanager, exchange_client, ccxt_api, webhook_listener)
        else:
            cls._instance.webhook_listener = webhook_listener
        return cls._instance

    def __init__(self, botconfig, logmanager, exchange_client, ccxt_api, order_book, webhook_listener=None):
        # self.id = TradeBotUtils._instance_count
        # TradeBotUtils._instance_count += 1
        # print(f"TradeBotUtils Instance ID: {self.id}")
        self.exchange = exchange_client
        self.webhook_listener = webhook_listener
        self.bot_config = botconfig
        self.log_manager = logmanager
        self.ccxt_exceptions = ccxt_api
        self.order_book = order_book
        self.base_currency, self.quote_currency, self.trading_pair = None, None, None
        self.base_deci, self.quote_deci = None, None
        self.base_incri, self.quote_incri, self.balances = None, None, None

    def set_trade_parameters(self, trading_pair, base_currency, quote_currency, base_decimal, quote_decimal,
                             base_increment, quote_increment, balances):
        self.base_currency = base_currency
        self.quote_currency = quote_currency
        self.trading_pair = trading_pair
        self.base_deci = base_decimal
        self.quote_deci = quote_decimal
        self.base_incri = base_increment
        self.quote_incri = quote_increment
        self.balances = balances

    @LoggerManager.log_method_call
    def refresh_authentication(self):
        # Instantiate BotConfig to access the configuration
        # bot_config = BotConfig()
        self.bot_config.reload_config()  # Reload the configuration

        # Fetch new API key and secret from BotConfig
        new_api_key = self.bot_config.api_key
        new_api_secret = self.bot_config.api_secret

        # Update the exchange client with new credentials
        self.exchange.apiKey = new_api_key
        self.exchange.secret = new_api_secret

        # Log the refresh action
        self.log_manager.webhook_logger.info("Authentication refreshed.")

    @LoggerManager.log_method_call
    async def fetch_spot(self, symbol):
            ticker = self. exchange.fetch_ticker(symbol)
            # Extract the spot price (last price)
            spot_price = ticker['last']
            self.log_manager.webhook_logger.info(f'fetch_spot: {symbol} spot price: {spot_price}')
            return spot_price

    @LoggerManager.log_method_call
    async def fetch_precision(self, symbol: str) -> tuple:
        """
        Fetch the precision for base and quote currencies of a given symbol.

        :param symbol: The symbol to fetch precision for.
        :return: A tuple containing base and quote decimal places.
        """
        try:
            markets = await self.ccxt_exceptions.ccxt_api_call(self.exchange.fetch_markets)
            if markets is None:
                raise ValueError("Failed to fetch markets.")

            for market in markets:
                if market['symbol'] == symbol:
                    base_precision = market['precision']['amount']  # float
                    quote_precision = market['precision']['price']  # float
                    base_increment = market['info']['base_increment']  # string
                    quote_increment = market['info']['quote_increment']  # string

                    if base_precision == 0 or quote_precision == 0:
                        raise ValueError("Precision value is zero, which may cause a division error.")
                    # base_decimal_places = 8
                    # quote_decimal_places = 8
                    base_decimal_places = -int(math.log10(base_precision))
                    quote_decimal_places = -int(math.log10(quote_precision))
                    # Check for negative decimal places
                    if base_decimal_places < 0:
                        raise ValueError("Base decimal places cannot be negative.")

                    return base_decimal_places, quote_decimal_places, base_increment, quote_increment

        except ValueError as e:
            self.log_manager.webhook_logger.error(f"fetch_precision: {e}")
            return None, None, None, None

        raise ValueError(f"Symbol {symbol} not found in exchange markets.")

    @staticmethod
    @LoggerManager.log_method_call
    def format_open_orders(open_orders: list) -> pd.DataFrame:
        """
        Format the open orders data received from the ccxt api(Coinbase Cloud) call.
        Parameters:
        Returns:
        - list: A list of dictionaries containing the required data.
        """

        data_to_load = [{
            'order_id': order['id'],
            'product_id': order['info']['product_id'],
            'side': order['info']['side'],
            'size': order['amount'],
            'amount': order['price'],
            'filled': order['filled'],
            'remaining': order['remaining']
        } for order in open_orders]
        df = pd.DataFrame(data_to_load)
        return df

    @LoggerManager.log_method_call
    async def get_open_orders(self):
        base_balance = Decimal(0)
        quote_bal = Decimal(0)
        try:
            fetched_open_orders = await self.ccxt_exceptions.ccxt_api_call(lambda: self.exchange.fetch_open_orders())

            # Ensure format_open_orders returns a DataFrame
            all_open_orders = self.format_open_orders(fetched_open_orders) if fetched_open_orders else pd.DataFrame()

            self.balances = await self.get_account_balance([self.trading_pair.split('/')[0], self.quote_currency])
            if None in self.balances.values():
                self.log_manager.webhook_logger.warning(
                    'None values detected in balances. Refreshing authentication and retrying.')
                self.refresh_authentication()
                self.balances = await self.get_account_balance([self.trading_pair.split('/')[0], self.quote_currency])

            base_value = self.balances.get(self.trading_pair.split('/')[0], 0)
            base_balance = self.float_to_decimal(base_value, self.base_deci) if base_value is not None else Decimal(0)
            quote_value = self.balances.get(self.quote_currency, 0)
            quote_balance = self.float_to_decimal(quote_value, self.quote_deci) if quote_value is not None else Decimal(0)

            # Check if all_open_orders DataFrame is empty
            if all_open_orders.empty:
                self.log_manager.webhook_logger.debug(
                    f'get_open_orders: No open orders found for {self.trading_pair}. Coin Balance: {base_balance}')
                return quote_balance, base_balance, None

            # Process non-empty all_open_orders
            print(f'{all_open_orders}')

            return quote_balance, base_balance, all_open_orders
        except CoinbaseAPIError as e:
            self.log_manager.webhook_logger.error(f'get_open_orders: Coinbase API Error occurred: {e}')
            return None, None, None
        except Exception as e:  # Basic but it does not seem to catch errors
            self.log_manager.webhook_logger.exception(f'get_open_orders: Error occurred: {e}')
            return None, None, None

    @staticmethod
    @LoggerManager.log_method_call
    def convert_price_to_precision(price):
        # Convert the price to the required precision for the symbol
        # Placeholder logic for demonstration purposes
        return round(price, 2)  # Assuming 2 decimal places for simplicity

    @staticmethod
    @LoggerManager.log_method_call
    def get_my_ip_address():
        hostname = socket.gethostname()
        ip_address = socket.gethostbyname(hostname)
        return ip_address

    from decimal import Decimal

    @LoggerManager.log_method_call
    async def get_account_balance(self, currencies):
        try:
            accounts = await self.ccxt_exceptions.ccxt_api_call(lambda: self.exchange.fetch_balance())

            # Check if accounts is None
            if accounts is None:
                self.log_manager.webhook_logger.error('get_account_balance: Failed to fetch accounts')
                return {currency: None for currency in currencies}

            # balances = {}
            for currency in currencies:
                if currency in accounts:
                    account = accounts[currency]
                    self.balances[currency] = float(account['free']) if account['free'] is not None else None
                else:
                    self.balances[currency] = None
                    self.log_manager.webhook_logger.info(f'get_account_balance: {currency} not found in accounts')

            return self.balances

        except Exception as e:
            my_ip = self.get_my_ip_address()  # debug when running on the laptop
            self.log_manager.webhook_logger.debug(
                f'get_account_balance: Exception occurred during API call from IP {my_ip}: {e}')
            return {currency: None for currency in currencies}

    @LoggerManager.log_method_call
    def adjust_precision(self, num_to_adjust, convert):

        """"" Adjust the amount based on the number of decimal places required for the symbol.
         base_deci and quote_deci are determined by the symbol presicion from markets and is the number of decimal places
         for the currency used in a particular market.  For example, for BTC/USD, base_deci is 8 and quote_deci is 2."""
        try:
            if convert == 'base':
                decimal_places = self.base_deci
            elif convert == 'usd':
                decimal_places = 2
            elif convert == 'quote':
                decimal_places = self.quote_deci
            else:
                decimal_places = 8
            adjusted_precision = self.float_to_decimal(num_to_adjust, decimal_places)

            return adjusted_precision
        except Exception as e:
            self.log_manager.webhook_logger.error(f'adjust_precision: An error occurred: {e}')
            return None

    @LoggerManager.log_method_call
    def adjusted_price_and_size(self, side, order_book, quote_price, available_coin_balance, usd_amount, response=None):
        # was available_balance
        """ Calculate and adjust price and size
        # Return adjusted_price and adjusted_size """
        best_ask_price = None
        best_bid_price = None
        # Set the precision for Decimal operations if needed
        getcontext().prec = 8  # more than 8 may cause class 'decimal.DivisionImpossible' error
        try:
            if side == 'buy':
                # For buy orders
                best_ask_price = Decimal(order_book['asks'][0][0])
                if best_ask_price is None:
                    pass
                adjusted_price = self.adjust_precision(best_ask_price + self.quote_incri, convert='quote')
                # lowest ask to comply with Coinbase's requirement
                quote_amount = Decimal(usd_amount) / Decimal(quote_price)
                adjusted_size = quote_amount / adjusted_price

                adjusted_size = adjusted_size.quantize(self.base_incri, rounding=ROUND_DOWN)
                if adjusted_price is None or adjusted_size is None:
                    self.log_manager.webhook_logger.info(
                        f'adjusted_price_and_size: {side}, best_ask_price: {best_ask_price}, adjusted_price: '
                        f'{adjusted_price}, adjusted_size: {adjusted_size}')
                    return None, None
            else:  # sell orders
                best_bid_price = Decimal(order_book['bids'][0][0]).quantize(self.quote_incri, rounding=ROUND_DOWN)
                if best_bid_price is None:
                    pass
                adjusted_price = self.adjust_precision(best_bid_price + self.quote_incri, convert='quote')
                # Ensure the sell price is above the highest bid to comply with Coinbase's requirement
                adjusted_size = Decimal(available_coin_balance).quantize(self.base_incri, rounding=ROUND_DOWN)
                if response is not None and 'insufficient base balance' in response:
                    adjusted_size = adjusted_size - (adjusted_size * Decimal(0.01))  # reduce size by 1%
                if adjusted_price is None or adjusted_size is None:
                    error_details = traceback.format_exc()
                    self.log_manager.webhook_logger.error(f'adjusted_price_and_size: Error placing order: {error_details}')
                    self.log_manager.webhook_logger.error(
                        f'adjusted_price_and_size: {side}, best_bid_price: {best_bid_price}, adjusted_price: '
                        f'{adjusted_price},adjusted_size: {adjusted_size} order book{order_book} {order_book["asks"][0][0]}')
                    return None, None
            return adjusted_price, adjusted_size
        except Exception as e:
            error_details = traceback.format_exc()
            self.log_manager.webhook_logger.error(
                f'adjusted_price_and_size: An error occurred:{side},best_ask_price:{best_ask_price},'
                f'best_bid_price:{best_bid_price}, {e}\nDetails: {error_details}')
            return None, None

    @staticmethod
    @LoggerManager.log_method_call
    def prepare_order_data(side: str, symbol: str, price: Decimal, size: Decimal) -> Dict[str, str]:
        order_data = {
            "product_id": symbol,
            "side": side,
            "price": float(price),
            "size": float(size),
            "post_only": True  # Force the order to only add liquidity to the book
        }
        return order_data

    @staticmethod
    @LoggerManager.log_method_call
    def get_decimal_string(base_deci):
        return '0.' + '0' * (base_deci - 1) + '1'

    @LoggerManager.log_method_call
    def float_to_decimal(self, value: float, decimal_places: int) -> Decimal:
        """
        Convert a float to a Decimal with a specified number of decimal places.
        """
        try:
            # Construct a string representing the desired decimal format
            decimal_format = '0.' + '0' * decimal_places if decimal_places > 0 else '0'

            # Convert the float to a Decimal
            value_decimal = Decimal(str(value))

            # Quantize the Decimal to the desired number of decimal places
            value_decimal = value_decimal.quantize(Decimal(decimal_format), rounding=ROUND_DOWN)

            return value_decimal
        except Exception as e:

            self.log_manager.webhook_logger.error(f'float_to_decimal: An error occurred: {e}. Value: {value},'
                                                  f'Decimal places: {decimal_places}')
            raise

    @staticmethod
    @LoggerManager.log_method_call
    def get_decimal_format(base_decimal: int) -> Decimal:
        """
        Generate a Decimal format string based on the number of decimal places.

        :param base_decimal: The number of decimal places for the base value.
        :return: A Decimal object representing the format.
        """
        if base_decimal < 0:
            raise ValueError("base_decimal must be a positive integer")

        decimal_format = '0.' + ('0' * (base_decimal - 1)) + '1'
        return Decimal(decimal_format)  # example 0.00000001

    @staticmethod
    @LoggerManager.log_method_call
    def decimal_places(value):
        # Convert the float to a string
        value_str = str(value)
        # Check if there is a decimal point in the string
        if '.' in value_str:
            return len(value_str) - value_str.index('.') - 1
        else:
            # If there is no decimal point, the number of decimal places is 0
            return 0
