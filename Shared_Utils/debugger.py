import os
import sys
import pandas as pd

class Debugging:
    @staticmethod
    def debug_code(current_file, frames):
        custom_functions = [
            frame.function for frame in frames if os.path.abspath(frame.filename) == current_file
        ]
        msg = (f"Debugging stack for {current_file}: {custom_functions}")
        return msg

    def print_order_tracker(self, order_tracker, func_name):
        """
        Prints the order_tracker in a tabular format for debugging purposes.

        Args:
            order_tracker: The order tracker to validate and print.
            func_name (str): Name of the function for context.
        """
        try:
            is_valid, message = self.validate_order_tracker(order_tracker)

            if is_valid:
                # Extract relevant fields and print
                if isinstance(order_tracker, list):
                    table_data = [
                        {
                            'Order ID': order.get('id'),
                            'Symbol': order.get('symbol'),
                            'Side': order.get('side'),
                            'Status': order.get('status'),
                            'Amount': order.get('amount'),
                            'Filled': order.get('filled'),
                            'Remaining': order.get('remaining'),
                            'Stop Price': order.get('stopPrice'),
                            'Limit Price': order.get('price'),
                            'Created Time': order.get('datetime'),
                            'Trigger Status': order.get('info', {}).get('trigger_status'),
                        }
                        for order in order_tracker
                    ]
                    df = pd.DataFrame(table_data)
                    print(f"Order Tracker for {func_name}:")
                    print(df.to_string(index=False))
                elif isinstance(order_tracker, pd.DataFrame):
                    print(f"Order Tracker DataFrame for {func_name}:")
                    print(order_tracker.to_string(index=False))
            else:
                print(f"Validation failed in  : {func_name}: {message}")

        except Exception as e:
            print(f"Error printing order_tracker in {func_name}. Exception: {e}")
            self.log_manager.error({e}, exc_info=True)

    def validate_order_tracker(self, order_tracker):
        """
        Validates the type and structure of order_tracker.

        Args:
            order_tracker: The object to validate.

        Returns:
            tuple: (is_valid, message), where:
                is_valid (bool): True if order_tracker is valid and non-empty.
                message (str): Description of the issue or success message.
        """
        if order_tracker is None:
            return False, "order_tracker is None."

        if isinstance(order_tracker, (list, dict)):
            if len(order_tracker) == 0:

                return False, "order_tracker is an empty list or dictionary."
            return True, "order_tracker is a valid non-empty list or dictionary."

        if isinstance(order_tracker, pd.DataFrame):
            if order_tracker.empty:
                return False, "order_tracker is an empty DataFrame."
            return True, "order_tracker is a valid non-empty DataFrame."

        return False, f"order_tracker is of invalid type: {type(order_tracker)}"