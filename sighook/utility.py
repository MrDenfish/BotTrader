
import time
import datetime
import math
import pandas as pd
from decimal import Decimal, ROUND_DOWN


class SenderUtils:
    _instance_count = 0

    def __init__(self, logmanager, exchange, ccxt_api):
        # self.id = SenderUtils._instance_count
        # SenderUtils._instance_count += 1
        # print(f"SenderUtils Instance ID: {self.id}")
        self.log_manager = logmanager
        self.exchange = exchange
        self.ccxt_exceptions = ccxt_api
        self.ticker_cache = None
        self.market_cache = None
        self.start_time = None
        self.web_url = None
        self.holdings = None

    def set_trade_parameters(self, start_time, ticker_cache, market_cache, hist_holdings):
        self.start_time = start_time
        self.ticker_cache = ticker_cache
        self.market_cache = market_cache
        self.holdings = hist_holdings

    @staticmethod
    def print_elapsed_time(start_time=None, func_name=None):
        """Calculate elapsed time and print it to the console."""

        end_time = time.time()
        if start_time is None:
            start_time = time.time()
            return start_time
        else:
            elapsed_seconds = int(end_time - start_time)
            hours = elapsed_seconds // 3600
            minutes = (elapsed_seconds % 3600) // 60
            seconds = elapsed_seconds % 60

            formatted_time = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
            print(f'Elapsed time for {func_name}: {formatted_time} (hh:mm:ss)')
            start_time = None  # reset start time
            return elapsed_seconds

    @staticmethod
    def time_unix(last_timestamp):
        if last_timestamp != 0:
            format_string = "%Y-%m-%d %H:%M:%S.%f"
            last_timestamp = datetime.datetime.strptime(last_timestamp, format_string)
            return int(last_timestamp.timestamp() * 1000)
        else:
            return 0

    @staticmethod
    def convert_timestamp(timestamp):
        try:
            # Assuming Unix timestamps are in milliseconds
            return pd.to_datetime(timestamp, unit='ms')
        except ValueError:
            # Fallback for standard datetime strings
            return pd.to_datetime(timestamp)

    @staticmethod
    def find_price(product_id, usd_pairs):
        """
            Find the price of a product given its ID.

            Parameters:
            - product_id (str): The ID of the product to search for.
            - usd_pairs (list): A list of product pairs in USD.

            Returns:
            - float: The price of the product. Returns None if the product ID is not found.
            """
        for pair in usd_pairs:
            if pair['id'] == product_id:
                return pair['price']
        # If the product ID is not found, return None or raise an exception.
        return None

    @staticmethod
    def percentage_difference(a, b):
        if b == 0:
            return float('inf')  # If b is 0, then it would cause a division by zero error
        return ((a - b) / b) * 100

    def fetch_precision(self, symbol: str) -> tuple:
        """
        Fetch the precision for base and quote currencies of a given symbol.

        :param symbol: The symbol to fetch precision for.
        :return: A tuple containing base and quote decimal places.
        """
        try:
            # markets = self.ccxt_exceptions.ccxt_api_call(self.exchange.fetch_markets)
            # if markets is None:
            #     raise ValueError("Failed to fetch markets.")

            for market in self.market_cache:
                if market['symbol'] == symbol['symbol']:
                    base_precision = market['precision']['price']  # float
                    quote_precision = market['precision']['amount']  # float
                    # base_increment = market['info']['base_increment']  # string
                    # quote_increment = market['info']['quote_increment']  # string

                    if base_precision == 0 or quote_precision == 0:
                        raise ValueError("Precision value is zero, which may cause a division error.")
                    # base_decimal_places = 8
                    # quote_decimal_places = 8
                    base_decimal_places = -int(math.log10(base_precision))
                    quote_decimal_places = -int(math.log10(quote_precision))
                    # Check for negative decimal places
                    if base_decimal_places < 0:
                        raise ValueError("Base decimal places cannot be negative.")

                    return base_decimal_places, quote_decimal_places

        except ValueError as e:
            self.log_manager.webhook_logger.error(f"fetch_precision: {e}")
            return None, None
        except Exception as e:
            self.log_manager.sighook_logger.error(f'fetch_precision: Error processing order for {symbol}: {e}')

        raise ValueError(f"Symbol {symbol} not found in exchange markets.")

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
