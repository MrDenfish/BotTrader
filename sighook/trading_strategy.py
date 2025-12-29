
import asyncio
import uuid
from typing import Dict, Any, List, Tuple
import pandas as pd
from sqlalchemy import select

from Config.config_manager import CentralConfig
from sighook.signal_manager import SignalManager
from sighook.indicators import Indicators
from TableModels.ohlcv_data import OHLCVData
from Shared_Utils.dynamic_symbol_filter import DynamicSymbolFilter


class TradingStrategy:
    """
    Core decision-making class for trading:
    - Fetches OHLCV data
    - Calculates indicators (via Indicators)
    - Updates buy/sell matrix
    - Decides on Buy/Sell/Hold + TP/SL
    """

    _instance = None

    @classmethod
    def get_instance(cls, logger_manager, shared_utils_precision,shared_data_manager, trade_recorder):
        if cls._instance is None:
            cls._instance = cls(logger_manager, shared_utils_precision,shared_data_manager,
                                trade_recorder)
        return cls._instance

    def __init__(self, logger_manager, shared_utils_precision,
                 shared_data_manager, trade_recorder):
        self.config = CentralConfig()
        self.logger = logger_manager
        self.shared_utils_precision = shared_utils_precision
        self.shared_data_manager = shared_data_manager

        # ✅ Indicators & Signal Manager (with TP/SL support)
        self.indicators = Indicators(logger_manager)
        self.signal_manager = SignalManager(logger_manager,shared_data_manager,
                                            shared_utils_precision,
                                            trade_recorder)

        # ✅ Cached config thresholds
        self._max_ohlcv_rows = self.config.max_ohlcv_rows or 720
        self._hodl = self.config._hodl
        self.start_time = None

        # ✅ Strategy snapshot ID for trade-strategy linkage
        # Generated once per bot run to link all trades in this session
        self.current_snapshot_id = uuid.uuid4()

        # ✅ Dynamic Symbol Filter (replaces hardcoded exclusion list)
        # Automatically excludes poor performers based on rolling metrics
        # Fallback to static list if dynamic filtering disabled
        self.dynamic_filter = DynamicSymbolFilter(
            shared_data_manager=shared_data_manager,
            config=self.config,
            logger_manager=logger_manager
        )

        # Static fallback list (used only if dynamic filtering fails or is disabled)
        self._fallback_excluded_symbols = [
            'A8-USD', 'PENGU-USD',  # Original blacklist (Dec 2024)
            # Top losers from 30-day analysis (Dec 10, 2025):
            'ELA-USD', 'ALCX-USD', 'UNI-USD', 'CLANKER-USD', 'ZORA-USD',
            'DASH-USD', 'BCH-USD', 'AVAX-USD', 'SWFTC-USD', 'AVNT-USD',
            'PRIME-USD', 'ICP-USD', 'KAITO-USD', 'IRYS-USD', 'TIME-USD',
            'NMR-USD', 'NEON-USD', 'QNT-USD', 'PERP-USD', 'BOBBOB-USD',
            'OMNI-USD', 'TIA-USD', 'IP-USD', 'TNSR-USD',  # Added Dec 15, 2025
            # Additional losers from 28-day analysis (Dec 28, 2025):
            'FARM-USD',  # 0% win rate, -$8.40 loss
            'XLM-USD',   # 0% win rate, -$5.00 loss
            'AVT-USD',   # 32% win rate, -$10.16 loss (highest total loss)
        ]

    # ---------------------------
    # ✅ Properties
    # ---------------------------
    @property
    def hodl(self):
        return self._hodl

    @property
    def max_ohlcv_rows(self):
        return self._max_ohlcv_rows

    @property
    def market_data(self):
        return self.shared_data_manager.market_data

    @property
    def usd_pairs(self):
        return self.shared_data_manager.market_data.get('usd_pairs_cache')

    # =========================================================
    # ✅ Core Workflow
    # =========================================================
    async def process_all_rows(self, filtered_ticker_cache: pd.DataFrame,
                               buy_sell_matrix: pd.DataFrame,
                               open_orders: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], pd.DataFrame]:
        """
        Fetch OHLCV, calculate indicators, update buy/sell matrix, and decide trading actions.
        """
        skipped_symbols = []
        strategy_results = []

        try:
            if "asset" in buy_sell_matrix.columns:
                buy_sell_matrix.set_index("asset", inplace=True)

            ohlcv_data_dict = await self.fetch_valid_ohlcv_batches(filtered_ticker_cache)
            valid_symbols = list(ohlcv_data_dict.keys())

            # Get dynamically excluded symbols (cached, refreshed hourly)
            try:
                excluded_symbols = await self.dynamic_filter.get_excluded_symbols()
            except Exception as e:
                self.logger.error(f"Dynamic filter failed, using fallback list: {e}", exc_info=True)
                excluded_symbols = set(self._fallback_excluded_symbols)

            for symbol in valid_symbols:
                asset = symbol.replace("/", "-").split("-")[0]

                # ✅ Skip blacklisted symbols (dynamic + permanent exclusions)
                if symbol in excluded_symbols:
                    self.logger.info(f"⛔ Skipping excluded symbol: {symbol}")
                    skipped_symbols.append(symbol)
                    continue

                ohlcv_df = ohlcv_data_dict[symbol]
                if ohlcv_df is None or ohlcv_df.empty:
                    self.logger.warning(f"⚠️ Skipping {symbol} - invalid OHLCV data")
                    continue

                # ✅ Calculate Indicators
                _, quote_deci, _, _ = self.shared_utils_precision.fetch_precision(symbol)
                ohlcv_df = self.indicators.calculate_indicators(ohlcv_df, quote_deci)
                if ohlcv_df is None or ohlcv_df.empty:
                    continue

                # ✅ Cache ATR for this symbol (enables ATR-based TP/SL for new buys)
                self._cache_atr_for_symbol(symbol.replace("/", "-"), ohlcv_df)

                # ✅ Decide Action (Buy/Sell/TP/SL)
                trade_decision = await self.decide_action(ohlcv_df, symbol)
                strategy_results.append({'asset': asset, 'symbol': symbol, **trade_decision})

                # ✅ Update Matrix
                if asset not in buy_sell_matrix.index:
                    self.logger.warning(f"⚠️ Asset {asset} not in matrix, skipping matrix update.")
                    continue

                self.signal_manager.update_indicator_matrix(asset, ohlcv_df, buy_sell_matrix)
                buy_signal, sell_signal = self.signal_manager.evaluate_signals(asset, buy_sell_matrix)
                buy_sell_matrix.at[asset, 'Buy Signal'] = buy_signal
                buy_sell_matrix.at[asset, 'Sell Signal'] = sell_signal
                if buy_signal[0] == 0 and "blocked" in buy_signal[3]:
                    self.logger.warning("Buy signal blocked", extra={'asset': asset, 'reason': buy_signal[3]})
            return strategy_results, buy_sell_matrix

        except Exception as e:
            self.logger.error(f"❌ Error in process_all_rows: {e}", exc_info=True)
            return strategy_results, buy_sell_matrix

    async def decide_action(self, ohlcv_df: pd.DataFrame, symbol: str) -> Dict[str, Any]:
        """
        Determines action (buy/sell/hold/tp_sl) for a given symbol.
        """
        try:
            price = float(ohlcv_df['close'].iloc[-1])
            asset = symbol.split('/')[0]

            # ✅ Primary Buy/Sell Scoring
            signal_data = self.signal_manager.buy_sell_scoring(ohlcv_df, symbol)
            action = signal_data.get('action', 'hold')
            trigger = signal_data.get('trigger', 'score')
            order_type = signal_data.get('type', 'limit')
            score = signal_data.get('Score', {})

            # ✅ TP/SL Evaluation (Overrides action if holding active position)
            tp_sl_trigger = await self.signal_manager.evaluate_tp_sl_conditions(symbol, price)
            if tp_sl_trigger in ['profit', 'loss']:
                action = 'sell'
                trigger = tp_sl_trigger
                order_type = 'tp_sl'

            return self.build_strategy_order(symbol, asset, order_type, price, trigger, action, score)

        except Exception as e:
            self.logger.error(f"❌ Error in decide_action for {symbol}: {e}", exc_info=True)
            return {}

    def build_strategy_order(self, symbol: str, asset: str, order_type: str,
                             price: float, trigger: str, action: str = 'buy',
                             score: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Standardized strategy order format with strategy linkage metadata.
        """
        base_deci, quote_deci, _, _ = self.shared_utils_precision.fetch_precision(symbol)
        return {
            'asset': asset,
            'symbol': symbol,
            'action': action,
            'type': order_type,
            'price': self.shared_utils_precision.safe_convert(price, quote_deci),
            'trigger': trigger,
            'score': score or {},
            'snapshot_id': str(self.current_snapshot_id),  # ✅ Strategy linkage
            'volume': None,
            'sell_cond': None,
            'value': None
        }

    # =========================================================
    # ✅ ATR Caching (for webhook TP/SL calculations)
    # =========================================================
    def _cache_atr_for_symbol(self, product_id: str, ohlcv_df: pd.DataFrame, period: int = 14):
        """
        Calculate proper True Range ATR and cache it for webhook TP/SL calculations.
        This ensures ATR is available for NEW buys (not just existing positions).

        Args:
            product_id: Trading pair (e.g., "BTC-USD")
            ohlcv_df: DataFrame with OHLCV data
            period: ATR lookback period (default: 14)
        """
        try:
            if ohlcv_df is None or len(ohlcv_df) < period + 1:
                return

            # Initialize caches if they don't exist
            if 'atr_pct_cache' not in self.shared_data_manager.market_data:
                self.shared_data_manager.market_data['atr_pct_cache'] = {}
            if 'atr_price_cache' not in self.shared_data_manager.market_data:
                self.shared_data_manager.market_data['atr_price_cache'] = {}

            # Calculate True Range ATR (same formula as webhook_order_manager)
            trs = []
            prev_close = float(ohlcv_df.iloc[0]['close'])

            for idx in range(1, len(ohlcv_df)):
                row = ohlcv_df.iloc[idx]
                high = float(row['high'])
                low = float(row['low'])
                close = float(row['close'])

                # True Range = max(high - low, |high - prev_close|, |prev_close - low|)
                tr = max(high - low, abs(high - prev_close), abs(prev_close - low))
                trs.append(tr)
                prev_close = close

                # Keep only last 'period' True Ranges
                if len(trs) > period:
                    trs.pop(0)

            # Calculate ATR and ATR percentage
            if trs and prev_close > 0:
                atr_price = sum(trs) / len(trs)
                atr_pct = atr_price / prev_close

                # Cache for webhook to use
                self.shared_data_manager.market_data['atr_pct_cache'][product_id] = atr_pct
                self.shared_data_manager.market_data['atr_price_cache'][product_id] = atr_price

                self.logger.debug(f"[ATR_CACHE] {product_id}: ATR={atr_price:.6f}, ATR%={atr_pct*100:.2f}%")

        except Exception as e:
            self.logger.debug(f"[ATR_CACHE] Failed for {product_id}: {e}")

    # =========================================================
    # ✅ OHLCV Handling
    # =========================================================
    async def fetch_valid_ohlcv_batches(self, filtered_ticker_cache: pd.DataFrame) -> Dict[str, pd.DataFrame]:
        """
        Fetch OHLCV for all symbols with controlled concurrency.
        """
        semaphore = asyncio.Semaphore(10)  # Limit to 10 concurrent queries (tune as needed)

        async def safe_fetch(symbol):
            async with semaphore:
                return await self.fetch_ohlcv_data_from_db(symbol)

        tasks = {
            row['symbol']: asyncio.create_task(safe_fetch(row['symbol']))
            for _, row in filtered_ticker_cache.iterrows()
        }

        raw_data = await asyncio.gather(*tasks.values(), return_exceptions=False)

        return {
            symbol: df
            for symbol, df in zip(tasks.keys(), raw_data)
            if isinstance(df, pd.DataFrame) and not df.empty
        }

    async def fetch_ohlcv_data_from_db(self, symbol: str) -> pd.DataFrame:
        """
        Fetch OHLCV data from DB for a given symbol.
        """
        try:
            async with self.shared_data_manager.database_session_manager.async_session() as session:
                result = await session.execute(
                    select(OHLCVData)
                    .where(OHLCVData.symbol == symbol)
                    .order_by(OHLCVData.time.desc())
                    .limit(self.max_ohlcv_rows)
                )
                rows = result.scalars().all()

            if not rows:
                return pd.DataFrame()

            ohlcv_df = pd.DataFrame(
                [{'time': r.time, 'open': r.open, 'high': r.high,
                  'low': r.low, 'close': r.close, 'volume': r.volume} for r in rows]
            ).sort_values(by='time', ascending=True).reset_index(drop=True)

            if ohlcv_df.isnull().values.any():
                self.logger.warning(f"⚠️ NaN detected in OHLCV for {symbol}")
                return pd.DataFrame()

            return ohlcv_df
        except asyncio.CancelledError:
            self.logger.warning("🛑 fetch_ohlcv_data_from_db was cancelled.", exc_info=True)
            raise

        except Exception as e:
            self.logger.error(f"❌ Error fetching OHLCV for {symbol}: {e}", exc_info=True)
            return pd.DataFrame()


