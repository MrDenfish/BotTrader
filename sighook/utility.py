
from decimal import Decimal
import socket
import time
import datetime

import pandas as pd


class SenderUtils:
    _instance_count = 0

    def __init__(self, logmanager, exchange, ccxt_api):
        # self.id = SenderUtils._instance_count
        # SenderUtils._instance_count += 1
        # print(f"SenderUtils Instance ID: {self.id}")
        self.log_manager = logmanager
        self.exchange = exchange
        self.ccxt_exceptions = ccxt_api

    def get_balance(self, coin):
        """Get the balance of a coin in the exchange account."""
        try:
            balance = self.ccxt_exceptions.ccxt_api_call(lambda: self.exchange.fetch_balance())

            coin_balance = Decimal(balance[coin]['total']) if coin in balance else Decimal('0.0')
            usd_balance = Decimal(balance['USD']['total']) if 'USD' in balance else Decimal('0.0')

        except Exception as e:
            self.log_manager.sighook_logger.error(f'SenderUtils get_balance: Exception occurred during  {e}')
            coin_balance = Decimal('0.0')
            usd_balance = Decimal('0.0')

        return coin_balance, usd_balance

    @staticmethod
    def print_elapsed_time(start_time=None, func_name=None):
        """Calculate elapsed time and print it to the console."""

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
            print(f'Elapsed time for {func_name}: {formatted_time} (hh:mm:ss)')
            start_time = None # reset start time
            return elapsed_seconds

    @staticmethod
    def time_unix(last_timestamp):
        if last_timestamp != 0:
            format_string = "%Y-%m-%d %H:%M:%S.%f"
            last_timestamp = datetime.datetime.strptime(last_timestamp, format_string)
            return int(last_timestamp.timestamp() * 1000)
        else:
            return 0

    @staticmethod
    def convert_timestamp(timestamp):
        try:
            # Assuming Unix timestamps are in milliseconds
            return pd.to_datetime(timestamp, unit='ms')
        except ValueError:
            # Fallback for standard datetime strings
            return pd.to_datetime(timestamp)

    @staticmethod
    def find_price(product_id, usd_pairs):
        """
            Find the price of a product given its ID.

            Parameters:
            - product_id (str): The ID of the product to search for.
            - usd_pairs (list): A list of product pairs in USD.

            Returns:
            - float: The price of the product. Returns None if the product ID is not found.
            """
        for pair in usd_pairs:
            if pair['id'] == product_id:
                return pair['price']
        # If the product ID is not found, return None or raise an exception.
        return None

    @staticmethod
    def percentage_difference(a, b):
        if b == 0:
            return float('inf')  # If b is 0, then it would cause a division by zero error
        return ((a - b) / b) * 100

    @staticmethod
    def format_open_orders(open_orders: list) -> pd.DataFrame:
        """
        Format the open orders data received from the ccxt api(Coinbase Cloud) call.

        Parameters:


        Returns:
        - list: A list of dictionaries containing the required data.
        """

        data_to_load = [{
            'order_id': order['id'],
            'product_id': order['info']['product_id'],
            'side': order['info']['side'],
            'size': order['amount'],
            'amount': order['price'],
            'filled': order['filled'],
            'remaining': order['remaining']
        } for order in open_orders]
        df = pd.DataFrame(data_to_load)
        return df

    def get_my_ip_address(self):  # rarely used except when exception occurs for debugging
        hostname = socket.gethostname()
        ip_address = socket.gethostbyname(hostname)
        self.log_manager.sighook_logger.info(f"Hostname: {hostname}")
        return ip_address
