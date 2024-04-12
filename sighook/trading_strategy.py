

import asyncio
import pandas as pd
from indicators import Indicators


class TradingStrategy:
    """focus on decision-making based on data provided by MarketManager"""
    def __init__(self, webhook, tickermanager, utility, exchange, alerts, logmanager, ccxt_api, metrics, config,
                 max_concurrent_tasks):

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
        self.semaphore = asyncio.Semaphore(max_concurrent_tasks)

    def set_trade_parameters(self, start_time, ticker_cache, market_cache):
        self.start_time = start_time
        self.ticker_cache = ticker_cache
        self.market_cache = market_cache

    async def process_all_rows(self, filtered_ticker_cache, buy_sell_matrix, ohlcv_data_dict):
        """PART IV: Trading Strategies"""

        tasks = [self.process_row(row, buy_sell_matrix, ohlcv_data_dict) for _, row in
                 filtered_ticker_cache.iterrows()]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        buy_sell_matrix = self.process_row_results(results, buy_sell_matrix)
        self.utility.print_elapsed_time(self.start_time, 'process all row')
        return results, buy_sell_matrix  # Return both the results and the aggregated orders

    # @profile
    async def process_row(self, row, buy_sell_matrix, ohlcv_data_dict):
        """PART IV: Trading Strategies"""
        try:
            symbol = row['symbol'].replace('-', '/')
            price_str = row['info']['price']
            price = float(price_str) if price_str else 0.0
            if symbol == 'USD/USD':
                return None
            ohlcv_data = ohlcv_data_dict.get(symbol)
            if ohlcv_data is None:
                return None
                # Access the DataFrame directly from ohlcv_data
            ohlcv_df = ohlcv_data['data']
            bollinger_df = self.indicators.calculate_bollinger_bands(ohlcv_df)
            action_data = self.decide_action(ohlcv_df, bollinger_df, symbol, row['info']['price'], buy_sell_matrix)

            order_info = {
                'symbol': symbol,
                'action': action_data.get('action'),
                'price': price,
                'trigger': action_data.get('trigger'),
                'band_ratio': action_data.get('band_ratio'),
                'sell_cond': action_data.get('sell_cond'),
                # Include bollinger_df and action_data for further use
                'bollinger_df': bollinger_df.to_dict('list'),  # Convert DataFrame to a more serializable format
                'action_data': action_data
            }
            return {'order_info': order_info}

        except Exception as e:
            return {"error": f"Error processing row for symbol {row['symbol']}:  {str(e)}"}

    def process_row_results(self, results, buy_sell_matrix):
        """PART IV: Trading Strategies
        enter results from indicators and Order cancellation and Data Collection into the buy_sell_matrix"""
        try:
            for result in results:
                if isinstance(result, Exception) or "error" in result:
                    continue  # Skip this result and move on to the next

                    # Skip the iteration if the item is not a dictionary
                if not isinstance(result, dict):
                    continue

                symbol = result.get('symbol')
                action_data = result['order_info']['action_data']
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
        df = self.indicators.calculate_roc(df)  # Calculate ROC
        df = self.indicators.calculate_macd(df)  # Calculate MACD
        df = self.indicators.swing_trading_signals(df)
        if self.is_valid_bollinger_df(bollinger_df):
            buy_sell_data, trigger = self.buy_sell(bollinger_df, df, symbol)  # get buy sell data
        coin = symbol.split('/')[0]
        if coin in buy_sell_matrix['coin'].values:
            updates[coin] = {
                'Buy Ratio': buy_sell_data['buy_sig_ratio'],
                'Buy Touch': buy_sell_data['buy_sig_touch'],
                'W-Bottom Signal': buy_sell_data['w_bottom_signal'],
                'Buy RSI': buy_sell_data['buy_signal_rsi'],
                'Buy ROC': buy_sell_data['buy_signal_roc'],
                'Buy MACD': buy_sell_data['buy_signal_macd'],  # Include MACD buy signal
                'Buy Swing': buy_sell_data['buy_swing_signal'],
                'Buy Signal': buy_sell_data['buy_signal'],
                'Sell Ratio': buy_sell_data['sell_sig_ratio'],
                'Sell Touch': buy_sell_data['sell_sig_touch'],
                'M-Top Signal': buy_sell_data['m_top_signal'],
                'Sell RSI': buy_sell_data['sell_signal_rsi'],
                'Sell ROC': buy_sell_data['sell_signal_roc'],
                'Sell MACD': buy_sell_data['sell_signal_macd'],  # Include MACD sell signal
                'Sell Swing': buy_sell_data['sell_swing_signal'],
                'Sell Signal': buy_sell_data['sell_signal']
            }
        action = buy_sell_data['action']
        sell_cond = buy_sell_data['sell_signal']
        band_ratio = buy_sell_data['band_ratio']

        return {'action': action, 'band_ratio': band_ratio, 'trigger': trigger, 'updates': updates, 'sell_cond': sell_cond}

    @staticmethod
    def format_row_result(symbol, action_data, bollinger_df, order_info=None):
        """PART III: Order cancellation and Data Collection"""
        """
        Formats the result of processing a row with trading data.

        :param symbol: The symbol for which the row was processed.
        :param action_data: The action data resulting from decision-making (buy/sell/nothing).
        :param bollinger_df: The DataFrame containing Bollinger Bands and related indicators.
        :param order_info: Optional. Information about any orders that were executed as part of processing this row.
        :return: A dictionary with formatted results including symbol, action, key indicators, and order information.
        """
        # Ensure action_data is not empty
        if not action_data:
            action_data = {}

        # Prepare result dictionary with basic info
        result = {
            'symbol': symbol,
            'action': action_data.get('action', None),
            'band_ratio': action_data.get('band_ratio', None),
            'price': action_data.get('price', None),
            'action_data': action_data,
            'roc': None,
            'rsi': None,
            'macd': None,
            'signal_line': None,
            'macd_histogram': None,
            'swing_trend': None,
            'order_info': order_info  # Include order_info in the result if available
        }

        # If Bollinger DataFrame is not empty, extract the latest values of key indicators
        if bollinger_df is not None and not bollinger_df.empty:
            latest_row = bollinger_df.iloc[-1]
            result.update({
                'roc': latest_row.get('ROC', None),
                'rsi': latest_row.get('RSI', None),
                'macd': latest_row.get('MACD', None),
                'signal_line': latest_row.get('Signal_Line', None),
                'macd_histogram': latest_row.get('MACD_Histogram', None),
                'swing_trend': latest_row.get('Buy Swing', None)
                # Assuming 'Buy Swing' is an indicator column in your DataFrame
            })

        return result

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
            'w_bottom_signal': False,
            'm_top_signal': False,
            'buy_signal_rsi': False,
            'sell_signal_rsi': False,
            'buy_signal_roc': False,
            'sell_signal_roc': False,
            'buy_signal_macd': False,
            'sell_signal_macd': False,
            'buy_swing_signal': False,
            'sell_swing_signal': False
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
            buy_sell_data['w_bottom_signal'], buy_sell_data['m_top_signal'] = (
                self.indicators.algorithmic_trading_strategy(bollinger_df))

            # RSI-based signals
            buy_sell_data['buy_sig_rsi'] = df['RSI'].iloc[-1] < 30  # RSI less than 30 indicates oversold
            buy_sell_data['sell_sig_rsi'] = df['RSI'].iloc[-1] > 70  # RSI greater than 70 indicates overbought

            # ROC-based signals
            buy_sell_data['buy_signal_roc'] = df['ROC'].iloc[-1] > 1  # ROC buy condition
            buy_sell_data['sell_signal_roc'] = df['ROC'].iloc[-1] < -1  # ROC sell condition

            # MACD-based signals
            # Check if the MACD line has crossed above the Signal Line for a buy signal
            buy_sell_data['buy_signal_macd'] = (df['MACD'].iloc[-2] < df['Signal_Line'].iloc[-2] and
                                                df['MACD'].iloc[-1] > df['Signal_Line'].iloc[-1])

            # Check if the MACD line has crossed below the Signal Line for a sell signal
            buy_sell_data['sell_signal_macd'] = (df['MACD'].iloc[-2] > df['Signal_Line'].iloc[-2] and
                                                 df['MACD'].iloc[-1] < df['Signal_Line'].iloc[-1])
            # swing trade signals
            buy_sell_data['buy_swing_signal'] = df['Buy Swing'].iloc[-1]
            buy_sell_data['sell_swing_signal'] = df['Sell Swing'].iloc[-1]

            # ROC-based signals with precedence
            if df['ROC'].iloc[-1] > 1:  # ROC buy condition
                buy_sell_data['buy_signal_roc'] = True
                buy_sell_data['buy_signal'] = 'bro'  # Set ROC as the primary trigger
            elif df['ROC'].iloc[-1] < -1:  # ROC sell condition
                buy_sell_data['sell_signal_roc'] = True
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
                if buy_sell_data['w_bottom_signal']:
                    buy_sell_data['buy_signal'] = add_signal(buy_sell_data['buy_signal'], 'wbs')
                if buy_sell_data['buy_signal_rsi']:
                    buy_sell_data['buy_signal'] = add_signal(buy_sell_data['buy_signal'], 'brs')
                if buy_sell_data['buy_signal_macd']:
                    buy_sell_data['buy_signal'] = add_signal(buy_sell_data['buy_signal'], 'bmc')
                if buy_sell_data['buy_swing_signal']:  # Assuming this is a boolean
                    buy_sell_data['buy_signal'] = add_signal(buy_sell_data['buy_signal'], 'bss')

            if not buy_sell_data['sell_signal']:
                # Similar logic for adding sell signals...
                if buy_sell_data['sell_sig_ratio']:
                    buy_sell_data['sell_signal'] = add_signal(buy_sell_data['sell_signal'], 'ssr')
                if buy_sell_data['sell_sig_touch']:
                    buy_sell_data['sell_signal'] = add_signal(buy_sell_data['sell_signal'], 'sst')
                if buy_sell_data['m_top_signal']:
                    buy_sell_data['sell_signal'] = add_signal(buy_sell_data['sell_signal'], 'mts')
                if buy_sell_data['sell_signal_rsi']:
                    buy_sell_data['sell_signal'] = add_signal(buy_sell_data['sell_signal'], 'srs')
                if buy_sell_data['sell_signal_macd']:
                    buy_sell_data['sell_signal'] = add_signal(buy_sell_data['sell_signal'], 'smc')
                if buy_sell_data['sell_swing_signal']:
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
            if any(item['Currency'] == coin for item in holdings):
                sell_action = 'close_at_limit'
                sell_pair = symbol
                sell_limit = price
                sell_order = 'limit'
                self.log_manager.sighook_logger.sell(f'Sell signal created for {symbol}, order triggered by {trigger}.')
                return sell_action, sell_pair, sell_limit, sell_order

            return None, None, None, None
        except Exception as e:
            self.log_manager.sighook_logger.error(f'Error in handle_action(): {e}\nTraceback:,', exc_info=True)
            return None, None, None, None
