#!/usr/bin/env python3
"""
Hard Stop Reduction Analysis — BotTrader
==========================================
Compares backtest runs against the baseline to measure hard stop reduction
impact on trade count, win rate, P&L, and Profit Factor.

Usage:
    python analysis/fee_sizing/hard_stop_analysis.py
"""

import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path

MAKER_FEE = 0.0025
TAKER_FEE = 0.0040
RT_FEE = 0.0065
NOTIONAL = 75

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent


def load_trades(jsonl_paths):
    """Load trades from a list of (set_label, path) tuples."""
    trades = []
    for set_label, path in jsonl_paths:
        if not path.exists():
            print(f"  WARNING: {path} not found, skipping")
            continue
        with open(path) as f:
            for line in f:
                t = json.loads(line)
                t["_set"] = set_label
                trades.append(t)
    return trades


def net_pnl(trade):
    gross_pct = (trade["exit_price"] - trade["entry_price"]) / trade["entry_price"]
    return NOTIONAL * gross_pct - NOTIONAL * RT_FEE


def profit_factor(trades):
    wins = sum(net_pnl(t) for t in trades if net_pnl(t) > 0)
    losses = abs(sum(net_pnl(t) for t in trades if net_pnl(t) <= 0))
    if losses == 0:
        return float("inf") if wins > 0 else 0.0
    return wins / losses


def win_rate(trades):
    if not trades:
        return 0.0
    return sum(1 for t in trades if net_pnl(t) > 0) / len(trades) * 100


def fmt_dollar(v):
    return f"${v:+,.2f}" if v != 0 else "$0.00"


def analyze_run(label, trades, baseline_count=None):
    """Print metrics for a set of trades."""
    n = len(trades)
    exit_counts = Counter(t["exit_reason"] for t in trades)

    hs = exit_counts.get("hard_stop", 0)
    ts = exit_counts.get("trailing_stop", 0)
    se = exit_counts.get("stale_exit", 0)
    rm = exit_counts.get("roc_momo_20m", 0)
    other = n - hs - ts - se - rm

    total_net = sum(net_pnl(t) for t in trades)
    wr = win_rate(trades)
    pf = profit_factor(trades)

    pf_str = f"{pf:.2f}" if pf < 100 else "inf"
    blocked = f" ({baseline_count - n} blocked)" if baseline_count else ""

    print(f"  {label}")
    print(f"    Trades: {n}{blocked}")
    print(f"    Win rate: {wr:.1f}%")
    print(f"    Net P&L: {fmt_dollar(total_net)} (PF={pf_str})")
    print(f"    Exit breakdown:")
    print(f"      trailing_stop: {ts:3d} ({ts/n*100:5.1f}%)")
    print(f"      hard_stop:     {hs:3d} ({hs/n*100:5.1f}%)  <-- target")
    print(f"      stale_exit:    {se:3d} ({se/n*100:5.1f}%)")
    if rm:
        print(f"      roc_momo_20m:  {rm:3d} ({rm/n*100:5.1f}%)")
    if other:
        print(f"      other:         {other:3d} ({other/n*100:5.1f}%)")

    return {"n": n, "wr": wr, "pnl": total_net, "pf": pf, "hs": hs, "ts": ts}


def comparison_table(baseline_stats, run_stats, run_label):
    """Print a side-by-side comparison table."""
    print()
    print(f"  {'Metric':<25s}  {'Baseline':>12s}  {run_label:>12s}  {'Delta':>12s}")
    print(f"  {'-'*65}")

    for key, label, fmt in [
        ("n", "Trades", "d"),
        ("wr", "Win Rate %", ".1f"),
        ("pnl", "Net P&L", "$"),
        ("pf", "Profit Factor", ".2f"),
        ("hs", "Hard Stops", "d"),
        ("ts", "Trailing Stops", "d"),
    ]:
        bv = baseline_stats[key]
        rv = run_stats[key]
        delta = rv - bv

        if fmt == "d":
            b_str = f"{bv:d}"
            r_str = f"{rv:d}"
            d_str = f"{delta:+d}"
        elif fmt == "$":
            b_str = fmt_dollar(bv)
            r_str = fmt_dollar(rv)
            d_str = fmt_dollar(delta)
        elif fmt == ".1f":
            b_str = f"{bv:.1f}%"
            r_str = f"{rv:.1f}%"
            d_str = f"{delta:+.1f}%"
        elif fmt == ".2f":
            b_str = f"{bv:.2f}"
            r_str = f"{rv:.2f}"
            d_str = f"{delta:+.2f}"
        else:
            b_str = str(bv)
            r_str = str(rv)
            d_str = str(delta)

        print(f"  {label:<25s}  {b_str:>12s}  {r_str:>12s}  {d_str:>12s}")


# ---------------------------------------------------------------------------
# Run 1: Regime ATR percentile 60 → 40
# ---------------------------------------------------------------------------

def run1_analysis():
    print("=" * 70)
    print("  RUN 1: regime_max_atr_percentile 60 → 40")
    print("  All other parameters unchanged from baseline")
    print("=" * 70)

    # Load baseline
    baseline_paths = [
        ("set_a", PROJECT_ROOT / "backtest/diagnostic_output/diagnostic_trades.jsonl"),
        ("set_b", PROJECT_ROOT / "backtest/diagnostic_output_set_b/diagnostic_trades.jsonl"),
        ("set_c", PROJECT_ROOT / "backtest/diagnostic_output_set_c/diagnostic_trades.jsonl"),
    ]
    baseline = load_trades(baseline_paths)

    # Load Run 1
    run1_paths = [
        ("set_a", PROJECT_ROOT / "backtest/diagnostic_output_run1/diagnostic_trades.jsonl"),
        ("set_b", PROJECT_ROOT / "backtest/diagnostic_output_set_b_run1/diagnostic_trades.jsonl"),
        ("set_c", PROJECT_ROOT / "backtest/diagnostic_output_set_c_run1/diagnostic_trades.jsonl"),
    ]
    run1 = load_trades(run1_paths)

    if not run1:
        print("\n  Run 1 data not found. Run backtests first.\n")
        return

    print()
    print("--- Baseline (regime_max_atr_percentile=60) ---")
    b_stats = analyze_run("Combined (all sets)", baseline)

    print()
    print("--- Run 1 (regime_max_atr_percentile=40) ---")
    r1_stats = analyze_run("Combined (all sets)", run1, baseline_count=len(baseline))

    print()
    print("--- Comparison ---")
    comparison_table(b_stats, r1_stats, "ATR<=40")

    # Per-set breakdown
    print()
    print("--- Per-Set Breakdown ---")
    for s in ["set_a", "set_b", "set_c"]:
        b_set = [t for t in baseline if t["_set"] == s]
        r_set = [t for t in run1 if t["_set"] == s]
        print()
        b_s = analyze_run(f"Baseline {s.upper()}", b_set)
        r_s = analyze_run(f"Run 1    {s.upper()}", r_set, baseline_count=len(b_set))

    # Key finding
    delta_hs = r1_stats["hs"] - b_stats["hs"]
    delta_pnl = r1_stats["pnl"] - b_stats["pnl"]
    delta_n = r1_stats["n"] - b_stats["n"]
    print()
    if delta_pnl > 0:
        print(f"KEY FINDING: Lowering ATR percentile to 40 blocks {-delta_n} trades, reduces hard stops by {-delta_hs},")
        print(f"  and improves net P&L by {fmt_dollar(delta_pnl)} (from {fmt_dollar(b_stats['pnl'])} to {fmt_dollar(r1_stats['pnl'])}).")
    else:
        print(f"KEY FINDING: Lowering ATR percentile to 40 blocks {-delta_n} trades, reduces hard stops by {-delta_hs},")
        print(f"  but net P&L worsens by {fmt_dollar(delta_pnl)} (from {fmt_dollar(b_stats['pnl'])} to {fmt_dollar(r1_stats['pnl'])}).")


# ---------------------------------------------------------------------------
# Run 2: ADX threshold sweep
# ---------------------------------------------------------------------------

def run2_analysis():
    print()
    print("=" * 70)
    print("  RUN 2: ADX minimum threshold sweep (20, 25, 30)")
    print("  regime_max_atr_percentile restored to 60 (baseline)")
    print("=" * 70)

    # Load baseline (ADX=20, same as current)
    baseline_paths = [
        ("set_a", PROJECT_ROOT / "backtest/diagnostic_output/diagnostic_trades.jsonl"),
        ("set_b", PROJECT_ROOT / "backtest/diagnostic_output_set_b/diagnostic_trades.jsonl"),
        ("set_c", PROJECT_ROOT / "backtest/diagnostic_output_set_c/diagnostic_trades.jsonl"),
    ]
    baseline = load_trades(baseline_paths)
    b_stats = analyze_run("Baseline (ADX >= 20)", baseline)

    for adx_threshold in [25, 30]:
        suffix = f"_run2_adx{adx_threshold}"
        run_paths = [
            ("set_a", PROJECT_ROOT / f"backtest/diagnostic_output{suffix}/diagnostic_trades.jsonl"),
            ("set_b", PROJECT_ROOT / f"backtest/diagnostic_output_set_b{suffix}/diagnostic_trades.jsonl"),
            ("set_c", PROJECT_ROOT / f"backtest/diagnostic_output_set_c{suffix}/diagnostic_trades.jsonl"),
        ]
        run = load_trades(run_paths)
        if not run:
            print(f"\n  ADX>={adx_threshold} data not found. Run backtests first.\n")
            continue

        print()
        r_stats = analyze_run(f"ADX >= {adx_threshold}", run, baseline_count=len(baseline))

        # Count trades blocked by ADX specifically
        # (trades in baseline but not in this run = blocked by tighter ADX filter)
        blocked = len(baseline) - len(run)
        print(f"    Trades blocked by ADX filter: {blocked}")

        comparison_table(b_stats, r_stats, f"ADX>={adx_threshold}")

    # Key finding placeholder
    print()
    print("KEY FINDING: See comparison tables above for ADX sweep results.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 70)
    print("  BotTrader Hard Stop Reduction Analysis")
    print("  Baseline: 320 trades, regime_max_atr_percentile=60, adx_min=20")
    print("=" * 70)

    run1_analysis()
    run2_analysis()


if __name__ == "__main__":
    main()
