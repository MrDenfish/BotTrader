
"""focus solely on decision-making based on data provided by MarketManager"""

from decimal import Decimal, ROUND_HALF_UP

import pandas as pd
from bollinger import BollingerBands


class TradingStrategy:
    def __init__(self, webhook, utility, coms, logmanager, ccxt_api):
        self.coms = coms
        self.ccxt_exceptions = ccxt_api
        self.log_manager = logmanager
        self.utility = utility
        self.bollinger = BollingerBands()
        self.webhook = webhook
        self.results = None
        self.ticker_cache = None
        self.start_time = None
        self.current_holdings = None

    def set_trade_parameters(self, start_time, ticker_cache, hist_holdings):
        self.start_time = start_time
        self.ticker_cache = ticker_cache
        self.current_holdings = hist_holdings

    def update_results(self, symbol, action, price, band_ratio):
        """ Update the results DataFrame with the new entry """
        new_entry = {'symbol': symbol, 'action': action, 'price': price, 'band_ratio': band_ratio}
        self.results = self.results.concat(new_entry, ignore_index=True)
        return self.results

    def process_row(self, row, exchange, old_portfolio, high_total_vol):
        symbol = f"{row['symbol'].replace('-', '/')}"
        price = None  # Initialize with default values
        bollinger_df = None  # Initialize with default value
        updates = {}  # Initialize a dictionary to store updates
        action_data = None  # Initialize action_data
        action = None  # Initialize action
        band_ratio = None  # Initialize band_ratio

        try:
            price_str = row['info']['price']
            price = float(price_str) if price_str else 0.0
            ohlcv = self.ccxt_exceptions.ccxt_api_call(lambda: exchange.fetch_ohlcv(symbol, timeframe='1m'))
            df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'volume'])
            bollinger_df = self.bollinger.calculate_bollinger_bands(df)
            rsi = self.bollinger.calculate_rsi(df)  # Calculate RSI

            if not (bollinger_df is None or bollinger_df.iloc[-1][
                ['basis', 'upper', 'lower', 'band_ratio']].isna().any() or bollinger_df.empty):
                (action, buy_signal, sell_signal, band_ratio, buy_sig_touch, sell_sig_touch, buy_sig_ratio,
                 sell_sig_ratio, w_bottom_signal, m_top_signal, buy_signal_rsi, sell_signal_rsi) = self.buy_sell(
                    bollinger_df, rsi, symbol)

                coin = symbol.split('/')[0]
                if coin in high_total_vol['coin'].values:
                    updates[coin] = {
                        'Buy Ratio': buy_sig_ratio,
                        'Buy Touch': buy_sig_touch,
                        'W-Bottom Signal': w_bottom_signal,
                        'Buy RSI': buy_signal_rsi,
                        'Buy Signal': buy_signal,
                        'Sell Ratio': sell_sig_ratio,
                        'Sell Touch': sell_sig_touch,
                        'M-Top Signal': m_top_signal,
                        'Sell RSI': sell_signal_rsi,
                        'Sell Signal': sell_signal
                    }

                    if action:  # buy or sell signal generated and high volume
                        action_data = self.handle_action(symbol, action, price, band_ratio, sell_signal, old_portfolio)
                elif action == 'sell':  # sell signal generated and low volume
                    action_data = self.handle_action(symbol, action, price, band_ratio, sell_signal, old_portfolio)

        except Exception as e:
            self.log_manager.sighook_logger.error(f'Error in process_row(): {e}')
            # Handle specific exceptions as necessary

        # Single return statement
        return {
            'symbol': symbol,
            'action': action,
            'band_ratio': band_ratio,
            'price': price,
            'action_data': action_data,
            'bollinger_df': bollinger_df,
            'updates': updates
        }

    def buy_sell(self, bollinger_df, rsi, symbol):
        """Determine buy or sell signal based on Bollinger band ratio."""
        action = None


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

            if buy_signal_ratio or buy_signal_touch or w_bottom_signal or buy_signal_rsi:
                buy_signals = [buy_signal_ratio, buy_signal_touch, w_bottom_signal, buy_signal_rsi]
                buy_signal = buy_signals.count(True) >= 3
            else:
                buy_signal = False
            if sell_signal_ratio or sell_signal_touch or m_top_signal or sell_signal_rsi:
                sell_signals = [sell_signal_ratio, sell_signal_touch, m_top_signal, sell_signal_rsi]
                sell_signal = sell_signals.count(True) >= 3
            else:
                sell_signal = False
            # if buy_signal:
            #     self.log_manager.sighook_logger.info(f'{symbol} buy signal generated @ {last_row["close"]}')
            # elif sell_signal:
            #     self.log_manager.sighook_logger.info(f'{symbol} sell signal generated @ {last_row["close"]}')
            action = 'buy' if buy_signal else 'sell' if sell_signal else None

            return (action, buy_signal, sell_signal, last_row['band_ratio'], buy_signal_touch,sell_signal_touch,
                    buy_signal_ratio, sell_signal_ratio, w_bottom_signal, m_top_signal, buy_signal_rsi, sell_signal_rsi)
        except Exception as e:
            self.log_manager.sighook_logger.error(f'Error in buy_sell(): {e}')
            return action, False, False, None, False, False, False, False, False, False, False, False

    def handle_action(self, symbol, action, price, band_ratio, sell_cond, old_portfolio):
        # Separate logic for handling buy and sell actions
        try:
            if action == 'buy':
                coin_balance, usd_balance = self.utility.get_balance(symbol)
                usd_balance = usd_balance.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                if usd_balance > 100:  # min funds to buy
                    # Prepare buy action data
                    buy_action = 'open_at_limit'
                    buy_pair = symbol
                    buy_limit = price
                    buy_order = 'limit'
                    self.webhook.send_webhook(buy_action, buy_pair, buy_limit, buy_order)  # send webhook
                    self.log_manager.sighook_logger.warning(f'{symbol} buy signal triggered @ {buy_action} price'
                                                            f' {buy_limit}, USD balance: ${usd_balance}')
                    return {'buy_action': buy_action, 'buy_pair': buy_pair, 'buy_limit': buy_limit, 'curr_band_ratio':
                            band_ratio, 'sell_action': None, 'sell_symbol': None, 'sell_limit': None, 'sell_cond': None}
                else:
                    self.log_manager.sighook_logger.warning(f'Insufficient funds ${usd_balance} to buy {symbol}')
                    return None
            elif action == 'sell':
                # Prepare sell action data
                sell_action, sell_symbol, sell_limit, sell_order = self.sell_signal(symbol, price, sell_cond, old_portfolio)
                if sell_action:
                    self.webhook.send_webhook(sell_action, sell_symbol, sell_limit, sell_order)
                    self.log_manager.sighook_logger.warning(f'{symbol} sell signal triggered @ {sell_action} price'
                                                            f' {sell_limit}')
                    return {'buy_action': None, 'buy_pair': None, 'buy_limit': None, 'curr_band_ratio': None,
                            'sell_action': sell_action, 'sell_symbol': sell_symbol, 'sell_limit': sell_limit,
                            'sell_cond': sell_cond}
        except Exception as e:
            self.log_manager.sighook_logger.error(f'Error in handle_action(): {e}')
        return None

    @staticmethod
    def sell_signal(symbol, price, sell_cond, old_portfolio):
        coin = symbol.split('/')[0]
        if sell_cond and any(item['Currency'] == coin for item in old_portfolio):  # sell
            sell_action = 'close_at_limit'
            sell_pair = symbol
            sell_limit = price
            sell_order = 'limit'
            print(f'sell signal received for {symbol} order created')
        else:
            print(f'sell signal generated for {symbol}. Order was not created. Coin not in portfolio.')
            return None, None, None, None

        return sell_action, sell_pair, sell_limit, sell_order


