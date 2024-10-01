

class Indicators:
    """PART III: Trading Strategies"""
    """ This class contains the functions to calculate various technical indicators and trading signals."""
    def __init__(self, config, logmanager):
        self.log_manager = logmanager

    def calculate_bollinger_bands(self, df, length=20, mult=2.0):
        try:
            if df.empty:
                raise ValueError("Input DataFrame is empty")

            # Calculate the Bollinger Bands
            df['basis'] = df['close'].rolling(window=length).mean()  # Simple moving average
            df['std'] = df['close'].rolling(window=length).std()  # Rolling standard deviation
            df['upper'] = df['basis'] + df['std'] * mult
            df['lower'] = df['basis'] - df['std'] * mult
            df['band_ratio'] = df['upper'] / df['lower']

            # Optionally drop the NaN values from the DataFrame after calculation
            df.dropna(subset=['basis', 'upper', 'lower', 'band_ratio'], inplace=True)

            return df

        except ValueError as e:
            if "DataFrame is empty" in str(e):
                return None
        except Exception as e:
            self.log_manager.error(f"Error in calculate_bollinger_bands(): {e}", exc_info=True)
            return df

    def calculate_trends(self, df, short=50, long=200, period=30):
        try:
            if df.empty:
                raise ValueError("Input DataFrame is empty")

            df['50_sma'] = df['close'].rolling(window=short).mean()  # simple moving average
            df['200_sma'] = df['close'].rolling(window=long).mean()
            df['sma'] = df['close'].rolling(window=period).mean()  # simple moving average
            df['volatility'] = df['close'].rolling(window=period).std()

            # Optionally drop the NaN values from the DataFrame after calculation
            df.dropna(subset=['50_sma', '200_sma', 'sma', 'volatility'], inplace=True)
            return df
        except ValueError as e:
            # Handle the specific case where the symbol is not found
            if "DataFrame is empty" in str(e):
                return None
        except Exception as e:
            self.log_manager.error(f"Error in calculate_sma(): {e}", exc_info=True)
            return df
        raise


    def calculate_rsi(self, df, period=14):
        try:
            delta = df['close'].diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()

            rs = gain / loss
            df['RSI'] = 100 - (100 / (1 + rs))
            # Optionally drop the NaN values from the DataFrame after calculation
            df.dropna(subset=['RSI'], inplace=True)
            return df
        except Exception as e:
            self.log_manager.error(f"Error in calculate_rsi(): {e}", exc_info=True)
            return df


    def calculate_roc(self, df, symbol, roc_len=3):
        try:
            # rate of change for the closing price compared to roc_len bars ago.
            df['ROC'] = ((df['close'] - df['close'].shift(roc_len)) / df['close'].shift(roc_len)) * 100  # rate of change in %
            # rate of change of the closing price from the start of the roc_len period prior to the current roc_len period
            roc_previous = ((df['close'].shift(3) - df['close'].shift(3 + roc_len)) / df['close'].shift(3 + roc_len)) * 100
            # difference between the current period's ROC and the previous period's ROC.
            df['ROC_Diff'] = df['ROC'] - roc_previous  # positive is good, negative is bad
            if symbol == 'BCH/USD':  # debug
                df['ROC_Diff'] = 5
            # Optionally drop the NaN values from the DataFrame after calculation
            df.dropna(subset=['ROC_Diff', 'ROC'], inplace=True)
            return df
        except Exception as e:
            self.log_manager.error(f"Error in calculate_roc(): {e}", exc_info=True)
            return df


    def calculate_macd(self, df, fast_period=12, slow_period=26, signal_period=9):
        try:
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
            # Optionally drop the NaN values from the DataFrame after calculation
            df.dropna(subset=['MACD', 'Signal_Line', 'EMA_fast', 'EMA_slow', ], inplace=True)
            return df
        except Exception as e:
            self.log_manager.error(f"Error in calculate_macd(): {e}", exc_info=True)
            return df

    def swing_trading_signals(self, df):
        try:
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
            #print(f"{buy_conditions},from swing trading signals") # debug
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
        except Exception as e:
            self.log_manager.error(f"Error in swing_trading_signals(): {e}", exc_info=True)
            return df

    def identify_w_bottoms_m_tops(self, bollinger_df, rsi_df=None, min_time_between_signals=5, min_price_change=0.01):
        w_bottoms = []
        m_tops = []
        last_w_bottom = None
        last_m_top = None

        try:
            for i in range(1, len(bollinger_df) - 1):
                # Check for W-Bottom with volume and RSI confirmation
                if (bollinger_df.iloc[i - 1]['low'] < bollinger_df.iloc[i - 1]['lower'] and
                        bollinger_df.iloc[i]['lower'] < bollinger_df.iloc[i]['low'] < bollinger_df.iloc[i + 1]['low'] and
                        bollinger_df.iloc[i + 1]['close'] > bollinger_df.iloc[i + 1]['basis'] and
                        bollinger_df.iloc[i + 1]['volume'] > bollinger_df['volume'].rolling(10).mean().iloc[i + 1] and
                        (rsi_df is None or rsi_df.iloc[i]['RSI'] < 30)):  # Optional RSI confirmation

                    # Noise reduction - filter based on minimum time between signals
                    if last_w_bottom is None or (i - last_w_bottom) >= min_time_between_signals:
                        # Noise reduction - filter based on price change
                        if last_w_bottom is None or abs(
                                bollinger_df.iloc[i]['low'] - bollinger_df.iloc[last_w_bottom]['low']) / \
                                bollinger_df.iloc[last_w_bottom]['low'] > min_price_change:
                            w_bottoms.append(i)
                            last_w_bottom = i  # Update the last W-Bottom signal

                # Check for M-Top with volume and RSI confirmation
                if (bollinger_df.iloc[i - 1]['high'] > bollinger_df.iloc[i - 1]['upper'] and
                        bollinger_df.iloc[i]['upper'] > bollinger_df.iloc[i]['high'] > bollinger_df.iloc[i + 1]['high'] and
                        bollinger_df.iloc[i + 1]['close'] < bollinger_df.iloc[i + 1]['basis'] and
                        bollinger_df.iloc[i + 1]['volume'] > bollinger_df['volume'].rolling(10).mean().iloc[i + 1] and
                        (rsi_df is None or rsi_df.iloc[i]['RSI'] > 70)):  # Optional RSI confirmation

                    # Noise reduction - filter based on minimum time between signals
                    if last_m_top is None or (i - last_m_top) >= min_time_between_signals:
                        # Noise reduction - filter based on price change
                        if last_m_top is None or abs(bollinger_df.iloc[i]['high'] - bollinger_df.iloc[last_m_top]['high']) / \
                                bollinger_df.iloc[last_m_top]['high'] > min_price_change:
                            m_tops.append(i)
                            last_m_top = i  # Update the last M-Top signal

            return w_bottoms, m_tops
        except Exception as e:
            self.log_manager.error(f"Error in identify_w_bottoms_m_tops(): {e}", exc_info=True)
            return w_bottoms, m_tops

    @staticmethod
    def check_for_confirmation(bollinger_df, i, pattern_type):
        """
        Check for confirmation after a pattern is identified.
        :param bollinger_df: DataFrame with Bollinger Bands and price data.
        :param i: Index where the pattern is identified.
        :param pattern_type: 'W-Bottom' or 'M-Top'
        :return: Boolean indicating whether confirmation criteria are met.
        """
        # Confirmation for a W-Bottom with RSI Oversold and price below the lower band
        if pattern_type == 'W-Bottom':
            return bollinger_df.iloc[i]['RSI'] < 30 and  bollinger_df.iloc[i]['lower'] < bollinger_df.iloc[i]['low']

        # Confirmation for an M-Top with RSI Overbought and price above the upper band
        elif pattern_type == 'M-Top':
            return bollinger_df.iloc[i]['RSI'] > 70 and  bollinger_df.iloc[i]['upper'] < bollinger_df.iloc[i]['high']
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
            self.log_manager.error(f"Error in algorithmic_trading_strategy(): {e}", exc_info=True)
            return False, False


