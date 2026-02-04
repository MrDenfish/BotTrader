"""
Grid Search Optimizer for 4h Hybrid Maker Strategy

Optimizes key parameters to maximize net P&L while maintaining viability criteria.

Key parameters to optimize:
1. Donchian period (donch_len)
2. Viability filter multiplier (vol_min_mult)
3. TP fee multiples (tp1_fee_mult, tp2_fee_mult)
4. Stop multiplier (stop_mult)

Success criteria:
- Net P&L > 0
- Trade frequency: 2-4 trades/month
- Win rate > 50%
- TP1 hit rate > 50%
"""

import itertools
import pandas as pd
from datetime import datetime
from typing import List, Dict
import json

from config_4h_hybrid import Hybrid4hConfig
from engine_4h_hybrid import Hybrid4hBacktestEngine


class Optimizer4hHybrid:
    """Grid search optimizer for 4h hybrid strategy"""

    def __init__(self, symbol: str = "BTC-USD", days: int = 60):
        self.symbol = symbol
        self.days = days
        self.results = []

    def optimize(self, param_grid: Dict[str, List]) -> pd.DataFrame:
        """
        Run grid search over parameter combinations.

        Args:
            param_grid: Dictionary mapping parameter names to lists of values

        Returns:
            DataFrame with results sorted by net P&L
        """

        # Generate all combinations
        param_names = list(param_grid.keys())
        param_values = list(param_grid.values())
        combinations = list(itertools.product(*param_values))

        print("=" * 80)
        print("4H HYBRID STRATEGY GRID SEARCH")
        print("=" * 80)
        print(f"Symbol: {self.symbol}")
        print(f"Period: {self.days} days")
        print(f"Parameters: {param_names}")
        print(f"Total combinations: {len(combinations)}")
        print("=" * 80)
        print()

        # Test each combination
        for idx, values in enumerate(combinations, 1):
            params = dict(zip(param_names, values))

            print(f"[{idx}/{len(combinations)}] Testing: {params}")

            # Create config with these parameters
            config = self._create_config(params)

            # Run backtest
            engine = Hybrid4hBacktestEngine(config)
            stats = engine.run_backtest(self.symbol, self.days)

            # Store results
            result = {
                **params,
                'trades': stats.get('trades', 0),
                'gross_pnl': stats.get('gross_pnl', 0),
                'fees': stats.get('fees', 0),
                'net_pnl': stats.get('net_pnl', 0),
                'win_rate': stats.get('win_rate', 0),
                'profit_factor': stats.get('profit_factor', 0),
                'tp1_hit_pct': stats.get('tp1_hit_pct', 0),
                'tp2_hit_pct': stats.get('tp2_hit_pct', 0),
                'signals': stats.get('signals', 0),
                'entries': stats.get('entries', 0),
                'expired': stats.get('expired', 0),
                # Phase 2: Entry diagnostics
                'retest_fills': stats.get('retest_fills', 0),
                'chase_fills': stats.get('chase_fills', 0),
                'chase_attempts': stats.get('chase_attempts', 0),
                'chase_success_rate': stats.get('chase_success_rate', 0)
            }

            # Calculate trade frequency
            if stats.get('trades', 0) > 0:
                result['trades_per_month'] = (stats['trades'] / self.days) * 30
            else:
                result['trades_per_month'] = 0

            self.results.append(result)

            # Print summary
            print(f"  → Trades: {result['trades']}, "
                  f"Net P&L: ${result['net_pnl']:.2f}, "
                  f"Win Rate: {result['win_rate']:.1%}, "
                  f"TP1 Hit: {result['tp1_hit_pct']:.1f}%")
            print()

        # Convert to DataFrame
        df = pd.DataFrame(self.results)

        # Sort by net P&L descending
        df = df.sort_values('net_pnl', ascending=False)

        return df

    def _create_config(self, params: Dict) -> Hybrid4hConfig:
        """Create config from parameter dictionary"""
        config = Hybrid4hConfig(
            maker_fee=0.004,  # Use closer-to-actual fees
            taker_fee=0.008
        )

        # Apply parameters
        for key, value in params.items():
            if hasattr(config, key):
                setattr(config, key, value)

        return config

    def print_summary(self, df: pd.DataFrame, top_n: int = 10):
        """Print summary of optimization results"""

        print("\n" + "=" * 80)
        print("OPTIMIZATION RESULTS SUMMARY")
        print("=" * 80)
        print()

        # Overall stats
        print(f"Total configurations tested: {len(df)}")
        print(f"Configurations with positive P&L: {(df['net_pnl'] > 0).sum()}")
        print(f"Configurations with trades: {(df['trades'] > 0).sum()}")
        print()

        # Best overall
        print("=" * 80)
        print(f"TOP {top_n} CONFIGURATIONS BY NET P&L")
        print("=" * 80)
        print()

        for idx, (i, row) in enumerate(df.head(top_n).iterrows(), 1):
            print(f"{idx}. Net P&L: ${row['net_pnl']:.2f}")
            print(f"   Params: donch_len={row.get('donch_len', 'N/A')}, "
                  f"vol_min_mult={row.get('vol_min_mult', 'N/A'):.1f}, "
                  f"tp1_fee_mult={row.get('tp1_fee_mult', 'N/A'):.1f}, "
                  f"tp2_fee_mult={row.get('tp2_fee_mult', 'N/A'):.1f}")
            print(f"   Trades: {row['trades']}, "
                  f"Freq: {row['trades_per_month']:.1f}/mo, "
                  f"Win Rate: {row['win_rate']:.1%}, "
                  f"TP1 Hit: {row['tp1_hit_pct']:.1f}%")
            print()

        # Viable configurations (meet success criteria + minimum trades)
        viable = df[
            (df['trades'] >= 20) &  # Phase 2: Minimum 20 trades
            (df['net_pnl'] > 0) &
            (df['trades_per_month'] >= 2) &
            (df['trades_per_month'] <= 6) &  # Phase 2: Allow up to 6/month
            (df['win_rate'] > 0.50) &
            (df['tp1_hit_pct'] > 50)
        ]

        print("=" * 80)
        print(f"VIABLE CONFIGURATIONS (meet all criteria): {len(viable)}")
        print("=" * 80)

        if len(viable) > 0:
            print()
            for idx, (i, row) in enumerate(viable.head(5).iterrows(), 1):
                print(f"{idx}. Net P&L: ${row['net_pnl']:.2f}")
                print(f"   Params: donch_len={row.get('donch_len', 'N/A')}, "
                      f"vol_min_mult={row.get('vol_min_mult', 'N/A'):.1f}, "
                      f"tp1_fee_mult={row.get('tp1_fee_mult', 'N/A'):.1f}, "
                      f"tp2_fee_mult={row.get('tp2_fee_mult', 'N/A'):.1f}")
                print(f"   Trades: {row['trades']}, "
                      f"Freq: {row['trades_per_month']:.1f}/mo, "
                      f"Win Rate: {row['win_rate']:.1%}")
                print()
        else:
            print("\n⚠️  No configurations meet all success criteria")
            print("Consider relaxing constraints or expanding parameter grid")

        print("=" * 80)

    def export_results(self, df: pd.DataFrame, filename: str = None):
        """Export results to CSV"""
        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"optimizer_4h_hybrid_{self.symbol}_{timestamp}.csv"

        df.to_csv(filename, index=False)
        print(f"📊 Results exported: {filename}")


def run_quick_optimization():
    """Run a quick optimization with focused parameter ranges (Phase 2)"""

    optimizer = Optimizer4hHybrid(symbol="BTC-USD", days=60)

    # Phase 2 Parameter grid - focused on productive region
    # Total: 3 * 3 * 2 * 2 * 2 * 2 = 144 combinations
    param_grid = {
        # Focus on productive region from Phase 1
        'donch_len': [8, 10, 12],
        'vol_min_mult': [0.75, 1.0, 1.25],

        # Phase 2: Compression filter (fixed settings for quick test)
        'use_compression_filter': [True, False],
        # 'bb_width_pct_threshold': [20],  # Not varied in quick test

        # Phase 2: Chase entry (simplified for quick test)
        # 'retest_ttl_minutes': [120],  # Not varied in quick test
        'chase_offset': [0.0010, 0.0015],

        # Phase 2: Expanded targets
        'tp1_fee_mult': [3.0, 3.5],
        'tp2_fee_mult': [8, 10],

        # Phase 2: Wider stops
        'stop_mult': [2.5, 3.0],
    }

    # Run optimization
    df_results = optimizer.optimize(param_grid)

    # Print summary
    optimizer.print_summary(df_results, top_n=10)

    # Export
    optimizer.export_results(df_results)

    return df_results


def run_comprehensive_optimization():
    """Run comprehensive optimization with additional parameters"""

    optimizer = Optimizer4hHybrid(symbol="BTC-USD", days=60)

    param_grid = {
        'donch_len': [8, 10, 12, 15, 20, 25, 30],
        'vol_min_mult': [0.5, 1.0, 1.5, 2.0, 2.5, 3.0],
        'tp1_fee_mult': [1.0, 1.5, 2.0, 2.5, 3.0],
        'tp2_fee_mult': [2.0, 3.0, 4.0, 5.0, 6.0],
        'stop_mult': [1.5, 2.0, 2.5, 3.0],
    }

    print(f"⚠️  WARNING: This will test {len(list(itertools.product(*param_grid.values())))} combinations!")
    print("This may take a while...")
    print()

    df_results = optimizer.optimize(param_grid)
    optimizer.print_summary(df_results, top_n=15)
    optimizer.export_results(df_results)

    return df_results


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--comprehensive":
        print("Running COMPREHENSIVE optimization...")
        df = run_comprehensive_optimization()
    else:
        print("Running QUICK optimization...")
        print("(Use --comprehensive for full parameter sweep)")
        print()
        df = run_quick_optimization()

    print("\n✅ Optimization complete!")
