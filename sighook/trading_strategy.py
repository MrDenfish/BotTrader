
"""focus solely on decision-making based on data provided by MarketManager"""

from decimal import Decimal, ROUND_HALF_UP

import pandas as pd
from indicators import BollingerBands
import traceback
from ccxt.base.errors import RequestTimeout
import asyncio


class TradingStrategy:
    def __init__(self, webhook, tickermanager, utility, coms, exchange, logmanager, ccxt_api, metrics):
        self.exchange = exchange
        self.coms = coms
        self.ccxt_exceptions = ccxt_api
        self.log_manager = logmanager
        self.utility = utility
        self.ticker_manager = tickermanager
        self.bollinger = BollingerBands()
        self.market_metrics = metrics
        self.webhook = webhook
        self.results = None
        self.ticker_cache = None
        self.market_cache = None
        self.start_time = None
        self.current_holdings = None

    def set_trade_parameters(self, start_time, ticker_cache, market_cache, hist_holdings):
        self.start_time = start_time
        self.ticker_cache = ticker_cache
        self.market_cache = market_cache
        self.current_holdings = hist_holdings

    async def process_row_async(self, row, old_portfolio, buy_sell_matrix, counter):

        retries = 3  # make max of three attempts to place order
        backoff_factor = 0.3
        rate_limit_wait = 1  # seconds
        symbol = f"{row['symbol'].replace('-', '/')}"

        price = None  # Initialize with default values
        bollinger_df = None  # Initialize with default value
        updates = {}  # Initialize a dictionary to store updates
        action_data = None  # Initialize action_data
        action = None  # Initialize action
        band_ratio = None  # Initialize band_ratio
        rsi, roc = None, None
        # for attempt in range(retries):
        try:
            price_str = row['info']['price']
            price = float(price_str) if price_str else 0.0
            ohlcv = await self.exchange.fetch_ohlcv(symbol, '1m')  # fetch ohlcv
            # data
            df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'volume'])
            bollinger_df = self.bollinger.calculate_bollinger_bands(df)
            rsi = self.bollinger.calculate_rsi(df)  # Calculate RSI
            roc = self.bollinger.calculate_roc(df)  # Calculate ROC
            df = self.market_metrics.analyze_price_trends(df)

            if self.is_valid_bollinger_df(bollinger_df):
                buy_sell_data = self.buy_sell(bollinger_df, rsi, roc, symbol)  # get buy sell data

                coin = symbol.split('/')[0]
                if coin in buy_sell_matrix['coin'].values:
                    updates[coin] = {
                        'Buy Ratio': buy_sell_data['buy_sig_ratio'],
                        'Buy Touch': buy_sell_data['buy_sig_touch'],
                        'W-Bottom Signal': buy_sell_data['w_bottom_signal'],
                        'Buy RSI': buy_sell_data['buy_signal_rsi'],
                        'Buy ROC': buy_sell_data['buy_signal_roc'],
                        'Sell ROC': buy_sell_data['sell_signal_roc'],
                        'Buy Signal': buy_sell_data['buy_signal'],
                        'Sell Ratio': buy_sell_data['sell_sig_ratio'],
                        'Sell Touch': buy_sell_data['sell_sig_touch'],
                        'M-Top Signal': buy_sell_data['m_top_signal'],
                        'Sell RSI': buy_sell_data['sell_signal_rsi'],
                        'Sell Signal': buy_sell_data['sell_signal']
                    }
                action = buy_sell_data['action']
                band_ratio = buy_sell_data['band_ratio']
                if action:  # buy or sell signal generated and high volume
                    # print(f"process_row: {symbol} at row index {row.name} --DEBUG")  # debug statement
                    action_data = await self.handle_action(symbol, buy_sell_data['action'], price, buy_sell_data[
                        'band_ratio'], buy_sell_data['sell_signal'], old_portfolio)
                elif action == 'sell':  # sell signal generated and low volume
                    # print(f"process_row: {symbol} at row index {row.name} --DEBUG")  # debug statement
                    action_data = await self.handle_action(symbol, buy_sell_data['action'], price, buy_sell_data[
                        'band_ratio'], buy_sell_data['sell_signal'], old_portfolio)
        except RequestTimeout as timeout_error:
            self.log_manager.sighook_logger.error(f'Request timeout error for {symbol}: {timeout_error}')
            await asyncio.sleep(backoff_factor * (2 ** 1))

        except Exception as e:
            self.log_manager.sighook_logger.error(f'Error in process_row() for {symbol}: {e}')
            # Handle specific exceptions as necessary

        # Single return statement
        counter['processed'] += 1
        return {
            'symbol': symbol,
            'action': action,
            'band_ratio': band_ratio,
            'price': price,
            'action_data': action_data,
            'bollinger_df': bollinger_df,
            'roc': roc,
            'rsi': rsi,
            'updates': updates
        }

    def buy_sell(self, bollinger_df, rsi, roc, symbol):
        """Determine buy or sell signal based on Bollinger band data, rsi and roc values. Values of the matrix are
         of boolen type. If 3 or more conditions are true, then the signal is true. If 3 or more conditions are false,
         the signal is False. ROC values can override the 3 condition rule if the rate of change is significant.
         """
        action = None
        buy_sell_data = {
            'action': None,
            'buy_signal': False,
            'sell_signal': False,
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
            'sell_signal_roc': False
        }
        try:
            if len(bollinger_df) < 20 or bollinger_df.iloc[-1][['basis', 'upper', 'lower', 'band_ratio']].isna().any():
                return None, None, None  # Not enough data or NaN values present

            last_row = bollinger_df.iloc[-1]
            prev_row = bollinger_df.iloc[-2]

            buy_signal_ratio = abs(prev_row['band_ratio'] - 1) < 0.05 and abs(last_row['band_ratio'] - 1) > 0.05
            sell_signal_ratio = abs(prev_row['band_ratio'] - 1) > 0.05 and prev_row['basis'] > last_row['basis']

            # Buy Signal: Price touching or below the lower Bollinger Band
            buy_signal_touch = last_row['close'] < last_row['lower']
            # Sell Signal: Price touching or above the upper Bollinger Band
            sell_signal_touch = last_row['close'] > last_row['upper']

            # bottom buy, top sell
            w_bottom_signal, m_top_signal = self. bollinger.algorithmic_trading_strategy(bollinger_df)

            # RSI-based signals
            buy_signal_rsi = rsi.iloc[-1] < 30  # RSI less than 30 indicates oversold
            sell_signal_rsi = rsi.iloc[-1] > 70  # RSI greater than 70 indicates overbought

            # ROC-based signals
            buy_signal_roc = roc.iloc[-1] > 1  # ROC buy condition
            sell_signal_roc = roc.iloc[-1] < -1  # ROC sell condition

            if buy_signal_ratio or buy_signal_touch or w_bottom_signal or buy_signal_rsi or buy_signal_roc:
                buy_signals = [buy_signal_ratio, buy_signal_touch, w_bottom_signal, buy_signal_rsi, buy_signal_roc]
                # need 3 conditions to be true or rate of change is significant
                buy_signal = buy_signals.count(True) >= 3 or buy_signal_roc
            else:
                buy_signal = False
            if sell_signal_ratio or sell_signal_touch or m_top_signal or sell_signal_rsi or sell_signal_roc:
                sell_signals = [sell_signal_ratio, sell_signal_touch, m_top_signal, sell_signal_rsi, sell_signal_roc]
                # need 3 conditions to be true or rate of change is significant
                sell_signal = sell_signals.count(True) >= 3 or sell_signal_roc
            else:
                sell_signal = False
            action = 'buy' if buy_signal else 'sell' if sell_signal else None
            # Update buy_sell_data with the calculated values
            buy_sell_data.update({
                'action': action,
                'buy_signal': buy_signal,
                'sell_signal': sell_signal,
                'band_ratio': last_row['band_ratio'],
                'buy_sig_touch': buy_signal_touch,
                'sell_sig_touch': sell_signal_touch,
                'buy_sig_ratio': buy_signal_ratio,
                'sell_sig_ratio': sell_signal_ratio,
                'w_bottom_signal': w_bottom_signal,
                'm_top_signal': m_top_signal,
                'buy_signal_rsi': buy_signal_rsi,
                'sell_signal_rsi': sell_signal_rsi,
                'buy_signal_roc': buy_signal_roc,
                'sell_signal_roc': sell_signal_roc
            })
            return buy_sell_data
        except Exception as e:
            self.log_manager.sighook_logger.error(f'Error in buy_sell(): {e}')
            return buy_sell_data

    async def handle_action(self, symbol, action, price, band_ratio, sell_cond, old_portfolio):
        # Separate logic for handling buy and sell actions
        try:
            if action == 'buy':
                coin_balance, usd_balance = await self.ticker_manager.get_ticker_balance(symbol)
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
                sell_action, sell_symbol, sell_limit, sell_order = self.sell_signal(symbol, price, sell_cond,
                                                                                    old_portfolio, trigger='buysell Matrix')
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
            # print(f'sell signal generated for {symbol}. Order was not created. Coin not in portfolio.')  # debug statement
            return None, None, None, None

        return sell_action, sell_pair, sell_limit, sell_order

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
