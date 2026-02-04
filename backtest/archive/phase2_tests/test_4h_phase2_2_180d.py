"""
180-Day Backtest: Phase 2.2 4h Hybrid Strategy

Comprehensive validation of Phase 2.2 implementation over 6-month period.

Phase 2.2 Features:
- A1: Recent compression check (24h lookback)
- B1: Immediate retest bid placement
- B2: Volatility-aware chase offset (0.25× ATR%)
- B4: Chase repricing (up to 3 reprices, 30min intervals)
- H1: Expired setup diagnostics

Target Metrics:
- Trade frequency: 2-4 trades/month
- Fill rate: >30%
- Net P&L: Positive
- Win rate: >50%
"""

import sys
from datetime import datetime

from config_4h_hybrid import get_phase2_2_baseline_config
from engine_4h_hybrid import Hybrid4hBacktestEngine


def run_test():
    """Run 180-day Phase 2.2 comprehensive validation"""

    print("=" * 80)
    print("PHASE 2.2 COMPREHENSIVE VALIDATION (180 DAYS)")
    print("=" * 80)
    print()
    print("Testing Phase 2.2 4h Hybrid Strategy on BTC-USD over 6 months")
    print()
    print("Phase 2.2 Features:")
    print("  A1: Recent compression check (24h lookback)")
    print("  B1: Immediate retest bid placement")
    print("  B2: Volatility-aware chase offset (0.25× ATR%)")
    print("  B4: Chase repricing (up to 3 reprices @ 30min)")
    print("  H1: Expired setup diagnostics")
    print()
    print("=" * 80)
    print()

    symbol = "BTC-USD"
    days = 180

    # Use Phase 2.2 baseline config
    config = get_phase2_2_baseline_config()

    print(f"Phase 2.2 Config:")
    print(f"  - Compression: ≤{config.bb_width_pct_threshold}th percentile (last {config.compression_lookback_4h} bars)")
    print(f"  - Retest: {config.retest_offset:.4f} ({config.retest_offset*100:.2f}%) offset, {config.retest_ttl_minutes}min TTL")
    print(f"  - Chase: {config.chase_offset_min:.4f}-{config.chase_atr_mult}×ATR offset, {config.chase_ttl_minutes}min TTL")
    print(f"  - Repricing: {config.chase_max_reprices} reprices @ {config.chase_reprice_interval_minutes}min intervals")
    print(f"  - Max extension: {config.chase_max_extension*100:.1f}%")
    print()

    engine = Hybrid4hBacktestEngine(config, data_dir="data")

    print(f"Running 180-day backtest (this may take 1-2 minutes)...\n")

    try:
        stats = engine.run_backtest(symbol, days)

        print("\n" + "=" * 80)
        print("PHASE 2.2 COMPREHENSIVE TEST RESULTS (180 DAYS)")
        print("=" * 80)
        print()

        engine.print_results(stats, symbol)

        # Export results
        engine.export_trades("trades_phase2_2_180d.csv")
        print(f"\n📊 Trades exported: trades_phase2_2_180d.csv")

        # Phase 2.2 detailed analysis
        print("\n" + "=" * 80)
        print("PHASE 2.2 PERFORMANCE ANALYSIS (6 MONTHS)")
        print("=" * 80)
        print()

        if stats.get('trades', 0) > 0:
            trades = stats['trades']
            trades_per_month = (trades / days) * 30

            print(f"📈 Trade Frequency")
            print(f"   Total trades:        {trades}")
            print(f"   Trades/month:        {trades_per_month:.1f}")
            print(f"   Signals generated:   {stats.get('signals', 0)}")
            print(f"   Signal→Entry rate:   {stats.get('signal_to_entry_pct', 0):.1f}%")
            print(f"   Setups expired:      {stats.get('expired_setups', 0)}")
            print()

            # Entry breakdown
            retest_fills = stats.get('retest_fills', 0)
            chase_fills = stats.get('chase_fills', 0)
            total_fills = retest_fills + chase_fills

            if total_fills > 0:
                print(f"🎯 Entry Performance")
                print(f"   Retest fills:        {retest_fills} ({retest_fills/total_fills*100:.1f}%)")
                print(f"   Chase fills:         {chase_fills} ({chase_fills/total_fills*100:.1f}%)")
                print()

            # P&L metrics
            gross_pnl = stats.get('gross_pnl', 0)
            fees = stats.get('fees', 0)
            net_pnl = stats.get('net_pnl', 0)
            win_rate = stats.get('win_rate', 0)
            profit_factor = stats.get('profit_factor', 0)

            print(f"💰 P&L Performance")
            print(f"   Gross P&L:           ${gross_pnl:.2f}")
            print(f"   Total fees:          ${fees:.2f}")
            print(f"   Net P&L:             ${net_pnl:.2f}")
            print(f"   Win rate:            {win_rate:.1%}")
            print(f"   Profit factor:       {profit_factor:.2f}")
            print()

            # Exit analysis
            tp1_hit = stats.get('tp1_hit_pct', 0)
            tp2_hit = stats.get('tp2_hit_pct', 0)

            print(f"🎯 Exit Performance")
            print(f"   TP1 hit rate:        {tp1_hit:.1f}%")
            print(f"   TP2 hit rate:        {tp2_hit:.1f}%")
            print()

            # Success criteria
            print("=" * 80)
            print("SUCCESS CRITERIA (180-DAY EVALUATION)")
            print("=" * 80)
            print()

            criteria = {
                "Trade frequency (2-4/month)": (2 <= trades_per_month <= 4, f"{trades_per_month:.1f}/month"),
                "Fill rate (>30%)": (stats.get('signal_to_entry_pct', 0) >= 30, f"{stats.get('signal_to_entry_pct', 0):.1f}%"),
                "Net P&L positive": (net_pnl > 0, f"${net_pnl:.2f}"),
                "Win rate (>50%)": (win_rate >= 0.50, f"{win_rate:.1%}"),
                "Profit factor (>1.0)": (profit_factor > 1.0, f"{profit_factor:.2f}")
            }

            passed_count = 0
            for criterion, (passed, value) in criteria.items():
                status = "✅ PASS" if passed else "❌ FAIL"
                print(f"{criterion:<35} {value:<15} {status}")
                if passed:
                    passed_count += 1

            print()
            print(f"Overall: {passed_count}/{len(criteria)} criteria passed")
            print()

            if passed_count >= 4:
                print("🎉 PHASE 2.2 VALIDATION: SUCCESSFUL!")
                print()
                print("Phase 2.2 improvements demonstrate:")
                print("  ✅ Improved trade frequency")
                print("  ✅ Better fill rates with immediate retest + chase")
                print("  ✅ Effective repricing logic")
                print("  ✅ Actionable diagnostics")
                print()
                print("Ready for production deployment consideration.")
            elif passed_count >= 2:
                print("⚠️  PHASE 2.2 VALIDATION: PARTIAL SUCCESS")
                print()
                print("Phase 2.2 shows promise but needs refinement:")
                print("  - Review expired setup diagnostics for insights")
                print("  - Consider adjusting compression threshold")
                print("  - Tune chase parameters based on fill patterns")
            else:
                print("❌ PHASE 2.2 VALIDATION: NEEDS SIGNIFICANT WORK")
                print()
                print("Recommended actions:")
                print("  - Analyze expired setups for common patterns")
                print("  - Test with looser filters (compression, volatility)")
                print("  - Consider alternative entry strategies")

        else:
            print("❌ No trades generated in 180-day period")
            print()
            print("Critical issue - strategy not triggering:")
            print(f"  Signals detected:    {stats.get('signals', 0)}")
            print(f"  Expired setups:      {stats.get('expired_setups', 0)}")
            print()
            print("Likely causes:")
            print("  - Compression filter too restrictive")
            print("  - Volatility filter too strict")
            print("  - Consider reviewing filter combinations")

        print()
        print("=" * 80)
        print()

        # Expired setup summary
        expired_count = len(engine.strategy.expired_setups)
        if expired_count > 0:
            print(f"📋 Expired Setup Summary: {expired_count} setups failed to fill")
            print()
            print("Common expiration reasons:")

            reasons = {}
            for setup in engine.strategy.expired_setups:
                reason = setup.expiration_reason
                reasons[reason] = reasons.get(reason, 0) + 1

            for reason, count in sorted(reasons.items(), key=lambda x: x[1], reverse=True):
                print(f"   {reason:<40} {count:>3}")

            print()
            print("💡 Use view_expired_setups.py for detailed analysis")
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
