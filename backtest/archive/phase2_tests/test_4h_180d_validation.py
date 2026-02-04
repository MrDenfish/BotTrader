"""
180-Day Extended Validation - 4h Hybrid Maker Strategy

Phase 2.1a: Extended validation with 180 days of data
- Tests Phase 2 baseline configuration
- Tests Phase 2.1 hardened configuration
- Provides comprehensive comparison and analysis
"""

from config_4h_hybrid import get_phase2_baseline_config, get_phase2_1_hardened_config
from engine_4h_hybrid import Hybrid4hBacktestEngine


def run_180d_validation():
    """Run 180-day validation for Phase 2 baseline and Phase 2.1 hardened"""

    print("=" * 80)
    print("180-DAY EXTENDED VALIDATION - PHASE 2.1a")
    print("=" * 80)
    print("Testing configurations over 6-month period")
    print("Data: 2025-08-04 to 2026-01-31 (180 days)")
    print("=" * 80)
    print()

    # ========== Phase 2 Baseline ==========
    print("=" * 80)
    print("RUNNING: Phase 2 Baseline (180 days)")
    print("=" * 80)
    print()

    config_baseline = get_phase2_baseline_config()
    engine_baseline = Hybrid4hBacktestEngine(config_baseline)
    stats_baseline = engine_baseline.run_backtest("BTC-USD", days=180)

    print()
    engine_baseline.print_results(stats_baseline, "BTC-USD [Phase 2 Baseline - 180d]")
    engine_baseline.export_trades("trades_phase2_baseline_btc_180d.csv")
    print()

    # ========== Phase 2.1 Hardened ==========
    print("=" * 80)
    print("RUNNING: Phase 2.1 Hardened (180 days)")
    print("=" * 80)
    print()

    config_hardened = get_phase2_1_hardened_config()
    engine_hardened = Hybrid4hBacktestEngine(config_hardened)
    stats_hardened = engine_hardened.run_backtest("BTC-USD", days=180)

    print()
    engine_hardened.print_results(stats_hardened, "BTC-USD [Phase 2.1 Hardened - 180d]")
    engine_hardened.export_trades("trades_phase2_1_hardened_btc_180d.csv")
    print()

    # ========== Comparison Analysis ==========
    print("=" * 80)
    print("180-DAY COMPARISON: Phase 2 Baseline vs Phase 2.1 Hardened")
    print("=" * 80)
    print()

    if "error" not in stats_baseline and "error" not in stats_hardened:
        # Extract metrics
        trades_b = stats_baseline.get('trades', 0)
        trades_h = stats_hardened.get('trades', 0)

        signals_b = stats_baseline.get('signals', 0)
        signals_h = stats_hardened.get('signals', 0)

        entries_b = stats_baseline.get('entries', 0)
        entries_h = stats_hardened.get('entries', 0)

        pnl_b = stats_baseline.get('net_pnl', 0)
        pnl_h = stats_hardened.get('net_pnl', 0)

        gross_b = stats_baseline.get('gross_pnl', 0)
        gross_h = stats_hardened.get('gross_pnl', 0)

        fees_b = stats_baseline.get('fees', 0)
        fees_h = stats_hardened.get('fees', 0)

        wr_b = stats_baseline.get('win_rate', 0)
        wr_h = stats_hardened.get('win_rate', 0)

        pf_b = stats_baseline.get('profit_factor', 0)
        pf_h = stats_hardened.get('profit_factor', 0)

        tp1_b = stats_baseline.get('tp1_hit_pct', 0)
        tp1_h = stats_hardened.get('tp1_hit_pct', 0)

        tp2_b = stats_baseline.get('tp2_hit_pct', 0)
        tp2_h = stats_hardened.get('tp2_hit_pct', 0)

        chase_fills_b = stats_baseline.get('chase_fills', 0)
        chase_fills_h = stats_hardened.get('chase_fills', 0)

        chase_attempts_b = stats_baseline.get('chase_attempts', 0)
        chase_attempts_h = stats_hardened.get('chase_attempts', 0)

        # Print comparison table
        print(f"{'Metric':<35} {'Phase 2':<20} {'Phase 2.1':<20} {'Delta':<20}")
        print("-" * 95)

        print(f"{'Signals':<35} {signals_b:<20} {signals_h:<20} {signals_h - signals_b:+<20}")
        print(f"{'Entries':<35} {entries_b:<20} {entries_h:<20} {entries_h - entries_b:+<20}")
        print(f"{'Trades (closed)':<35} {trades_b:<20} {trades_h:<20} {trades_h - trades_b:+<20}")
        print()

        print(f"{'Net P&L':<35} ${pnl_b:<19.2f} ${pnl_h:<19.2f} ${pnl_h - pnl_b:+<19.2f}")
        print(f"{'Gross P&L':<35} ${gross_b:<19.2f} ${gross_h:<19.2f} ${gross_h - gross_b:+<19.2f}")
        print(f"{'Total Fees':<35} ${fees_b:<19.2f} ${fees_h:<19.2f} ${fees_h - fees_b:+<19.2f}")
        print()

        print(f"{'Win Rate':<35} {wr_b:<19.1%} {wr_h:<19.1%} {wr_h - wr_b:+<19.1%}")
        print(f"{'Profit Factor':<35} {pf_b:<20.2f} {pf_h:<20.2f} {pf_h - pf_b:+<20.2f}")
        print()

        print(f"{'TP1 Hit Rate':<35} {tp1_b:<19.1f}% {tp1_h:<19.1f}% {tp1_h - tp1_b:+<19.1f}%")
        print(f"{'TP2 Hit Rate':<35} {tp2_b:<19.1f}% {tp2_h:<19.1f}% {tp2_h - tp2_b:+<19.1f}%")
        print()

        print(f"{'Chase Fills':<35} {chase_fills_b:<20} {chase_fills_h:<20} {chase_fills_h - chase_fills_b:+<20}")
        print(f"{'Chase Attempts':<35} {chase_attempts_b:<20} {chase_attempts_h:<20} {chase_attempts_h - chase_attempts_b:+<20}")

        if chase_attempts_b > 0:
            chase_sr_b = chase_fills_b / chase_attempts_b
        else:
            chase_sr_b = 0

        if chase_attempts_h > 0:
            chase_sr_h = chase_fills_h / chase_attempts_h
        else:
            chase_sr_h = 0

        print(f"{'Chase Success Rate':<35} {chase_sr_b:<19.1%} {chase_sr_h:<19.1%} {chase_sr_h - chase_sr_b:+<19.1%}")

        print()
        print("=" * 95)
        print()

        # Performance per month
        months = 180 / 30
        print(f"Performance Metrics (Monthly Averages):")
        print(f"  Phase 2 Baseline:  {trades_b/months:.1f} trades/month, ${pnl_b/months:+.2f}/month")
        print(f"  Phase 2.1 Hardened: {trades_h/months:.1f} trades/month, ${pnl_h/months:+.2f}/month")
        print()

        # Key findings
        print("Key Findings:")
        if pnl_h > pnl_b:
            improvement = ((pnl_h / pnl_b) - 1) * 100 if pnl_b != 0 else 0
            print(f"  ✅ Phase 2.1 Hardened outperformed by ${pnl_h - pnl_b:+.2f} ({improvement:+.1f}%)")
        elif pnl_h < pnl_b:
            decline = ((pnl_h / pnl_b) - 1) * 100 if pnl_b != 0 else 0
            print(f"  ⚠️  Phase 2.1 Hardened underperformed by ${pnl_h - pnl_b:+.2f} ({decline:+.1f}%)")
        else:
            print(f"  → Performance identical")

        if chase_fills_h < chase_fills_b:
            print(f"  ✅ Hardening prevented {chase_fills_b - chase_fills_h} chase entries")

        if trades_h == trades_b:
            print(f"  ✅ Same number of trades - filtered chases still filled via retest")
        elif trades_h > trades_b:
            print(f"  ⚠️  Hardening resulted in {trades_h - trades_b} more trades")
        else:
            print(f"  ⚠️  Hardening resulted in {trades_b - trades_h} fewer trades")

        print()

    print("=" * 80)
    print("180-DAY VALIDATION COMPLETE")
    print("=" * 80)
    print()
    print("Trade logs exported:")
    print("  - trades_phase2_baseline_btc_180d.csv")
    print("  - trades_phase2_1_hardened_btc_180d.csv")
    print()


if __name__ == "__main__":
    run_180d_validation()
