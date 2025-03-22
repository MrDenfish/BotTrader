from inspect import stack # debugging
import math
import pandas as pd
from decimal import Decimal, ROUND_DOWN

class PrecisionUtils:
    _instance = None  # Singleton instance

    @classmethod
    def get_instance(cls, logmanager, market_data=None):
        """ Ensures only one instance of PrecisionUtils is created. """
        if cls._instance is None:
            cls._instance = cls(logmanager, market_data)
        return cls._instance

    def __init__(self, logmanager,  market_data):
        """ Initialize PrecisionUtils. """
        if PrecisionUtils._instance is not None:
            raise Exception("This class is a singleton! Use get_instance() instead.")

        self.market_data = market_data
        self.log_manager = logmanager



    def fetch_precision(self, symbol: str, usd_pairs) -> tuple:
        """
        Fetch the precision for base and quote currencies of a given symbol.

        :param symbol: The symbol to fetch precision for, in the format 'BTC-USD' or 'BTC/USD'.
        :return: A tuple containing base and quote decimal places.
        """
        try:
            if usd_pairs is None:
                usd_pairs = self.market_data['usd_pairs_cache']
            if len(usd_pairs) ==0:
                return 4, 2, 1e-08, 1e-08 #default values for empty usd_pairs

            if isinstance(symbol, pd.Series):
                caller_function_name = stack()[1].function  # debugging
                self.log_manager.debug(f" {caller_function_name}")  # debugging
                symbol = symbol.iloc[1]

            # Normalize symbol format to 'BTC/USD' for consistent comparison

            if '-' in symbol:
                ticker = symbol.replace('-', '/')
            else:
                ticker = symbol
            if '/' not in ticker:
                ticker = f"{ticker}/USD"
            asset = ticker.split('/')[0]
            ticker_value = ticker.iloc[0] if isinstance(ticker, pd.Series) else ticker

            if ticker_value == 'USD/USD' or ticker_value == 'USD':
                return 2, 2, 1e-08, 1e-08


            market = usd_pairs.set_index('asset').to_dict(orient='index')  # dataframe to dictionary
            if market.get(asset):
                base_precision = market.get(asset,{}).get('precision',{}).get('amount', 1e-08)  # Expected to be a float
                quote_precision = market.get(asset,{}).get('precision',{}).get('price', 1e-08)  # Expected to be a float
                base_increment = base_precision  # string
                quote_increment = quote_precision  # string

                if base_precision <= 0 or quote_precision <= 0:
                    raise ValueError("Precision value is zero or negative, which may cause a division error.")

                # Calculate decimal places using logarithm
                base_decimal_places = -int(math.log10(base_precision))
                quote_decimal_places = -int(math.log10(quote_precision))

                # Check for negative decimal places (should not happen if the log10 is correct)
                if base_decimal_places < 0 or quote_decimal_places < 0:
                    raise ValueError("Decimal places cannot be negative.")

                return base_decimal_places, quote_decimal_places, base_increment, quote_increment
            else:
                return 4, 2, 1e-08, 1e-08

        except ValueError as e:
            self.log_manager.error(f"fetch_precision: {e}", exc_info=True)
            return None, None, None, None
        except Exception as e:
            if "No market found" in str(e):
                self.log_manager.info(f"No market found in market cache for symbol {symbol}.")
            else:
                self.log_manager.error(f'fetch_precision: Error processing order for {symbol}: {e}',
                                       exc_info=True)

        raise ValueError(f"Symbol {symbol} not found in market_cache.")

    def adjust_price_and_size(self, order_data, order_book) -> tuple[Decimal, Decimal]:
        """
        Adjusts price and size based on order book data, ensuring proper precision.

        Args:
            order_data (dict): Order details containing side, order size, quote amount, etc.
            order_book (dict): Market order book data containing highest_bid and lowest_ask.

        Returns:
            tuple[Decimal, Decimal]: Adjusted price and size.
        """
        try:
            side = order_data['side'].upper()
            highest_bid = Decimal(str(order_book.get('highest_bid', 0)))
            lowest_ask = Decimal(str(order_book.get('lowest_ask', 0)))

            if highest_bid == 0 or lowest_ask == 0:
                raise ValueError("Invalid order book data: highest_bid or lowest_ask is zero.")

            # Determine the base price
            if side == 'SELL':
                adjusted_price = highest_bid  # Selling uses highest bid
            elif side == 'BUY':
                adjusted_price = lowest_ask  # Buying uses lowest ask
            else:
                raise ValueError(f"Unsupported order side: {side}")

            # Determine adjusted size
            if side == 'SELL':
                adjusted_size = Decimal(str(max(order_data.get('sell_amount', 0) , order_data.get('base_avail_to_trade', 0) )))
            else:  # BUY case
                quote_amount = Decimal(str(order_data.get('order_amount', 0)))
                if adjusted_price == 0:
                    raise ValueError("Adjusted price cannot be zero for BUY order.")
                adjusted_size = quote_amount / adjusted_price


            # Calculate spread and adjustment factor
            spread = lowest_ask - highest_bid
            adjustment_percentage = Decimal('0.002')  # 0.2%
            adjustment_factor = spread * adjustment_percentage

            # Ensure the adjustment factor respects the asset's precision
            precision_quote = Decimal(f"1e-{order_data.get('quote_decimal', 2)}")
            precision_base = Decimal(f"1e-{order_data.get('base_decimal',8)}")
            precision = min(precision_quote, precision_base)
            adjustment_factor = max(adjustment_factor, precision_quote)

            # Adjust both bid and ask
            adjusted_bid = highest_bid + adjustment_factor  # Increase bid slightly
            adjusted_ask = lowest_ask - adjustment_factor  # Decrease ask slightly

            # Apply adjusted price for both sides
            if side == 'BUY':
                adjusted_price = adjusted_ask
            elif side == 'SELL':
                adjusted_price = adjusted_bid

            # Ensure adjusted price and size respects precision
            adjusted_price = adjusted_price.quantize(precision_quote, rounding=ROUND_DOWN)
            adjusted_size = adjusted_size.quantize(precision_base, rounding=ROUND_DOWN)



            return adjusted_price, adjusted_size

        except Exception as e:
            self.log_manager.error(f"adjust_price_and_size: Error - {e}", exc_info=True)
            return None, None

    def adjust_precision(self, base_deci, quote_deci, num_to_adjust, convert):
        """
        Adjust the amount based on the required number of decimal places for a given symbol.
        Handles both scalar values and pandas Series/DataFrame columns.
        """
        try:
            # Determine the decimal places based on the conversion type
            if convert == 'base':
                decimal_places = base_deci
            elif convert == 'usd':
                decimal_places = 2
            elif convert == 'quote':
                decimal_places = quote_deci
            else:
                decimal_places = 8

            # Handle scalar (single value) inputs
            if isinstance(num_to_adjust, (int, float, Decimal)):
                return self.float_to_decimal(num_to_adjust, decimal_places)

            # Handle pandas Series or DataFrame column inputs
            elif isinstance(num_to_adjust, pd.Series):
                return num_to_adjust.apply(lambda x: self.float_to_decimal(x, decimal_places))

            # Handle pandas DataFrame inputs (if needed)
            elif isinstance(num_to_adjust, pd.DataFrame):
                raise ValueError("DataFrame input is not supported for num_to_adjust. Use column-based operations.")

            else:
                caller_function_name = stack()[1].function
                print(f'{caller_function_name}')
                raise TypeError(f"Unsupported input type for num_to_adjust: {type(num_to_adjust)}")

        except Exception as e:
            self.log_manager.error(f'adjust_precision: An error occurred: {e}', exc_info=True)
            return None

    def float_to_decimal(self, value, decimal_places):
        """
        Convert a float or array-like values to a Decimal with a specified number of decimal places.
        Supports scalar values and pandas Series for better compatibility with DataFrames.
        """
        try:
            # Construct a string representing the desired decimal format
            decimal_format = '0.' + '0' * decimal_places if decimal_places > 0 else '0'

            # Handle scalar values
            if isinstance(value, (int, float, str, Decimal)):
                value_decimal = Decimal(str(value)).quantize(Decimal(decimal_format), rounding=ROUND_DOWN)
                return value_decimal

            # Handle pandas Series or NumPy arrays
            elif isinstance(value, pd.Series):
                return value.apply(lambda x: Decimal(str(x)).quantize(Decimal(decimal_format), rounding=ROUND_DOWN))

            else:
                raise TypeError(f"Unsupported input type for float_to_decimal: {type(value)}")

        except Exception as e:
            self.log_manager.error(f'float_to_decimal: An error occurred: {e}. Value: {value},'
                                   f'Decimal places: {decimal_places}', exc_info=True)
            raise


    def get_decimal_format(self, base_decimal: int) -> Decimal:
        """
        Generate a Decimal format string based on the number of decimal places.

        :param base_decimal: The number of decimal places for the base value.
        :return: A Decimal object representing the format.
        """
        try:
            if base_decimal < 0:
                raise ValueError("base_decimal must be a positive integer")

            decimal_format = '0.' + ('0' * (base_decimal - 1)) + '1'
            return Decimal(decimal_format)  # example 0.00000001
        except Exception as e:
            self.log_manager.error(f'An error was detected {e}',exc_info=True)
            return Decimal(0)
