"""
5m vs 1m Resolution Comparison Test

Validates that 5m base data produces similar backtest results to 1m data
for the purpose of optimization ranking.

Expected differences:
- Fill timing: 5m may be slightly less precise
- Fill rate: 5m may overestimate slightly (5m bars span 5× the range)
- Trade count: Should be identical or very close
- Net P&L: Should be within 5-10% for ranking purposes
"""

from config_4h_hybrid import get_phase2_2_baseline_config
from engine_4h_hybrid import Hybrid4hBacktestEngine


def run_comparison():
    """Compare 5m vs 1m backtest results"""

    print("=" * 80)
    print("5M VS 1M RESOLUTION COMPARISON TEST")
    print("=" * 80)
    print()
    print("Validating that 5m data produces similar results to 1m for optimization.")
    print()
    print("=" * 80)
    print()

    symbol = "BTC-USD"
    days = 60
    config = get_phase2_2_baseline_config()

    # Run on 1m data
    print("📊 RUNNING BACKTEST ON 1M DATA...")
    print()
    engine_1m = Hybrid4hBacktestEngine(config, data_dir="data", resolution='1m')
    stats_1m = engine_1m.run_backtest(symbol, days)

    print()
    print("=" * 80)
    print("1M RESULTS")
    print("=" * 80)
    print()
    engine_1m.print_results(stats_1m, symbol)

    # Run on 5m data
    print()
    print("=" * 80)
    print()
    print("📊 RUNNING BACKTEST ON 5M DATA...")
    print()
    engine_5m = Hybrid4hBacktestEngine(config, data_dir="data", resolution='5m')
    stats_5m = engine_5m.run_backtest(symbol, days)

    print()
    print("=" * 80)
    print("5M RESULTS")
    print("=" * 80)
    print()
    engine_5m.print_results(stats_5m, symbol)

    # Compare key metrics
    print()
    print("=" * 80)
    print("COMPARISON ANALYSIS")
    print("=" * 80)
    print()

    metrics = [
        ('Signals', 'signals'),
        ('Trades', 'trades'),
        ('Gross P&L', 'gross_pnl'),
        ('Fees', 'fees'),
        ('Net P&L', 'net_pnl'),
        ('Win Rate', 'win_rate'),
        ('Profit Factor', 'profit_factor'),
        ('Signal→Entry %', 'signal_to_entry_pct'),
    ]

    print(f"{'Metric':<20} {'1m':<15} {'5m':<15} {'Diff %':<10} {'Status'}")
    print("-" * 80)

    all_pass = True

    for label, key in metrics:
        val_1m = stats_1m.get(key, 0)
        val_5m = stats_5m.get(key, 0)

        # Calculate difference
        if val_1m == 0 and val_5m == 0:
            diff_pct = 0
        elif val_1m == 0:
            diff_pct = 100
        else:
            diff_pct = abs(val_5m - val_1m) / abs(val_1m) * 100

        # Format values
        if 'rate' in key.lower() or 'pct' in key.lower():
            val_1m_str = f"{val_1m:.1%}" if val_1m else "0.0%"
            val_5m_str = f"{val_5m:.1%}" if val_5m else "0.0%"
        elif 'pnl' in key.lower() or 'fee' in key.lower():
            val_1m_str = f"${val_1m:.2f}"
            val_5m_str = f"${val_5m:.2f}"
        else:
            val_1m_str = f"{val_1m}"
            val_5m_str = f"{val_5m}"

        # Determine status based on metric
        if key in ['signals', 'trades']:
            # Trade count should be identical or within ±1
            passed = abs(val_5m - val_1m) <= 1
            threshold = "±1 trade"
        elif key == 'signal_to_entry_pct':
            # Fill rate should be within ±10%
            passed = diff_pct <= 10
            threshold = "±10%"
        elif 'pnl' in key or 'fee' in key:
            # P&L should be within ±15% for ranking purposes
            passed = diff_pct <= 15
            threshold = "±15%"
        else:
            # Other metrics: ±20%
            passed = diff_pct <= 20
            threshold = "±20%"

        status = "✅ PASS" if passed else "❌ FAIL"

        if not passed:
            all_pass = False

        print(f"{label:<20} {val_1m_str:<15} {val_5m_str:<15} {diff_pct:<10.1f} {status}")

    print()
    print("=" * 80)
    print("VALIDATION SUMMARY")
    print("=" * 80)
    print()

    if all_pass:
        print("✅ VALIDATION PASSED")
        print()
        print("5m resolution is suitable for optimization:")
        print("  • Trade counts match (within ±1 trade)")
        print("  • P&L values are within acceptable range for ranking")
        print("  • Fill rates are comparable")
        print()
        print("Recommendation:")
        print("  • Use 5m data for parameter optimization (faster)")
        print("  • Use 1m data for final validation (more precise)")
    else:
        print("❌ VALIDATION FAILED")
        print()
        print("5m resolution shows significant differences from 1m:")
        print("  • Review metrics marked as FAIL above")
        print("  • May need to adjust tolerances or use 1m for optimization")

    print()
    print("=" * 80)
    print()

    return all_pass


if __name__ == "__main__":
    success = run_comparison()
    exit(0 if success else 1)
