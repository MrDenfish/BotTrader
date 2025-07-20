
import time
from decimal import Decimal, ROUND_UP
from typing import Dict, Any, Optional
import pandas as pd
from tabulate import tabulate


class ColorCodes:
    _instance = None  # Singleton instance

    @classmethod
    def get_instance(cls):
        """ Ensures only one instance of PrintData is created. """
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    RESET = "\033[0m"

    # Foreground colors
    BLACK = "\033[30m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    ORANGE = "\033[38;5;208m"  # Orange is not standard, using 256-color code
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"

    # Bright colors
    BRIGHT_RED = "\033[91m"
    BRIGHT_GREEN = "\033[92m"
    BRIGHT_YELLOW = "\033[93m"
    BRIGHT_BLUE = "\033[94m"
    BRIGHT_MAGENTA = "\033[95m"
    BRIGHT_CYAN = "\033[96m"
    BRIGHT_WHITE = "\033[97m"

    # Styles
    BOLD = "\033[1m"
    UNDERLINE = "\033[4m"

    @staticmethod
    def format(text: str, color: str) -> str:
        return f"{color}{text}{ColorCodes.RESET}"


class PrintData:
    _instance = None  # Singleton instance

    @classmethod
    def get_instance(cls, logger_manager, shared_utils_utility):
        """ Ensures only one instance of PrintData is created. """
        if cls._instance is None:
            cls._instance = cls(logger_manager, shared_utils_utility)
        return cls._instance

    def __init__(self, logger_manager, shared_utils_utility):
        self.logger_manager = logger_manager  # 🙂
        if logger_manager.loggers['shared_logger'].name == 'shared_logger':  # 🙂
            self.logger = logger_manager.loggers['shared_logger']
        self.shared_utils_utility = shared_utils_utility

    @staticmethod
    def print_elapsed_time(start_time=None, func_name=None):
        """Calculate elapsed time and print it to the console."""
        try:
            end_time = time.time()
            if start_time is None:
                start_time = time.time()
                return start_time
            else:
                elapsed_seconds = int(end_time - start_time)
                hours = elapsed_seconds // 3600
                minutes = (elapsed_seconds % 3600) // 60
                seconds = elapsed_seconds % 60

                formatted_time = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
                print(f'******   Elapsed time for {func_name}: {formatted_time} (hh:mm:ss) ******')
                return elapsed_seconds
        except Exception as e:
            print(f"Error calculating elapsed time: {e}")
            return None

    @staticmethod
    def format_large_number(val):
        if pd.isna(val): return val
        val = float(val)
        if val >= 1_000_000:
            return f"{val / 1_000_000:.1f}M"
        elif val >= 1_000:
            return f"{val / 1_000:.1f}K"
        return f"{val:.1f}"

    def prepare_condensed_matrix(self,
                                 matrix: pd.DataFrame,
                                 col_rename_map: dict,
                                 volume_columns=('base volume', 'quote volume'),
                                 color_output=True
                                 ) -> pd.DataFrame:
        def green(text):
            return f"\033[92m{text}\033[0m" if color_output else str(text)

        # def red(text):
        #     return f"\033[91m{text}\033[0m" if color_output else str(text)
        #
        # def yellow(text):
        #     return f"\033[93m{text}\033[0m" if color_output else str(text)

        def extract_threshold(value):
            if isinstance(value, tuple) and len(value) == 3:
                try:
                    return round(float(value[2]), 2)
                except (TypeError, ValueError):
                    return None
            return None

        def extract_computed_value(value):
            if isinstance(value, tuple) and len(value) == 3:
                return value[1]
            return value

        def color_if_signal(value):
            if isinstance(value, tuple) and len(value) == 3:
                signal, computed, threshold = value
                if signal == 1:
                    computed = green(computed)
                    return f"{computed}/{round(threshold, 2)}"
                else:
                    # ✅ If no signal, just show computed
                    return round(computed, 2) if computed is not None else ""
            return value

        # 1. Threshold row
        threshold_row = {col: extract_threshold(matrix.iloc[0][col]) for col in matrix.columns}
        threshold_df = pd.DataFrame([threshold_row])
        # Clean up matrix values
        matrix = matrix.copy()
        for col in matrix.columns:
            if col in ['Buy Signal', 'Sell Signal']:
                # ✅ Special formatting: Threshold row shows only threshold
                threshold_df[col] = threshold_df[col].apply(
                    lambda val: round(val, 2) if isinstance(val, (float, int)) else val)

                # ✅ Regular rows show computed/threshold
                matrix[col] = matrix[col].apply(color_if_signal)
            elif any(x in col for x in ['Buy', 'Sell']) and col not in ['Buy Signal', 'Sell Signal']:
                matrix[col] = matrix[col].apply(
                    lambda val: green(val[1]) if isinstance(val, tuple) and val[0] == 1 else extract_computed_value(val)
                )
            else:
                matrix[col] = matrix[col].apply(extract_computed_value)

        # Format volumes
        for vol_col in volume_columns:
            if vol_col in matrix.columns:
                matrix[vol_col] = matrix[vol_col].map(self.format_large_number)

        # Rename 'price change %' ➝ 'chg%' and format
        if 'price change %' in matrix.columns:
            matrix = matrix.rename(columns={'price change %': 'chg%'})
            matrix['chg%'] = matrix['chg%'].apply(
                lambda x: f"{round(float(x), 1)}%" if isinstance(x, (int, float)) else x
            )

        # Rename short columns
        matrix.rename(columns=col_rename_map, inplace=True)
        threshold_df.rename(columns=col_rename_map, inplace=True)

        # Concatenate and set proper index
        final_matrix = pd.concat(
            [threshold_df.fillna(''), matrix.fillna('')],
            ignore_index=True
        )
        final_matrix.index = ['Threshold'] + list(matrix.index)

        return final_matrix

    def print_data(self, min_volume=None, open_orders=None, buy_sell_matrix=None, submitted_orders=None, aggregated_df=None):
        try:
            print("\n" + "<><><><<><>" * 20 + "\n")

            # ✅ PRINT OPEN ORDERS
            if open_orders is not None and len(open_orders) > 0:
                print("📬 Open Orders (abbreviated):")

                # Copy to avoid modifying original DataFrame
                open_orders = open_orders.copy()

                # Shorten order_id and parent_id
                open_orders['order_id'] = open_orders['order_id'].apply(
                    lambda x: f"{x[:8]}...{x[-4:]}" if isinstance(x, str) and len(x) > 12 else x)
                open_orders['parent_id'] = open_orders['parent_id'].apply(
                    lambda x: f"{x[:8]}...{x[-4:]}" if isinstance(x, str) and len(x) > 12 else x)

                # Simplify order_type
                open_orders['type'] = open_orders['type'].replace('TAKE_PROFIT_STOP_LOSS', 'TP/SL')

                # Truncate timestamp
                open_orders['time active'] = open_orders['time active'].apply(
                    lambda x: x[:21] if isinstance(x, str)
                    else x.strftime('%Y-%m-%d %H:%M:%S.%f')[:21] if not pd.isna(x)
                    else ""
                )

                print(tabulate(open_orders, headers='keys', tablefmt='pretty', showindex=False,
                               stralign='center', numalign='center'))
                print("")
            else:
                print("❌ No open orders found.")

            # ✅ PRINT SUBMITTED ORDERS
            if submitted_orders is not None and len(submitted_orders) > 0:
                print("✅ Orders Submitted:")
                print(
                    tabulate(submitted_orders, headers='keys', tablefmt='fancy_outline', showindex=False, stralign='center',
                             numalign='center'))
                print("")
            else:
                print("❌ No orders were submitted.")

            # ✅ PRINT BUY/SELL MATRIX
            if buy_sell_matrix is not None and len(buy_sell_matrix) > 0:
                # Define renaming map for short labels
                col_rename_map = {
                    'Buy Ratio': 'bRt', 'Buy RSI': 'bRSI', 'Buy ROC': 'bROC', 'Buy MACD': 'bMACD',
                    'Sell Ratio': 'sRt', 'Sell RSI': 'sRSI', 'Sell ROC': 'sROC', 'Sell MACD': 'sMACD',
                    'Buy Signal': 'bSig', 'Sell Signal': 'sSig',
                    'base volume': 'bVol', 'quote volume': 'qVol',
                    'price change %': 'chg%'
                }
                condensed_matrix = self.prepare_condensed_matrix(buy_sell_matrix, col_rename_map)

                if condensed_matrix is not None and not condensed_matrix.empty:
                    pd.set_option('display.max_columns', None)
                    pd.set_option('display.width', 0)

                    # Slice dataframe (excluding the threshold row), then filter
                    data_rows = condensed_matrix.iloc[1:].copy()
                    num_signaled = data_rows[
                        (data_rows['bSig'] != '') | (data_rows['sSig'] != '')
                        ].shape[0]

                    minvol = self.format_large_number(
                        Decimal(min_volume.quantize(Decimal('0.01'), ROUND_UP))) if min_volume else "N/A"
                    volume_text = f"{num_signaled} Currencies trading with a Buy/Sell signal (Min Vol: {minvol})"
                    print(f"\n� {volume_text}\n")

                    self.print_condensed_buy_sell_matrix(condensed_matrix)

                    # print(tabulate(condensed_matrix, headers='keys', tablefmt='fancy_grid', showindex=True,
                    #                stralign='center', numalign='center'))
                    print("")

            # ✅ PRINT AGGREGATED HOLDINGS
            if aggregated_df is not None and not aggregated_df.empty:
                column_mapping = {
                    'weighted_average_price': 'Wgt Avg Price',
                    'initial_investment': 'Cost Basis',
                    'unrealized_profit_loss': 'Unrealized PnL',
                    'unrealized_profit_pct': 'Unrealized PnL%',
                    'current_value': 'Value $'
                }
                aggregated_df = aggregated_df.rename(columns=column_mapping)

                # 👉 Filter rows where 'Value $' > 0.01
                aggregated_df = aggregated_df[aggregated_df['Value $'] > 0.01]

                # 👉 Sort by 'symbol' column alphabetically
                if 'symbol' in aggregated_df.columns:
                    aggregated_df = aggregated_df.sort_values(by='symbol', ascending=True)

                print(f"📊 Holdings with Changes - sighook output:\n{aggregated_df.to_string(index=False)}")
            else:
                print("❌ No changes to holdings.")

            print("\n" + "<><><><<><>" * 20 + "\n")


        except Exception as e:
            self.logger.error(f"⚠️ Error printing data: {e}", exc_info=True)

    def print_order_tracker(self, order_tracker, func_name):
        """
        -------->  possibly replace with debug summary in webhook_validate_orders.py   <----------


        Prints the order_tracker in a tabular format for debugging purposes.

        Args:
            order_tracker: The order tracker to validate and print.
            func_name (str): Name of the function for context.
        """
        try:
            is_valid, message = self.shared_utils_utility.validate_order_tracker(order_tracker)

            if is_valid:
                # Extract relevant fields and print
                if isinstance(order_tracker, dict):
                    table_data = [
                        {
                            'Order ID': order_id,
                            'Symbol': order.get('symbol'),
                            'side': order.get('side'),
                            'type': order.get('type'),
                            'Status': order.get('status'),
                            'Amount': order.get("info", {}).get("order_configuration", {}).get("trigger_bracket_gtc", {}).get("base_size"),
                            'Filled': order.get('filled'),
                            'Remaining': order.get('remaining'),
                            'Stop Price': order.get('stopPrice'),
                            'Limit Price': order.get("info", {}).get("order_configuration", {}).get("trigger_bracket_gtc", {}).get("limit_price"),
                            'Created Time': order.get('datetime'),
                            'Order Duration': order.get("order_duration"),
                            'Trigger Status': order.get('info', {}).get('trigger_status') if order.get('trigger_price') is not None else 'Not Active'
                        }
                        for order_id, order in order_tracker.items()
                    ]

                    df = pd.DataFrame(table_data)
                    print(f"Order Tracker for {func_name}:")
                    print(tabulate(df, headers='keys', tablefmt='pretty', showindex=False, stralign='center',
                                   numalign='center'))
                    print("")
                    # print(df.to_string(index=False))
                elif isinstance(order_tracker, pd.DataFrame):
                    print(f"Order Tracker DataFrame for {func_name}:")
                    print(order_tracker.to_string(index=False))
            else:
                print(f"Validation failed in  : {func_name}: {message}")

        except Exception as e:
            print(f"Error printing order_tracker in {func_name}. Exception: {e}")
            self.logger_manager.error({e}, exc_info=True)


    def print_condensed_buy_sell_matrix(self,condensed_matrix):
        # Define logical column groups
        try:
            meta_cols = ['price', 'bVol', 'qVol', 'chg%']
            buy_cols = ['bRt', 'Buy Touch', 'W-Bottom', 'bRSI', 'bROC', 'bMACD', 'Buy Swing']
            sell_cols = ['sRt', 'Sell Touch', 'M-Top', 'sRSI', 'sROC', 'sMACD', 'Sell Swing']
            signal_cols = ['bSig', 'sSig']

            # Slice data rows and filter for non-empty signals or threshold row
            filtered_matrix = condensed_matrix.loc[
                (condensed_matrix.index == 'Threshold') |
                ((condensed_matrix[signal_cols] != 0).any(axis=1))
                ].copy()

            # Optional: Format index into a column for cleaner tabulate output
            filtered_matrix.insert(0, 'Symbol', filtered_matrix.index)

            # Display metadata
            print("\n📊 Market Overview (Price & Volume)")
            print(tabulate(filtered_matrix[['Symbol'] + meta_cols], headers='keys', tablefmt='fancy_grid'))

            # Display buy indicators
            print("\n📈 Buy Indicators")
            print(tabulate(filtered_matrix[['Symbol'] + buy_cols], headers='keys', tablefmt='fancy_grid'))

            # Display sell indicators
            print("\n📉 Sell Indicators")
            print(tabulate(filtered_matrix[['Symbol'] + sell_cols], headers='keys', tablefmt='fancy_grid'))

            # Display signal scores
            print("\n🚨 Trade Signals")
            print(tabulate(filtered_matrix[['Symbol'] + signal_cols], headers='keys', tablefmt='fancy_grid'))
        except Exception as e:
            self.logger_manager.error(f"Error printing condensed buy/sell matrix: {e}", exc_info=True)
            print(f"❌ Error printing condensed buy/sell matrix: {e}")
