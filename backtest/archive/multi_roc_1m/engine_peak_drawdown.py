"""
ROC Peak-Drawdown Backtest Engine
==================================

Integrates indicators, strategy logic, and data loading for backtesting.

Flow:
1. Load 1m OHLCV data from CSV files (backtest/data/*.csv)
2. Calculate all indicators (ROC, ATR_fast, ATR_slow, volume ratio)
3. Run strategy state machine bar-by-bar
4. Generate trade list and performance metrics

Date: January 28, 2026
"""

import sys
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import List, Dict, Tuple
from decimal import Decimal
import sqlalchemy as sa

from backtest.indicators_roc_atr import add_all_indicators
from backtest.strategy_peak_drawdown import (
    PeakDrawdownConfig,
    PeakDrawdownStrategy,
    TradeResult
)


class PeakDrawdownBacktestEngine:
    """
    Backtest engine for ROC peak-drawdown strategy.

    Handles data loading, indicator calculation, and strategy execution.
    """

    def __init__(
        self,
        config: PeakDrawdownConfig,
        start_date: datetime,
        end_date: datetime,
        symbols: List[str],
        db_url: str,
        verbose: bool = True
    ):
        """
        Initialize backtest engine.

        Args:
            config: Strategy configuration
            start_date: Backtest start date
            end_date: Backtest end date
            symbols: List of symbols to trade
            db_url: PostgreSQL database URL
            verbose: Print progress messages
        """
        self.config = config
        self.start_date = start_date
        self.end_date = end_date
        self.symbols = symbols
        self.db_url = db_url
        self.verbose = verbose

        # Strategy instance
        self.strategy = PeakDrawdownStrategy(config)

        # Data cache
        self.data: Dict[str, pd.DataFrame] = {}

    def log(self, message: str):
        """Print message if verbose"""
        if self.verbose:
            print(message)

    def load_data(self):
        """
        Load 1-minute OHLCV data from CSV files for all symbols.

        Stores data in self.data[symbol] as DataFrames.
        """
        self.log(f"Loading 1m data for {len(self.symbols)} symbols...")
        self.log(f"Period: {self.start_date.date()} to {self.end_date.date()}")

        from pathlib import Path
        data_dir = Path("backtest/data")

        for symbol in self.symbols:
            # Convert symbol format: BTC-USD -> BTC_USD.csv
            csv_filename = f"{symbol.replace('-', '_')}.csv"
            csv_path = data_dir / csv_filename

            if not csv_path.exists():
                self.log(f"⚠️  CSV file not found for {symbol}: {csv_path}")
                continue

            # Load CSV file
            df = pd.read_csv(csv_path, parse_dates=['time'])

            # Filter by date range
            df = df[
                (df['time'] >= self.start_date) &
                (df['time'] <= self.end_date)
            ].reset_index(drop=True)

            if df.empty:
                self.log(f"⚠️  No data for {symbol} in date range")
                continue

            # Ensure correct column order
            df = df[['time', 'open', 'high', 'low', 'close', 'volume']]

            self.log(f"  {symbol}: {len(df)} bars")
            self.data[symbol] = df

        if not self.data:
            raise ValueError("No data loaded for any symbol!")

        self.log(f"✅ Data loaded for {len(self.data)} symbols\n")

    def calculate_indicators(self):
        """
        Calculate all indicators for each symbol.

        Adds columns: roc, atr_fast_pct, atr_slow_pct, vol_ratio
        """
        self.log("Calculating indicators...")

        for symbol, df in self.data.items():
            self.log(f"  {symbol}: ROC({self.config.roc_len}), "
                     f"ATR_fast({self.config.atr_fast_len}), "
                     f"ATR_slow({self.config.tf_slow}/{self.config.atr_slow_len})")

            # Add all indicators
            df_with_indicators = add_all_indicators(
                df,
                roc_len=self.config.roc_len,
                roc_ema_len=self.config.roc_ema_len,
                atr_fast_len=self.config.atr_fast_len,
                atr_slow_len=self.config.atr_slow_len,
                tf_slow=self.config.tf_slow,
                vol_sma_len=self.config.vol_sma_len
            )

            self.data[symbol] = df_with_indicators

        self.log("✅ Indicators calculated\n")

    def run_backtest(self):
        """
        Execute strategy bar-by-bar across all symbols.

        Processes bars in chronological order across all symbols.
        """
        self.log(f"Running backtest: {self.config.variant_name}")
        self.log("=" * 60)

        # Combine all symbol data with symbol column
        all_data = []
        for symbol, df in self.data.items():
            df_copy = df.copy()
            df_copy['symbol'] = symbol
            all_data.append(df_copy)

        combined_df = pd.concat(all_data, ignore_index=True)
        combined_df = combined_df.sort_values('time').reset_index(drop=True)

        self.log(f"Total bars to process: {len(combined_df)}")

        # Process each bar
        for idx, row in combined_df.iterrows():
            symbol = row['symbol']

            # Extract bar and indicators as Series
            bar = row[['time', 'open', 'high', 'low', 'close', 'volume']]
            indicators = row[['roc', 'atr_fast_pct', 'atr_slow_pct', 'vol_ratio']]

            # Process bar through strategy
            self.strategy.process_bar(symbol, bar, indicators)

            # Progress update every 10000 bars
            if self.verbose and (idx + 1) % 10000 == 0:
                print(f"  Processed {idx + 1}/{len(combined_df)} bars...", end='\r')

        if self.verbose:
            print()  # New line after progress

        self.log(f"✅ Backtest complete\n")

    def generate_results(self) -> Dict:
        """
        Generate comprehensive backtest results.

        Returns:
            Dictionary with trades, metrics, and analysis
        """
        trades = self.strategy.trades
        stats = self.strategy.get_summary_stats()

        self.log("=" * 60)
        self.log("BACKTEST RESULTS")
        self.log("=" * 60)
        self.log(f"Configuration: {self.config.variant_name}")
        self.log(f"Period: {self.start_date.date()} to {self.end_date.date()}")
        self.log(f"Symbols: {len(self.symbols)}")
        self.log("")
        self.log(f"Total Trades: {stats['total_trades']}")
        self.log(f"Total P&L: ${stats['total_pnl']:.2f}")
        self.log(f"Win Rate: {stats['win_rate']:.1f}%")
        self.log(f"Profit Factor: {stats['profit_factor']:.2f}")
        self.log(f"Avg Win: ${stats['avg_win']:.2f}")
        self.log(f"Avg Loss: ${stats['avg_loss']:.2f}")
        self.log(f"Avg Bars Held: {stats['avg_bars_held']:.1f}")
        self.log("")
        self.log("Exit Reason Distribution:")
        for reason, count in stats['exit_reasons'].items():
            pct = 100 * count / stats['total_trades']
            self.log(f"  {reason}: {count} ({pct:.1f}%)")
        self.log("=" * 60)

        return {
            'config': self.config,
            'trades': trades,
            'stats': stats,
            'start_date': self.start_date,
            'end_date': self.end_date,
            'symbols': self.symbols
        }

    def run(self) -> Dict:
        """
        Execute full backtest pipeline.

        Returns:
            Dictionary with results
        """
        self.load_data()
        self.calculate_indicators()
        self.run_backtest()
        results = self.generate_results()

        return results

    def export_trades_to_csv(self, filename: str):
        """
        Export trade list to CSV for analysis.

        Args:
            filename: Output CSV path
        """
        trades = self.strategy.trades

        if not trades:
            self.log("No trades to export")
            return

        # Convert trades to DataFrame
        trade_dicts = []
        for trade in trades:
            trade_dicts.append({
                'symbol': trade.symbol,
                'entry_time': trade.entry_time,
                'exit_time': trade.exit_time,
                'entry_price': float(trade.entry_price),
                'exit_price': float(trade.exit_price),
                'size': float(trade.size),
                'exit_reason': trade.exit_reason,
                'bars_held': trade.bars_held,
                'gross_pnl': float(trade.gross_pnl),
                'fees': float(trade.fees),
                'net_pnl': float(trade.net_pnl),
                'pnl_pct': trade.pnl_pct,
                'entry_roc': trade.entry_roc,
                'entry_atr_slow_pct': trade.entry_atr_slow_pct,
                'entry_atr_fast_pct': trade.entry_atr_fast_pct,
                'entry_vol_ratio': trade.entry_vol_ratio,
                'exit_roc': trade.exit_roc,
                'roc_peak': trade.roc_peak,
                'dd_at_exit': trade.dd_at_exit,
                'stop_slow_at_exit': float(trade.stop_slow_at_exit),
                'stop_fast_at_exit': float(trade.stop_fast_at_exit) if trade.stop_fast_at_exit != Decimal('-Infinity') else None,
                'armed_roc_dd': trade.armed_roc_dd,
                'armed_fast_trail': trade.armed_fast_trail,
                'mae_pct': trade.mae_pct,
                'mfe_pct': trade.mfe_pct
            })

        df = pd.DataFrame(trade_dicts)
        df.to_csv(filename, index=False)

        self.log(f"📊 Trades exported to: {filename}")


# ============================================================================
# Helper Functions
# ============================================================================

def get_default_symbols() -> List[str]:
    """
    Get default symbol list for backtesting.

    Returns symbols with reliable 1m data and sufficient liquidity.
    """
    return [
        'BTC-USD',
        'ETH-USD',
        'SOL-USD',
        'XRP-USD',
        'ADA-USD',
        'AVAX-USD',
        'DOGE-USD',
        'MATIC-USD',
        'LINK-USD',
        'UNI-USD'
    ]


def run_sample_backtest(
    config: PeakDrawdownConfig,
    days: int = 7,
    db_url: str = "postgresql://bot_user:***REDACTED***@localhost:5433/bot_trader_db"
):
    """
    Run a quick sample backtest for testing.

    Args:
        config: Strategy configuration
        days: Number of days to backtest
        db_url: Database connection string
    """
    end_date = datetime(2026, 1, 27)
    start_date = end_date - timedelta(days=days)

    symbols = get_default_symbols()

    engine = PeakDrawdownBacktestEngine(
        config=config,
        start_date=start_date,
        end_date=end_date,
        symbols=symbols,
        db_url=db_url,
        verbose=True
    )

    results = engine.run()

    # Export trades
    filename = f"backtest_results/{config.variant_name}_{days}d_sample.csv"
    engine.export_trades_to_csv(filename)

    return results


if __name__ == '__main__':
    # Quick test
    from backtest.strategy_peak_drawdown import get_variant_a_config

    print("=" * 80)
    print("PEAK-DRAWDOWN BACKTEST ENGINE TEST")
    print("=" * 80)
    print()

    # Test with Variant A, 7-day sample
    config = get_variant_a_config()
    results = run_sample_backtest(config, days=7)

    print()
    print("✅ Engine test complete")
