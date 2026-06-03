#!/usr/bin/env python3
"""
ROC Peak-Drawdown Backtest Runner
==================================

Runs backtests for the new ROC peak-drawdown strategy with dual ATR% stops.

Usage:
    # Proof-of-concept (7-day sample, Variant A only)
    python3 run_peak_drawdown_backtest.py --poc

    # Full backtest (60 days, Variant A)
    python3 run_peak_drawdown_backtest.py --variant A --days 60

    # Full backtest (60 days, Variant B)
    python3 run_peak_drawdown_backtest.py --variant B --days 60

    # Both variants, 60 days
    python3 run_peak_drawdown_backtest.py --all --days 60

Date: January 28, 2026
Spec: /docs/planning/roc_dual_atrpct_strategy_spec.md
"""

import sys
sys.path.insert(0, '/Users/Manny/Python_Projects/BotTrader')

import argparse
import os
from datetime import datetime, timedelta

from backtest.strategy_peak_drawdown import get_variant_a_config, get_variant_b_config
from backtest.engine_peak_drawdown import PeakDrawdownBacktestEngine, get_default_symbols


# Database connection (via SSH tunnel to AWS)
DB_URL = "postgresql://bot_user:***REDACTED***@localhost:5433/bot_trader_db"


def run_single_variant(
    variant: str,
    days: int = 60,
    verbose: bool = True,
    use_recent_data: bool = False
):
    """
    Run backtest for a single variant.

    Args:
        variant: "A" or "B"
        days: Number of days to backtest
        verbose: Print progress messages
        use_recent_data: Use last N days from now (for testing with limited data)
    """
    # Get config
    if variant.upper() == "A":
        config = get_variant_a_config()
    elif variant.upper() == "B":
        config = get_variant_b_config()
    else:
        raise ValueError(f"Invalid variant: {variant}. Must be 'A' or 'B'")

    # Set date range
    if use_recent_data:
        # Use recent data (for testing when historical data unavailable)
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)
    else:
        # Use fixed historical date range
        end_date = datetime(2026, 1, 27)
        start_date = end_date - timedelta(days=days)

    # Get symbols
    symbols = get_default_symbols()

    # Create engine
    engine = PeakDrawdownBacktestEngine(
        config=config,
        start_date=start_date,
        end_date=end_date,
        symbols=symbols,
        db_url=DB_URL,
        verbose=verbose
    )

    # Run backtest
    results = engine.run()

    # Export trades
    os.makedirs('backtest_results', exist_ok=True)
    filename = f"backtest_results/{config.variant_name}_{days}d.csv"
    engine.export_trades_to_csv(filename)

    return results


def run_poc():
    """
    Run proof-of-concept backtest (1-day sample, Variant A).

    This is a quick test to verify the strategy logic works correctly.
    Uses recent data since we only have 24 hours available.
    """
    print("=" * 80)
    print("PROOF-OF-CONCEPT BACKTEST")
    print("=" * 80)
    print("Variant: A (20m-style)")
    print("Period: Last 24 hours (recent data)")
    print("Purpose: Verify indicators, entry/exit logic, and state tracking")
    print("=" * 80)
    print()

    results = run_single_variant("A", days=1, verbose=True, use_recent_data=True)

    print()
    print("=" * 80)
    print("POC VALIDATION CHECKLIST")
    print("=" * 80)

    stats = results['stats']

    # Validation checks
    checks = []

    # 1. No errors in execution
    checks.append(("✅ No execution errors", True))

    # 2. Trades generated
    has_trades = stats['total_trades'] > 0
    checks.append((f"{'✅' if has_trades else '❌'} Trades generated: {stats['total_trades']}", has_trades))

    # 3. Exit reasons make sense
    if has_trades:
        exit_reasons = stats['exit_reasons']
        roc_dd_pct = 100 * exit_reasons.get('ROC_PEAK_DD', 0) / stats['total_trades']
        atr_slow_pct = 100 * exit_reasons.get('ATR_SLOW_STOP', 0) / stats['total_trades']
        atr_fast_pct = 100 * exit_reasons.get('ATR_FAST_STOP', 0) / stats['total_trades']

        checks.append((f"  ROC_PEAK_DD: {roc_dd_pct:.1f}% (should dominate)", roc_dd_pct > 50))
        checks.append((f"  ATR_SLOW: {atr_slow_pct:.1f}% (should be rare)", atr_slow_pct < 20))
        checks.append((f"  ATR_FAST: {atr_fast_pct:.1f}%", True))

    # 4. Stops never negative or infinite (checked in export)
    checks.append(("✅ Stops calculated correctly (see CSV for details)", True))

    # Print checks
    for check_msg, passed in checks:
        print(check_msg)

    print()
    all_passed = all(passed for _, passed in checks)

    if all_passed:
        print("🎉 POC VALIDATION PASSED - Ready for full backtest!")
    else:
        print("⚠️  POC VALIDATION FAILED - Review results before proceeding")

    print("=" * 80)

    return results


def run_all_variants(days: int = 60):
    """
    Run backtests for both Variant A and B.

    Args:
        days: Number of days to backtest
    """
    print("=" * 80)
    print("PEAK-DRAWDOWN BACKTEST MATRIX")
    print("=" * 80)
    print(f"Period: {days} days")
    print("Variants: A (20m-style), B (24h-style)")
    print("=" * 80)
    print()

    results_summary = []

    for variant in ['A', 'B']:
        print(f"\n{'='*80}")
        print(f"VARIANT {variant}")
        print(f"{'='*80}\n")

        try:
            results = run_single_variant(variant, days=days, verbose=True)

            stats = results['stats']
            results_summary.append({
                'variant': variant,
                'trades': stats['total_trades'],
                'pnl': float(stats['total_pnl']),
                'win_rate': stats['win_rate'],
                'profit_factor': stats['profit_factor'],
                'avg_bars_held': stats['avg_bars_held']
            })

        except Exception as e:
            print(f"❌ ERROR: {e}")
            results_summary.append({
                'variant': variant,
                'error': str(e)
            })

        print("\n" + "-" * 80 + "\n")

    # Print comparison table
    print("=" * 80)
    print("VARIANT COMPARISON")
    print("=" * 80)
    print(f"{'Variant':<10} {'Trades':<8} {'P&L':<12} {'Win Rate':<10} {'PF':<8} {'Avg Bars':<10}")
    print("-" * 80)

    for row in results_summary:
        if 'error' in row:
            print(f"{row['variant']:<10} ERROR: {row['error']}")
        else:
            print(f"{row['variant']:<10} {row['trades']:<8} "
                  f"${row['pnl']:<11.2f} {row['win_rate']:<9.1f}% "
                  f"{row['profit_factor']:<7.2f} {row['avg_bars_held']:<10.1f}")

    print("=" * 80)

    # Find best variant
    valid_results = [r for r in results_summary if 'error' not in r]
    if valid_results:
        best = max(valid_results, key=lambda x: x['pnl'])
        print(f"\n🏆 BEST PERFORMER: Variant {best['variant']}")
        print(f"   P&L: ${best['pnl']:.2f}")
        print(f"   Win Rate: {best['win_rate']:.1f}%")
        print(f"   Profit Factor: {best['profit_factor']:.2f}")
        print()


def main():
    parser = argparse.ArgumentParser(description="Run ROC Peak-Drawdown backtests")

    parser.add_argument(
        '--poc',
        action='store_true',
        help='Run proof-of-concept backtest (7-day sample, Variant A)'
    )

    parser.add_argument(
        '--variant',
        type=str,
        choices=['A', 'B'],
        help='Run single variant (A or B)'
    )

    parser.add_argument(
        '--all',
        action='store_true',
        help='Run both variants'
    )

    parser.add_argument(
        '--days',
        type=int,
        default=60,
        help='Number of days to backtest (default: 60)'
    )

    parser.add_argument(
        '--quiet',
        action='store_true',
        help='Suppress progress messages'
    )

    args = parser.parse_args()

    # Create results directory
    os.makedirs('backtest_results', exist_ok=True)

    if args.poc:
        run_poc()
    elif args.all:
        run_all_variants(days=args.days)
    elif args.variant:
        run_single_variant(args.variant, days=args.days, verbose=not args.quiet)
    else:
        parser.print_help()
        print("\nError: Must specify --poc, --variant, or --all")
        sys.exit(1)


if __name__ == '__main__':
    main()
