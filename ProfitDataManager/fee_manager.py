from decimal import Decimal
from collections import defaultdict
from datetime import datetime, timedelta, timezone




class FeeMonitor:
    def __init__(self, logger, fee_rates: dict = None):
        self.logger = logger
        self.fee_rates = fee_rates or {
            "maker": Decimal("0.004"),
            "taker": Decimal("0.006")
        }

        # Tracks total fees by symbol and type
        self.fee_history = defaultdict(lambda: {"maker": Decimal("0"), "taker": Decimal("0")})
        self.order_fee_log = {}  # order_id -> {fee, fee_type, timestamp}

    def log_fee(self, order_id: str, symbol: str, fee: Decimal, fee_type: str):
        """
        Log an individual order fee.
        """
        self.fee_history[symbol][fee_type] += fee
        self.order_fee_log[order_id] = {
            "symbol": symbol,
            "fee": fee,
            "fee_type": fee_type,
            "timestamp": datetime.now(timezone.utc)
        }
        self.logger.debug(f"💸 Logged {fee_type} fee for {symbol}: ${fee:.4f} on order {order_id}")

    def get_symbol_fee_summary(self, symbol: str) -> dict:
        """Return total maker/taker fees for a given symbol."""
        return self.fee_history.get(symbol, {"maker": Decimal("0"), "taker": Decimal("0")})

    def get_total_fees_usd(self) -> Decimal:
        """Return total USD fees across all symbols."""
        return sum(
            fee_data["maker"] + fee_data["taker"]
            for fee_data in self.fee_history.values()
        )

    def is_fee_acceptable(self, quote_value: Decimal, estimated_profit: Decimal) -> bool:
        """
        Determine if a trade's fee impact is reasonable vs expected profit.
        """
        taker_fee = quote_value * self.fee_rates["taker"]
        return estimated_profit > taker_fee

    def classify_order_fee_type(self, order_data: dict, best_bid: Decimal, best_ask: Decimal) -> str:
        """
        Determine whether an order is likely to be maker or taker.
        """
        price = Decimal(order_data.get("price"))
        side = order_data.get("side")

        if side == "buy" and price < best_ask:
            return "maker"
        elif side == "sell" and price > best_bid:
            return "maker"
        return "taker"

    def prune_old_logs(self, older_than_days: int = 7):
        """
        Remove old logs to keep memory use in check.
        """
        threshold = datetime.now(timezone.utc) - timedelta(days=older_than_days)
        self.order_fee_log = {
            oid: data for oid, data in self.order_fee_log.items()
            if data["timestamp"] > threshold
        }
        self.logger.debug(f"🧹 Pruned old fee logs older than {older_than_days} days")

