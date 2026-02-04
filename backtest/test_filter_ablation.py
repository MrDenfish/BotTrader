"""
Filter Ablation Study: Test impact of each filter independently

This script systematically tests the impact of loosening or disabling each filter
to identify which filters are the primary bottlenecks for signal frequency.

Test matrix:
1. Baseline (all filters ON)
2. Regime filter OFF
3. Viability filter loosened (vol_min_mult: 1.0 → 0.5)
4. Viability filter OFF (vol_min_mult: 0.0)
5. All filters loosened together
6. Kitchen sink (everything loosened + compression OFF)
"""

from config_4h_hybrid import get_phase2_3_roc_baseline_config
from engine_4h_hybrid import Hybrid4hBacktestEngine


def run_filter_ablation_study(days: int = 180):
    """Run systematic filter ablation tests"""

    print("=" * 80)
    print("FILTER ABLATION STUDY")
    print("=" * 80)
    print()
    print(f"Testing period: {days} days")
    print("Goal: Identify which filters are blocking signals")
    print()

    test_configs = []

    # ========================================================================
    # Test 1: Baseline (all filters ON, compression OFF for isolation)
    # ========================================================================

    baseline = get_phase2_3_roc_baseline_config()
    baseline.use_compression_filter = False  # Already know compression isn't the main issue
    baseline.roc_score_thresh = 0.8  # Use loose threshold from calibration test

    test_configs.append({
        'name': 'Baseline',
        'description': 'All filters ON, compression OFF, loose ROC threshold',
        'config': baseline
    })

    # ========================================================================
    # Test 2: Disable Regime Filter
    # ========================================================================

    no_regime = get_phase2_3_roc_baseline_config()
    no_regime.use_compression_filter = False
    no_regime.roc_score_thresh = 0.8
    # Disable regime by setting EMA200 period to 1 (always pass)
    no_regime.ema200_len_1d = 1

    test_configs.append({
        'name': 'No Regime Filter',
        'description': 'Regime filter DISABLED (EMA200 check OFF)',
        'config': no_regime
    })

    # ========================================================================
    # Test 3: Loosen Viability Filter (1.0 → 0.5)
    # ========================================================================

    loose_viability = get_phase2_3_roc_baseline_config()
    loose_viability.use_compression_filter = False
    loose_viability.roc_score_thresh = 0.8
    loose_viability.vol_min_mult = 0.5  # Halve the requirement

    test_configs.append({
        'name': 'Loose Viability',
        'description': 'vol_min_mult: 1.0 → 0.5 (50% reduction)',
        'config': loose_viability
    })

    # ========================================================================
    # Test 4: Disable Viability Filter
    # ========================================================================

    no_viability = get_phase2_3_roc_baseline_config()
    no_viability.use_compression_filter = False
    no_viability.roc_score_thresh = 0.8
    no_viability.vol_min_mult = 0.0  # Effectively disabled

    test_configs.append({
        'name': 'No Viability Filter',
        'description': 'vol_min_mult: 1.0 → 0.0 (DISABLED)',
        'config': no_viability
    })

    # ========================================================================
    # Test 5: Loosen ALL Filters
    # ========================================================================

    all_loose = get_phase2_3_roc_baseline_config()
    all_loose.use_compression_filter = False
    all_loose.roc_score_thresh = 0.5  # Very loose
    all_loose.vol_min_mult = 0.5  # Loose
    all_loose.ema200_len_1d = 50  # Shorter EMA (less restrictive)

    test_configs.append({
        'name': 'All Filters Loose',
        'description': 'ROC=0.5, vol_mult=0.5, EMA200→EMA50',
        'config': all_loose
    })

    # ========================================================================
    # Test 6: Kitchen Sink (everything minimal)
    # ========================================================================

    kitchen_sink = get_phase2_3_roc_baseline_config()
    kitchen_sink.use_compression_filter = False
    kitchen_sink.roc_score_thresh = 0.0  # Accept all ROC scores
    kitchen_sink.vol_min_mult = 0.0  # No volatility requirement
    kitchen_sink.ema200_len_1d = 1  # No regime filter

    test_configs.append({
        'name': 'Kitchen Sink',
        'description': 'ALL filters DISABLED (maximum signal frequency)',
        'config': kitchen_sink
    })

    # ========================================================================
    # Run all tests
    # ========================================================================

    results = []

    for test in test_configs:
        print()
        print("=" * 80)
        print(f"📊 TEST: {test['name']}")
        print("=" * 80)
        print(f"Description: {test['description']}")
        print()

        config = test['config']
        engine = Hybrid4hBacktestEngine(config)
        stats = engine.run_backtest("BTC-USD", days=days)

        # Extract key metrics
        signals = stats.get('signals', 0)
        trades = stats.get('trades', 0)
        net_pnl = stats.get('net_pnl', 0)
        win_rate = stats.get('win_rate', 0)

        # Calculate monthly rates
        months = days / 30.0
        signals_per_month = signals / months if months > 0 else 0
        trades_per_month = trades / months if months > 0 else 0

        results.append({
            'name': test['name'],
            'description': test['description'],
            'signals': signals,
            'signals_per_month': signals_per_month,
            'trades': trades,
            'trades_per_month': trades_per_month,
            'net_pnl': net_pnl,
            'win_rate': win_rate,
        })

        print()
        print(f"  Signals: {signals} ({signals_per_month:.2f}/month)")
        print(f"  Trades: {trades} ({trades_per_month:.2f}/month)")
        print(f"  Net P&L: ${net_pnl:.2f}")
        print(f"  Win Rate: {win_rate:.1%}")
        print()

    # ========================================================================
    # Comparative Analysis
    # ========================================================================

    print()
    print("=" * 80)
    print("FILTER ABLATION RESULTS")
    print("=" * 80)
    print()

    print(f"{'Test Name':<20} {'Signals':<10} {'Sig/mo':<10} {'Trades':<10} {'Tr/mo':<10} {'Net P&L':<12}")
    print("-" * 80)

    for r in results:
        print(f"{r['name']:<20} {r['signals']:<10} {r['signals_per_month']:<10.2f} {r['trades']:<10} {r['trades_per_month']:<10.2f} ${r['net_pnl']:<11.2f}")

    print("=" * 80)
    print()

    # ========================================================================
    # Analysis and Recommendations
    # ========================================================================

    print()
    print("=" * 80)
    print("ANALYSIS")
    print("=" * 80)
    print()

    baseline_result = results[0]
    no_regime_result = results[1]
    loose_viability_result = results[2]
    no_viability_result = results[3]
    all_loose_result = results[4]
    kitchen_sink_result = results[5]

    target_min = 2.0

    print("Target frequency: 2.0-4.0 trades/month")
    print()

    # Regime filter impact
    regime_lift = no_regime_result['trades_per_month'] - baseline_result['trades_per_month']
    regime_lift_pct = (regime_lift / baseline_result['trades_per_month'] * 100) if baseline_result['trades_per_month'] > 0 else 0

    print(f"Regime Filter Impact:")
    print(f"  Baseline: {baseline_result['trades_per_month']:.2f} trades/month")
    print(f"  Without regime: {no_regime_result['trades_per_month']:.2f} trades/month")
    print(f"  Lift: {regime_lift:+.2f} trades/month ({regime_lift_pct:+.1f}%)")
    if regime_lift > 1.0:
        print(f"  ✅ Regime filter is A MAJOR BOTTLENECK")
    elif regime_lift > 0.3:
        print(f"  ⚠️  Regime filter is blocking some signals")
    else:
        print(f"  ℹ️  Regime filter has minimal impact")
    print()

    # Viability filter impact
    viability_lift_loose = loose_viability_result['trades_per_month'] - baseline_result['trades_per_month']
    viability_lift_off = no_viability_result['trades_per_month'] - baseline_result['trades_per_month']

    print(f"Viability Filter Impact:")
    print(f"  Baseline: {baseline_result['trades_per_month']:.2f} trades/month")
    print(f"  Loosened (0.5×): {loose_viability_result['trades_per_month']:.2f} trades/month (lift: {viability_lift_loose:+.2f})")
    print(f"  Disabled (0.0×): {no_viability_result['trades_per_month']:.2f} trades/month (lift: {viability_lift_off:+.2f})")
    if viability_lift_off > 1.0:
        print(f"  ✅ Viability filter is A MAJOR BOTTLENECK")
    elif viability_lift_off > 0.3:
        print(f"  ⚠️  Viability filter is blocking some signals")
    else:
        print(f"  ℹ️  Viability filter has minimal impact")
    print()

    # Kitchen sink check
    print(f"Maximum Possible Frequency (all filters OFF):")
    print(f"  Kitchen sink: {kitchen_sink_result['trades_per_month']:.2f} trades/month")
    if kitchen_sink_result['trades_per_month'] >= target_min:
        print(f"  ✅ Target IS achievable if we loosen filters enough")
    else:
        print(f"  🚨 Even with NO filters, frequency is {kitchen_sink_result['trades_per_month']:.2f} < {target_min:.1f}")
        print(f"     This suggests a FUNDAMENTAL ISSUE with the strategy or data")
    print()

    # ========================================================================
    # Recommendations
    # ========================================================================

    print()
    print("=" * 80)
    print("RECOMMENDATIONS")
    print("=" * 80)
    print()

    if kitchen_sink_result['trades_per_month'] < target_min:
        print("🚨 CRITICAL FINDING:")
        print("   Even with ALL filters disabled, frequency is below target.")
        print()
        print("Possible explanations:")
        print("  1. Setup trigger logic (ROC-score) is fundamentally broken")
        print("  2. Entry fill logic is too conservative (post-only never fills)")
        print("  3. Data quality issue (insufficient coverage or bad data)")
        print("  4. Time period is anomalous (unusual market conditions)")
        print()
        print("Recommended next steps:")
        print("  • Debug ROC-score calculation (verify it's working)")
        print("  • Check entry fill simulation (are setups generated but not filled?)")
        print("  • Examine gate audit for kitchen sink test")
        print("  • Consider pivoting strategy architecture entirely")
    elif no_regime_result['trades_per_month'] >= target_min:
        print("✅ SOLUTION FOUND:")
        print(f"   Disabling regime filter achieves {no_regime_result['trades_per_month']:.2f} trades/month")
        print()
        print("Recommendation:")
        print("  • Remove or significantly loosen regime filter (EMA200)")
        print("  • Test on longer periods to validate performance")
    elif all_loose_result['trades_per_month'] >= target_min:
        print("⚠️  PARTIAL SOLUTION:")
        print(f"   Loosening all filters achieves {all_loose_result['trades_per_month']:.2f} trades/month")
        print()
        print("Recommendation:")
        print("  • Implement selective filter loosening")
        print("  • Test combinations to find optimal balance")
    else:
        print("⚠️  INCONCLUSIVE:")
        print("   No single-filter change restores target frequency")
        print()
        print("Recommendation:")
        print("  • Consider filter redesign (scoring system instead of gates)")
        print("  • Test longer time periods (365+ days)")
        print("  • Evaluate alternative strategy architectures")

    print()
    print("=" * 80)
    print()


if __name__ == "__main__":
    run_filter_ablation_study(days=180)
