
"""focus solely on decision-making based on data provided by MarketManager"""

from decimal import Decimal, ROUND_HALF_UP
import datetime
import pandas as pd
from indicators import Indicators
import traceback
from ccxt.base.errors import RequestTimeout
import asyncio


class TradingStrategy:
    def __init__(self, webhook, tickermanager, utility, coms, exchange, logmanager, ccxt_api, metrics, alerts, config,
                 max_concurrent_tasks=10):
        self._version = config.program_version
        self.exchange = exchange
        self.alerts = alerts
        self.coms = coms
        self.ccxt_exceptions = ccxt_api
        self.log_manager = logmanager
        self.utility = utility
        self.ticker_manager = tickermanager
        self.indicators = Indicators()
        self.market_metrics = metrics
        self.webhook = webhook
        self.results = None
        self.session = None
        self.ticker_cache = None
        self.market_cache = None
        self.start_time = None
        self.holdings = None
        self.semaphore = asyncio.Semaphore(max_concurrent_tasks)

    def set_trade_parameters(self, start_time, session, ticker_cache, market_cache, hist_holdings):
        self.start_time = start_time
        self.session = session
        self.ticker_cache = ticker_cache
        self.market_cache = market_cache
        self.holdings = hist_holdings

    @property
    def version(self):
        return self._version

    async def process_row_async(self, row, old_portfolio, buy_sell_matrix, counter):
        async with self.semaphore:  # Acquire a semaphore slot
            retries = 3  # make max of three attempts to place order
            backoff_factor = 0.3
            rate_limit_wait = 1  # seconds
            symbol = f"{row['symbol'].replace('-', '/')}"
            if symbol == 'USD/USD':
                pass
            # base_deci, quote_deci = self.utility.fetch_precision(ticker)
            price = None  # Initialize with default values
            bollinger_df = None  # Initialize with default value
            updates = {}  # Initialize a dictionary to store updates
            ohlcv = []
            action_data = None  # Initialize action_data
            action = None  # Initialize action
            band_ratio = None  # Initialize band_ratio
            # for attempt in range(retries):
            try:
                if not symbol == 'USD/USD':
                    price_str = row['info']['price']

                    price = float(price_str) if price_str else 0.0

                    ohlcv = await self.ccxt_exceptions.ccxt_api_call(self.exchange.fetch_ohlcv, symbol, '1m')  # fetch ohlcv
                else:
                    return
                # data
                df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'volume'])
                bollinger_df = self.indicators.calculate_bollinger_bands(df)
                df = self.indicators.calculate_trends(df)  # Calculate 50, 200 volitility and sma trends
                df = self.indicators.calculate_rsi(df)  # Calculate RSI
                df = self.indicators.calculate_roc(df)  # Calculate ROC
                df = self.indicators.calculate_macd(df)  # Calculate MACD
                df = self.indicators.swing_trading_signals(df)
                if self.is_valid_bollinger_df(bollinger_df):
                    buy_sell_data = self.buy_sell(bollinger_df, df, symbol)  # get buy sell data

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
                    band_ratio = buy_sell_data['band_ratio']

                    # Check for buy or sell actions and handle accordingly
                    if action == 'buy' or action == 'sell':
                        action_data = await self.handle_action(symbol, action, price, band_ratio,
                                                               buy_sell_data['sell_signal'], old_portfolio)
            except RequestTimeout as timeout_error:
                self.log_manager.sighook_logger.error(f'Request timeout error for {symbol}: {timeout_error}')
                await asyncio.sleep(backoff_factor * (2 ** 1))

            except Exception as xcept:
                self.log_manager.sighook_logger.error(f'Error in process_row(): {symbol}: {str(xcept)}')  # debug statement

        # Single return statement
        counter['processed'] += 1
        return {
            'symbol': symbol,
            'action': action,
            'band_ratio': band_ratio,
            'price': price,
            'action_data': action_data,
            'bollinger_df': bollinger_df,
            'roc': df['ROC'].iloc[-1],
            'rsi': df['RSI'].iloc[-1],
            'macd': df['MACD'].iloc[-1],
            'signal_line': df['Signal_Line'].iloc[-1],  # Added Signal Line to return data
            'macd_histogram': df['MACD_Histogram'].iloc[-1],  # Added MACD Histogram to return data
            'swing_trend': df['Buy Swing'].iloc[-1],  # Added Swing Trend to return data
            'updates': updates
        }

    def buy_sell(self, bollinger_df, df, symbol):
        """Determine buy or sell signal based on Bollinger band data, rsi and roc macd values. Values of the matrix are
                of boolen type. If 3 or more conditions are true, then the signal is true. If 3 or more conditions are false,
                the signal is False. ROC values can override the 3 condition rule if the rate of change is significant.
                br - Buy Ratio
                bt - Buy Touch
                wb - W-Bottom Signal
                rs - RSI Signal
                ro - ROC Signal
                mc - MACD Signal
                sw - Swing Signal"""

        action = None
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
            buy_sell_data['buy_signal_touch'] = last_row['close'] < last_row['lower']
            # Sell Signal: Price touching or above the upper Bollinger Band
            buy_sell_data['sell_signal_touch'] = last_row['close'] > last_row['upper']

            # bottom buy, top sell
            buy_sell_data['w_bottom_signal'], buy_sell_data['m_top_signal'] = (
                self.indicators.algorithmic_trading_strategy(bollinger_df))

            # RSI-based signals
            buy_sell_data['buy_signal_rsi'] = df['RSI'].iloc[-1] < 30  # RSI less than 30 indicates oversold
            buy_sell_data['sell_signal_rsi'] = df['RSI'].iloc[-1] > 70  # RSI greater than 70 indicates overbought

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

            # Check each condition and append its code to buy_triggers or sell_triggers
            if buy_sell_data['buy_sig_ratio']:
                buy_sell_data['buy_signal'] += 'br'
            if buy_sell_data['buy_signal_touch']:
                buy_sell_data['buy_signal'] += '-bt'
            if buy_sell_data['w_bottom_signal']:
                buy_sell_data['buy_signal'] += '-wb'
            if buy_sell_data['buy_signal_rsi']:
                buy_sell_data['buy_signal'] += '-rs'
            if buy_sell_data['buy_signal_roc']:
                buy_sell_data['buy_signal'] = 'ro'  # ROC can be a standalone trigger
            if buy_sell_data['buy_signal_macd']:
                buy_sell_data['buy_signal'] += '-mc'
            if buy_sell_data['buy_swing_signal']:  # Assuming this is a boolean
                buy_sell_data['buy_signal'] += '-sw'

            # Similar checks for sell triggers...
            if buy_sell_data['sell_sig_ratio']:
                buy_sell_data['sell_signal'] += 'br'
            if buy_sell_data['sell_signal_touch']:
                buy_sell_data['sell_signal'] += '-bt'
            if buy_sell_data['m_top_signal']:
                buy_sell_data['sell_signal'] += '-wb'
            if buy_sell_data['sell_signal_rsi']:
                buy_sell_data['sell_signal'] += '-rs'
            if buy_sell_data['sell_signal_roc']:
                buy_sell_data['sell_signal'] = 'ro'  # ROC can be a standalone trigger
            if buy_sell_data['sell_signal_macd']:
                buy_sell_data['sell_signal'] += '-mc'
            if buy_sell_data['sell_swing_signal']:
                buy_sell_data['sell_signal'] += '-sw'

            # Determine the final action based on the triggers
            buy_conditions_met = len(buy_sell_data['buy_signal'].split('-'))  # Count triggers
            sell_conditions_met = len(buy_sell_data['sell_signal'].split('-'))

            if buy_conditions_met >= 3 or 'ro' in buy_sell_data['buy_signal']:
                buy_sell_data['action'] = 'buy'
            elif sell_conditions_met >= 3 or 'ro' in buy_sell_data['sell_signal']:
                buy_sell_data['action'] = 'sell'

            return buy_sell_data
        except Exception as e:
            self.log_manager.sighook_logger.error(f'Error in buy_sell(): {e}')
            return buy_sell_data

    async def handle_action(self, symbol, action, price, band_ratio, sell_cond, old_portfolio):
        # Separate logic for handling buy and sell actions
        try:
            if action == 'buy':
                coin_balance, usd_balance = self.ticker_manager.get_ticker_balance(symbol)
                usd_balance = usd_balance.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                coin_balance_value = coin_balance * Decimal(price)
                if usd_balance > 100 and coin_balance_value < 10.00:  # min funds to buy and max balance value to buy
                    # Prepare buy action data
                    buy_action = 'open_at_limit'
                    buy_pair = symbol
                    buy_limit = price
                    buy_order = 'limit'
                    await self.webhook.send_webhook(buy_action, buy_pair, buy_limit, buy_order)  # send webhook
                    self.log_manager.sighook_logger.warning(f'{symbol} buy signal triggered @ {buy_action} price'
                                                            f' {buy_limit}, USD balance: ${usd_balance}')
                    return {'buy_action': buy_action, 'buy_pair': buy_pair, 'buy_limit': buy_limit, 'curr_band_ratio':
                            band_ratio, 'sell_action': None, 'sell_symbol': None, 'sell_limit': None, 'sell_cond': None}
                else:
                    self.log_manager.sighook_logger.warning(f'Insufficient funds ${usd_balance} to buy {symbol}')
                    return None
            elif action == 'sell':
                # Prepare sell action data
                sell_action, sell_symbol, sell_limit, sell_order, trigger = (
                    self.sell_signal(symbol, price, sell_cond, old_portfolio, trigger='buysell Matrix'))
                if sell_action:
                    await self.webhook.send_webhook(sell_action, sell_symbol, sell_limit, sell_order)
                    self.log_manager.sighook_logger.warning(f'{symbol} sell signal triggered @ {sell_action} price'
                                                            f' {sell_limit}')
                    return {'buy_action': None, 'buy_pair': None, 'buy_limit': None, 'curr_band_ratio': None,
                            'sell_action': sell_action, 'sell_symbol': sell_symbol, 'sell_limit': sell_limit,
                            'sell_cond': sell_cond}
        except Exception as e:
            tb_str = traceback.format_exc()  # get complete traceback as a string
            self.log_manager.sighook_logger.error(f'Error in handle_action(): {e}\nTraceback: {tb_str}')
        return None

    @staticmethod
    def sell_signal(symbol, price, sell_cond, old_portfolio, trigger):
        coin = symbol.split('/')[0]
        if sell_cond and any(item['Currency'] == coin for item in old_portfolio):  # sell
            sell_action = 'close_at_limit'
            sell_pair = symbol
            sell_limit = price
            sell_order = 'limit'
            print(f'sell signal received for {symbol} order created by {trigger}')
        else:
            return None, None, None, None, None

        return sell_action, sell_pair, sell_limit, sell_order, trigger

    @staticmethod
    def is_valid_bollinger_df(bollinger_df):
        return not (bollinger_df is None or
                    bollinger_df.iloc[-1][['basis', 'upper', 'lower', 'band_ratio']].isna().any() or
                    bollinger_df.empty)

    def update_results(self, symbol, action, price, band_ratio):
        """ Update the results DataFrame with the new entry """
        new_entry = {'symbol': symbol, 'action': action, 'price': price, 'band_ratio': band_ratio}
        self.results = self.results.concat(new_entry, ignore_index=True)
        return self.results
