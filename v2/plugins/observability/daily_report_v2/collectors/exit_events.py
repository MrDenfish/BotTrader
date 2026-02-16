"""Exit event accumulator — in-memory tracker for exit manager activity."""

from __future__ import annotations

from datetime import datetime, timezone

from v2.core.types import RiskEvent

from ..models import ExitEventDetail, ExitManagerStats

MAX_EVENTS = 30


class ExitEventAccumulator:
    """Accumulates exit manager events from the event bus during runtime."""

    def __init__(self) -> None:
        self._hard_stops = 0
        self._soft_stops = 0
        self._trailing_stops = 0
        self._trailing_activations = 0
        self._events: list[ExitEventDetail] = []

    def record_exit(self, event: RiskEvent) -> None:
        """Record an exit trigger event (hard_stop, soft_stop, trailing_stop)."""
        reason = event.reason
        meta = event.metadata

        if reason == "hard_stop":
            self._hard_stops += 1
        elif reason == "soft_stop":
            self._soft_stops += 1
        elif reason == "trailing_stop":
            self._trailing_stops += 1

        if len(self._events) < MAX_EVENTS:
            self._events.append(ExitEventDetail(
                timestamp=datetime.now(timezone.utc),
                symbol=meta.get("symbol", ""),
                reason=reason,
                price=meta.get("price", 0.0),
                pnl_pct=meta.get("pnl_pct", 0.0),
                peak_price=meta.get("peak_price"),
            ))

    def record_trailing_activation(self, event: RiskEvent) -> None:
        """Record a trailing stop activation (not a trigger)."""
        self._trailing_activations += 1

    def snapshot(self) -> ExitManagerStats:
        """Return a snapshot of the current stats."""
        total = self._hard_stops + self._soft_stops + self._trailing_stops
        return ExitManagerStats(
            hard_stops=self._hard_stops,
            soft_stops=self._soft_stops,
            trailing_stops=self._trailing_stops,
            trailing_activations=self._trailing_activations,
            total_exits=total,
            events=list(self._events),
        )

    def reset(self) -> None:
        """Reset all counters for the next period."""
        self._hard_stops = 0
        self._soft_stops = 0
        self._trailing_stops = 0
        self._trailing_activations = 0
        self._events.clear()
