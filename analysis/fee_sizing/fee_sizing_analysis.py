#!/usr/bin/env python3
"""
Fee/Sizing Analysis — BotTrader
================================
Loads all three JSONL diagnostic trade datasets and produces a structured
console report answering five strategic questions about fee drag and sizing.

Usage:
    python analysis/fee_sizing/fee_sizing_analysis.py
"""

import json
import math
import os
import statistics
from collections import Counter, defaultdict
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAKER_FEE = 0.0025   # 0.25%
TAKER_FEE = 0.0040   # 0.40%
RT_FEE    = 0.0065   # round-trip total

CAPITAL = 10_000
MAX_POSITION_PCT = 0.05  # 5% of capital

NOTIONAL_LEVELS = [30, 50, 75, 100, 125, 150, 175, 200, 250, 300]

FEE_TIERS = [
    {"label": "$0-$50K",     "monthly_vol": 50_000,    "maker": 0.0025, "taker": 0.0040},
    {"label": "$50K-$100K",  "monthly_vol": 100_000,   "maker": 0.0020, "taker": 0.0035},
    {"label": "$100K-$250K", "monthly_vol": 250_000,   "maker": 0.0014, "taker": 0.0024},
    {"label": "$250K-$500K", "monthly_vol": 500_000,   "maker": 0.0012, "taker": 0.0022},
    {"label": "$500K-$1M",   "monthly_vol": 1_000_000, "maker": 0.0010, "taker": 0.0020},
]

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_FILES = [
    ("set_a", SCRIPT_DIR / "backtest_trades" / "set_a_diagnostic_trades.jsonl"),
    ("set_b", SCRIPT_DIR / "backtest_trades" / "set_b_diagnostic_trades.jsonl"),
    ("set_c", SCRIPT_DIR / "backtest_trades" / "set_c_diagnostic_trades.jsonl"),
]

MIN_SAMPLE = 15  # minimum trades for statistical reporting

# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_trades():
    trades = []
    skipped = 0
    for set_label, path in DATA_FILES:
        with open(path) as f:
            for i, line in enumerate(f):
                t = json.loads(line)
                # Validate required fields
                em = t.get("entry_metadata", {})
                if not t.get("entry_price") or not t.get("exit_price") or not em:
                    print(f"  WARNING: Skipping {set_label} line {i} ({t.get('symbol','?')}) — missing required fields")
                    skipped += 1
                    continue
                t["_set"] = set_label
                trades.append(t)
    return trades, skipped

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def gross_pnl_pct(trade):
    return (trade["exit_price"] - trade["entry_price"]) / trade["entry_price"]


def net_pnl_at(trade, notional, maker=MAKER_FEE, taker=TAKER_FEE):
    gross = notional * gross_pnl_pct(trade)
    fee = notional * (maker + taker)
    return gross - fee


def profit_factor(net_pnls):
    wins = sum(p for p in net_pnls if p > 0)
    losses = abs(sum(p for p in net_pnls if p <= 0))
    if losses == 0:
        return float("inf") if wins > 0 else 0.0
    return wins / losses


def win_rate(net_pnls):
    if not net_pnls:
        return 0.0
    return sum(1 for p in net_pnls if p > 0) / len(net_pnls) * 100


def fmt_dollar(v):
    return f"${v:+,.2f}" if v != 0 else "$0.00"


def fmt_pct(v):
    return f"{v:.1f}%"


def fired_indicators(trade):
    """Return frozenset of indicator names that fired (contribution > 0)."""
    em = trade.get("entry_metadata", {})
    sc = em.get("score_components", {})
    buy_components = sc.get("buy", [])
    return frozenset(
        c["indicator"] for c in buy_components if c.get("contribution", 0) > 0
    )


def section_header(title):
    print()
    print("=" * 80)
    print(f"  {title}")
    print("=" * 80)


def sub_header(title):
    print()
    print(f"--- {title} ---")

# ---------------------------------------------------------------------------
# Section 1 — Notional Sensitivity Analysis
# ---------------------------------------------------------------------------

def section_1(trades):
    section_header("SECTION 1: Notional Sensitivity Analysis")
    print()
    print("Question: At what notional does the strategy become net profitable?")
    print(f"Fee basis: {MAKER_FEE*100:.2f}% maker + {TAKER_FEE*100:.2f}% taker = {RT_FEE*100:.2f}% round-trip")
    print(f"Note: pnl/pnl_pct in JSONL are gross (pre-fee). Rescaling uses entry/exit prices directly.")
    print()

    sets = ["set_a", "set_b", "set_c"]
    by_set = {s: [t for t in trades if t["_set"] == s] for s in sets}

    # Header
    print(f"{'Notional':>10s}  {'Set A':>10s}  {'Set B':>10s}  {'Set C':>10s}  {'Combined':>10s}  {'Gross':>10s}  {'Fees':>10s}  {'PF':>6s}")
    print("-" * 80)

    breakeven_notional = None
    breakeven_pnl = None

    for n in NOTIONAL_LEVELS:
        row = {}
        for s in sets:
            pnls = [net_pnl_at(t, n) for t in by_set[s]]
            row[s] = sum(pnls)
        all_pnls = [net_pnl_at(t, n) for t in trades]
        combined = sum(all_pnls)
        gross = sum(n * gross_pnl_pct(t) for t in trades)
        fees = n * RT_FEE * len(trades)
        pf = profit_factor(all_pnls)

        marker = ""
        if breakeven_notional is None and combined > 0:
            breakeven_notional = n
            breakeven_pnl = combined
            marker = "  <-- breakeven"

        pf_str = f"{pf:.2f}" if pf < 100 else "inf"
        print(f"${n:>8d}  {fmt_dollar(row['set_a']):>10s}  {fmt_dollar(row['set_b']):>10s}  {fmt_dollar(row['set_c']):>10s}  {fmt_dollar(combined):>10s}  {fmt_dollar(gross):>10s}  {fmt_dollar(-fees):>10s}  {pf_str:>6s}{marker}")

    print()
    max_notional = CAPITAL * MAX_POSITION_PCT
    print(f"Capital constraint: {MAX_POSITION_PCT*100:.0f}% of ${CAPITAL:,} = ${max_notional:,.0f} max per trade")

    # Explain WHY notional doesn't help
    avg_gross_pct = statistics.mean(gross_pnl_pct(t) for t in trades) * 100
    print(f"\nCRITICAL INSIGHT: Profit Factor is constant ({profit_factor([net_pnl_at(t, 75) for t in trades]):.2f}) across all notional levels.")
    print(f"  This is because Kraken fees are percentage-based — scaling notional scales both")
    print(f"  gross P&L and fees equally. The ratio never changes.")
    print(f"  Avg gross return per trade: {avg_gross_pct:+.3f}%")
    print(f"  Round-trip fee:             {RT_FEE*100:+.3f}%")
    print(f"  Avg NET return per trade:   {avg_gross_pct - RT_FEE*100:+.3f}%")
    print(f"  The avg gross return ({avg_gross_pct:+.3f}%) does not cover the fee ({RT_FEE*100:.3f}%).")
    print(f"  Notional scaling cannot fix this. The levers are: lower fee %, or better trade selection.")

    # Compute the max RT fee that would break even
    # breakeven: avg_gross_pct == RT_fee_pct
    breakeven_rt_fee = avg_gross_pct / 100  # as decimal
    print(f"\n  Breakeven round-trip fee: {breakeven_rt_fee*100:.3f}% (currently {RT_FEE*100:.3f}%)")

    if breakeven_notional:
        print(f"\nKEY FINDING: Strategy breaks even at ${breakeven_notional} notional (combined net P&L = {fmt_dollar(breakeven_pnl)}).", end="")
        if breakeven_notional <= max_notional:
            print(f" This is within the 5% capital constraint (${max_notional:.0f}).")
        else:
            print(f" WARNING: This exceeds the 5% capital constraint (${max_notional:.0f}).")
    else:
        print(f"\nKEY FINDING: Strategy does NOT break even at any tested notional up to ${NOTIONAL_LEVELS[-1]}.")
        print(f"  Notional scaling is irrelevant with percentage-based fees.")
        print(f"  Required: either reduce RT fee below {breakeven_rt_fee*100:.2f}%, or improve trade selection.")

# ---------------------------------------------------------------------------
# Section 2 — Trade Quality Analysis (Revised)
# ---------------------------------------------------------------------------

def section_2(trades):
    section_header("SECTION 2: Trade Quality Analysis")

    # --- Preamble: indicator_count finding ---
    counts = Counter(t["entry_metadata"]["indicator_count"] for t in trades)
    print()
    print("Indicator count distribution across all 320 trades:")
    for k in sorted(counts):
        print(f"  indicator_count={k}: {counts[k]} trades ({counts[k]/len(trades)*100:.1f}%)")
    print()
    print("NOTE: The score_high trigger ($150 notional, indicator_count >= 4) fired on only")
    print(f"4 trades in {len(trades)} — insufficient for statistical conclusions.")
    print("The threshold may be set too high to generate meaningful volume.")

    # --- 2A: Indicator combo analysis ---
    sub_header("2A: Indicator Combo Analysis")
    print("Which specific indicator combinations are most predictive?")
    print()

    combo_trades = defaultdict(list)
    for t in trades:
        combo = fired_indicators(t)
        combo_trades[combo].append(t)

    # Sort by frequency, top 10
    sorted_combos = sorted(combo_trades.items(), key=lambda x: -len(x[1]))[:10]

    print(f"{'Rank':>4s}  {'Count':>5s}  {'Win%':>6s}  {'AvgNet':>8s}  {'TotalNet':>10s}  {'PF':>6s}  Indicators")
    print("-" * 95)
    for rank, (combo, ctrades) in enumerate(sorted_combos, 1):
        pnls = [net_pnl_at(t, 75) for t in ctrades]
        n = len(pnls)
        wr = win_rate(pnls)
        avg_net = statistics.mean(pnls)
        total_net = sum(pnls)
        pf = profit_factor(pnls)
        pf_str = f"{pf:.2f}" if pf < 100 else "inf"
        label = " + ".join(sorted(combo)) if combo else "(none)"
        sample_flag = " *" if n < MIN_SAMPLE else ""
        print(f"{rank:>4d}  {n:>5d}  {fmt_pct(wr):>6s}  {fmt_dollar(avg_net):>8s}  {fmt_dollar(total_net):>10s}  {pf_str:>6s}  {label}{sample_flag}")

    print()
    print("  * = fewer than 15 trades (low confidence)")

    # Find best combo with enough trades
    profitable_combos = []
    for combo, ctrades in sorted_combos:
        if len(ctrades) >= MIN_SAMPLE:
            pnls = [net_pnl_at(t, 75) for t in ctrades]
            pf = profit_factor(pnls)
            if pf > 1.0:
                profitable_combos.append((combo, len(ctrades), pf, sum(pnls)))

    if profitable_combos:
        best = max(profitable_combos, key=lambda x: x[2])
        label = " + ".join(sorted(best[0]))
        print(f"\nKEY FINDING: '{label}' is net profitable at $75 (PF={best[2]:.2f}, {best[1]} trades, {fmt_dollar(best[3])} total).")
    else:
        print("\nKEY FINDING: No indicator combination with >= 15 trades is net profitable at $75 notional.")

    # --- 2B: Score band analysis ---
    sub_header("2B: Score Band Analysis")
    print("Trades grouped by buy_score bands.")
    print()

    bands = [
        ("5.0-5.5", lambda s: 5.0 <= s <= 5.5),
        ("5.5-6.0", lambda s: 5.5 < s < 6.0),
        ("6.0+",    lambda s: s >= 6.0),
    ]

    print(f"{'Band':>8s}  {'Count':>5s}  {'Win%':>6s}  {'AvgNet':>8s}  {'TotalNet':>10s}  {'PF':>6s}")
    print("-" * 55)
    for label, pred in bands:
        band_trades = [t for t in trades if pred(t["entry_metadata"]["buy_score"])]
        if not band_trades:
            print(f"{label:>8s}  {'0':>5s}  {'N/A':>6s}  {'N/A':>8s}  {'N/A':>10s}  {'N/A':>6s}")
            continue
        pnls = [net_pnl_at(t, 75) for t in band_trades]
        n = len(pnls)
        wr = win_rate(pnls)
        avg_net = statistics.mean(pnls)
        total_net = sum(pnls)
        pf = profit_factor(pnls)
        pf_str = f"{pf:.2f}" if pf < 100 else "inf"
        flag = " *" if n < MIN_SAMPLE else ""
        print(f"{label:>8s}  {n:>5d}  {fmt_pct(wr):>6s}  {fmt_dollar(avg_net):>8s}  {fmt_dollar(total_net):>10s}  {pf_str:>6s}{flag}")

    print()
    print("  * = fewer than 15 trades (low confidence)")

    # --- 2C: Market regime analysis ---
    sub_header("2C: Market Regime Analysis")
    print("Trades grouped by entry regime. Does regime filtering improve quality?")
    print()

    regime_trades = defaultdict(list)
    for t in trades:
        r = t.get("entry_metadata", {}).get("regime", {}).get("label", "unknown")
        regime_trades[r].append(t)

    print(f"{'Regime':<25s}  {'Count':>5s}  {'Win%':>6s}  {'AvgNet':>8s}  {'TotalNet':>10s}  {'PF':>6s}  {'Hard%':>6s}")
    print("-" * 80)
    for regime in sorted(regime_trades, key=lambda r: -len(regime_trades[r])):
        rtrades = regime_trades[regime]
        pnls = [net_pnl_at(t, 75) for t in rtrades]
        n = len(pnls)
        wr = win_rate(pnls)
        avg_net = statistics.mean(pnls)
        total_net = sum(pnls)
        pf = profit_factor(pnls)
        pf_str = f"{pf:.2f}" if pf < 100 else "inf"
        hard_count = sum(1 for t in rtrades if t["exit_reason"] == "hard_stop")
        hard_pct = hard_count / n * 100
        flag = " *" if n < MIN_SAMPLE else ""
        print(f"{regime:<25s}  {n:>5d}  {fmt_pct(wr):>6s}  {fmt_dollar(avg_net):>8s}  {fmt_dollar(total_net):>10s}  {pf_str:>6s}  {fmt_pct(hard_pct):>6s}{flag}")

    print()
    print("  * = fewer than 15 trades (low confidence)")

    # Identify if any regime is net profitable
    best_regime = None
    best_pf = 0
    for regime, rtrades in regime_trades.items():
        if len(rtrades) >= MIN_SAMPLE:
            pnls = [net_pnl_at(t, 75) for t in rtrades]
            pf = profit_factor(pnls)
            if pf > best_pf:
                best_pf = pf
                best_regime = regime
    if best_regime and best_pf > 1.0:
        n = len(regime_trades[best_regime])
        print(f"KEY FINDING: '{best_regime}' regime is net profitable at $75 (PF={best_pf:.2f}, {n} trades).")
    else:
        print(f"KEY FINDING: No regime with >= 15 trades is net profitable at $75. Best regime: '{best_regime}' (PF={best_pf:.2f}).")

# ---------------------------------------------------------------------------
# Section 3 — Fee Tier Analysis
# ---------------------------------------------------------------------------

def section_3(trades):
    section_header("SECTION 3: Fee Tier Analysis")
    print()
    print("Question: What Kraken 30-day volume tier is needed to break even?")
    print()

    # Trades per month estimate: 320 trades across 3 × 180-day sets
    # Average: 320/3 sets, each set ~180 days → ~106 trades/180d → ~17.7 trades/month per set
    # But this is per-set; combined rate is what we care about for a single running instance
    total_trades = len(trades)
    avg_per_set = total_trades / 3
    trades_per_month = avg_per_set / 6  # 180 days ≈ 6 months

    sub_header("3A: Combined Net P&L by Fee Tier (at $75 notional)")
    print()
    print(f"{'Tier':<15s}  {'Maker':>6s}  {'Taker':>6s}  {'RT':>6s}  {'NetP&L':>10s}  {'PF':>6s}  {'Fee/Trade':>10s}")
    print("-" * 70)

    breakeven_tier = None
    for tier in FEE_TIERS:
        pnls = [net_pnl_at(t, 75, tier["maker"], tier["taker"]) for t in trades]
        total_net = sum(pnls)
        pf = profit_factor(pnls)
        pf_str = f"{pf:.2f}" if pf < 100 else "inf"
        fee_per = 75 * (tier["maker"] + tier["taker"])
        marker = ""
        if breakeven_tier is None and total_net > 0:
            breakeven_tier = tier
            marker = "  <-- breakeven"
        print(f"{tier['label']:<15s}  {tier['maker']*100:.2f}%  {tier['taker']*100:.2f}%  {(tier['maker']+tier['taker'])*100:.2f}%  {fmt_dollar(total_net):>10s}  {pf_str:>6s}  {fmt_dollar(-fee_per):>10s}{marker}")

    # --- Volume reality check ---
    sub_header("3B: Volume Reality Check")
    print()
    print(f"Estimated trade frequency: ~{trades_per_month:.0f} trades/month (from {avg_per_set:.0f} trades over 180 days)")
    print()
    print(f"{'Notional':>10s}  {'Mo.Volume':>12s}  {'Tier Reached':>15s}  {'Net P&L':>10s}  {'Viable?':>8s}")
    print("-" * 65)

    for n in [75, 100, 150, 200, 250, 300]:
        monthly_vol = trades_per_month * n
        # Find which tier this volume qualifies for
        qualified_tier = FEE_TIERS[0]
        for tier in FEE_TIERS:
            if monthly_vol >= tier["monthly_vol"]:
                qualified_tier = tier
        # Net P&L at this notional with the qualified tier's fees
        pnls = [net_pnl_at(t, n, qualified_tier["maker"], qualified_tier["taker"]) for t in trades]
        total_net = sum(pnls)
        viable = "YES" if total_net > 0 and n <= CAPITAL * MAX_POSITION_PCT else "no"
        if total_net > 0 and n > CAPITAL * MAX_POSITION_PCT:
            viable = "size!"  # profitable but exceeds position limit
        print(f"${n:>8d}  ${monthly_vol:>10,.0f}  {qualified_tier['label']:>15s}  {fmt_dollar(total_net):>10s}  {viable:>8s}")

    print()
    if breakeven_tier:
        print(f"KEY FINDING: Strategy breaks even at the {breakeven_tier['label']} fee tier ($75 notional).")
        vol_needed = breakeven_tier["monthly_vol"]
        notional_needed = vol_needed / trades_per_month if trades_per_month > 0 else float("inf")
        print(f"  That tier requires ${vol_needed:,}/month volume = ~${notional_needed:,.0f}/trade at {trades_per_month:.0f} trades/month.")
    else:
        print("KEY FINDING: Strategy does NOT break even at any tested fee tier at $75 notional.")

# ---------------------------------------------------------------------------
# Section 4 — Exit Reason Breakdown
# ---------------------------------------------------------------------------

def section_4(trades):
    section_header("SECTION 4: Exit Reason Breakdown")
    print()
    print("Question: Which exit categories are net profitable vs. net negative after fees?")
    print("Note: 'roc_momo_20m' and 'score' are signal-based exits (sell signal detected),")
    print("      not stop-based exits. Interpret differently from trailing/hard/stale stops.")
    print()

    # Discover all exit reasons
    exit_groups = defaultdict(list)
    for t in trades:
        exit_groups[t["exit_reason"]].append(t)

    # --- Combined view ---
    sub_header("All Sets Combined (at $75 notional)")
    print()
    print(f"{'Exit Reason':<18s}  {'N':>4s}  {'%Tot':>5s}  {'Win%':>6s}  {'AvgGross':>9s}  {'AvgFee':>8s}  {'AvgNet':>8s}  {'TotalNet':>10s}  {'Hours':>6s}  {'MFE%':>6s}  {'MAE%':>7s}")
    print("-" * 115)

    largest_drag = None
    largest_drag_val = 0

    for reason in sorted(exit_groups, key=lambda r: -len(exit_groups[r])):
        rtrades = exit_groups[reason]
        n = len(rtrades)
        pct_total = n / len(trades) * 100
        pnls = [net_pnl_at(t, 75) for t in rtrades]
        gross_pnls = [75 * gross_pnl_pct(t) for t in rtrades]
        fee_per = 75 * RT_FEE
        avg_gross = statistics.mean(gross_pnls)
        avg_net = statistics.mean(pnls)
        total_net = sum(pnls)
        wr = win_rate(pnls)
        avg_hours = statistics.mean(t["bars_held"] / 60 for t in rtrades)
        avg_mfe = statistics.mean(t["mfe_pct"] for t in rtrades)
        avg_mae = statistics.mean(t["mae_pct"] for t in rtrades)

        print(f"{reason:<18s}  {n:>4d}  {fmt_pct(pct_total):>5s}  {fmt_pct(wr):>6s}  {fmt_dollar(avg_gross):>9s}  {fmt_dollar(-fee_per):>8s}  {fmt_dollar(avg_net):>8s}  {fmt_dollar(total_net):>10s}  {avg_hours:>6.1f}  {fmt_pct(avg_mfe):>6s}  {avg_mae:>+7.1f}%")

        if total_net < largest_drag_val:
            largest_drag_val = total_net
            largest_drag = reason

    # --- Per-set breakdown ---
    sub_header("Per-Set Breakdown (at $75 notional)")
    print()
    for set_label in ["set_a", "set_b", "set_c"]:
        set_trades = [t for t in trades if t["_set"] == set_label]
        set_exit = defaultdict(list)
        for t in set_trades:
            set_exit[t["exit_reason"]].append(t)
        print(f"  {set_label.upper()} ({len(set_trades)} trades):")
        print(f"  {'Exit Reason':<18s}  {'N':>4s}  {'Win%':>6s}  {'TotalNet':>10s}  {'PF':>6s}")
        print(f"  {'-'*55}")
        for reason in sorted(set_exit, key=lambda r: -len(set_exit[r])):
            rtrades = set_exit[reason]
            pnls = [net_pnl_at(t, 75) for t in rtrades]
            total_net = sum(pnls)
            wr_val = win_rate(pnls)
            pf = profit_factor(pnls)
            pf_str = f"{pf:.2f}" if pf < 100 else "inf"
            print(f"  {reason:<18s}  {len(rtrades):>4d}  {fmt_pct(wr_val):>6s}  {fmt_dollar(total_net):>10s}  {pf_str:>6s}")
        print()

    # Net positive exits?
    net_positive = [(r, sum(net_pnl_at(t, 75) for t in exit_groups[r]))
                    for r in exit_groups
                    if sum(net_pnl_at(t, 75) for t in exit_groups[r]) > 0]

    print()
    if net_positive:
        pos_str = ", ".join(f"'{r}' ({fmt_dollar(v)})" for r, v in sorted(net_positive, key=lambda x: -x[1]))
        print(f"KEY FINDING: Net positive exit reasons at $75: {pos_str}.")
    else:
        print("KEY FINDING: NO exit reason is net positive at $75 notional.")

    if largest_drag:
        print(f"  Largest dollar drag: '{largest_drag}' at {fmt_dollar(largest_drag_val)} total net P&L.")

# ---------------------------------------------------------------------------
# Section 5 — Kelly Criterion & Risk/Reward
# ---------------------------------------------------------------------------

def section_5(trades):
    section_header("SECTION 5: Kelly Criterion & Risk/Reward")
    print()
    print("Question: What does Kelly suggest for optimal sizing?")
    print()

    # --- Combined ---
    sub_header("Combined Dataset")
    _kelly_analysis(trades, "Combined")

    # --- Per set ---
    sub_header("Per-Set Kelly Estimates")
    for set_label in ["set_a", "set_b", "set_c"]:
        set_trades = [t for t in trades if t["_set"] == set_label]
        _kelly_analysis(set_trades, set_label.upper(), brief=True)


def _kelly_analysis(trades, label, brief=False):
    pnls = [net_pnl_at(t, 75) for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    if not wins or not losses:
        print(f"  {label}: Cannot compute Kelly — {'no wins' if not wins else 'no losses'}.")
        return

    total = len(pnls)
    wr = len(wins) / total
    avg_win = statistics.mean(wins)
    avg_loss = abs(statistics.mean(losses))
    wl_ratio = avg_win / avg_loss if avg_loss > 0 else float("inf")

    kelly = wr - (1 - wr) / wl_ratio
    half_kelly = kelly / 2

    kelly_notional = kelly * CAPITAL
    half_kelly_notional = half_kelly * CAPITAL

    ev_at_75 = (wr * avg_win) - ((1 - wr) * avg_loss)

    if not brief:
        print(f"  Trades: {total}")
        print(f"  Win rate: {fmt_pct(wr * 100)} ({len(wins)} wins / {len(losses)} losses)")
        print(f"  Avg win:  {fmt_dollar(avg_win)} (at $75 notional)")
        print(f"  Avg loss: {fmt_dollar(-avg_loss)} (at $75 notional)")
        print(f"  Win/Loss ratio: {wl_ratio:.3f}")
        print()
        print(f"  Kelly fraction (f*):  {kelly:+.4f}")
        print(f"  Half-Kelly:           {half_kelly:+.4f}")
        print(f"  Kelly notional:       {fmt_dollar(kelly_notional)} (against ${CAPITAL:,} capital)")
        print(f"  Half-Kelly notional:  {fmt_dollar(half_kelly_notional)}")
        print()
        print(f"  Expected Value per trade at $75:  {fmt_dollar(ev_at_75)}")

        if kelly_notional > 0:
            # EV at Kelly notional
            pnls_kelly = [net_pnl_at(t, kelly_notional) for t in trades]
            wins_k = [p for p in pnls_kelly if p > 0]
            losses_k = [p for p in pnls_kelly if p <= 0]
            avg_win_k = statistics.mean(wins_k) if wins_k else 0
            avg_loss_k = abs(statistics.mean(losses_k)) if losses_k else 0
            ev_kelly = (wr * avg_win_k) - ((1 - wr) * avg_loss_k)
            print(f"  Expected Value per trade at Kelly ({fmt_dollar(kelly_notional)}):  {fmt_dollar(ev_kelly)}")

        print()
        if kelly <= 0:
            print("  NOTE: Kelly fraction is NEGATIVE. The strategy's edge at $75 notional")
            print("  with current fees does not justify sizing up. The priority should be")
            print("  reducing fees (higher volume tier) or increasing notional to improve")
            print("  the gross-to-fee ratio before applying Kelly sizing.")
        elif kelly_notional < 75:
            print(f"  NOTE: Kelly suggests SMALLER sizing than current $75 ({fmt_dollar(kelly_notional)}).")
            print("  The edge is thin relative to variance. Half-Kelly is the conservative choice.")
        elif kelly_notional > CAPITAL * MAX_POSITION_PCT:
            print(f"  NOTE: Kelly notional ({fmt_dollar(kelly_notional)}) exceeds the 5% capital constraint")
            print(f"  (${CAPITAL * MAX_POSITION_PCT:,.0f}). Use the constraint as the ceiling.")
        else:
            print(f"  NOTE: Kelly suggests sizing at {fmt_dollar(kelly_notional)}. Half-Kelly ({fmt_dollar(half_kelly_notional)})")
            print("  is the standard conservative approach to account for estimation error.")
    else:
        print(f"  {label}: WR={fmt_pct(wr*100)}, W/L={wl_ratio:.3f}, Kelly={kelly:+.4f} ({fmt_dollar(kelly_notional)}), EV@$75={fmt_dollar(ev_at_75)}")

    print()
    if not brief:
        if kelly <= 0:
            print(f"KEY FINDING: Kelly fraction is negative ({kelly:+.4f}). The fee-adjusted edge")
            print(f"  does not support sizing up. Reduce fees or increase notional first.")
        else:
            print(f"KEY FINDING: Kelly suggests {fmt_dollar(kelly_notional)} per trade (half-Kelly: {fmt_dollar(half_kelly_notional)}).")


# ---------------------------------------------------------------------------
# Strategic Summary
# ---------------------------------------------------------------------------

def strategic_summary(trades):
    section_header("STRATEGIC SUMMARY")
    print()
    print("All five key findings consolidated for decision-making:")
    print()

    # Recompute key metrics for the summary
    # 1. Breakeven notional
    avg_gross_pct = statistics.mean(gross_pnl_pct(t) for t in trades) * 100
    breakeven_rt = avg_gross_pct / 100
    for n in NOTIONAL_LEVELS:
        pnls = [net_pnl_at(t, n) for t in trades]
        if sum(pnls) > 0:
            print(f"  1. NOTIONAL: Strategy breaks even at ${n} notional (combined net P&L = {fmt_dollar(sum(pnls))}).")
            break
    else:
        print(f"  1. NOTIONAL: Notional scaling cannot fix percentage-based fees. Avg gross return ({avg_gross_pct:+.3f}%) < RT fee ({RT_FEE*100:.3f}%). Need RT fee < {breakeven_rt*100:.2f}%.")

    # 2. Trade quality
    combo_trades = defaultdict(list)
    for t in trades:
        combo = fired_indicators(t)
        combo_trades[combo].append(t)
    profitable_combos = []
    for combo, ctrades in combo_trades.items():
        if len(ctrades) >= MIN_SAMPLE:
            pnls = [net_pnl_at(t, 75) for t in ctrades]
            pf = profit_factor(pnls)
            if pf > 1.0:
                profitable_combos.append((combo, pf))
    if profitable_combos:
        best = max(profitable_combos, key=lambda x: x[1])
        label = " + ".join(sorted(best[0]))
        print(f"  2. QUALITY: '{label}' combo is net profitable at $75 (PF={best[1]:.2f}).")
    else:
        print(f"  2. QUALITY: No indicator combo with >= {MIN_SAMPLE} trades is net profitable at $75.")

    # 3. Fee tier
    for tier in FEE_TIERS:
        pnls = [net_pnl_at(t, 75, tier["maker"], tier["taker"]) for t in trades]
        if sum(pnls) > 0:
            print(f"  3. FEE TIER: Breakeven at {tier['label']} tier ($75 notional).")
            break
    else:
        print(f"  3. FEE TIER: Not profitable at any fee tier at $75 notional.")

    # 4. Exit drag
    exit_totals = {}
    for t in trades:
        r = t["exit_reason"]
        if r not in exit_totals:
            exit_totals[r] = 0
        exit_totals[r] += net_pnl_at(t, 75)
    worst = min(exit_totals, key=exit_totals.get)
    best_exit = max(exit_totals, key=exit_totals.get)
    print(f"  4. EXIT DRAG: Largest drag is '{worst}' ({fmt_dollar(exit_totals[worst])}). Best: '{best_exit}' ({fmt_dollar(exit_totals[best_exit])}).")

    # 5. Kelly
    pnls = [net_pnl_at(t, 75) for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    if wins and losses:
        wr = len(wins) / len(pnls)
        avg_win = statistics.mean(wins)
        avg_loss = abs(statistics.mean(losses))
        wl_ratio = avg_win / avg_loss
        kelly = wr - (1 - wr) / wl_ratio
        if kelly <= 0:
            print(f"  5. KELLY: Negative ({kelly:+.4f}) — fee-adjusted edge insufficient for sizing up.")
        else:
            print(f"  5. KELLY: {kelly:+.4f} → ${kelly * CAPITAL:,.0f}/trade (half-Kelly: ${kelly * CAPITAL / 2:,.0f}).")

    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 80)
    print("  BotTrader Fee/Sizing Analysis Report")
    print(f"  Date: 2026-04-10")
    print(f"  Datasets: 3 OOS sets, Kraken fees ({MAKER_FEE*100:.2f}% maker / {TAKER_FEE*100:.2f}% taker)")
    print("=" * 80)

    trades, skipped = load_trades()
    print(f"\nLoaded {len(trades)} trades ({skipped} skipped)")

    # Summary by set
    for set_label in ["set_a", "set_b", "set_c"]:
        n = sum(1 for t in trades if t["_set"] == set_label)
        print(f"  {set_label}: {n} trades")

    section_1(trades)
    section_2(trades)
    section_3(trades)
    section_4(trades)
    section_5(trades)
    strategic_summary(trades)


if __name__ == "__main__":
    main()
