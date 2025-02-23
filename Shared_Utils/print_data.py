
from tabulate import tabulate
from decimal import Decimal, ROUND_UP
import pandas as pd
import time

class PrintData:
    _instance = None  # Singleton instance

    @classmethod
    def get_instance(cls, log_manager):
        """ Ensures only one instance of PrintData is created. """
        if cls._instance is None:
            cls._instance = cls(log_manager)
        return cls._instance

    def __init__(self, log_manager):
        self.log_manager = log_manager

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

    def format_large_number(self, value):
        """Format large numbers for better readability."""
        if isinstance(value, (int, float, Decimal)):
            if abs(value) >= 1_000_000_000:
                return f"{value / 1_000_000_000:.1f}B"
            elif abs(value) >= 1_000_000:
                return f"{value / 1_000_000:.1f}M"
            elif abs(value) >= 1_000:
                return f"{value / 1_000:.1f}K"
            return f"{value:.2f}"
        return value

    def extract_tuple(self,value):
        """
        Extracts and formats values from a tuple (signal, value, threshold).
        - If threshold exists, it is displayed.
        - If threshold is None, it is omitted.
        - Ensures clean formatting.
        """
        if isinstance(value, tuple) and len(value) == 3:
            signal, computed_value, threshold = value
            if computed_value is not None and threshold is not None:
                return f"{signal} ({computed_value}, {threshold})"
            elif threshold is None and computed_value is None:
                return f"{signal}"
                #return f"{signal} ({computed_value}, {threshold})"  # Show threshold
            elif computed_value is None and threshold is not None:
                return f"{signal} ({threshold})"
            else:
                return f"{signal} ({computed_value})"  # Omit threshold if None
        return value

    def extract_threshold(self, value):
        """Extracts only the threshold value for the header row."""

        return value[2] if isinstance(value, tuple) and len(value) == 3 and value[2] is not None else ""

    def print_data(self, min_volume=None, open_orders=None, buy_sell_matrix=None, submitted_orders=None, aggregated_df=None):
        try:
            buy_sell_matrix = buy_sell_matrix.reset_index()

            print("\n" + "<><><><<><>" * 20 + "\n")

            # ✅ PRINT OPEN ORDERS
            if open_orders is not None and len(open_orders) > 0:
                print("� Open Orders:")
                print(tabulate(open_orders, headers='keys', tablefmt='pretty', showindex=False, stralign='center',
                               numalign='center'))
                print("")
            else:
                print("❌ No open orders found.")

            # ✅ PRINT SUBMITTED ORDERS
            if submitted_orders is not None and len(submitted_orders) > 0:
                print("� Orders Submitted:")
                print(
                    tabulate(submitted_orders, headers='keys', tablefmt='fancy_outline', showindex=False, stralign='center',
                             numalign='center'))
                print("")
            else:
                print("❌ No orders were submitted.")

            # ✅ PRINT BUY/SELL MATRIX
            if buy_sell_matrix is not None and len(buy_sell_matrix) > 0:
                # Ensure asset column remains as index
                buy_sell_matrix = buy_sell_matrix.copy()

                # ✅ Create a threshold row (only for threshold values)
                threshold_row = {col: self.extract_threshold(buy_sell_matrix.iloc[0][col]) for col in
                                 buy_sell_matrix.columns}
                threshold_df = pd.DataFrame([threshold_row])  # Convert to DataFrame

                # ✅ Remove threshold from other rows and format values correctly
                def clean_tuple(value):
                    """Extracts only the computed value from the tuple, omitting threshold and formatting correctly."""
                    if isinstance(value, tuple) and len(value) == 3:
                        signal, computed_value, threshold = value
                        return computed_value if computed_value is not None else signal  # Remove threshold
                    return value

                def clean_signals(value):
                    """Extracts only the signal value (0 or 1) for Buy/Sell Signal columns."""
                    if isinstance(value, tuple) and len(value) == 3:
                        return f'{value[1]}/{value[2]}'  # Extract only the computed and threshold values
                    return value

                # Apply formatting functions
                for col in buy_sell_matrix.columns:
                    if col in ['Buy Signal', 'Sell Signal']:
                        buy_sell_matrix[col] = buy_sell_matrix[col].apply(clean_signals)
                    elif col in ['Buy Ratio', 'Buy RSI', 'Buy ROC', 'Buy MACD', 'Sell Ratio', 'Sell RSI', 'Sell ROC',
                                 'Sell MACD']:
                        buy_sell_matrix[col] = buy_sell_matrix[col].apply(clean_tuple)
                    else:
                        buy_sell_matrix[col] = buy_sell_matrix[col].apply(self.extract_tuple)

                # ✅ Combine threshold row and formatted data
                formatted_matrix = pd.concat([threshold_df, buy_sell_matrix], ignore_index=True)

                # ✅ Adjust index: Set 'Threshold' as the first row's index, keep assets for the rest
                formatted_matrix.index = ['Threshold'] + list(buy_sell_matrix.index)

                # ✅ Filter rows with Buy/Sell signals (after threshold row)
                filtered_matrix = formatted_matrix.iloc[1:].copy()  # Exclude 'Threshold' row from filtering
                filtered_matrix = filtered_matrix[
                    (filtered_matrix['Buy Signal'].notna()) & (filtered_matrix['Buy Signal'] != '') |
                    (filtered_matrix['Sell Signal'].notna()) & (filtered_matrix['Sell Signal'] != '')
                    ].copy()

                # ✅ Format large numbers (volumes)
                for col in ['base volume', 'quote volume']:
                    if col in formatted_matrix.columns:
                        formatted_matrix[col] = formatted_matrix[col].map(self.format_large_number)

                # ✅ Round `price change %` to 1 decimal place
                if 'price change %' in formatted_matrix.columns:
                    formatted_matrix['price change %'] = formatted_matrix['price change %'].apply(
                        lambda x: f"{round(float(x), 1)}%" if isinstance(x, (int, float)) or
                                                              (isinstance(x, str) and
                                                               x.replace('.', '', 1).replace('-', '', 1).isdigit())
                                                           else x
                    )

                    formatted_matrix = formatted_matrix.rename(columns={'price change %': 'price change'})

                # ✅ Dynamically adjust pandas settings for column display
                pd.set_option('display.max_columns', None)  # Show all columns
                pd.set_option('display.width', 0)  # Auto-fit width
                # ✅ Print Buy/Sell Matrix
                minvol = self.format_large_number(
                    Decimal(min_volume.quantize(Decimal('0.01'), ROUND_UP))) if min_volume else "N/A"
                volume_text = f"{len(filtered_matrix)} Currencies trading with a Buy/Sell signal (Min Vol: {minvol})"
                print(f"\n� {volume_text}")
                # Dynamically determine columns to display
                columns_to_display = [col for col in formatted_matrix.columns if not all(formatted_matrix[col].isna())]

                # Print only the relevant columns
                print(
                    tabulate(formatted_matrix[columns_to_display], headers='keys', tablefmt='fancy_outline', showindex=True,
                             stralign='center', numalign='center'))

                print("")

            # ✅ PRINT AGGREGATED HOLDINGS
            if aggregated_df is not None and not aggregated_df.empty:
                # Define the mapping of old column names to new column names
                column_mapping = {
                    'weighted_average_price': 'Wgt Avg Price',
                    'initial_investment': 'Cost Basis',
                    'unrealized_profit_loss': 'Unrealized PnL',
                    'unrealized_profit_pct': 'Unrealized PnL%',
                    'current_value': 'Value $'
                }

                # Rename columns
                aggregated_df = aggregated_df.rename(columns=column_mapping)

                print(f"� Holdings with Changes:\n{aggregated_df.to_string(index=False)}")
            else:
                print("❌ No changes to holdings.")

            print("\n" + "<><><><<><>" * 20 + "\n")

        except Exception as e:
            self.log_manager.error(f"⚠️ Error printing data: {e}", exc_info=True)

    @staticmethod
    def print_order_tracker(order_tracker_master, msg=None):
        """Print the order tracker to the console - - - Used mainly for debugging"""

        try:
            for order_id, order_details in order_tracker_master.items():
                order_config = order_details.get('info', {}).get('order_configuration', {}).get('limit_limit_gtc', {})
                order_info = order_details.get('info', {})
                order_size = order_config.get('base_size')
                asset_price = order_config.get('limit_price')
                if not order_config:
                    symbol = order_details.get('symbol')
                    order_status = order_details.get('status_of_order')
                    order_size = order_details.get('amount')
                    price = order_details.get('current_price')
                    print(f" {symbol}")
                    print(f"\nID: {order_status}")
                    print(f" size:{order_size}")
                    print(f" price:{price}")


                if order_info:
                    print(f"\n ---- {order_details.get('type').upper()} {order_details.get('side').upper()} ----")
                    print(f" {order_details['info']['product_id']}")
                    print(f"\nID: {order_id}")
                    print(f" size:{order_size}")
                    print(f" limit price:{asset_price}")
                    print(f" {order_details['info']['status']}")
                else:
                    for key, value in order_details.items():
                        print(f" {value['product_id']}")
                        print(f"\nID: {order_id}")
                        print(f" size:{order_size}")
                        print(f" limit price:{asset_price}")
                        print(f" {value['status']}")
                print("-" * 40)
        except Exception as e:
            print(f"Error printing order tracker: {e}")

    @staticmethod
    def print_profit_data( asset, profit_data, msg=None):
        try:
            print(f"<" + "-" * 80 + msg + "-" * 80 + ">")
            print(f"                Asset: {asset}, Balance: {profit_data['balance']}, Current Price: "
                  f"{profit_data['current_price']}, Current Value:  {profit_data['current_value']}, "
                  f"Cost Basis: {profit_data['cost_basis']}, Profit: ${profit_data['profit']} / "
                  f"{profit_data['profit_percentage']}% ")
            print(f"<" + "-" * 160 + "-" * len(msg)  + ">")
        except Exception as e:
            print(f"Error printing profit data: {e}")
