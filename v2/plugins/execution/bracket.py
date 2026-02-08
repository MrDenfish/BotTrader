"""Bracket execution manager — TP/SL attached orders.

Places a limit order with attached take-profit and stop-loss
(trigger_bracket_gtc configuration). Ported from
webhook/webhook_order_types.py ``process_limit_and_tp_sl_orders()``.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
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


@registry.plugin("execution", "bracket")
class BracketExecution(ExecutionManager):
    """TP/SL bracket order execution.

    Submits a limit order with an attached ``trigger_bracket_gtc``
    configuration that includes:
    - ``limit_price``: take-profit target
    - ``stop_trigger_price``: stop-loss trigger

    The signal must include ``tp_pct`` and ``sl_pct`` (or absolute
    ``tp_price`` / ``sl_price``) in its metadata.
    """

    name = "bracket"

    def __init__(self, event_bus=None, **kwargs: Any) -> None:
        self._bus = event_bus
        self._default_tp_pct = Decimal(str(kwargs.get("tp_pct", "0.015")))
        self._default_sl_pct = Decimal(str(kwargs.get("sl_pct", "0.03")))
        self._post_only: bool = kwargs.get("post_only", True)
        self._mode: str = kwargs.get("mode", "live")

    def configure(self, config: Any) -> None:
        if isinstance(config, dict):
            if "tp_pct" in config:
                self._default_tp_pct = Decimal(str(config["tp_pct"]))
            if "sl_pct" in config:
                self._default_sl_pct = Decimal(str(config["sl_pct"]))
            self._post_only = config.get("post_only", self._post_only)
            self._mode = config.get("mode", self._mode)

    async def execute_signal(
        self, signal: Signal, exchange: ExchangeAdapter
    ) -> Order | None:
        """Execute signal as a bracket order (limit + TP/SL).

        Returns the submitted Order on success, or None on failure.
        In backtest mode, returns None.
        """
        if self._mode == "backtest":
            return None

        side = Side.BUY if signal.direction == Direction.BUY else Side.SELL

        # Determine quantity
        qty = self._determine_qty(signal)
        if qty is None or qty <= 0:
            logger.warning("Cannot execute bracket: no valid quantity")
            return None

        # Determine entry price
        entry_price = self._determine_entry_price(signal, exchange)
        if entry_price is None:
            logger.warning("Cannot execute bracket: no entry price for %s", signal.symbol)
            return None

        # Calculate TP/SL prices
        tp_price, sl_price = self._calculate_tp_sl(side, entry_price, signal.metadata)

        # Build order with bracket metadata
        order_id = str(uuid.uuid4())
        order = Order(
            order_id=order_id,
            symbol=signal.symbol,
            side=side,
            order_type=OrderType.LIMIT,
            price=entry_price,
            qty=qty,
            status=OrderStatus.PENDING,
            timestamp=datetime.now(timezone.utc),
            metadata={
                "post_only": self._post_only,
                "tp_price": tp_price,
                "sl_price": sl_price,
                "signal_reason": signal.reason,
                **signal.metadata,
            },
        )

        result = await exchange.submit_order(order)

        if result.status == OrderStatus.REJECTED:
            logger.warning(
                "Bracket order rejected for %s: %s",
                signal.symbol,
                result.metadata.get("reject_reason", "unknown"),
            )
            return None

        logger.info(
            "Bracket order: %s %s %s @ %s (TP=%s, SL=%s)",
            side.value, qty, signal.symbol, entry_price, tp_price, sl_price,
        )
        return result

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

    def _determine_entry_price(self, signal: Signal, exchange: ExchangeAdapter) -> Decimal | None:
        """Get entry price from signal or market."""
        if signal.price is not None:
            return Decimal(str(signal.price))
        return None

    def _calculate_tp_sl(
        self,
        side: Side,
        entry_price: Decimal,
        metadata: dict,
    ) -> tuple[Decimal, Decimal]:
        """Calculate take-profit and stop-loss prices.

        Uses absolute prices from metadata if provided, otherwise
        calculates from percentage offsets.
        """
        # Check for absolute prices first
        tp_abs = metadata.get("tp_price")
        sl_abs = metadata.get("sl_price")
        if tp_abs is not None and sl_abs is not None:
            return Decimal(str(tp_abs)), Decimal(str(sl_abs))

        # Calculate from percentages
        tp_pct = Decimal(str(metadata.get("tp_pct", self._default_tp_pct)))
        sl_pct = Decimal(str(metadata.get("sl_pct", self._default_sl_pct)))

        if side == Side.BUY:
            tp_price = entry_price * (Decimal("1") + tp_pct)
            sl_price = entry_price * (Decimal("1") - sl_pct)
        else:
            tp_price = entry_price * (Decimal("1") - tp_pct)
            sl_price = entry_price * (Decimal("1") + sl_pct)

        return tp_price, sl_price
