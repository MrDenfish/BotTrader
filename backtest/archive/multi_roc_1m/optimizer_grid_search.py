#!/usr/bin/env python3
"""
Grid Search Parameter Optimizer
================================

Systematic parameter optimization for the ROC peak-drawdown strategy.

Tests all combinations of parameters to find optimal configuration.

Usage:
    # Quick test (small grid)
    python3 backtest/optimizer_grid_search.py --variant A --quick

    # Full optimization
    python3 backtest/optimizer_grid_search.py --variant A --days 60

Date: January 28, 2026
"""

import sys
sys.path.insert(0, '/Users/Manny/Python_Projects/BotTrader')

import argparse
import os
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import List, Dict, Tuple
from itertools import product
from decimal import Decimal
import multiprocessing as mp
from functools import partial

from backtest.strategy_peak_drawdown import PeakDrawdownConfig
from backtest.engine_peak_drawdown import PeakDrawdownBacktestEngine, get_default_symbols


# Database connection
DB_URL = "postgresql://bot_user:7317botTrade4ssm@localhost:5433/bot_trader_db"


# ============================================================================
# Parameter Grids
# ============================================================================

def get_variant_a_grid_quick() -> Dict:
    """
    Quick grid for Variant A (20m-style) - 27 combinations.

    Tests core parameters only for fast iteration.
    """
    return {
        'roc_entry_threshold': [0.002, 0.003, 0.004],  # Entry quality
        'X_dd': [0.30, 0.40, 0.50],                     # Peak drawdown
        'M_fast': [1.5, 1.8, 2.0],                      # Fast stop tightness

        # Fixed params
        'roc_len': [9],
        'roc_ema_len': [5],
        'roc_arm_threshold': [0.0015],
        'vol_sma_len': [20],
        'vol_ratio_min': [2.0],
        'atr_fast_len': [14],
        'fast_trail_activation_k': [1.0],
        'tf_slow': ['15min'],
        'atr_slow_len': [14],
        'M_slow': [3.0],
        'min_bars_after_entry_for_exit': [3],
        'order_size': [Decimal("100.00")],
        'fee_rate': [Decimal("0.006")]
    }


def get_variant_a_grid_full() -> Dict:
    """
    Full grid for Variant A (20m-style) - 1,296 combinations.

    Comprehensive parameter sweep.
    """
    return {
        'roc_entry_threshold': [0.002, 0.003, 0.004, 0.005],
        'roc_arm_threshold': [0.0010, 0.0015, 0.0020],
        'X_dd': [0.30, 0.35, 0.40, 0.45, 0.50],
        'M_fast': [1.5, 1.8, 2.0, 2.2],
        'M_slow': [2.5, 3.0, 3.5],
        'vol_ratio_min': [1.5, 2.0, 2.5],

        # Fixed params
        'roc_len': [9],
        'roc_ema_len': [5],
        'vol_sma_len': [20],
        'atr_fast_len': [14],
        'fast_trail_activation_k': [1.0],
        'tf_slow': ['15min'],
        'atr_slow_len': [14],
        'min_bars_after_entry_for_exit': [3],
        'order_size': [Decimal("100.00")],
        'fee_rate': [Decimal("0.006")]
    }


def get_variant_b_grid_quick() -> Dict:
    """
    Quick grid for Variant B (24h-style) - 27 combinations.
    """
    return {
        'roc_entry_threshold': [0.006, 0.009, 0.012],
        'X_dd': [0.50, 0.60, 0.70],
        'M_fast': [2.0, 2.2, 2.5],

        # Fixed params
        'roc_len': [60],
        'roc_ema_len': [9],
        'roc_arm_threshold': [0.0030],
        'vol_sma_len': [45],
        'vol_ratio_min': [2.0],
        'atr_fast_len': [21],
        'fast_trail_activation_k': [1.0],
        'tf_slow': ['1H'],
        'atr_slow_len': [14],
        'M_slow': [3.5],
        'min_bars_after_entry_for_exit': [3],
        'order_size': [Decimal("100.00")],
        'fee_rate': [Decimal("0.006")]
    }


# ============================================================================
# Grid Search Engine
# ============================================================================

def create_config_from_params(variant_name: str, params: Dict) -> PeakDrawdownConfig:
    """Create strategy config from parameter dict."""
    return PeakDrawdownConfig(
        variant_name=variant_name,
        roc_len=params['roc_len'],
        roc_ema_len=params['roc_ema_len'],
        roc_entry_threshold=params['roc_entry_threshold'],
        roc_arm_threshold=params['roc_arm_threshold'],
        vol_sma_len=params['vol_sma_len'],
        vol_ratio_min=params['vol_ratio_min'],
        atr_fast_len=params['atr_fast_len'],
        M_fast=params['M_fast'],
        fast_trail_activation_k=params['fast_trail_activation_k'],
        tf_slow=params['tf_slow'],
        atr_slow_len=params['atr_slow_len'],
        M_slow=params['M_slow'],
        X_dd=params['X_dd'],
        min_bars_after_entry_for_exit=params['min_bars_after_entry_for_exit'],
        order_size=params['order_size'],
        fee_rate=params['fee_rate']
    )


def calculate_sharpe_ratio(returns: pd.Series, periods_per_year: int = 525600) -> float:
    """
    Calculate annualized Sharpe ratio.

    Args:
        returns: Series of returns (not cumulative)
        periods_per_year: Number of 1-minute bars in a year

    Returns:
        Sharpe ratio (annualized)
    """
    if len(returns) == 0 or returns.std() == 0:
        return 0.0

    mean_return = returns.mean()
    std_return = returns.std()

    # Annualize
    sharpe = (mean_return / std_return) * np.sqrt(periods_per_year)

    return float(sharpe)


def run_single_backtest(
    params: Dict,
    variant_name: str,
    start_date: datetime,
    end_date: datetime,
    symbols: List[str],
    db_url: str
) -> Dict:
    """
    Run a single backtest with given parameters.

    Returns:
        Dictionary with results and metrics
    """
    # Create config
    config = create_config_from_params(variant_name, params)

    # Run backtest
    try:
        engine = PeakDrawdownBacktestEngine(
            config=config,
            start_date=start_date,
            end_date=end_date,
            symbols=symbols,
            db_url=db_url,
            verbose=False  # Suppress output
        )

        results = engine.run()
        stats = results['stats']
        trades = results['trades']

        # Calculate additional metrics
        if trades:
            returns = pd.Series([float(t.net_pnl) for t in trades])
            sharpe = calculate_sharpe_ratio(returns)

            # Max drawdown
            cumulative = returns.cumsum()
            running_max = cumulative.cummax()
            drawdown = (cumulative - running_max) / running_max.abs().replace(0, 1)
            max_drawdown = float(drawdown.min())
        else:
            sharpe = 0.0
            max_drawdown = 0.0

        return {
            'params': params,
            'total_pnl': float(stats['total_pnl']),
            'total_trades': stats['total_trades'],
            'win_rate': stats['win_rate'],
            'profit_factor': stats['profit_factor'],
            'avg_win': float(stats['avg_win']),
            'avg_loss': float(stats['avg_loss']),
            'avg_bars_held': stats['avg_bars_held'],
            'sharpe_ratio': sharpe,
            'max_drawdown': max_drawdown,
            'exit_reasons': stats['exit_reasons'],
            'success': True
        }

    except Exception as e:
        return {
            'params': params,
            'error': str(e),
            'success': False
        }


def generate_param_combinations(param_grid: Dict) -> List[Dict]:
    """
    Generate all combinations from parameter grid.

    Args:
        param_grid: Dict of parameter names to lists of values

    Returns:
        List of parameter dictionaries
    """
    # Get all keys and values
    keys = list(param_grid.keys())
    values = list(param_grid.values())

    # Generate cartesian product
    combinations = []
    for combo in product(*values):
        param_dict = dict(zip(keys, combo))
        combinations.append(param_dict)

    return combinations


def run_grid_search(
    variant: str,
    param_grid: Dict,
    start_date: datetime,
    end_date: datetime,
    symbols: List[str] = None,
    db_url: str = DB_URL,
    parallel: bool = True,
    max_workers: int = 4
) -> pd.DataFrame:
    """
    Run grid search optimization.

    Args:
        variant: "A" or "B"
        param_grid: Parameter grid dictionary
        start_date: Backtest start date
        end_date: Backtest end date
        symbols: List of symbols (default: from database)
        db_url: Database URL
        parallel: Use parallel processing
        max_workers: Number of parallel workers

    Returns:
        DataFrame with all results ranked by Sharpe ratio
    """
    variant_name = f"{variant}_GRID_SEARCH"

    if symbols is None:
        symbols = get_default_symbols()

    # Generate all combinations
    param_combinations = generate_param_combinations(param_grid)

    print("=" * 80)
    print("GRID SEARCH PARAMETER OPTIMIZATION")
    print("=" * 80)
    print(f"Variant: {variant}")
    print(f"Period: {start_date.date()} to {end_date.date()}")
    print(f"Symbols: {len(symbols)}")
    print(f"Total combinations: {len(param_combinations)}")
    print(f"Parallel processing: {parallel} (workers: {max_workers})")
    print("=" * 80)
    print()

    # Run all backtests
    results = []

    if parallel and len(param_combinations) > 1:
        # Parallel execution
        print(f"Running {len(param_combinations)} backtests in parallel...")

        # Create partial function with fixed args
        run_func = partial(
            run_single_backtest,
            variant_name=variant_name,
            start_date=start_date,
            end_date=end_date,
            symbols=symbols,
            db_url=db_url
        )

        # Run in parallel
        with mp.Pool(processes=max_workers) as pool:
            results = pool.map(run_func, param_combinations)

    else:
        # Sequential execution
        for i, params in enumerate(param_combinations, 1):
            print(f"[{i}/{len(param_combinations)}] Testing configuration...", end='\r')
            result = run_single_backtest(
                params, variant_name, start_date, end_date, symbols, db_url
            )
            results.append(result)

    print()
    print(f"✅ Grid search complete: {len(results)} results")
    print()

    # Convert to DataFrame
    results_df = pd.DataFrame(results)

    # Separate successful and failed
    success_df = results_df[results_df['success'] == True].copy()
    failed_df = results_df[results_df['success'] == False].copy()

    if not success_df.empty:
        # Sort by Sharpe ratio (primary metric)
        success_df = success_df.sort_values('sharpe_ratio', ascending=False).reset_index(drop=True)

        # Add rank
        success_df['rank'] = range(1, len(success_df) + 1)

    print(f"Successful: {len(success_df)}")
    print(f"Failed: {len(failed_df)}")

    return success_df, failed_df


# ============================================================================
# Results Analysis
# ============================================================================

def print_top_configs(results_df: pd.DataFrame, n: int = 10):
    """Print top N configurations."""
    print("=" * 80)
    print(f"TOP {n} CONFIGURATIONS (by Sharpe Ratio)")
    print("=" * 80)
    print()

    for i, row in results_df.head(n).iterrows():
        print(f"Rank #{row['rank']}:")
        print(f"  Sharpe Ratio: {row['sharpe_ratio']:.3f}")
        print(f"  Total P&L: ${row['total_pnl']:.2f}")
        print(f"  Profit Factor: {row['profit_factor']:.2f}")
        print(f"  Win Rate: {row['win_rate']:.1f}%")
        print(f"  Trades: {row['total_trades']}")
        print(f"  Max Drawdown: {row['max_drawdown']:.1%}")
        print(f"  Avg Hold: {row['avg_bars_held']:.1f} bars")

        params = row['params']
        print(f"  Parameters:")
        print(f"    roc_entry: {params['roc_entry_threshold']:.4f} ({params['roc_entry_threshold']*100:.2f}%)")
        print(f"    X_dd: {params['X_dd']:.2f}")
        print(f"    M_fast: {params['M_fast']:.1f}")
        print(f"    M_slow: {params['M_slow']:.1f}")
        print(f"    vol_ratio_min: {params['vol_ratio_min']:.1f}")
        print()


def export_results(
    results_df: pd.DataFrame,
    variant: str,
    grid_type: str = "quick"
):
    """Export results to CSV."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"backtest_results/grid_search_{variant}_{grid_type}_{timestamp}.csv"

    # Flatten params column for CSV
    params_df = pd.json_normalize(results_df['params'])
    export_df = pd.concat([
        results_df.drop('params', axis=1),
        params_df
    ], axis=1)

    export_df.to_csv(filename, index=False)

    print(f"📊 Results exported to: {filename}")


# ============================================================================
# Main
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="Grid search parameter optimization")

    parser.add_argument(
        '--variant',
        type=str,
        choices=['A', 'B'],
        required=True,
        help='Strategy variant (A=20m-style, B=24h-style)'
    )

    parser.add_argument(
        '--quick',
        action='store_true',
        help='Use quick grid (27 combinations) for fast testing'
    )

    parser.add_argument(
        '--days',
        type=int,
        default=1,
        help='Number of days to backtest (default: 1 for recent data)'
    )

    parser.add_argument(
        '--workers',
        type=int,
        default=4,
        help='Number of parallel workers (default: 4)'
    )

    parser.add_argument(
        '--no-parallel',
        action='store_true',
        help='Disable parallel processing'
    )

    args = parser.parse_args()

    # Create results directory
    os.makedirs('backtest_results', exist_ok=True)

    # Get parameter grid
    if args.variant == 'A':
        param_grid = get_variant_a_grid_quick() if args.quick else get_variant_a_grid_full()
    else:
        param_grid = get_variant_b_grid_quick()

    grid_type = "quick" if args.quick else "full"

    # Date range (use recent data)
    end_date = datetime.now()
    start_date = end_date - timedelta(days=args.days)

    # Run grid search
    success_df, failed_df = run_grid_search(
        variant=args.variant,
        param_grid=param_grid,
        start_date=start_date,
        end_date=end_date,
        parallel=not args.no_parallel,
        max_workers=args.workers
    )

    if not success_df.empty:
        # Print top configs
        print_top_configs(success_df, n=5)

        # Export results
        export_results(success_df, args.variant, grid_type)

        # Summary statistics
        print("=" * 80)
        print("GRID SEARCH SUMMARY")
        print("=" * 80)
        print(f"Best Sharpe Ratio: {success_df['sharpe_ratio'].max():.3f}")
        print(f"Best P&L: ${success_df['total_pnl'].max():.2f}")
        print(f"Best Profit Factor: {success_df['profit_factor'].max():.2f}")
        print(f"Best Win Rate: {success_df['win_rate'].max():.1f}%")
        print("=" * 80)
    else:
        print("❌ No successful configurations!")


if __name__ == '__main__':
    main()
