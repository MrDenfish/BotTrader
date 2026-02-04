"""
60-Day Backtest: Phase 2.2 4h Hybrid Strategy

Validate Phase 2.2 improvements over BTC-USD 60-day period.

Phase 2.2 Features Being Tested:
- A1: Recent compression check (24h lookback)
- B1: Immediate retest bid placement
- B2: Volatility-aware chase offset
- B4: Chase repricing (up to 3 reprices)
- H1: Expired setup diagnostics

Success Criteria:
- 2-4 trades/month (improved frequency vs Phase 2.1)
- Fill rate improvement (more retest+chase fills)
- Diagnostics show actionable insights
- Net P&L positive
"""

import sys
from datetime import datetime

from config_4h_hybrid import get_phase2_2_baseline_config
from engine_4h_hybrid import Hybrid4hBacktestEngine


def run_test():
    """Run 60-day Phase 2.2 validation test"""

    print("=" * 80)
    print("PHASE 2.2 VALIDATION TEST (60 DAYS)")
    print("=" * 80)
    print()
    print("Testing Phase 2.2 4h Hybrid Strategy on BTC-USD")
    print()
    print("Key Phase 2.2 Features:")
    print("  A1: Recent compression check (24h lookback)")
    print("  B1: Immediate retest bid placement")
    print("  B2: Volatility-aware chase offset (0.25× ATR%)")
    print("  B4: Chase repricing (up to 3 reprices, 30min intervals)")
    print("  H1: Expired setup diagnostics")
    print()
    print("=" * 80)
    print()

    symbol = "BTC-USD"
    days = 60

    # Use Phase 2.2 baseline config
    config = get_phase2_2_baseline_config()

    print(f"\nPhase 2.2 Config Settings:")
    print(f"  - compression_lookback_4h: {config.compression_lookback_4h} bars (24h)")
    print(f"  - bb_width_pct_threshold: {config.bb_width_pct_threshold} percentile")
    print(f"  - retest_offset: {config.retest_offset:.4f} ({config.retest_offset*100:.2f}%)")
    print(f"  - retest_ttl_minutes: {config.retest_ttl_minutes} min")
    print(f"  - chase_offset_min: {config.chase_offset_min:.4f} ({config.chase_offset_min*100:.2f}%)")
    print(f"  - chase_atr_mult: {config.chase_atr_mult}× ATR%")
    print(f"  - chase_max_reprices: {config.chase_max_reprices}")
    print(f"  - chase_reprice_interval_minutes: {config.chase_reprice_interval_minutes} min")
    print(f"  - setup_ttl_bars_4h: {config.setup_ttl_bars_4h} bars (24h)")
    print()

    engine = Hybrid4hBacktestEngine(config, data_dir="data")

    print(f"Running 60-day backtest...\n")

    try:
        stats = engine.run_backtest(symbol, days)

        print("\n" + "=" * 80)
        print("PHASE 2.2 TEST RESULTS")
        print("=" * 80)
        print()

        engine.print_results(stats, symbol)

        # Export results
        engine.export_trades("trades_phase2_2_60d.csv")
        print(f"\n✅ Trades exported to: trades_phase2_2_60d.csv")

        # Phase 2.2 specific analysis
        print("\n" + "=" * 80)
        print("PHASE 2.2 FEATURE ANALYSIS")
        print("=" * 80)
        print()

        if stats.get('trades', 0) > 0:
            trades_per_month = (stats['trades'] / days) * 30

            print(f"Trade Frequency:     {stats['trades']} trades in {days} days ({trades_per_month:.1f}/month)")
            print(f"Signals Generated:   {stats.get('signals', 0)}")
            print(f"Signal→Entry Rate:   {stats.get('signal_to_entry_pct', 0):.1f}%")
            print(f"Setups Expired:      {stats.get('expired_setups', 0)}")
            print()

            # Entry type breakdown
            retest_fills = stats.get('retest_fills', 0)
            chase_fills = stats.get('chase_fills', 0)
            total_fills = retest_fills + chase_fills

            if total_fills > 0:
                print("Entry Fill Breakdown:")
                print(f"  Retest fills:      {retest_fills} ({retest_fills/total_fills*100:.1f}%)")
                print(f"  Chase fills:       {chase_fills} ({chase_fills/total_fills*100:.1f}%)")
                print()

            # P&L analysis
            net_pnl = stats.get('net_pnl', 0)
            win_rate = stats.get('win_rate', 0)
            tp1_hit_pct = stats.get('tp1_hit_pct', 0)
            tp2_hit_pct = stats.get('tp2_hit_pct', 0)

            print(f"Net P&L:             ${net_pnl:.2f}")
            print(f"Win Rate:            {win_rate:.1%}")
            print(f"TP1 Hit Rate:        {tp1_hit_pct:.1f}%")
            print(f"TP2 Hit Rate:        {tp2_hit_pct:.1f}%")
            print()

            # Success criteria check
            print("=" * 80)
            print("SUCCESS CRITERIA")
            print("=" * 80)
            print()

            criteria = {
                "Trade frequency (2-4/month)": 2 <= trades_per_month <= 4,
                "Net P&L positive": net_pnl > 0,
                "Fill rate >30%": stats.get('signal_to_entry_pct', 0) >= 30,
                "Win rate >50%": win_rate >= 0.50
            }

            for criterion, passed in criteria.items():
                status = "✅ PASS" if passed else "❌ FAIL"
                print(f"{criterion:<35} {status}")

            all_pass = all(criteria.values())

            print()
            if all_pass:
                print("🎉 PHASE 2.2 VALIDATION SUCCESSFUL!")
                print()
                print("Phase 2.2 improvements are working as intended:")
                print("  ✅ Improved trade frequency")
                print("  ✅ Better fill rates")
                print("  ✅ Positive returns")
                print()
                print("Ready for 180-day full validation test.")
            else:
                print("⚠️  PHASE 2.2 NEEDS TUNING")
                print()
                print("Review diagnostics (expired_setups) for insights.")

        else:
            print("❌ No trades generated in 60-day period")
            print()
            print("Possible issues:")
            print("  - Compression filter too tight (threshold too low)")
            print("  - Donchian period too large")
            print("  - Volatility filter too strict")
            print()
            print(f"Signals detected: {stats.get('signals', 0)}")
            print(f"Expired setups:   {stats.get('expired_setups', 0)}")

        print()
        print("=" * 80)

    except Exception as e:
        print("\n" + "=" * 80)
        print("❌ TEST FAILED")
        print("=" * 80)
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    run_test()
