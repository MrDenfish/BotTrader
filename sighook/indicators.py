from Shared_Utils.config_manager import CentralConfig
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

    # def calculate_indicators(self, df, indicators_config=None):
    #     """
    #     Calculate all required indicators for buy/sell decisions with detailed values.
    #     """
    #     try:
    #         if df.empty:
    #             raise ValueError("Input DataFrame is empty")
    #
    #         # Check if the dataset is large enough
    #         min_required_rows = 50  # Adjust based on the smallest needed window size
    #         if len(df) < min_required_rows:
    #             self.log_manager.warning(f"Insufficient OHLCV data. Rows fetched: {len(df)}")
    #             return df  # Return unmodified DataFrame
    #
    #         # Default indicator configuration
    #         if indicators_config is None:
    #             indicators_config = {
    #                 'bollinger': True,
    #                 'trends': True,
    #                 'macd': True,
    #                 'rsi': True,
    #                 'roc': True,
    #                 'w_bottoms': True,
    #                 'swing_trading': True,
    #             }
    #
    #         # ✅ Initialize indicators with tuples (0/1, computed value, threshold)
    #         for signal in [
    #             'Buy Ratio', 'Buy Touch', 'W-Bottom', 'Buy RSI', 'Buy ROC', 'Buy MACD', 'Buy Swing',
    #             'Sell Ratio', 'Sell Touch', 'M-Top', 'Sell RSI', 'Sell ROC', 'Sell MACD', 'Sell Swing',
    #             'Buy Signal', 'Sell Signal'
    #         ]:
    #             df[signal] = [(0, None, None)] * len(df)
    #         # ✅ Calculate Bollinger Bands **FIRST**
    #         if indicators_config.get('bollinger'):
    #             df['basis'] = df['close'].rolling(window=min(self.bb_window, len(df))).mean()
    #             df['std'] = df['close'].rolling(window=min(self.bb_std, len(df))).std()
    #             df['upper'] = df['basis'] + 2 * df['std']
    #             df['lower'] = df['basis'] - 2 * df['std']
    #             df['band_ratio'] = df['upper'] / df['lower']
    #
    #             # ✅ Fill NaN values to avoid errors
    #             df[['basis', 'std', 'upper', 'lower', 'band_ratio']] = df[
    #                 ['basis', 'std', 'upper', 'lower', 'band_ratio']].fillna(0.0)
    #
    #             # ✅ Apply Hybrid (Bounded Dynamic) Thresholds
    #             rolling_window = min(50, len(df))  # Define rolling window dynamically
    #             lower_bound = self.bb_lower_band  # Minimum band ratio allowed
    #             upper_bound = self.bb_upper_band  # Maximum band ratio allowed
    #
    #             df['dynamic_buy_ratio'] = (
    #                 df['band_ratio'].rolling(window=rolling_window, min_periods=1).quantile(0.9)
    #                 .clip(lower_bound, upper_bound)  # Keep within limits
    #             )
    #
    #             df['dynamic_sell_ratio'] = (
    #                 df['band_ratio'].rolling(window=rolling_window, min_periods=1).quantile(0.1)
    #                 .clip(lower_bound * self.sell_ratio, upper_bound * self.sell_ratio)  # Slightly relaxed for selling
    #             )
    #
    #             # ✅ Structured Buy/Sell Signals based on Dynamic Ratio
    #             df['Buy Ratio'] = df.apply(
    #                 lambda row: (1, round(row['band_ratio'], 2), round(row['dynamic_buy_ratio'], 2))
    #                 if row['band_ratio'] > row['dynamic_buy_ratio'] else
    #                 (0, round(row['band_ratio'], 2), round(row['dynamic_buy_ratio'], 2)), axis=1
    #             )
    #
    #             df['Sell Ratio'] = df.apply(
    #                 lambda row: (1, round(row['band_ratio'], 2), round(row['dynamic_sell_ratio'], 2))
    #                 if row['band_ratio'] < row['dynamic_sell_ratio'] else
    #                 (0, round(row['band_ratio'], 2), round(row['dynamic_sell_ratio'], 2)), axis=1
    #             )
    #
    #         # ✅ Calculate Trends
    #         if indicators_config.get('trends'):
    #             df['50_sma'] = df['close'].rolling(window=self.sma_fast).mean()
    #             df['200_sma'] = df['close'].rolling(window=self.sma_slow).mean()
    #             df['sma'] = df['close'].rolling(window=self.sma).mean()
    #             df['volatility'] = df['close'].rolling(window=self.sma_volatility).std()
    #
    #         # ✅ Calculate MACD
    #         if indicators_config.get('macd'):
    #             df['EMA_fast'] = df['close'].ewm(span=self.macd_fast, min_periods=1, adjust=False).mean()
    #             df['EMA_slow'] = df['close'].ewm(span=self.macd_slow, min_periods=1, adjust=False).mean()
    #             df['MACD'] = df['EMA_fast'] - df['EMA_slow']
    #             df['Signal_Line'] = df['MACD'].ewm(span=self.macd_signal, min_periods=1, adjust=False).mean()
    #             df['MACD_Histogram'] = df['MACD'] - df['Signal_Line']
    #
    #             df['Buy MACD'] = df.apply(
    #                 lambda row: (1, round(row['MACD_Histogram'], 4), 0) if row['MACD_Histogram'] > 0 else
    #                 (0, round(row['MACD_Histogram'], 4), 0), axis=1
    #             )
    #
    #             df['Sell MACD'] = df.apply(
    #                 lambda row: (1, round(row['MACD_Histogram'], 4), 0) if row['MACD_Histogram'] < 0 else
    #                 (0, round(row['MACD_Histogram'], 4), 0), axis=1
    #             )
    #
    #         # ✅ Calculate RSI
    #         if indicators_config.get('rsi'):
    #             delta = df['close'].diff()
    #             gain = delta.where(delta > 0, 0).rolling(window=self.rsi_window, min_periods=1).mean()
    #             loss = -delta.where(delta < 0, 0).rolling(window=self.rsi_window, min_periods=1).mean()
    #             rs = gain / loss.replace(0, np.nan)
    #             df['RSI'] = 100 - (100 / (1 + rs))
    #             df.loc[:, 'RSI'] = df['RSI'].fillna(50)
    #
    #
    #             df['Buy RSI'] = df.apply(lambda row: (1, round(row['RSI'], 0), self.rsi_buy)
    #             if row['RSI'] < self.rsi_buy + 7 else
    #             (0, round(row['RSI'], 0), self.rsi_buy), axis=1)
    #
    #             df['Sell RSI'] = df.apply(lambda row: (1, round(row['RSI'], 0), self.rsi_sell)
    #             if row['RSI'] > self.rsi_sell - 7 else
    #             (0, round(row['RSI'], 0), self.rsi_sell), axis=1)
    #
    #         # ✅ Calculate ROC
    #         if indicators_config.get('roc'):
    #             df['ROC'] = df['close'].pct_change(min(3, len(df))) * 100
    #             df['ROC_Diff'] = df['ROC'].diff()
    #             df[['ROC', 'ROC_Diff', 'RSI']] = df[['ROC', 'ROC_Diff', 'RSI']].fillna(0.0)
    #
    #             df['Buy ROC'] = df.apply(lambda row: (1, round(row['ROC'], 1), 5)
    #             if row['ROC'] > 5 and row['ROC_Diff'] > 0.3 and row['RSI'] <= self.rsi_buy else
    #             (0, round(row['ROC'], 1), 5), axis=1)
    #
    #             df['Sell ROC'] = df.apply(lambda row: (1, round(row['ROC'], 1), -2.5)
    #             if row['ROC'] < -2.5 and row['ROC_Diff'] < -0.2 and row['RSI'] >= self.rsi_sell else
    #             (0, round(row['ROC'], 1), -2.5), axis=1)
    #
    #         # ✅ Identify W-Bottoms and M-Tops
    #         if indicators_config.get('w_bottoms'):
    #             w_bottoms, m_tops = self.identify_w_bottoms_m_tops(df)
    #             df['W-Bottom'] = w_bottoms
    #             df['M-Top'] = m_tops
    #
    #         # ✅ Apply Swing Trading
    #         if indicators_config.get('swing_trading'):
    #             df = self.swing_trading_signals(df)
    #
    #         df.dropna()
    #         return df
    #
    #     except Exception as e:
    #         self.log_manager.error(f"Error in calculate_indicators(): {e}", exc_info=True)
    #         return None

    def calculate_indicators(self, df, indicators_config=None):
        """
        Calculate all required indicators for buy/sell decisions with detailed values.
        """
        try:
            if df.empty:
                raise ValueError("Input DataFrame is empty")

            # ✅ Ensure we have enough data to calculate indicators
            min_required_rows = 50  # Adjust based on the smallest needed window size
            if len(df) < min_required_rows:
                self.log_manager.warning(f"Insufficient OHLCV data. Rows fetched: {len(df)}")
                return df  # Return unmodified DataFrame

            # ✅ Default indicator configuration
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

            # ✅ Initialize all signal columns to avoid None issues
            signal_columns = [
                'Buy Ratio', 'Buy Touch', 'W-Bottom', 'Buy RSI', 'Buy ROC', 'Buy MACD', 'Buy Swing',
                'Sell Ratio', 'Sell Touch', 'M-Top', 'Sell RSI', 'Sell ROC', 'Sell MACD', 'Sell Swing',
                'Buy Signal', 'Sell Signal'
            ]
            for signal in signal_columns:
                df[signal] = [(0, None, None)] * len(df)

            # ✅ Bollinger Bands Calculation
            if indicators_config.get('bollinger'):
                df['basis'] = df['close'].rolling(window=min(self.bb_window, len(df))).mean()
                df['std'] = df['close'].rolling(window=min(self.bb_std, len(df))).std()
                df['upper'] = df['basis'] + 2 * df['std']
                df['lower'] = df['basis'] - 2 * df['std']
                df['lower'] = df['lower'].replace(0, np.nan)  # Prevent divide by zero
                df['band_ratio'] = df['upper'] / df['lower']

                df[['basis', 'std', 'upper', 'lower', 'band_ratio']] = df[
                    ['basis', 'std', 'upper', 'lower', 'band_ratio']].fillna(1.0)

                rolling_window = min(50, len(df))
                df['dynamic_buy_ratio'] = df['band_ratio'].rolling(window=rolling_window, min_periods=1).quantile(0.9).clip(
                    self.bb_lower_band, self.bb_upper_band)
                df['dynamic_sell_ratio'] = df['band_ratio'].rolling(window=rolling_window, min_periods=1).quantile(0.1).clip(
                    self.bb_lower_band * self.sell_ratio, self.bb_upper_band * self.sell_ratio)

                df['Buy Ratio'] = df.apply(lambda row: (1, round(row['band_ratio'], 2), round(row['dynamic_buy_ratio'], 2))
                if row['band_ratio'] > row['dynamic_buy_ratio']
                else (0, round(row['band_ratio'], 2), round(row['dynamic_buy_ratio'], 2)), axis=1)

                df['Sell Ratio'] = df.apply(lambda row: (1, round(row['band_ratio'], 2), round(row['dynamic_sell_ratio'], 2))
                if row['band_ratio'] < row['dynamic_sell_ratio']
                else (0, round(row['band_ratio'], 2), round(row['dynamic_sell_ratio'], 2)), axis=1)

            # ✅ RSI Calculation
            if indicators_config.get('rsi'):
                delta = df['close'].diff()
                gain = delta.where(delta > 0, 0).rolling(window=self.rsi_window, min_periods=1).mean()
                loss = -delta.where(delta < 0, 0).rolling(window=self.rsi_window, min_periods=1).mean()
                rs = gain / loss.replace(0, np.nan)
                df['RSI'] = 100 - (100 / (1 + rs))
                df['RSI'] = df['RSI'].clip(0, 100).fillna(50)

                df['Buy RSI'] = df.apply(lambda row: (1, round(row['RSI'], 0), self.rsi_buy)
                if row['RSI'] < self.rsi_buy + 7 else (0, round(row['RSI'], 0), self.rsi_buy), axis=1)

                df['Sell RSI'] = df.apply(lambda row: (1, round(row['RSI'], 0), self.rsi_sell)
                if row['RSI'] > self.rsi_sell - 7 else (0, round(row['RSI'], 0), self.rsi_sell), axis=1)

            # ✅ ROC Calculation
            if indicators_config.get('roc'):
                df['ROC'] = df['close'].pct_change(min(3, len(df))) * 100
                df['ROC_Diff'] = df['ROC'].diff()
                df[['ROC', 'ROC_Diff']] = df[['ROC', 'ROC_Diff']].fillna(0)

                df['Buy ROC'] = df.apply(lambda row: (1, round(row['ROC'], 1), 5)
                if row['ROC'] > 5 and row['ROC_Diff'] > 0.3 and row['RSI'] <= self.rsi_buy else (0, round(row['ROC'], 1), 5),
                                         axis=1)

                df['Sell ROC'] = df.apply(lambda row: (1, round(row['ROC'], 1), -2.5)
                if row['ROC'] < -2.5 and row['ROC_Diff'] < -0.2 and row['RSI'] >= self.rsi_sell else (
                0, round(row['ROC'], 1), -2.5), axis=1)

            # ✅ MACD Calculation (Added Back)
            if indicators_config.get('macd'):
                df['EMA_fast'] = df['close'].ewm(span=self.macd_fast, min_periods=1, adjust=False).mean()
                df['EMA_slow'] = df['close'].ewm(span=self.macd_slow, min_periods=1, adjust=False).mean()
                df['MACD'] = df['EMA_fast'] - df['EMA_slow']
                df['Signal_Line'] = df['MACD'].ewm(span=self.macd_signal, min_periods=1, adjust=False).mean()
                df['MACD_Histogram'] = df['MACD'] - df['Signal_Line']

                df['Buy MACD'] = df.apply(lambda row: (1, round(row['MACD_Histogram'], 4), 0)
                if row['MACD_Histogram'] > 0 else (0, round(row['MACD_Histogram'], 4), 0), axis=1)

                df['Sell MACD'] = df.apply(lambda row: (1, round(row['MACD_Histogram'], 4), 0)
                if row['MACD_Histogram'] < 0 else (0, round(row['MACD_Histogram'], 4), 0), axis=1)

            # ✅ Trends & Volatility Calculation
            if indicators_config.get('trends'):
                df['50_sma'] = df['close'].rolling(window=self.sma_fast).mean()
                df['200_sma'] = df['close'].rolling(window=self.sma_slow).mean()
                df['sma'] = df['close'].rolling(window=self.sma).mean()
                df['volatility'] = df['close'].rolling(window=self.sma_volatility).std().fillna(0)

            # ✅ Identify W-Bottoms and M-Tops
            if indicators_config.get('w_bottoms'):
                w_bottoms, m_tops = self.identify_w_bottoms_m_tops(df)
                df['W-Bottom'] = w_bottoms
                df['M-Top'] = m_tops

            # ✅ Swing Trading Signals (Ensure volatility exists first)
            if 'volatility' not in df.columns:
                df['volatility'] = df['close'].rolling(window=self.sma_volatility).std().fillna(0)

            if indicators_config.get('swing_trading'):
                df = self.swing_trading_signals(df)

            # ✅ Remove unnecessary NaN values
            df.dropna(inplace=True)

            # ✅ Debugging print (check if indicators are working)
            #print(df[['Buy Ratio', 'Buy RSI', 'Buy ROC', 'MACD_Histogram']].head(10))

            return df

        except Exception as e:
            self.log_manager.error(f"Error in calculate_indicators(): {e}", exc_info=True)
            return None

    # def calculate_indicators(self, df, indicators_config=None):
    #     """
    #     Calculate all required indicators for buy/sell decisions with detailed values.
    #     """
    #     try:
    #         if df.empty:
    #             raise ValueError("Input DataFrame is empty")
    #
    #         # ✅ Ensure we have enough data to calculate indicators
    #         min_required_rows = 50  # Adjust based on the smallest needed window size
    #         if len(df) < min_required_rows:
    #             self.log_manager.warning(f"Insufficient OHLCV data. Rows fetched: {len(df)}")
    #             return df  # Return unmodified DataFrame
    #
    #         # ✅ Default indicator configuration
    #         if indicators_config is None:
    #             indicators_config = {
    #                 'bollinger': True,
    #                 'trends': True,
    #                 'macd': True,
    #                 'rsi': True,
    #                 'roc': True,
    #                 'w_bottoms': True,
    #                 'swing_trading': True,
    #             }
    #
    #         # ✅ Initialize all signal columns to avoid None issues
    #         for signal in [
    #             'Buy Ratio', 'Buy Touch', 'W-Bottom', 'Buy RSI', 'Buy ROC', 'Buy MACD', 'Buy Swing',
    #             'Sell Ratio', 'Sell Touch', 'M-Top', 'Sell RSI', 'Sell ROC', 'Sell MACD', 'Sell Swing',
    #             'Buy Signal', 'Sell Signal'
    #         ]:
    #             df[signal] = [(0, None, None)] * len(df)  # Ensure consistent structure
    #
    #         # ✅ Calculate Bollinger Bands **FIRST**
    #         if indicators_config.get('bollinger'):
    #             df['basis'] = df['close'].rolling(window=min(self.bb_window, len(df))).mean()
    #             df['std'] = df['close'].rolling(window=min(self.bb_std, len(df))).std()
    #             df['upper'] = df['basis'] + 2 * df['std']
    #             df['lower'] = df['basis'] - 2 * df['std']
    #
    #             # � Fix: Prevent division errors in `band_ratio`
    #             df['lower'] = df['lower'].replace(0, np.nan)  # Avoid divide by zero
    #             df['band_ratio'] = df['upper'] / df['lower']
    #
    #             # ✅ Fill NaN values to avoid errors
    #             df[['basis', 'std', 'upper', 'lower', 'band_ratio']] = df[
    #                 ['basis', 'std', 'upper', 'lower', 'band_ratio']].fillna(1.0)
    #
    #             # ✅ Apply Dynamic Thresholds
    #             rolling_window = min(50, len(df))  # Ensure rolling window does not exceed available rows
    #             lower_bound = self.bb_lower_band
    #             upper_bound = self.bb_upper_band
    #
    #             df['dynamic_buy_ratio'] = (
    #                 df['band_ratio'].rolling(window=rolling_window, min_periods=1).quantile(0.9)
    #                 .clip(lower_bound, upper_bound)
    #             )
    #
    #             df['dynamic_sell_ratio'] = (
    #                 df['band_ratio'].rolling(window=rolling_window, min_periods=1).quantile(0.1)
    #                 .clip(lower_bound * self.sell_ratio, upper_bound * self.sell_ratio)
    #             )
    #
    #             # ✅ Assign Buy/Sell Ratios
    #             df['Buy Ratio'] = df.apply(
    #                 lambda row: (1, round(row['band_ratio'], 2), round(row['dynamic_buy_ratio'], 2))
    #                 if row['band_ratio'] > row['dynamic_buy_ratio'] else
    #                 (0, round(row['band_ratio'], 2), round(row['dynamic_buy_ratio'], 2)), axis=1
    #             )
    #
    #             df['Sell Ratio'] = df.apply(
    #                 lambda row: (1, round(row['band_ratio'], 2), round(row['dynamic_sell_ratio'], 2))
    #                 if row['band_ratio'] < row['dynamic_sell_ratio'] else
    #                 (0, round(row['band_ratio'], 2), round(row['dynamic_sell_ratio'], 2)), axis=1
    #             )
    #
    #         # ✅ Calculate RSI
    #         if indicators_config.get('rsi'):
    #             delta = df['close'].diff()
    #             gain = delta.where(delta > 0, 0).rolling(window=self.rsi_window, min_periods=1).mean()
    #             loss = -delta.where(delta < 0, 0).rolling(window=self.rsi_window, min_periods=1).mean()
    #             rs = gain / loss.replace(0, np.nan)
    #
    #             df['RSI'] = 100 - (100 / (1 + rs))
    #
    #             # � Fix: Ensure RSI stays within 0-100 and replace NaN
    #             df['RSI'] = df['RSI'].clip(0, 100).fillna(50)
    #
    #             df['Buy RSI'] = df.apply(lambda row: (1, round(row['RSI'], 0), self.rsi_buy)
    #             if row['RSI'] < self.rsi_buy + 7 else
    #             (0, round(row['RSI'], 0), self.rsi_buy), axis=1)
    #
    #             df['Sell RSI'] = df.apply(lambda row: (1, round(row['RSI'], 0), self.rsi_sell)
    #             if row['RSI'] > self.rsi_sell - 7 else
    #             (0, round(row['RSI'], 0), self.rsi_sell), axis=1)
    #
    #         # ✅ Calculate ROC
    #         if indicators_config.get('roc'):
    #             df['ROC'] = df['close'].pct_change(min(3, len(df))) * 100
    #             df['ROC_Diff'] = df['ROC'].diff()
    #
    #             # � Fix: Ensure no NaN values in ROC
    #             df[['ROC', 'ROC_Diff']] = df[['ROC', 'ROC_Diff']].fillna(0)
    #
    #             df['Buy ROC'] = df.apply(lambda row: (1, round(row['ROC'], 1), 5)
    #             if row['ROC'] > 5 and row['ROC_Diff'] > 0.3 and row['RSI'] <= self.rsi_buy else
    #             (0, round(row['ROC'], 1), 5), axis=1)
    #
    #             df['Sell ROC'] = df.apply(lambda row: (1, round(row['ROC'], 1), -2.5)
    #             if row['ROC'] < -2.5 and row['ROC_Diff'] < -0.2 and row['RSI'] >= self.rsi_sell else
    #             (0, round(row['ROC'], 1), -2.5), axis=1)
    #
    #         # ✅ Identify W-Bottoms and M-Tops
    #         if indicators_config.get('w_bottoms'):
    #             w_bottoms, m_tops = self.identify_w_bottoms_m_tops(df)
    #             df['W-Bottom'] = w_bottoms
    #             df['M-Top'] = m_tops
    #
    #         # ✅ Ensure volatility always exists before calling swing_trading_signals
    #         if 'volatility' not in df.columns:
    #             df['volatility'] = df['close'].rolling(window=self.sma_volatility).std().fillna(0)
    #
    #         # ✅ Apply Swing Trading
    #         if indicators_config.get('swing_trading'):
    #             df = self.swing_trading_signals(df)
    #
    #         # � Fix: Ensure no lingering NaN values in the final DataFrame
    #         df.dropna(inplace=True)  # Remove any remaining rows with NaN values
    #
    #         print(df[['Buy Ratio', 'Buy RSI', 'Buy ROC', 'MACD_Histogram']].head(10))
    #
    #         return df
    #
    #     except Exception as e:
    #         self.log_manager.error(f"Error in calculate_indicators(): {e}", exc_info=True)
    #         return None

    def calculate_bollinger_bands(self, df, length=None, mult=None):
        try:
            if df.empty:
                raise ValueError("Input DataFrame is empty")
            length = length or self.bb_window
            bb_std = mult or self.bb_std
            # Calculate the Bollinger Bands
            df['basis'] = df['close'].rolling(window=length).mean()  # Simple moving average
            df['std'] = df['close'].rolling(window=length).std()  # Rolling standard deviation
            df['upper'] = df['basis'] + df['std'] * bb_std
            df['lower'] = df['basis'] - df['std'] * bb_std
            df['band_ratio'] = df['upper'] / df['lower']

            # Ensure no NaN values before applying conditions
            df[['upper', 'lower', 'band_ratio']] = df[['upper', 'lower', 'band_ratio']].fillna(0.0)

            # Apply structured tuple format (0/1, computed_value, threshold)
            df['Buy Touch'] = df.apply(
                lambda row: (
                    1, round(row['close'], 2), round(row['lower'], 2)
                ) if row['close'] < row['lower'] else (0, round(row['close'], 2), round(row['lower'], 2)),
                axis=1
            )

            df['Sell Touch'] = df.apply(
                lambda row: (
                    1, round(row['close'], 2), round(row['upper'], 2)
                ) if row['close'] > row['upper'] else (0, round(row['close'], 2), round(row['upper'], 2)),
                axis=1
            )

            df['Buy Ratio'] = df.apply(
                lambda row: (
                    1, round(row['band_ratio'], 2), self.buy_ratio
                ) if row['band_ratio'] > self.buy_ratio else (0, round(row['band_ratio'], 2), self.buy_ratio),
                axis=1
            )

            df['Sell Ratio'] = df.apply(
                lambda row: (
                    1, round(row['band_ratio'], 2), self.sell_ratio
                ) if row['band_ratio'] < self.sell_ratio else (0, round(row['band_ratio'], 2), self.sell_ratio),
                axis=1
            )

            # Drop NaN values after all calculations
            df.dropna(subset=['basis', 'upper', 'lower', 'band_ratio'])

            return df

        except ValueError as e:
            if "DataFrame is empty" in str(e):
                return None
        except Exception as e:
            self.log_manager.error(f"Error in calculate_bollinger_bands(): {e}", exc_info=True)
            return df


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

    def identify_w_bottoms_m_tops(self, df):
        """
        Identify W-Bottom and M-Top patterns using dynamically determined parameters.
        """
        w_bottoms = [(0, None, None)] * len(df)
        m_tops = [(0, None, None)] * len(df)

        try:
            # ✅ Dynamic `min_time_between_signals`
            min_time_between_signals = max(3, int(len(df) * 0.005))  # 0.5% of dataset size

            # ✅ Dynamic `min_price_change` using ATR
            df['atr'] = df['high'].rolling(self.atr_window).max() - df['low'].rolling(self.atr_window).min()  # Approximate ATR
            min_price_change = df['atr'].median() * 0.1  # 10% of median ATR

            # ✅ Dynamic rolling window for volume confirmation
            volatility = df['close'].pct_change().rolling(self.atr_window+7).std()
            rolling_window = int(10 + (volatility.mean() * 100))
            rolling_window = max(5, min(self.atr_window+7, rolling_window))  # Keep reasonable range
            df['volume_mean'] = df['volume'].rolling(rolling_window, min_periods=1).mean()

            last_w_bottom, last_m_top = None, None
            detected_w_bottoms = []
            detected_m_tops = []

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
                        if last_w_bottom is None or abs(curr['low'] - df.iloc[last_w_bottom]['low']) / \
                                df.iloc[last_w_bottom]['low'] > min_price_change:
                            w_bottoms[i] = (1, round(curr['low'], 2), round(min_price_change, 4))
                            last_w_bottom = i
                            detected_w_bottoms.append(i)
                            #print(f"✅ W-Bottom Detected at Index {i}: Low={round(curr['low'], 2)}")

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
                            m_tops[i] = (1, round(curr['high'], 2), round(min_price_change, 4))
                            last_m_top = i
                            detected_m_tops.append(i)
                            #print(f"✅ M-Top Detected at Index {i}: High={round(curr['high'], 2)}")

            # print(f"� Total W-Bottoms: {sum(1 for x in w_bottoms if x[0] == 1)}")
            # print(f"� Total M-Tops: {sum(1 for x in m_tops if x[0] == 1)}")

            return w_bottoms, m_tops

        except Exception as e:
            self.log_manager.error(f"Error in identify_w_bottoms_m_tops(): {e}", exc_info=True)
            return [(0, None, None)] * len(df), [(0, None, None)] * len(df)

    def plot_w_bottoms_m_tops(self, df, detected_w_bottoms, detected_m_tops):  # debugging to get a visual of the data
        """
        Plot Bollinger Bands with detected W-Bottoms and M-Tops.
        """
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










