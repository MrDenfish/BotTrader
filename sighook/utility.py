import time
import pandas as pd
from decimal import Decimal, ROUND_DOWN
from datetime import datetime
from dateutil import parser
import pytz
import math
import socket

class SenderUtils:
    _instance_count = 0

    def __init__(self, logmanager, exchange,ccxt_api):
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

    def set_trade_parameters(self, start_time, ticker_cache, market_cache):
        self.start_time = start_time
        self.ticker_cache = ticker_cache
        self.market_cache = market_cache

    @staticmethod
    # Function to standardize any timestamp input
    def standardize_timestamp(timestamp_str):
        dt = parser.parse(timestamp_str)
        if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
            dt = dt.replace(tzinfo=pytz.UTC)  # Handle naive timestamps as UTC
        return dt.astimezone(pytz.UTC)

    # Usage example
    # standard_timestamp = standardize_timestamp("your_timestamp_here")

    @staticmethod
    def get_my_ip_address():
        hostname = socket.gethostname()
        ip_address = socket.gethostbyname(hostname)
        return ip_address

    def calculate_time_difference(self, time_string):
        try:
            time_format = "%Y-%m-%dT%H:%M:%S.%fZ"
            order_time = datetime.strptime(time_string, time_format)
            current_time = datetime.utcnow()
            difference = current_time - order_time
            difference_in_minutes = difference.total_seconds() / 60
            return f"{int(difference_in_minutes)} minutes"
        except Exception as e:
            self.log_manager.sighook_logger.error(f"Error calculating time difference: {e}", exc_info=True)
            return None

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
            print(f'******   Elapsed time for {func_name}: {formatted_time} (hh:mm:ss) ******')
            return elapsed_seconds

    @staticmethod
    def convert_timestamp(timestamp):
        try:
            # Assuming Unix timestamps are in milliseconds
            return pd.to_datetime(timestamp, unit='ms')
        except ValueError:
            # Fallback for standard datetime strings
            return pd.to_datetime(timestamp)

    def time_unix(self, last_timestamp):
        if not last_timestamp or last_timestamp == 0:
            # If the timestamp is None or explicitly zero, return 0
            return 0

        if isinstance(last_timestamp, datetime):
            # If last_timestamp is already a datetime object, convert directly to Unix time
            return int(last_timestamp.timestamp() * 1000)

        # Assume last_timestamp is a string if it's not a datetime object
        format_string = "%Y-%m-%d %H:%M:%S.%f"
        try:
            # Try to parse the string to a datetime object
            parsed_timestamp = datetime.strptime(last_timestamp, format_string)
            return int(parsed_timestamp.timestamp() * 1000)
        except ValueError as e:
            # Log error if parsing fails
            self.log_manager.sighook_logger.error(f"Error parsing timestamp: {e}")
            return None
        except Exception as e:
            # Log unexpected errors
            self.log_manager.sighook_logger.error(f"Error converting timestamp to unix: {e}", exc_info=True)
            return None

    import math

    def fetch_precision(self, symbol: str) -> tuple:
        """
        Fetch the precision for base and quote currencies of a given symbol.

        :param symbol: The symbol to fetch precision for, in the format 'BTC-USD' or 'BTC/USD'.
        :return: A tuple containing base and quote decimal places.
        """
        try:
            # Normalize symbol format to 'BTC/USD' for consistent comparison
            ticker = symbol.replace('-', '/') if '-' in symbol else symbol
            ticker_value = ticker.iloc[0] if isinstance(ticker, pd.Series) else ticker
            # Iterate through market data cache
            for market in self.market_cache:
                # Compare market symbol or product_id to the normalized ticker
                if market['symbol'] == ticker_value or market['info']['product_id'] == ticker_value:
                    base_precision = market['precision']['price']  # Expected to be a float
                    quote_precision = market['precision']['amount']  # Expected to be a float

                    if base_precision <= 0 or quote_precision <= 0:
                        raise ValueError("Precision value is zero or negative, which may cause a division error.")

                    # Calculate decimal places using logarithm
                    base_decimal_places = -int(math.log10(base_precision))
                    quote_decimal_places = -int(math.log10(quote_precision))

                    # Check for negative decimal places (should not happen if the log10 is correct)
                    if base_decimal_places < 0 or quote_decimal_places < 0:
                        raise ValueError("Decimal places cannot be negative.")

                    return base_decimal_places, quote_decimal_places

            # If no matching market found
            raise LookupError(f"No market found for symbol {symbol} check format.")

        except ValueError as e:
            self.log_manager.sighook_logger.error(f"fetch_precision: {e}", exc_info=True)
            return None, None
        except Exception as e:
            if "No market found" in str(e):
                self.log_manager.sighook_logger.info(f"No market found for symbol {symbol}.")
            else:
                self.log_manager.sighook_logger.error(f'fetch_precision: Error processing order for {symbol}: {e}', exc_info=True)

        raise ValueError(f"Symbol {symbol} not found in exchange markets.")

    def adjust_precision(self, base_deci, quote_deci, num_to_adjust, convert):
        """PART III: Order cancellation and Data Collection """
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
            self.log_manager.webhook_logger.error(f'adjust_precision: An error occurred: {e}', exc_info=True)
            return None

    def float_to_decimal(self, value: float, decimal_places: int) -> Decimal:
        """
        Convert a float to a Decimal with a specified number of decimal places.
        Used in Part I
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

            self.log_manager.sighook_logger.error(f'float_to_decimal: An error occurred: {e}. Value: {value},'
                                                  f'Decimal places: {decimal_places}', exc_info=True)
            raise
