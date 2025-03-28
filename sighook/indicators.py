
from decimal import Decimal

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from Config.config_manager import CentralConfig


class Indicators:
    """PART III: Trading Strategies"""
    """ This class contains the functions to calculate various technical indicators and trading signals."""
    def __init__(self, logmanager):
        self.config = CentralConfig()
        self.log_manager = logmanager
        self.bb_window = int(self.config._bb_window)
        self.bb_std = int(self.config._bb_std)
        self.bb_lower_band = Decimal(self.config._bb_lower_band)
        self.bb_upper_band = Decimal(self.config._bb_upper_band)
        self.atr_window = int(self.config._atr_window)
        self.macd_fast = int(self.config._macd_fast)
        self.macd_slow = int(self.config._macd_slow)
        self.macd_signal = int(self.config._macd_signal)
        self.rsi_window = int(self.config._rsi_window)
        self.roc_window = int(self.config._roc_window)  # # default is 4
        self.roc_buy_24h = int(self.config._roc_buy_24h)  # default is 5
        self.roc_sell_24h = int(self.config._roc_sell_24h)  # default is 2
        self.rsi_buy = int(self.config._rsi_buy)
        self.rsi_sell = int(self.config._rsi_sell)
        self.buy_ratio = Decimal(self.config._buy_ratio)
        self.sell_ratio = Decimal(self.config._sell_ratio)
        self.sma_fast = int(self.config._sma_fast)
        self.sma_slow = int(self.config._sma_slow)
        self.sma = int(self.config._sma)
        self.sma_volatility = int(self.config._sma_volatility)

    def calculate_indicators(self, df, quote_deci, indicators_config=None):
        """Calculate all required indicators for buy/sell decisions with weighted scoring."""

        debug = False  # Toggle this for detailed output
        try:
            if df.empty:
                raise ValueError("Input DataFrame is empty")

            if len(df) < 50:
                self.log_manager.warning(f"Insufficient OHLCV data. Rows fetched: {len(df)}")
                return df

            if indicators_config is None:
                indicators_config = {
                    'bollinger': True, 'trends': True, 'macd': True,
                    'rsi': True, 'roc': True, 'w_bottoms': True, 'swing_trading': True,
                }

            self.strategy_weights = {
                'Buy Ratio': 1.2, 'Buy Touch': 1.5, 'W-Bottom': 2.0, 'Buy RSI': 2.5,
                'Buy ROC': 2.0, 'Buy MACD': 1.8, 'Buy Swing': 2.2,
                'Sell Ratio': 1.2, 'Sell Touch': 1.5, 'M-Top': 2.0, 'Sell RSI': 2.5,
                'Sell ROC': 2.0, 'Sell MACD': 1.8, 'Sell Swing': 2.2
            }

            # Initialize all columns with (0, None, None)
            signal_columns = list(self.strategy_weights.keys()) + ['Buy Signal', 'Sell Signal']
            for signal in signal_columns:
                df[signal] = [(0, None, None)] * len(df)

            if indicators_config.get('bollinger'):
                df['basis'] = df['close'].rolling(window=self.bb_window).mean()
                df['std'] = df['close'].rolling(window=self.bb_window).std()
                df['upper'] = df['basis'] + 2 * df['std']
                df['lower'] = df['basis'] - 2 * df['std']
                df[['upper', 'lower']] = df[['upper', 'lower']].replace(0, np.nan).bfill()
                df['band_ratio'] = (df['upper'] / df['lower']).replace(0, np.nan).bfill()

                df['prev_close'] = df['close'].shift(1)
                df['prev_upper'] = df['upper'].shift(1)
                df['prev_lower'] = df['lower'].shift(1)

                def compute_buy_touch(row):
                    close = round(row['close'], quote_deci)
                    upper = round(row['upper'], quote_deci) if pd.notna(row['upper']) else 0.0
                    if pd.notna(row['prev_close']) and pd.notna(row['prev_upper']):
                        if row['prev_close'] < row['prev_upper'] and row['close'] >= row['upper']:
                            return (1, close, upper)
                    return (0, close, upper)

                def compute_sell_touch(row):
                    close = round(row['close'], quote_deci)
                    lower = round(row['lower'], quote_deci) if pd.notna(row['lower']) else 0.0
                    if pd.notna(row['prev_close']) and pd.notna(row['prev_lower']):
                        if row['prev_close'] >= row['prev_lower'] and row['close'] < row['lower']:
                            return (1, close, lower)
                    return (0, close, lower)

                df['Buy Touch'] = df.apply(compute_buy_touch, axis=1)
                df['Sell Touch'] = df.apply(compute_sell_touch, axis=1)

                df['Buy Ratio'] = df.apply(
                    lambda row: (1, round(row['band_ratio'], quote_deci), self.buy_ratio)
                    if row['band_ratio'] > self.buy_ratio else (0, round(row['band_ratio'], quote_deci), self.buy_ratio),
                    axis=1
                )

                df['Sell Ratio'] = df.apply(
                    lambda row: (1, round(row['band_ratio'], quote_deci), self.sell_ratio)
                    if row['band_ratio'] < self.sell_ratio else (0, round(row['band_ratio'], quote_deci), self.sell_ratio),
                    axis=1
                )

            if indicators_config.get('trends'):
                df['50_sma'] = df['close'].rolling(window=self.sma_fast).mean()
                df['200_sma'] = df['close'].rolling(window=self.sma_slow).mean()
                df['sma'] = df['close'].rolling(window=self.sma).mean()
                df['volatility'] = df['close'].rolling(window=self.sma_volatility).std()

            if indicators_config.get('macd'):
                df['EMA_fast'] = df['close'].ewm(span=self.macd_fast, adjust=False).mean()
                df['EMA_slow'] = df['close'].ewm(span=self.macd_slow, adjust=False).mean()
                df['MACD'] = df['EMA_fast'] - df['EMA_slow']
                df['Signal_Line'] = df['MACD'].ewm(span=self.macd_signal, adjust=False).mean()
                df['MACD_Histogram'] = df['MACD'] - df['Signal_Line']

                df['Buy MACD'] = df['MACD_Histogram'].apply(lambda v: (1, round(v, 4), 0) if v > 0 else (0, round(v, 4), 0))
                df['Sell MACD'] = df['MACD_Histogram'].apply(lambda v: (1, round(v, 4), 0) if v < 0 else (0, round(v, 4), 0))

            # RSI
            delta = df['close'].diff()
            gain = delta.where(delta > 0, 0.0)
            loss = -delta.where(delta < 0, 0.0)
            avg_gain = gain.rolling(window=self.rsi_window, min_periods=self.rsi_window).mean()
            avg_loss = loss.rolling(window=self.rsi_window, min_periods=self.rsi_window).mean()
            rs = avg_gain / avg_loss.replace(0, np.nan)
            df['RSI'] = 100 - (100 / (1 + rs))
            df['RSI'] = df['RSI'].fillna(50).clip(0, 100)

            if debug:
                print("Recent RSI values:")
                print(df[['time', 'close', 'RSI']].tail(10))

            df['Buy RSI'] = df['RSI'].apply(
                lambda r: (1, round(r, 2), 30.0) if r < self.rsi_buy else (0, round(r, 2), 30.0)
            )

            df['Sell RSI'] = df['RSI'].apply(
                lambda r: (1, round(r, 2), 70.0) if r > self.rsi_sell else (0, round(r, 2), 70.0)
            )

            # ✅ Rate of Change (ROC)
            roc_window = getattr(self, 'roc_window', 3)
            self.buy_roc_threshold = getattr(self, 'buy_roc_threshold', self.roc_buy_24h)
            self.sell_roc_threshold = getattr(self, 'sell_roc_threshold', -self.roc_sell_24h)
            # Percentage change over `roc_window` periods
            df['ROC'] = df['close'].pct_change(periods=roc_window) * 100
            df['ROC'] = df['ROC'].fillna(0)

            # ROC change over time
            df['ROC_Diff'] = df['ROC'].diff().fillna(0)

            # Optional debug log (just for reviewing)
            if debug:
                print(f"\nROC debug (window={roc_window}):")
                print(df[['time', 'close', 'ROC', 'ROC_Diff']].tail(10))

            # ✅ Buy ROC signal
            df['Buy ROC'] = df['ROC'].apply(
                lambda r: (1, round(r, 2), self.buy_roc_threshold)
                if r > self.buy_roc_threshold else (0, round(r, 2), self.buy_roc_threshold)
            )

            # ✅ Sell ROC signal
            df['Sell ROC'] = df['ROC'].apply(
                lambda r: (1, round(r, 2), self.sell_roc_threshold)
                if r < self.sell_roc_threshold else (0, round(r, 2), self.sell_roc_threshold)
            )

            # W-Bottom / M-Top
            if indicators_config.get('w_bottoms'):
                df['W-Bottom'], df['M-Top'] = self.identify_w_bottoms_m_tops(df, quote_deci)

            # ✅ Buy/Sell Swing Integration
            if indicators_config.get('swing_trading'):
                volatility_mean = df['volatility'].mean()
                swing_window = 30

                df['rolling_high'] = df['close'].rolling(window=swing_window).max()
                df['rolling_low'] = df['close'].rolling(window=swing_window).min()

                def buy_swing_logic(row):
                    if (
                            row['close'] > row['50_sma'] and
                            row['RSI'] >= self.rsi_buy and row['RSI'] <= self.rsi_sell and
                            row['MACD'] > row['Signal_Line'] and
                            row['close'] > row['200_sma'] and
                            row['volatility'] > 0.8 * volatility_mean and
                            row['close'] >= row['rolling_high']
                    ):
                        if debug:
                            print(f"Buy Swing ✅ at {row.name}: close={row['close']}, high={row['rolling_high']}")
                        return (1, round(row['close'], quote_deci), None)
                    return (0, round(row['close'], quote_deci), None)

                def sell_swing_logic(row):
                    if (
                            row['close'] < row['50_sma'] and
                            row['RSI'] >= self.rsi_buy and row['RSI'] <= self.rsi_sell and
                            row['MACD'] < row['Signal_Line'] and
                            row['close'] < row['200_sma'] and
                            row['volatility'] < 1.2 * volatility_mean and
                            row['close'] <= row['rolling_low']
                    ):
                        if debug:
                            print(f"Sell Swing ✅ at {row.name}: close={row['close']}, low={row['rolling_low']}")
                        return (1, round(row['close'], quote_deci), None)
                    return (0, round(row['close'], quote_deci), None)

                df['Buy Swing'] = df.apply(buy_swing_logic, axis=1)
                df['Sell Swing'] = df.apply(sell_swing_logic, axis=1)

            return df

        except Exception as e:
            self.log_manager.error(f"Error in calculate_indicators(): {e}", exc_info=True)
            return None

    def swing_trading_signals(self, df, quote_deci):
        """
        Detect Buy Swing and Sell Swing signals based on price trend, momentum, and volatility.
        Returns the modified DataFrame with Buy Swing and Sell Swing columns updated.
        """
        try:
            # ✅ Use existing volatility column or compute if missing
            if 'volatility' not in df.columns or df['volatility'].isna().all():
                df['volatility'] = df['close'].rolling(window=self.sma_volatility).std()

            volatility_mean = df['volatility'].mean()
            atr_threshold = df['volatility'].median() * 0.03  # Dynamic threshold

            # ✅ Add Buy Swing Signal
            df['Buy Swing'] = df.apply(
                lambda row: (
                    1,
                    round(row['close'], quote_deci),
                    round(volatility_mean * 0.8, quote_deci)
                ) if (
                        row['close'] > row['50_sma'] and
                        row['RSI'] > 50 and
                        row['MACD'] > row['Signal_Line'] and
                        row['volatility'] > volatility_mean * 0.8
                ) else (
                    0,
                    round(row['close'], quote_deci),
                    round(volatility_mean * 0.8, quote_deci)
                ),
                axis=1
            )

            # ✅ Add Sell Swing Signal
            df['Sell Swing'] = df.apply(
                lambda row: (
                    1,
                    round(row['close'], quote_deci),
                    round(volatility_mean * 1.2, quote_deci)
                ) if (
                        row['close'] < row['50_sma'] and
                        row['RSI'] < 50 and
                        row['MACD'] < row['Signal_Line'] and
                        row['volatility'] < volatility_mean * 1.2
                ) else (
                    0,
                    round(row['close'], quote_deci),
                    round(volatility_mean * 1.2, quote_deci)
                ),
                axis=1
            )

            return df

        except Exception as e:
            self.log_manager.error(f"Error in swing_trading_signals(): {e}", exc_info=True)
            return df

    def identify_w_bottoms_m_tops(self, df, quote_deci):
        """
        Identify W-Bottom and M-Top patterns using dynamically determined parameters.
        """
        #  debug flags
        debug = False

        try:
            # ✅ Initialize W-Bottom & M-Top lists
            w_bottoms = [(0, 0.0, 0.0)] * len(df)  # Changed from None → 0.0
            m_tops = [(0, 0.0, 0.0)] * len(df)  # Changed from None → 0.0

            # ✅ Dynamic `min_time_between_signals`
            min_time_between_signals = max(3, int(len(df) * 0.005))  # 0.5% of dataset size

            # ✅ Dynamic `min_price_change` using ATR
            df['atr'] = df['high'].rolling(self.atr_window).max() - df['low'].rolling(self.atr_window).min()
            df['atr'] = df['atr'].bfill().fillna(0.0)  # Ensure no NaN values
            min_price_change = df['atr'].median() * 0.065  # 6.5% of median ATR

            # ✅ Dynamic rolling window for volume confirmation
            volatility = df['close'].pct_change().rolling(self.atr_window + 7).std()
            rolling_window = int(10 + (volatility.mean() * 100))
            rolling_window = max(5, min(self.atr_window + 7, rolling_window))  # Keep reasonable range
            df['volume_mean'] = df['volume'].rolling(rolling_window, min_periods=1).mean().fillna(0.0)

            last_w_bottom, last_m_top = None, None

            for i in range(1, len(df) - 1):
                prev, curr, next_row = df.iloc[i - 1], df.iloc[i], df.iloc[i + 1]

                # ✅ W-Bottom Detection
                if (
                        prev['low'] < prev['lower'] and
                        curr['lower'] < curr['low'] < next_row['low'] and
                        next_row['close'] > next_row['basis'] and
                        # next_row['volume'] > next_row['volume_mean'] #default
                        next_row['volume'] > (1.025 * next_row['volume_mean'])  # debug
                ):

                    if debug:
                        print(f"W-Bottom candidate at {df.index[i]}:")
                        print(f"  prev.low={prev['low']}, prev.lower={prev['lower']}")
                        print(f"  curr.low={curr['low']}, curr.lower={curr['lower']}")
                        print(f"  next.close={next_row['close']}, next.basis={next_row['basis']}")
                        print(f"  next.volume={next_row['volume']}, mean={next_row['volume_mean']}")

                    if last_w_bottom is None or (i - last_w_bottom) >= min_time_between_signals:
                        if last_w_bottom is None or abs(curr['low'] - df.iloc[last_w_bottom]['low']) / df.iloc[last_w_bottom][
                            'low'] > min_price_change:
                            df.at[df.index[i], 'W-Bottom'] = (
                                1,
                                round(curr['low'], quote_deci),
                                round(min_price_change, quote_deci)
                            )
                            last_w_bottom = i

                # ✅ M-Top Detection
                if (
                        prev['high'] > prev['upper'] and
                        curr['upper'] > curr['high'] > next_row['high'] and
                        next_row['close'] < next_row['basis'] and
                        next_row['volume'] > next_row['volume_mean']
                ):
                    if debug:
                        print(f"M-Top candidate at {df.index[i]}:")
                        print(f"  prev.high={prev['high']}, prev.upper={prev['upper']}")
                        print(f"  curr.high={curr['high']}, curr.upper={curr['upper']}")
                        print(f"  next.close={next_row['close']}, next.basis={next_row['basis']}")
                        print(f"  next.volume={next_row['volume']}, mean={next_row['volume_mean']}")

                    if last_m_top is None or (i - last_m_top) >= min_time_between_signals:
                        if last_m_top is None or abs(curr['high'] - df.iloc[last_m_top]['high']) / df.iloc[last_m_top][
                            'high'] > min_price_change:
                            df.at[df.index[i], 'M-Top'] = (
                                1,
                                round(curr['high'], quote_deci),
                                round(min_price_change, quote_deci)
                            )
                            last_m_top = i

            print("Valid Bollinger Bands entries:", df[['upper', 'lower', 'basis']].dropna().shape[0])

            return df['W-Bottom'].tolist(), df['M-Top'].tolist()


        except Exception as e:
            self.log_manager.error(f"Error in identify_w_bottoms_m_tops(): {e}", exc_info=True)
            return [(0, 0.0, 0.0)] * len(df), [(0, 0.0, 0.0)] * len(df)  # ✅ Changed from None → 0.0

    def plot_w_bottoms_m_tops(self, df, detected_w_bottoms, detected_m_tops):  # debugging to get a visual of the data
        """
        Plot Bollinger Bands with detected W-Bottoms and M-Tops.
        """
        try:
            plt.figure(figsize=(14, 6))

            # Plot Closing Price
            plt.plot(df.index, df['close'], label='Close Price', color='blue', alpha=0.6)

            # Plot Bollinger Bands
            plt.plot(df.index, df['upper'], linestyle='dashed', color='red', label='Upper Band')
            plt.plot(df.index, df['lower'], linestyle='dashed', color='green', label='Lower Band')
            plt.plot(df.index, df['basis'], linestyle='dashed', color='black', label='Basis (SMA)')

            # Plot W-Bottoms (Green Triangles Up)
            if detected_w_bottoms:
                plt.scatter(df.index[detected_w_bottoms], df['low'][detected_w_bottoms], color='lime', marker='^', s=100,
                            label='W-Bottom')

            # Plot M-Tops (Red Triangles Down)
            if detected_m_tops:
                plt.scatter(df.index[detected_m_tops], df['high'][detected_m_tops], color='red', marker='v', s=100,
                            label='M-Top')

            # Labels & Legends
            plt.title("Bollinger Bands with Detected W-Bottoms and M-Tops")
            plt.xlabel("Time")
            plt.ylabel("Price")
            plt.legend()
            plt.grid()

            plt.show()
        except Exception as e:
            self.log_manager.error(f"Error in plot_w_bottoms_m_tops(): {e}", exc_info=True)










