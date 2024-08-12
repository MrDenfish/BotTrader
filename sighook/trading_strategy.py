

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
            price_str = row['info']['price']
            price = float(price_str) if price_str else 0.0
            if asset == 'USD/USD':
                return None

            # Fetch OHLCV data from the database
            ohlcv_data = await self.fetch_ohlcv_data_from_db(asset)
            if ohlcv_data is None:
                return None

            # Convert the list of SQLAlchemy objects to a DataFrame
            ohlcv_df = pd.DataFrame([{
                'time': data.time,
                'open': data.open,
                'high': data.high,
                'low': data.low,
                'close': data.close,
                'volume': data.volume
            } for data in ohlcv_data])

            # Check if DataFrame contains valid data
            if ohlcv_df.isnull().values.any():
                print(f"DataFrame contains NaN values for {asset}")
                return None

            # Ensure the DataFrame is sorted by time
            ohlcv_df.sort_values(by='time', inplace=True)

            # Calculate Bollinger Bands
            bollinger_df = self.indicators.calculate_bollinger_bands(ohlcv_df)
            action_data = self.decide_action(ohlcv_df, bollinger_df, asset, row['info']['price'], buy_sell_matrix)

            order_info = {
                'symbol': asset,
                'action': action_data.get('action'),
                'price': price,
                'value': row['free'] * price,
                'trigger': action_data.get('trigger'),
                'band_ratio': action_data.get('band_ratio'),
                'sell_cond': action_data.get('sell_cond'),
                'bollinger_df': bollinger_df.to_dict('list'),  # Convert DataFrame to a more serializable format
                'action_data': action_data,
                'trailing_stop': 'trailing_stop' in row['info']
            }
            return {'order_info': order_info}

        except Exception as e:
            return {"error": f"Error processing row for symbol {row['symbol']}: {str(e)}"}

    async def fetch_ohlcv_data_from_db(self, asset):
        """Fetch OHLCV data from the database for a given asset."""
        async with self.db_manager.AsyncSessionLocal() as session:
            result = await session.execute(
                select(OHLCVData).filter(OHLCVData.symbol == asset).order_by(OHLCVData.time.desc()).limit(1440)
            )
            ohlcv_data = result.scalars().all()
            if ohlcv_data:
                return ohlcv_data
            return None

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
                symbol = order_info.get('symbol')
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
            self.log_manager.sighook_logger.error(f"fetch_ohlcv: {e}", exc_info=True)

    def decide_action(self, df, bollinger_df, symbol, price, buy_sell_matrix):
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
            buy_sell_data, trigger = self.buy_sell(bollinger_df, df, symbol)  # get buy sell dat
            if trigger:
                print(f"Trigger for {symbol}: {trigger}")
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

    @staticmethod
    def is_valid_bollinger_df(bollinger_df):
        """PART III: Order cancellation and Data Collection"""
        return not (bollinger_df is None or
                    bollinger_df.iloc[-1][['basis', 'upper', 'lower', 'band_ratio']].isna().any() or
                    bollinger_df.empty)

    def buy_sell(self, bollinger_df, df, symbol):
        """PART IV: Trading Strategies"""
        """Determine buy or sell signal based on Bollinger band data, rsi and roc macd values. Values of the matrix are
                of boolean type. If 3 or more conditions are true, then the signal is true. If 3 or more conditions are
                false,
                the signal is False. ROC values can override the 3 condition rule if the rate of change is significant.
                bsr & ssr - Ratio-based signal
                bst & sst - Price touching or below the lower Bollinger Band
                wbs & mts - W-Bottom Signal, M-Tops Signal
                brs & srs - RSI Signal (oversold or overbought)
                bro & sro- ROC Signal
                bmc & smc - MACD Signal
                bss & sss - Swing Signal """

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
            # Ratio-based signals
            buy_sell_data['buy_sig_ratio'] = (
                        abs(prev_row['band_ratio'] - 1) < 0.05 and abs(last_row['band_ratio'] - 1) > 0.05)
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

            if buy_sell_data['buy_signal_roc']:
                self.log_manager.sighook_logger.warning(f'ROC buy signal for {symbol} ROC: {df["ROC"].iloc[-1]} ROC_Diff:'
                                                        f' {df["ROC_Diff"].iloc[-1]}')
            if buy_sell_data['sell_signal_roc']:
                self.log_manager.sighook_logger.warning(f'ROC sell signal for {symbol} ROC: {df["ROC"].iloc[-1]} ROC_Diff: '
                                                        f'{df["ROC_Diff"].iloc[-1]}')

            # MACD-based signals
            # Check if the MACD line has crossed above the Signal Line for a buy signal
            buy_sell_data['buy_signal_macd'] = (df['MACD'].iloc[-2] < df['Signal_Line'].iloc[-2] and
                                                df['MACD'].iloc[-1] > df['Signal_Line'].iloc[-1])

            # Check if the MACD line has crossed below the Signal Line for a sell signal
            buy_sell_data['sell_macd'] = (df['MACD'].iloc[-2] > df['Signal_Line'].iloc[-2] and
                                          df['MACD'].iloc[-1] < df['Signal_Line'].iloc[-1])
            # swing trade signals
            buy_sell_data['buy_swing'] = df['Buy Swing'].iloc[-1]
            buy_sell_data['sell_swing'] = df['Sell Swing'].iloc[-1]

            # ROC-based signals with precedence
            if buy_sell_data['buy_signal_roc']:  # ROC buy condition
                buy_sell_data['buy_signal'] = 'bro'  # Set ROC as the primary trigger
            elif buy_sell_data['sell_signal_roc']:  # ROC sell condition
                buy_sell_data['sell_signal'] = 'sro'  # Set ROC as the primary trigger

            # Function to add signals without leading hyphen for the first condition
            def add_signal(signal_str, new_signal):
                return new_signal if not signal_str else f"{signal_str}-{new_signal}"

            # Append other signals only if ROC signal is not set
            if not buy_sell_data['buy_signal']:
                # Similar logic for adding buy signals...
                if buy_sell_data['buy_sig_ratio']:
                    buy_sell_data['buy_signal'] = add_signal(buy_sell_data['buy_signal'], 'bsr')
                if buy_sell_data['buy_sig_touch']:
                    buy_sell_data['buy_signal'] = add_signal(buy_sell_data['buy_signal'], 'bst')
                if buy_sell_data['w_bottom']:
                    buy_sell_data['buy_signal'] = add_signal(buy_sell_data['buy_signal'], 'wbs')
                if buy_sell_data['buy_rsi']:
                    buy_sell_data['buy_signal'] = add_signal(buy_sell_data['buy_signal'], 'brs')
                if buy_sell_data['buy_signal_macd']:
                    buy_sell_data['buy_signal'] = add_signal(buy_sell_data['buy_signal'], 'bmc')
                if buy_sell_data['buy_swing']:  # Assuming this is a boolean
                    buy_sell_data['buy_signal'] = add_signal(buy_sell_data['buy_signal'], 'bss')

            if not buy_sell_data['sell_signal']:
                # Similar logic for adding sell signals...
                if buy_sell_data['sell_sig_ratio']:
                    buy_sell_data['sell_signal'] = add_signal(buy_sell_data['sell_signal'], 'ssr')
                if buy_sell_data['sell_sig_touch']:
                    buy_sell_data['sell_signal'] = add_signal(buy_sell_data['sell_signal'], 'sst')
                if buy_sell_data['m_top_signal']:
                    buy_sell_data['sell_signal'] = add_signal(buy_sell_data['sell_signal'], 'mts')
                if buy_sell_data['sell_rsi']:
                    buy_sell_data['sell_signal'] = add_signal(buy_sell_data['sell_signal'], 'srs')
                if buy_sell_data['sell_macd']:
                    buy_sell_data['sell_signal'] = add_signal(buy_sell_data['sell_signal'], 'smc')
                if buy_sell_data['sell_swing']:
                    buy_sell_data['sell_signal'] = add_signal(buy_sell_data['sell_signal'], 'sss')

            # More robust way to count conditions: Count hyphens and add one for the first condition
            buy_conditions_met = buy_sell_data['buy_signal'].count('-') + bool(buy_sell_data['buy_signal'])
            sell_conditions_met = buy_sell_data['sell_signal'].count('-') + bool(buy_sell_data['sell_signal'])

            # Determine the final action based on the triggers
            if buy_conditions_met >= 3 or 'bro' in buy_sell_data['buy_signal']:
                buy_sell_data['action'] = 'buy'
                trigger = buy_sell_data['buy_signal']
            elif sell_conditions_met >= 3 or 'sro' in buy_sell_data['sell_signal']:
                buy_sell_data['action'] = 'sell'
                trigger = buy_sell_data['sell_signal']

            return buy_sell_data, trigger
        except Exception as e:
            self.log_manager.sighook_logger.error(f'Error in buy_sell() {symbol}: {e}', exc_info=True)
        return buy_sell_data, None


    def sell_signal_from_indicators(self, symbol, price, trigger, holdings):
        """PART V: Order Execution"""
        try:
            # Convert DataFrame to list of dictionaries if holdings is a DataFrame ( it will be when it comes from
            # "process_sell_order" function)
            if isinstance(holdings, pd.DataFrame):
                holdings = holdings.to_dict('records')
            coin = symbol.split('/')[0]
            if any(item['quote_currency'] == coin for item in holdings):
                sell_action = 'close_at_limit'
                sell_pair = symbol
                sell_limit = price
                sell_order = 'limit'
                self.log_manager.sighook_logger.sell(f'Sell signal created for {symbol}, order triggered by {trigger} @ '
                                                     f'{price}.')
                return sell_action, sell_pair, sell_limit, sell_order

            return None, None, None, None
        except Exception as e:
            self.log_manager.sighook_logger.error(f'Error in handle_action(): {e}\nTraceback:,', exc_info=True)
            return None, None, None, None
