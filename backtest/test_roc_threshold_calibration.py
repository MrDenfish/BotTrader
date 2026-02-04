"""
Step 3: ROC-score Threshold Calibration (Section 11.3)

Test multiple ROC-score thresholds with compression OFF to find the optimal
threshold that achieves target signal frequency (60-120 setups/year).

This implements the percentile-based calibration approach:
- Test P40, P50, P60, P70 percentile thresholds
- Measure resulting signal frequency
- Identify threshold that hits 2-4 trades/month target
"""

from config_4h_hybrid import get_phase2_3_roc_baseline_config
from engine_4h_hybrid import Hybrid4hBacktestEngine
import numpy as np


def run_roc_threshold_calibration(days: int = 180):
    """Run ROC-score threshold calibration test

    Tests multiple thresholds to find optimal value for target frequency.
    """

    print("=" * 80)
    print("ROC-SCORE THRESHOLD CALIBRATION (Section 11.3)")
    print("=" * 80)
    print()
    print(f"Testing period: {days} days")
    print("Compression: OFF (isolate setup trigger threshold)")
    print()

    # Get baseline config and disable compression
    config = get_phase2_3_roc_baseline_config()
    config.use_compression_filter = False

    # First, run a baseline test to collect ROC-score distribution
    print("=" * 80)
    print("📊 STEP 1: BASELINE TEST - COLLECT ROC-SCORE DISTRIBUTION")
    print("=" * 80)
    print()

    engine = Hybrid4hBacktestEngine(config)
    stats = engine.run_backtest("BTC-USD", days=days)

    # The ROC-score percentile calibration is already printed by the engine
    # We just need to extract the recommended thresholds and test them

    print()
    print("=" * 80)
    print("📊 STEP 2: TEST PERCENTILE-BASED THRESHOLDS")
    print("=" * 80)
    print()

    # Define thresholds to test (based on typical distributions)
    # These will be refined based on actual distribution
    test_configs = [
        ("P40-approx", 0.8),
        ("P50-approx", 1.0),
        ("P60-approx", 1.1),
        ("P70-approx", 1.2),
        ("Current", 1.2),  # Current default
    ]

    results = []

    for label, threshold in test_configs:
        print()
        print("-" * 80)
        print(f"Testing: {label} (threshold={threshold:.2f})")
        print("-" * 80)

        # Create config with this threshold
        test_config = get_phase2_3_roc_baseline_config()
        test_config.use_compression_filter = False
        test_config.roc_score_thresh = threshold

        # Run backtest
        test_engine = Hybrid4hBacktestEngine(test_config)
        test_stats = test_engine.run_backtest("BTC-USD", days=days)

        # Extract key metrics
        signals = test_stats.get('signals', 0)
        trades = test_stats.get('trades', 0)
        net_pnl = test_stats.get('net_pnl', 0)
        win_rate = test_stats.get('win_rate', 0)

        # Calculate monthly rates
        months = days / 30.0
        signals_per_month = signals / months if months > 0 else 0
        trades_per_month = trades / months if months > 0 else 0

        results.append({
            'label': label,
            'threshold': threshold,
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
    print("THRESHOLD CALIBRATION RESULTS")
    print("=" * 80)
    print()

    print(f"{'Threshold':<15} {'Label':<12} {'Signals':<10} {'Sig/mo':<10} {'Trades':<10} {'Tr/mo':<10} {'Net P&L':<12} {'Win Rate':<10}")
    print("-" * 80)

    for r in results:
        print(f"{r['threshold']:<15.2f} {r['label']:<12} {r['signals']:<10} {r['signals_per_month']:<10.2f} {r['trades']:<10} {r['trades_per_month']:<10.2f} ${r['net_pnl']:<11.2f} {r['win_rate']:<9.1%}")

    print("=" * 80)
    print()

    # ========================================================================
    # Recommendations
    # ========================================================================

    print()
    print("=" * 80)
    print("RECOMMENDATIONS")
    print("=" * 80)
    print()

    target_min = 2.0  # 2 trades/month minimum
    target_max = 4.0  # 4 trades/month maximum

    print(f"Target frequency: {target_min}-{target_max} trades/month")
    print()

    # Find thresholds that meet target
    meets_target = [r for r in results if r['trades_per_month'] >= target_min]

    if meets_target:
        print("✅ Thresholds that meet target frequency:")
        for r in meets_target:
            print(f"   • {r['label']}: {r['threshold']:.2f} ({r['trades_per_month']:.2f} trades/month)")
        print()

        # Recommend the one with best performance
        best = max(meets_target, key=lambda x: x['net_pnl'])
        print(f"✅ RECOMMENDED: {best['label']} (threshold={best['threshold']:.2f})")
        print(f"   → {best['trades_per_month']:.2f} trades/month")
        print(f"   → ${best['net_pnl']:.2f} net P&L")
        print(f"   → {best['win_rate']:.1%} win rate")
    else:
        print("⚠️  NO thresholds meet target frequency!")
        print()

        # Find the one with highest frequency
        best = max(results, key=lambda x: x['trades_per_month'])
        shortage_pct = (target_min - best['trades_per_month']) / target_min * 100

        print(f"📊 Best available: {best['label']} (threshold={best['threshold']:.2f})")
        print(f"   → {best['trades_per_month']:.2f} trades/month")
        print(f"   → Still {shortage_pct:.1f}% below target")
        print()

        print("🚨 CONCLUSION: Even with very loose ROC-score threshold, frequency is too low")
        print()
        print("Next steps:")
        print("  1. Consider loosening OTHER filters (regime, viability)")
        print("  2. Consider redesigning filter architecture (scoring system)")
        print("  3. Consider pivoting strategy approach entirely")

    print()
    print("=" * 80)
    print()


if __name__ == "__main__":
    # Run calibration test on 180 days (matches current data availability)
    run_roc_threshold_calibration(days=180)
