# Description: Bollinger Bands indicator
from logging_manager import LoggerManager


class BollingerBands:
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
    
    def calculate_sma(self, df, length=20):
        try:
            if df.empty:
                raise ValueError("Input DataFrame is empty")

            df['basis'] = df['close'].rolling(window=length).mean()  # simple moving average
        except ValueError as e:
            # Handle the specific case where the symbol is not found
            if "DataFrame is empty" in str(e):
                return None
        except Exception as e:
            print(f"Error in calculate_sma(): {e}")
            return df

        return df
    @staticmethod
    def calculate_rsi(df, period=14):
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()

        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return rsi

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
