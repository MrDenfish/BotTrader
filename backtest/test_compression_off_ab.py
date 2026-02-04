"""
Step 2: Compression OFF A/B Test (Section 10.2)

Test both Donchian and ROC-score modes with compression filter disabled
to isolate whether compression is the dominant bottleneck.

Expected outcome:
- If signal frequency increases dramatically, compression was the bottleneck
- If signal frequency remains low, setup triggers are also too strict
"""

from config_4h_hybrid import Hybrid4hConfig, get_phase2_2_baseline_config, get_phase2_3_roc_baseline_config
from engine_4h_hybrid import Hybrid4hBacktestEngine


def run_compression_off_test(days: int = 180):
    """Run A/B test with compression OFF for both setup modes"""

    print("=" * 80)
    print("COMPRESSION OFF A/B TEST (Step 2)")
    print("=" * 80)
    print()
    print(f"Testing period: {days} days")
    print("Regime: ON, Viability: ON, Compression: OFF")
    print()

    # ========================================================================
    # Test 1: Donchian with Compression OFF
    # ========================================================================

    print("=" * 80)
    print("📊 TEST 1: DONCHIAN + COMPRESSION OFF")
    print("=" * 80)
    print()

    # Get baseline config and disable compression
    donch_config = get_phase2_2_baseline_config()
    donch_config.use_compression_filter = False

    print(f"Config: {donch_config}")
    print(f"  Setup mode: {donch_config.setup_mode}")
    print(f"  Compression filter: {donch_config.use_compression_filter}")
    print()

    engine_donch = Hybrid4hBacktestEngine(donch_config)
    stats_donch = engine_donch.run_backtest("BTC-USD", days=days)
    engine_donch.print_results(stats_donch, "BTC-USD")

    # ========================================================================
    # Test 2: ROC-Score with Compression OFF
    # ========================================================================

    print()
    print("=" * 80)
    print("📊 TEST 2: ROC-SCORE + COMPRESSION OFF")
    print("=" * 80)
    print()

    # Get baseline config and disable compression
    roc_config = get_phase2_3_roc_baseline_config()
    roc_config.use_compression_filter = False

    print(f"Config: {roc_config}")
    print(f"  Setup mode: {roc_config.setup_mode}")
    print(f"  Compression filter: {roc_config.use_compression_filter}")
    print()

    engine_roc = Hybrid4hBacktestEngine(roc_config)
    stats_roc = engine_roc.run_backtest("BTC-USD", days=days)
    engine_roc.print_results(stats_roc, "BTC-USD")

    # ========================================================================
    # Comparative Analysis
    # ========================================================================

    print()
    print("=" * 80)
    print("COMPRESSION OFF: COMPARATIVE ANALYSIS")
    print("=" * 80)
    print()

    # Extract key metrics
    donch_signals = stats_donch.get('signals', 0)
    roc_signals = stats_roc.get('signals', 0)

    donch_trades = stats_donch.get('trades', 0)
    roc_trades = stats_roc.get('trades', 0)

    # Calculate monthly rates (based on actual days)
    months = days / 30.0
    donch_signals_per_month = donch_signals / months if months > 0 else 0
    roc_signals_per_month = roc_signals / months if months > 0 else 0
    donch_trades_per_month = donch_trades / months if months > 0 else 0
    roc_trades_per_month = roc_trades / months if months > 0 else 0

    print(f"Metric                         Donchian             ROC-Score            ")
    print("-" * 80)
    print(f"Signals                        {donch_signals:<20} {roc_signals:<20}")
    print(f"Signals/month                  {donch_signals_per_month:<20.2f} {roc_signals_per_month:<20.2f}")
    print(f"Trades                         {donch_trades:<20} {roc_trades:<20}")
    print(f"Trades/month                   {donch_trades_per_month:<20.2f} {roc_trades_per_month:<20.2f}")
    print(f"Net P&L                        ${stats_donch.get('net_pnl', 0):<19.2f} ${stats_roc.get('net_pnl', 0):<19.2f}")
    print(f"Win rate                       {stats_donch.get('win_rate', 0):<19.1%} {stats_roc.get('win_rate', 0):<19.1%}")
    print("=" * 80)
    print()

    # ========================================================================
    # Key Insights
    # ========================================================================

    print("=" * 80)
    print("KEY INSIGHTS")
    print("=" * 80)
    print()

    # Frequency check
    target_min = 2.0  # 2 trades/month minimum
    target_max = 4.0  # 4 trades/month maximum

    print(f"Trade Frequency Target: {target_min}-{target_max} trades/month")
    print()

    if donch_trades_per_month >= target_min:
        print(f"✅ Donchian: {donch_trades_per_month:.2f} trades/month (meets target)")
    else:
        print(f"⚠️  Donchian: {donch_trades_per_month:.2f} trades/month (below target)")

    if roc_trades_per_month >= target_min:
        print(f"✅ ROC-Score: {roc_trades_per_month:.2f} trades/month (meets target)")
    else:
        print(f"⚠️  ROC-Score: {roc_trades_per_month:.2f} trades/month (below target)")

    print()

    # Bottleneck diagnosis
    print("Bottleneck Diagnosis:")
    print()

    if donch_signals_per_month >= target_min and roc_signals_per_month >= target_min:
        print("✅ Compression was the primary bottleneck")
        print("   → Disabling compression restored adequate signal frequency")
        print("   → Next: Optimize compression parameters or keep it disabled")
    elif donch_signals_per_month < 1.0 or roc_signals_per_month < 1.0:
        print("🚨 Setup triggers are ALSO too strict (even with compression OFF)")
        print("   → Need to loosen BOTH compression AND setup trigger thresholds")
        print("   → Donchian: Consider lower donchian length")
        print("   → ROC-Score: Lower roc_score_thresh (calibrate by percentile)")
    else:
        print("⚠️  Mixed results - compression was A bottleneck but not the only one")
        print("   → Loosen compression parameters AND adjust setup triggers")

    print()
    print("=" * 80)
    print("NEXT STEPS")
    print("=" * 80)
    print()

    if roc_trades_per_month < target_min:
        print("Recommended:")
        print("  • Step 3: ROC-score threshold calibration (with compression OFF)")
        print("  • Target P60-P70 percentile to restore 60-120 setups/year")
        print("  • Re-test with calibrated threshold")
        print("  • Step 4: Re-enable compression with looser parameters")
    else:
        print("Recommended:")
        print("  • Keep compression OFF or use very loose parameters")
        print("  • Proceed to performance optimization")

    print()
    print("=" * 80)
    print()


if __name__ == "__main__":
    # Run test on 180 days (matches our current data availability)
    run_compression_off_test(days=180)
