"""
CLI entry point for running strategies.

Usage:
    python -m strategies --strategy hybrid_4h_maker --symbol BTC-USD --days 360
    python -m strategies --strategy hybrid_4h_maker --symbol BTC-USD --days 60 --config phase2_3
"""

import argparse
import sys


def main():
    parser = argparse.ArgumentParser(
        description='Run a strategy plugin through the backtest engine'
    )
    parser.add_argument('--strategy', '-s', default='hybrid_4h_maker',
                        help='Strategy name (default: hybrid_4h_maker)')
    parser.add_argument('--symbol', default='BTC-USD',
                        help='Trading pair (default: BTC-USD)')
    parser.add_argument('--days', '-d', type=int, default=60,
                        help='Number of days to backtest (default: 60)')
    parser.add_argument('--config', '-c', default='default',
                        help='Config preset name (default: default)')
    parser.add_argument('--data-dir', default='backtest/data',
                        help='Data directory (default: backtest/data)')
    parser.add_argument('--resolution', '-r', default='1m',
                        help='Base resolution (default: 1m)')
    parser.add_argument('--export', '-e', default=None,
                        help='Export trades to CSV file')

    args = parser.parse_args()

    # Get config
    config = _get_config(args.strategy, args.config)
    if config is None:
        print(f"Unknown config preset: {args.config}")
        sys.exit(1)

    # Get strategy
    strategy = _get_strategy(args.strategy)
    if strategy is None:
        print(f"Unknown strategy: {args.strategy}")
        sys.exit(1)

    # Run backtest
    from strategies.engines.backtest_engine import BacktestEngine

    engine = BacktestEngine(
        strategy=strategy,
        config=config,
        data_dir=args.data_dir,
        resolution=args.resolution,
    )

    engine.load([args.symbol], days=args.days)
    stats = engine.run()
    engine.print_results(stats, args.symbol)

    if args.export:
        engine.export_trades(args.export)


def _get_config(strategy_name: str, config_name: str):
    """Get config preset by name."""
    if strategy_name == 'hybrid_4h_maker':
        from backtest.config_4h_hybrid import (
            get_default_config,
            get_conservative_config,
            get_aggressive_config,
            get_phase2_baseline_config,
            get_phase2_2_baseline_config,
            get_phase2_3_roc_baseline_config,
            get_phase2_4_stage_c_test_config,
        )
        presets = {
            'default': get_default_config,
            'conservative': get_conservative_config,
            'aggressive': get_aggressive_config,
            'phase2': get_phase2_baseline_config,
            'phase2_2': get_phase2_2_baseline_config,
            'phase2_3': get_phase2_3_roc_baseline_config,
            'phase2_4': get_phase2_4_stage_c_test_config,
        }
        factory = presets.get(config_name)
        if factory:
            return factory()
        return None

    return None


def _get_strategy(strategy_name: str):
    """Get strategy instance by name."""
    if strategy_name == 'hybrid_4h_maker':
        from strategies.hybrid_4h_maker.strategy import Hybrid4hMakerStrategy
        return Hybrid4hMakerStrategy()

    # Try registry
    from strategies.registry import get_registry
    registry = get_registry()
    cls = registry.get(strategy_name)
    if cls:
        return cls()

    return None


if __name__ == '__main__':
    main()
