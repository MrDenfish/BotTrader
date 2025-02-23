
from datetime import datetime, timezone
from dateutil import parser
import pandas as pd

import pytz

class DatesAndTimes:
    _instance = None

    @classmethod
    def get_instance(cls, logmanager):
        if cls._instance is None:
            cls._instance = cls(logmanager)
        return cls._instance

    def __init__(self, logmanager):
        self.log_manager = logmanager

    @staticmethod
    def _prepare_datetime(trade):
        """
    Ensure a trade object has a properly formatted `trade_time` and a default `record_type`.

    Args:
        trade (dict): The trade object to process.

    Returns:
        dict: The processed trade object with ensured datetime and record type.
    """

        # Ensure 'trade_time' is a datetime
        if 'trade_time' in trade:
            value = trade['trade_time']
            if isinstance(value, str):
                trade['trade_time'] = parser.isoparse(value)  # Convert string to datetime

        # Ensure 'record_type' is set
        if 'record_type' not in trade or trade['record_type'] is None:
            trade['record_type'] = 'trade'  # Default value

        return trade

    @staticmethod
    def standardize_timestamp(timestamp):
        if isinstance(timestamp, datetime):
            return timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=pytz.UTC)
        try:
            dt = parser.parse(timestamp)
            return dt.astimezone(pytz.UTC)
        except Exception as e:
            print(f"Error standardizing timestamp: {e}")
            return None

    def calculate_time_difference(self, time_string):
        try:
            time_format = "%Y-%m-%dT%H:%M:%S.%fZ"
            order_time = datetime.strptime(time_string, time_format)
            current_time1 = datetime.now(timezone.utc)
            current_time = datetime.utcnow()
            difference = current_time - order_time
            difference_in_minutes = difference.total_seconds() / 60
            return f"{int(difference_in_minutes)} minutes"
        except Exception as e:
            self.log_manager.error(f"Error calculating time difference: {e}", exc_info=True)
            return None

    @staticmethod
    def convert_timestamp(timestamp):
        try:
            # Assuming Unix timestamps are in milliseconds
            return pd.to_datetime(timestamp, unit='ms')
        except ValueError:
            # Fallback for standard datetime strings
            return pd.to_datetime(timestamp)

    def time_unix(self, last_timestamp):
        if not last_timestamp or last_timestamp == 0:
            # If the timestamp is None or explicitly zero, return 0
            return 0

        if isinstance(last_timestamp, datetime):
            # If last_timestamp is already a datetime object, convert directly to Unix time
            return int(last_timestamp.timestamp() * 1000)

        # Assume last_timestamp is a string if it's not a datetime object
        format_string = "%Y-%m-%d %H:%M:%S.%f"
        try:
            # Try to parse the string to a datetime object
            parsed_timestamp = datetime.strptime(last_timestamp, format_string)
            return int(parsed_timestamp.timestamp() * 1000)
        except ValueError as e:
            # Log error if parsing fails
            self.log_manager.error(f"Error parsing timestamp: {e}")
            return None
        except Exception as e:
            # Log unexpected errors
            self.log_manager.error(f"Error converting timestamp to unix: {e}", exc_info=True)
            return None