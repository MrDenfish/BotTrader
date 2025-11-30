import asyncio
import os
from decimal import Decimal
from typing import Optional
import pandas as pd
import decimal
import random
from collections import defaultdict, deque
from http.client import RemoteDisconnected
from pandas.core.methods.describe import select_describe_func
from requests.exceptions import HTTPError
from ccxt.base.errors import BadSymbol



class MyPortfolioPosition:
    def __init__(self, asset, available_to_trade, total_balance):
        self.asset = asset
        self.available_to_trade = Decimal(available_to_trade)
        self.total_balance = Decimal(total_balance)

    def __repr__(self):
        return f"PortfolioPosition(asset={self.asset}, available_to_trade={self.available_to_trade}, total_balance={self.total_balance})"

class TickerManager:
    _instance = None
    _lock = asyncio.Lock()  # Ensures thread-safety in an async environment

    @classmethod
    async def get_instance(cls, config, coinbase_api, test_debug_maint, shared_utils_print, shared_utils_color, logger_manager,
                           order_book_manager, rest_client, portfolio_uuid, exchange, ccxt_api, shared_data_manager, shared_utils_precision):
        """Ensures only one instance of TickerManager is created."""
        if cls._instance is None:
            async with cls._lock:
                if cls._instance is None:  # Double-check after acquiring the lock
                    cls._instance = cls(config, coinbase_api, test_debug_maint,
                                        shared_utils_print, shared_utils_color,logger_manager,
                                        order_book_manager, rest_client, portfolio_uuid, exchange,
                                        ccxt_api,shared_data_manager, shared_utils_precision)
        return cls._instance

    def __init__(self, config, coinbase_api, test_debug_maint, shared_utils_print, shared_utils_color, logger_manager, order_book_manager,
                 rest_client, portfolio_uuid, exchange, ccxt_api, shared_data_manager, shared_utils_precision):
        if TickerManager._instance is not None:
            raise Exception("TickerManager is a singleton and has already been initialized!")
        self.bot_config = config
        self.exchange = exchange
        self.rest_client = rest_client
        self.portfolio_uuid = portfolio_uuid
        self.min_quote_volume = None
        self.last_ticker_update = None
        self.shill_coins = self.bot_config._shill_coins
        self._min_value_to_monitor = self.bot_config.min_value_to_monitor
        self.logger_manager = logger_manager  # 🙂
        if logger_manager.loggers['shared_logger'].name == 'shared_logger':  # 🙂
            self.logger = logger_manager.loggers['shared_logger']
        self.ccxt_api = ccxt_api
        self.coinbase_api = coinbase_api
        self.order_book_manager = order_book_manager
        self.shared_data_manager = shared_data_manager
        self.shared_utils_print = shared_utils_print
        self.test_debug_maint = test_debug_maint
        self.shared_utils_precision = shared_utils_precision
        self.mid_history = defaultdict(lambda: deque(maxlen=120))
        self.enrich_limit = self.bot_config.enrich_limit
        self.start_time = None

    # Potentially for future use
    @property
    def market_data(self):
        return self.shared_data_manager.market_data

    @property
    def min_value_to_monitor(self):
        return self._min_value_to_monitor  # Minimum order amount

    @property
    def ticker_cache(self):
        return self.shared_data_manager.market_data.get("ticker_cache", {})

    @property
    def open_orders(self):
        return self.shared_data_manager.order_management.get('order_tracker', {})
    # <><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><>

    #--------------> Helper Methods <----------------

    def _select_active_products_by_liquidity(self, df, formatted_tickers, limit: int = 20) -> set[str]:
        # Guard against missing cols
        try:
            cols = df.columns
            need = {"symbol", "24h_quote_volume", "type", "quote"}
            if not need.issubset(set(cols)):
                return set(list(formatted_tickers.keys())[:limit])

            # Keep USD spot only (adjust if you also trade perps)
            filt = (df["type"].astype(str).str.upper() == "SPOT") & (df["quote"].astype(str).str.upper() == "USD")

            # Rank by 24h quote volume desc
            ranked = (
                df.loc[filt, ["symbol", "24h_quote_volume"]]
                .dropna(subset=["symbol", "24h_quote_volume"])
                .sort_values("24h_quote_volume", ascending=False)
                .head(limit * 2)  # oversample a bit; we'll intersect next
            )

            # Intersect with what we actually have prices for this cycle
            have_ticks = set(formatted_tickers.keys())
            active = [sym for sym in ranked["symbol"].tolist() if sym in have_ticks]
            return set(active[:limit])
        except Exception as e:
            self.logger.error(f"❌ Error in _select_active_products_by_liquidity: {e}", exc_info=True)
            return set(list(formatted_tickers.keys())[:limit])

    async def update_ticker_cache(self, open_orders=None, start_time=None) -> tuple:
        """
        Update market_data dictionary with market and portfolio data.

        Args:
            open_orders (list): List of open orders (optional).
            start_time (float): Start time for market updates (optional).

        Returns:
            tuple: Updated ticker cache and additional metadata.
        """
        try:
            # Fetch market data and portfolio balances concurrently
            market_data_task = self.coinbase_api.fetch_all_products()
            balances_task = self.fetch_and_filter_balances(self.portfolio_uuid)
            market_data, non_zero_balances = await asyncio.gather(market_data_task, balances_task)
            if not market_data:
                self.logger.error("Market data retrieval failed, returning empty dataset.")
                return {}, {}

            if not non_zero_balances:
                self.logger.warning("No non-zero balances found, returning empty dataset.")

            # Process market data
            filtered_vol_data, usd_pairs, avg_volume = self.filter_volume_for_market_data(market_data)
            supported_vol_markets, supported_usd_markets = await self.filter_markets_by_criteria(
                filtered_vol_data, usd_pairs
            )

            # Prepare DataFrame caches
            tickers_cache = self.prepare_dataframe(supported_vol_markets, non_zero_balances)
            usd_pairs_cache = self.prepare_dataframe(supported_usd_markets, non_zero_balances)

            if tickers_cache.empty:
                self.logger.error("Ticker cache is empty.")
                return {}, {}

            # Fetch current prices and update ticker cache
            tickers_cache, bid_ask_spread = await self.parallel_fetch_and_update(usd_pairs_cache, tickers_cache)

            # Process spot positions and include precision data
            spot_positions = self.process_spot_positions(non_zero_balances, tickers_cache, usd_pairs_cache)
            open_orders = await self.coinbase_api.fetch_open_orders()
            open_orders_dict = {
                o["id"]: o for o in open_orders if isinstance(o, dict) and "id" in o
            }

            # Calculate ATR for active positions
            product_ids = usd_pairs_cache["symbol"].tolist() if not usd_pairs_cache.empty else []
            atr_pct_cache, atr_price_cache = await self._calculate_atr_for_products(
                product_ids, spot_positions
            )

            # Calculate average quote volume
            min_quote_volume = tickers_cache['24h_quote_volume'].min()

            return {
                "ticker_cache": tickers_cache,
                "filtered_vol": supported_vol_markets,
                "usd_pairs_cache": usd_pairs_cache,
                "bid_ask_spread": bid_ask_spread,
                "atr_pct_cache": atr_pct_cache,
                "atr_price_cache": atr_price_cache,
                "avg_quote_volume": Decimal(min_quote_volume).quantize(Decimal('0')),
                "spot_positions": spot_positions
            }, {"non_zero_balances": non_zero_balances, 'order_tracker': open_orders_dict}


        except asyncio.CancelledError:
            self.logger.error("❌ update_ticker_cache was cancelled.", exc_info=True)
            raise  # Re-raise so caller is aware of the cancellation

        except Exception as e:
            self.logger.error(f"❌ Error in update_ticker_cache: {e}", exc_info=True)
            return {}, {}

    async def fetch_and_filter_balances(self, portfolio_uuid: str) -> dict:
        """
        Fetch portfolio breakdown and filter non-zero balances, including staked funds.

        Args:
            portfolio_uuid (str): The UUID of the portfolio.

        Returns:
            dict: Consolidated non-zero balances as PortfolioPosition objects
                  (wallet + staked funds merged).
        """
        try:
            portfolio_data = await self.get_portfolio_breakdown(portfolio_uuid)
            if not portfolio_data:
                self.logger.warning("⚠️ Portfolio breakdown failed, attempting fallback to get_accounts()")
                return await self._fetch_balances_from_accounts()

            spot_positions = portfolio_data.breakdown.spot_positions
            non_zero_balances = {}

            for pos in spot_positions:
                asset = pos.asset  # ✅ PortfolioPosition object attribute
                total_balance_fiat = Decimal(str(pos.total_balance_fiat))

                # ✅ Only include if above min threshold or USD
                if total_balance_fiat <= self.min_value_to_monitor and asset != "USD":
                    continue

                if asset in non_zero_balances:
                    existing = non_zero_balances[asset]

                    # ---- Convert both to dicts for merging ----
                    existing_dict = existing.__dict__.copy()
                    pos_dict = pos.__dict__.copy()

                    # ---- Merge balances ----
                    existing_balance = Decimal(str(existing_dict.get("total_balance_crypto", "0")))
                    new_balance = Decimal(str(pos_dict.get("total_balance_crypto", "0")))
                    total_balance_crypto = existing_balance + new_balance

                    # ---- Merge cost basis ----
                    existing_cost_basis = Decimal(str(existing_dict.get("cost_basis", {}).get("value", "0")))
                    new_cost_basis = Decimal(str(pos_dict.get("cost_basis", {}).get("value", "0")))
                    total_cost_basis = existing_cost_basis + new_cost_basis

                    # ---- Weighted average entry price ----
                    existing_avg_price = Decimal(str(existing_dict.get("average_entry_price", {}).get("value", "0")))
                    new_avg_price = Decimal(str(pos_dict.get("average_entry_price", {}).get("value", "0")))

                    if total_balance_crypto > 0:
                        weighted_avg_price = (
                                                     (existing_avg_price * existing_balance) + (new_avg_price * new_balance)
                                             ) / total_balance_crypto
                    else:
                        weighted_avg_price = new_avg_price

                    # ✅ Update merged dict
                    existing_dict["total_balance_crypto"] = float(total_balance_crypto)
                    existing_dict["cost_basis"]["value"] = str(total_cost_basis)
                    existing_dict["average_entry_price"]["value"] = str(weighted_avg_price)

                    # ✅ Convert back to PortfolioPosition object
                    non_zero_balances[asset] = type(existing)(**existing_dict)

                else:
                    # ✅ Keep original PortfolioPosition object
                    non_zero_balances[asset] = pos

            return non_zero_balances

        except Exception as e:
            self.logger.error(f"❌ Error in fetch_and_filter_balances: {e}", exc_info=True)
            self.logger.warning("⚠️ Attempting fallback to get_accounts() due to error")
            try:
                return await self._fetch_balances_from_accounts()
            except Exception as fallback_error:
                self.logger.error(f"❌ Fallback also failed: {fallback_error}", exc_info=True)
                return {}

    async def _fetch_balances_from_accounts(self) -> dict:
        """
        Fallback method to fetch balances using get_accounts() API when
        get_portfolio_breakdown() fails.

        Returns balances in a compatible format (though without average_entry_price
        and some other portfolio-specific fields).
        """
        self.logger.info("📞 Fetching balances from get_accounts() fallback")

        try:
            accounts_response = await asyncio.to_thread(self.rest_client.get_accounts)

            if not accounts_response or not hasattr(accounts_response, 'accounts'):
                self.logger.warning("⚠️ get_accounts() returned empty or invalid data")
                return {}

            non_zero_balances = {}

            for account in accounts_response.accounts:
                asset = account.currency
                available_balance = Decimal(str(account.available_balance.value))
                hold_balance = Decimal(str(account.hold.value))
                total_balance_crypto = available_balance + hold_balance

                # Skip if below minimum threshold (except USD)
                # Note: We don't have fiat value here, so use a conservative crypto threshold
                if total_balance_crypto <= Decimal('0.0001') and asset != 'USD':
                    continue

                # Create a simplified position dict compatible with process_spot_positions
                # Note: Missing average_entry_price and unrealized_pnl - position_monitor will skip these
                position = {
                    'asset': asset,
                    'total_balance_crypto': str(total_balance_crypto),
                    'available_to_trade_crypto': str(available_balance),
                    'total_balance_fiat': '0',  # Not available from get_accounts
                    'available_to_trade_fiat': '0',
                    'allocation': '0',
                    'unrealized_pnl': {'value': '0', 'currency': 'USD'},  # Not available
                    'cost_basis': {'value': '0', 'currency': 'USD'},  # Not available
                    # Missing average_entry_price - will cause position_monitor to skip
                    'precision': str(account.currency),
                    'available_to_transfer_fiat': '0',
                    'available_to_transfer_crypto': str(available_balance),
                    'funding_pnl': {'value': '0', 'currency': 'USD'},
                    'available_to_send_fiat': '0',
                    'available_to_send_crypto': str(available_balance),
                }

                non_zero_balances[asset] = position

            self.logger.info(f"✅ Fallback get_accounts() returned {len(non_zero_balances)} positions")
            return non_zero_balances

        except Exception as e:
            self.logger.error(f"❌ Error in _fetch_balances_from_accounts: {e}", exc_info=True)
            return {}

    def process_spot_positions( self, non_zero_balances: dict,
                                tickers_cache: pd.DataFrame,
                                usd_pairs_cache: Optional[pd.DataFrame] = None) -> dict:
        """
        Process spot positions by rounding numeric values to proper precision
        and merging precision metadata into the output.

        Args:
            non_zero_balances (dict): Dictionary of custom objects for non-zero balances.
            tickers_cache (pd.DataFrame): DataFrame containing ticker information (not used here).
            usd_pairs_cache (Optional[pd.DataFrame]): Optional cache to assist in precision lookup.

        Returns:
            dict: Processed spot positions with rounded numeric values and precision metadata.
        """
        try:
            processed_positions = {}
            field_precision = None

            for asset, data in non_zero_balances.items():
                # Fetch base (crypto) and quote (USD) decimal precision
                base_deci, quote_deci, _, _ = self.shared_utils_precision.fetch_precision(
                    asset,
                    usd_pairs_override=usd_pairs_cache
                )

                precision_info = {
                    "amount": base_deci,
                    "price": quote_deci
                }

                # Convert object to dictionary form if needed
                data_dict = vars(data) if hasattr(data, '__dict__') else data
                processed_position = {"precision": precision_info}

                for key, value in data_dict.items():
                    if isinstance(value, (float, int, Decimal)) and not isinstance(value, bool):
                        try:
                            decimal_value = Decimal(str(value))

                            # Determine field-specific precision
                            if key in ['total_balance_crypto', 'available_to_trade_crypto', 'available_to_transfer_crypto']:
                                field_precision = Decimal(f'1e-{base_deci}')
                            elif key in ['total_balance_fiat', 'available_to_trade_fiat', 'available_to_transfer_fiat']:
                                field_precision = Decimal(f'1e-{quote_deci}')
                            else:
                                # Fallback precision for other numerics
                                field_precision = Decimal('1.0') if decimal_value == decimal_value.to_integral() else Decimal(f'1e-{quote_deci}')

                            # Quantize the value safely
                            processed_position[key] = self.shared_utils_precision.safe_quantize(decimal_value, field_precision)

                        except (decimal.InvalidOperation, ValueError) as e:
                            self.logger.warning(
                                f"⚠️ Failed to quantize {key}={value} with precision={field_precision}: {e}"
                            )
                            processed_position[key] = Decimal("0")

                processed_positions[asset] = processed_position

            return processed_positions

        except Exception as e:
            self.logger.error(f"❌ Error in process_spot_positions: {e}", exc_info=True)
            return {}

    def safe_float(self, val, default=0.0):
        try:
            return float(val)
        except (ValueError, TypeError):
            return default

    def filter_volume_for_market_data(self, market_data):
        """
        Filters market data from Coinbase REST /products endpoint.

        Returns:
            - filtered_data: markets with quote volume >= average
            - usd_pairs: all USD-quoted pairs regardless of volume
            - average_quote_volume: mean of valid quote volumes
        """
        try:
            filtered_data = []
            usd_pairs = []
            valid_volumes = []

            # First pass: collect all valid USD quote volumes
            for item in market_data:
                try:
                    if item.get('quote_currency_id') != 'USD':
                        continue

                    quote_volume = self.safe_float(item.get('approximate_quote_24h_volume'))
                    if quote_volume > 0:
                        valid_volumes.append(quote_volume)
                except Exception as e:
                    self.logger.debug(f"⏩ Skipping item during volume average scan: {e}")
                    continue

            if not valid_volumes:
                self.logger.debug("⚠️ No valid quote volumes found for averaging.")
                return [], [], None

            average_quote_volume = sum(valid_volumes) / len(valid_volumes)

            # Second pass: filter and format usable entries
            for item in market_data:
                try:
                    if (
                            item.get('quote_currency_id') != 'USD'
                            or item.get('product_type') != 'SPOT'
                            or item.get('status') != 'online'
                    ):
                        continue

                    quote_volume = self.safe_float(item.get('approximate_quote_24h_volume'))
                    volume_24h = self.safe_float(item.get('volume_24h'))
                    price = self.safe_float(item.get('price'))
                    price_change = self.safe_float(item.get('price_percentage_change_24h'))

                    common_entry = {
                        'asset': item.get('base_currency_id'),
                        'quote': item.get('quote_currency_id'),
                        'symbol': item.get('product_id'),
                        'precision': {
                            'base_increment': item.get('base_increment'),
                            'quote_increment': item.get('quote_increment'),
                            'price_increment': item.get('price_increment')
                        },
                        'info': {
                            'product_id': item.get('product_id'),
                            'type': item.get('product_type'),
                            'price': price,
                            'volume_24h': volume_24h,
                            '24h_quote_volume': quote_volume,
                            'price_percentage_change_24h': price_change,
                            'average_min_vol': average_quote_volume
                        }
                    }

                    usd_pairs.append(common_entry)

                    if quote_volume >= average_quote_volume:
                        filtered_data.append(common_entry)

                except Exception as e:
                    self.logger.debug(f"⏩ Skipping item during filter: {e}")
                    continue

            return filtered_data, usd_pairs, average_quote_volume

        except Exception as e:
            self.logger.error(f"❌ Error in filter_volume_for_market_data: {e}", exc_info=True)
            return [], [], 0

    async def filter_markets_by_criteria(self, minimum_volume_market_data, usd_pairs):
        """
        Filter markets based on 24-hour quote volume and USD quote only.
        Filter out undesirable coins

        Args:
            minimum_volume_market_data (list): Markets filtered by volume.
            usd_pairs (list): Markets with USD as the quote currency.

        Returns:
            tuple: Filtered markets based on volume and USD pairs.
        """
        try:
            # msg = self.test_debug_maint.debug_code(os.path.abspath(__file__),stack()) #debugging
            # Filter markets by 24-hour quote volume
            supported_markets_vol = [
                market for market in minimum_volume_market_data
                if float(market['info'].get('24h_quote_volume', 0)) > 0
            ]

            # Filter markets by USD quote
            supported_markets_usd = [
                market for market in usd_pairs if market.get('quote') == 'USD'
            ]
            # filter out sketchy coins
            supported_markets_usd = [
                market for market in supported_markets_usd if market.get('asset') not in self.shill_coins
            ]
            return supported_markets_vol, supported_markets_usd
        except Exception as e:
            self.logger.error(f"❌ Error filtering markets: {e}", exc_info=True)
            return [], []

    def prepare_dataframe(self, tickers_dict, balances):
        """
        Prepare a DataFrame of tickers and balances.

        Args:
            tickers_dict (list): List of market data dictionaries containing asset info.
            balances (dict): Dictionary of balances, where each value is a PortfolioPosition object.

        Returns:
            pd.DataFrame: A DataFrame containing combined ticker and balance data.
        """
        try:
            # Extract balance data directly from PortfolioPosition objects in the balances dictionary
            avail_qty = {asset: details.available_to_trade_crypto for asset, details in balances.items()}
            total_qty = {asset: details.total_balance_crypto for asset, details in balances.items()}

            # Transform tickers_dict into a DataFrame
            if tickers_dict:
                df = pd.DataFrame(tickers_dict)

                # Flatten the 'info' column into separate columns
                info_df = pd.json_normalize(df['info'])
                df = pd.concat([df.drop(columns=['info']), info_df], axis=1)

                # Map balances to the DataFrame
                df['free'] = df['asset'].map(avail_qty).fillna(Decimal(0))  # Add free column
                df['total'] = df['asset'].map(total_qty).fillna(Decimal(0))  # Add total column

                # Add USD balance as a row if available
                usd_position = balances.get('USD')
                if usd_position:
                    usd_row = {
                        'symbol': 'USD/USD',
                        'asset': 'USD',
                        'free': Decimal(usd_position.available_to_trade_fiat).quantize(Decimal('0.01')),
                        'total': Decimal(usd_position.total_balance_fiat).quantize(Decimal('0.01')),
                        'volume_24h': 0
                    }
                    df = pd.concat([df, pd.DataFrame([usd_row])], ignore_index=True)

            else:
                df = pd.DataFrame()

            return df
        except Exception as e:
            self.logger.error(f"❌ Error in prepare_dataframe: {e}", exc_info=True)
            return pd.DataFrame()

    async def _calculate_atr_for_products(self, product_ids: list, spot_positions: dict, period: int = 14) -> tuple:
        """
        Calculate ATR (Average True Range) for products with active positions.

        Args:
            product_ids: List of all product IDs
            spot_positions: Dictionary of active positions {symbol: position_data}
            period: ATR period (default: 14 candles)

        Returns:
            tuple: (atr_pct_cache, atr_price_cache) dictionaries
        """
        atr_pct_cache = {}
        atr_price_cache = {}

        # Only calculate ATR for products with active positions to minimize API calls
        active_products = [f"{symbol}-USD" for symbol in spot_positions.keys() if symbol not in ['USD', 'USDC']]

        if not active_products:
            self.logger.debug("[ATR] No active positions, skipping ATR calculation")
            return atr_pct_cache, atr_price_cache

        self.logger.info(f"[ATR] Calculating ATR for {len(active_products)} active positions: {active_products}")

        for product_id in active_products:
            try:
                self.logger.info(f"[ATR] Fetching 1h OHLCV for {product_id}")
                # Fetch 1-hour OHLCV data (need 200+ candles for 14-period ATR)
                ohlcv_response = await self.coinbase_api.fetch_ohlcv(
                    product_id,
                    params={'granularity': 'ONE_HOUR', 'limit': 200}
                )

                if not ohlcv_response or 'data' not in ohlcv_response:
                    self.logger.info(f"[ATR] No OHLCV data for {product_id}")
                    continue

                df = ohlcv_response['data']
                if len(df) < period + 1:
                    self.logger.info(f"[ATR] Insufficient OHLCV data for {product_id}: {len(df)} candles")
                    continue

                # Calculate ATR using True Range
                trs = []
                prev_close = Decimal(str(df.iloc[0]['close']))

                for idx in range(1, len(df)):
                    row = df.iloc[idx]
                    high = Decimal(str(row['high']))
                    low = Decimal(str(row['low']))
                    close = Decimal(str(row['close']))

                    # True Range = max(high - low, |high - prev_close|, |prev_close - low|)
                    tr = max(high - low, abs(high - prev_close), abs(prev_close - low))
                    trs.append(tr)
                    prev_close = close

                    # Keep only last 'period' True Ranges
                    if len(trs) > period:
                        trs.pop(0)

                # Calculate ATR and convert to percentage
                if trs and prev_close > 0:
                    atr_price = sum(trs) / Decimal(len(trs))
                    atr_pct = atr_price / prev_close

                    # Store in caches (as floats for JSON serialization)
                    atr_price_cache[product_id] = float(atr_price)
                    atr_pct_cache[product_id] = float(atr_pct)

                    self.logger.debug(f"[ATR] {product_id}: ATR={float(atr_price):.4f}, ATR%={float(atr_pct)*100:.2f}%")

            except Exception as e:
                self.logger.info(f"[ATR] Calculation failed for {product_id}: {e}")
                continue

        self.logger.info(f"[ATR] Cached ATR for {len(atr_pct_cache)}/{len(active_products)} products")
        return atr_pct_cache, atr_price_cache

    async def parallel_fetch_and_update(self, usd_pairs, df, update_type: str = "current_price"):
        """
        PART I: Data Gathering and Database Loading
        PART VI: Profitability Analysis and Order Generation

        - Fetch fast L1 prices via best_bid_ask (bulk).
        - Normalize/quantize using analyze_spread.
        - Maintain per-product mid history.
        - Optionally enrich a small active set with L1 sizes via product_book(limit=1).
        - Update 'df' (either current_price or bid/ask), and return (df, bid_ask_spread).
        """
        bid_ask_spread: dict[str, dict] = {}
        formatted_tickers: dict[str, dict] = {}
        active_products = set()

        try:
            # --- 0) Fetch all USD product ids and their fast L1 prices --------------
            product_ids_raw = await self.coinbase_api.get_all_usd_pairs()
            product_ids = await self.coinbase_api._filter_valid_product_ids(product_ids_raw)
            tickers = await self.coinbase_api.get_best_bid_ask(product_ids)

            if not tickers:
                self.logger.error("Failed to fetch bids and asks.")
                return df, bid_ask_spread

            # Per Coinbase "best_bid_ask", expect: {"pricebooks": [{ "product_id": "...", "bid_price": "x", "ask_price": "y", ...}, ...]}
            pricebooks = tickers.get("pricebooks", []) or []
            formatted_tickers: dict[str, dict] = {}

            for entry in pricebooks:
                product_id = entry.get("product_id")
                if not product_id:
                    continue

                bids = entry.get("bids") or []
                asks = entry.get("asks") or []
                if not bids or not asks:
                    # empty or malformed book
                    self.logger.debug(f"Skipping {product_id}: empty orderbook (bids={len(bids)}, asks={len(asks)})")
                    continue

                # L1 prices/sizes from product_book
                try:
                    bid_p = bids[0].get("price")
                    ask_p = asks[0].get("price")
                    bid_s = bids[0].get("size")
                    ask_s = asks[0].get("size")

                    bid = float(bid_p) if bid_p is not None else None
                    ask = float(ask_p) if ask_p is not None else None
                    bid1_sz_dec = Decimal(str(bid_s)) if bid_s is not None else None
                    ask1_sz_dec = Decimal(str(ask_s)) if ask_s is not None else None
                except (TypeError, ValueError):
                    self.logger.warning(f"Malformed product_book for {product_id}: {entry}")
                    continue

                if bid is None or ask is None:
                    continue

                # Precision lookup
                try:
                    asset = product_id.split("-")[0]
                    base_deci, quote_deci, _, _ = self.shared_utils_precision.fetch_precision(
                        asset, usd_pairs_override=usd_pairs
                    )
                except Exception as e:
                    self.logger.warning(f"Precision lookup failed for {product_id}: {e}")
                    continue

                # Normalize/quantize prices + compute spread
                try:
                    ba = {"bid": bid, "ask": ask}
                    bid_adj, ask_adj, spread = self.order_book_manager.analyze_spread(quote_deci, ba)
                    if bid_adj is None or ask_adj is None or spread is None:
                        continue

                    # Internal map (Decimals) for strategy/risk
                    formatted_tickers[product_id] = {
                        "bid": bid_adj,
                        "ask": ask_adj,
                        "spread": spread,
                        "bid_size_1": bid1_sz_dec,
                        "ask_size_1": ask1_sz_dec,
                    }

                    # External/reporting map (floats)
                    bid_ask_spread[product_id] = {
                        "bid": float(bid_adj),
                        "ask": float(ask_adj),
                        "spread": float(spread),
                        "bid_size_1": float(bid1_sz_dec) if bid1_sz_dec is not None else None,
                        "ask_size_1": float(ask1_sz_dec) if ask1_sz_dec is not None else None,
                    }

                    # Mid history (cheap)
                    try:
                        mid_val = (Decimal(str(bid_adj)) + Decimal(str(ask_adj))) / Decimal("2")
                        self.mid_history[product_id].append(mid_val)
                    except Exception:
                        pass

                except Exception as e:
                    self.logger.warning(f"Could not analyze spread for {product_id}: {e}", exc_info=True)
                    continue

            # --- 1) Optional L1 size enrichment (small active set only) -------------
            # Decide which products are "active" for this cycle (e.g., shortlist from your strategy)

            active_products = self._select_active_products_by_liquidity(df, formatted_tickers, limit=self.enrich_limit)
            to_enrich = list(active_products)  # already limited

            if to_enrich:
                books = await self.coinbase_api.get_product_books(to_enrich, limit=1, max_concurrency=8)
                for pid, book in books.items():
                    if not book:
                        continue
                    bid1 = book["bids"][0]["size"] if book.get("bids") else None
                    ask1 = book["asks"][0]["size"] if book.get("asks") else None

                    # Store Decimals in the internal map used for risk/filters
                    formatted_tickers[pid]["bid_size_1"] = bid1
                    formatted_tickers[pid]["ask_size_1"] = ask1

                    # Store floats in the external reporting map
                    if pid in bid_ask_spread:
                        bid_ask_spread[pid]["bid_size_1"] = float(bid1) if bid1 is not None else None
                        bid_ask_spread[pid]["ask_size_1"] = float(ask1) if ask1 is not None else None

            # --- 2) Update df for symbols we care about -----------------------------
            if not callable(getattr(self.logger, "info", None)):
                self.logger.error("log_manager.info is not callable, check for possible overwriting.")

            # Expect usd_pairs to be a DataFrame with a 'symbol' column of product_ids, e.g., 'BTC-USD'
            for symbol in usd_pairs["symbol"].tolist():
                try:
                    ticker = formatted_tickers.get(symbol)
                    if not ticker:
                        if symbol not in ["USD/USD", "USD"]:
                            self.logger.info(f"No ticker data for symbol: {symbol}")
                        continue

                    bid = ticker.get("bid")
                    ask = ticker.get("ask")
                    if bid is None or ask is None:
                        continue

                    if symbol in df["symbol"].values:
                        if update_type == "bid_ask":
                            df.loc[df["symbol"] == symbol, ["bid", "ask"]] = [bid, ask]
                        elif update_type == "current_price":
                            df.loc[df["symbol"] == symbol, "current_price"] = float(ask)

                    # 'bid_ask_spread' already populated above

                except Exception as e:
                    self.logger.error(f"Error processing symbol {symbol}: {e}", exc_info=True)
                    continue

            return df, bid_ask_spread

        except Exception as e:
            self.logger.error(f"Error in parallel_fetch_and_update: {e}", exc_info=True)
            return df, bid_ask_spread

    async def get_portfolio_breakdown(self, portfolio_uuid: str, currency: str = "USD") -> object:
        """
        Fetch the portfolio breakdown using the REST client.

        Args:
            portfolio_uuid (str): The portfolio UUID.
            currency (str): Currency symbol for monetary values (default: "USD").

        Returns:
            object: Portfolio breakdown response object.
        """
        max_retries = 3
        retry_delay = 2  # Seconds
        wait = 0

        for attempt in range(1, max_retries + 1):
            try:
                self.logger.debug(f"🔁 Attempt {attempt} to fetch portfolio breakdown.")
                wait = min(2 ** attempt, 60) + random.uniform(0, 1)
                return self.rest_client.get_portfolio_breakdown(portfolio_uuid, currency)

            except HTTPError as e:
                if "429" in str(e):
                    self.logger.warning(f"⏳ Rate limit hit. Sleeping for {wait:.2f}s...")
                    await asyncio.sleep(wait)
                elif "401" in str(e):
                    self.logger.error("❌ ❌ Unauthorized access. Check your API keys and internet connection.❌ ❌")
                    break
                else:
                    self.logger.error(f"❌ HTTP error during get_portfolio_breakdown: {e}")
                    break
            except RemoteDisconnected as rd:
                self.logger.info(f"Network error on attempt {attempt}: {rd}, retrying in {wait}s")
                await asyncio.sleep(wait)
            except Exception as e:
                self.logger.error(f"❌ Unexpected error: {e}", exc_info=True)
                break
        self.logger.error("❌ All retry attempts failed for get_portfolio_breakdown.")
        return None


