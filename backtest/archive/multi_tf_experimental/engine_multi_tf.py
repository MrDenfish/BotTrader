"""
Backtest Engine for Multi-Timeframe ROC Strategy
=================================================

Runs backtests on 5m or 15m timeframes using resampled data.

Key features:
- Loads 1m CSV data and resamples to signal timeframe (5m or 15m)
- Calculates indicators on signal timeframe
- Calculates ATR_slow on higher timeframe (1h or 4h)
- Supports continuation and pullback entry modes
- Tracks signal-to-entry conversion rate

Date: January 30, 2026
"""

import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from decimal import Decimal

from backtest.strategy_multi_tf import PeakDrawdownStrategyMultiTF, MultiTFConfig
from backtest.indicators_roc_atr import calculate_roc, calculate_atr, calculate_volume_ratio
from backtest.data_resampler import DataResampler


class BacktestEngineMultiTF:
    """
    Backtest harness for multi-timeframe ROC peak-drawdown strategy.
    """

    def __init__(
        self,
        config: MultiTFConfig,
        symbols: List[str],
        start_date: datetime,
        end_date: datetime,
        data_dir: Path = Path("backtest/data")
    ):
        self.config = config
        self.symbols = symbols
        self.start_date = start_date
        self.end_date = end_date
        self.data_dir = data_dir

        # Data storage
        self.data_signal_tf: Dict[str, pd.DataFrame] = {}  # Signal timeframe data
        self.data_slow_tf: Dict[str, pd.DataFrame] = {}  # Slow ATR timeframe data
        self.indicators: Dict[str, pd.DataFrame] = {}  # All indicators

        # Strategy instance
        self.strategy = PeakDrawdownStrategyMultiTF(config)

        # Logging
        self.log_messages: List[str] = []

    def log(self, message: str):
        """Add a log message"""
        print(message)
        self.log_messages.append(message)

    def load_data(self):
        """
        Load 1-minute CSV data and resample to signal and slow timeframes.
        """
        self.log(f"Loading and resampling data for {len(self.symbols)} symbols...")
        self.log(f"Signal TF: {self.config.signal_timeframe}, Slow ATR TF: {self.config.tf_slow}")
        self.log(f"Period: {self.start_date.date()} to {self.end_date.date()}")

        for symbol in self.symbols:
            # Load 1m data from CSV
            csv_filename = f"{symbol.replace('-', '_')}.csv"
            csv_path = self.data_dir / csv_filename

            if not csv_path.exists():
                self.log(f"⚠️  CSV file not found for {symbol}: {csv_path}")
                continue

            try:
                # Load 1m data
                df_1m = pd.read_csv(csv_path, parse_dates=['time'])

                # Filter by date range
                df_1m = df_1m[
                    (df_1m['time'] >= self.start_date) &
                    (df_1m['time'] <= self.end_date)
                ].reset_index(drop=True)

                if df_1m.empty:
                    self.log(f"⚠️  No data for {symbol} in date range")
                    continue

                # Resample to signal timeframe
                df_signal = DataResampler.resample_ohlcv(df_1m, self.config.signal_timeframe)

                # Resample to slow ATR timeframe
                df_slow = DataResampler.resample_ohlcv(df_1m, self.config.tf_slow)

                self.data_signal_tf[symbol] = df_signal
                self.data_slow_tf[symbol] = df_slow

                self.log(f"  {symbol}: {len(df_signal)} bars ({self.config.signal_timeframe}), "
                         f"{len(df_slow)} bars ({self.config.tf_slow})")

            except Exception as e:
                self.log(f"⚠️  Error loading {symbol}: {e}")
                continue

        if not self.data_signal_tf:
            raise ValueError("No data loaded for any symbol!")

        self.log(f"✅ Data loaded for {len(self.data_signal_tf)} symbols\n")

    def calculate_indicators(self):
        """
        Calculate all indicators for each symbol.

        Indicators calculated on signal timeframe:
        - ROC (with EMA smoothing)
        - Volume ratio
        - ATR_fast

        Indicators calculated on slow timeframe:
        - ATR_slow

        Then merged together into one indicators DataFrame per symbol.
        """
        self.log("Calculating indicators...")

        for symbol in self.data_signal_tf.keys():
            df_signal = self.data_signal_tf[symbol]
            df_slow = self.data_slow_tf[symbol]

            # Calculate on signal timeframe
            roc = calculate_roc(
                df_signal['close'],
                length=self.config.roc_len,
                ema_len=self.config.roc_ema_len
            )

            vol_ratio = calculate_volume_ratio(
                df_signal['volume'],
                sma_len=self.config.vol_sma_len
            )

            atr_fast = calculate_atr(
                df_signal[['high', 'low', 'close']],
                atr_len=self.config.atr_fast_len
            )

            # ATR as % of price
            atr_fast_pct = atr_fast / df_signal['close']

            # Calculate ATR_slow on higher timeframe
            atr_slow = calculate_atr(
                df_slow[['high', 'low', 'close']],
                atr_len=self.config.atr_slow_len
            )

            atr_slow_pct = atr_slow / df_slow['close']

            # Merge slow ATR into signal timeframe using forward fill
            # (each signal bar gets the most recent slow ATR value)
            df_slow_subset = pd.DataFrame({
                'time': df_slow['time'],
                'atr_slow_pct': atr_slow_pct
            })

            df_indicators = pd.DataFrame({
                'time': df_signal['time'],
                'roc': roc,
                'vol_ratio': vol_ratio,
                'atr_fast_pct': atr_fast_pct
            })

            # Merge on time using nearest backward fill (asof merge)
            df_indicators = pd.merge_asof(
                df_indicators.sort_values('time'),
                df_slow_subset.sort_values('time'),
                on='time',
                direction='backward'
            )

            self.indicators[symbol] = df_indicators

            self.log(f"  {symbol}: ROC({self.config.roc_len}), "
                     f"ATR_fast({self.config.atr_fast_len}, {self.config.signal_timeframe}), "
                     f"ATR_slow({self.config.atr_slow_len}, {self.config.tf_slow})")

        self.log("✅ Indicators calculated\n")

    def run_backtest(self):
        """
        Run the backtest across all symbols and bars.
        """
        self.log(f"Running backtest: {self.config.variant_name}")
        self.log(f"Entry mode: {self.config.entry_mode}")
        self.log("="*60)

        # Calculate total bars to process
        total_bars = sum(len(df) for df in self.data_signal_tf.values())
        self.log(f"Total bars to process: {total_bars}")

        # Find earliest and latest times across all symbols
        all_times = []
        for symbol in self.data_signal_tf.keys():
            all_times.extend(self.data_signal_tf[symbol]['time'].tolist())

        unique_times = sorted(set(all_times))

        # Process each timestamp
        bars_processed = 0
        for timestamp in unique_times:
            # Process each symbol at this timestamp
            for symbol in self.data_signal_tf.keys():
                df_signal = self.data_signal_tf[symbol]
                df_indicators = self.indicators[symbol]

                # Find bar for this timestamp
                bar_idx = df_signal[df_signal['time'] == timestamp].index

                if len(bar_idx) == 0:
                    continue  # No bar at this timestamp

                bar_idx = bar_idx[0]
                bar = df_signal.iloc[bar_idx]
                indicators = df_indicators.iloc[bar_idx]

                # Process bar
                self.strategy.process_bar(symbol, bar, indicators)

                bars_processed += 1

            # Progress update
            if bars_processed % 1000 == 0:
                print(f"\r  Processed {bars_processed}/{total_bars} bars...", end='')

        print(f"\r  Processed {total_bars}/{total_bars} bars...")
        self.log("✅ Backtest complete\n")

    def print_results(self):
        """
        Print backtest results summary.
        """
        results = self.strategy.get_results_summary()

        self.log("="*60)
        self.log("BACKTEST RESULTS")
        self.log("="*60)
        self.log(f"Configuration: {self.config.variant_name}")
        self.log(f"Signal Timeframe: {self.config.signal_timeframe}")
        self.log(f"Entry Mode: {self.config.entry_mode}")
        self.log(f"Period: {self.start_date.date()} to {self.end_date.date()}")
        self.log(f"Symbols: {len(self.symbols)}")
        self.log("")
        self.log(f"Total Signals: {results['total_signals']}")
        self.log(f"Total Entries: {results['total_entries']}")
        self.log(f"Signals Expired: {results['signals_expired']}")
        self.log(f"Signal→Entry Rate: {results['signal_to_entry_pct']:.1f}%")
        self.log("")
        self.log(f"Total Trades: {results['total_trades']}")
        self.log(f"Total P&L: ${results['total_pnl']:.2f}")
        self.log(f"Win Rate: {results['win_rate']*100:.1f}%")
        self.log(f"Profit Factor: {results['profit_factor']:.2f}")
        self.log(f"Avg Win: ${results['avg_win']:.2f}")
        self.log(f"Avg Loss: ${results['avg_loss']:.2f}")
        self.log(f"Avg Bars Held: {results['avg_bars_held']:.1f}")
        self.log("")
        self.log(f"Gross P&L: ${results['gross_pnl']:.2f}")
        self.log(f"Total Fees: ${results['total_fees']:.2f}")
        self.log(f"Avg Gross P&L/Trade: ${results['avg_gross_pnl']:.2f}")
        self.log(f"Avg Fees/Trade: ${results['avg_fees']:.2f}")
        self.log("="*60)

        return results


def run_quick_test():
    """
    Quick test of 5m backtest on 7 days.
    """
    print("="*80)
    print("MULTI-TIMEFRAME BACKTEST - 5M QUICK TEST")
    print("="*80)

    # Configuration for 5m
    config = MultiTFConfig(
        variant_name="5M_CONT_TEST",
        signal_timeframe='5m',
        entry_mode='continuation',

        # ROC parameters (scaled up for 5m)
        roc_len=9,
        roc_ema_len=5,
        roc_entry_threshold=0.008,  # 0.8% (higher than 1m's 0.5%)
        roc_arm_threshold=0.004,  # 0.4%

        # Volume filter
        vol_sma_len=20,
        vol_ratio_min=2.0,

        # ATR_fast (on 5m)
        atr_fast_len=14,
        M_fast=2.0,
        fast_trail_activation_k=1.0,

        # ATR_slow (on 1h for 5m signals)
        tf_slow='1h',
        atr_slow_len=14,
        M_slow=3.0,

        # ROC exit
        X_dd=0.40,

        # Entry execution
        entry_max_bars_to_wait=10,
        min_bars_after_entry_for_exit=2,

        # Position sizing
        order_size=Decimal("100.00"),
        fee_rate=Decimal("0.006")
    )

    # Test period: 7 days
    end_date = datetime(2026, 1, 29)
    start_date = end_date - timedelta(days=7)

    # Test symbols
    symbols = ['BTC-USD', 'ETH-USD', 'SOL-USD']

    # Run backtest
    engine = BacktestEngineMultiTF(
        config=config,
        symbols=symbols,
        start_date=start_date,
        end_date=end_date
    )

    engine.load_data()
    engine.calculate_indicators()
    engine.run_backtest()
    results = engine.print_results()

    return results


if __name__ == "__main__":
    run_quick_test()
