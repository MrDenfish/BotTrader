"""
Phase 2 Baseline Test - 4h Hybrid Maker Strategy

Tests the Phase 2 baseline configuration:
- donch_len=10, vol_min_mult=1.0 (from Phase 1 best)
- Compression filter enabled (bb_width_pct <= 20)
- Chase entry enabled (retest_ttl=120m, chase_offset=0.10%)
- Expanded targets (TP1=3×, TP2=8×)
- Wider stops (stop_mult=2.5)

Compares to Phase 1 best to measure improvement.
"""

from config_4h_hybrid import get_phase2_baseline_config
from engine_4h_hybrid import Hybrid4hBacktestEngine


def run_phase2_baseline_test():
    print("=" * 80)
    print("PHASE 2 BASELINE TEST - 4H HYBRID MAKER STRATEGY")
    print("=" * 80)
    print()

    config = get_phase2_baseline_config()
    print(f"Config: {config}")
    print()

    engine = Hybrid4hBacktestEngine(config)
    stats = engine.run_backtest("BTC-USD", days=60)

    engine.print_results(stats, "BTC-USD")
    engine.export_trades("trades_phase2_baseline_btc.csv")

    # Comparison
    print("\n" + "=" * 80)
    print("PHASE 1 vs PHASE 2 COMPARISON")
    print("=" * 80)
    print()
    print("Phase 1 Best (from optimizer):")
    print("  - donch_len=10, vol_min_mult=1.0")
    print("  - NO compression filter")
    print("  - NO chase entry")
    print("  - TP1=2.0×, TP2=5.0×")
    print("  - Result: 7 trades, -$5.42 net P&L")
    print()
    print("Phase 2 Baseline:")
    print("  - donch_len=10, vol_min_mult=1.0")
    print("  - Compression filter (bb_width_pct <= 20)")
    print("  - Chase entry (retest_ttl=120m, chase=0.10%)")
    print("  - TP1=3.0×, TP2=8.0×, stop=2.5×")
    print(f"  - Result: {stats.get('trades', 0)} trades, "
          f"${stats.get('net_pnl', 0):.2f} net P&L")
    print()
    print(f"  Entry breakdown:")
    print(f"    Retest fills: {stats.get('retest_fills', 0)}")
    print(f"    Chase fills: {stats.get('chase_fills', 0)}")
    print(f"    Chase attempts: {stats.get('chase_attempts', 0)}")
    print(f"    Chase success: {stats.get('chase_success_rate', 0):.1%}")
    print("=" * 80)


if __name__ == "__main__":
    run_phase2_baseline_test()
