
import math
from inspect import stack  # debugging

import pandas as pd
from typing import Optional
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_EVEN, InvalidOperation, localcontext, getcontext


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

        # Initialize _usd_pairs to None (set later via set_trade_parameters)
        self._usd_pairs = None

        # Initialize dust threshold configuration for FIFO allocations
        self._init_dust_thresholds()



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
        if self._usd_pairs is None:
            # Try to load from shared_data_manager if available
            if self.shared_data_manager:
                usd_pairs = self.shared_data_manager.market_data.get('usd_pairs_cache')
                if usd_pairs is not None and not usd_pairs.empty:
                    self._usd_pairs = usd_pairs
                    return self._usd_pairs
            # Return empty DataFrame if not available
            import pandas as pd
            return pd.DataFrame()
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

    def quant_from_places(self, decimal_places: int) -> Decimal:
        """Return a quantizer Decimal like 1e-5 for decimal_places=5."""
        if not isinstance(decimal_places, int) or decimal_places < 0:
            self.logger.warning(f"⚠️ quant_from_places: invalid decimal_places={decimal_places}; defaulting to 4.")
            decimal_places = 4
        return Decimal('1').scaleb(-decimal_places)

    def safe_quantize(self, value: Decimal, precision: Decimal, rounding=ROUND_DOWN) -> Decimal:
        try:
            if value is None:
                return None
            if not isinstance(value, Decimal):
                value = Decimal(str(value))
            if not value:
                pass
            # Handle non-finite cleanly (NaN/Inf)
            if not value.is_finite():
                caller_function_name = stack()[1].function  # debugging
                print(f'{caller_function_name}')  # debugging
                self.logger.warning(f"⚠️ safe_quantize: non-finite value {value}; returning 0 at requested scale.")
                return Decimal(0).quantize(precision, rounding=rounding)
            return value.quantize(precision, rounding=rounding)
        except InvalidOperation:
            try:
                # Bump context precision enough for integer digits + scale (more predictable than len(str(...)))
                with localcontext() as ctx:
                    int_part = abs(value).to_integral_value(rounding=ROUND_DOWN)
                    int_digits = len(int_part.as_tuple().digits) or 1
                    # precision exponent is negative: e.g., precision=Decimal('1E-5') -> scale 5
                    scale = -precision.as_tuple().exponent
                    ctx.prec = max(int_digits + scale, getcontext().prec, 28)
                    return value.quantize(precision, rounding=rounding)
            except Exception as fallback_error:
                caller_function_name = stack()[1].function  # debugging
                self.logger.error(f"❌ safe_quantize failed for value={value}, precision={precision}: {fallback_error}", exc_info=True)
                return Decimal(0)  # or raise, per your policy

    def safe_convert(self, val, decimal_places: int = 4, *, rounding=ROUND_DOWN) -> Decimal:
        """
        Safely convert a value to Decimal and quantize to `decimal_places`.
        Defaults to 4 places. Truncates by default (ROUND_DOWN).
        """
        quant = self.quant_from_places(decimal_places)
        try:
            d = val if isinstance(val, Decimal) else Decimal(str(val))
        except Exception as e:
            self.logger.warning(f"⚠️ safe_convert: bad input {val!r}; returning 0 at scale {decimal_places}: {e}")
            d = Decimal(0)
        # Delegate all heavy lifting to unified quantizer
        return self.safe_quantize(d, quant, rounding=rounding)

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

    # =========================================================================
    # FIFO Allocation Support - Dust Thresholds and Granularity
    # =========================================================================

    def _init_dust_thresholds(self):
        """
        Initialize dust threshold configuration for FIFO allocations.

        Dust is the minimum amount below which we don't create allocations
        or consider inventory available. This prevents accumulated rounding
        errors and handles amounts too small to be economically meaningful.
        """
        # Default thresholds
        self.DEFAULT_DUST_THRESHOLD = Decimal('0.00001')
        self.DEFAULT_MIN_TRADE_SIZE = Decimal('0.0001')

        # Symbol-specific dust thresholds
        # These are conservative defaults - can be overridden per symbol
        self.DUST_THRESHOLDS = {
            # Major pairs - BTC
            'BTC-USD': Decimal('0.00001'),    # ~$0.50 at $50k BTC
            'BTC-USDT': Decimal('0.00001'),

            # Major pairs - ETH
            'ETH-USD': Decimal('0.0001'),     # ~$0.30 at $3k ETH
            'ETH-USDT': Decimal('0.0001'),

            # Layer 1s
            'SOL-USD': Decimal('0.001'),      # ~$0.10 at $100 SOL
            'ADA-USD': Decimal('0.1'),        # ~$0.05 at $0.50 ADA
            'AVAX-USD': Decimal('0.01'),

            # Altcoins - Higher Value
            'LINK-USD': Decimal('0.01'),
            'UNI-USD': Decimal('0.01'),
            'AAVE-USD': Decimal('0.001'),

            # Altcoins - Medium Value
            'MATIC-USD': Decimal('0.1'),
            'DOT-USD': Decimal('0.01'),
            'ATOM-USD': Decimal('0.01'),

            # Altcoins - Lower Value
            'DOGE-USD': Decimal('1.0'),       # ~$0.10 at $0.10 DOGE
            'XRP-USD': Decimal('0.1'),
            'TRX-USD': Decimal('1.0'),

            # Meme/Low-Value Coins
            'SHIB-USD': Decimal('1000'),      # 1000 SHIB minimum
            'PEPE-USD': Decimal('10000'),

            # Stablecoins
            'USDC-USD': Decimal('0.01'),      # 1 cent
            'USDT-USD': Decimal('0.01'),
            'DAI-USD': Decimal('0.01'),
        }

        # Minimum trade sizes (exchange minimums)
        self.MIN_TRADE_SIZES = {
            'BTC-USD': Decimal('0.0001'),     # ~$5 at $50k
            'ETH-USD': Decimal('0.001'),      # ~$3 at $3k
            'SOL-USD': Decimal('0.01'),
            'DOGE-USD': Decimal('10.0'),
            'SHIB-USD': Decimal('10000'),
            # Add more as needed
        }

    def get_dust_threshold(self, symbol: str) -> Decimal:
        """
        Get dust threshold for a trading pair.

        Dust is the minimum amount below which we don't create allocations
        or consider inventory available.

        Args:
            symbol: Trading pair (e.g., 'BTC-USD')

        Returns:
            Decimal dust threshold

        Example:
            >>> precision_utils.get_dust_threshold('BTC-USD')
            Decimal('0.00001')
        """
        # Normalize symbol format (handle both 'BTC-USD' and 'BTC/USD')
        normalized = symbol.replace('/', '-')
        return self.DUST_THRESHOLDS.get(normalized, self.DEFAULT_DUST_THRESHOLD)

    def get_min_trade_size(self, symbol: str) -> Decimal:
        """
        Get minimum trade size for a trading pair.

        This is the exchange's minimum order size.

        Args:
            symbol: Trading pair (e.g., 'BTC-USD')

        Returns:
            Decimal minimum trade size
        """
        normalized = symbol.replace('/', '-')
        return self.MIN_TRADE_SIZES.get(normalized, self.DEFAULT_MIN_TRADE_SIZE)

    def is_dust(self, value: Decimal, symbol: str) -> bool:
        """
        Check if a value is considered dust for a symbol.

        Args:
            value: Amount to check
            symbol: Trading pair

        Returns:
            True if value <= dust threshold

        Example:
            >>> precision_utils.is_dust(Decimal('0.000001'), 'BTC-USD')
            True  # Less than 0.00001 threshold
        """
        if not isinstance(value, Decimal):
            value = self.safe_decimal(value)

        threshold = self.get_dust_threshold(symbol)
        return value <= threshold

    def validate_trade_size(self, size: Decimal, symbol: str) -> bool:
        """
        Validate that a trade size meets minimum requirements.

        Args:
            size: Trade size to validate
            symbol: Trading pair

        Returns:
            True if size is valid (>= min_trade_size)

        Example:
            >>> precision_utils.validate_trade_size(Decimal('0.0001'), 'BTC-USD')
            True  # Meets minimum
            >>> precision_utils.validate_trade_size(Decimal('0.00001'), 'BTC-USD')
            False  # Below minimum
        """
        if not isinstance(size, Decimal):
            size = self.safe_decimal(size)

        min_size = self.get_min_trade_size(symbol)
        return size >= min_size

    def round_with_bankers(self, value: Decimal, symbol: str, is_base: bool = True) -> Decimal:
        """
        Round a value using banker's rounding (ROUND_HALF_EVEN) for fairness.

        Banker's rounding minimizes bias by rounding .5 to the nearest even number.
        This is important for FIFO allocations to prevent systematic over/under allocation.

        Args:
            value: The decimal value to round
            symbol: Trading pair (e.g., 'BTC-USD')
            is_base: True if base currency (BTC), False if quote (USD)

        Returns:
            Rounded Decimal value

        Example:
            >>> precision_utils.round_with_bankers(Decimal('0.123456789'), 'BTC-USD', is_base=True)
            Decimal('0.12345679')  # Rounded to 8 decimals with banker's rounding
        """
        try:
            base_prec, quote_prec, _, _ = self.fetch_precision(symbol)
            decimals = base_prec if is_base else quote_prec

            quantizer = Decimal('1') / (Decimal('10') ** decimals)
            return value.quantize(quantizer, rounding=ROUND_HALF_EVEN)

        except Exception as e:
            self.logger.error(f"❌ round_with_bankers failed for {symbol}: {e}", exc_info=True)
            # Fallback to safe_quantize with ROUND_HALF_EVEN
            return self.safe_quantize(value, Decimal('1e-8'), rounding=ROUND_HALF_EVEN)

    def set_dust_threshold(self, symbol: str, threshold: Decimal) -> None:
        """
        Set or update dust threshold for a symbol.

        Useful for dynamically adjusting thresholds based on price changes
        or adding new trading pairs.

        Args:
            symbol: Trading pair (e.g., 'NEW-USD')
            threshold: New dust threshold (Decimal)

        Example:
            >>> precision_utils.set_dust_threshold('NEW-USD', Decimal('0.001'))
        """
        normalized = symbol.replace('/', '-')
        self.DUST_THRESHOLDS[normalized] = threshold
        self.logger.info(f"✅ Dust threshold for {normalized} set to {threshold}")

    def set_min_trade_size(self, symbol: str, min_size: Decimal) -> None:
        """
        Set or update minimum trade size for a symbol.

        Args:
            symbol: Trading pair
            min_size: New minimum trade size (Decimal)
        """
        normalized = symbol.replace('/', '-')
        self.MIN_TRADE_SIZES[normalized] = min_size
        self.logger.info(f"✅ Minimum trade size for {normalized} set to {min_size}")

    def format_for_display(self, value: Decimal, symbol: str, is_base: bool = True) -> str:
        """
        Format a value for human-readable display with appropriate precision.

        Args:
            value: Decimal value to format
            symbol: Trading pair
            is_base: True if base currency, False if quote

        Returns:
            Formatted string

        Example:
            >>> precision_utils.format_for_display(Decimal('0.12345678'), 'BTC-USD', is_base=True)
            '0.12345678 BTC'
        """
        try:
            base_prec, quote_prec, _, _ = self.fetch_precision(symbol)
            decimals = base_prec if is_base else quote_prec

            # Get currency name
            normalized = symbol.replace('/', '-')
            base, quote = normalized.split('-')
            currency = base if is_base else quote

            # Format with appropriate precision
            format_str = f"{{:.{decimals}f}} {currency}"
            return format_str.format(value)

        except Exception as e:
            self.logger.error(f"❌ format_for_display failed for {symbol}: {e}")
            return str(value)
