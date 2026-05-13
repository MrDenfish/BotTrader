
import json, os
import socket
import asyncio
import datetime
import pandas as pd

from aiohttp import web
from decimal import Decimal
from typing import Dict, Any, Optional, Union




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

    def write_jsonl(self,path: str, payload: Dict[str, Any]) -> None:
        """Appends a JSON object as a new line per each TP/SL computed.
        This will allow seeing the risk/reward, ATR-driven stop,
        fee/spread cushions etc """

        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, separators=(",", ":")) + "\n")

    def log_event_loop(self, name):
        """Logs the current event loop ID."""
        loop_id = id(asyncio.get_running_loop())
        self.logger.debug(f"� {name} is running in event loop: {loop_id}")

    def has_open_orders(self, trading_pair, open_orders):
        """
        Returns a tuple: (bool, order)
        - bool: whether an open order for the given trading_pair exists
        - order: the first matching order dict, or None
        """
        if not trading_pair or not open_orders:
            return False, None

        for order in open_orders.values():
            if isinstance(order, dict) and order.get("symbol") == trading_pair:
                return True, order

        return False, None

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
            return {str(k): self.convert_json_safe(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self.convert_json_safe(i) for i in obj]
        elif isinstance(obj, Decimal):
            return float(obj)
        elif isinstance(obj, (datetime.datetime, datetime.date)):
            return obj.isoformat()
        elif isinstance(obj, pd.DataFrame):
            return obj.to_dict(orient='records')
        return obj

    def get_passive_order_data(self, raw_entry: Union[str, dict]) -> Optional[dict]:
        """
        Parses a passive order's 'order_data' field into a dictionary.

        Args:
            raw_entry (Union[str, dict]): The passive order dictionary or its 'order_data' string.

        Returns:
            dict | None: Parsed order data or None if parsing fails.
        """
        try:
            # If passed the full order record dict
            if isinstance(raw_entry, dict):
                order_data = raw_entry.get("order_data")
                if isinstance(order_data, str):
                    return json.loads(order_data)
                elif isinstance(order_data, dict):
                    return order_data
            # If passed the raw JSON string directly
            elif isinstance(raw_entry, str):
                return json.loads(raw_entry)

        except (json.JSONDecodeError, TypeError) as e:
            print(f"⚠️ Failed to parse passive order data: {e}")

        return None

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

    def prepare_order_fees_and_decimals(self,details: dict, precision_data: tuple) -> tuple:
        base_deci, quote_deci, *_ = precision_data
        maker_fee = Decimal(details.get("maker_fee", "0.0015"))
        taker_fee = Decimal(details.get("taker_fee", "0.0025"))
        quote_increment = Decimal("1").scaleb(-quote_deci)
        return maker_fee, taker_fee, base_deci, quote_deci, quote_increment

    def assign_basic_order_fields(self,details: dict) -> dict:
        trading_pair = details.get("trading_pair", "")
        base_currency = details.get("asset", trading_pair.split("/")[0])
        quote_currency = trading_pair.split("/")[1] if "/" in trading_pair else "USD"

        return {
            "trading_pair": trading_pair,
            "base_currency": base_currency,
            "quote_currency": quote_currency,
            "usd_balance": Decimal(details.get("usd_balance", 0)),
            "usd_avail_balance": Decimal(details.get("usd_avail_balance", 0)),
            "base_avail_balance": Decimal(details.get("base_balance", 0)),
            "total_balance_crypto": Decimal(details.get("available_to_trade_crypto", 0)),
            "available_to_trade_crypto": Decimal(details.get("available_to_trade_crypto", 0)),
        }

    def initialize_order_amounts(self,side: str, fiat_amount: Decimal, crypto_amount: Decimal) -> tuple:
        if side.lower() == "buy":
            return Decimal(fiat_amount), Decimal("0")
        return Decimal("0"), Decimal(crypto_amount)