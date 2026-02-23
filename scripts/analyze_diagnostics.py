#!/usr/bin/env python3
"""Post-hoc analysis of diagnostic backtest data.

Reads diagnostic_trades.jsonl from the real strategy and (optionally) the
random baseline, then produces comparison tables and insights.

Usage:
    python scripts/analyze_diagnostics.py backtest/diagnostic_output/ [backtest/diagnostic_output_random/]
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np


def load_trades(directory: str) -> list[dict]:
    """Load trades from diagnostic_trades.jsonl."""
    path = Path(directory) / "diagnostic_trades.jsonl"
    if not path.exists():
        print(f"Error: {path} not found")
        sys.exit(1)
    trades = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                trades.append(json.loads(line))
    return trades


def summarize(trades: list[dict], label: str) -> dict:
    """Compute summary statistics for a set of trades."""
    if not trades:
        return {"label": label, "trades": 0}

    pnls = [t["pnl"] for t in trades]
    pnl_pcts = [t["pnl_pct"] for t in trades]
    winners = [t for t in trades if t["pnl"] > 0]
    losers = [t for t in trades if t["pnl"] <= 0]

    return {
        "label": label,
        "trades": len(trades),
        "total_pnl": round(sum(pnls), 2),
        "avg_pnl": round(np.mean(pnls), 4),
        "median_pnl": round(float(np.median(pnls)), 4),
        "win_rate": round(len(winners) / len(trades) * 100, 1),
        "avg_win": round(np.mean([t["pnl"] for t in winners]), 4) if winners else 0,
        "avg_loss": round(np.mean([t["pnl"] for t in losers]), 4) if losers else 0,
        "mfe_avg_pct": round(np.mean([t["mfe_pct"] for t in trades]), 3),
        "mae_avg_pct": round(np.mean([t["mae_pct"] for t in trades]), 3),
        "post_exit_fav_avg_pct": round(np.mean([t["post_exit_max_favorable_pct"] for t in trades]), 3),
        "avg_bars_held": round(np.mean([t["bars_held"] for t in trades]), 1),
    }


def print_comparison(real: dict, random: dict | None) -> None:
    """Print side-by-side comparison of real vs random."""
    print("\n" + "=" * 70)
    print("STRATEGY COMPARISON: Real vs Random Baseline")
    print("=" * 70)

    headers = ["Metric", real["label"]]
    if random:
        headers.append(random["label"])

    row_fmt = "  {:<30} {:>15}"
    row_fmt2 = "  {:<30} {:>15} {:>15}"

    def pr(metric, val_real, val_rand=None):
        if val_rand is not None:
            print(row_fmt2.format(metric, str(val_real), str(val_rand)))
        else:
            print(row_fmt.format(metric, str(val_real)))

    if random:
        print(row_fmt2.format("Metric", real["label"], random["label"]))
        print("  " + "-" * 62)
    else:
        print(row_fmt.format("Metric", real["label"]))
        print("  " + "-" * 47)

    pr("Trades", real["trades"], random["trades"] if random else None)
    pr("Total P&L", f"${real['total_pnl']}", f"${random['total_pnl']}" if random else None)
    pr("Avg P&L", f"${real['avg_pnl']}", f"${random['avg_pnl']}" if random else None)
    pr("Win Rate", f"{real['win_rate']}%", f"{random['win_rate']}%" if random else None)
    pr("Avg Win", f"${real['avg_win']}", f"${random['avg_win']}" if random else None)
    pr("Avg Loss", f"${real['avg_loss']}", f"${random['avg_loss']}" if random else None)
    pr("MFE avg %", f"{real['mfe_avg_pct']}%", f"{random['mfe_avg_pct']}%" if random else None)
    pr("MAE avg %", f"{real['mae_avg_pct']}%", f"{random['mae_avg_pct']}%" if random else None)
    pr("Post-exit fav %", f"{real['post_exit_fav_avg_pct']}%",
       f"{random['post_exit_fav_avg_pct']}%" if random else None)
    pr("Avg bars held", str(real["avg_bars_held"]),
       str(random["avg_bars_held"]) if random else None)

    if random:
        print("\n  Interpretation:")
        real_avg = real["avg_pnl"]
        rand_avg = random["avg_pnl"]
        if abs(real_avg - rand_avg) < abs(real_avg) * 0.2:
            print("  -> Real and random perform SIMILARLY -> exit manager is the bottleneck")
        elif real_avg > rand_avg:
            print("  -> Real outperforms random -> indicators have value, exits need tuning")
        else:
            print("  -> Random outperforms real -> indicators may be HARMFUL (anti-edge)")


def analyze_winning_vs_losing_indicators(trades: list[dict]) -> None:
    """Compare indicator values for winning vs losing trades."""
    print("\n" + "=" * 70)
    print("INDICATOR ANALYSIS: Winners vs Losers")
    print("=" * 70)

    winners = [t for t in trades if t["pnl"] > 0]
    losers = [t for t in trades if t["pnl"] <= 0]

    if not winners or not losers:
        print("  Not enough winners or losers for comparison")
        return

    # Compare raw values at entry
    raw_keys = ["rsi", "roc", "rvol", "macd_hist"]

    print(f"\n  {'Indicator':<15} {'Winners Avg':>12} {'Losers Avg':>12} {'Delta':>10}")
    print("  " + "-" * 51)

    for key in raw_keys:
        win_vals = []
        lose_vals = []
        for t in winners:
            raw = t.get("entry_metadata", {}).get("raw_values", {})
            if key in raw:
                win_vals.append(raw[key])
        for t in losers:
            raw = t.get("entry_metadata", {}).get("raw_values", {})
            if key in raw:
                lose_vals.append(raw[key])

        if win_vals and lose_vals:
            w_avg = np.mean(win_vals)
            l_avg = np.mean(lose_vals)
            print(f"  {key:<15} {w_avg:>12.4f} {l_avg:>12.4f} {w_avg - l_avg:>10.4f}")


def analyze_regime_filter(trades: list[dict]) -> None:
    """Simulate what-if: only trading in certain regimes."""
    print("\n" + "=" * 70)
    print("REGIME FILTER SIMULATION")
    print("=" * 70)

    regimes = defaultdict(list)
    for t in trades:
        regime = t.get("entry_metadata", {}).get("regime", {}).get("label", "unknown")
        regimes[regime].append(t)

    print(f"\n  {'Regime':<25} {'Trades':>6} {'P&L':>10} {'Win%':>7} {'Avg P&L':>10}")
    print("  " + "-" * 60)

    for regime, rtrades in sorted(regimes.items(), key=lambda x: -len(x[1])):
        pnls = [t["pnl"] for t in rtrades]
        wins = sum(1 for p in pnls if p > 0)
        win_rate = wins / len(rtrades) * 100 if rtrades else 0
        print(f"  {regime:<25} {len(rtrades):>6} ${sum(pnls):>9.2f} {win_rate:>6.1f}% ${np.mean(pnls):>9.4f}")

    # What-if: uptrend only
    uptrend = [t for t in trades
               if "uptrend" in t.get("entry_metadata", {}).get("regime", {}).get("label", "")]
    if uptrend:
        uptrend_pnl = sum(t["pnl"] for t in uptrend)
        total_pnl = sum(t["pnl"] for t in trades)
        print(f"\n  What-if uptrend only: {len(uptrend)} trades, P&L ${uptrend_pnl:.2f} "
              f"(vs ${total_pnl:.2f} all trades)")


def analyze_mfe_mae_detail(trades: list[dict]) -> None:
    """Detailed MFE/MAE analysis — are stops too tight?"""
    print("\n" + "=" * 70)
    print("MFE/MAE DETAIL: Are stops too tight?")
    print("=" * 70)

    losers = [t for t in trades if t["pnl"] <= 0]
    if not losers:
        print("  No losing trades")
        return

    # How many losers actually saw profit before the stop fired?
    losers_with_profit = [t for t in losers if t["mfe_pct"] > 0.5]
    pct = len(losers_with_profit) / len(losers) * 100

    print(f"\n  Losing trades: {len(losers)}")
    print(f"  Losers that saw >0.5% profit before stop: {len(losers_with_profit)} ({pct:.1f}%)")

    if losers_with_profit:
        mfes = [t["mfe_pct"] for t in losers_with_profit]
        print(f"    Their MFE avg: {np.mean(mfes):.3f}%")
        print(f"    Their MFE max: {max(mfes):.3f}%")
        print(f"    -> These trades could have been profitable with tighter trailing stops")

    # Distribution of MAE for all trades
    maes = [t["mae_pct"] for t in trades]
    print(f"\n  MAE distribution (all trades):")
    for pctile in [25, 50, 75, 90]:
        val = float(np.percentile(maes, pctile))
        print(f"    {pctile}th percentile: {val:.3f}%")


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/analyze_diagnostics.py <diagnostic_output_dir> [random_output_dir]")
        sys.exit(1)

    real_dir = sys.argv[1]
    random_dir = sys.argv[2] if len(sys.argv) > 2 else None

    real_trades = load_trades(real_dir)
    print(f"Loaded {len(real_trades)} trades from {real_dir}")

    random_trades = None
    if random_dir:
        random_trades = load_trades(random_dir)
        print(f"Loaded {len(random_trades)} trades from {random_dir}")

    # Comparison
    real_summary = summarize(real_trades, "Composite")
    random_summary = summarize(random_trades, "Random") if random_trades else None
    print_comparison(real_summary, random_summary)

    # Detailed analysis of real strategy
    analyze_winning_vs_losing_indicators(real_trades)
    analyze_regime_filter(real_trades)
    analyze_mfe_mae_detail(real_trades)


if __name__ == "__main__":
    main()
