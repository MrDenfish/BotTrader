
import asyncio
import pandas as pd
from sqlalchemy import select
from indicators import Indicators
from decimal import Decimal
from Shared_Utils.config_manager import CentralConfig as config


class TradingStrategy:
    """focus on decision-making based on data provided by MarketManager"""
    _instance = None

    @classmethod
    def get_instance(cls, webhook, tickermanager, exchange, alerts, logmanager, ccxt_api, metrics,
                        max_concurrent_tasks, database_session_mngr, sharded_utils_print, db_tables):
        if cls._instance is None:
            cls._instance = cls(webhook, tickermanager, exchange, alerts, logmanager, ccxt_api, metrics,
                                max_concurrent_tasks, database_session_mngr, sharded_utils_print, db_tables)
        return cls._instance

    def __init__(self, webhook, tickermanager, exchange, alerts, logmanager, ccxt_api, metrics,
                 max_concurrent_tasks, database_session_mngr, sharded_utils_print, db_tables):
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
        self._sell_target = self.config._sell_target
        self._buy_target = self.config._buy_target
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
        self.semaphore = asyncio.Semaphore(max_concurrent_tasks)
        self.sharded_utils_print = sharded_utils_print

    def set_trade_parameters(self, start_time, market_data):
        self.start_time = start_time
        self.ticker_cache = market_data['ticker_cache']
        self.market_cache_vol = market_data['filtered_vol']
        self.holdings_list = market_data['spot_positions']

    @property
    def buy_target(self):
        return int(self._buy_target)

    @property
    def sell_target(self):
        return int(self._sell_target)

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

    # async def process_all_rows(self, filtered_ticker_cache, buy_sell_matrix, open_orders):
    #     """Process all rows, updating buy_sell_matrix with latest indicator values from Bollinger Bands."""
    #     skipped_symbols = []
    #     strategy_results = []
    #
    #     try:
    #         # ✅ Ensure 'asset' is the index for easier row access
    #         if "asset" in buy_sell_matrix.columns:
    #             buy_sell_matrix.set_index("asset", inplace=True)
    #
    #         # Fetch OHLCV data asynchronously
    #         tasks = {
    #             row['symbol']: self.fetch_ohlcv_data_from_db(row['symbol'])
    #             for _, row in filtered_ticker_cache.iterrows()
    #         }
    #         ohlcv_data = await asyncio.gather(*tasks.values(), return_exceptions=False)
    #         ohlcv_data_dict = {symbol: data for symbol, data in zip(tasks.keys(), ohlcv_data)}
    #
    #         valid_symbols = [symbol for symbol, data in ohlcv_data_dict.items() if isinstance(data, pd.DataFrame)]
    #
    #         for symbol in valid_symbols:
    #             asset = symbol.split('/')[0]
    #             temp_symbol = symbol.replace("/", "-")
    #
    #             # Skip if there's an open order or hodling
    #             if self.symbol_has_open_order(temp_symbol, open_orders) or asset in self.hodl:
    #                 skipped_symbols.append(temp_symbol)
    #                 continue
    #
    #             ohlcv_df = ohlcv_data_dict[symbol]
    #             ohlcv_df = self.indicators.calculate_indicators(ohlcv_df)
    #
    #             # Validate Bollinger Bands
    #             bollinger_df = self.indicators.calculate_bollinger_bands(ohlcv_df)
    #             if not self.is_valid_bollinger_df(bollinger_df):
    #                 self.log_manager.error(f"Invalid Bollinger DataFrame for {symbol}")
    #                 continue  # Skip invalid symbols
    #
    #             # ✅ Ensure W-Bottoms and M-Tops exist
    #             if 'W-Bottom' not in ohlcv_df.columns or 'M-Top' not in ohlcv_df.columns:
    #                 print(f"⚠️ Missing W-Bottom/M-Top in {symbol}. Available cols: {ohlcv_df.columns}")
    #                 continue
    #
    #             action_data = self.decide_action(ohlcv_df, bollinger_df, symbol)
    #             strategy_results.append({
    #                 'asset': asset,
    #                 'symbol': symbol,
    #                 **action_data  # Flatten action_data into the results
    #             })
    #
    #             # ✅ Ensure asset exists in buy_sell_matrix before updating
    #             if asset not in buy_sell_matrix.asset.values:
    #                 self.log_manager.warning(f"Asset {asset} not found in buy_sell_matrix. Skipping update.")
    #                 continue
    #
    #             # ✅ Efficiently update buy_sell_matrix values
    #             for col in buy_sell_matrix.columns:
    #                 if col in bollinger_df.columns:
    #                     computed_value = bollinger_df[col].iloc[-1]
    #
    #                     # ✅ Extract second value from tuple if applicable
    #                     if isinstance(computed_value, tuple) and len(computed_value) > 1:
    #                         computed_value = computed_value[1]
    #
    #                     existing_tuple = buy_sell_matrix.at[asset, col]
    #
    #                     # ✅ Ensure structured tuple format for updates
    #                     threshold = existing_tuple[2] if isinstance(existing_tuple, tuple) and len(
    #                         existing_tuple) == 3 else None
    #                     buy_sell_matrix.at[asset, col] = (0, computed_value, threshold)
    #
    #             # ✅ Update 'Buy Signal' and 'Sell Signal' using action_data
    #             buy_signal_value = (f"{action_data['buy_sell_data']['Buy Signal'][1]}/"
    #                                 f"{action_data['buy_sell_data']['Buy Signal'][2]}")  # 2nd value of the tuple
    #
    #             sell_signal_value = (f"{action_data['buy_sell_data']['Sell Signal'][1]}/"
    #                                 f"{action_data['buy_sell_data']['Sell Signal'][2]}")   # 2nd value of the tuple
    #
    #             existing_buy_tuple = buy_sell_matrix.at[asset, 'Buy Signal']
    #             existing_sell_tuple = buy_sell_matrix.at[asset, 'Sell Signal']
    #
    #             # ✅ Ensure proper tuple structure
    #             buy_threshold = existing_buy_tuple[2] if isinstance(existing_buy_tuple, tuple) and len(
    #                 existing_buy_tuple) == 3 else None
    #             sell_threshold = existing_sell_tuple[2] if isinstance(existing_sell_tuple, tuple) and len(
    #                 existing_sell_tuple) == 3 else None
    #
    #             # ✅ Assign updated values
    #             buy_sell_matrix.at[asset, 'Buy Signal'] = (buy_signal_value)
    #             buy_sell_matrix.at[asset, 'Sell Signal'] = (sell_signal_value)
    #
    #             # ✅ Use the update function for additional calculations
    #             buy_sell_matrix = self.update_buy_sell_matrix(asset, buy_sell_matrix, bollinger_df)
    #
    #         if skipped_symbols:
    #             print(f"Skipped Symbols: {', '.join(skipped_symbols)}")
    #
    #         return strategy_results, buy_sell_matrix
    #
    #     except Exception as e:
    #         self.log_manager.error(f"Error in process_all_rows: {e}", exc_info=True)
    #         return strategy_results, buy_sell_matrix
    async def process_all_rows(self, filtered_ticker_cache, buy_sell_matrix, open_orders):
        """Process all rows, updating buy_sell_matrix with latest indicator values from Bollinger Bands."""
        skipped_symbols = []
        strategy_results = []

        try:
            # ✅ Ensure 'asset' is the index for easier row access
            if "asset" in buy_sell_matrix.columns:
                buy_sell_matrix.set_index("asset", inplace=True)  # ✅ FIX: Apply inplace=True

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

                # ✅ Skip if there's an open order or asset is in hodl list
                if self.symbol_has_open_order(temp_symbol, open_orders) or asset in self.hodl:
                    skipped_symbols.append(temp_symbol)
                    continue

                ohlcv_df = ohlcv_data_dict[symbol]
                ohlcv_df = self.indicators.calculate_indicators(ohlcv_df)

                # Validate Bollinger Bands
                bollinger_df = self.indicators.calculate_bollinger_bands(ohlcv_df)
                if not self.is_valid_bollinger_df(bollinger_df):
                    self.log_manager.error(f"Invalid Bollinger DataFrame for {symbol}")
                    continue  # Skip invalid symbols

                # ✅ Ensure W-Bottoms and M-Tops exist
                if 'W-Bottom' not in ohlcv_df.columns or 'M-Top' not in ohlcv_df.columns:
                    print(f"⚠️ Missing W-Bottom/M-Top in {symbol}. Available cols: {ohlcv_df.columns}")
                    continue

                action_data = self.decide_action(ohlcv_df, bollinger_df, symbol)
                strategy_results.append({
                    'asset': asset,
                    'symbol': symbol,
                    **action_data  # Flatten action_data into the results
                })

                # ✅ Ensure asset exists in buy_sell_matrix before updating
                if asset not in buy_sell_matrix.index:
                    self.log_manager.warning(f"⚠️ Asset {asset} not found in buy_sell_matrix index. Skipping update.")
                    print(f"Available assets: {buy_sell_matrix.index.tolist()}")  # Debugging output
                    continue

                # ✅ Efficiently update buy_sell_matrix values
                for col in buy_sell_matrix.columns:
                    if col in bollinger_df.columns:
                        computed_value = bollinger_df[col].iloc[-1]

                        # ✅ Extract second value from tuple if applicable
                        if isinstance(computed_value, tuple) and len(computed_value) > 1:
                            computed_value = computed_value[1]

                        existing_tuple = buy_sell_matrix.at[asset, col]

                        # ✅ Ensure structured tuple format for updates
                        threshold = existing_tuple[2] if isinstance(existing_tuple, tuple) and len(
                            existing_tuple) == 3 else None
                        buy_sell_matrix.at[asset, col] = (0, computed_value, threshold)

                # ✅ Update 'Buy Signal' and 'Sell Signal' using action_data
                buy_signal_value = (f"{action_data['buy_sell_data']['Buy Signal'][1]}/"
                                    f"{action_data['buy_sell_data']['Buy Signal'][2]}")  # 2nd value of the tuple

                sell_signal_value = (f"{action_data['buy_sell_data']['Sell Signal'][1]}/"
                                     f"{action_data['buy_sell_data']['Sell Signal'][2]}")  # 2nd value of the tuple

                existing_buy_tuple = buy_sell_matrix.at[asset, 'Buy Signal']
                existing_sell_tuple = buy_sell_matrix.at[asset, 'Sell Signal']

                # ✅ Ensure proper tuple structure
                buy_threshold = existing_buy_tuple[2] if isinstance(existing_buy_tuple, tuple) and len(
                    existing_buy_tuple) == 3 else None
                sell_threshold = existing_sell_tuple[2] if isinstance(existing_sell_tuple, tuple) and len(
                    existing_sell_tuple) == 3 else None

                # ✅ Assign updated values
                buy_sell_matrix.at[asset, 'Buy Signal'] = (buy_signal_value)
                buy_sell_matrix.at[asset, 'Sell Signal'] = (sell_signal_value)

                # ✅ Use the update function for additional calculations
                buy_sell_matrix = self.update_buy_sell_matrix(asset, buy_sell_matrix, bollinger_df)

            if skipped_symbols:
                print(f"Skipped Symbols: {', '.join(skipped_symbols)}")

            return strategy_results, buy_sell_matrix

        except Exception as e:
            self.log_manager.error(f"Error in process_all_rows: {e}", exc_info=True)
            return strategy_results, buy_sell_matrix

    def update_buy_sell_matrix(self, asset, buy_sell_matrix, bollinger_df):
        """Update indicator-related columns in buy_sell_matrix with values from the most recent row."""
        try:
            most_recent_row = bollinger_df.iloc[-1]  # Use the latest available row

            indicator_columns = {
                'Buy Ratio': float(self.buy_ratio),
                'Buy Touch': None,
                'W-Bottom': None,  # No threshold for pattern-based indicators
                'Buy RSI': float(self.buy_rsi),
                'Buy ROC': float(self.roc_24hr),
                'Buy MACD': 0,
                'Buy Swing': None,  # No threshold for pattern-based indicators
                'Sell Ratio': float(self.sell_ratio),
                'Sell Touch': None,
                'M-Top': None,  # No threshold for pattern-based indicators
                'Sell RSI': float(self.sell_rsi),
                'Sell ROC': -float(self.roc_24hr / 2),
                'Sell MACD': 0,
                'Sell Swing': None,  # No threshold for pattern-based indicators
            }

            # ✅ Ensure asset exists in buy_sell_matrix
            if asset not in buy_sell_matrix.index:
                self.log_manager.warning(f"Asset {asset} not found in buy_sell_matrix. Skipping update.")
                return buy_sell_matrix

            # ✅ Efficiently update buy_sell_matrix
            for col, threshold in indicator_columns.items():
                if col in most_recent_row.index:
                    computed_value = most_recent_row[col]

                    # ✅ Extract second element if computed_value is a tuple
                    if isinstance(computed_value, tuple) and len(computed_value) > 1:
                        computed_value = computed_value[1]

                    # ✅ Ensure computed_value is numeric before comparison
                    if threshold is not None and isinstance(computed_value, (int, float, Decimal)):
                        decision = 1 if ((col.startswith("Buy") and computed_value > threshold) or
                                         (col.startswith("Sell") and computed_value < threshold)) else 0
                    else:
                        decision = 0  # Default to no signal if computed_value is invalid or threshold is None

                    # ✅ Ensure structured tuple format before updating
                    buy_sell_matrix.at[asset, col] = (decision, computed_value, threshold)

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

    def decide_action(self, ohlcv_df, bollinger_df, symbol):
        """
        Determine buy or sell action based on indicators and Bollinger Bands.
        """
        try:
            # Call buy_sell to get action and related data
            buy_sell_data, trigger, bollinger_df = self.buy_sell(bollinger_df, ohlcv_df, symbol)

            # Find the volume_24h for the given symbol in self.market_cache
            volume_24h = None  # Default value if not found
            for market in self.market_cache_vol:
                if market.get('symbol') == symbol:
                    volume_24h = market.get('info', {}).get('volume_24h')
                    break  # Exit loop once the symbol is found

            # Return the action dictionary with the added 'volume_24h'
            return {
                'action': buy_sell_data.get('action', None),
                'price': Decimal(ohlcv_df['close'].iloc[0]),
                'band_ratio': buy_sell_data.get('band_ratio', None),
                'trigger': trigger,
                'sell_cond': buy_sell_data.get('Sell Signal', None),
                'buy_sell_data': buy_sell_data,
                'volume_24h': volume_24h  # Add the volume_24h value
            }

        except Exception as e:
            self.log_manager.error(f"Error in decide_action for {symbol}: {e}", exc_info=True)
            return {
                'action': None,
                'price': None,
                'band_ratio': None,
                'trigger': None,
                'sell_cond': None,
                'buy_sell_data': {},
                'volume_24h': None
            }

    def buy_sell(self, bollinger_df, ohlcv_df, symbol):
        """
        Determine buy or sell signals based on the most recent row in Bollinger Bands DataFrame.
        Evaluates conditions like RSI, MACD, ROC, and vectorized scoring for buy/sell signals.
        """
        try:
            # Ensure the necessary indicators are present in ohlcv_df
            if 'ROC' not in ohlcv_df.columns or 'ROC_Diff' not in ohlcv_df.columns:
                ohlcv_df = self.indicators.calculate_indicators(ohlcv_df)

            # Handle missing or NaN values for critical indicators
            if ohlcv_df[['ROC', 'ROC_Diff']].iloc[0].isna().any():
                self.log_manager.warning(f"Missing ROC data for {symbol}. Skipping ROC-based signals.")
                return {'action': 'hold', 'Buy Signal': (0, None, None), 'Sell Signal': (0, None, None)}, None, bollinger_df

            # Extract latest OHLCV row
            last_ohlcv = ohlcv_df.iloc[0]

            # Extract structured tuple values safely
            def get_tuple_value(indicator, default=None):
                return indicator[1] if isinstance(indicator, tuple) else indicator if pd.notna(indicator) else default

            roc_value = get_tuple_value(last_ohlcv.get('ROC'))
            rsi_value = get_tuple_value(last_ohlcv.get('RSI'))
            roc_diff_value = last_ohlcv.get('ROC_Diff', 0.0)

            # Evaluate ROC-based buy and sell signals
            buy_signal_roc = (
                    roc_value is not None and roc_value > 5 and
                    roc_diff_value > 0.3 and
                    rsi_value is not None and rsi_value < 30
            )
            sell_signal_roc = (
                    roc_value is not None and roc_value < -2.5 and
                    roc_diff_value < -0.2 and
                    rsi_value is not None and rsi_value > 70
            )

            # If ROC signals override, return immediately
            if buy_signal_roc:
                return {
                    'action': 'buy',
                    'Buy Signal': (1, round(roc_value, 2), 5),
                    'Sell Signal': (0, None, None),
                    'band_ratio': bollinger_df['band_ratio'].iloc[0]
                }, 'roc_buy', bollinger_df
            elif sell_signal_roc:
                return {
                    'action': 'sell',
                    'Sell Signal': (1, round(roc_value, 2), -2.5),
                    'Buy Signal': (0, None, None),
                    'band_ratio': bollinger_df['band_ratio'].iloc[0]
                }, 'roc_sell', bollinger_df

            # Focus on the most recent row of the Bollinger DataFrame
            most_recent_row = bollinger_df.iloc[0]

            # Define buy and sell condition columns
            buy_conditions = ['Buy RSI', 'Buy MACD', 'Buy Touch', 'Buy Ratio', 'W-Bottom', 'Buy Swing']
            sell_conditions = ['Sell RSI', 'Sell MACD', 'Sell Touch', 'Sell Ratio', 'M-Top', 'Sell Swing']

            # Extract structured tuple values (binary flag only)
            buy_score = sum(most_recent_row[col][0] for col in buy_conditions if col in most_recent_row)
            sell_score = sum(most_recent_row[col][0] for col in sell_conditions if col in most_recent_row)

            # Determine Buy Signal and Sell Signal based on scores
            buy_signal = (1, buy_score,  self.buy_target) if buy_score >=  self.buy_target else (0, buy_score, self.buy_target)
            sell_signal = (1, sell_score, self.sell_target) if sell_score >= self.sell_target else (0, sell_score, self.sell_target)

            # Assign signals to the dataframe
            bollinger_df.at[most_recent_row.name, 'Buy Signal'] = buy_signal
            bollinger_df.at[most_recent_row.name, 'Sell Signal'] = sell_signal

            # Cancel signals if both buy and sell are True
            if buy_signal[0] == 1 and sell_signal[0] == 1:
                buy_signal = (0, buy_score, self.buy_target)
                sell_signal = (0, sell_score, self.sell_target)
            # Final action determination
            action = 'hold'
            trigger = None
            if buy_signal[0] == 1:
                action = 'buy'
                trigger = 'limit_buy'
            elif sell_signal[0] == 1:
                action = 'sell'
                trigger = 'limit_sell'

            return {
                'action': action,
                'price': round(bollinger_df['close'].iloc[0], 4),
                'band_ratio': round(most_recent_row['band_ratio'], 4),
                'Buy Signal': buy_signal,
                'Sell Signal': sell_signal
            }, trigger, bollinger_df

        except Exception as e:
            self.log_manager.error(f"Error in buy_sell() for {symbol}: {e}", exc_info=True)
            return {'action': None, 'Buy Signal': (0, None, None), 'Sell Signal': (0, None, None)}, None, bollinger_df

    def sell_signal_from_indicators(self, symbol, price, trigger, holdings):
        """PART V: Order Execution"""
        try:
            # Convert DataFrame to list of dictionaries if holdings is a DataFrame ( it will be when it comes from
            # "process_sell_order" function)
            if isinstance(holdings, pd.DataFrame):
                holdings = holdings.to_dict('records')
            coin = symbol.split('/')[0]
            sell_order = 'bracket' # for testing defalut is limit
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

    # def update_buy_sell_matrix(self, asset, buy_sell_matrix, bollinger_df):
    #     """
    #     Update indicator-related columns in buy_sell_matrix with values from the most recent row.
    #     """
    #     # Extract the most recent row from Bollinger DataFrame
    #     try:
    #         most_recent_row = bollinger_df.iloc[0]
    #         # Update buy_sell_matrix for the current asset
    #         if asset in buy_sell_matrix['asset'].values:
    #             # Locate the row in buy_sell_matrix corresponding to the asset
    #             idx = buy_sell_matrix[buy_sell_matrix['asset'] == asset].index[0]
    #
    #             # Update the indicator-related columns with values from most_recent_row
    #             indicator_columns = [
    #                 'Buy Ratio', 'Buy Touch', 'W-Bottom', 'Buy RSI', 'Buy ROC', 'Buy MACD', 'Buy Swing',
    #                 'Sell Ratio', 'Sell Touch', 'M-Top', 'Sell RSI', 'Sell ROC', 'Sell MACD', 'Sell Swing',
    #                 'Buy Signal', 'Sell Signal'
    #             ]
    #
    #             for col in indicator_columns:
    #                 if col in most_recent_row.index:  # Ensure the column exists in most_recent_row
    #                     buy_sell_matrix.at[idx, col] = most_recent_row[col]
    #
    #         # If asset doesn't exist in buy_sell_matrix, log a warning (if needed)
    #         else:
    #             self.log_manager.warning(f"Asset {asset} not found in buy_sell_matrix. Skipping update.")
    #
    #         return buy_sell_matrix
    #     except Exception as e:
    #         self.log_manager.error(f"Error updating buy_sell_matrix: {e}", exc_info=True)

    # async def process_all_rows(self, filtered_ticker_cache, buy_sell_matrix, open_orders):
    #     """
    #     Process all rows, handling strategy calculations in a vectorized manner.
    #     """
    #     try:
    #         # Fetch OHLCV data
    #         tasks = {
    #             row['symbol']: self.fetch_ohlcv_data_from_db(row['symbol'])
    #             for _, row in filtered_ticker_cache.iterrows()
    #         }
    #         ohlcv_data = await asyncio.gather(*tasks.values(), return_exceptions=True)
    #         ohlcv_data_dict = {symbol: data for symbol, data in zip(tasks.keys(), ohlcv_data)}
    #
    #         # Filter valid symbols
    #         valid_symbols = [symbol for symbol, data in ohlcv_data_dict.items() if isinstance(data, pd.DataFrame)]
    #         strategy_results = []
    #         bollinger_df = pd.DataFrame()  # Initialize an empty DataFrame for Bollinger Bands
    #         # Initialize a list to collect skipped symbols
    #         skipped_symbols = []
    #
    #         for symbol in valid_symbols:
    #             # Extract the asset (base currency)
    #             asset = symbol.split('/')[0]
    #
    #             # Check if the asset already has an open order or is in holdings
    #             if self.symbol_has_open_order(symbol, open_orders) or self.symbol_in_holdings(symbol):
    #                 skipped_symbols.append(symbol)
    #                 continue
    #
    #             ohlcv_df = ohlcv_data_dict[symbol]
    #             ohlcv_df = self.indicators.calculate_indicators(ohlcv_df)
    #
    #             # Validate Bollinger Bands
    #             bollinger_df = self.indicators.calculate_bollinger_bands(ohlcv_df)
    #             if not self.is_valid_bollinger_df(bollinger_df):
    #                 self.log_manager.error(f"Invalid Bollinger DataFrame for {symbol}")
    #                 continue  # Skip invalid symbols
    #
    #             # Extract the most recent row from Bollinger DataFrame
    #             most_recent_row = bollinger_df.iloc[-1]
    #
    #             # Update buy_sell_matrix for the current asset
    #             if asset in buy_sell_matrix['asset'].values:
    #                 # Locate the row in buy_sell_matrix corresponding to the asset
    #                 idx = buy_sell_matrix[buy_sell_matrix['asset'] == asset].index[0]
    #
    #                 # Update the indicator-related columns with values from most_recent_row
    #                 indicator_columns = [
    #                     'Buy Ratio', 'Buy Touch', 'W-Bottom', 'Buy RSI', 'Buy ROC', 'Buy MACD', 'Buy Swing',
    #                     'Sell Ratio', 'Sell Touch', 'M-Top', 'Sell RSI', 'Sell ROC', 'Sell MACD', 'Sell Swing',
    #                     'Buy Signal', 'Sell Signal'
    #                 ]
    #
    #                 for col in indicator_columns:
    #                     if col in most_recent_row.index:  # Ensure the column exists in most_recent_row
    #                         buy_sell_matrix.at[idx, col] = most_recent_row[col]
    #
    #             # If asset doesn't exist in buy_sell_matrix, log a warning (if needed)
    #             else:
    #                 self.log_manager.warning(f"Asset {asset} not found in buy_sell_matrix. Skipping update.")
    #
    #             # Collect strategy results for further processing
    #             action_data = self.decide_action(ohlcv_df, bollinger_df, symbol)
    #             strategy_results.append({
    #                 'asset': asset,
    #                 'symbol': symbol,
    #                 **action_data  # Flatten action_data into the results
    #             })
    #
    #         # Print summary of skipped symbols
    #         if skipped_symbols:
    #             self.utility.print_data(None, open_orders, None, None, None, None, None)
    #             print(f"Skipped Symbols: {', '.join(skipped_symbols)}")
    #         return strategy_results, buy_sell_matrix
    #
    #     except Exception as e:
    #         self.log_manager.error(f"Error in process_all_rows: {e}", exc_info=True)
    #         return [], buy_sell_matrix

    # async def process_all_rows(self, filtered_ticker_cache, buy_sell_matrix, open_orders):
    #     """ PART IV:
    #     Process all rows, handling strategy calculations in a vectorized manner.
    #     """
    #     try:
    #         # print(filtered_ticker_cache.head())
    #         # print(filtered_ticker_cache.dtypes)
    #         # print([row['symbol'] for _, row in filtered_ticker_cache.iterrows()])
    #         # Fetch OHLCV data
    #         tasks = {
    #             row['symbol']: self.fetch_ohlcv_data_from_db(row['symbol'])
    #             for _, row in filtered_ticker_cache.iterrows()
    #         }
    #         ohlcv_data = await asyncio.gather(*tasks.values(), return_exceptions=False)
    #
    #         ohlcv_data_dict = {symbol: data for symbol, data in zip(tasks.keys(), ohlcv_data)}
    #
    #         # Filter valid symbols
    #         valid_symbols = [symbol for symbol, data in ohlcv_data_dict.items() if isinstance(data, pd.DataFrame)]
    #         strategy_results = []
    #         indicator_updates = []
    #         skipped_symbols = []
    #         temp_symbol = None
    #         for symbol in valid_symbols:
    #             # Extract the asset (base currency)
    #             asset = symbol.split('/')[0]
    #             if "/" in symbol:
    #                 temp_symbol = symbol.replace("/", "-")
    #             # Check if the asset already has an open order or is in holdings
    #             if self.symbol_has_open_order(temp_symbol, open_orders) or self.symbol_in_holdings(asset):
    #                 skipped_symbols.append(temp_symbol)
    #                 continue
    #
    #             ohlcv_df = ohlcv_data_dict[symbol]
    #             ohlcv_df = self.indicators.calculate_indicators(ohlcv_df)
    #
    #             # Validate Bollinger Bands
    #             bollinger_df = self.indicators.calculate_bollinger_bands(ohlcv_df)
    #             if not self.is_valid_bollinger_df(bollinger_df):
    #                 self.log_manager.error(f"Invalid Bollinger DataFrame for {symbol}")
    #                 continue  # Skip invalid symbols
    #
    #             # Collect strategy results for further processing
    #             action_data = self.decide_action(ohlcv_df, bollinger_df, symbol)
    #             strategy_results.append({
    #                 'asset': asset,
    #                 'symbol': symbol,
    #                 **action_data  # Flatten action_data into the results
    #             })
    #
    #             # Perform the updates to buy_sell_matrix after action_data is computed
    #             buy_sell_matrix = self.update_buy_sell_matrix(asset, buy_sell_matrix, bollinger_df)
    #
    #         # Print summary of skipped symbols
    #         if skipped_symbols:
    #             self.sharded_utils_print.print_data(None, open_orders, None, None, None, None, None)
    #             print(f"Skipped Symbols: {', '.join(skipped_symbols)}")
    #    #         return strategy_results, buy_sell_matrix
    #
    #     except Exception as e:
    #         self.log_manager.error(f"Error in process_all_rows: {e}", exc_info=True)
    #         return [], buy_sell_matrix