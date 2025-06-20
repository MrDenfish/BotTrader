
import asyncio
from decimal import Decimal
from typing import Tuple, Dict

import pandas as pd
from sqlalchemy import select

import TableModels.ohlcv_data
from Config.config_manager import CentralConfig as config
from sighook.indicators import Indicators
from sighook.signal_manager import SignalManager


class TradingStrategy:
    """focus on decision-making based on data provided by MarketManager"""
    _instance = None

    @classmethod
    def get_instance(
            cls, webhook, ticker_manager, exchange, alerts, logger_manager, ccxt_api, metrics,
            max_concurrent_tasks, database_session_mngr, sharded_utils_print, db_tables, shared_utils_precision,
            shared_data_manager
    ):
        if cls._instance is None:
            cls._instance = cls(
                webhook, ticker_manager, exchange, alerts, logger_manager, ccxt_api, metrics,
                max_concurrent_tasks, database_session_mngr, sharded_utils_print, db_tables, shared_utils_precision,
                shared_data_manager)
        return cls._instance

    def __init__(
            self, webhook, ticker_manager, exchange, alerts, logger_manager, ccxt_api, metrics,
            max_concurrent_tasks, database_session_mngr, sharded_utils_print, db_tables, shared_utils_precision,
            shared_data_manager):

        self.config = config()
        self._version = self.config.program_version
        self.exchange = exchange
        self.alerts = alerts
        self.ccxt_exceptions = ccxt_api
        self.logger = logger_manager  # 🙂

        self.shared_data_manager = shared_data_manager
        self.shared_utils_precision = shared_utils_precision
        self.ticker_manager = ticker_manager
        self.indicators = Indicators(logger_manager)
        self._buy_rsi = self.config._rsi_buy
        self._sell_rsi = self.config._rsi_sell
        self._buy_ratio = self.config._buy_ratio
        self._sell_ratio = self.config._sell_ratio
        self._roc_buy_24h = self.config._roc_buy_24h
        self._hodl = self.config._hodl
        self.shill_coins = self.config._shill_coins
        self.market_metrics = metrics
        self.webhook = webhook
        self.ohlcv_data = {}  # A dictionary to store OHLCV data for each symbol
        self._max_ohlcv_rows = self.config.max_ohlcv_rows
        self.results = None
        self.start_time = None
        self.db_manager = database_session_mngr
        self.db_tables = db_tables
        self.shared_utils_precision = shared_utils_precision
        self.semaphore = asyncio.Semaphore(max_concurrent_tasks)
        self.sharded_utils_print = sharded_utils_print
        self.signal_manager = SignalManager( logger=self.logger, shared_utils_precision=shared_utils_precision)
        self.dynamic_indicators = {
            'Buy Touch', 'Sell Touch', 'W-Bottom', 'M-Top',
            'Buy Swing', 'Sell Swing', 'Buy MACD', 'Sell MACD'
        }

    @property
    def market_data(self):
        return self.shared_data_manager.market_data

    @property
    def order_management(self):
        return self.shared_data_manager.order_management

    @property
    def ticker_cache(self):
        return self.shared_data_manager.market_data.get('ticker_cache')

    @property
    def holdings_list(self):
        return self.shared_data_manager.market_data.get('spot_positions')

    @property
    def market_cache_vol(self):
        return self.shared_data_manager.market_data.get('filtered_vol')

    @property
    def usd_pairs(self):
        return self.shared_data_manager.market_data.get('usd_pairs_cache')

    @property
    def hodl(self):
        return self._hodl

    @property
    def roc_buy_24h(self):
        return int(self._roc_buy_24h)

    @property
    def max_ohlcv_rows(self):
        return self._max_ohlcv_rows

    @property
    def buy_rsi(self):
        return self._buy_rsi

    @property
    def sell_rsi(self):
        return self._sell_rsi

    @property
    def buy_ratio(self):
        return self._buy_ratio

    @property
    def sell_ratio(self):
        return self._sell_ratio



    # process all rows helper methods
    async def fetch_valid_ohlcv_batches(self, filtered_ticker_cache: pd.DataFrame) -> Dict[str, pd.DataFrame]:
        """Fetch and validate OHLCV data for all symbols."""
        tasks = {
            row['symbol']: self.fetch_ohlcv_data_from_db(row['symbol'])
            for _, row in filtered_ticker_cache.iterrows()
        }
        raw_data = await asyncio.gather(*tasks.values(), return_exceptions=False)
        return {
            symbol: df for symbol, df in zip(tasks.keys(), raw_data)
            if isinstance(df, pd.DataFrame) and not df.empty
        }

    def update_indicator_matrix(self, asset: str, ohlcv_df: pd.DataFrame, buy_sell_matrix: pd.DataFrame):
        """Update buy_sell_matrix for a given asset using the latest OHLCV data."""

        last_row = ohlcv_df.iloc[-1]

        for col in buy_sell_matrix.columns:
            if col in ohlcv_df.columns:
                raw_tuple = last_row[col] if isinstance(last_row[col], tuple) else (0, 0.0, None)

                # Validate and normalize the tuple
                if not isinstance(raw_tuple, tuple) or len(raw_tuple) < 2:
                    raw_tuple = (0, 0.0, None)

                decision = int(raw_tuple[0]) if raw_tuple[0] in {0, 1} else 0
                value = float(raw_tuple[1]) if raw_tuple[1] is not None else 0.0
                threshold = float(raw_tuple[2]) if len(raw_tuple) > 2 and raw_tuple[2] is not None else None

                # Use 0.0 for static indicators if threshold is missing
                if threshold is None and col not in self.dynamic_indicators:
                    threshold = 0.0

                buy_sell_matrix.at[asset, col] = (decision, value, threshold)

    def build_strategy_order(self, symbol, asset, type, price, trigger, action='buy', score=None):
        """
        Build a standardized strategy order dictionary aligned with webhook payload format.
        """
        base_deci, quote_deci ,_ , _ = self.shared_utils_precision.fetch_precision(symbol)
        return {
            'asset': asset,
            'symbol': symbol,
            'action': action,
            'type': type,
            'price': self.shared_utils_precision.safe_convert(price, quote_deci),
            'trigger': trigger,
            'score': score,
            'volume': None,
            'sell_cond': None,
            'value': None
        }

    def decide_action(self, ohlcv_df, symbol):
        try:
            #buy_sell_data = self.buy_sell_scoring(ohlcv_df, symbol)
            signal_data = self.signal_manager.buy_sell_scoring(ohlcv_df, symbol)
            asset = symbol.split('/')[0]
            price = ohlcv_df['close'].iloc[-1]
            trigger = signal_data.get('trigger')
            action = signal_data.get('action')
            type = signal_data.get('type')
            score = signal_data.get('Score')

            return self.build_strategy_order(
                symbol=symbol,
                asset=asset,
                type=type,
                price=price,
                trigger=trigger,
                action=action,
                score=score
            )

        except Exception as e:
            self.logger.error(f"❌ Error in decide_action for {symbol}: {e}", exc_info=True)
            return {}

    async def process_all_rows(self, filtered_ticker_cache, buy_sell_matrix, open_orders):
        """Process all rows, updating buy_sell_matrix with the latest indicator values."""
        skipped_symbols = []
        strategy_results = []

        # Define dynamic indicators (those that set their own thresholds)
        dynamic_threshold_indicators = {
            'Buy Touch', 'Sell Touch', 'W-Bottom', 'M-Top',
            'Buy Swing', 'Sell Swing', 'Buy MACD', 'Sell MACD'
        }

        try:
            if "asset" in buy_sell_matrix.columns:
                buy_sell_matrix.set_index("asset", inplace=True)

            # Fetch OHLCV data
            ohlcv_data_dict = await self.fetch_valid_ohlcv_batches(filtered_ticker_cache)
            valid_symbols = list(ohlcv_data_dict.keys())

            for symbol in valid_symbols:
                if "/" in symbol:
                    asset = symbol.replace("/", "-") # normalize symbol
                asset = symbol.split('-')[0]

                _, quote_deci, _, _ = self.shared_utils_precision.fetch_precision(symbol)

                ohlcv_df = ohlcv_data_dict[symbol]
                ohlcv_df = self.indicators.calculate_indicators(ohlcv_df, quote_deci)

                if ohlcv_df is None or ohlcv_df.empty:
                    self.logger.error(f"⚠️ Invalid OHLCV data for {symbol}")
                    continue

                buy_sell_score_data = self.decide_action(ohlcv_df, symbol)

                strategy_results.append({'asset': asset, 'symbol': symbol, **buy_sell_score_data})

                if asset not in buy_sell_matrix.index:
                    self.logger.warning(f"⚠️ Asset {asset} not in matrix. Skipping.")
                    continue

                # ✅ Update indicators in buy_sell_matrix
                self.update_indicator_matrix(asset, ohlcv_df, buy_sell_matrix)

                # Compute target levels and signals
                buy_signal, sell_signal = self.signal_manager.evaluate_signals(asset, buy_sell_matrix)
                buy_sell_matrix.at[asset, 'Buy Signal'] = buy_signal
                buy_sell_matrix.at[asset, 'Sell Signal'] = sell_signal

            return strategy_results, buy_sell_matrix

        except Exception as e:
            self.logger.error(f"❌ Error in process_all_rows: {e}", exc_info=True)
            return strategy_results, buy_sell_matrix


    async def fetch_ohlcv_data_from_db(self, asset):
        """ PART IV:
        Fetch OHLCV data from the database for a given asset.
        Verify if this is sufficient for indicator calculations
        (e.g., MACD or Bollinger Bands require lookback periods)."""

        query = (
            select(TableModels.ohlcv_data.OHLCVData)
            .where(TableModels.ohlcv_data.OHLCVData.symbol == asset)
            .order_by(TableModels.ohlcv_data.OHLCVData.time.desc())
            .limit(self.max_ohlcv_rows)
        )

        try:
            ohlcv_data = await self.db_manager.database.fetch_all(query)

            if ohlcv_data:
                # Convert the result to a pandas DataFrame
                ohlcv_df = pd.DataFrame([{
                    'time': data['time'],
                    'open': data['open'],
                    'high': data['high'],
                    'low': data['low'],
                    'close': data['close'],
                    'volume': data['volume']
                } for data in ohlcv_data])

                # ✅ Sort in ascending order so indicators work correctly
                ohlcv_df = ohlcv_df.sort_values(by='time', ascending=True).reset_index(drop=True)

                if ohlcv_df.isnull().values.any():
                    self.logger.error(f"NaN values detected in OHLCV data for {asset}")
                    return None
                if len(ohlcv_df) < 720:
                    self.logger.warning(f"Insufficient OHLCV data for {asset}. Rows fetched: {len(ohlcv_df)}")

                return ohlcv_df
            return None
        except Exception as e:
            self.logger.error(f"❌ Error fetching OHLCV data for {asset}: {e}", exc_info=True)
            return None

    @staticmethod
    def is_valid_bollinger_df(bollinger_df):
        """
        Validate the Bollinger DataFrame to ensure it is non-empty, non-null,
        and has valid data in the required columns.
        """
        try:
            # Check if DataFrame is None or empty
            if bollinger_df is None or bollinger_df.empty:
                return False

            # Check if the required columns exist
            required_columns = ['basis', 'upper', 'lower', 'band_ratio']
            # Check if DataFrame exists, has sufficient rows, and required columns
            if bollinger_df is None or len(bollinger_df) < 20 or not all(
                    col in bollinger_df.columns for col in required_columns):
                return False

            # Check the last row for NaN values in required columns
            if bollinger_df.iloc[-1][required_columns].isna().any():
                return False

            return True
        except Exception as e:
            return False


    def sell_signal_from_indicators(self, symbol, price, trigger, type, holdings):
        """PART V: Order Execution
        calling method:  order_manager.handle_sell_action()"""
        try:
            # Convert DataFrame to list of dictionaries if holdings is a DataFrame ( it will be when it comes from
            # "process_sell_order" function)
            if isinstance(holdings, pd.DataFrame):
                holdings = holdings.to_dict('records')
            coin = symbol.split('/')[0]
            sell_order = 'limit' # for testing default is limit
            if type == 'market_sell':
                sell_order = 'market'
            if any(item['asset'] == coin for item in holdings):
                sell_action = 'close_at_limit'
                sell_pair = symbol
                sell_limit = price
                self.logger.sell(f'Sell signal created for {symbol}, order triggered by {trigger} @ '
                                                     f'{price}.')
                return sell_action, sell_pair, sell_limit, sell_order

            return None, None, None, None
        except Exception as e:
            self.logger.error(f'❌ Error in handle_action(): {e}\nTraceback:,', exc_info=True)
            return None, None, None, None

