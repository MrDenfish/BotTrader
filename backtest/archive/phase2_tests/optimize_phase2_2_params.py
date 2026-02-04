"""
Phase 2.2 Parameter Optimization

Systematically test different parameter combinations to find optimal settings
that achieve 2-4 trades/month target with positive returns.

Key parameters to optimize:
1. bb_width_pct_threshold: Compression filter tightness
2. donch_len: Donchian breakout period
3. vol_min_mult: Volatility filter strictness

Goal: 2-4 trades/month with positive net P&L
"""

import sys
from datetime import datetime
import itertools

from config_4h_hybrid import Hybrid4hConfig, get_phase2_2_baseline_config
from engine_4h_hybrid import Hybrid4hBacktestEngine


def test_config(config: Hybrid4hConfig, symbol: str, days: int) -> dict:
    """Run backtest with given config and return key metrics"""

    engine = Hybrid4hBacktestEngine(config, data_dir="data")
    stats = engine.run_backtest(symbol, days)

    trades = stats.get('trades', 0)
    trades_per_month = (trades / days) * 30 if days > 0 else 0

    return {
        'trades': trades,
        'trades_per_month': trades_per_month,
        'signals': stats.get('signals', 0),
        'fill_rate': stats.get('signal_to_entry_pct', 0),
        'net_pnl': stats.get('net_pnl', 0),
        'win_rate': stats.get('win_rate', 0),
        'profit_factor': stats.get('profit_factor', 0),
        'expired': stats.get('expired_setups', 0)
    }


def run_optimization():
    """Run parameter optimization grid search"""

    print("=" * 80)
    print("PHASE 2.2 PARAMETER OPTIMIZATION")
    print("=" * 80)
    print()
    print("Testing parameter combinations to achieve 2-4 trades/month")
    print()

    symbol = "BTC-USD"
    days = 60  # Use 60 days for faster iteration

    # Parameter grid
    bb_thresholds = [30, 40, 50, 60]  # Compression percentile (looser = more signals)
    donch_lens = [5, 7, 10, 15]  # Donchian period (shorter = more signals)
    vol_mults = [0.5, 1.0, 1.5]  # Volatility filter (lower = more signals)

    total_tests = len(bb_thresholds) * len(donch_lens) * len(vol_mults)

    print(f"Parameter Grid:")
    print(f"  bb_width_pct_threshold: {bb_thresholds}")
    print(f"  donch_len: {donch_lens}")
    print(f"  vol_min_mult: {vol_mults}")
    print(f"\nTotal combinations: {total_tests}")
    print()
    print("=" * 80)
    print()

    results = []
    test_num = 0

    for bb_thresh, donch_len, vol_mult in itertools.product(bb_thresholds, donch_lens, vol_mults):
        test_num += 1

        # Create config with these parameters
        config = get_phase2_2_baseline_config()
        config.bb_width_pct_threshold = bb_thresh
        config.donch_len = donch_len
        config.vol_min_mult = vol_mult

        print(f"[{test_num}/{total_tests}] Testing: bb={bb_thresh}, donch={donch_len}, vol={vol_mult}...", end=" ")

        try:
            metrics = test_config(config, symbol, days)

            # Store results
            result = {
                'bb_thresh': bb_thresh,
                'donch_len': donch_len,
                'vol_mult': vol_mult,
                **metrics
            }
            results.append(result)

            # Print quick summary
            print(f"✓ {metrics['trades']} trades ({metrics['trades_per_month']:.1f}/mo), "
                  f"Net: ${metrics['net_pnl']:.2f}, WR: {metrics['win_rate']:.1%}")

        except Exception as e:
            print(f"✗ Error: {e}")
            continue

    print()
    print("=" * 80)
    print("OPTIMIZATION RESULTS")
    print("=" * 80)
    print()

    if not results:
        print("No successful tests!")
        return

    # Sort by trades/month descending
    results_by_freq = sorted(results, key=lambda x: x['trades_per_month'], reverse=True)

    # Sort by net P&L descending
    results_by_pnl = sorted(results, key=lambda x: x['net_pnl'], reverse=True)

    # Find configs in target range (2-4 trades/month)
    target_configs = [r for r in results if 2 <= r['trades_per_month'] <= 4]

    print(f"📊 Summary Statistics:")
    print(f"   Tests completed: {len(results)}/{total_tests}")
    print(f"   Configs with 2-4 trades/month: {len(target_configs)}")
    print()

    # Show top 10 by frequency
    print("=" * 80)
    print("TOP 10 BY TRADE FREQUENCY")
    print("=" * 80)
    print()
    print(f"{'Rank':<5} {'BB%':<5} {'Don':<5} {'Vol':<5} {'Trd/mo':<8} {'Signals':<8} {'Fill%':<7} {'Net P&L':<10} {'WR':<7}")
    print("-" * 80)

    for i, r in enumerate(results_by_freq[:10], 1):
        in_target = "✓" if 2 <= r['trades_per_month'] <= 4 else " "
        print(f"{i:<5} {r['bb_thresh']:<5} {r['donch_len']:<5} {r['vol_mult']:<5} "
              f"{r['trades_per_month']:<8.1f} {r['signals']:<8} {r['fill_rate']:<7.1f} "
              f"${r['net_pnl']:<9.2f} {r['win_rate']:<7.1%} {in_target}")

    print()

    # Show top 10 by P&L
    print("=" * 80)
    print("TOP 10 BY NET P&L")
    print("=" * 80)
    print()
    print(f"{'Rank':<5} {'BB%':<5} {'Don':<5} {'Vol':<5} {'Trd/mo':<8} {'Signals':<8} {'Net P&L':<10} {'WR':<7} {'PF':<7}")
    print("-" * 80)

    for i, r in enumerate(results_by_pnl[:10], 1):
        in_target = "✓" if 2 <= r['trades_per_month'] <= 4 else " "
        print(f"{i:<5} {r['bb_thresh']:<5} {r['donch_len']:<5} {r['vol_mult']:<5} "
              f"{r['trades_per_month']:<8.1f} {r['signals']:<8} "
              f"${r['net_pnl']:<9.2f} {r['win_rate']:<7.1%} {r['profit_factor']:<7.2f} {in_target}")

    print()

    # Show configs in target range
    if target_configs:
        print("=" * 80)
        print(f"CONFIGS IN TARGET RANGE (2-4 trades/month)")
        print("=" * 80)
        print()
        print(f"{'BB%':<5} {'Don':<5} {'Vol':<5} {'Trd/mo':<8} {'Trades':<7} {'Net P&L':<10} {'WR':<7} {'PF':<7}")
        print("-" * 80)

        # Sort by P&L within target range
        target_configs_sorted = sorted(target_configs, key=lambda x: x['net_pnl'], reverse=True)

        for r in target_configs_sorted:
            print(f"{r['bb_thresh']:<5} {r['donch_len']:<5} {r['vol_mult']:<5} "
                  f"{r['trades_per_month']:<8.1f} {r['trades']:<7} "
                  f"${r['net_pnl']:<9.2f} {r['win_rate']:<7.1%} {r['profit_factor']:<7.2f}")

        print()
        print("=" * 80)
        print("RECOMMENDED CONFIGURATION")
        print("=" * 80)
        print()

        # Pick best from target range (highest P&L)
        best = target_configs_sorted[0]

        print(f"Best config in target range:")
        print(f"  bb_width_pct_threshold: {best['bb_thresh']}")
        print(f"  donch_len: {best['donch_len']}")
        print(f"  vol_min_mult: {best['vol_mult']}")
        print()
        print(f"Expected performance (60 days):")
        print(f"  Trades/month: {best['trades_per_month']:.1f}")
        print(f"  Total trades: {best['trades']}")
        print(f"  Net P&L: ${best['net_pnl']:.2f}")
        print(f"  Win rate: {best['win_rate']:.1%}")
        print(f"  Profit factor: {best['profit_factor']:.2f}")

    else:
        print("⚠️  No configs achieved 2-4 trades/month target")
        print()
        print("Consider:")
        print("  - Further loosening compression threshold (>60)")
        print("  - Shorter Donchian period (<5)")
        print("  - Lower volatility filter (<0.5)")
        print("  - Disabling compression filter entirely")

    print()
    print("=" * 80)


if __name__ == "__main__":
    run_optimization()
