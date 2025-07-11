
import math
from inspect import stack  # debugging

import pandas as pd
from typing import Optional
from decimal import localcontext, Decimal, ROUND_DOWN, InvalidOperation


class PrecisionUtils:
    _instance = None  # Singleton instance

    @classmethod
    def get_instance(cls, logger_manager, shared_data_manager):
        """ Ensures only one instance of PrecisionUtils is created. """
        if cls._instance is None:
            cls._instance = cls(logger_manager, shared_data_manager)
        return cls._instance

    def __init__(self, logger_manager, shared_data_manager):
        """ Initialize PrecisionUtils. """
        if PrecisionUtils._instance is not None:
            raise Exception("This class is a singleton! Use get_instance() instead.")

        self.logger = logger_manager.loggers.get('shared_logger') # 🙂
        self.shared_data_manager = shared_data_manager



    def set_trade_parameters(self):
        if not self.shared_data_manager:
            self.logger.error("❌ Cannot set parameters: shared_data_manager not bound")
            return

        usd_pairs = self.shared_data_manager.market_data.get('usd_pairs_cache')
        if usd_pairs is None or usd_pairs.empty:
            self.logger.warning("⚠️ usd_pairs_cache is empty during set_trade_parameters")
            return

        self._usd_pairs = usd_pairs
        self.logger.info(f"✅ PrecisionUtils set_trade_parameters() — {len(usd_pairs)} pairs loaded")

    @property
    def usd_pairs(self):
        return self._usd_pairs

    # def bind_shared_data(self, shared_data_manager):
    #     self.shared_data_manager = shared_data_manager

    def compute_safe_base_size(self, reported: Decimal, base_decimal: int, max_value: Optional[Decimal] = None) -> Decimal:
        buffer = Decimal(f"1e-{base_decimal}")
        safe = reported - buffer
        if max_value is not None:
            safe = min(safe, max_value)
        return safe.quantize(Decimal(f"1e-{base_decimal}"), rounding=ROUND_DOWN)

    def safe_decimal(self, value, default="0"):
        try:
            return Decimal(value)
        except (TypeError, ValueError, InvalidOperation):
            return Decimal(default)

    def safe_convert(self, val, decimal_places: int = 1) -> Decimal:
        """
        Safely convert a value to Decimal and quantize it using the given number of decimal places.
        If decimal_places is invalid, defaults to 4.
        """
        try:
            self.logger.debug(f"🔍 Converting val={val} with decimal_places={decimal_places}")
            if not isinstance(decimal_places, int) or decimal_places < 0:
                self.logger.warning(f"⚠️ safe_convert: Invalid decimal_places={decimal_places}. Defaulting to 1.")
                decimal_places = 1

            quantize_format = Decimal('1').scaleb(-decimal_places)
            return Decimal(str(val)).quantize(quantize_format, rounding=ROUND_DOWN)

        except (InvalidOperation, ValueError, TypeError) as e:
            self.logger.warning(f"⚠️ safe_convert: Could not convert value={val} with decimal_places={decimal_places}: {e}")
            fallback_format = Decimal('1').scaleb(-decimal_places if isinstance(decimal_places, int) and decimal_places >= 0 else -1)
            return Decimal('0').quantize(fallback_format)

    def safe_quantize(self, value: Decimal, precision: Decimal, rounding=ROUND_DOWN) -> Decimal:
        try:
            return value.quantize(precision, rounding=rounding)
        except InvalidOperation:
            try:
                with localcontext() as ctx:
                    ctx.prec = max(len(str(value).replace('.', '').replace('-', '')), 28)  # generous precision
                    return value.quantize(precision, rounding=rounding)
            except Exception as fallback_error:
                self.logger.error(f"❌ safe_quantize failed for value={value}, precision={precision}: {fallback_error}")
                return Decimal(0)  # Or raise, depending on your policy

    def fetch_precision(self, symbol: str, *, usd_pairs_override: Optional[pd.DataFrame] = None) -> tuple:
        """
        Fetch the precision for base and quote currencies of a given symbol.

        :param symbol: The symbol to fetch precision for, in the format 'BTC-USD' or 'BTC/USD'.
        :return: A tuple containing base and quote decimal places.
        """

        try:
            usd_pairs_df = usd_pairs_override if usd_pairs_override is not None else self.usd_pairs

            if usd_pairs_df is None or usd_pairs_df.empty:
                self.logger.warning("⚠️ fetch_precision: usd_pairs is empty. Using default values.")
                return 4, 2, 1e-08, 1e-08

            if isinstance(symbol, pd.Series):
                caller_function_name = stack()[1].function  # debugging
                self.logger.debug(f" {caller_function_name}")  # debugging
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

            market = usd_pairs_df.set_index('asset').to_dict(orient='index')  # dataframe to dictionary
            if market.get(asset):
                base_precision = Decimal(market.get(asset,{}).get('precision',{}).get('base_increment', 1e-08) ) # Expected to be a float
                quote_precision = Decimal(market.get(asset,{}).get('precision',{}).get('quote_increment', 1e-08) ) # Expected to be a float
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
                return 0, 2, 1e-08, 1e-08

        except ValueError as e:
            self.logger.error(f"❌ fetch_precision: {e}", exc_info=True)
            return None, None, None, None
        except Exception as e:
            if "No market found" in str(e):
                self.logger.info(f"No market found in market cache for symbol {symbol}.")
            else:
                self.logger.error(f'fetch_precision: Error processing order for {symbol}: {e}', exc_info=True)

        raise ValueError(f"Symbol {symbol} not found in market_cache.")

    def adjust_price_and_size(self, order_data, order_book) -> tuple[Decimal, Decimal]:
        """
        Adjusts price and size based on order book data, ensuring proper precision and size limits.
        Returns (adjusted_price, adjusted_size).
        """
        try:
            # Precision settings
            try:
                quote_decimal = int(order_data.get('quote_decimal', 3))
                base_decimal = int(order_data.get('base_decimal', 8))
                precision_quote = Decimal(f"1e-{quote_decimal}")
                precision_base = Decimal(f"1e-{base_decimal}")
            except Exception as e:
                self.logger.error(f"❌ Failed to parse decimal precision values: {e}")
                return None, None

            caller_function = stack()[1].function
            side = str(order_data.get('side', '')).upper()

            # Defensive conversion of order book prices
            try:
                highest_bid = self.safe_convert(order_book.get('highest_bid'), quote_decimal)


                lowest_ask = self.safe_convert(order_book.get('lowest_ask'), quote_decimal)
            except InvalidOperation as e:
                self.logger.error(f"❌ InvalidOperation on bid/ask conversion: {e} — Data: {order_book}")
                return None, None

            if highest_bid == 0 or lowest_ask == 0:
                self.logger.warning("🚫 Invalid order book data: highest_bid or lowest_ask is zero.")
                return None, None

            self.logger.debug(f"📈 highest_bid: {highest_bid}, lowest_ask: {lowest_ask}, side: {side}")

            # Spread adjustment
            try:
                spread = lowest_ask - highest_bid
                adjustment_percentage = Decimal('0.002')
                adjustment_factor = max(spread * adjustment_percentage, Decimal(f"1e-{order_data.get('quote_decimal', 3)}"))
            except InvalidOperation as e:
                self.logger.error(f"❌ InvalidOperation calculating spread/adjustment_factor: {e}")
                return None, None

            # Adjusted prices
            try:
                adjusted_bid = highest_bid + adjustment_factor
                adjusted_ask = lowest_ask - adjustment_factor
            except InvalidOperation as e:
                self.logger.error(f"❌ InvalidOperation adjusting bid/ask: {e}")
                return None, None
            # Fee rate
            try:
                fee_rate = self.safe_convert(order_data.get('taker_fee')) if order_data.get('type') == 'market' else Decimal(
                    str(order_data.get('maker_fee')))
            except (InvalidOperation, TypeError) as e:
                self.logger.error(f"❌ Fee rate parsing error: {e}")
                return None, None

            # Order side logic
            try:
                if side == 'BUY':
                    net_proceeds = adjusted_ask * (Decimal("1.0") - fee_rate)
                    adjusted_price = self.safe_quantize(net_proceeds, precision_quote)

                    quote_amount_fiat = self.safe_convert(order_data.get('order_amount_fiat'), quote_decimal)
                    if adjusted_price <= 0:
                        raise ValueError("Adjusted price must be > 0 for BUY order.")

                    adjusted_size = quote_amount_fiat / adjusted_price
                    adjusted_size = self.safe_quantize(adjusted_size, precision_base)

                elif side == 'SELL':
                    gross_cost = adjusted_bid * (Decimal("1.0") + fee_rate)
                    adjusted_price = self.safe_quantize(gross_cost, precision_quote)

                    base_avail = self.safe_convert(order_data.get('base_avail_to_trade'), base_decimal)
                    sell_amount = self.safe_convert(order_data.get('sell_amount'), base_decimal)
                    raw_size = min(sell_amount, base_avail)

                    safety_margin = precision_base * 2  # 2 ticks
                    adjusted_size = self.safe_quantize(raw_size - safety_margin, precision_base)
                    if adjusted_size > base_avail:
                        adjusted_size = self.safe_quantize(base_avail, precision_base)
                else:
                    raise ValueError(f"Unsupported order side: {side}")

            except (InvalidOperation, ValueError, ZeroDivisionError) as e:
                self.logger.error(f"❌ Error during price/size adjustment: {e}", exc_info=True)
                return None, None

            # Final checks
            if adjusted_price is None or adjusted_size is None:
                self.logger.warning("🚨 Adjusted price or size is None.")
                return None, None
            if not isinstance(adjusted_price, Decimal) or not isinstance(adjusted_size, Decimal):
                self.logger.warning("🚨 Adjusted values are not Decimals.")
                return None, None

            self.logger.debug(f"✅ Final adjusted_price={adjusted_price}, adjusted_size={adjusted_size}")
            if adjusted_size == 0:
                pass
            return adjusted_price, adjusted_size

        except Exception as e:
            self.logger.error(f"❌ adjust_price_and_size (outer): Unexpected error — {e}", exc_info=True)
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
            if num_to_adjust is None:
                return None

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
            self.logger.error(f'❌ adjust_precision: An error occurred: {e}', exc_info=True)
            return None

    def float_to_decimal(self, value, decimal_places: int = 4) -> Decimal | pd.Series:
        """
        Convert a float, int, str, or pandas Series to a Decimal with specified decimal places.
        Uses safe_convert() internally to guard against InvalidOperation errors.

        Args:
            value: Single value or pd.Series to convert.
            decimal_places: Number of decimal places to quantize to.

        Returns:
            Decimal or Series of Decimals quantized to the specified precision.
        """
        try:
            # Handle scalar values
            if isinstance(value, (int, float, str, Decimal)):
                return self.safe_convert(value, decimal_places)

            # Handle pandas Series
            elif isinstance(value, pd.Series):
                return value.apply(lambda x: self.safe_convert(x, decimal_places))

            else:
                raise TypeError(f"Unsupported input type for float_to_decimal: {type(value)}")

        except Exception as e:
            self.logger.error(
                f'❌ float_to_decimal: Error converting value={value} to Decimal with {decimal_places} places: {e}',
                exc_info=True
            )
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
            self.logger.error(f'❌ An error was detected {e}', exc_info=True)
            return Decimal(0)
