#!/usr/bin/env python3
"""Compare v1 and v2 signal logs for agreement analysis.

Reads JSONL files from v1 (scores.jsonl) and v2 (v2_score_log.jsonl),
aligns signals by (symbol, timestamp) within a configurable window,
and reports match statistics.

v1 logs every evaluation (including holds) for all 31 symbols.
v2 only logs buy/sell signals. Both are filtered to buy/sell only.

Usage:
    python scripts/compare_signals.py \
        --v1 logs/scores.jsonl \
        --v2 logs/v2_score_log.jsonl \
        [--window 60]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path


def load_signals(path: Path) -> list[dict]:
    """Load actionable signals (buy/sell) from a JSONL file."""
    signals = []
    with open(path) as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                print(f"  Warning: skipping malformed line {line_num} in {path}", file=sys.stderr)
                continue
            action = rec.get("action", "").lower()
            if action not in ("buy", "sell"):
                continue
            ts_raw = rec.get("ts") or rec.get("timestamp")
            if ts_raw:
                try:
                    rec["_ts"] = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                except (ValueError, AttributeError):
                    continue
            else:
                continue
            rec["_symbol"] = rec.get("symbol", "")
            rec["_action"] = action
            signals.append(rec)
    return signals


def align_signals(
    v1_signals: list[dict],
    v2_signals: list[dict],
    window_secs: int = 60,
) -> tuple[list[tuple[dict, dict]], list[dict], list[dict]]:
    """Align signals by (symbol, timestamp) within window.

    Returns (matched_pairs, v1_only, v2_only).
    """
    window = timedelta(seconds=window_secs)
    v2_used: set[int] = set()
    matched: list[tuple[dict, dict]] = []
    v1_only: list[dict] = []

    for s1 in v1_signals:
        best_idx = None
        best_delta = None
        for i, s2 in enumerate(v2_signals):
            if i in v2_used:
                continue
            if s1["_symbol"] != s2["_symbol"]:
                continue
            delta = abs(s1["_ts"] - s2["_ts"])
            if delta <= window:
                if best_delta is None or delta < best_delta:
                    best_idx = i
                    best_delta = delta
        if best_idx is not None:
            v2_used.add(best_idx)
            matched.append((s1, v2_signals[best_idx]))
        else:
            v1_only.append(s1)

    v2_only = [s for i, s in enumerate(v2_signals) if i not in v2_used]
    return matched, v1_only, v2_only


def print_report(
    matched: list[tuple[dict, dict]],
    v1_only: list[dict],
    v2_only: list[dict],
) -> None:
    """Print a human-readable comparison report."""
    total_v1 = len(matched) + len(v1_only)
    total_v2 = len(matched) + len(v2_only)
    direction_agree = sum(1 for s1, s2 in matched if s1["_action"] == s2["_action"])

    print("=" * 60)
    print("  Signal Comparison Report: v1 vs v2")
    print("=" * 60)
    print(f"  v1 signals (buy/sell): {total_v1}")
    print(f"  v2 signals (buy/sell): {total_v2}")
    print(f"  Matched pairs:        {len(matched)}")
    print(f"  v1-only:              {len(v1_only)}")
    print(f"  v2-only:              {len(v2_only)}")

    if matched:
        rate = direction_agree / len(matched) * 100
        print(f"  Direction agreement:  {direction_agree}/{len(matched)} ({rate:.1f}%)")
    else:
        print("  Direction agreement:  N/A (no matched pairs)")

    print("=" * 60)

    if matched:
        print("\n  Matched signals (first 10):")
        for s1, s2 in matched[:10]:
            agree = "OK" if s1["_action"] == s2["_action"] else "MISMATCH"
            print(
                f"    {s1['_ts']:%H:%M:%S} {s1['_symbol']:>7} "
                f"v1={s1['_action']:4} v2={s2['_action']:4} [{agree}]"
            )

    if v1_only:
        print(f"\n  v1-only signals (first 5):")
        for s in v1_only[:5]:
            print(f"    {s['_ts']:%H:%M:%S} {s['_symbol']:>7} {s['_action']}")

    if v2_only:
        print(f"\n  v2-only signals (first 5):")
        for s in v2_only[:5]:
            print(f"    {s['_ts']:%H:%M:%S} {s['_symbol']:>7} {s['_action']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare v1 and v2 signal logs")
    parser.add_argument("--v1", default="logs/scores.jsonl", help="Path to v1 scores.jsonl")
    parser.add_argument("--v2", default="logs/v2_score_log.jsonl", help="Path to v2 v2_score_log.jsonl")
    parser.add_argument(
        "--window", type=int, default=60,
        help="Alignment window in seconds (default: 60)",
    )
    args = parser.parse_args()

    v1_path = Path(args.v1)
    v2_path = Path(args.v2)

    if not v1_path.exists():
        print(f"Error: v1 file not found: {v1_path}", file=sys.stderr)
        sys.exit(1)
    if not v2_path.exists():
        print(f"Error: v2 file not found: {v2_path}", file=sys.stderr)
        sys.exit(1)

    v1_signals = load_signals(v1_path)
    v2_signals = load_signals(v2_path)

    print(f"  Loaded {len(v1_signals)} v1 signals, {len(v2_signals)} v2 signals")

    matched, v1_only, v2_only = align_signals(v1_signals, v2_signals, args.window)
    print_report(matched, v1_only, v2_only)


if __name__ == "__main__":
    main()
