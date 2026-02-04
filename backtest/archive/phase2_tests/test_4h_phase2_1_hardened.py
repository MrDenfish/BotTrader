"""
Phase 2.1 Hardened Test - 4h Hybrid Maker Strategy

Tests the Phase 2.1 hardened configuration with chase entry improvements:
- Max-extension guard (0.5% runaway protection)
- Dynamic ATR-scaled chase offset (0.5× ATR%)
- Compression expansion confirmation (requires BB expanding)

Compares to Phase 2 baseline to measure impact of hardening.
"""

from config_4h_hybrid import get_phase2_baseline_config, get_phase2_1_hardened_config
from engine_4h_hybrid import Hybrid4hBacktestEngine


def run_phase2_1_hardened_test():
    """Run Phase 2.1 hardened test with 60-day data"""

    print("=" * 80)
    print("PHASE 2.1 HARDENED TEST - CHASE ENTRY IMPROVEMENTS")
    print("=" * 80)
    print()

    # Phase 2 baseline for comparison
    print("Running Phase 2 Baseline...")
    config_baseline = get_phase2_baseline_config()
    engine_baseline = Hybrid4hBacktestEngine(config_baseline)
    stats_baseline = engine_baseline.run_backtest("BTC-USD", days=60)
    engine_baseline.print_results(stats_baseline, "BTC-USD [Phase 2 Baseline]")
    print()

    # Phase 2.1 hardened
    print("Running Phase 2.1 Hardened...")
    config_hardened = get_phase2_1_hardened_config()
    engine_hardened = Hybrid4hBacktestEngine(config_hardened)
    stats_hardened = engine_hardened.run_backtest("BTC-USD", days=60)
    engine_hardened.print_results(stats_hardened, "BTC-USD [Phase 2.1 Hardened]")
    print()

    # Export trades
    engine_baseline.export_trades("trades_phase2_baseline_btc.csv")
    engine_hardened.export_trades("trades_phase2_1_hardened_btc.csv")

    # Comparison summary
    print("=" * 80)
    print("COMPARISON: Phase 2 Baseline vs Phase 2.1 Hardened")
    print("=" * 80)
    print()

    if "error" not in stats_baseline and "error" not in stats_hardened:
        print(f"{'Metric':<30} {'Phase 2 Baseline':<20} {'Phase 2.1 Hardened':<20} {'Delta':<15}")
        print("-" * 85)

        # Trades
        trades_b = stats_baseline.get('trades', 0)
        trades_h = stats_hardened.get('trades', 0)
        print(f"{'Trades':<30} {trades_b:<20} {trades_h:<20} {trades_h - trades_b:+<15}")

        # Net P&L
        pnl_b = stats_baseline.get('net_pnl', 0)
        pnl_h = stats_hardened.get('net_pnl', 0)
        print(f"{'Net P&L':<30} ${pnl_b:<19.2f} ${pnl_h:<19.2f} ${pnl_h - pnl_b:+<14.2f}")

        # Win rate
        wr_b = stats_baseline.get('win_rate', 0)
        wr_h = stats_hardened.get('win_rate', 0)
        print(f"{'Win Rate':<30} {wr_b:<19.1%} {wr_h:<19.1%} {wr_h - wr_b:+<14.1%}")

        # Chase fills
        chase_b = stats_baseline.get('chase_fills', 0)
        chase_h = stats_hardened.get('chase_fills', 0)
        print(f"{'Chase Fills':<30} {chase_b:<20} {chase_h:<20} {chase_h - chase_b:+<15}")

        # Chase attempts
        chase_att_b = stats_baseline.get('chase_attempts', 0)
        chase_att_h = stats_hardened.get('chase_attempts', 0)
        print(f"{'Chase Attempts':<30} {chase_att_b:<20} {chase_att_h:<20} {chase_att_h - chase_att_b:+<15}")

        # Chase success rate
        chase_sr_b = stats_baseline.get('chase_success_rate', 0)
        chase_sr_h = stats_hardened.get('chase_success_rate', 0)
        print(f"{'Chase Success Rate':<30} {chase_sr_b:<19.1%} {chase_sr_h:<19.1%} {chase_sr_h - chase_sr_b:+<14.1%}")

        print()
        print("Hardening Features:")
        print("  ✓ Max-extension guard (0.5% runaway protection)")
        print("  ✓ Dynamic ATR-scaled chase offset (0.5× ATR%)")
        print("  ✓ Compression expansion confirmation")
        print()

    print("=" * 80)


if __name__ == "__main__":
    run_phase2_1_hardened_test()
