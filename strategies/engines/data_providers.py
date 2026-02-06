"""
Data providers for the strategy engine.

Handles CSV loading and data resampling. Generalized from
backtest/engine_4h_hybrid.py and backtest/data_resampler.py.
"""

from pathlib import Path
from datetime import timedelta
from typing import Optional

import pandas as pd
import numpy as np

from backtest.data_resampler import DataResampler


def load_data(
    symbol: str,
    data_dir: str = "backtest/data",
    days: int = 60,
    resolution: str = '1m',
) -> Optional[pd.DataFrame]:
    """
    Load OHLCV data at specified resolution.

    Args:
        symbol: Trading pair (e.g., 'BTC-USD')
        data_dir: Directory containing CSV data files
        days: Number of days to load
        resolution: '1m' or '5m'

    Returns:
        DataFrame with OHLCV data, or None if not found.
    """
    data_path = Path(data_dir)
    symbol_underscore = symbol.replace('-', '_')
    csv_paths = [
        data_path / f"{symbol_underscore}_{resolution}.csv",
        data_path / f"{symbol}_{resolution}.csv",
        data_path / f"{symbol_underscore}.csv",
    ]

    csv_path = None
    for path in csv_paths:
        if path.exists():
            csv_path = path
            break

    if csv_path is None:
        print(f"  {symbol}: CSV not found (tried {csv_paths})")
        return None

    df = pd.read_csv(csv_path)

    time_col = 'time' if 'time' in df.columns else 'timestamp'
    df[time_col] = pd.to_datetime(df[time_col])
    df = df.set_index(time_col)
    df = df.sort_index()
    df.index.name = 'timestamp'

    end_date = df.index[-1]
    start_date = end_date - timedelta(days=days)
    df = df[df.index >= start_date]

    print(f"  {symbol}: {len(df)} bars ({resolution})")

    return df


def calculate_indicators(
    df_base: pd.DataFrame,
    config,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, set, set]:
    """
    Resample to 4h and 1D, calculate indicators, align into base timeframe.

    Args:
        df_base: Base timeframe data (1m or 5m)
        config: Strategy config (Hybrid4hConfig) for indicator parameters

    Returns:
        (df_4h, df_1d, df_aligned, _4h_timestamps, _1d_timestamps)
    """
    # Resample
    df_4h = DataResampler.resample_ohlcv(df_base, '4h', label='right')
    df_1d = DataResampler.resample_ohlcv(df_base, '1d', label='right')

    _4h_timestamps = set(df_4h.index)
    _1d_timestamps = set(df_1d.index)

    # Calculate 4h indicators
    df_4h = _calculate_4h_indicators(df_4h, config)

    # Calculate 1D indicators
    df_1d = _calculate_1d_indicators(df_1d, config)

    # Align into base timeframe
    df_aligned = _align_indicators(df_base, df_4h, df_1d)

    return df_4h, df_1d, df_aligned, _4h_timestamps, _1d_timestamps


def _calculate_4h_indicators(df: pd.DataFrame, config) -> pd.DataFrame:
    """Calculate 4h indicators: ATR%, Donchian, Bollinger Bands, ROC-score."""
    atr_len = config.atr_len_4h
    df['tr'] = _calculate_true_range(df)
    df['atr'] = df['tr'].ewm(span=atr_len, adjust=False).mean()
    df['atr_pct'] = df['atr'] / df['close']

    donch_len = config.donch_len
    df['donch_high_prev'] = df['high'].shift(1).rolling(donch_len).max()

    bb_len = config.bb_len
    bb_k = config.bb_k

    df['bb_mid'] = df['close'].rolling(bb_len).mean()
    df['bb_std'] = df['close'].rolling(bb_len).std()
    df['bb_upper'] = df['bb_mid'] + bb_k * df['bb_std']
    df['bb_lower'] = df['bb_mid'] - bb_k * df['bb_std']

    df['bb_width'] = (df['bb_upper'] - df['bb_lower']) / df['bb_mid']

    bb_width_window = config.bb_width_window
    df['bb_width_pct'] = df['bb_width'].rolling(bb_width_window).apply(
        lambda x: (x <= x.iloc[-1]).sum() / len(x) * 100 if len(x) > 0 else 0,
        raw=False
    )

    roc_len = config.roc_len_4h
    roc_raw = df['close'].pct_change(roc_len)

    if config.roc_ema_len > 1:
        df['roc_4h'] = roc_raw.ewm(span=config.roc_ema_len, adjust=False).mean()
    else:
        df['roc_4h'] = roc_raw

    df['roc_score_4h'] = df['roc_4h'] / df['atr_pct'].replace(0, np.nan)

    return df


def _calculate_1d_indicators(df: pd.DataFrame, config) -> pd.DataFrame:
    """Calculate 1D indicators: EMA200, EMA50."""
    ema200_len = config.ema200_len_1d
    ema50_len = config.ema50_len_1d

    df['ema200'] = df['close'].ewm(span=ema200_len, adjust=False).mean()
    df['ema50'] = df['close'].ewm(span=ema50_len, adjust=False).mean()

    lookback = config.ema_slope_lookback_days
    df['ema200_slope'] = df['ema200'] - df['ema200'].shift(lookback)
    df['ema50_slope'] = df['ema50'] - df['ema50'].shift(lookback)

    return df


def _calculate_true_range(df: pd.DataFrame) -> pd.Series:
    """Calculate true range."""
    high_low = df['high'] - df['low']
    high_prev_close = abs(df['high'] - df['close'].shift(1))
    low_prev_close = abs(df['low'] - df['close'].shift(1))

    tr = pd.concat([high_low, high_prev_close, low_prev_close], axis=1).max(axis=1)
    return tr


def _align_indicators(
    df_base: pd.DataFrame,
    df_4h: pd.DataFrame,
    df_1d: pd.DataFrame
) -> pd.DataFrame:
    """Merge 4h and 1D indicator values into base timeframe bars (forward fill)."""
    df_4h_ind = df_4h[['atr_pct', 'donch_high_prev', 'bb_width_pct', 'bb_width', 'roc_score_4h']].copy()
    df_4h_ind.columns = ['atr_pct_4h', 'donch_high_prev_4h', 'bb_width_pct_4h', 'bb_width_4h', 'roc_score_4h']

    df_1d_ind = df_1d[['ema200', 'ema50', 'ema200_slope', 'ema50_slope']].copy()
    df_1d_ind.columns = ['ema200_1d', 'ema50_1d', 'ema200_slope_1d', 'ema50_slope_1d']

    df_aligned = df_base.copy()
    df_aligned = pd.merge_asof(df_aligned, df_4h_ind, left_index=True, right_index=True, direction='backward')
    df_aligned = pd.merge_asof(df_aligned, df_1d_ind, left_index=True, right_index=True, direction='backward')

    return df_aligned
