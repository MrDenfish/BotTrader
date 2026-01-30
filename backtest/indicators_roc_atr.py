"""
ROC Peak-Drawdown Strategy Indicators
======================================

Implements all technical indicators for the ROC peak-drawdown momentum strategy:
- ROC (Rate of Change) with optional EMA smoothing
- Multi-timeframe ATR (1m and higher-TF)
- ATR% (ATR normalized to price)
- Volume ratio
- Bar aggregation for higher timeframes

Date: January 28, 2026
Spec: /docs/planning/roc_dual_atrpct_strategy_spec.md
"""

import pandas as pd
import numpy as np
from typing import Tuple, Dict


def calculate_roc(close: pd.Series, length: int, ema_len: int = 1) -> pd.Series:
    """
    Calculate Rate of Change (ROC) with optional EMA smoothing.

    Args:
        close: Close prices
        length: Lookback period (e.g., 9 for 9-minute ROC on 1m data)
        ema_len: EMA smoothing length (1 = no smoothing)

    Returns:
        ROC as a fraction (e.g., 0.02 = 2% change)
    """
    # Calculate raw ROC
    roc_raw = close.pct_change(periods=length)

    # Apply EMA smoothing if requested
    if ema_len > 1:
        roc_smoothed = roc_raw.ewm(span=ema_len, adjust=False).mean()
        return roc_smoothed

    return roc_raw


def calculate_true_range(df: pd.DataFrame) -> pd.Series:
    """
    Calculate True Range (TR) for ATR.

    TR = max(high - low, abs(high - prev_close), abs(low - prev_close))

    Args:
        df: DataFrame with 'high', 'low', 'close' columns

    Returns:
        True Range series
    """
    high = df['high']
    low = df['low']
    close_prev = df['close'].shift()

    tr1 = high - low
    tr2 = abs(high - close_prev)
    tr3 = abs(low - close_prev)

    # Max of the three components
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    return tr


def calculate_atr(df: pd.DataFrame, atr_len: int) -> pd.Series:
    """
    Calculate ATR using Wilder's smoothing.

    Args:
        df: DataFrame with OHLC data
        atr_len: ATR period (e.g., 14)

    Returns:
        ATR values
    """
    tr = calculate_true_range(df)

    # Wilder's smoothing (RMA) = EMA with alpha = 1/period
    atr = tr.ewm(alpha=1/atr_len, adjust=False).mean()

    return atr


def calculate_atr_pct(df: pd.DataFrame, atr_len: int) -> pd.Series:
    """
    Calculate ATR as a percentage of price (ATR%).

    This is the key normalization that makes stops adaptive to volatility.

    Args:
        df: DataFrame with OHLC data
        atr_len: ATR period

    Returns:
        ATR% = ATR / close
    """
    atr = calculate_atr(df, atr_len)
    atr_pct = atr / df['close']

    return atr_pct


def aggregate_bars_to_timeframe(df_1m: pd.DataFrame, tf: str) -> pd.DataFrame:
    """
    Aggregate 1-minute bars to higher timeframe (15T or 1H).

    Args:
        df_1m: DataFrame with 1m OHLCV data, indexed by time
        tf: Target timeframe ('15T' or '1H' - pandas freq strings)

    Returns:
        Aggregated OHLC DataFrame
    """
    # Ensure time is the index
    if 'time' in df_1m.columns:
        df_1m = df_1m.set_index('time')

    # Convert to pandas frequency strings (Pandas 3.0+ uses 'min' not 'T')
    tf_map = {
        '15m': '15min', '15T': '15min', '15min': '15min',
        '1h': '1H', '1H': '1H',
        '60m': '60min', '60T': '60min', '60min': '60min'
    }
    tf_normalized = tf_map.get(tf, tf)

    # Resample to target timeframe
    df_agg = df_1m.resample(tf_normalized).agg({
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum'
    }).dropna()

    return df_agg


def calculate_higher_tf_atr_pct(df_1m: pd.DataFrame, tf: str, atr_len: int) -> pd.Series:
    """
    Calculate ATR% on a higher timeframe, then forward-fill to 1m timeline.

    This is critical for ATR_slow - we want stable volatility measure from 15m/1h,
    but need it aligned with 1m execution bars.

    Args:
        df_1m: 1-minute OHLCV DataFrame (time-indexed)
        tf: Higher timeframe ('15T' or '1H' - pandas freq strings)
        atr_len: ATR period for the higher timeframe

    Returns:
        ATR% series aligned to 1m timeline (forward-filled)
    """
    # Ensure time index
    if 'time' in df_1m.columns:
        df_1m = df_1m.set_index('time')

    # Aggregate to higher TF
    df_htf = aggregate_bars_to_timeframe(df_1m, tf)

    # Calculate ATR% on higher TF
    atr_pct_htf = calculate_atr_pct(df_htf, atr_len)

    # Forward-fill to align with 1m timeline
    # Each higher-TF bar's ATR% applies to all 1m bars until next higher-TF bar closes
    atr_pct_1m = atr_pct_htf.reindex(df_1m.index, method='ffill')

    return atr_pct_1m


def calculate_volume_ratio(volume: pd.Series, sma_len: int) -> pd.Series:
    """
    Calculate volume ratio: current volume / SMA(volume).

    Used as entry filter to confirm genuine momentum (not low-volume noise).

    Args:
        volume: Volume series
        sma_len: SMA period (e.g., 20-60)

    Returns:
        Volume ratio (e.g., 2.5 = 2.5x average volume)
    """
    vol_sma = volume.rolling(window=sma_len).mean()
    vol_ratio = volume / vol_sma

    # Handle division by zero
    vol_ratio = vol_ratio.replace([np.inf, -np.inf], np.nan)

    return vol_ratio


def add_all_indicators(
    df: pd.DataFrame,
    roc_len: int,
    roc_ema_len: int,
    atr_fast_len: int,
    atr_slow_len: int,
    tf_slow: str,
    vol_sma_len: int
) -> pd.DataFrame:
    """
    Add all required indicators to a 1m OHLCV DataFrame.

    This is the main function to call for preparing backtest data.

    Args:
        df: 1m OHLCV DataFrame with columns: time, open, high, low, close, volume
        roc_len: ROC lookback period
        roc_ema_len: ROC EMA smoothing length (1 = no smoothing)
        atr_fast_len: ATR period for fast trailing stop (1m data)
        atr_slow_len: ATR period for slow catastrophic stop (higher-TF data)
        tf_slow: Timeframe for slow ATR ('15m' or '1h')
        vol_sma_len: Volume SMA period for ratio calculation

    Returns:
        DataFrame with added columns:
        - roc: Smoothed ROC
        - atr_fast_pct: ATR% from 1m data
        - atr_slow_pct: ATR% from higher-TF data
        - vol_ratio: Volume / SMA(volume)
    """
    # Make a copy to avoid modifying original
    df = df.copy()

    # Ensure time index for resampling
    time_col_exists = 'time' in df.columns
    if time_col_exists:
        df = df.set_index('time')

    # 1. ROC with smoothing
    df['roc'] = calculate_roc(df['close'], length=roc_len, ema_len=roc_ema_len)

    # 2. ATR_fast (1m)
    df['atr_fast_pct'] = calculate_atr_pct(df, atr_fast_len)

    # 3. ATR_slow (higher TF, forward-filled)
    df['atr_slow_pct'] = calculate_higher_tf_atr_pct(df, tf_slow, atr_slow_len)

    # 4. Volume ratio
    df['vol_ratio'] = calculate_volume_ratio(df['volume'], vol_sma_len)

    # Fill NaN values (first rows won't have indicators)
    df['roc'] = df['roc'].fillna(0)
    df['atr_fast_pct'] = df['atr_fast_pct'].fillna(df['atr_fast_pct'].mean())
    df['atr_slow_pct'] = df['atr_slow_pct'].fillna(df['atr_slow_pct'].mean())
    df['vol_ratio'] = df['vol_ratio'].fillna(1.0)

    # Restore time column if it was there
    if time_col_exists:
        df = df.reset_index()

    return df


def calculate_indicators_per_symbol(
    df: pd.DataFrame,
    roc_len: int,
    roc_ema_len: int,
    atr_fast_len: int,
    atr_slow_len: int,
    tf_slow: str,
    vol_sma_len: int
) -> pd.DataFrame:
    """
    Calculate indicators for multi-symbol DataFrame.

    Args:
        df: DataFrame with 'symbol' column + OHLCV data
        Other args: Same as add_all_indicators

    Returns:
        DataFrame with indicators added per symbol
    """
    dfs = []

    for symbol in df['symbol'].unique():
        symbol_df = df[df['symbol'] == symbol].copy()
        symbol_df = symbol_df.sort_values('time')

        # Add indicators for this symbol
        symbol_df = add_all_indicators(
            symbol_df,
            roc_len=roc_len,
            roc_ema_len=roc_ema_len,
            atr_fast_len=atr_fast_len,
            atr_slow_len=atr_slow_len,
            tf_slow=tf_slow,
            vol_sma_len=vol_sma_len
        )

        dfs.append(symbol_df)

    # Combine all symbols
    result = pd.concat(dfs, ignore_index=True)
    return result.sort_values(['time', 'symbol'])


# ============================================================================
# Testing / Verification Functions
# ============================================================================

def test_indicators_on_sample():
    """
    Test indicator calculations on synthetic data.
    Quick sanity check before running full backtest.
    """
    print("Testing indicators on synthetic data...")

    # Create sample 1m data (100 bars)
    np.random.seed(42)
    dates = pd.date_range('2026-01-01', periods=100, freq='1min')

    close = 100 + np.cumsum(np.random.randn(100) * 0.5)  # Random walk
    high = close + np.abs(np.random.randn(100) * 0.3)
    low = close - np.abs(np.random.randn(100) * 0.3)
    open_price = close + np.random.randn(100) * 0.2
    volume = np.random.randint(1000, 10000, 100)

    df = pd.DataFrame({
        'time': dates,
        'open': open_price,
        'high': high,
        'low': low,
        'close': close,
        'volume': volume
    })

    # Add indicators
    df_with_indicators = add_all_indicators(
        df,
        roc_len=9,
        roc_ema_len=5,
        atr_fast_len=14,
        atr_slow_len=14,
        tf_slow='15min',  # pandas frequency: 'min' for minutes, 'H' for hours
        vol_sma_len=20
    )

    # Verify columns exist
    required_cols = ['roc', 'atr_fast_pct', 'atr_slow_pct', 'vol_ratio']
    for col in required_cols:
        assert col in df_with_indicators.columns, f"Missing column: {col}"
        assert not df_with_indicators[col].isna().all(), f"{col} is all NaN"

    print("✅ All indicators calculated successfully")
    print(f"\nSample stats:")
    print(f"  ROC range: {df_with_indicators['roc'].min():.4f} to {df_with_indicators['roc'].max():.4f}")
    print(f"  ATR_fast% range: {df_with_indicators['atr_fast_pct'].min():.4f} to {df_with_indicators['atr_fast_pct'].max():.4f}")
    print(f"  ATR_slow% range: {df_with_indicators['atr_slow_pct'].min():.4f} to {df_with_indicators['atr_slow_pct'].max():.4f}")
    print(f"  Vol ratio range: {df_with_indicators['vol_ratio'].min():.2f} to {df_with_indicators['vol_ratio'].max():.2f}")

    return df_with_indicators


if __name__ == '__main__':
    # Run self-test
    test_indicators_on_sample()
