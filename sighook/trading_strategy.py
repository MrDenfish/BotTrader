
import asyncio
import pandas as pd
from sqlalchemy import select
from sighook.indicators import Indicators
from decimal import Decimal
from Config.config_manager import CentralConfig as config


class TradingStrategy:
    """focus on decision-making based on data provided by MarketManager"""
    _instance = None

    @classmethod
    def get_instance(cls, webhook, tickermanager, exchange, alerts, logmanager, ccxt_api, metrics,
                        max_concurrent_tasks, database_session_mngr, sharded_utils_print, db_tables, shared_utils_precision):
        if cls._instance is None:
            cls._instance = cls(webhook, tickermanager, exchange, alerts, logmanager, ccxt_api, metrics,
                                max_concurrent_tasks, database_session_mngr, sharded_utils_print, db_tables, shared_utils_precision)
        return cls._instance

    def __init__(self, webhook, tickermanager, exchange, alerts, logmanager, ccxt_api, metrics,
                 max_concurrent_tasks, database_session_mngr, sharded_utils_print, db_tables, shared_utils_precision):
        self.config = config()
        self._version = self.config.program_version
        self.exchange = exchange
        self.alerts = alerts
        self.ccxt_exceptions = ccxt_api
        self.log_manager = logmanager

        self.ticker_manager = tickermanager
        self.indicators = Indicators(logmanager)
        self._buy_rsi = self.config._rsi_buy
        self._sell_rsi = self.config._rsi_sell
        self._buy_ratio = self.config._buy_ratio
        self._sell_ratio = self.config._sell_ratio
        self._roc_24hr = self.config._roc_24hr
        self._hodl = self.config._hodl
        self.market_metrics = metrics
        self.webhook = webhook
        self.ohlcv_data = {}  # A dictionary to store OHLCV data for each symbol
        self._max_ohlcv_rows = self.config.max_ohlcv_rows
        self.results = None
        self.ticker_cache = None
        self.market_cache = None
        self.start_time = None
        self.db_manager = database_session_mngr
        self.db_tables = db_tables
        self.shared_utils_precision = shared_utils_precision
        self.semaphore = asyncio.Semaphore(max_concurrent_tasks)
        self.sharded_utils_print = sharded_utils_print
        # ✅ Ensure buy/sell targets are not None
        self._buy_target = self.config._buy_target if self.config._buy_target is not None else 7
        self._sell_target = self.config._sell_target if self.config._sell_target is not None else 7

    def set_trade_parameters(self, start_time, market_data):
        self.start_time = start_time
        self.ticker_cache = market_data['ticker_cache']
        self.market_cache_vol = market_data['filtered_vol']
        self.holdings_list = market_data['spot_positions']
        self.usd_pairs = market_data['usd_pairs_cache']

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
    def roc_24hr(self):
        return int(self._roc_24hr)

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

    async def process_all_rows(self, filtered_ticker_cache, buy_sell_matrix, open_orders):
        """Process all rows, updating buy_sell_matrix with the latest indicator values."""
        skipped_symbols = []
        strategy_results = []

        try:
            # ✅ Ensure 'asset' is the index
            if "asset" in buy_sell_matrix.columns:
                buy_sell_matrix.set_index("asset", inplace=True)

            # Fetch OHLCV data asynchronously
            tasks = {
                row['symbol']: self.fetch_ohlcv_data_from_db(row['symbol'])
                for _, row in filtered_ticker_cache.iterrows()
            }
            ohlcv_data = await asyncio.gather(*tasks.values(), return_exceptions=False)
            ohlcv_data_dict = {symbol: data for symbol, data in zip(tasks.keys(), ohlcv_data)}

            valid_symbols = [symbol for symbol, data in ohlcv_data_dict.items() if isinstance(data, pd.DataFrame)]

            for symbol in valid_symbols:
                asset = symbol.split('/')[0]
                temp_symbol = symbol.replace("/", "-")
                _, quote_deci, _, _ = self.shared_utils_precision.fetch_precision(symbol, self.usd_pairs)

                # ✅ Skip if there's an open order or asset is in the HODL list
                if self.symbol_has_open_order(temp_symbol, open_orders) or asset in self.hodl:
                    skipped_symbols.append(temp_symbol)
                    continue

                ohlcv_df = ohlcv_data_dict[symbol]
                ohlcv_df = self.indicators.calculate_indicators(ohlcv_df, quote_deci)  # ✅ Now includes Bollinger Bands

                # ✅ Ensure valid indicators before proceeding
                if ohlcv_df is None or ohlcv_df.empty:
                    self.log_manager.error(f"⚠️ Invalid OHLCV data for {symbol}")
                    continue

                action_data = self.decide_action(ohlcv_df, symbol)
                strategy_results.append(
                    {
                        'asset': asset,
                        'symbol': symbol,
                        **action_data
                    }
                )

                if asset not in buy_sell_matrix.index:
                    self.log_manager.warning(f"⚠️ Asset {asset} not found in buy_sell_matrix index. Skipping update.")
                    continue

                # ✅ Update buy_sell_matrix values
                for col in buy_sell_matrix.columns:
                    if col in ohlcv_df.columns:
                        computed_value = ohlcv_df[col].iloc[-1] if not ohlcv_df.empty and not ohlcv_df[col].isna().all() else 0
                        computed_value = computed_value[1] if isinstance(computed_value, tuple) and len(computed_value) > 1 else computed_value
                        computed_value = float(computed_value) if computed_value is not None else 0.0
                        existing_tuple = buy_sell_matrix.at[asset, col]

                        threshold = float(existing_tuple[2]) if (
                                isinstance(existing_tuple, tuple) and len(existing_tuple) == 3 and existing_tuple[2] is not None
                        ) else 0.0

                        decision = 1 if computed_value > threshold else 0
                        buy_sell_matrix.at[asset, col] = (decision, computed_value, threshold)

                # ✅ Update buy_target & sell_target using strategy weights
                self.update_dynamic_targets(asset, buy_sell_matrix)

                # ✅ Compute buy & sell signal scores
                buy_signal_value, sell_signal_value = self.compute_weighted_scores(asset, buy_sell_matrix)

                # ✅ Debugging: Check computed scores
                print(f"✅ Computed Buy Score for {asset}: {buy_signal_value}")
                print(f"✅ Computed Sell Score for {asset}: {sell_signal_value}")

                # ✅ Apply structured signal logic
                buy_signal, sell_signal = self.compute_signals(buy_signal_value, sell_signal_value)
                buy_sell_matrix.at[asset, 'Buy Signal'] = buy_signal
                buy_sell_matrix.at[asset, 'Sell Signal'] = sell_signal

            # ✅ Debugging logs
            # print(f'{strategy_results}')  # Debugging
            # print(f'{buy_sell_matrix.to_string(index=False)}')  # Debugging

            return strategy_results, buy_sell_matrix

        except Exception as e:
            self.log_manager.error(f"❌ Error in process_all_rows: {e}", exc_info=True)
            return strategy_results, buy_sell_matrix


    def update_dynamic_targets(self, asset, buy_sell_matrix):
        """Update dynamic buy & sell targets based on strategy weights."""
        try:
            strategy_weights = {
                'Buy Ratio': 1.2, 'Buy Touch': 1.5, 'W-Bottom': 2.0, 'Buy RSI': 2.5,
                'Buy ROC': 2.0, 'Buy MACD': 1.8, 'Buy Swing': 2.2, 'Sell Ratio': 1.2,
                'Sell Touch': 1.5, 'M-Top': 2.0, 'Sell RSI': 2.5, 'Sell ROC': 2.0,
                'Sell MACD': 1.8, 'Sell Swing': 2.2
            }

            def safe_float(val):
                """Ensure the threshold is a valid float or default to 0."""
                try:
                    return float(val) if val is not None else 0.0
                except (TypeError, ValueError):
                    return 0.0

            total_buy_weight = sum(strategy_weights[col] for col in strategy_weights if col.startswith("Buy"))
            # print(f'{total_buy_weight}') #debug
            total_sell_weight = sum(strategy_weights[col] for col in strategy_weights if col.startswith("Sell"))
            # print(f'{total_sell_weight}') #debug

            self.buy_target = total_buy_weight * 0.7  # Adjusted threshold
            self.sell_target = total_sell_weight * 0.7  # Adjusted threshold

            self.log_manager.debug(f"Dynamic Buy Target for {asset}: {self.buy_target}")
            self.log_manager.debug(f"Dynamic Sell Target for {asset}: {self.sell_target}")


        except Exception as e:
            self.log_manager.error(f"Error updating dynamic targets: {e}", exc_info=True)

    def compute_weighted_scores(self, asset, buy_sell_matrix):
        """Compute weighted buy and sell scores based on strategy decisions and weights."""
        try:
            strategy_weights = {
                'Buy Ratio': 1.2, 'Buy Touch': 1.5, 'W-Bottom': 2.0, 'Buy RSI': 2.5,
                'Buy ROC': 2.0, 'Buy MACD': 1.8, 'Buy Swing': 2.2, 'Sell Ratio': 1.2,
                'Sell Touch': 1.5, 'M-Top': 2.0, 'Sell RSI': 2.5, 'Sell ROC': 2.0,
                'Sell MACD': 1.8, 'Sell Swing': 2.2
            }

            # **✅ Print the buy/sell matrix values for debugging**
            # print(f"\n� DEBUG - Asset: {asset}")
            # for col, value in buy_sell_matrix.loc[asset].items():
            #     if col.startswith("Buy") or col.startswith("Sell"):
            #         print(f"{col}: {value}")  # Check raw values

            # ✅ Calculate buy score
            buy_score = sum(
                min(value[0] * strategy_weights[col], 10)  # Cap max contribution to 10 per indicator
                if isinstance(value, tuple) and len(value) == 3 else 0
                for col, value in buy_sell_matrix.loc[asset].items()
                if col.startswith("Buy") and col in strategy_weights
            )

            # ✅ Calculate sell score
            sell_score = sum(
                (value[0] * strategy_weights[col]) if isinstance(value, tuple) and len(value) == 3 else 0
                for col, value in buy_sell_matrix.loc[asset].items()
                if col.startswith("Sell") and col in strategy_weights
            )

            print(f"� {asset} Computed Buy Score: {buy_score}")
            print(f"� {asset} Computed Sell Score: {sell_score}")

            return buy_score, sell_score
        except Exception as e:
            self.log_manager.error(f"Error computing weighted scores: {e}", exc_info=True)
            return 0, 0

    def compute_signals(self, buy_score, sell_score):
        """Determine final buy/sell signals based on weighted scores."""
        try:
            # Debugging logs
            self.log_manager.debug(f"Buy Score: {buy_score}, Buy Target: {self.buy_target}")
            self.log_manager.debug(f"Sell Score: {sell_score}, Sell Target: {self.sell_target}")

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
            self.log_manager.error(f"Error computing final buy/sell signals: {e}", exc_info=True)
            return (0, 0.0, 0.0), (0, 0.0, 0.0)

    def update_buy_sell_matrix(self, asset, buy_sell_matrix, bollinger_df):
        """Update buy_sell_matrix using weighted strategy scores."""
        try:
            most_recent_row = bollinger_df.iloc[-1]  # Use the latest available row

            strategy_weights = {
                'Buy Ratio': 1.2, 'Buy Touch': 1.5, 'W-Bottom': 2.0, 'Buy RSI': 2.5,
                'Buy ROC': 2.0, 'Buy MACD': 1.8, 'Buy Swing': 2.2, 'Sell Ratio': 1.2,
                'Sell Touch': 1.5, 'M-Top': 2.0, 'Sell RSI': 2.5, 'Sell ROC': 2.0,
                'Sell MACD': 1.8, 'Sell Swing': 2.2
            }

            # Ensure asset exists in buy_sell_matrix
            if asset not in buy_sell_matrix.index:
                self.log_manager.warning(f"Asset {asset} not found in buy_sell_matrix. Skipping update.")
                return buy_sell_matrix

            # Compute weighted buy and sell scores
            buy_score = sum(
                value[0] * strategy_weights[col]  # Decision (0/1) * Weight
                for col, value in buy_sell_matrix.loc[asset].items()
                if col.startswith("Buy") and col in strategy_weights
            )

            sell_score = sum(
                value[0] * strategy_weights[col]
                for col, value in buy_sell_matrix.loc[asset].items()
                if col.startswith("Sell") and col in strategy_weights
            )
            self.buy_target =3.0 # debug
            # Dynamically calculate buy and sell targets

            # self.buy_target = sum(
            #     strategy_weights[col]
            #     for col, value in buy_sell_matrix.loc[asset].items()
            #     if col.startswith("Buy") and col in strategy_weights
            # )

            self.sell_target = sum(
                strategy_weights[col]
                for col, value in buy_sell_matrix.loc[asset].items()
                if col.startswith("Sell") and col in strategy_weights
            )

            # Compute Buy & Sell Signals
            buy_signal = (1, buy_score, self.buy_target) if buy_score >= self.buy_target else (0, buy_score, self.buy_target)
            sell_signal = (1, sell_score, self.sell_target) if sell_score >= self.sell_target else (
            0, sell_score, self.sell_target)

            # Update Buy & Sell Signals in the matrix
            buy_sell_matrix.at[asset, 'Buy Signal'] = buy_signal
            buy_sell_matrix.at[asset, 'Sell Signal'] = sell_signal

            return buy_sell_matrix

        except Exception as e:
            self.log_manager.error(f"Error updating buy_sell_matrix: {e}", exc_info=True)
            return buy_sell_matrix

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
         verify if this is sufficient for indicator calculations
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

                # Check for NaN values in the DataFrame
                if ohlcv_df.isnull().values.any():
                    self.log_manager.error(f"NaN values detected in OHLCV data for {asset}")
                    return None
                if len(ohlcv_df) < 720:
                    self.log_manager.warning(f"Insufficient OHLCV data for {asset}. Rows fetched: {len(ohlcv_df)}") # Log a warning if the DataFrame is too small
                return ohlcv_df  # Return the DataFrame
            return None
        except Exception as e:
            self.log_manager.error(f"Error fetching OHLCV data for {asset}: {e}", exc_info=True)
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

    def decide_action(self, ohlcv_df, symbol):
        """
        Determine buy or sell action based on computed indicators.
        """
        try:
            # ✅ Compute buy/sell signals directly from ohlcv_df
            buy_sell_data, trigger = self.buy_sell_scoring(ohlcv_df, symbol)

            # ✅ Retrieve latest price
            latest_price = Decimal(ohlcv_df['close'].iloc[-1])

            # ✅ Find volume_24h for the symbol in market data
            volume_24h = next(
                (market.get('info', {}).get('volume_24h') for market in self.market_cache_vol if market.get('symbol') == symbol),
                None
            )

            # ✅ Construct action dictionary
            return {
                'action': buy_sell_data.get('action', None),
                'price': latest_price,
                'band_ratio': buy_sell_data.get('band_ratio', None),
                'trigger': trigger,
                'sell_cond': buy_sell_data['Sell Signal'][1] if buy_sell_data.get('Sell Signal') else None,
                'buy_sell_data': buy_sell_data,
                'volume_24h': volume_24h
            }

        except Exception as e:
            self.log_manager.error(f"❌ Error in decide_action for {symbol}: {e}", exc_info=True)
            return {}

    def buy_sell_scoring(self, ohlcv_df, symbol):
        """Determine buy or sell signals using weighted scores and ROC-based priority."""
        try:
            _, quote_deci, _, _ = self.shared_utils_precision.fetch_precision(symbol, self.usd_pairs)

            last_ohlcv = ohlcv_df.iloc[-1]  # Get the most recent row

            # ✅ Prioritize ROC-Based Buy/Sell Conditions
            roc_value = last_ohlcv.get('ROC', None)
            roc_diff_value = last_ohlcv.get('ROC_Diff', 0.0)
            rsi_value = last_ohlcv.get('RSI', None)

            if roc_value is not None:
                buy_signal_roc = roc_value > 5 and abs(roc_diff_value) > 0.3 and rsi_value is not None and rsi_value < 30
                sell_signal_roc = roc_value < -2.5 and abs(roc_diff_value) > 0.3 and rsi_value is not None and rsi_value > 70

                if buy_signal_roc:
                    return {'action': 'buy', 'Buy Signal': (1, roc_value, 5),
                            'Sell Signal': (0, None, None)}, 'roc_buy'
                elif sell_signal_roc:
                    return {'action': 'sell', 'Sell Signal': (1, roc_value, -2.5),
                            'Buy Signal': (0, None, None)}, 'roc_sell'

            # ✅ Weighted Buy/Sell Scores
            buy_conditions = ['Buy RSI', 'Buy MACD', 'Buy Touch', 'Buy Ratio', 'W-Bottom', 'Buy Swing']
            sell_conditions = ['Sell RSI', 'Sell MACD', 'Sell Touch', 'Sell Ratio', 'M-Top', 'Sell Swing']

            buy_score = sum(last_ohlcv[col][1] for col in buy_conditions if col in last_ohlcv and last_ohlcv[col][1] is not None)
            sell_score = sum(last_ohlcv[col][1] for col in sell_conditions if col in last_ohlcv and last_ohlcv[col][1] is not None)

            buy_signal = (1, buy_score, self.buy_target) if buy_score >= self.buy_target else (0, buy_score, self.buy_target)
            sell_signal = (1, sell_score, self.sell_target) if sell_score >= self.sell_target else (0, sell_score, self.sell_target)

            action = 'buy' if buy_signal[0] == 1 else 'sell' if sell_signal[0] == 1 else 'hold'

            return {'action': action, 'Buy Signal': buy_signal, 'Sell Signal': sell_signal}, action

        except Exception as e:
            self.log_manager.error(f"❌ Error in buy_sell_scoring() for {symbol}: {e}", exc_info=True)
            return {'action': None, 'Buy Signal': (0, None, None), 'Sell Signal': (0, None, None)}, None


    def sell_signal_from_indicators(self, symbol, price, trigger, holdings):
        """PART V: Order Execution"""
        try:
            # Convert DataFrame to list of dictionaries if holdings is a DataFrame ( it will be when it comes from
            # "process_sell_order" function)
            if isinstance(holdings, pd.DataFrame):
                holdings = holdings.to_dict('records')
            coin = symbol.split('/')[0]
            sell_order = 'limit' # for testing default is limit
            if trigger == 'market_sell':
                sell_order = 'market'
            if any(item['asset'] == coin for item in holdings):
                sell_action = 'close_at_limit'
                sell_pair = symbol
                sell_limit = price
                self.log_manager.sell(f'Sell signal created for {symbol}, order triggered by {trigger} @ '
                                                     f'{price}.')
                return sell_action, sell_pair, sell_limit, sell_order

            return None, None, None, None
        except Exception as e:
            self.log_manager.error(f'Error in handle_action(): {e}\nTraceback:,', exc_info=True)
            return None, None, None, None

