
import asyncio
import datetime
import socket
from decimal import Decimal

import pandas as pd
from aiohttp import web


class SharedUtility:
    _instance = None  # Singleton instance

    @classmethod
    def get_instance(cls, logger_manager):
        """ Ensures only one instance of SharedUtility is created. """
        if cls._instance is None:
            cls._instance = cls(logger_manager)
        return cls._instance

    def __init__(self, logger_manager):
        self.logger_manager = logger_manager  # 🙂
        if logger_manager.loggers['shared_logger'].name == 'shared_logger':  # 🙂
            self.logger = logger_manager.loggers['shared_logger']



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

    @staticmethod
    async def get_event_loop():
        """Returns the running event loop or creates a new one if none exists."""
        try:
            return asyncio.get_running_loop()
        except RuntimeError:  # No running loop found
            return asyncio.new_event_loop()

    def log_event_loop(self, name):
        """Logs the current event loop ID."""
        loop_id = id(asyncio.get_running_loop())
        self.logger.debug(f"� {name} is running in event loop: {loop_id}")

    # def refresh_authentication(self):
    #     try:
    #         # Reload the configuration
    #         self.bot_config.reload_config()
    #
    #         # Fetch new API key and secret from BotConfig
    #         new_api_key = self.bot_config.api_key
    #         new_api_secret = self.bot_config.api_secret
    #
    #         # Update the exchange client with new credentials
    #         if new_api_key and new_api_secret:
    #             self.exchange.apiKey = new_api_key
    #             self.exchange.secret = new_api_secret
    #
    #             # Log the refresh action
    #             if self.logger and hasattr(self.logger, 'webhook_logger'):
    #                 self.logger.info("Authentication refreshed.")
    #             else:
    #                 print("Authentication refreshed.")  # Fallback logging
    #         else:
    #             raise ValueError("API key or secret is missing.")
    #     except Exception as e:
    #         error_message = f"❌ Failed to refresh authentication: {e}"
    #         if self.logger and hasattr(self.logger, 'webhook_logger'):
    #             self.logger.error(error_message)
    #         else:
    #             print(error_message)  # Fallback logging


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

    def convert_json_safe(self, obj):
        """Recursively convert complex types (Decimal, datetime, DataFrame) to JSON-safe formats."""
        if isinstance(obj, dict):
            return {k: self.convert_json_safe(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self.convert_json_safe(i) for i in obj]
        elif isinstance(obj, Decimal):
            return float(obj)
        elif isinstance(obj, (datetime.datetime, datetime.date)):
            return obj.isoformat()
        elif isinstance(obj, pd.DataFrame):
            return obj.to_dict(orient='records')  # or 'split', 'index', etc.
        return obj

    def safe_json_response(self, data: dict, status: int = 200) -> web.Response:
        """Wrapper for web.json_response that safely handles Decimal values."""
        safe_data = self.convert_json_safe(data)
        return web.json_response(safe_data, status=status)

    def pretty_summary(self, source) -> str:
        """
        Return a concise and user-friendly order summary.
        """
        lines = [
            f"� Order Summary [{source.__class__.__name__}]",
            f"Pair:         {source.trading_pair}",
            f"Side:         {source.side.upper()}  | Type: {source.type.upper()}",
            f"Amount:       {source.order_amount_fiat} {source.base_currency}",
            f"USD Balance:  ${source.usd_avail_balance} available",
            f"Price:        ${source.adjusted_price} | Size: {source.adjusted_size} {source.base_currency}",
            f"Stop-Loss:    ${source.stop_loss_price} | Take-Profit: ${source.take_profit_price}",
            f"Fees:         Maker: {source.maker} | Taker: {source.taker}",
            f"Spread:       {source.spread}",
            f"Open Orders:  {len(source.open_orders) if isinstance(source.open_orders, pd.DataFrame) else 'N/A'}",
            f"Status:       {source.status}"
        ]
        return "\n".join(lines)
