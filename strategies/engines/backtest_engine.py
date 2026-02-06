"""
Unified Backtest Engine - drives any StrategyPlugin.

Generalized from backtest/engine_4h_hybrid.py to work with any
strategy that implements the StrategyPlugin interface.
"""

import pandas as pd
from pathlib import Path
from datetime import timedelta
from typing import Optional

from strategies.core.base import StrategyPlugin
from strategies.core.engine import StrategyEngine
from strategies.core.types import OHLCV
from strategies.engines import data_providers


class BacktestEngine(StrategyEngine):
    """
    Backtest engine that drives any StrategyPlugin.

    Loads data, computes indicators, iterates bars, and collects results.
    """

    def __init__(
        self,
        strategy: StrategyPlugin,
        config=None,
        data_dir: str = "backtest/data",
        resolution: str = '1m',
    ):
        """
        Initialize backtest engine.

        Args:
            strategy: A StrategyPlugin instance (or class to instantiate)
            config: Strategy-specific config (passed to strategy.configure())
            data_dir: Directory containing CSV data files
            resolution: Base data resolution ('1m' or '5m')
        """
        self._strategy = strategy
        self._config = config
        self.data_dir = data_dir
        self.resolution = resolution
        self._results = {}

        # Timeframe boundary sets
        self._4h_timestamps = set()
        self._1d_timestamps = set()

        if config is not None:
            self._strategy.configure(config)

    def load(self, symbols: list[str], **kwargs) -> None:
        """Load data for symbols (stored for run())."""
        self._symbols = symbols
        self._days = kwargs.get('days', 60)

    def run(self) -> dict:
        """Run backtest for all loaded symbols."""
        self._strategy.start()

        all_results = {}
        for symbol in self._symbols:
            stats = self._run_symbol(symbol, self._days)
            all_results[symbol] = stats

        self._strategy.stop()
        self._results = all_results

        # If single symbol, return flat dict
        if len(all_results) == 1:
            return list(all_results.values())[0]
        return all_results

    def get_strategy(self) -> StrategyPlugin:
        return self._strategy

    def get_results(self) -> dict:
        return self._results

    def _run_symbol(self, symbol: str, days: int) -> dict:
        """Run backtest for a single symbol."""
        print(f"\nBacktesting {symbol} ({self.resolution} base data)...")

        # Load data
        df_base = data_providers.load_data(
            symbol, self.data_dir, days, self.resolution
        )
        if df_base is None:
            return {"error": "Data not found"}

        first_ts = df_base.index[0]
        last_ts = df_base.index[-1]
        print(f"  Data window: {first_ts} to {last_ts} ({len(df_base)} {self.resolution} bars)")

        # Calculate indicators
        df_4h, df_1d, df_aligned, self._4h_timestamps, self._1d_timestamps = \
            data_providers.calculate_indicators(df_base, self._config)

        print(f"  4h bars: {len(df_4h)}")
        print(f"  1D bars: {len(df_1d)}")

        # Run strategy bar-by-bar
        total_bars = len(df_aligned)
        print(f"  Processing {total_bars} {self.resolution} bars...")

        for idx, (ts, row) in enumerate(df_aligned.iterrows()):
            # Create OHLCV bar
            bar = OHLCV(
                timestamp=ts,
                open=row['open'],
                high=row['high'],
                low=row['low'],
                close=row['close'],
                volume=row.get('volume', 0.0),
            )

            # Check timeframe boundaries
            is_4h_close = ts in self._4h_timestamps
            is_1d_close = ts in self._1d_timestamps

            # Build indicators dict
            indicators = {
                '4h': {
                    'atr_pct': row.get('atr_pct_4h', 0),
                    'donch_high_prev': row.get('donch_high_prev_4h', 0),
                    'bb_width_pct': row.get('bb_width_pct_4h', 0),
                    'bb_width': row.get('bb_width_4h', 0),
                    'roc_score_4h': row.get('roc_score_4h', 0),
                },
                '1D': {
                    'ema200': row.get('ema200_1d', 0),
                    'ema50': row.get('ema50_1d', 0),
                    'ema200_slope': row.get('ema200_slope_1d', 0),
                    'ema50_slope': row.get('ema50_slope_1d', 0),
                },
            }

            context = {
                'is_4h_close': is_4h_close,
                'is_1d_close': is_1d_close,
            }

            # Evaluate strategy
            self._strategy.evaluate(symbol, bar, indicators, context)

            # Progress indicator
            if (idx + 1) % 10000 == 0:
                print(f"    Processed {idx + 1}/{total_bars} bars...")

        print(f"  Backtest complete")

        # Get statistics
        stats = self._strategy.get_statistics()
        return stats

    def print_results(self, stats: dict, symbol: str):
        """Print backtest results (same format as old engine)."""
        print("\n" + "=" * 80)
        print(f"BACKTEST RESULTS: {symbol}")
        print("=" * 80)
        print(f"Config: {self._config}")
        print()

        if "error" in stats:
            print(f"Error: {stats['error']}")
            print("=" * 80)
            return

        print(f"Signals: {stats['signals']}")
        print(f"Entries: {stats['entries']}")
        print(f"Signal->Entry: {stats.get('signal_to_entry_pct', 0):.1f}%")
        print(f"Expired: {stats['expired']}")
        print()

        print(f"Trades: {stats['trades']}")
        print(f"Gross P&L: ${stats.get('gross_pnl', 0):.2f}")
        print(f"Fees: ${stats.get('fees', 0):.2f}")
        print(f"Net P&L: ${stats.get('net_pnl', 0):.2f}")
        print()

        if stats['trades'] > 0:
            print(f"Win Rate: {stats.get('win_rate', 0):.1%}")
            print(f"Profit Factor: {stats.get('profit_factor', 0):.2f}")
            print(f"Avg Win: ${stats.get('avg_win', 0):.2f}")
            print(f"Avg Loss: ${stats.get('avg_loss', 0):.2f}")
            print()

            print(f"TP1 Hit: {stats.get('tp1_hit_pct', 0):.1f}%")
            print(f"TP2 Hit: {stats.get('tp2_hit_pct', 0):.1f}%")
            print(f"Avg Hold: {stats.get('avg_bars_held', 0):.1f} bars (1m)")

        print("=" * 80)

        # Print enhanced diagnostics if available
        if 'gate_audit' in stats and stats['gate_audit'].get('total_4h_bars', 0) > 0:
            print(self._strategy.print_enhanced_diagnostics())

    def export_trades(self, filename: str = "trades_plugin.csv"):
        """Export trades to CSV."""
        strategy = self._strategy

        if not hasattr(strategy, 'trades') or not strategy.trades:
            print("No trades to export")
            return

        trades_data = []
        for t in strategy.trades:
            trades_data.append({
                'symbol': t.symbol,
                'entry_time': t.entry_time,
                'entry_price': t.entry_price,
                'exit_time': t.exit_time,
                'qty': t.qty_total,
                'gross_pnl': t.gross_pnl,
                'fees': t.fees_total,
                'net_pnl': t.net_pnl,
                'win': t.net_pnl > 0,
                'stop_hit': t.stop_hit,
                'tp1_filled': t.tp1_filled,
                'tp2_filled': t.tp2_filled,
                'runner_exit': t.runner_exit_reason,
                'mfe_pct': t.mfe_pct * 100,
                'mae_pct': t.mae_pct * 100,
                'bars_held': t.bars_held,
                'setup_type': t.setup_type,
                'entry_mode': t.entry_mode,
                'entry_type': t.entry_type,
                'entry_wait_minutes': t.entry_wait_minutes
            })

        df_trades = pd.DataFrame(trades_data)
        df_trades.to_csv(filename, index=False)
        print(f"Trades exported: {filename}")
