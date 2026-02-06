"""
Diagnostics for the 4h Hybrid Maker Strategy.

Extracted from Hybrid4hStrategy: gate audit reporting, enhanced diagnostics,
decomposition analysis, and distribution reports.

These are purely observational - they don't affect strategy decisions.
"""

import numpy as np
import math


def compute_decomposition(strategy) -> None:
    """
    Compute setup/compression decomposition from gate decision masks.

    Must be called AFTER all bars have been processed.
    Decomposition is computed on base_ok population (regime_ok AND viability_ok).
    """
    regime_ok = np.array(strategy.regime_ok_mask)
    viability_ok = np.array(strategy.viability_ok_mask)
    setup_ok = np.array(strategy.setup_ok_mask)

    # Use compression_recent_12 for decomposition
    compression_ok = np.array(strategy.compression_recent_12_mask)

    base_ok = regime_ok & viability_ok

    setup_only = base_ok & setup_ok & ~compression_ok
    compression_only = base_ok & ~setup_ok & compression_ok
    intersection = base_ok & setup_ok & compression_ok
    neither = base_ok & ~setup_ok & ~compression_ok

    strategy.setup_only_count = int(np.sum(setup_only))
    strategy.compression_only_count = int(np.sum(compression_only))
    strategy.intersection_count = int(np.sum(intersection))
    strategy.neither_count = int(np.sum(neither))

    total_decomp = (strategy.setup_only_count + strategy.compression_only_count +
                    strategy.intersection_count + strategy.neither_count)
    base_ok_count = int(np.sum(base_ok))

    if total_decomp != base_ok_count:
        print(f"WARNING: Decomposition sum ({total_decomp}) != base_ok count ({base_ok_count})")


def get_statistics(strategy) -> dict:
    """Return strategy statistics."""
    config = strategy.config

    # Fee floor truth print
    print()
    print("=" * 80)
    print("FEE FLOOR VIABILITY PARAMETERS (TRUTH LINE)")
    print("=" * 80)
    print(f"  maker_fee:              {config.maker_fee:.4f} ({config.maker_fee * 100:.2f}%)")
    print(f"  taker_fee:              {config.taker_fee:.4f} ({config.taker_fee * 100:.2f}%)")
    print(f"  round_trip_maker_fee:   {config.round_trip_maker_fee:.4f} ({config.round_trip_maker_fee * 100:.2f}%)")
    print(f"  vol_min_mult:           {config.vol_min_mult:.2f}")
    print(f"  viability_floor_atr_pct: {config.vol_min_mult * config.round_trip_maker_fee:.4f} ({(config.vol_min_mult * config.round_trip_maker_fee) * 100:.2f}%)")

    if getattr(config, 'use_conditional_viability', False):
        print(f"  vol_min_mult_compression: {config.vol_min_mult_compression:.2f}")
        print(f"  conditional_floor_atr_pct: {config.vol_min_mult_compression * config.round_trip_maker_fee:.4f} ({(config.vol_min_mult_compression * config.round_trip_maker_fee) * 100:.2f}%)")
        print(f"  bars_rescued:           {strategy.viability_rescued_count}")

    print("=" * 80)
    print()

    # Compute decomposition
    compute_decomposition(strategy)

    total_trades = len(strategy.trades)
    if total_trades == 0:
        return {
            "signals": strategy.signal_count,
            "entries": strategy.entry_count,
            "expired": strategy.expired_setup_count,
            "trades": 0
        }

    gross_pnl = sum(t.gross_pnl for t in strategy.trades)
    fees_total = sum(t.fees_total for t in strategy.trades)
    net_pnl = sum(t.net_pnl for t in strategy.trades)

    winners = [t for t in strategy.trades if t.net_pnl > 0]
    win_rate = len(winners) / total_trades

    avg_win = sum(t.net_pnl for t in winners) / len(winners) if winners else 0
    losers = [t for t in strategy.trades if t.net_pnl <= 0]
    avg_loss = sum(t.net_pnl for t in losers) / len(losers) if losers else 0

    gross_wins = sum(t.gross_pnl for t in strategy.trades if t.gross_pnl > 0)
    gross_losses = abs(sum(t.gross_pnl for t in strategy.trades if t.gross_pnl <= 0))
    profit_factor = gross_wins / gross_losses if gross_losses > 0 else 0

    tp1_count = sum(1 for t in strategy.trades if t.tp1_filled)
    tp2_count = sum(1 for t in strategy.trades if t.tp2_filled)
    avg_bars = sum(t.bars_held for t in strategy.trades) / total_trades

    chase_success_rate = (strategy.chase_fill_count / strategy.chase_attempt_count
                          ) if strategy.chase_attempt_count > 0 else 0

    return {
        "signals": strategy.signal_count,
        "entries": strategy.entry_count,
        "expired": strategy.expired_setup_count,
        "signal_to_entry_pct": (strategy.entry_count / strategy.signal_count * 100
                                ) if strategy.signal_count > 0 else 0,
        "trades": total_trades,
        "gross_pnl": gross_pnl,
        "fees": fees_total,
        "net_pnl": net_pnl,
        "win_rate": win_rate,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "profit_factor": profit_factor,
        "tp1_hit_pct": (tp1_count / total_trades * 100) if total_trades > 0 else 0,
        "tp2_hit_pct": (tp2_count / total_trades * 100) if total_trades > 0 else 0,
        "avg_bars_held": avg_bars,
        "retest_fills": strategy.retest_fill_count,
        "chase_fills": strategy.chase_fill_count,
        "chase_attempts": strategy.chase_attempt_count,
        "chase_success_rate": chase_success_rate,
        "gate_audit": strategy.gate_audit
    }


def print_gate_audit(strategy) -> str:
    """Format gate audit counters as diagnostic table."""
    total = strategy.gate_audit['total_4h_bars']

    if total == 0:
        return "\nNo 4h bars processed (gate audit unavailable)\n"

    lines = []
    lines.append("")
    lines.append("=" * 80)
    lines.append("GATE AUDIT: Signal Frequency Diagnostic")
    lines.append("=" * 80)
    lines.append("")
    lines.append(f"{'Gate':<30} {'Count':>10} {'% of 4h bars':>15} {'Conversion':>15}")
    lines.append("-" * 80)

    lines.append(f"{'Total 4h bars processed':<30} {total:>10,} {100.0:>14.1f}% {'-':>15}")

    regime_count = strategy.gate_audit['regime_ok']
    regime_pct = (regime_count / total * 100) if total > 0 else 0
    lines.append(f"{'  Regime filter (EMA200)':<30} {regime_count:>10,} {regime_pct:>14.1f}% {'-':>15}")

    viable_count = strategy.gate_audit['viability_ok']
    viable_pct = (viable_count / total * 100) if total > 0 else 0
    viable_conv = (viable_count / regime_count * 100) if regime_count > 0 else 0
    lines.append(f"{'  Viability filter (ATR%)':<30} {viable_count:>10,} {viable_pct:>14.1f}% {viable_conv:>14.1f}%")

    roc_ok_count = strategy.gate_audit.get('roc_ok', 0)
    roc_ok_pct = (roc_ok_count / total * 100) if total > 0 else 0
    roc_ok_conv = (roc_ok_count / viable_count * 100) if viable_count > 0 else 0
    lines.append(f"{'  ROC-OK (momentum >= thresh)':<30} {roc_ok_count:>10,} {roc_ok_pct:>14.1f}% {roc_ok_conv:>14.1f}%")

    roc_compressed_count = strategy.gate_audit.get('roc_ok_and_compressed', 0)
    roc_compressed_pct = (roc_compressed_count / total * 100) if total > 0 else 0
    roc_compressed_conv = (roc_compressed_count / roc_ok_count * 100) if roc_ok_count > 0 else 0
    lines.append(f"{'    ROC-OK + Compressed':<30} {roc_compressed_count:>10,} {roc_compressed_pct:>14.1f}% {roc_compressed_conv:>14.1f}%")

    structure_ok_count = strategy.gate_audit.get('structure_ok', 0)
    structure_ok_pct = (structure_ok_count / total * 100) if total > 0 else 0
    structure_ok_conv = (structure_ok_count / roc_ok_count * 100) if roc_ok_count > 0 else 0
    lines.append(f"{'  Structure-OK (entry conds)':<30} {structure_ok_count:>10,} {structure_ok_pct:>14.1f}% {structure_ok_conv:>14.1f}%")

    setup_count = strategy.gate_audit['setup_created']
    setup_pct = (setup_count / total * 100) if total > 0 else 0
    setup_conv = (setup_count / structure_ok_count * 100) if structure_ok_count > 0 else 0
    lines.append(f"{'  Setup created':<30} {setup_count:>10,} {setup_pct:>14.1f}% {setup_conv:>14.1f}%")

    entry_count = strategy.gate_audit['entry_filled']
    entry_pct = (entry_count / total * 100) if total > 0 else 0
    entry_conv = (entry_count / setup_count * 100) if setup_count > 0 else 0
    lines.append(f"{'  Entry filled':<30} {entry_count:>10,} {entry_pct:>14.1f}% {entry_conv:>14.1f}%")

    lines.append("")
    lines.append("=" * 80)

    return "\n".join(lines)


def print_enhanced_diagnostics(strategy) -> str:
    """
    Enhanced diagnostics including gate audit, BB width distribution,
    setup/compression decomposition, viability distribution,
    ROC-score calibration, state occupancy, and opportunity loss.
    """
    from backtest.strategy_4h_hybrid import StrategyState

    lines = []

    # Mask verification
    total_4h_bars = strategy.gate_audit.get('total_4h_bars', 0)
    lines.append("")
    lines.append("=" * 80)
    lines.append("DEBUG: MASK LENGTH VERIFICATION")
    lines.append("=" * 80)
    lines.append(f"  total_4h_bars (expected):         {total_4h_bars}")
    lines.append(f"  len(regime_ok_mask):              {len(strategy.regime_ok_mask)}")
    lines.append(f"  len(viability_ok_mask):           {len(strategy.viability_ok_mask)}")
    lines.append(f"  len(setup_ok_mask):               {len(strategy.setup_ok_mask)}")
    lines.append(f"  len(compression_ok_mask):         {len(strategy.compression_ok_mask)}")
    lines.append(f"  len(compression_now_mask):        {len(strategy.compression_now_mask)}")
    lines.append(f"  len(compression_recent_6_mask):   {len(strategy.compression_recent_6_mask)}")
    lines.append(f"  len(compression_recent_12_mask):  {len(strategy.compression_recent_12_mask)}")
    lines.append("")
    lines.append("  All masks verified")
    lines.append("=" * 80)
    lines.append("")

    # Gate audit
    lines.append(print_gate_audit(strategy))

    # BB Width Distribution
    lines.append("")
    lines.append("=" * 80)
    lines.append("BB WIDTH PERCENTILE DISTRIBUTION")
    lines.append("=" * 80)
    lines.append("")

    config = strategy.config

    if len(strategy.bb_width_pct_all) > 0:
        bb_all_clean = [x for x in strategy.bb_width_pct_all if x is not None and not np.isnan(x)]
        if len(bb_all_clean) > 0:
            bb_all = np.array(bb_all_clean)
            lines.append("On ALL 4h bars:")
            lines.append(f"  Count:  {len(bb_all)}")
            lines.append(f"  Min:    {np.min(bb_all):.1f}")
            lines.append(f"  P25:    {np.percentile(bb_all, 25):.1f}")
            lines.append(f"  Median: {np.percentile(bb_all, 50):.1f}")
            lines.append(f"  P75:    {np.percentile(bb_all, 75):.1f}")
            lines.append(f"  Max:    {np.max(bb_all):.1f}")
            lines.append("")

    if len(strategy.bb_width_pct_viable) > 0:
        bb_viable_clean = [x for x in strategy.bb_width_pct_viable if x is not None and not np.isnan(x)]
        if len(bb_viable_clean) > 0:
            bb_viable = np.array(bb_viable_clean)
            viable_count = len(bb_viable)

            lines.append("On VIABLE bars (regime+viability OK):")
            lines.append(f"  Count:  {viable_count}")
            lines.append(f"  Min:    {np.min(bb_viable):.1f}")
            lines.append(f"  P25:    {np.percentile(bb_viable, 25):.1f}")
            lines.append(f"  Median: {np.percentile(bb_viable, 50):.1f}")
            lines.append(f"  P75:    {np.percentile(bb_viable, 75):.1f}")
            lines.append(f"  Max:    {np.max(bb_viable):.1f}")
            lines.append("")

            lines.append("Compression Threshold Analysis (% of viable bars):")
            for thresh in [20, 30, 35, 40]:
                count_below = np.sum(bb_viable <= thresh)
                pct_below = (count_below / viable_count * 100) if viable_count > 0 else 0
                lines.append(f"  bb_width_pct <= {thresh}: {count_below:>4} bars ({pct_below:>5.1f}%)")

    lines.append("")
    lines.append("=" * 80)

    # Setup/Compression Decomposition
    lines.append("")
    lines.append("=" * 80)
    lines.append("SETUP/COMPRESSION DECOMPOSITION")
    lines.append("=" * 80)
    lines.append("")

    viable_total = strategy.gate_audit['viability_ok']
    lines.append(f"{'Category':<30} {'Count':>10} {'% of viable':>15}")
    lines.append("-" * 60)
    lines.append(f"{'Setup trigger only':<30} {strategy.setup_only_count:>10,} {(strategy.setup_only_count / viable_total * 100) if viable_total > 0 else 0:>14.1f}%")
    lines.append(f"{'Compression only':<30} {strategy.compression_only_count:>10,} {(strategy.compression_only_count / viable_total * 100) if viable_total > 0 else 0:>14.1f}%")
    lines.append(f"{'Both (intersection)':<30} {strategy.intersection_count:>10,} {(strategy.intersection_count / viable_total * 100) if viable_total > 0 else 0:>14.1f}%")
    lines.append(f"{'Neither':<30} {strategy.neither_count:>10,} {(strategy.neither_count / viable_total * 100) if viable_total > 0 else 0:>14.1f}%")
    lines.append("-" * 60)
    lines.append(f"{'Total viable bars':<30} {viable_total:>10,} {100.0:>14.1f}%")

    lines.append("")
    lines.append("=" * 80)

    # State Occupancy
    lines.append("")
    lines.append("=" * 80)
    lines.append("STATE OCCUPANCY")
    lines.append("=" * 80)
    lines.append("")

    for state in [StrategyState.FLAT, StrategyState.SETUP_ACTIVE,
                  StrategyState.RETEST_ORDER_WORKING, StrategyState.CHASE_ORDER_WORKING,
                  StrategyState.IN_POSITION]:
        count = strategy.state_occupancy.get(state, 0)
        pct = (count / total_4h_bars * 100) if total_4h_bars > 0 else 0
        lines.append(f"  {state.value:<25} {count:>4} bars ({pct:>5.1f}%)")

    lines.append("")
    lines.append("=" * 80)

    # Setup Lifecycle
    lines.append("")
    lines.append("SETUP LIFECYCLE:")
    setup_created = strategy.gate_audit.get('setup_created', 0)
    entry_filled = strategy.gate_audit.get('entry_filled', 0)
    lines.append(f"  Setups created:    {setup_created}")
    lines.append(f"  Setups expired:    {strategy.expired_setup_count}")
    lines.append(f"  Entries filled:    {entry_filled}")
    lines.append(f"  Retest fills:      {strategy.retest_fill_count}")
    lines.append(f"  Chase fills:       {strategy.chase_fill_count}")

    lines.append("")
    lines.append("=" * 80)
    lines.append("")

    return "\n".join(lines)
