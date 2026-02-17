"""Maker-only execution manager.

In backtest mode, the strategy handles fill simulation internally,
so this is a pass-through. In live/paper mode, this handles post-only
price adjustment, retries on rejection, and order submission.

Ported from webhook/webhook_order_types.py ``place_limit_order()``.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from typing import Any

from v2.core import registry
from v2.core.interfaces import ExecutionManager, ExchangeAdapter
from v2.core.types import (
    Direction,
    Order,
    OrderEvent,
    OrderStatus,
    OrderType,
    Side,
    Signal,
)

logger = logging.getLogger(__name__)


@registry.plugin("execution", "maker_only")
class MakerOnlyExecution(ExecutionManager):
    """Post-only limit order execution with price adjustment and retries.

    On each attempt:
    1. Fetch best bid/ask from exchange
    2. For BUY: price = min(ask * (1 - buffer), ask - min_buffer)
    3. For SELL: price = min(bid * (1 - buffer), bid - min_buffer)
    4. Submit with post_only=True
    5. On post-only rejection, increase buffer and retry

    Buffer starts at 0.1% and increases by 0.05% per retry (up to 3 retries).
    """

    name = "maker_only"

    def __init__(self, event_bus=None, **kwargs: Any) -> None:
        self._bus = event_bus
        self._max_retries: int = kwargs.get("max_retries", 3)
        self._post_only: bool = kwargs.get("post_only", True)
        self._initial_buffer_pct = Decimal(str(kwargs.get("initial_buffer_pct", "0.001")))
        self._buffer_increment = Decimal(str(kwargs.get("buffer_increment", "0.0005")))
        self._min_price_buffer = Decimal(str(kwargs.get("min_price_buffer", "0.0000001")))
        self._default_notional: Decimal | None = (
            Decimal(str(kwargs["default_notional"])) if "default_notional" in kwargs else None
        )
        self._mode: str = kwargs.get("mode", "live")

        # Stale order cancellation config
        self._stale_timeout_seconds = float(kwargs.get("stale_timeout_seconds", 300))
        self._cancel_drift_pct = float(kwargs.get("cancel_drift_pct", 0.005))

        # Tracked open orders: {order_id: {symbol, side, price, timestamp}}
        self._tracked_orders: dict[str, dict] = {}

    def configure(self, config: Any) -> None:
        if isinstance(config, dict):
            self._max_retries = config.get("max_retries", self._max_retries)
            self._post_only = config.get("post_only", self._post_only)
            self._mode = config.get("mode", self._mode)
            if "initial_buffer_pct" in config:
                self._initial_buffer_pct = Decimal(str(config["initial_buffer_pct"]))
            if "buffer_increment" in config:
                self._buffer_increment = Decimal(str(config["buffer_increment"]))
            if "default_notional" in config:
                self._default_notional = Decimal(str(config["default_notional"]))
            if "stale_timeout_seconds" in config:
                self._stale_timeout_seconds = float(config["stale_timeout_seconds"])
            if "cancel_drift_pct" in config:
                self._cancel_drift_pct = float(config["cancel_drift_pct"])

    async def execute_signal(
        self, signal: Signal, exchange: ExchangeAdapter
    ) -> Order | None:
        """Execute signal as a post-only limit order.

        Returns the submitted Order on success, or None if all retries fail.
        In backtest mode, returns None (strategies handle fills internally).
        """
        if self._mode == "backtest":
            return None

        side = Side.BUY if signal.direction == Direction.BUY else Side.SELL

        # Determine quantity
        qty = self._determine_qty(signal)
        if qty is None or qty <= 0:
            logger.warning("Cannot execute signal: no valid quantity (signal=%s)", signal)
            return None

        buffer_pct = self._initial_buffer_pct

        for attempt in range(1, self._max_retries + 1):
            # Get current best bid/ask
            bba = await self._get_bid_ask(exchange, signal.symbol)
            if bba is None:
                logger.warning("No bid/ask available for %s — skipping", signal.symbol)
                return None

            bid, ask = bba["bid"], bba["ask"]

            # Calculate post-only price
            price = self._calculate_post_only_price(side, bid, ask, buffer_pct)

            # Build order
            order_id = str(uuid.uuid4())
            order = Order(
                order_id=order_id,
                symbol=signal.symbol,
                side=side,
                order_type=OrderType.LIMIT,
                price=price,
                qty=qty,
                status=OrderStatus.PENDING,
                timestamp=datetime.now(timezone.utc),
                metadata={
                    "post_only": self._post_only,
                    "signal_reason": signal.reason,
                    "attempt": attempt,
                    **signal.metadata,
                },
            )

            result = await exchange.submit_order(order)

            if result.status == OrderStatus.REJECTED:
                reason = result.metadata.get("reject_reason", "")
                if self._is_post_only_rejection(reason, result.metadata):
                    logger.info(
                        "Post-only rejection on attempt %d/%d for %s — adjusting buffer",
                        attempt, self._max_retries, signal.symbol,
                    )
                    buffer_pct += self._buffer_increment
                    continue
                else:
                    logger.warning(
                        "Order rejected (non-post-only): %s — %s",
                        signal.symbol, reason,
                    )
                    if self._bus:
                        self._bus.publish(OrderEvent(order=result, event_type="rejected"))
                    return None
            else:
                logger.info(
                    "Order submitted: %s %s %s @ %s (attempt %d)",
                    side.value, qty, signal.symbol, price, attempt,
                )
                # Track OPEN orders for stale cancellation
                if result.status == OrderStatus.OPEN:
                    self._tracked_orders[result.order_id] = {
                        "symbol": signal.symbol,
                        "side": side,
                        "price": price,
                        "timestamp": time.monotonic(),
                    }
                return result

        logger.warning(
            "All %d attempts exhausted for %s %s",
            self._max_retries, side.value, signal.symbol,
        )
        if self._bus:
            exhausted_order = Order(
                order_id="exhausted",
                symbol=signal.symbol,
                side=side,
                order_type=OrderType.LIMIT,
                price=Decimal("0"),
                qty=qty,
                status=OrderStatus.REJECTED,
                timestamp=datetime.now(timezone.utc),
                metadata={"reason": "max_retries_exhausted", "attempts": self._max_retries},
            )
            self._bus.publish(OrderEvent(order=exhausted_order, event_type="rejected"))
        return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _determine_qty(self, signal: Signal) -> Decimal | None:
        """Determine order quantity from signal.

        Priority: signal.qty > signal.notional > default_notional.
        """
        if signal.qty is not None:
            return Decimal(str(signal.qty))
        notional = signal.notional
        if notional is None and self._default_notional is not None:
            notional = float(self._default_notional)
        if notional is not None and signal.price is not None and signal.price > 0:
            return Decimal(str(notional)) / Decimal(str(signal.price))
        return None

    @staticmethod
    def _calculate_post_only_price(
        side: Side, bid: Decimal, ask: Decimal, buffer_pct: Decimal,
    ) -> Decimal:
        """Calculate price that won't cross the spread (post-only safe)."""
        min_buffer = Decimal("0.0000001")
        if side == Side.BUY:
            # Place below the ask
            return min(ask * (Decimal("1") - buffer_pct), ask - min_buffer)
        else:
            # Place above the bid
            return max(bid * (Decimal("1") + buffer_pct), bid + min_buffer)

    @staticmethod
    def _is_post_only_rejection(reason: str, metadata: dict) -> bool:
        """Check if rejection is due to post-only crossing the book."""
        reason_lower = reason.lower()
        msg = metadata.get("reject_message", "").lower()
        post_only_keywords = ["post-only", "post_only", "priced below", "match existing", "invalid_limit_price"]
        return any(kw in reason_lower or kw in msg for kw in post_only_keywords)

    @staticmethod
    async def _get_bid_ask(exchange: ExchangeAdapter, symbol: str) -> dict[str, Decimal] | None:
        """Get best bid/ask from exchange."""
        # Use get_best_bid_ask if available (CoinbaseExchange), else fallback to ticker
        if hasattr(exchange, "get_best_bid_ask"):
            result = await exchange.get_best_bid_ask(symbol)
            if result.get("bid") and result.get("ask"):
                return result

        ticker = await exchange.get_ticker(symbol)
        if ticker.bid and ticker.ask:
            return {
                "bid": Decimal(str(ticker.bid)),
                "ask": Decimal(str(ticker.ask)),
            }
        return None

    # ------------------------------------------------------------------
    # Stale order tracking and cancellation
    # ------------------------------------------------------------------

    def on_fill(self, order_id: str) -> None:
        """Remove filled order from tracking."""
        self._tracked_orders.pop(order_id, None)

    def on_order_event(self, order: Order) -> None:
        """Remove orders that are no longer open."""
        if order.status in (OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED):
            self._tracked_orders.pop(order.order_id, None)

    async def check_stale_orders(self, exchange: ExchangeAdapter) -> None:
        """Cancel tracked orders that have exceeded timeout and price has drifted.

        Called periodically from app.py (throttled to every 30s).
        """
        if not self._tracked_orders:
            return

        now = time.monotonic()
        to_cancel: list[str] = []

        for order_id, info in list(self._tracked_orders.items()):
            age = now - info["timestamp"]
            if age < self._stale_timeout_seconds:
                continue

            # Get current bid/ask
            bba = await self._get_bid_ask(exchange, info["symbol"])
            if bba is None:
                continue

            order_price = info["price"]
            side = info["side"]

            # Check price drift
            if side == Side.BUY:
                # BUY order is stale if ask has risen beyond drift threshold
                ask = float(bba["ask"])
                drift = (ask - float(order_price)) / float(order_price)
                if drift > self._cancel_drift_pct:
                    to_cancel.append(order_id)
                    logger.info(
                        "Stale BUY order %s for %s: age=%.0fs, drift=+%.2f%%",
                        order_id[:8], info["symbol"], age, drift * 100,
                    )
            else:
                # SELL order is stale if bid has dropped beyond drift threshold
                bid = float(bba["bid"])
                drift = (float(order_price) - bid) / float(order_price)
                if drift > self._cancel_drift_pct:
                    to_cancel.append(order_id)
                    logger.info(
                        "Stale SELL order %s for %s: age=%.0fs, drift=+%.2f%%",
                        order_id[:8], info["symbol"], age, drift * 100,
                    )

        for order_id in to_cancel:
            try:
                cancelled = await exchange.cancel_order(order_id)
                if cancelled:
                    info = self._tracked_orders.pop(order_id, {})
                    logger.warning(
                        "Cancelled stale order %s (%s %s)",
                        order_id[:8], info.get("side", "?"), info.get("symbol", "?"),
                    )
                    if self._bus:
                        self._bus.publish(OrderEvent(
                            order=Order(
                                order_id=order_id,
                                symbol=info.get("symbol", ""),
                                side=info.get("side", Side.BUY),
                                order_type=OrderType.LIMIT,
                                price=info.get("price", Decimal("0")),
                                qty=Decimal("0"),
                                status=OrderStatus.CANCELLED,
                                timestamp=datetime.now(timezone.utc),
                                metadata={"reason": "stale_order_cancelled"},
                            ),
                            event_type="cancelled",
                        ))
            except Exception:
                logger.exception("Failed to cancel stale order %s", order_id[:8])
