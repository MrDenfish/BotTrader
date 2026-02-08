"""Structured logging observer.

Subscribes to all events and logs them in a consistent format.
"""

from __future__ import annotations

import logging
from typing import Any

from v2.core import registry
from v2.core.interfaces import Observer
from v2.core.types import (
    CandleEvent,
    FillEvent,
    OrderEvent,
    PositionEvent,
    RiskEvent,
    SignalEvent,
)

logger = logging.getLogger("v2.observer")


@registry.plugin("observer", "structured_log")
class StructuredLogObserver(Observer):
    """Logs all events at appropriate levels."""

    name = "structured_log"

    def __init__(self, event_bus=None, level: str = "INFO", **kwargs: Any) -> None:
        self._level = getattr(logging, level.upper(), logging.INFO)

    def on_event(self, event: Any) -> None:
        if isinstance(event, SignalEvent):
            sig = event.signal
            logger.log(
                self._level,
                "SIGNAL %s %s @ %s | reason=%s strategy=%s",
                sig.direction.value.upper(), sig.symbol, sig.price,
                sig.reason, event.strategy_name,
            )
        elif isinstance(event, FillEvent):
            fill = event.fill
            logger.log(
                self._level,
                "FILL %s %s %s @ %s qty=%s fee=%s",
                fill.fill_id, fill.side.value.upper(), fill.symbol,
                fill.price, fill.qty, fill.fee,
            )
        elif isinstance(event, OrderEvent):
            order = event.order
            logger.log(
                self._level,
                "ORDER %s %s %s %s @ %s qty=%s status=%s",
                event.event_type.upper(), order.order_id,
                order.side.value.upper(), order.symbol,
                order.price, order.qty, order.status.value,
            )
        elif isinstance(event, RiskEvent):
            logger.warning(
                "RISK %s: %s", event.event_type, event.reason,
            )
        elif isinstance(event, PositionEvent):
            pos = event.position
            logger.log(
                self._level,
                "POSITION %s %s qty=%s avg_entry=%s",
                event.event_type.upper(), pos.symbol, pos.qty,
                pos.avg_entry_price,
            )
        # Skip CandleEvents (too noisy) unless DEBUG
        elif isinstance(event, CandleEvent):
            logger.debug(
                "CANDLE %s %s close=%s",
                event.candle.symbol, event.candle.timestamp,
                event.candle.close,
            )
