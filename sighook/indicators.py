# Description: Bollinger Bands indicator
from logging_manager import LoggerManager


class Indicators:
    def __init__(self):
        self.log_manager = LoggerManager()

    @staticmethod
    def calculate_bollinger_bands(df, length=20, mult=2.0):
        try:
            if df.empty:
                raise ValueError("Input DataFrame is empty")

            df['basis'] = df['close'].rolling(window=length).mean()  # simple moving average
            df['std'] = df['close'].rolling(window=length).std()
            df['upper'] = df['basis'] + df['std'] * mult
            df['lower'] = df['basis'] - df['std'] * mult
            df['band_ratio'] = df['upper'] / df['lower']
        except ValueError as e:
            # Handle the specific case where the symbol is not found
            if "DataFrame is empty" in str(e):
                return None
        except Exception as e:
            print(f"Error in calculate_bollinger_bands(): {e}")
            return df

        return df

    @staticmethod
    def calculate_trends(df, short=50, long=200, period=30):
        try:
            if df.empty:
                raise ValueError("Input DataFrame is empty")

            df['50_sma'] = df['close'].rolling(window=short).mean()  # simple moving average
            df['200_sma'] = df['close'].rolling(window=long).mean()
            df['sma'] = df['close'].rolling(window=period).mean()  # simple moving average
            df['volatility'] = df['close'].rolling(window=period).std()
            return df
        except ValueError as e:
            # Handle the specific case where the symbol is not found
            if "DataFrame is empty" in str(e):
                return None
        except Exception as e:
            print(f"Error in calculate_sma(): {e}")
            return df
        raise

    @staticmethod
    def calculate_macd(df, fast_period=12, slow_period=26, signal_period=9):
        # Calculate the short/fast EMA
        df['EMA_fast'] = df['close'].ewm(span=fast_period, adjust=False).mean()

        # Calculate the long/slow EMA
        df['EMA_slow'] = df['close'].ewm(span=slow_period, adjust=False).mean()

        # Calculate the MACD line
        df['MACD'] = df['EMA_fast'] - df['EMA_slow']

        # Calculate the signal line
        df['Signal_Line'] = df['MACD'].ewm(span=signal_period, adjust=False).mean()

        # (Optional) Calculate the MACD histogram
        df['MACD_Histogram'] = df['MACD'] - df['Signal_Line']

        return df

    @staticmethod
    def calculate_rsi(df, period=14):
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()

        rs = gain / loss
        df['RSI'] = 100 - (100 / (1 + rs))
        return df

    @staticmethod
    def calculate_roc(df, roc_len=3):
        # Calculate the first ROC
        df['ROC'] = ((df['close'] - df['close'].shift(roc_len)) / df['close'].shift(roc_len)) * 100
        # Calculate the second ROC
        # roc_previous = ((df['close'].shift(3) - df['close'].shift(3 + roc_len)) / df['close'].shift(3 + roc_len)) * 100
        # Subtract the second ROC from the first ROC

        return df

    @staticmethod
    def identify_w_bottoms_m_tops(bollinger_df):
        w_bottoms = []
        m_tops = []
        try:
            for i in range(1, len(bollinger_df) - 1):
                # Check for W-Bottom
                if (bollinger_df.iloc[i - 1]['low'] < bollinger_df.iloc[i - 1]['lower'] and
                        bollinger_df.iloc[i]['lower'] < bollinger_df.iloc[i]['low'] < bollinger_df.iloc[i + 1]['low'] and
                        bollinger_df.iloc[i + 1]['close'] > bollinger_df.iloc[i + 1]['basis']):
                    w_bottoms.append(i)

                # Check for M-Top
                if (bollinger_df.iloc[i - 1]['high'] > bollinger_df.iloc[i - 1]['upper'] and
                        bollinger_df.iloc[i]['upper'] > bollinger_df.iloc[i]['high'] > bollinger_df.iloc[i + 1]['high'] and
                        bollinger_df.iloc[i + 1]['close'] < bollinger_df.iloc[i + 1]['basis']):
                    m_tops.append(i)

            return w_bottoms, m_tops
        except Exception as e:
            print(f"Error in identify_w_bottoms_m_tops(): {e}")
            return w_bottoms, m_tops

    @staticmethod
    def check_for_confirmation(bollinger_df, index, pattern_type):
        """
        Check for confirmation after a pattern is identified.
        :param bollinger_df: DataFrame with Bollinger Bands and price data.
        :param index: Index where the pattern is identified.
        :param pattern_type: 'W-Bottom' or 'M-Top'
        :return: Boolean indicating whether confirmation criteria are met.
        """
        # Example: Confirmation for a W-Bottom could be a close above the middle band
        if pattern_type == 'W-Bottom':
            return bollinger_df.iloc[index]['close'] > bollinger_df.iloc[index]['basis']
        # Example: Confirmation for an M-Top could be a close below the middle band
        elif pattern_type == 'M-Top':
            return bollinger_df.iloc[index]['close'] < bollinger_df.iloc[index]['basis']
        else:
            return False

    def algorithmic_trading_strategy(self, bollinger_df):
        """
        Main function to handle the trading strategy.
        :param bollinger_df: DataFrame with Bollinger Bands and price data.
        """
        try:
            w_bottoms, m_tops = self.identify_w_bottoms_m_tops(bollinger_df)
            buy_signal, sell_signal = False, False

            for index in w_bottoms:
                if self.check_for_confirmation(bollinger_df, index, 'W-Bottom'):
                    buy_signal = True

            for index in m_tops:
                if self.check_for_confirmation(bollinger_df, index, 'M-Top'):
                    sell_signal = True
            return buy_signal, sell_signal
        except Exception as e:
            print(f"Error in algorithmic_trading_strategy(): {e}")
            return False, False

    @staticmethod
    def swing_trading_signals(df):
        # Initialize a dictionary to store the trading signals for the given symbol

        df['Buy Swing'] = False
        df['Sell Swing'] = False

        # Define a threshold for low and high volatility (this would need to be optimized)
        low_volatility_threshold = df['volatility'].mean() * 0.8
        high_volatility_threshold = df['volatility'].mean() * 1.2

        # Ensure there's enough data for analysis
        if df.empty or len(df) < 200:
            return df

        # Last row in the DataFrame
        last_row = df.iloc[-1]

        # Conditions for a Buy Swing Signal
        # 1. The current price is above the 50-day moving average, indicating an uptrend.
        # 2. The RSI is below 70 but above 30, avoiding overbought conditions but ensuring some momentum.
        # 3. The MACD line is above the Signal line, indicating bullish momentum.
        # 4. The current price is above the 200-day moving average, confirming the long-term uptrend.
        buy_conditions = [
            last_row['close'] > last_row['50_sma'],
            30 < last_row['RSI'] < 70,
            last_row['MACD'] > last_row['Signal_Line'],
            last_row['close'] > last_row['200_sma'],
            last_row['volatility'] > low_volatility_threshold  # Expecting higher volatility for a strong move
        ]

        # Conditions for a Sell Swing Signal
        # 1. The current price is below the 50-day moving average, indicating a downtrend.
        # 2. The RSI is above 30 but below 70, avoiding oversold conditions but ensuring some downward momentum.
        # 3. The MACD line is below the Signal line, indicating bearish momentum.
        # 4. The current price is below the 200-day moving average, confirming the long-term downtrend.
        sell_conditions = [
            last_row['close'] < last_row['50_sma'],
            30 < last_row['RSI'] < 70,
            last_row['MACD'] < last_row['Signal_Line'],
            last_row['close'] < last_row['200_sma'],
            last_row['volatility'] < high_volatility_threshold  # Lower volatility might indicate a potential reversal
        ]

        # Check if all buy conditions are met
        if all(buy_conditions):
            df['Buy Swing'] = True

        # Check if all sell conditions are met
        if all(sell_conditions):
            df['Sell Swing'] = True

        # Return the swing trading signals
        return df
