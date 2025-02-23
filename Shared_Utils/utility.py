
from decimal import Decimal
import pandas as pd
import socket

class SharedUtility:
    _instance = None  # Singleton instance

    @classmethod
    def get_instance(cls, logmanager):
        """ Ensures only one instance of SharedUtility is created. """
        if cls._instance is None:
            cls._instance = cls(logmanager)
        return cls._instance

    def __init__(self, logmanager):
        self.log_manager = logmanager


    @staticmethod
    def get_my_ip_address():
        hostname = socket.gethostname()
        ip_address = socket.gethostbyname(hostname)
        return ip_address

    @staticmethod
    def string_default(obj):
        """used to format json.dumps."""
        if isinstance(obj, Decimal):
            return str(obj)
        raise TypeError

    def refresh_authentication(self):
        try:
            # Reload the configuration
            self.bot_config.reload_config()

            # Fetch new API key and secret from BotConfig
            new_api_key = self.bot_config.api_key
            new_api_secret = self.bot_config.api_secret

            # Update the exchange client with new credentials
            if new_api_key and new_api_secret:
                self.exchange.apiKey = new_api_key
                self.exchange.secret = new_api_secret

                # Log the refresh action
                if self.log_manager and hasattr(self.log_manager, 'webhook_logger'):
                    self.log_manager.info("Authentication refreshed.")
                else:
                    print("Authentication refreshed.")  # Fallback logging
            else:
                raise ValueError("API key or secret is missing.")
        except Exception as e:
            error_message = f"Failed to refresh authentication: {e}"
            if self.log_manager and hasattr(self.log_manager, 'webhook_logger'):
                self.log_manager.error(error_message)
            else:
                print(error_message)  # Fallback logging


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


