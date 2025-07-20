
import asyncio
import time
from typing import Dict, Any, List, Tuple
from decimal import Decimal
import pandas as pd
from sqlalchemy import select

from Config.config_manager import CentralConfig
from sighook.signal_manager import SignalManager
from sighook.indicators import Indicators
from TableModels.ohlcv_data import OHLCVData


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
        self.signal_manager = SignalManager(logger_manager,
                                            shared_utils_precision,
                                            trade_recorder)

        # ✅ Cached config thresholds
        self._max_ohlcv_rows = self.config.max_ohlcv_rows or 720
        self._hodl = self.config._hodl
        self.start_time = None

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

            for symbol in valid_symbols:
                asset = symbol.replace("/", "-").split("-")[0]

                ohlcv_df = ohlcv_data_dict[symbol]
                if ohlcv_df is None or ohlcv_df.empty:
                    self.logger.warning(f"⚠️ Skipping {symbol} - invalid OHLCV data")
                    continue

                # ✅ Calculate Indicators
                _, quote_deci, _, _ = self.shared_utils_precision.fetch_precision(symbol)
                ohlcv_df = self.indicators.calculate_indicators(ohlcv_df, quote_deci)
                if ohlcv_df is None or ohlcv_df.empty:
                    continue

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
        Standardized strategy order format.
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
            'volume': None,
            'sell_cond': None,
            'value': None
        }

    # =========================================================
    # ✅ OHLCV Handling
    # =========================================================
    async def fetch_valid_ohlcv_batches(self, filtered_ticker_cache: pd.DataFrame) -> Dict[str, pd.DataFrame]:
        """
        Fetch OHLCV for all symbols in filtered ticker cache.
        """
        tasks = {
            row['symbol']: self.fetch_ohlcv_data_from_db(row['symbol'])
            for _, row in filtered_ticker_cache.iterrows()
        }
        raw_data = await asyncio.gather(*tasks.values(), return_exceptions=False)
        return {symbol: df for symbol, df in zip(tasks.keys(), raw_data) if isinstance(df, pd.DataFrame) and not df.empty}

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

        except Exception as e:
            self.logger.error(f"❌ Error fetching OHLCV for {symbol}: {e}", exc_info=True)
            return pd.DataFrame()


