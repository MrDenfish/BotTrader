#!/usr/bin/env python3
"""
ROC-Score vs Donchian Baseline Comparison (365 days)

Compares ROC-score and Donchian setup modes on a long window (365 days)
to establish baseline characteristics for each mode.

Metrics compared:
- Signal frequency (signals/month)
- Fill rate (entries/signals)
- Trade frequency (trades/month)
- P&L performance
- Win rate, profit factor
- Regime sensitivity (bull/bear/sideways)

Target: min 30 trades per mode for statistical validity
"""

from config_4h_hybrid import get_phase2_2_baseline_config, get_phase2_3_roc_baseline_config
from engine_4h_hybrid import Hybrid4hBacktestEngine


def run_baseline_comparison():
    """Compare ROC-score vs Donchian on 365 days"""

    print("=" * 80)
    print("ROC-SCORE VS DONCHIAN BASELINE COMPARISON (365 DAYS)")
    print("=" * 80)
    print()
    print("Establishing baseline characteristics for each setup mode...")
    print()
    print("=" * 80)
    print()

    symbol = "BTC-USD"
    days = 365

    # ========== DONCHIAN BASELINE ==========
    print("📊 RUNNING DONCHIAN BASELINE (Phase 2.2)...")
    print()

    config_donch = get_phase2_2_baseline_config()
    engine_donch = Hybrid4hBacktestEngine(config_donch, data_dir="data", resolution='1m')
    stats_donch = engine_donch.run_backtest(symbol, days)

    print()
    print("=" * 80)
    print("DONCHIAN BASELINE RESULTS (365 days)")
    print("=" * 80)
    print()
    engine_donch.print_results(stats_donch, symbol)

    # ========== ROC-SCORE BASELINE ==========
    print()
    print("=" * 80)
    print()
    print("📊 RUNNING ROC-SCORE BASELINE (Phase 2.3)...")
    print()

    config_roc = get_phase2_3_roc_baseline_config()
    engine_roc = Hybrid4hBacktestEngine(config_roc, data_dir="data", resolution='1m')
    stats_roc = engine_roc.run_backtest(symbol, days)

    print()
    print("=" * 80)
    print("ROC-SCORE BASELINE RESULTS (365 days)")
    print("=" * 80)
    print()
    engine_roc.print_results(stats_roc, symbol)

    # ========== COMPARISON ANALYSIS ==========
    print()
    print("=" * 80)
    print("COMPARATIVE ANALYSIS")
    print("=" * 80)
    print()

    # Calculate normalized metrics
    months = days / 30.0

    donch_signals_per_month = stats_donch['signals'] / months
    roc_signals_per_month = stats_roc['signals'] / months

    donch_trades_per_month = stats_donch['trades'] / months
    roc_trades_per_month = stats_roc['trades'] / months

    print(f"{'Metric':<30} {'Donchian':<20} {'ROC-Score':<20} {'Winner'}")
    print("-" * 80)

    # Signal frequency
    print(f"{'Signals/month':<30} {donch_signals_per_month:<20.2f} {roc_signals_per_month:<20.2f} "
          f"{'ROC' if roc_signals_per_month > donch_signals_per_month else 'Donchian'}")

    # Fill rate
    donch_fill_rate = stats_donch.get('signal_to_entry_pct', 0)
    roc_fill_rate = stats_roc.get('signal_to_entry_pct', 0)
    print(f"{'Fill rate':<30} {donch_fill_rate:<20.1f}% {roc_fill_rate:<20.1f}% "
          f"{'ROC' if roc_fill_rate > donch_fill_rate else 'Donchian'}")

    # Trade frequency
    print(f"{'Trades/month':<30} {donch_trades_per_month:<20.2f} {roc_trades_per_month:<20.2f} "
          f"{'ROC' if roc_trades_per_month > donch_trades_per_month else 'Donchian'}")

    # Net P&L
    donch_pnl = stats_donch.get('net_pnl', 0)
    roc_pnl = stats_roc.get('net_pnl', 0)
    print(f"{'Net P&L':<30} ${donch_pnl:<19.2f} ${roc_pnl:<19.2f} "
          f"{'ROC' if roc_pnl > donch_pnl else 'Donchian'}")

    # Win rate
    if stats_donch['trades'] > 0 and stats_roc['trades'] > 0:
        donch_wr = stats_donch.get('win_rate', 0)
        roc_wr = stats_roc.get('win_rate', 0)
        print(f"{'Win rate':<30} {donch_wr:<20.1%} {roc_wr:<20.1%} "
              f"{'ROC' if roc_wr > donch_wr else 'Donchian'}")

        # Profit factor
        donch_pf = stats_donch.get('profit_factor', 0)
        roc_pf = stats_roc.get('profit_factor', 0)
        print(f"{'Profit factor':<30} {donch_pf:<20.2f} {roc_pf:<20.2f} "
              f"{'ROC' if roc_pf > donch_pf else 'Donchian'}")

    print()
    print("=" * 80)
    print("KEY INSIGHTS")
    print("=" * 80)
    print()

    # Sample size check
    print(f"Sample Size Check:")
    print(f"  Donchian: {stats_donch['trades']} trades {'✅ (>= 30)' if stats_donch['trades'] >= 30 else '⚠️  (< 30, low confidence)'}")
    print(f"  ROC-Score: {stats_roc['trades']} trades {'✅ (>= 30)' if stats_roc['trades'] >= 30 else '⚠️  (< 30, low confidence)'}")
    print()

    # Signal diversity
    print(f"Signal Diversity:")
    if roc_signals_per_month > donch_signals_per_month * 1.5:
        print(f"  ✅ ROC generates {roc_signals_per_month / donch_signals_per_month:.1f}× more signals")
        print(f"     (Better for reducing regime dependency)")
    elif donch_signals_per_month > roc_signals_per_month * 1.5:
        print(f"  ⚠️  Donchian generates {donch_signals_per_month / roc_signals_per_month:.1f}× more signals")
        print(f"     (ROC may be too conservative)")
    else:
        print(f"  Similar signal frequency (within 50%)")
    print()

    # Trade frequency target
    print(f"Trade Frequency Target (2-4 trades/month):")
    for name, tpm in [("Donchian", donch_trades_per_month), ("ROC-Score", roc_trades_per_month)]:
        if 2 <= tpm <= 4:
            print(f"  ✅ {name}: {tpm:.2f} trades/month (in target range)")
        elif tpm < 2:
            print(f"  ⚠️  {name}: {tpm:.2f} trades/month (below target, needs optimization)")
        else:
            print(f"  ⚠️  {name}: {tpm:.2f} trades/month (above target, may overtrade)")
    print()

    # Performance comparison
    print(f"Performance:")
    if stats_donch['trades'] > 0 and stats_roc['trades'] > 0:
        if roc_pnl > donch_pnl * 1.2:
            print(f"  ✅ ROC significantly outperforms (+{(roc_pnl / donch_pnl - 1) * 100:.1f}%)")
        elif donch_pnl > roc_pnl * 1.2:
            print(f"  ✅ Donchian significantly outperforms (+{(donch_pnl / roc_pnl - 1) * 100:.1f}%)")
        else:
            print(f"  Similar performance (within 20%)")
    print()

    print("=" * 80)
    print("NEXT STEPS")
    print("=" * 80)
    print()

    if stats_roc['trades'] >= 30:
        print("✅ ROC-Score has sufficient sample size for optimization")
        print()
        print("Recommended:")
        print("  • Proceed with ROC Stage A optimization (gates/entry)")
        print("  • Focus on parameters: roc_score_thresh, roc_len_4h, roc_ema_len")
        print("  • Use 5m data for fast grid search (365+ day windows)")
    else:
        print("⚠️  ROC-Score sample size too small (<30 trades)")
        print()
        print("Recommended:")
        print("  • Lower roc_score_thresh (try 0.8, 1.0, 1.2)")
        print("  • Disable compression filter temporarily")
        print("  • Re-test on 365 days to confirm signal frequency")

    print()
    print("=" * 80)
    print()

    return {
        'donchian': stats_donch,
        'roc': stats_roc,
        'donch_trades_per_month': donch_trades_per_month,
        'roc_trades_per_month': roc_trades_per_month,
    }


if __name__ == "__main__":
    results = run_baseline_comparison()

    # Exit with success if both modes have adequate samples
    success = (results['donchian']['trades'] >= 30 and results['roc']['trades'] >= 30)
    exit(0 if success else 1)
