"""v1 vs v2 comparison collector — signal agreement, fill deltas, symbol diff."""

from __future__ import annotations

import logging
from datetime import datetime

from ..models import ComparisonData
from .signals import parse_signals

logger = logging.getLogger(__name__)


def collect_comparison(
    v1_log_path: str,
    v2_log_path: str,
    start: datetime,
    end: datetime,
    match_window_seconds: float = 60.0,
) -> ComparisonData:
    """Compare v1 and v2 signals for the reporting period.

    Signal matching: greedy, by (symbol, action) within match_window_seconds.
    """
    v1_signals = parse_signals(v1_log_path, start, end)
    v2_signals = parse_signals(v2_log_path, start, end)

    v1_symbols = {s.get("symbol", "") for s in v1_signals}
    v2_symbols = {s.get("symbol", "") for s in v2_signals}

    # Greedy matching: for each v1 signal, find closest unmatched v2 signal
    v2_available = list(range(len(v2_signals)))
    matched_v1 = set()
    matched_v2 = set()
    divergences: list[dict] = []

    for i, v1_sig in enumerate(v1_signals):
        v1_sym = v1_sig.get("symbol", "")
        v1_action = v1_sig.get("action", "").upper()
        v1_ts = v1_sig.get("_ts", 0.0)

        best_j = None
        best_delta = float("inf")

        for j in v2_available:
            if j in matched_v2:
                continue
            v2_sig = v2_signals[j]
            v2_sym = v2_sig.get("symbol", "")
            v2_action = v2_sig.get("action", "").upper()
            v2_ts = v2_sig.get("_ts", 0.0)

            if v2_sym != v1_sym or v2_action != v1_action:
                continue

            delta = abs(v1_ts - v2_ts)
            if delta <= match_window_seconds and delta < best_delta:
                best_delta = delta
                best_j = j

        if best_j is not None:
            matched_v1.add(i)
            matched_v2.add(best_j)

    # Unmatched signals
    v1_only = [v1_signals[i] for i in range(len(v1_signals)) if i not in matched_v1]
    v2_only = [v2_signals[j] for j in range(len(v2_signals)) if j not in matched_v2]

    # Top divergences (v1-only signals, capped at 10)
    for sig in v1_only[:10]:
        divergences.append({
            "source": "v1_only",
            "symbol": sig.get("symbol", ""),
            "action": sig.get("action", ""),
            "timestamp": sig.get("_ts", 0),
        })
    for sig in v2_only[:10]:
        divergences.append({
            "source": "v2_only",
            "symbol": sig.get("symbol", ""),
            "action": sig.get("action", ""),
            "timestamp": sig.get("_ts", 0),
        })

    total = max(len(v1_signals), len(v2_signals), 1)
    agreement_rate = len(matched_v1) / total

    return ComparisonData(
        v1_signal_count=len(v1_signals),
        v2_signal_count=len(v2_signals),
        agreement_count=len(matched_v1),
        v1_only_count=len(v1_only),
        v2_only_count=len(v2_only),
        agreement_rate=agreement_rate,
        v1_symbols=v1_symbols,
        v2_symbols=v2_symbols,
        symbols_only_v1=v1_symbols - v2_symbols,
        symbols_only_v2=v2_symbols - v1_symbols,
        divergences=divergences,
    )
