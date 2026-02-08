"""Maker-only execution manager.

In backtest mode, the strategy handles fill simulation internally,
so this is a pass-through. In live/paper mode, this handles post-only
price adjustment, retries on rejection, and order submission.

Ported from webhook/webhook_order_types.py ``place_limit_order()``.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from typing import Any

from v2.core import registry
from v2.core.interfaces import ExecutionManager, ExchangeAdapter
from v2.core.types import (
    Direction,
    Order,
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
        self._mode: str = kwargs.get("mode", "live")

    def configure(self, config: Any) -> None:
        if isinstance(config, dict):
            self._max_retries = config.get("max_retries", self._max_retries)
            self._post_only = config.get("post_only", self._post_only)
            self._mode = config.get("mode", self._mode)
            if "initial_buffer_pct" in config:
                self._initial_buffer_pct = Decimal(str(config["initial_buffer_pct"]))
            if "buffer_increment" in config:
                self._buffer_increment = Decimal(str(config["buffer_increment"]))

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
                    return None
            else:
                logger.info(
                    "Order submitted: %s %s %s @ %s (attempt %d)",
                    side.value, qty, signal.symbol, price, attempt,
                )
                return result

        logger.warning(
            "All %d attempts exhausted for %s %s",
            self._max_retries, side.value, signal.symbol,
        )
        return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _determine_qty(self, signal: Signal) -> Decimal | None:
        """Determine order quantity from signal."""
        if signal.qty is not None:
            return Decimal(str(signal.qty))
        if signal.notional is not None and signal.price is not None and signal.price > 0:
            return Decimal(str(signal.notional)) / Decimal(str(signal.price))
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
