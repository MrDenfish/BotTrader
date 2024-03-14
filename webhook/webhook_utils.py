"""This module likely contains various utility functions that support different operations of the trade bot."""


from custom_exceptions import CoinbaseAPIError

from log_manager import LoggerManager

from decimal import Decimal, ROUND_DOWN, InvalidOperation

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
        try:
            ticker = await self.ccxt_exceptions.ccxt_api_call(lambda: self.exchange.fetch_ticker(symbol))
            # Extract the spot price (last price)
            spot_price = ticker['last']
            self.log_manager.webhook_logger.info(f'fetch_spot: {symbol} spot price: {spot_price}')
            return spot_price
        except Exception as ex:
            error_details = traceback.format_exc()
            self.log_manager.webhook_logger.error(f'fetch_spot: Error details: {error_details}')
            self.log_manager.webhook_logger.error(f'fetch_spot: Error fetching ticker {symbol}: {ex}')

    @LoggerManager.log_method_call
    async def fetch_precision(self, symbol: str) -> tuple:
        """
        Fetch the precision for base and quote currencies of a given symbol.

        :param symbol: The symbol to fetch precision for.
        :return: A tuple containing base and quote decimal places.
        """
        markets = None
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
            error_details = traceback.format_exc()
            self.log_manager.webhook_logger.error(
                f'fetch_precision: An error occurred:{markets} {e}\nDetails: {error_details}')
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
    async def get_open_orders(self, balances, base_deci, quote_deci, quote_currency, trading_pair):
        base_balance = Decimal(0)
        quote_bal = Decimal(0)
        try:
            fetched_open_orders = await self.ccxt_exceptions.ccxt_api_call(lambda: self.exchange.fetch_open_orders())

            # Ensure format_open_orders returns a DataFrame
            all_open_orders = self.format_open_orders(fetched_open_orders) if fetched_open_orders else pd.DataFrame()

            balances = await self.get_account_balance(balances, [trading_pair.split('/')[0], quote_currency])
            if None in balances.values():
                self.log_manager.webhook_logger.warning(
                    'None values detected in balances. Refreshing authentication and retrying.')
                self.refresh_authentication()
                balances = await self.get_account_balance([trading_pair.split('/')[0], quote_currency])

            base_value = balances.get(trading_pair.split('/')[0], 0)
            base_balance = self.float_to_decimal(base_value, base_deci) if base_value is not None else Decimal(0)
            quote_value = balances.get(quote_currency, 0)
            quote_balance = self.float_to_decimal(quote_value, quote_deci) if quote_value is not None else Decimal(0)

            # Check if all_open_orders DataFrame is empty
            if all_open_orders.empty:
                self.log_manager.webhook_logger.debug(
                    f'get_open_orders: No open orders found for {trading_pair}. Coin Balance: {base_balance}')
                return quote_balance, base_balance, None

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
    async def get_account_balance(self, balances, currencies):
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
                    balances[currency] = float(account['free']) if account['free'] is not None else None
                else:
                    balances[currency] = None
                    self.log_manager.webhook_logger.info(f'get_account_balance: {currency} not found in accounts')

            return balances

        except Exception as e:
            my_ip = self.get_my_ip_address()  # debug when running on the laptop
            self.log_manager.webhook_logger.debug(
                f'get_account_balance: Exception occurred during API call from IP {my_ip}: {e}')
            return {currency: None for currency in currencies}

    @LoggerManager.log_method_call
    def adjust_precision(self, base_deci, quote_deci, num_to_adjust, convert):

        """"" Adjust the amount based on the number of decimal places required for the symbol.
         base_deci and quote_deci are determined by the symbol presicion from markets and is the number of decimal places
         for the currency used in a particular market.  For example, for BTC/USD, base_deci is 8 and quote_deci is 2."""
        try:
            if convert == 'base':
                decimal_places = base_deci
            elif convert == 'usd':
                decimal_places = 2
            elif convert == 'quote':
                decimal_places = quote_deci
            else:
                decimal_places = 8
            adjusted_precision = self.float_to_decimal(num_to_adjust, decimal_places)

            return adjusted_precision
        except Exception as e:
            self.log_manager.webhook_logger.error(f'adjust_precision: An error occurred: {e}')
            return None

    @LoggerManager.log_method_call
    def adjust_price_and_size(self, base_deci, quote_deci, base_incri, side, order_book, quote_price,
                              available_coin_balance, usd_amount, response=None):
        """ Calculate and adjust price and size
                # Return adjusted_price and adjusted_size """

        best_bid_price = Decimal(order_book['bids'][0][0])
        best_ask_price = Decimal(order_book['asks'][0][0])
        spread = best_ask_price - best_bid_price

        # Dynamic adjustment factor based on a percentage of the spread
        adjustment_factor = spread * Decimal('0.1')  # Example: 25% of the spread
        try:
            if side == 'buy':
                # Adjust the buy price to be slightly higher than the best bid
                adjusted_price = best_ask_price + adjustment_factor
                adjusted_price = self.adjust_precision(base_deci, quote_deci, adjusted_price, convert='quote')
                quote_amount = Decimal(usd_amount) / quote_price
                adjusted_size = quote_amount / adjusted_price
                adjusted_size = adjusted_size.quantize(base_incri, rounding=ROUND_DOWN)
                if None in (adjusted_price, adjusted_size):
                    self.log_manager.webhook_logger.info(
                        f'adjusted_price_and_size: {side}, best_ask_price: {best_ask_price}, adjusted_price: '
                        f'{adjusted_price}, adjusted_size: {adjusted_size}')
                    return None, None
            else:
                # Adjust the sell price to be slightly lower than the best ask
                adjusted_price = best_bid_price - adjustment_factor
                adjusted_price = self.adjust_precision(base_deci, quote_deci, adjusted_price, convert='quote')
                adjusted_size = Decimal(available_coin_balance)

            # Apply quantization based on market precision rules
            # adjusted_price = adjusted_price.quantize(self.quote_incri, rounding=ROUND_DOWN)
            # adjusted_size = Decimal(available_coin_balance).quantize(self.base_incri, rounding=ROUND_DOWN)
            if response is not None and 'insufficient base balance' in response:
                adjusted_size = adjusted_size - (adjusted_size * Decimal(0.01))  # reduce size by 1%
                adjusted_size = adjusted_size.quantize(base_incri, rounding=ROUND_DOWN)
            if None in (adjusted_price, adjusted_size):
                error_details = traceback.format_exc()
                self.log_manager.webhook_logger.error(f'adjusted_price_and_size: Error placing order: {error_details}')
                self.log_manager.webhook_logger.error(
                    f'adjusted_price_and_size: {side}, best_bid_price: {best_bid_price}, adjusted_price: '
                    f'{adjusted_price},adjusted_size: {adjusted_size} order book{order_book} {order_book["asks"][0][0]}')
                return None, None
            return adjusted_price, adjusted_size
        except InvalidOperation as e:
            # Log the error and the values that caused it
            self.log_manager.webhook_logger.error(f'Invalid operation encountered during quantization: {e}')
            self.log_manager.webhook_logger.error(
                f'available_coin_balance: {available_coin_balance}, base_incri: {base_incri}')
            return None, None
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
            error_details = traceback.format_exc()
            self.log_manager.webhook_logger.error(
                f'float_to_decimal: An error occurred:decimal places:{decimal_places},value:{value},'
                f' {e}\nDetails: {error_details}')
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
