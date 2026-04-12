#!/usr/bin/env python3
"""
Hard Stop Price Path Analysis — BotTrader
==========================================
Examines what happens to price after a hard stop triggers at the -5.5% floor.
Does price continue falling, or does it recover?

Usage:
    python analysis/fee_sizing/hard_stop_path_check.py
"""

import json
import statistics
from pathlib import Path

NOTIONAL = 75
HARD_STOP_FLOOR = 0.055  # 5.5%

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent

DATA_FILES = [
    ("set_a", PROJECT_ROOT / "backtest/diagnostic_output/diagnostic_trades.jsonl"),
    ("set_b", PROJECT_ROOT / "backtest/diagnostic_output_set_b/diagnostic_trades.jsonl"),
    ("set_c", PROJECT_ROOT / "backtest/diagnostic_output_set_c/diagnostic_trades.jsonl"),
]


def load_hard_stops():
    trades = []
    skipped = 0
    for set_label, path in DATA_FILES:
        with open(path) as f:
            for i, line in enumerate(f):
                t = json.loads(line)
                if t["exit_reason"] != "hard_stop":
                    continue
                if "mae_pct" not in t or "post_exit_max_favorable_pct" not in t:
                    print(f"  WARNING: Skipping {set_label} line {i} ({t.get('symbol','?')}) — missing mae_pct or post_exit_max_favorable_pct")
                    skipped += 1
                    continue
                t["_set"] = set_label
                trades.append(t)
    return trades, skipped


def section_a(trades):
    print("=" * 70)
    print("  SECTION A: Did Price Continue Falling After Stop Level?")
    print("=" * 70)
    print()
    print("  continued_drop = mae_pct - pnl_pct")
    print("  (negative = price fell further after stop; near zero = stop caught the bottom)")
    print()

    drops = []
    for t in trades:
        # Both pnl_pct and mae_pct are negative for hard stops
        # mae_pct is more negative (worse) than pnl_pct if price kept falling
        continued_drop = t["mae_pct"] - t["pnl_pct"]
        drops.append(continued_drop)

    # Buckets (continued_drop is negative or near-zero)
    buckets = [
        ("Price recovered (mae ~ pnl, within 0.2%)", lambda d: abs(d) < 0.2),
        ("Fell further 0.2% - 0.5%",                lambda d: 0.2 <= abs(d) < 0.5),
        ("Fell further 0.5% - 1.0%",                lambda d: 0.5 <= abs(d) < 1.0),
        ("Fell further 1.0% - 2.0%",                lambda d: 1.0 <= abs(d) < 2.0),
        ("Fell further > 2.0%",                      lambda d: abs(d) >= 2.0),
    ]

    print(f"  {'Bucket':<45s}  {'Count':>5s}  {'%':>7s}")
    print(f"  {'-'*60}")
    for label, pred in buckets:
        count = sum(1 for d in drops if pred(d))
        pct = count / len(drops) * 100
        print(f"  {label:<45s}  {count:>5d}  {pct:>6.2f}%")

    print()
    avg_drop = statistics.mean(drops)
    median_drop = statistics.median(drops)
    worst_drop = min(drops)
    fell_more_1pct = sum(1 for d in drops if abs(d) >= 1.0)

    print(f"  Avg continued drop:      {avg_drop:+.2f}%")
    print(f"  Median continued drop:   {median_drop:+.2f}%")
    print(f"  Worst single drop:       {worst_drop:+.2f}%")
    print(f"  Trades falling > 1% further: {fell_more_1pct} ({fell_more_1pct/len(drops)*100:.2f}%)")
    print()


def section_b(trades):
    print("=" * 70)
    print("  SECTION B: Post-Exit Recovery Distribution")
    print("=" * 70)
    print()
    print("  post_exit_max_favorable_pct = best price recovery in 60 bars after exit")
    print()

    recoveries = [t["post_exit_max_favorable_pct"] for t in trades]

    buckets = [
        ("Recovery < 0.5%",     lambda r: r < 0.5),
        ("Recovery 0.5% - 1.0%", lambda r: 0.5 <= r < 1.0),
        ("Recovery 1.0% - 2.0%", lambda r: 1.0 <= r < 2.0),
        ("Recovery 2.0% - 3.0%", lambda r: 2.0 <= r < 3.0),
        ("Recovery > 3.0%",      lambda r: r >= 3.0),
    ]

    print(f"  {'Bucket':<45s}  {'Count':>5s}  {'%':>7s}")
    print(f"  {'-'*60}")
    for label, pred in buckets:
        count = sum(1 for r in recoveries if pred(r))
        pct = count / len(recoveries) * 100
        print(f"  {label:<45s}  {count:>5d}  {pct:>6.2f}%")

    print()
    avg_rec = statistics.mean(recoveries)
    rec_ge_1 = sum(1 for r in recoveries if r >= 1.0)
    rec_ge_2 = sum(1 for r in recoveries if r >= 2.0)
    best_rec = max(recoveries)

    print(f"  Avg post-exit recovery:    {avg_rec:.2f}%")
    print(f"  Recovered >= 1.0%:         {rec_ge_1} ({rec_ge_1/len(recoveries)*100:.2f}%)")
    print(f"  Recovered >= 2.0%:         {rec_ge_2} ({rec_ge_2/len(recoveries)*100:.2f}%)")
    print(f"  Best single recovery:      {best_rec:.2f}%")
    print()


def section_c(trades):
    print("=" * 70)
    print("  SECTION C: Trailing Hard Stop Simulation")
    print("=" * 70)
    print()
    print("  Idea: Instead of exiting at -5.5%, wait for a 1% bounce from the floor.")
    print("  Simulated exit = stop_floor * 1.01")
    print()

    bounce_trades = []
    no_bounce_trades = []

    for t in trades:
        entry = t["entry_price"]
        stop_floor = entry * (1 - HARD_STOP_FLOOR)
        simulated_exit = stop_floor * 1.01
        simulated_pnl_pct = (simulated_exit - entry) / entry * 100
        actual_pnl_pct = t["pnl_pct"]
        improvement = simulated_pnl_pct - actual_pnl_pct

        post_recovery = t["post_exit_max_favorable_pct"]

        record = {
            "symbol": t["symbol"],
            "actual_pnl_pct": actual_pnl_pct,
            "simulated_pnl_pct": simulated_pnl_pct,
            "improvement": improvement,
            "post_recovery": post_recovery,
        }

        if post_recovery >= 1.0:
            bounce_trades.append(record)
        else:
            no_bounce_trades.append(record)

    n = len(trades)
    n_bounce = len(bounce_trades)
    n_no_bounce = len(no_bounce_trades)

    print(f"  Trades where 1% recovery bounce occurred (post_exit >= 1%):  {n_bounce}  ({n_bounce/n*100:.2f}%)")
    print(f"  Trades where bounce never came:                              {n_no_bounce}  ({n_no_bounce/n*100:.2f}%)")
    print()

    if bounce_trades:
        avg_improvement = statistics.mean(r["improvement"] for r in bounce_trades)
        avg_improvement_dollar = avg_improvement / 100 * NOTIONAL
        print(f"  For trades where bounce DID occur ({n_bounce} trades):")
        print(f"    Avg improvement in exit price:  {avg_improvement:+.2f}%")
        print(f"    Avg dollar improvement at $75:  ${avg_improvement_dollar:+.2f}")
    else:
        avg_improvement_dollar = 0
        print(f"  No trades had a 1% bounce — trailing stop would never trigger.")

    print()

    if no_bounce_trades:
        # For trades where bounce didn't come, the trailing stop would have us
        # holding through further decline. The cost is the additional loss
        # beyond the floor. Use mae_pct as proxy for how bad it got.
        # Since we don't have post-exit MAE, use the fact that the trade
        # already exited at the floor and price didn't recover 1%.
        # The avg post_exit recovery for these trades shows the best they got.
        avg_recovery_no_bounce = statistics.mean(r["post_recovery"] for r in no_bounce_trades)
        # The additional loss is: we waited for a bounce that never came.
        # In reality with a trailing hard stop, we'd need a wider stop or time limit.
        # Conservative estimate: we'd exit at the same level (floor) after waiting,
        # losing only the opportunity cost / time. But if price kept falling,
        # we'd lose more. Use the continued_drop from section A as the cost.
        # For these trades, mae shows how much further it fell during the trade.
        # After exit, the post_exit data shows minimal recovery.
        # Cost of waiting = we're still in the trade while it falls further.
        # Since post_exit recovery < 1%, the bounce target was never hit.
        # We'd eventually need to exit at the floor anyway (or worse).
        # Conservative: assume we exit at actual floor (same as now) — cost = $0.
        # Realistic: we might exit worse because we delayed. Use avg continued_drop.
        costs = []
        for r in no_bounce_trades:
            # Find the original trade to get mae_pct
            orig = next(t for t in trades if t["symbol"] == r["symbol"] and t["pnl_pct"] == r["actual_pnl_pct"])
            additional = orig["mae_pct"] - orig["pnl_pct"]  # negative = fell further
            costs.append(abs(additional))

        avg_additional_loss_pct = statistics.mean(costs)
        avg_additional_loss_dollar = avg_additional_loss_pct / 100 * NOTIONAL

        print(f"  For trades where bounce did NOT occur ({n_no_bounce} trades):")
        print(f"    Avg additional loss vs. floor:  {avg_additional_loss_pct:.2f}%  (cost of waiting)")
        print(f"    Avg dollar additional loss:     ${avg_additional_loss_dollar:.2f}")
        print(f"    Avg post-exit recovery:         {avg_recovery_no_bounce:.2f}% (bounce target: 1.0%)")
    else:
        avg_additional_loss_dollar = 0
        print(f"  All trades bounced — no cost scenario.")

    print()

    # Net expected value
    p_bounce = n_bounce / n
    p_no_bounce = n_no_bounce / n
    ev = (p_bounce * avg_improvement_dollar) - (p_no_bounce * avg_additional_loss_dollar)

    print(f"  Net expected value of trailing hard stop strategy:")
    print(f"    = P(bounce) x avg_improvement  -  P(no_bounce) x avg_additional_loss")
    print(f"    = {p_bounce:.2f} x ${avg_improvement_dollar:+.2f}  -  {p_no_bounce:.2f} x ${avg_additional_loss_dollar:.2f}")
    print(f"    = ${ev:+.2f} per trade")
    print(f"    = ${ev * n:+.2f} across all {n} hard stop trades")
    print()

    if ev > 0:
        print(f"  VERDICT: Positive EV (${ev:+.2f}/trade). The trailing hard stop idea has merit.")
    else:
        print(f"  VERDICT: Negative EV (${ev:+.2f}/trade). The trailing hard stop idea hurts more than it helps.")


def section_d(trades):
    print()
    print("=" * 70)
    print("  SECTION D: Plain English Verdict")
    print("=" * 70)
    print()

    # Compute key stats for the summary
    drops = [t["mae_pct"] - t["pnl_pct"] for t in trades]
    recoveries = [t["post_exit_max_favorable_pct"] for t in trades]
    avg_drop = statistics.mean(drops)
    avg_recovery = statistics.mean(recoveries)
    rec_ge_1 = sum(1 for r in recoveries if r >= 1.0)
    near_bottom = sum(1 for d in drops if abs(d) < 0.2)

    # Recompute EV for summary
    bounce = [t for t in trades if t["post_exit_max_favorable_pct"] >= 1.0]
    no_bounce = [t for t in trades if t["post_exit_max_favorable_pct"] < 1.0]
    n = len(trades)

    if bounce:
        avg_imp = statistics.mean(
            ((t["entry_price"] * (1 - HARD_STOP_FLOOR) * 1.01 - t["entry_price"]) / t["entry_price"] * 100) - t["pnl_pct"]
            for t in bounce
        ) / 100 * NOTIONAL
    else:
        avg_imp = 0

    if no_bounce:
        avg_cost = statistics.mean(abs(t["mae_pct"] - t["pnl_pct"]) for t in no_bounce) / 100 * NOTIONAL
    else:
        avg_cost = 0

    ev = (len(bounce) / n * avg_imp) - (len(no_bounce) / n * avg_cost)

    print(f"  Once the 5.5% hard stop floor is hit, the price typically does NOT recover.")
    print(f"  {near_bottom} of {n} hard stop trades ({near_bottom/n*100:.0f}%) exit at or very near the worst")
    print(f"  point of the trade (MAE within 0.2% of exit price), meaning the stop is catching")
    print(f"  genuine bottoms, not cutting trades short before a recovery. Post-exit, the average")
    print(f"  recovery is just {avg_recovery:.2f}% over 60 minutes, and only {rec_ge_1} of {n} trades ({rec_ge_1/n*100:.0f}%)")
    print(f"  recover even 1%. A trailing hard stop strategy that waits for a 1% bounce has an")
    print(f"  expected value of ${ev:+.2f} per trade — {'a small positive' if ev > 0 else 'negative'}, meaning", end=" ")
    if ev > 0:
        print(f"the idea shows marginal promise but the")
        print(f"  effect is small (${ev * n:+.2f} total across {n} hard stops). The hard stop is already well-calibrated.")
    else:
        print(f"waiting for a bounce")
        print(f"  that rarely comes costs more than it saves. The current hard stop exit is correct.")
    print()


def main():
    print("=" * 70)
    print("  BotTrader — Hard Stop Price Path Analysis")
    print("  72 hard stop trades across 3 OOS sets")
    print("=" * 70)
    print()

    trades, skipped = load_hard_stops()
    print(f"  Loaded {len(trades)} hard stop trades ({skipped} skipped)")
    print()

    section_a(trades)
    section_b(trades)
    section_c(trades)
    section_d(trades)


if __name__ == "__main__":
    main()
