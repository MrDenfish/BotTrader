
import asyncio
from decimal import Decimal
from typing import Tuple, Dict, Any

import pandas as pd
from sqlalchemy import select

from Config.config_manager import CentralConfig as config
from sighook.indicators import Indicators


class TradingStrategy:
    """focus on decision-making based on data provided by MarketManager"""
    _instance = None

    @classmethod
    def get_instance(
            cls, webhook, ticker_manager, exchange, alerts, logger_manager, ccxt_api, metrics,
            max_concurrent_tasks, database_session_mngr, sharded_utils_print, db_tables, shared_utils_precision, shared_data_manager
    ):
        if cls._instance is None:
            cls._instance = cls(
                webhook, ticker_manager, exchange, alerts, logger_manager, ccxt_api, metrics,
                max_concurrent_tasks, database_session_mngr, sharded_utils_print, db_tables, shared_utils_precision,
                shared_data_manager)
        return cls._instance

    def __init__(
            self, webhook, ticker_manager, exchange, alerts, logger_manager, ccxt_api, metrics,
            max_concurrent_tasks, database_session_mngr, sharded_utils_print, db_tables, shared_utils_precision, shared_data_manager):
        self.config = config()
        self._version = self.config.program_version
        self.exchange = exchange
        self.alerts = alerts
        self.ccxt_exceptions = ccxt_api
        self.logger = logger_manager  # 🙂

        self.shared_data_manager = shared_data_manager
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
        # ✅ Ensure buy/sell targets are not None
        self._buy_target = self.config._buy_target if self.config._buy_target is not None else 7
        self._sell_target = self.config._sell_target if self.config._sell_target is not None else 7
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
        return self.market_data.get('ticker_cache')

    @property
    def holdings_list(self):
        return self.market_data.get('spot_positions')

    @property
    def market_cache_vol(self):
        return self.market_data.get('filtered_vol')

    @property
    def usd_pairs(self):
        return self.market_data.get('usd_pairs_cache')


    @property
    def buy_target(self):
        return int(self._buy_target) if self._buy_target is not None else 7

    @buy_target.setter
    def buy_target(self, value):
        self._buy_target = value

    @property
    def sell_target(self):
        return int(self._sell_target) if self._sell_target is not None else 7

    @sell_target.setter
    def sell_target(self, value):
        self._sell_target = value

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

    STRATEGY_WEIGHTS = {
        'Buy Ratio': 1.2, 'Buy Touch': 1.5, 'W-Bottom': 2.0, 'Buy RSI': 2.5,
        'Buy ROC': 2.0, 'Buy MACD': 1.8, 'Buy Swing': 2.2, 'Sell Ratio': 1.2,
        'Sell Touch': 1.5, 'M-Top': 2.0, 'Sell RSI': 2.5, 'Sell ROC': 2.0,
        'Sell MACD': 1.8, 'Sell Swing': 2.2
    }

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

    def evaluate_signals(self, asset: str, matrix: pd.DataFrame) -> Tuple[Tuple[int, float, float], Tuple[int, float, float]]:
        """
        Update dynamic thresholds, compute weighted scores, and return buy/sell signals for a given asset.
        """
        try:
            self.update_dynamic_targets(asset, matrix)
            buy_score, sell_score = self.compute_weighted_scores(asset, matrix)
            buy_signal, sell_signal = self.compute_signals(buy_score, sell_score)
            return buy_signal, sell_signal
        except Exception as e:
            self.logger.error(f"❌ Error in evaluate_signals for {asset}: {e}", exc_info=True)
            return (0, 0.0, 0.0), (0, 0.0, 0.0)

    def build_strategy_order(self, symbol, asset, type, price, trigger, action='buy', score=None):
        """
        Build a standardized strategy order dictionary aligned with webhook payload format.
        """
        return {
            'asset': asset,
            'symbol': symbol,
            'action': action,
            'type': type,
            'price': Decimal(str(price)),
            'trigger': trigger,
            'score': score,
            'volume': None,
            'sell_cond': None,
            'value': None
        }

    def decide_action(self, ohlcv_df, symbol):
        try:
            buy_sell_data = self.buy_sell_scoring(ohlcv_df, symbol)
            asset = symbol.split('/')[0]
            price = ohlcv_df['close'].iloc[-1]
            trigger = buy_sell_data.get('trigger')
            action = buy_sell_data.get('action')
            type = buy_sell_data.get('type')
            score = buy_sell_data.get('Score')

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
                asset = symbol.split('/')[0]
                temp_symbol = symbol.replace("/", "-")
                _, quote_deci, _, _ = self.shared_utils_precision.fetch_precision(symbol)

                # if self.symbol_has_open_order(temp_symbol, open_orders) or asset in self.shill_coins:
                if asset in self.shill_coins:
                    skipped_symbols.append(temp_symbol)
                    continue

                ohlcv_df = ohlcv_data_dict[symbol]
                ohlcv_df = self.indicators.calculate_indicators(ohlcv_df, quote_deci)

                if ohlcv_df is None or ohlcv_df.empty:
                    self.log_manager.error(f"⚠️ Invalid OHLCV data for {symbol}")
                    continue

                buy_sell_score_data = self.decide_action(ohlcv_df, symbol)

                strategy_results.append({'asset': asset, 'symbol': symbol, **buy_sell_score_data})

                if asset not in buy_sell_matrix.index:
                    self.log_manager.warning(f"⚠️ Asset {asset} not in matrix. Skipping.")
                    continue

                # ✅ Update indicators in buy_sell_matrix
                self.update_indicator_matrix(asset, ohlcv_df, buy_sell_matrix)

                # Compute target levels and signals
                buy_signal, sell_signal = self.evaluate_signals(asset, buy_sell_matrix)
                buy_sell_matrix.at[asset, 'Buy Signal'] = buy_signal
                buy_sell_matrix.at[asset, 'Sell Signal'] = sell_signal

            return strategy_results, buy_sell_matrix

        except Exception as e:
            self.logger.error(f"❌ Error in process_all_rows: {e}", exc_info=True)
            return strategy_results, buy_sell_matrix

    def update_dynamic_targets(self, asset: str, buy_sell_matrix: pd.DataFrame):
        """Update dynamic buy & sell targets based on strategy weights."""
        try:
            weights = self.STRATEGY_WEIGHTS

            def safe_float(val):
                """Ensure the threshold is a valid float or default to 0."""
                try:
                    return float(val) if val is not None else 0.0
                except (TypeError, ValueError):
                    return 0.0

            total_buy_weight = sum(weights[col] for col in weights if col.startswith("Buy"))
            # print(f'{total_buy_weight}') #debug
            total_sell_weight = sum(weights[col] for col in weights if col.startswith("Sell"))
            # print(f'{total_sell_weight}') #debug

            self.buy_target = total_buy_weight * 0.7  # Adjusted threshold
            self.sell_target = total_sell_weight * 0.7  # Adjusted threshold

            self.logger.debug(f"Dynamic Buy Target for {asset}: {self.buy_target}")
            self.logger.debug(f"Dynamic Sell Target for {asset}: {self.sell_target}")


        except Exception as e:
            self.logger.error(f"❌ Error updating dynamic targets: {e}", exc_info=True)

    def compute_weighted_scores(self, asset, buy_sell_matrix):
        """Compute weighted buy and sell scores based on strategy decisions and weights."""
        try:
            weights = self.STRATEGY_WEIGHTS

            # **✅ Print the buy/sell matrix values for debugging**
            # print(f"\n� DEBUG - Asset: {asset}")
            # for col, value in buy_sell_matrix.loc[asset].items():
            #     if col.startswith("Buy") or col.startswith("Sell"):
            #         print(f"{col}: {value}")  # Check raw values

            # ✅ Calculate buy score
            buy_score = sum(
                min(value[0] * weights[col], 10)  # Cap max contribution to 10 per indicator
                if isinstance(value, tuple) and len(value) == 3 else 0
                for col, value in buy_sell_matrix.loc[asset].items()
                if col.startswith("Buy") and col in weights
            )

            # ✅ Calculate sell score
            sell_score = sum(
                (value[0] * weights[col]) if isinstance(value, tuple) and len(value) == 3 else 0
                for col, value in buy_sell_matrix.loc[asset].items()
                if col.startswith("Sell") and col in weights
            )

            return buy_score, sell_score
        except Exception as e:
            self.logger.error(f"❌ Error computing weighted scores: {e}", exc_info=True)
            return 0, 0

    def compute_signals(self, buy_score, sell_score):
        """Determine final buy/sell signals based on weighted scores."""
        try:
            # Debugging logs
            self.logger.debug(f"Buy Score: {buy_score}, Buy Target: {self.buy_target}")
            self.logger.debug(f"Sell Score: {sell_score}, Sell Target: {self.sell_target}")

            buy_signal = (1, buy_score, self.buy_target) if buy_score >= self.buy_target else (0, buy_score, self.buy_target)
            sell_signal = (1, sell_score, self.sell_target) if sell_score >= self.sell_target else (0, sell_score, self.sell_target)
            # print(f'buy_signal:{buy_signal}') #debug
            # print(f'sell_signal:{sell_signal}') #debug
            # Resolve conflicting signals (if both are active)
            if buy_signal[0] == 1 and sell_signal[0] == 1:
                if buy_score > sell_score:
                    sell_signal = (0, sell_score, self.sell_target)
                else:
                    buy_signal = (0, buy_score, self.buy_target)
                # print(f'buy_signal:{buy_signal}') #debug
                # print(f'sell_signal:{sell_signal}') #debug
            return buy_signal, sell_signal

        except Exception as e:
            self.logger.error(f"❌ Error computing final buy/sell signals: {e}", exc_info=True)
            return (0, 0.0, 0.0), (0, 0.0, 0.0)

    @staticmethod
    def symbol_has_open_order(symbol, open_orders):
        """ PART IV:
        Check if the symbol has an open order.
        """
        try:
            if open_orders.empty:
                return False
            else:
                return symbol.replace('/', '-') in open_orders['product_id'].values
        except Exception as e:
            return False

    async def fetch_ohlcv_data_from_db(self, asset):
        """ PART IV:
        Fetch OHLCV data from the database for a given asset.
        Verify if this is sufficient for indicator calculations
        (e.g., MACD or Bollinger Bands require lookback periods)."""

        query = (
            select(self.db_tables.OHLCVData)
            .where(self.db_tables.OHLCVData.symbol == asset)
            .order_by(self.db_tables.OHLCVData.time.desc())
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

    def buy_sell_scoring(self, ohlcv_df: pd.DataFrame, symbol: str) -> Dict[str, Any]:
        """
        Determine buy/sell action using ROC priority and weighted scores.
        """
        try:
            action = 'hold'
            last_ohlcv = ohlcv_df.iloc[-1]
            _, quote_deci, _, _ = self.shared_utils_precision.fetch_precision(symbol)



            # Extract critical indicators
            roc_value = last_ohlcv.get('ROC', None)
            roc_diff_value = last_ohlcv.get('ROC_Diff', 0.0)
            rsi_value = last_ohlcv.get('RSI', None)

            # ✅ ROC-based priority (overrides scoring logic)
            if roc_value is not None:
                buy_signal_roc = roc_value > 5 and abs(roc_diff_value) > 0.3 and rsi_value is not None and rsi_value < 30
                sell_signal_roc = roc_value < -2.5 and abs(roc_diff_value) > 0.3 and rsi_value is not None and rsi_value > 70

                if buy_signal_roc:
                    return {
                        'action': 'buy',
                        'trigger': 'roc_buy',
                        'type': 'limit',
                        'Buy Signal': (1, float(roc_value), 5),
                        'Sell Signal': (0, None, None),
                        'Score': {'Buy Score': None, 'Sell Score': None}
                    }

                if sell_signal_roc:
                    return {
                        'action': 'sell',
                        'trigger': 'roc_sell',
                        'type': 'limit',
                        'Sell Signal': (1, float(roc_value), -2.5),
                        'Buy Signal': (0, None, None),
                        'Score': {'Buy Score': None, 'Sell Score': None}
                    }


            # ✅ Score-based evaluation
            weights = self.STRATEGY_WEIGHTS
            buy_score = 0.0
            sell_score = 0.0

            for indicator, weight in weights.items():
                value = last_ohlcv.get(indicator)
                if isinstance(value, tuple) and len(value) == 3:
                    decision = value[0]
                    buy_score += decision * weight if indicator.startswith("Buy") else 0.0
                    sell_score += decision * weight if indicator.startswith("Sell") else 0.0

            # Update targets dynamically if needed
            self.buy_target = sum(w for k, w in weights.items() if k.startswith("Buy")) * 0.7
            self.sell_target = sum(w for k, w in weights.items() if k.startswith("Sell")) * 0.7

            buy_signal = (1, round(buy_score, 3), self.buy_target) if buy_score >= self.buy_target else (0, round(buy_score, 3), self.buy_target)
            sell_signal = (1, round(sell_score, 3), self.sell_target) if sell_score >= self.sell_target else (
                0, round(sell_score, 3), self.sell_target)

            # Resolve conflicts

            if buy_signal[0] == 1 and sell_signal[0] == 0:
                action = 'buy'
            elif sell_signal[0] == 1 and buy_signal[0] == 0:
                action = 'sell'
            elif buy_signal[0] == 1 and sell_signal[0] == 1:
                action = 'buy' if buy_score > sell_score else 'sell'

            return {
                'action': action,
                'trigger': 'score',
                'type': 'limit',
                'Buy Signal': buy_signal,
                'Sell Signal': sell_signal,
                'Score': {'Buy Score': buy_score, 'Sell Score': sell_score}
            }

        except Exception as e:
            self.logger.error(f"❌ Error in buy_sell_scoring() for {symbol}: {e}", exc_info=True)
            return {
                'action': None,
                'trigger': None,
                'Sell Signal': None,
                'Buy Signal': (0, None, None),
                'Score': {'Buy Score': None, 'Sell Score': None}
            }

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

