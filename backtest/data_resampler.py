"""
Data Resampling Utilities for Multi-Timeframe Backtesting
==========================================================

Converts 1-minute OHLCV data to higher timeframes (5m, 15m, 1h, 4h).

Key features:
- Proper OHLCV resampling (open=first, high=max, low=min, close=last, volume=sum)
- Aligned to standard timeframe boundaries
- Efficient pandas resampling with proper time alignment

Date: January 30, 2026
"""

import pandas as pd
import numpy as np
from typing import Dict, List
from pathlib import Path


class DataResampler:
    """
    Resamples 1-minute OHLCV data to higher timeframes.
    """

    # Timeframe mapping for pandas resample
    # Note: Using 'min' instead of 'T' for pandas >= 2.2.0 compatibility
    TIMEFRAME_MAP = {
        '1m': '1min',
        '5m': '5min',
        '15m': '15min',
        '30m': '30min',
        '1h': '1h',
        '4h': '4h',
        '1d': '1D'
    }

    @staticmethod
    def resample_ohlcv(df: pd.DataFrame, timeframe: str, label: str = 'right') -> pd.DataFrame:
        """
        Resample 1-minute OHLCV data to a higher timeframe.

        Args:
            df: DataFrame with columns ['time', 'open', 'high', 'low', 'close', 'volume']
            timeframe: Target timeframe ('5m', '15m', '1h', '4h', etc.)
            label: 'right' (default) or 'left' - which edge to label the bar

        Returns:
            Resampled DataFrame with same columns
        """
        if timeframe not in DataResampler.TIMEFRAME_MAP:
            raise ValueError(f"Unknown timeframe: {timeframe}. Valid options: {list(DataResampler.TIMEFRAME_MAP.keys())}")

        # Make a copy and ensure time is datetime
        df = df.copy()
        if not pd.api.types.is_datetime64_any_dtype(df['time']):
            df['time'] = pd.to_datetime(df['time'])

        # Set time as index for resampling
        df = df.set_index('time')

        # Resample using proper OHLCV aggregation
        pandas_rule = DataResampler.TIMEFRAME_MAP[timeframe]

        resampled = df.resample(pandas_rule, label=label).agg({
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last',
            'volume': 'sum'
        })

        # Drop any rows where we don't have data (NaN close means no data in that period)
        resampled = resampled.dropna(subset=['close'])

        # Reset index to make 'time' a column again
        resampled = resampled.reset_index()

        return resampled

    @staticmethod
    def load_and_resample_csv(csv_path: Path, timeframe: str) -> pd.DataFrame:
        """
        Load a 1-minute CSV file and resample it to a higher timeframe.

        Args:
            csv_path: Path to CSV file with 1-minute data
            timeframe: Target timeframe ('5m', '15m', '1h', '4h', etc.)

        Returns:
            Resampled DataFrame
        """
        # Load CSV
        df = pd.read_csv(csv_path, parse_dates=['time'])

        # Validate columns
        required_cols = ['time', 'open', 'high', 'low', 'close', 'volume']
        if not all(col in df.columns for col in required_cols):
            raise ValueError(f"CSV must contain columns: {required_cols}")

        # Resample
        return DataResampler.resample_ohlcv(df, timeframe)

    @staticmethod
    def resample_all_symbols(data_dir: Path, symbols: List[str], timeframe: str) -> Dict[str, pd.DataFrame]:
        """
        Load and resample all symbol CSVs to a target timeframe.

        Args:
            data_dir: Directory containing CSV files (e.g., 'backtest/data/')
            symbols: List of symbols (e.g., ['BTC-USD', 'ETH-USD'])
            timeframe: Target timeframe ('5m', '15m', etc.)

        Returns:
            Dictionary mapping symbol -> resampled DataFrame
        """
        result = {}

        for symbol in symbols:
            # Convert symbol format: BTC-USD -> BTC_USD.csv
            csv_filename = f"{symbol.replace('-', '_')}.csv"
            csv_path = data_dir / csv_filename

            if not csv_path.exists():
                print(f"⚠️  CSV file not found for {symbol}: {csv_path}")
                continue

            try:
                resampled = DataResampler.load_and_resample_csv(csv_path, timeframe)
                result[symbol] = resampled
                print(f"  {symbol}: {len(resampled)} bars ({timeframe})")
            except Exception as e:
                print(f"⚠️  Error resampling {symbol}: {e}")
                continue

        return result


def test_resampling():
    """Quick test of resampling functionality"""
    print("="*60)
    print("DATA RESAMPLER TEST")
    print("="*60)

    # Test with BTC data
    data_dir = Path("backtest/data")
    csv_path = data_dir / "BTC_USD.csv"

    if not csv_path.exists():
        print(f"❌ Test file not found: {csv_path}")
        return

    # Load 1m data
    df_1m = pd.read_csv(csv_path, parse_dates=['time'])
    print(f"\n1m data: {len(df_1m)} bars")
    print(f"Period: {df_1m['time'].min()} to {df_1m['time'].max()}")

    # Resample to 5m
    df_5m = DataResampler.resample_ohlcv(df_1m, '5m')
    print(f"\n5m data: {len(df_5m)} bars")
    print(f"Period: {df_5m['time'].min()} to {df_5m['time'].max()}")
    print("\nFirst 5 bars (5m):")
    print(df_5m.head())

    # Resample to 15m
    df_15m = DataResampler.resample_ohlcv(df_1m, '15m')
    print(f"\n15m data: {len(df_15m)} bars")
    print(f"Period: {df_15m['time'].min()} to {df_15m['time'].max()}")

    # Resample to 1h
    df_1h = DataResampler.resample_ohlcv(df_1m, '1h')
    print(f"\n1h data: {len(df_1h)} bars")

    # Resample to 4h
    df_4h = DataResampler.resample_ohlcv(df_1m, '4h')
    print(f"\n4h data: {len(df_4h)} bars")

    print("\n✅ Resampling test complete")


if __name__ == "__main__":
    test_resampling()
