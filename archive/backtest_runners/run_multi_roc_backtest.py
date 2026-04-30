#!/usr/bin/env python3
"""
Multi-ROC Backtest Runner
=========================

Runs backtests for ROC_MOMO_20M and ROC_MOMO_24H strategies across multiple configurations.

Usage:
    # Test single configuration
    python3 run_multi_roc_backtest.py --config BASELINE --strategy both

    # Test all configurations
    python3 run_multi_roc_backtest.py --all

    # Test specific strategy only
    python3 run_multi_roc_backtest.py --config PURE_MOMENTUM --strategy 20m
    python3 run_multi_roc_backtest.py --config PURE_MOMENTUM --strategy 24h

Date: January 27, 2026
"""

import sys
sys.path.insert(0, '/Users/Manny/Python_Projects/BotTrader')

import argparse
from datetime import datetime, timedelta
from decimal import Decimal

from backtest.config_multi_roc import ALL_CONFIGS, get_config
from backtest.engine_multi_roc import MultiROCBacktestEngine
from backtest.reporter import BacktestReporter


# Database connection (via SSH tunnel to AWS)
DB_URL = "postgresql://bot_user:7317botTrade4ssm@localhost:5433/bot_trader_db"


def run_single_backtest(
    config_name: str,
    strategy: str,
    days: int = 60,
    verbose: bool = True
):
    """Run a single backtest configuration"""

    # Get config
    config = get_config(config_name)

    # Determine which strategies to test
    test_20m = strategy in ['both', '20m']
    test_24h = strategy in ['both', '24h']

    # Set date range
    end_date = datetime(2026, 1, 27)
    start_date = end_date - timedelta(days=days)

    # Create engine
    engine = MultiROCBacktestEngine(
        config=config,
        start_date=start_date,
        end_date=end_date,
        db_url=DB_URL,
        test_20m=test_20m,
        test_24h=test_24h,
        initial_capital=Decimal("10000.00"),
        verbose=verbose
    )

    # Run backtest
    results = engine.run()

    # Print results
    BacktestReporter.print_summary(results)

    # Export to CSV
    strategy_label = strategy if strategy != 'both' else 'combined'
    filename = f"backtest_results/{config_name}_{strategy_label}_{days}d.csv"
    BacktestReporter.export_csv(results, filename)

    if verbose:
        print(f"📊 Results exported to: {filename}\n")

    return results


def run_all_backtests(days: int = 60):
    """Run all 12 backtest combinations"""

    print("=" * 80)
    print("MULTI-ROC BACKTEST MATRIX")
    print("=" * 80)
    print(f"Period: {days} days")
    print(f"Configurations: {len(ALL_CONFIGS)}")
    print(f"Strategies per config: 3 (20M solo, 24H solo, Both combined)")
    print(f"Total backtests: {len(ALL_CONFIGS) * 3}")
    print("=" * 80)
    print()

    results_summary = []

    for config_name in ALL_CONFIGS.keys():
        print(f"\n{'='*80}")
        print(f"CONFIGURATION: {config_name}")
        print(f"{'='*80}\n")

        for strategy in ['20m', '24h', 'both']:
            print(f"\n--- Strategy: {strategy.upper()} ---\n")

            try:
                results = run_single_backtest(
                    config_name=config_name,
                    strategy=strategy,
                    days=days,
                    verbose=True
                )

                results_summary.append({
                    'config': config_name,
                    'strategy': strategy,
                    'trades': len(results.trades),
                    'pnl': float(results.total_pnl),
                    'win_rate': float(results.win_rate),
                    'profit_factor': float(results.profit_factor) if results.profit_factor else 0,
                })

            except Exception as e:
                print(f"❌ ERROR: {e}")
                results_summary.append({
                    'config': config_name,
                    'strategy': strategy,
                    'error': str(e)
                })

            print("\n" + "-" * 80 + "\n")

    # Print summary table
    print("\n" + "=" * 80)
    print("BACKTEST MATRIX SUMMARY")
    print("=" * 80)
    print(f"{'Config':<20} {'Strategy':<10} {'Trades':<8} {'P&L':<12} {'Win Rate':<10} {'PF':<8}")
    print("-" * 80)

    for row in results_summary:
        if 'error' in row:
            print(f"{row['config']:<20} {row['strategy']:<10} ERROR: {row['error']}")
        else:
            print(f"{row['config']:<20} {row['strategy']:<10} {row['trades']:<8} "
                  f"${row['pnl']:<11.2f} {row['win_rate']:<9.1f}% {row['profit_factor']:<8.2f}")

    print("=" * 80)

    # Find best configuration
    valid_results = [r for r in results_summary if 'error' not in r]
    if valid_results:
        best = max(valid_results, key=lambda x: x['pnl'])
        print(f"\n🏆 BEST PERFORMER:")
        print(f"   Config: {best['config']}")
        print(f"   Strategy: {best['strategy']}")
        print(f"   P&L: ${best['pnl']:.2f}")
        print(f"   Win Rate: {best['win_rate']:.1f}%")
        print()


def main():
    parser = argparse.ArgumentParser(description="Run Multi-ROC strategy backtests")

    parser.add_argument(
        '--config',
        type=str,
        choices=list(ALL_CONFIGS.keys()),
        help='Configuration to test (BASELINE, PURE_MOMENTUM, etc.)'
    )

    parser.add_argument(
        '--strategy',
        type=str,
        choices=['20m', '24h', 'both'],
        default='both',
        help='Which strategy to test (default: both)'
    )

    parser.add_argument(
        '--days',
        type=int,
        default=60,
        help='Number of days to backtest (default: 60)'
    )

    parser.add_argument(
        '--all',
        action='store_true',
        help='Run all 12 backtest combinations'
    )

    parser.add_argument(
        '--quiet',
        action='store_true',
        help='Suppress progress messages'
    )

    args = parser.parse_args()

    # Create results directory
    import os
    os.makedirs('backtest_results', exist_ok=True)

    if args.all:
        run_all_backtests(days=args.days)
    elif args.config:
        run_single_backtest(
            config_name=args.config,
            strategy=args.strategy,
            days=args.days,
            verbose=not args.quiet
        )
    else:
        parser.print_help()
        print("\nError: Must specify either --config or --all")
        sys.exit(1)


if __name__ == '__main__':
    main()
