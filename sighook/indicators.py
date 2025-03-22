from Config.config_manager import CentralConfig
from decimal import Decimal
import numpy as np
import matplotlib.pyplot as plt

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
        try:
            if df.empty:
                raise ValueError("Input DataFrame is empty")

            min_required_rows = 50  # Ensure enough data
            if len(df) < min_required_rows:
                self.log_manager.warning(f"Insufficient OHLCV data. Rows fetched: {len(df)}")
                return df

            # Default indicator configuration
            if indicators_config is None:
                indicators_config = {
                    'bollinger': True,
                    'trends': True,
                    'macd': True,
                    'rsi': True,
                    'roc': True,
                    'w_bottoms': True,
                    'swing_trading': True,
                }

            # ✅ Define strategy weights (Will later be dynamically updated)
            self.strategy_weights = {
                'Buy Ratio': 1.2, 'Buy Touch': 1.5, 'W-Bottom': 2.0, 'Buy RSI': 2.5,
                'Buy ROC': 2.0, 'Buy MACD': 1.8, 'Buy Swing': 2.2,
                'Sell Ratio': 1.2, 'Sell Touch': 1.5, 'M-Top': 2.0, 'Sell RSI': 2.5,
                'Sell ROC': 2.0, 'Sell MACD': 1.8, 'Sell Swing': 2.2
            }

            # ✅ Initialize all signal columns
            signal_columns = list(self.strategy_weights.keys()) + ['Buy Signal', 'Sell Signal']
            for signal in signal_columns:
                df[signal] = [(0, None, None)] * len(df)

            # ✅ Bollinger Bands Calculation (only once)
            if indicators_config.get('bollinger'):
                df['basis'] = df['close'].rolling(window=self.bb_window).mean()
                df['std'] = df['close'].rolling(window=self.bb_window).std()
                df['upper'] = df['basis'] + 2 * df['std']
                df['lower'] = df['basis'] - 2 * df['std']
                df[['upper', 'lower']] = df[['upper', 'lower']].replace(0, np.nan).bfill()
                df['band_ratio'] = df['upper'] / df['lower']
                df['band_ratio'] = df['band_ratio'].replace(0, np.nan).bfill()

                # ✅ Compute `Buy Touch`, `Sell Touch`, `Buy Ratio`, and `Sell Ratio`
                df['prev_close'] = df['close'].shift(1)
                df['prev_upper'] = df['upper'].shift(1)
                df['prev_lower'] = df['lower'].shift(1)

                df['Buy Touch'] = df.apply(
                    lambda row: (1, round(row['close'], quote_deci), round(row['upper'], quote_deci))
                    if row['prev_close'] <= row['prev_upper'] and row['close'] > row['upper'] else
                    (0, round(row['close'], quote_deci), round(row['upper'], quote_deci)), axis=1
                )

                df['Sell Touch'] = df.apply(
                    lambda row: (1, round(row['close'], quote_deci), round(row['lower'], quote_deci))
                    if row['prev_close'] >= row['prev_lower'] and row['close'] < row['lower'] else
                    (0, round(row['close'], quote_deci), round(row['lower'], quote_deci)), axis=1
                )

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

            # ✅ Calculate Trends
            if indicators_config.get('trends'):
                df['50_sma'] = df['close'].rolling(window=self.sma_fast).mean()
                df['200_sma'] = df['close'].rolling(window=self.sma_slow).mean()
                df['sma'] = df['close'].rolling(window=self.sma).mean()
                df['volatility'] = df['close'].rolling(window=self.sma_volatility).std()

            # ✅ Calculate MACD
            if indicators_config.get('macd'):
                df['EMA_fast'] = df['close'].ewm(span=self.macd_fast, min_periods=1, adjust=False).mean()
                df['EMA_slow'] = df['close'].ewm(span=self.macd_slow, min_periods=1, adjust=False).mean()
                df['MACD'] = df['EMA_fast'] - df['EMA_slow']
                df['Signal_Line'] = df['MACD'].ewm(span=self.macd_signal, min_periods=1, adjust=False).mean()
                df['MACD_Histogram'] = df['MACD'] - df['Signal_Line']

                df['Buy MACD'] = df.apply(
                    lambda row: (1, round(row['MACD_Histogram'], 4), 0) if row['MACD_Histogram'] > 0 else
                    (0, round(row['MACD_Histogram'], 4), 0), axis=1
                )

                df['Sell MACD'] = df.apply(
                    lambda row: (1, round(row['MACD_Histogram'], 4), 0) if row['MACD_Histogram'] < 0 else
                    (0, round(row['MACD_Histogram'], 4), 0), axis=1
                )

            # ✅ RSI Calculation
            delta = df['close'].diff()
            gain = delta.where(delta > 0, 0).rolling(window=self.rsi_window, min_periods=1).mean()
            loss = -delta.where(delta < 0, 0).rolling(window=self.rsi_window, min_periods=1).mean()
            rs = gain / loss.replace(0, np.nan)
            df['RSI'] = 100 - (100 / (1 + rs))
            df['RSI'] = df['RSI'].clip(0, 100).fillna(50)

            # ✅ Rate of Change (ROC)
            df['ROC'] = df['close'].pct_change(min(3, len(df))) * 100
            df['ROC_Diff'] = df['ROC'].diff()
            df[['ROC', 'ROC_Diff']] = df[['ROC', 'ROC_Diff']].fillna(0)

            # ✅ Identify W-Bottoms & M-Tops
            df['W-Bottom'], df['M-Top'] = self.identify_w_bottoms_m_tops(df, quote_deci)

            return df

        except Exception as e:
            self.log_manager.error(f"Error in calculate_indicators(): {e}", exc_info=True)
            return None

    # def calculate_bollinger_bands(self, df, quote_deci,length=None, mult=None):
    #     try:
    #         if df.empty:
    #             raise ValueError("Input DataFrame is empty")
    #         length = length or self.bb_window
    #         bb_std = mult or self.bb_std
    #         # Calculate the Bollinger Bands
    #         df['basis'] = df['close'].rolling(window=length).mean()  # Simple moving average
    #         df['std'] = df['close'].rolling(window=length).std()  # Rolling standard deviation
    #         df['upper'] = df['basis'] + df['std'] * bb_std
    #         df['lower'] = df['basis'] - df['std'] * bb_std
    #         df['band_ratio'] = df['upper'] / df['lower']
    #
    #         # Ensure no NaN values before applying conditions
    #         df[['upper', 'lower', 'band_ratio']] = df[['upper', 'lower', 'band_ratio']].fillna(0.0)
    #
    #         # Apply structured tuple format (0/1 (0 does not meet the condition, 1 does meet the condition, computed_value,
    #         # threshold)
    #
    #         df['prev_close'] = df['close'].shift(1)
    #         df['prev_upper'] = df['upper'].shift(1)
    #         df['prev_lower'] = df['lower'].shift(1)
    #
    #         df['Buy Touch'] = df.apply(
    #             lambda row: (
    #                 1, round(row['close'], quote_deci), round(row['upper'], quote_deci))
    #             if row['prev_close'] <= row['prev_upper'] and row['close'] > row['upper'] else
    #             (0, round(row['close'], quote_deci), round(row['upper'], quote_deci)), axis=1
    #         )
    #
    #         df['Sell Touch'] = df.apply(
    #             lambda row: (
    #                 1, round(row['close'], quote_deci), round(row['lower'], quote_deci))
    #             if row['prev_close'] >= row['prev_lower'] and row['close'] < row['lower'] else
    #             (0, round(row['close'], quote_deci), round(row['lower'], quote_deci)), axis=1
    #         )
    #
    #         df['Buy Ratio'] = df.apply(
    #             lambda row: (
    #                 1, round(row['band_ratio'], quote_deci), self.buy_ratio
    #             ) if row['band_ratio'] > self.buy_ratio else (0, round(row['band_ratio'], quote_deci), self.buy_ratio),
    #             axis=1
    #         )
    #
    #         df['Sell Ratio'] = df.apply(
    #             lambda row: (
    #                 1, round(row['band_ratio'], quote_deci), self.sell_ratio
    #             ) if row['band_ratio'] < self.sell_ratio else (0, round(row['band_ratio'], quote_deci), self.sell_ratio),
    #             axis=1
    #         )
    #
    #         # Drop NaN values after all calculations
    #         df.dropna(subset=['basis', 'upper', 'lower', 'band_ratio'])
    #
    #         return df
    #
    #     except ValueError as e:
    #         if "DataFrame is empty" in str(e):
    #             return None
    #     except Exception as e:
    #         self.log_manager.error(f"Error in calculate_bollinger_bands(): {e}", exc_info=True)
    #         return df

    def swing_trading_signals(self, df):
        try:
            # Compute overall volatility mean to prevent AttributeError
            volatility_mean = df['volatility'].mean()

            # Calculate Buy Swing conditions
            df['Buy Swing'] = df.apply(
                lambda row: (1, round(row['close'], 2), None)
                if (row['close'] > row['50_sma'] and
                    30 <= row['RSI'] <= 70 and
                    row['MACD'] > row['Signal_Line'] and
                    row['close'] > row['200_sma'] and
                    row['volatility'] > volatility_mean * 0.8)  # Use precomputed mean
                else (0, round(row['close'], 2), None), axis=1
            )

            # Calculate Sell Swing conditions
            df['Sell Swing'] = df.apply(
                lambda row: (1, round(row['close'], 2), None)
                if (row['close'] < row['50_sma'] and
                    30 <= row['RSI'] <= 70 and
                    row['MACD'] < row['Signal_Line'] and
                    row['close'] < row['200_sma'] and
                    row['volatility'] < volatility_mean * 1.2)  # Use precomputed mean
                else (0, round(row['close'], 2), None), axis=1
            )

            return df

        except Exception as e:
            self.log_manager.error(f"Error in swing_trading_signals(): {e}", exc_info=True)
            return df

    def identify_w_bottoms_m_tops(self, df, quote_deci):
        """
        Identify W-Bottom and M-Top patterns using dynamically determined parameters.
        """
        try:
            # ✅ Initialize W-Bottom & M-Top lists
            w_bottoms = [(0, 0.0, 0.0)] * len(df)  # Changed from None → 0.0
            m_tops = [(0, 0.0, 0.0)] * len(df)  # Changed from None → 0.0

            # ✅ Dynamic `min_time_between_signals`
            min_time_between_signals = max(3, int(len(df) * 0.005))  # 0.5% of dataset size

            # ✅ Dynamic `min_price_change` using ATR
            df['atr'] = df['high'].rolling(self.atr_window).max() - df['low'].rolling(self.atr_window).min()
            df['atr'] = df['atr'].bfill().fillna(0.0)  # Ensure no NaN values
            min_price_change = df['atr'].median() * 0.1  # 10% of median ATR

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
                        next_row['volume'] > next_row['volume_mean']
                ):
                    if last_w_bottom is None or (i - last_w_bottom) >= min_time_between_signals:
                        if last_w_bottom is None or abs(curr['low'] - df.iloc[last_w_bottom]['low']) / df.iloc[last_w_bottom][
                            'low'] > min_price_change:
                            df.at[df.index[i], 'W-Bottom'] = (1, round(curr['low'], quote_deci), round(min_price_change, quote_deci)) # ✅ FIXED
                            last_w_bottom = i

                # ✅ M-Top Detection
                if (
                        prev['high'] > prev['upper'] and
                        curr['upper'] > curr['high'] > next_row['high'] and
                        next_row['close'] < next_row['basis'] and
                        next_row['volume'] > next_row['volume_mean']
                ):
                    if last_m_top is None or (i - last_m_top) >= min_time_between_signals:
                        if last_m_top is None or abs(curr['high'] - df.iloc[last_m_top]['high']) / df.iloc[last_m_top][
                            'high'] > min_price_change:
                            df.at[df.index[i], 'M-Top'] = (1, round(curr['high'], quote_deci), round(min_price_change, quote_deci))  # ✅ FIXED
                            last_m_top = i

            return w_bottoms, m_tops

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










