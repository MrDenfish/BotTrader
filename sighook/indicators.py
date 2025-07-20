
from decimal import Decimal

import numpy as np
import pandas as pd

from Config.config_manager import CentralConfig


class Indicators:
    """PART III: Trading Strategies"""
    """ This class contains the functions to calculate various technical indicators and trading signals."""

    def __init__(self, logger_manager):
        self.config = CentralConfig()
        self.logger = logger_manager  # 🙂

        # Bollinger Bands
        self.bb_window = int(self.config.bb_window or 20)
        self.bb_std = Decimal(self.config.bb_std or 2)
        self.bb_lower_band = Decimal(self.config.bb_lower_band or 1.0)
        self.bb_upper_band = Decimal(self.config.bb_upper_band or 1.1)

        # MACD
        self.macd_fast = int(self.config.macd_fast or 12)
        self.macd_slow = int(self.config.macd_slow or 26)
        self.macd_signal = int(self.config.macd_signal or 9)

        # RSI & ROC
        self.rsi_window = int(self.config.rsi_window or 14)
        self.rsi_buy = float(self.config.rsi_buy or 35)
        self.rsi_sell = float(self.config.rsi_sell or 65)
        self.roc_window = int(self.config.roc_window or 4)
        self.roc_buy_threshold = float(self.config._roc_buy_24h or 5)
        self.roc_sell_threshold = -float(self.config._roc_sell_24h or 2)

        # SMA & Volatility
        self.sma_fast = int(self.config.sma_fast or 50)
        self.sma_slow = int(self.config.sma_slow or 200)
        self.sma = int(self.config.sma or 30)
        self.sma_volatility = int(self.config.sma_volatility or 30)

        # Buy/Sell Ratios
        self.buy_ratio = float(self.config.buy_ratio or 1.0)
        self.sell_ratio = float(self.config.sell_ratio or 0.95)

        # ATR & Swing
        self.atr_window = int(self.config.atr_window or 14)
        self.swing_window = int(getattr(self.config, "_swing_window", 20))  # Add this to config if missing

        # Strategy weights (used by scoring logic)
        self.strategy_weights = {
            'Buy Ratio': 1.2, 'Buy Touch': 1.5, 'W-Bottom': 2.0, 'Buy RSI': 2.5,
            'Buy ROC': 2.0, 'Buy MACD': 1.8, 'Buy Swing': 2.2,
            'Sell Ratio': 1.2, 'Sell Touch': 1.5, 'M-Top': 2.0, 'Sell RSI': 2.5,
            'Sell ROC': 2.0, 'Sell MACD': 1.8, 'Sell Swing': 2.2
        }

    @staticmethod
    def normalize_tuple(decision: int, value: float, threshold: float):
        """Ensure all tuples follow the (decision, value, threshold) structure."""
        return int(decision), float(value if value is not None else 0.0), float(threshold if threshold is not None else 0.0)

    def calculate_indicators(self, df: pd.DataFrame, quote_deci: int, debug: bool = False):
        """Compute all trading indicators and return a DataFrame ready for buy/sell scoring."""
        try:
            if df.empty or len(df) < max(self.bb_window, self.macd_slow, self.rsi_window):
                self.logger.warning(f"⚠️ Insufficient OHLCV data. Rows: {len(df)}")
                return df

            # ✅ Ensure all config thresholds are float (avoids Decimal errors)
            bb_std = float(self.bb_std)
            bb_lower_band = float(self.bb_lower_band)
            bb_upper_band = float(self.bb_upper_band)
            buy_ratio = float(self.buy_ratio)
            sell_ratio = float(self.sell_ratio)
            rsi_buy = float(self.rsi_buy)
            rsi_sell = float(self.rsi_sell)
            roc_buy_threshold = float(getattr(self, "roc_buy_threshold", self.roc_buy_threshold))
            roc_sell_threshold = float(getattr(self, "roc_sell_threshold", -self.roc_sell_threshold))

            # === 1. BOLLINGER BANDS ===
            df['basis'] = df['close'].rolling(window=self.bb_window).mean()
            df['std'] = df['close'].rolling(window=self.bb_window).std()
            df['upper'] = df['basis'] + bb_std * df['std']
            df['lower'] = df['basis'] - bb_std * df['std']
            df['band_ratio'] = (df['upper'] / df['lower']).fillna(1.0)

            def compute_touch(row, is_buy=True):
                if is_buy:
                    return self.normalize_tuple(row['close'] <= row['lower'], round(row['close'], quote_deci), row['lower'])
                return self.normalize_tuple(row['close'] >= row['upper'], round(row['close'], quote_deci), row['upper'])

            df['Buy Touch'] = df.apply(lambda r: compute_touch(r, True), axis=1)
            df['Sell Touch'] = df.apply(lambda r: compute_touch(r, False), axis=1)

            df['Buy Ratio'] = df['band_ratio'].apply(
                lambda r: self.normalize_tuple(r > buy_ratio, round(r, quote_deci), buy_ratio)
            )
            df['Sell Ratio'] = df['band_ratio'].apply(
                lambda r: self.normalize_tuple(r < sell_ratio, round(r, quote_deci), sell_ratio)
            )

            # === 2. MACD ===
            df['EMA_fast'] = df['close'].ewm(span=self.macd_fast, adjust=False).mean()
            df['EMA_slow'] = df['close'].ewm(span=self.macd_slow, adjust=False).mean()
            df['MACD'] = df['EMA_fast'] - df['EMA_slow']
            df['Signal_Line'] = df['MACD'].ewm(span=self.macd_signal, adjust=False).mean()
            df['MACD_Histogram'] = df['MACD'] - df['Signal_Line']
            df = self.compute_macd_signals(df)

            # === 3. RSI ===
            delta = df['close'].diff()
            gain = delta.where(delta > 0, 0.0)
            loss = -delta.where(delta < 0, 0.0)
            avg_gain = gain.rolling(window=self.rsi_window).mean()
            avg_loss = loss.rolling(window=self.rsi_window).mean()
            rs = avg_gain / avg_loss.replace(0, np.nan)
            df['RSI'] = (100 - (100 / (1 + rs))).clip(0, 100).fillna(50)

            df['Buy RSI'] = df['RSI'].apply(
                lambda r: self.normalize_tuple(r < rsi_buy, round(r, 2), rsi_buy)
            )
            df['Sell RSI'] = df['RSI'].apply(
                lambda r: self.normalize_tuple(r > rsi_sell, round(r, 2), rsi_sell)
            )

            # === 4. ROC ===
            df['ROC'] = df['close'].pct_change(periods=self.roc_window) * 100
            df['ROC_Diff'] = df['ROC'].diff().fillna(0)
            df['Buy ROC'] = df['ROC'].apply(
                lambda r: self.normalize_tuple(r > roc_buy_threshold, round(r, 2), roc_buy_threshold)
            )
            df['Sell ROC'] = df['ROC'].apply(
                lambda r: self.normalize_tuple(r < roc_sell_threshold, round(r, 2), roc_sell_threshold)
            )

            # === 5. W-BOTTOM & M-TOP ===
            df['W-Bottom'], df['M-Top'] = self.identify_w_bottoms_m_tops(df, quote_deci)

            # === 6. SWING TRADING ===
            df = self.compute_swing_signals(df, quote_deci)

            if debug:
                self.logger.info(f"✅ Indicators calculated for {df.iloc[-1]['time']}")

            return df

        except Exception as e:
            self.logger.error(f"❌ Error in calculate_indicators(): {e}", exc_info=True)
            return df

    def compute_macd_signals(self, df: pd.DataFrame):
        df['Buy MACD'], df['Sell MACD'] = [(0, 0.0, 0.0)] * len(df), [(0, 0.0, 0.0)] * len(df)
        for i in range(1, len(df)):
            prev, curr = df.iloc[i - 1], df.iloc[i]
            buy = prev['MACD'] < prev['Signal_Line'] and curr['MACD'] > curr['Signal_Line'] and curr['MACD'] > 0
            sell = prev['MACD'] > prev['Signal_Line'] and curr['MACD'] < curr['Signal_Line'] and curr['MACD'] < 0
            df.at[df.index[i], 'Buy MACD'] = self.normalize_tuple(buy, curr['MACD_Histogram'], 0.0)
            df.at[df.index[i], 'Sell MACD'] = self.normalize_tuple(sell, curr['MACD_Histogram'], 0.0)
        return df


    def compute_swing_signals(self, df: pd.DataFrame, quote_deci: int):
        df['volatility'] = df['close'].rolling(window=self.sma_volatility).std().fillna(0.0)
        df['rolling_high'] = df['close'].rolling(window=self.swing_window).max()
        df['rolling_low'] = df['close'].rolling(window=self.swing_window).min()

        df['Buy Swing'] = df.apply(
            lambda row: self.normalize_tuple(
                row['close'] > row['rolling_high'] and row['MACD'] > row['Signal_Line'], row['close'], None
            ), axis=1
        )
        df['Sell Swing'] = df.apply(
            lambda row: self.normalize_tuple(
                row['close'] < row['rolling_low'] and row['MACD'] < row['Signal_Line'], row['close'], None
            ), axis=1
        )
        return df

    def identify_w_bottoms_m_tops(self, df: pd.DataFrame, quote_deci: int, debug: bool = False):
        """
        Efficiently identify W-Bottom and M-Top patterns using price, Bollinger bands, and volume.
        Returns two lists of tuples aligned with (decision, value, threshold).
        """

        try:
            # --- Precompute supporting columns ---
            df['atr'] = (df['high'].rolling(self.atr_window).max() - df['low'].rolling(self.atr_window).min()).fillna(0)
            min_price_change = df['atr'].median() * 0.065  # Configurable multiplier
            rolling_volume_mean = df['volume'].rolling(window=self.atr_window, min_periods=1).mean().fillna(0)

            # --- Initialize outputs ---
            w_bottoms = [(0, 0.0, 0.0)] * len(df)
            m_tops = [(0, 0.0, 0.0)] * len(df)

            # --- Detect patterns ---
            for i in range(1, len(df) - 1):
                prev, curr, nxt = df.iloc[i - 1], df.iloc[i], df.iloc[i + 1]

                # ✅ W-Bottom (bullish reversal)
                if (
                        prev['low'] < prev['lower'] and
                        curr['low'] > prev['low'] and nxt['low'] > curr['low'] and
                        nxt['close'] > nxt['basis'] and
                        nxt['volume'] > rolling_volume_mean.iloc[i + 1] and
                        abs(curr['low'] - prev['low']) >= min_price_change
                ):
                    w_bottoms[i] = self.normalize_tuple(
                        1, round(curr['low'], quote_deci), round(min_price_change, quote_deci)
                    )
                    if debug:
                        self.logger.info(f"✅ W-Bottom at index {i} | Low={curr['low']}, MinChange={min_price_change}")

                # ✅ M-Top (bearish reversal)
                if (
                        prev['high'] > prev['upper'] and
                        curr['high'] < prev['high'] and nxt['high'] < curr['high'] and
                        nxt['close'] < nxt['basis'] and
                        nxt['volume'] > rolling_volume_mean.iloc[i + 1] and
                        abs(curr['high'] - prev['high']) >= min_price_change
                ):
                    m_tops[i] = self.normalize_tuple(
                        1, round(curr['high'], quote_deci), round(min_price_change, quote_deci)
                    )
                    if debug:
                        self.logger.info(f"❌ M-Top at index {i} | High={curr['high']}, MinChange={min_price_change}")

            return w_bottoms, m_tops

        except Exception as e:
            self.logger.error(f"❌ Error in identify_w_bottoms_m_tops(): {e}", exc_info=True)
            fallback = [(0, 0.0, 0.0)] * len(df)
            return fallback, fallback


    