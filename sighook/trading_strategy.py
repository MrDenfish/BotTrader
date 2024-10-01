
import asyncio
import pandas as pd
from sqlalchemy import select
from indicators import Indicators
from database_table_models import OHLCVData


class TradingStrategy:
    """focus on decision-making based on data provided by MarketManager"""
    def __init__(self, webhook, tickermanager, utility, exchange, alerts, logmanager, ccxt_api, metrics, config,
                 max_concurrent_tasks, database_session_mngr):

        self._version = config.program_version
        self.exchange = exchange
        self.alerts = alerts
        self.ccxt_exceptions = ccxt_api
        self.log_manager = logmanager
        self.utility = utility
        self.ticker_manager = tickermanager
        self.indicators = Indicators(config, logmanager)
        self.market_metrics = metrics
        self.webhook = webhook
        self.ohlcv_data = {}  # A dictionary to store OHLCV data for each symbol
        self.results = None
        self.ticker_cache = None
        self.market_cache = None
        self.start_time = None
        self.db_manager = database_session_mngr
        self.semaphore = asyncio.Semaphore(max_concurrent_tasks)

    def set_trade_parameters(self, start_time, ticker_cache, market_cache):
        self.start_time = start_time
        self.ticker_cache = ticker_cache
        self.market_cache = market_cache

    async def process_all_rows(self, filtered_ticker_cache, buy_sell_matrix):
        """PART IV: Trading Strategies"""
        tasks = [self.process_row(row, buy_sell_matrix) for _, row in filtered_ticker_cache.iterrows()]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        buy_sell_matrix = self.process_row_results(results, buy_sell_matrix)
        self.utility.print_elapsed_time(self.start_time, 'process all row')
        return results, buy_sell_matrix  # Return both the results and the aggregated orders

    async def process_row(self, row, buy_sell_matrix):
        """PART IV: Trading Strategies"""
        try:
            asset = row['symbol']
            if 'price' not in row['info']:
                print(f"Warning: 'price' not found in row['info'] for {asset}")
                row['info']['price'] = '0.0'  # or set a default
            price_str = row['info']['price']
            price = float(price_str) if price_str else 0.0
            if asset == 'USD/USD':
                return None

            # Fetch OHLCV data from the database
            ohlcv_df = await self.fetch_ohlcv_data_from_db(asset)
            if ohlcv_df is None:
                return None

            # Check if DataFrame contains valid data
            if ohlcv_df.isnull().values.any():  # This is safe now because ohlcv_df is a DataFrame
                print(f"DataFrame contains NaN values for {asset}")
                return None

            # Ensure the DataFrame is sorted by time
            ohlcv_df.sort_values(by='time', inplace=True)

            # Calculate Bollinger Bands
            bollinger_df = self.indicators.calculate_bollinger_bands(ohlcv_df)
            if not isinstance(bollinger_df, pd.DataFrame):
                print(f"bollinger_df is not a DataFrame for asset {asset}")
                return None
            action_data = self.decide_action(ohlcv_df, bollinger_df, asset, buy_sell_matrix)
            # Ensure all necessary keys are present in action_data
            required_keys = ['action', 'band_ratio', 'sell_cond', 'trigger', 'updates']
            for key in required_keys:
                if key not in action_data:
                    print(f"Warning: {key} missing in action_data for asset {asset}")
                    action_data[key] = None  # Set a default value if necessary
            #<><><><><><><><> DEBUG CODE <><><><><><><><>
            # print(f"Asset: {asset}")
            # print(f"Action Data: {action_data}")
            # print(f"Price: {price}")
            # print(f"Row: {row}")
            # print(f"Bollinger DataFrame:\n{bollinger_df}")
            #<><><><><><><><> DEBUG CODE <><><><><><><><>
            order_info = {
                'symbol': asset,
                'action': action_data.get('action') or 'none',
                'price': price if price is not None else 0.0,
                'value': float(row['free']) * price if row['free'] and price else 0.0,
                'trigger': action_data.get('trigger') or 'none',
                'band_ratio': action_data.get('band_ratio') if action_data.get('band_ratio') else 0.0,
                'sell_cond': action_data.get('sell_cond') or 'none',
                'bollinger_df': bollinger_df.to_dict('list') if isinstance(bollinger_df, pd.DataFrame) else {},
                'action_data': action_data
            }

            return {'order_info': order_info}


        except Exception as e:
            self.log_manager.error(f"Error processing row for symbol {row['symbol']}: {e}", exc_info=True)
            return {"error": f"Error processing row for symbol {row['symbol']}: {str(e)}"}

    async def fetch_ohlcv_data_from_db(self, asset):
        """Fetch OHLCV data from the database for a given asset."""
        async with self.db_manager.AsyncSessionLocal() as session:
            result = await session.execute(
                select(OHLCVData).filter(OHLCVData.symbol == asset).order_by(OHLCVData.time.desc()).limit(1440)
            )
            ohlcv_data = result.scalars().all()

            if ohlcv_data:
                # Convert list of SQLAlchemy objects to a pandas DataFrame
                ohlcv_df = pd.DataFrame([{
                    'time': data.time,
                    'open': data.open,
                    'high': data.high,
                    'low': data.low,
                    'close': data.close,
                    'volume': data.volume
                } for data in ohlcv_data])

                # Now check for NaN values in the DataFrame
                if ohlcv_df.isnull().values.any():
                    self.log_manager.error(f"NaN values detected in OHLCV data for {asset}")
                    return None

                return ohlcv_df  # Return the DataFrame, not the list
            return None

    def decide_action(self, df, bollinger_df, symbol, buy_sell_matrix):
        """PART IV: Trading Strategies"""
        trigger = None
        updates = {}  # Initialize a dictionary to store updates
        buy_sell_data = {}

        df = self.indicators.calculate_trends(df)  # Calculate 50, 200 volatility and sma trends
        df = self.indicators.calculate_rsi(df)  # Calculate RSI
        df = self.indicators.calculate_roc(df, symbol)  # Calculate ROC
        df = self.indicators.calculate_macd(df)  # Calculate MACD
        df = self.indicators.swing_trading_signals(df)
        if self.is_valid_bollinger_df(bollinger_df):
            #buy_sell_data, trigger = self.test_buy_sell(bollinger_df, df, symbol, force_buy=True) # debug
            buy_sell_data, trigger = self.buy_sell(bollinger_df, df, symbol)  # get buy sell dat
            if trigger:
                print(f"Trigger for {symbol}: {trigger}")
        else:
            self.log_manager.error(f"Invalid Bollinger DataFrame for {symbol}")
            return buy_sell_data
        coin = symbol.split('/')[0]
        if coin in buy_sell_matrix['coin'].values:
            updates[coin] = {
                'Buy Ratio': buy_sell_data['buy_sig_ratio'],
                'Buy Touch': buy_sell_data['buy_sig_touch'],
                'W-Bottom Signal': buy_sell_data['w_bottom'],
                'Buy RSI': buy_sell_data['buy_rsi'],
                'Buy ROC': buy_sell_data['buy_signal_roc'],
                'Buy MACD': buy_sell_data['buy_signal_macd'],  # Include MACD buy signal
                'Buy Swing': buy_sell_data['buy_swing'],
                'Buy Signal': buy_sell_data['buy_signal'],
                'Sell Ratio': buy_sell_data['sell_sig_ratio'],
                'Sell Touch': buy_sell_data['sell_sig_touch'],
                'M-Top Signal': buy_sell_data['m_top_signal'],
                'Sell RSI': buy_sell_data['sell_rsi'],
                'Sell ROC': buy_sell_data['sell_signal_roc'],
                'Sell MACD': buy_sell_data['sell_macd'],  # Include MACD sell signal
                'Sell Swing': buy_sell_data['sell_swing'],
                'Sell Signal': buy_sell_data['sell_signal']
            }
        action = buy_sell_data['action']
        sell_cond = buy_sell_data['sell_signal']
        band_ratio = buy_sell_data['band_ratio']

        return {'action': action, 'band_ratio': band_ratio, 'trigger': trigger, 'updates': updates, 'sell_cond': sell_cond}

    def process_row_results(self, results, buy_sell_matrix):
        """PART IV: Trading Strategies
        enter results from indicators and Order cancellation and Data Collection into the buy_sell_matrix"""
        try:
            for result in results:
                if result is None or isinstance(result, Exception) or "error" in result:
                    continue  # Skip this result and move on to the next

                # Skip the iteration if the item is not a dictionary
                if not isinstance(result, dict):
                    continue
                if not result.get('order_info'):
                    continue

                order_info = result.get('order_info')
                # symbol = order_info.get('symbol')
                action_data = order_info.get('action_data')
                if action_data and 'updates' in action_data:
                    updates = action_data['updates']
                    # load indicator results into the buy_sell_matrix
                    for coin, coin_updates in updates.items():
                        # Ensure the coin is in the buy_sell_matrix
                        if coin in buy_sell_matrix['coin'].values:
                            for col, value in coin_updates.items():
                                # Update only the columns that exist in buy_sell_matrix
                                if col in buy_sell_matrix.columns:
                                    buy_sell_matrix.loc[buy_sell_matrix['coin'] == coin, col] = value

            return buy_sell_matrix
        except Exception as e:
            self.log_manager.error(f"fetch_ohlcv: {e}", exc_info=True)



    @staticmethod
    def is_valid_bollinger_df(bollinger_df):
        """PART III: Order cancellation and Data Collection"""
        return not (bollinger_df is None or
                    bollinger_df.iloc[-1][['basis', 'upper', 'lower', 'band_ratio']].isna().any() or
                    bollinger_df.empty)

    def test_buy_sell(self, bollinger_df, df, symbol, force_buy=False):
        """PART IV: Trading Strategies"""
        buy_sell_data = {
            'action': None,
            'buy_signal': '',  # Initialize as an empty string
            'sell_signal': '',  # Initialize as an empty string
            'band_ratio': None,
            'buy_sig_touch': False,
            'sell_sig_touch': False,
            'buy_sig_ratio': False,
            'sell_sig_ratio': False,
            'w_bottom': False,
            'm_top_signal': False,
            'buy_rsi': False,
            'sell_rsi': False,
            'buy_signal_roc': False,
            'sell_signal_roc': False,
            'buy_signal_macd': False,
            'sell_macd': False,
            'buy_swing': False,
            'sell_swing': False
        }
        trigger = None

        try:
            if force_buy:
                self.log_manager.info(f"Forcing buy condition for {symbol}")
                # Force a buy condition for testing purposes
                buy_sell_data['buy_sig_touch'] = True  # Force Bollinger Band touch condition
                buy_sell_data['buy_rsi'] = True  # Force RSI condition
                buy_sell_data['buy_signal_roc'] = True  # Force ROC condition
                buy_sell_data['buy_signal'] = 'bst-brs-bro'  # Set signals for testing
                buy_sell_data['action'] = 'buy'
                trigger = buy_sell_data['buy_signal']
                return buy_sell_data, trigger

            # Standard logic if force_buy is False
            if len(bollinger_df) < 20 or bollinger_df.iloc[-1][['basis', 'upper', 'lower', 'band_ratio']].isna().any():
                return buy_sell_data  # Not enough data or NaN values present

            last_row = bollinger_df.iloc[-1]
            prev_row = bollinger_df.iloc[-2]

            # Ratio-based signals
            buy_sell_data['buy_sig_ratio'] = (abs(prev_row['band_ratio'] - 1) < 0.05 < abs(last_row['band_ratio'] - 1))
            buy_sell_data['sell_sig_ratio'] = abs(prev_row['band_ratio'] - 1) > 0.05 and prev_row['basis'] > last_row[
                'basis']

            # Buy Signal: Price touching or below the lower Bollinger Band
            buy_sell_data['buy_sig_touch'] = last_row['close'] < last_row['lower']

            # Sell Signal: Price touching or above the upper Bollinger Band
            buy_sell_data['sell_sig_touch'] = last_row['close'] > last_row['upper']

            # bottom buy, top sell
            buy_sell_data['w_bottom'], buy_sell_data['m_top_signal'] = (
                self.indicators.algorithmic_trading_strategy(bollinger_df))

            # RSI-based signals
            buy_sell_data['buy_sig_rsi'] = df['RSI'].iloc[-1] < 30  # RSI less than 30 indicates oversold
            buy_sell_data['sell_sig_rsi'] = df['RSI'].iloc[-1] > 70  # RSI greater than 70 indicates overbought

            # ROC-based signals
            buy_sell_data['buy_signal_roc'] = ((df['ROC'].iloc[-1] > 5) and (df['ROC_Diff'].iloc[-1] > 0.3) and
                                               (df['RSI'].iloc[-1] < 30))
            buy_sell_data['sell_signal_roc'] = ((df['ROC'].iloc[-1] < -2.5) and (df['ROC_Diff'].iloc[-1] < -0.2) and
                                                (df['RSI'].iloc[-1] > 70))

            # MACD-based signals
            buy_sell_data['buy_signal_macd'] = (df['MACD_Histogram'].iloc[-1] < 0 < df['MACD_Histogram'].iloc[0])

            # Add swing signals
            buy_sell_data['buy_swing'] = df['Buy Swing'].iloc[-1]
            buy_sell_data['sell_swing'] = df['Sell Swing'].iloc[-1]

            # ROC-based signals with precedence
            if buy_sell_data['buy_signal_roc']:
                buy_sell_data['buy_signal'] = 'bro'
            elif buy_sell_data['sell_signal_roc']:
                buy_sell_data['sell_signal'] = 'sro'

            # Count buy/sell conditions
            buy_conditions_met = buy_sell_data['buy_signal'].count('-') + bool(buy_sell_data['buy_signal'])
            sell_conditions_met = buy_sell_data['sell_signal'].count('-') + bool(buy_sell_data['sell_signal'])

            # Determine the final action based on the triggers
            if buy_conditions_met >= 3 or 'bro' in buy_sell_data['buy_signal']:
                self.log_manager.info(f"Buy conditions met for {symbol}. Buy Signal: {buy_sell_data['buy_signal']}") #Debug
                buy_sell_data['action'] = 'buy'
                trigger = buy_sell_data['buy_signal']

            elif sell_conditions_met >= 3 or 'sro' in buy_sell_data['sell_signal']:

                buy_sell_data['action'] = 'sell'
                trigger = buy_sell_data['sell_signal']
            else:
                self.log_manager.info(f"THERE ARE NO BUY OR SELL conditions met for {symbol}. Signals: {buy_sell_data}")
                #debug
            return buy_sell_data, trigger

        except Exception as e:
            self.log_manager.error(f'Error in buy_sell() {symbol}: {e}', exc_info=True)
            return buy_sell_data, None

    def buy_sell(self, bollinger_df, df, symbol):
        """PART IV: Trading Strategies"""
        """Determine buy or sell signal based on Bollinger band data, rsi, roc, and macd values."""

        buy_sell_data = {
            'action': None,
            'buy_signal': '',  # Initialize as an empty string
            'sell_signal': '',  # Initialize as an empty string
            'band_ratio': None,
            'buy_sig_touch': False,
            'sell_sig_touch': False,
            'buy_sig_ratio': False,
            'sell_sig_ratio': False,
            'w_bottom': False,
            'm_top_signal': False,
            'buy_rsi': False,
            'sell_rsi': False,
            'buy_signal_roc': False,
            'sell_signal_roc': False,
            'buy_signal_macd': False,
            'sell_macd': False,
            'buy_swing': False,
            'sell_swing': False
        }
        trigger = None
        try:
            if len(bollinger_df) < 20 or bollinger_df.iloc[-1][['basis', 'upper', 'lower', 'band_ratio']].isna().any():
                return buy_sell_data  # Not enough data or NaN values present

            last_row = bollinger_df.iloc[-1]
            prev_row = bollinger_df.iloc[-2]

            # RSI-based signals
            buy_sell_data['buy_rsi'] = df['RSI'].iloc[-1] < 30  # RSI less than 30 indicates oversold
            buy_sell_data['sell_rsi'] = df['RSI'].iloc[-1] > 70  # RSI greater than 70 indicates overbought

            # Ratio-based signals
            buy_sell_data['buy_sig_ratio'] = (abs(prev_row['band_ratio'] - 1) < 0.05 < abs(last_row['band_ratio'] - 1))
            buy_sell_data['sell_sig_ratio'] = abs(prev_row['band_ratio'] - 1) > 0.05 and prev_row['basis'] > last_row[
                'basis']

            # Buy Signal: Price touching or below the lower Bollinger Band
            buy_sell_data['buy_sig_touch'] = last_row['close'] < last_row['lower']
            # Sell Signal: Price touching or above the upper Bollinger Band
            buy_sell_data['sell_sig_touch'] = last_row['close'] > last_row['upper']

            # W-Bottom (buy) and M-Top (sell) signals
            buy_sell_data['w_bottom'], buy_sell_data['m_top_signal'] = (
                self.indicators.algorithmic_trading_strategy(bollinger_df))

            # MACD-based signals
            buy_sell_data['buy_signal_macd'] = (df['MACD_Histogram'].iloc[-1] < 0 < df['MACD_Histogram'].iloc[0])
            buy_sell_data['sell_macd'] = (df['MACD_Histogram'].iloc[-1] > 0 > df['MACD_Histogram'].iloc[0])

            # ROC-based signals
            buy_sell_data['buy_signal_roc'] = ((df['ROC'].iloc[-1] > 5) and (df['ROC_Diff'].iloc[-1] > 0.3) and
                                               (df['RSI'].iloc[-1] < 30))
            buy_sell_data['sell_signal_roc'] = ((df['ROC'].iloc[-1] < -2.5) and (df['ROC_Diff'].iloc[-1] < -0.2) and
                                                (df['RSI'].iloc[-1] > 70))

            # If ROC-based signals are set, they override the other conditions
            if buy_sell_data['buy_signal_roc']:
                buy_sell_data['buy_signal'] = 'bro'
                buy_sell_data['action'] = 'buy'
                trigger = 'bro'
            elif buy_sell_data['sell_signal_roc']:
                buy_sell_data['sell_signal'] = 'sro'
                buy_sell_data['action'] = 'sell'
                trigger = 'sro'

            # Count all buy signals that are True
            buy_conditions = [
                ('bsr', buy_sell_data['buy_sig_ratio']),
                ('bst', buy_sell_data['buy_sig_touch']),
                ('wbs', buy_sell_data['w_bottom']),
                ('brs', buy_sell_data['buy_rsi']),
                ('bmc', buy_sell_data['buy_signal_macd']),
                ('bss', buy_sell_data['buy_swing'])
            ]

            # Count all sell signals that are True
            sell_conditions = [
                ('ssr', buy_sell_data['sell_sig_ratio']),
                ('sst', buy_sell_data['sell_sig_touch']),
                ('mts', buy_sell_data['m_top_signal']),
                ('srs', buy_sell_data['sell_rsi']),
                ('smc', buy_sell_data['sell_macd']),
                ('sss', buy_sell_data['sell_swing'])
            ]

            # Filter out the active signals (True conditions)
            active_buy_signals = [label for label, condition in buy_conditions if condition]
            active_sell_signals = [label for label, condition in sell_conditions if condition]

            # Build the buy and sell signal strings
            buy_sell_data['buy_signal'] = '-'.join(active_buy_signals)
            buy_sell_data['sell_signal'] = '-'.join(active_sell_signals)

            # Count the conditions met
            buy_conditions_met = len(active_buy_signals)
            sell_conditions_met = len(active_sell_signals)

            # Determine the final action based on the triggers
            if buy_conditions_met >= 3:
                buy_sell_data['action'] = 'buy'
                trigger = buy_sell_data['buy_signal']
            elif sell_conditions_met >= 3:
                buy_sell_data['action'] = 'sell'
                trigger = buy_sell_data['sell_signal']

            return buy_sell_data, trigger
        except Exception as e:
            self.log_manager.error(f'Error in buy_sell() {symbol}: {e}', exc_info=True)
        return buy_sell_data, None


    def sell_signal_from_indicators(self, symbol, price, trigger, holdings):
        """PART V: Order Execution"""
        try:
            # Convert DataFrame to list of dictionaries if holdings is a DataFrame ( it will be when it comes from
            # "process_sell_order" function)
            if isinstance(holdings, pd.DataFrame):
                holdings = holdings.to_dict('records')
            coin = symbol.split('/')[0]
            if any(item['quote'] == coin for item in holdings):
                sell_action = 'close_at_limit'
                sell_pair = symbol
                sell_limit = price
                sell_order = 'limit'
                self.log_manager.sell(f'Sell signal created for {symbol}, order triggered by {trigger} @ '
                                                     f'{price}.')
                return sell_action, sell_pair, sell_limit, sell_order

            return None, None, None, None
        except Exception as e:
            self.log_manager.error(f'Error in handle_action(): {e}\nTraceback:,', exc_info=True)
            return None, None, None, None
