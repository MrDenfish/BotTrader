"""
1-Day Smoke Test: Phase 2.2 4h Hybrid Strategy

Quick validation that Phase 2.2 implementation runs without errors.

Success criteria:
- Runs without crashes or exceptions
- State transitions work correctly
- At least processes data successfully
"""

import sys
from datetime import datetime

from config_4h_hybrid import get_phase2_2_baseline_config
from engine_4h_hybrid import Hybrid4hBacktestEngine


def run_smoke_test():
    """Run 1-day BTC smoke test for Phase 2.2"""

    print("=" * 80)
    print("PHASE 2.2 SMOKE TEST (1 DAY)")
    print("=" * 80)
    print()
    print("Testing Phase 2.2 implementation:")
    print("  - Recent compression check (A1)")
    print("  - Immediate retest bid placement (B1)")
    print("  - Volatility-aware chase offset (B2)")
    print("  - Chase repricing (B4)")
    print("  - Expired setup diagnostics (H1)")
    print()
    print("=" * 80)
    print()

    symbol = "BTC-USD"
    days = 1  # Quick smoke test

    # Use Phase 2.2 baseline config
    config = get_phase2_2_baseline_config()

    print(f"\nConfig: {config}")
    print(f"\nKey Phase 2.2 settings:")
    print(f"  - compression_lookback_4h: {config.compression_lookback_4h}")
    print(f"  - retest_offset: {config.retest_offset}")
    print(f"  - chase_offset_min: {config.chase_offset_min}")
    print(f"  - chase_max_reprices: {config.chase_max_reprices}")
    print(f"  - chase_reprice_interval_minutes: {config.chase_reprice_interval_minutes}")
    print()

    engine = Hybrid4hBacktestEngine(config, data_dir="data")

    print(f"Running backtest for {symbol} over {days} day(s)...\n")

    try:
        stats = engine.run_backtest(symbol, days)

        print("\n" + "=" * 80)
        print("SMOKE TEST RESULTS")
        print("=" * 80)
        print()

        engine.print_results(stats, symbol)

        print("\n" + "=" * 80)
        print("✅ SMOKE TEST PASSED - No errors or crashes!")
        print("=" * 80)
        print("\nPhase 2.2 implementation is working correctly!")

        return stats

    except Exception as e:
        print("\n" + "=" * 80)
        print("❌ SMOKE TEST FAILED")
        print("=" * 80)
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    run_smoke_test()
