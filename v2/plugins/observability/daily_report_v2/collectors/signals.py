"""Signal statistics collector — reads from JSONL signal log."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from ..models import SignalStats

logger = logging.getLogger(__name__)


def collect_signals(
    log_path: str,
    start: datetime,
    end: datetime,
) -> SignalStats:
    """Parse JSONL signal log and compute stats for the period."""
    stats = SignalStats()
    by_symbol: dict[str, dict[str, int]] = {}

    path = Path(log_path)
    if not path.exists():
        logger.warning("Signal log not found: %s", log_path)
        return stats

    start_ts = start.timestamp()
    end_ts = end.timestamp()

    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue

        ts = _extract_timestamp(entry)
        if ts is None or ts < start_ts or ts >= end_ts:
            continue

        action = entry.get("action", "").upper()
        symbol = entry.get("symbol", "UNKNOWN")

        stats.total += 1
        if action == "BUY":
            stats.buy_count += 1
        elif action == "SELL":
            stats.sell_count += 1

        if symbol not in by_symbol:
            by_symbol[symbol] = {"BUY": 0, "SELL": 0}
        if action in ("BUY", "SELL"):
            by_symbol[symbol][action] += 1

    stats.by_symbol = by_symbol
    return stats


def parse_signals(
    log_path: str,
    start: datetime,
    end: datetime,
) -> list[dict]:
    """Parse JSONL and return raw signal dicts for the period.

    Used by the comparison collector for matching.
    """
    signals = []
    path = Path(log_path)
    if not path.exists():
        return signals

    start_ts = start.timestamp()
    end_ts = end.timestamp()

    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue

        ts = _extract_timestamp(entry)
        if ts is None or ts < start_ts or ts >= end_ts:
            continue

        entry["_ts"] = ts
        signals.append(entry)

    return signals


def _extract_timestamp(entry: dict) -> float | None:
    """Extract epoch timestamp from a signal log entry."""
    # Try epoch float first
    if "timestamp" in entry:
        val = entry["timestamp"]
        if isinstance(val, (int, float)):
            return float(val)
        # Try ISO string
        if isinstance(val, str):
            try:
                return datetime.fromisoformat(val.replace("Z", "+00:00")).timestamp()
            except ValueError:
                pass
    if "ts" in entry:
        val = entry["ts"]
        if isinstance(val, (int, float)):
            return float(val)
        if isinstance(val, str):
            try:
                return datetime.fromisoformat(val.replace("Z", "+00:00")).timestamp()
            except ValueError:
                pass
    return None
