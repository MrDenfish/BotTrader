#!/usr/bin/env python3
"""
BotTrader Backtest Runner

Quick script to run backtests with different configurations.

Usage:
    python3 run_backtest.py --days 30                    # 30-day backtest
    python3 run_backtest.py --start 2025-12-01 --end 2026-01-12
    python3 run_backtest.py --config aggressive          # Test different config
"""

import sys
import argparse
from datetime import datetime, timedelta
from decimal import Decimal

sys.path.insert(0, '/Users/Manny/Python_Projects/BotTrader')

from backtest.config import (
    StrategyConfig, BacktestConfig,
    CURRENT_PRODUCTION, OPTION_A_WIDER_TP, OPTION_2_WIDER_SL, OPTION_3_PEAK_FOCUS,
    TEST_1_CONSERVATIVE, TEST_2_MODERATE, TEST_3_AGGRESSIVE,
    AGGRESSIVE_TP_SL, CONSERVATIVE_TP_SL, ROC_SENSITIVE, ROC_STRICT
)
from backtest.engine import BacktestEngine
from backtest.reporter import BacktestReporter


# Database connection (SSH tunnel on port 5433)
DB_URL = "postgresql://bot_user:***REDACTED***@localhost:5433/bot_trader_db"


def parse_args():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(description="Run BotTrader Strategy Backtest")

    # Date range options
    parser.add_argument('--days', type=int, help='Number of days to backtest (from today backwards)')
    parser.add_argument('--start', type=str, help='Start date (YYYY-MM-DD)')
    parser.add_argument('--end', type=str, help='End date (YYYY-MM-DD)')

    # Strategy config
    parser.add_argument(
        '--config',
        type=str,
        default='production',
        choices=['production', 'test_1', 'test_2', 'test_3', 'option_a', 'option_2', 'option_3', 'aggressive', 'conservative', 'roc_sensitive', 'roc_strict'],
        help='Strategy configuration to test'
    )

    # Capital
    parser.add_argument('--capital', type=float, default=10000.0, help='Initial capital')

    # Output
    parser.add_argument('--export', type=str, help='Export trades to CSV file')
    parser.add_argument('--quiet', action='store_true', help='Suppress verbose output')

    return parser.parse_args()


def get_strategy_config(config_name: str) -> StrategyConfig:
    """Get strategy configuration by name"""
    configs = {
        'production': CURRENT_PRODUCTION,
        'test_1': TEST_1_CONSERVATIVE,
        'test_2': TEST_2_MODERATE,
        'test_3': TEST_3_AGGRESSIVE,
        'option_a': OPTION_A_WIDER_TP,
        'option_2': OPTION_2_WIDER_SL,
        'option_3': OPTION_3_PEAK_FOCUS,
        'aggressive': AGGRESSIVE_TP_SL,
        'conservative': CONSERVATIVE_TP_SL,
        'roc_sensitive': ROC_SENSITIVE,
        'roc_strict': ROC_STRICT
    }
    return configs.get(config_name, CURRENT_PRODUCTION)


def main():
    args = parse_args()

    # Determine date range
    if args.days:
        end_date = datetime.now()
        start_date = end_date - timedelta(days=args.days)
    elif args.start and args.end:
        start_date = datetime.strptime(args.start, '%Y-%m-%d')
        end_date = datetime.strptime(args.end, '%Y-%m-%d')
    else:
        # Default: 30 days
        end_date = datetime.now()
        start_date = end_date - timedelta(days=30)

    # Get strategy config
    strategy = get_strategy_config(args.config)

    # Create backtest config
    backtest_config = BacktestConfig(
        start_date=start_date,
        end_date=end_date,
        initial_capital=Decimal(str(args.capital)),
        verbose=not args.quiet
    )

    print("\n" + "=" * 70)
    print("  BotTrader Backtesting System")
    print("=" * 70)
    print()
    print(f"Configuration: {args.config.upper()}")
    print(f"Period: {start_date.date()} to {end_date.date()} ({(end_date - start_date).days} days)")
    print(f"Initial Capital: ${args.capital:,.2f}")
    print()

    # Run backtest
    engine = BacktestEngine(strategy, backtest_config, DB_URL)
    results = engine.run()

    # Print results
    reporter = BacktestReporter()
    reporter.print_summary(results)

    if not args.quiet:
        reporter.print_trade_list(results, limit=20)

    # Export if requested
    if args.export:
        reporter.export_csv(results, args.export)

    # Return exit code based on profitability
    return 0 if results.total_pnl > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
