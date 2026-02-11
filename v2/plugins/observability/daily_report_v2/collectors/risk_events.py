"""Risk event collector — in-memory accumulator for runtime events."""

from __future__ import annotations

from ..models import RiskStats

MAX_EVENTS = 20


class RiskEventAccumulator:
    """Accumulates risk events from the event bus during runtime.

    For CLI mode (no event bus), returns empty defaults.
    """

    def __init__(self) -> None:
        self._vetoes = 0
        self._circuit_breaker_trips = 0
        self._rejections = 0
        self._events: list[str] = []

    def record_veto(self, reason: str) -> None:
        self._vetoes += 1
        self._add_event(f"veto: {reason}")

    def record_circuit_breaker(self, reason: str) -> None:
        self._circuit_breaker_trips += 1
        self._add_event(f"circuit_breaker: {reason}")

    def record_rejection(self, reason: str) -> None:
        self._rejections += 1
        self._add_event(f"rejection: {reason}")

    def snapshot(self) -> RiskStats:
        """Return a snapshot of the current stats."""
        return RiskStats(
            vetoes=self._vetoes,
            circuit_breaker_trips=self._circuit_breaker_trips,
            rejections=self._rejections,
            events=list(self._events),
        )

    def reset(self) -> None:
        """Reset all counters for the next period."""
        self._vetoes = 0
        self._circuit_breaker_trips = 0
        self._rejections = 0
        self._events.clear()

    def _add_event(self, description: str) -> None:
        if len(self._events) < MAX_EVENTS:
            self._events.append(description)
