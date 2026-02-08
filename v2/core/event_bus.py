"""Typed publish/subscribe event system.

Plugins communicate exclusively through events. No plugin holds a direct
reference to another plugin — all interaction goes through the EventBus.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any, Callable

logger = logging.getLogger(__name__)


class EventBus:
    """Typed publish/subscribe event bus.

    Usage::

        bus = EventBus()
        bus.subscribe(CandleEvent, my_handler)
        bus.publish(CandleEvent(candle=...))
    """

    def __init__(self) -> None:
        self._handlers: dict[type, list[Callable]] = defaultdict(list)
        self._global_handlers: list[Callable] = []

    # ------------------------------------------------------------------
    # Subscribe
    # ------------------------------------------------------------------

    def subscribe(self, event_type: type, handler: Callable) -> None:
        """Register *handler* to be called when *event_type* is published."""
        self._handlers[event_type].append(handler)

    def subscribe_all(self, handler: Callable) -> None:
        """Register *handler* to be called for every event."""
        self._global_handlers.append(handler)

    def unsubscribe(self, event_type: type, handler: Callable) -> None:
        """Remove *handler* from *event_type* subscribers."""
        try:
            self._handlers[event_type].remove(handler)
        except ValueError:
            pass

    def unsubscribe_all(self, handler: Callable) -> None:
        """Remove a global handler."""
        try:
            self._global_handlers.remove(handler)
        except ValueError:
            pass

    # ------------------------------------------------------------------
    # Publish (synchronous)
    # ------------------------------------------------------------------

    def publish(self, event: Any) -> None:
        """Publish *event* synchronously to all subscribers.

        Handlers are called in subscription order. If a handler raises,
        the exception is logged and remaining handlers still execute.
        """
        event_type = type(event)
        for handler in self._handlers.get(event_type, []):
            self._safe_call(handler, event)
        for handler in self._global_handlers:
            self._safe_call(handler, event)

    # ------------------------------------------------------------------
    # Publish (async)
    # ------------------------------------------------------------------

    async def publish_async(self, event: Any) -> None:
        """Publish *event* to all subscribers, awaiting async handlers."""
        event_type = type(event)
        for handler in self._handlers.get(event_type, []):
            await self._safe_call_async(handler, event)
        for handler in self._global_handlers:
            await self._safe_call_async(handler, event)

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def handler_count(self, event_type: type | None = None) -> int:
        """Return the number of registered handlers.

        If *event_type* is ``None``, return the total across all types
        (including global handlers).
        """
        if event_type is not None:
            return len(self._handlers.get(event_type, []))
        total = sum(len(h) for h in self._handlers.values())
        total += len(self._global_handlers)
        return total

    def clear(self) -> None:
        """Remove all handlers."""
        self._handlers.clear()
        self._global_handlers.clear()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _safe_call(handler: Callable, event: Any) -> None:
        try:
            handler(event)
        except Exception:
            logger.exception(
                "Handler %s failed for event %s",
                getattr(handler, "__qualname__", handler),
                type(event).__name__,
            )

    @staticmethod
    async def _safe_call_async(handler: Callable, event: Any) -> None:
        try:
            result = handler(event)
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            logger.exception(
                "Async handler %s failed for event %s",
                getattr(handler, "__qualname__", handler),
                type(event).__name__,
            )
