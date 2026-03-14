"""Exit event accumulator — in-memory tracker for exit manager activity.

Also provides ``collect_exit_stats_from_db()`` to reconstruct exit counts
from v2_fills metadata, so stats survive container restarts.
"""

from __future__ import annotations

from datetime import datetime, timezone

import asyncpg

from v2.core.types import RiskEvent

from ..models import ExitEventDetail, ExitManagerStats

MAX_EVENTS = 30


class ExitEventAccumulator:
    """Accumulates exit manager events from the event bus during runtime."""

    def __init__(self) -> None:
        self._hard_stops = 0
        self._soft_stops = 0
        self._trailing_stops = 0
        self._stale_exits = 0
        self._signal_exits = 0
        self._trailing_activations = 0
        self._events: list[ExitEventDetail] = []

    def record_exit(self, event: RiskEvent) -> None:
        """Record an exit trigger event (hard_stop, soft_stop, trailing_stop, stale_exit)."""
        reason = event.reason
        meta = event.metadata

        if reason == "hard_stop":
            self._hard_stops += 1
        elif reason == "soft_stop":
            self._soft_stops += 1
        elif reason == "trailing_stop":
            self._trailing_stops += 1
        elif reason == "stale_exit":
            self._stale_exits += 1
        elif reason == "signal_exit":
            self._signal_exits += 1

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
        total = self._hard_stops + self._soft_stops + self._trailing_stops + self._stale_exits + self._signal_exits
        return ExitManagerStats(
            hard_stops=self._hard_stops,
            soft_stops=self._soft_stops,
            trailing_stops=self._trailing_stops,
            stale_exits=self._stale_exits,
            signal_exits=self._signal_exits,
            trailing_activations=self._trailing_activations,
            total_exits=total,
            events=list(self._events),
        )

    def reset(self) -> None:
        """Reset all counters for the next period."""
        self._hard_stops = 0
        self._soft_stops = 0
        self._trailing_stops = 0
        self._stale_exits = 0
        self._signal_exits = 0
        self._trailing_activations = 0
        self._events.clear()


async def collect_exit_stats_from_db(
    pool: asyncpg.Pool,
    start: datetime,
    end: datetime,
    exchange: str = "",
) -> ExitManagerStats:
    """Reconstruct exit manager stats from v2_fills metadata.

    Sell fills store the exit reason in ``metadata->>'signal_reason'``.
    This lets us recover hard_stop/soft_stop/trailing_stop/signal_exit
    counts even after a container restart.
    """
    params: list = [start, end]
    exchange_clause = ""
    if exchange:
        params.append(exchange)
        exchange_clause = f" AND exchange = ${len(params)}"

    rows = await pool.fetch(
        f"""
        SELECT metadata->>'signal_reason' AS reason, COUNT(*)::int AS cnt
        FROM v2_fills
        WHERE side = 'sell'
          AND timestamp >= $1 AND timestamp < $2{exchange_clause}
          AND metadata->>'signal_reason' IS NOT NULL
        GROUP BY metadata->>'signal_reason'
        """,
        *params,
    )

    hard = soft = trailing = stale = signal = 0
    for row in rows:
        reason = row["reason"]
        cnt = row["cnt"]
        if reason == "hard_stop":
            hard = cnt
        elif reason == "soft_stop":
            soft = cnt
        elif reason == "trailing_stop":
            trailing = cnt
        elif reason == "stale_exit":
            stale = cnt
        elif reason == "signal_exit":
            signal = cnt

    total = hard + soft + trailing + stale + signal
    return ExitManagerStats(
        hard_stops=hard,
        soft_stops=soft,
        trailing_stops=trailing,
        stale_exits=stale,
        signal_exits=signal,
        trailing_activations=0,  # not stored in fills — only in-memory
        total_exits=total,
    )


def merge_exit_stats(
    in_memory: ExitManagerStats,
    from_db: ExitManagerStats,
) -> ExitManagerStats:
    """Merge in-memory and DB exit stats, taking the max of each counter.

    In-memory has trailing_activations (not in DB) and events detail.
    DB has the authoritative exit counts (survives restarts).
    """
    return ExitManagerStats(
        hard_stops=max(in_memory.hard_stops, from_db.hard_stops),
        soft_stops=max(in_memory.soft_stops, from_db.soft_stops),
        trailing_stops=max(in_memory.trailing_stops, from_db.trailing_stops),
        stale_exits=max(in_memory.stale_exits, from_db.stale_exits),
        signal_exits=max(in_memory.signal_exits, from_db.signal_exits),
        trailing_activations=in_memory.trailing_activations,  # only in-memory
        total_exits=max(in_memory.total_exits, from_db.total_exits),
        events=in_memory.events,  # detail events only from in-memory
    )
