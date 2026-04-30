"""Indicator calculations for mean_reversion_v3.

Self-contained on purpose — this plugin owns its own indicator math so
future changes to composite_scoring's indicators don't ripple here.

Each indicator returns a (decision, value, threshold) tuple. Raw values
prefixed with ``_`` (e.g. ``_RSI``, ``_ADX``, ``_Supertrend_trend``)
are exposed for gates and diagnostics.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from v2.plugins.strategies.mean_reversion_v3.config import MeanReversionV3Config


def _norm(decision: bool | int, value: float, threshold: float) -> tuple[int, float, float]:
    return (
        int(bool(decision)),
        float(value if value is not None else 0.0),
        float(threshold if threshold is not None else 0.0),
    )


def _compute_atr(df: pd.DataFrame, period: int) -> pd.Series:
    """Average True Range using Wilder smoothing."""
    high = df["high"]
    low = df["low"]
    close = df["close"]
    tr_high_low = high - low
    tr_high_close = (high - close.shift(1)).abs()
    tr_low_close = (low - close.shift(1)).abs()
    tr = pd.concat([tr_high_low, tr_high_close, tr_low_close], axis=1).max(axis=1)
    # Wilder smoothing == EMA with alpha = 1/period
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def _compute_supertrend(df: pd.DataFrame, period: int, multiplier: float) -> tuple[pd.Series, pd.Series]:
    """Compute Supertrend.

    Returns (supertrend_line, trend_direction) where trend_direction is
    +1 for uptrend, -1 for downtrend. The line is the active band.

    Uses NumPy arrays in the hot loops; pandas Series only at the boundary.
    The two passes (final-band ratcheting and direction tracking) are inherently
    sequential because each step depends on the previous step's state.
    """
    n = len(df)
    if n < period + 1:
        return pd.Series([np.nan] * n, index=df.index), pd.Series([0] * n, index=df.index)

    atr = _compute_atr(df, period).to_numpy()
    hl2 = ((df["high"] + df["low"]) / 2.0).to_numpy()
    close = df["close"].to_numpy()

    basic_upper = hl2 + multiplier * atr
    basic_lower = hl2 - multiplier * atr

    final_upper = basic_upper.copy()
    final_lower = basic_lower.copy()

    for i in range(1, n):
        if np.isnan(basic_upper[i]) or np.isnan(basic_upper[i - 1]):
            continue
        # Upper band: keep basic value when narrower OR price broke above prior; else carry prior
        if not (basic_upper[i] < final_upper[i - 1] or close[i - 1] > final_upper[i - 1]):
            final_upper[i] = final_upper[i - 1]
        # Lower band: keep basic value when wider up OR price broke below prior; else carry prior
        if not (basic_lower[i] > final_lower[i - 1] or close[i - 1] < final_lower[i - 1]):
            final_lower[i] = final_lower[i - 1]

    supertrend = np.full(n, np.nan)
    direction = np.zeros(n, dtype=np.int8)

    first_valid = period
    if first_valid < n:
        if close[first_valid] > final_upper[first_valid]:
            supertrend[first_valid] = final_lower[first_valid]
            direction[first_valid] = 1
        else:
            supertrend[first_valid] = final_upper[first_valid]
            direction[first_valid] = -1

    for i in range(first_valid + 1, n):
        if direction[i - 1] == 1:
            if close[i] < final_lower[i]:
                supertrend[i] = final_upper[i]
                direction[i] = -1
            else:
                supertrend[i] = final_lower[i]
                direction[i] = 1
        else:
            if close[i] > final_upper[i]:
                supertrend[i] = final_lower[i]
                direction[i] = 1
            else:
                supertrend[i] = final_upper[i]
                direction[i] = -1

    return (
        pd.Series(supertrend, index=df.index),
        pd.Series(direction, index=df.index),
    )


def _compute_adx(df: pd.DataFrame, period: int) -> float:
    """Compute current ADX value via Wilder smoothing. Returns 0.0 if insufficient data."""
    n = len(df)
    if n < period + 1:
        return 0.0

    high = df["high"].values
    low = df["low"].values
    close = df["close"].values

    plus_dm = np.zeros(n)
    minus_dm = np.zeros(n)
    tr_arr = np.zeros(n)
    for i in range(1, n):
        up_move = high[i] - high[i - 1]
        down_move = low[i - 1] - low[i]
        plus_dm[i] = up_move if (up_move > down_move and up_move > 0) else 0.0
        minus_dm[i] = down_move if (down_move > up_move and down_move > 0) else 0.0
        tr_arr[i] = max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i] - close[i - 1]),
        )

    alpha = 1.0 / period
    smooth_plus = np.zeros(n)
    smooth_minus = np.zeros(n)
    smooth_tr = np.zeros(n)
    smooth_plus[period] = plus_dm[1:period + 1].sum()
    smooth_minus[period] = minus_dm[1:period + 1].sum()
    smooth_tr[period] = tr_arr[1:period + 1].sum()
    for i in range(period + 1, n):
        smooth_plus[i] = smooth_plus[i - 1] - smooth_plus[i - 1] * alpha + plus_dm[i]
        smooth_minus[i] = smooth_minus[i - 1] - smooth_minus[i - 1] * alpha + minus_dm[i]
        smooth_tr[i] = smooth_tr[i - 1] - smooth_tr[i - 1] * alpha + tr_arr[i]

    dx_arr = np.zeros(n)
    for i in range(period, n):
        if smooth_tr[i] > 0:
            plus_di = smooth_plus[i] / smooth_tr[i] * 100
            minus_di = smooth_minus[i] / smooth_tr[i] * 100
            di_sum = plus_di + minus_di
            dx_arr[i] = abs(plus_di - minus_di) / di_sum * 100 if di_sum > 0 else 0.0

    adx_arr = np.zeros(n)
    start = 2 * period
    if start >= n:
        return 0.0
    adx_arr[start] = dx_arr[period:start + 1].mean()
    for i in range(start + 1, n):
        adx_arr[i] = (adx_arr[i - 1] * (period - 1) + dx_arr[i]) / period
    return float(adx_arr[-1])


def compute_indicators(df: pd.DataFrame, cfg: MeanReversionV3Config) -> dict:
    """Compute all v3 indicator signals on *df*, return last-bar values."""
    n = len(df)
    needed = max(cfg.bb_window, cfg.macd_slow + cfg.macd_signal, cfg.rsi_window + 1, 3)
    if n < needed:
        return {}

    results: dict = {}

    # === Bollinger Bands (Touch only — Ratio dropped) ===
    basis = df["close"].rolling(window=cfg.bb_window).mean()
    std = df["close"].rolling(window=cfg.bb_window).std()
    upper = basis + cfg.bb_std * std
    lower = basis - cfg.bb_std * std

    last = df.iloc[-1]
    last_upper = float(upper.iloc[-1]) if not pd.isna(upper.iloc[-1]) else 0.0
    last_lower = float(lower.iloc[-1]) if not pd.isna(lower.iloc[-1]) else 0.0
    results["Buy Touch"] = _norm(last["close"] <= last_lower, last["close"], last_lower)

    # === MACD bullish crossover ===
    ema_fast = df["close"].ewm(span=cfg.macd_fast, adjust=False).mean()
    ema_slow = df["close"].ewm(span=cfg.macd_slow, adjust=False).mean()
    macd = ema_fast - ema_slow
    signal_line = macd.ewm(span=cfg.macd_signal, adjust=False).mean()
    macd_hist = macd - signal_line
    last_hist = float(macd_hist.iloc[-1])
    if n >= 2:
        prev_macd = float(macd.iloc[-2])
        prev_signal = float(signal_line.iloc[-2])
        last_macd = float(macd.iloc[-1])
        last_signal = float(signal_line.iloc[-1])
        buy_macd = (prev_macd < prev_signal and last_macd > last_signal and last_macd > 0)
    else:
        buy_macd = False
    results["Buy MACD"] = _norm(buy_macd, last_hist, 0.0)

    # === RSI ===
    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.rolling(window=cfg.rsi_window).mean()
    avg_loss = loss.rolling(window=cfg.rsi_window).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = (100 - (100 / (1 + rs))).clip(0, 100).fillna(50)
    rsi_value = float(rsi.iloc[-1])
    results["Buy RSI"] = _norm(rsi_value < cfg.rsi_buy, rsi_value, cfg.rsi_buy)
    results["_RSI"] = rsi_value

    # === ROC ===
    roc = df["close"].pct_change(periods=cfg.roc_window) * 100
    roc_value = float(roc.iloc[-1]) if not pd.isna(roc.iloc[-1]) else 0.0
    results["Buy ROC"] = _norm(roc_value > cfg.roc_buy_threshold, roc_value, cfg.roc_buy_threshold)
    results["_ROC"] = roc_value

    # === W-Bottom (3-bar pattern, 1-bar lag) ===
    w_bottom = False
    min_price_change = 0.0
    if n >= 3:
        atr_range = (
            df["high"].rolling(cfg.atr_window).max()
            - df["low"].rolling(cfg.atr_window).min()
        ).fillna(0)
        min_price_change = float(atr_range.median() * 0.065)
        vol_mean = df["volume"].rolling(window=cfg.atr_window, min_periods=1).mean().fillna(0)
        volume_available = df["volume"].sum() > 0
        prev_bar, curr_bar, next_bar = df.iloc[-3], df.iloc[-2], df.iloc[-1]
        prev_lower = float(lower.iloc[-3]) if not pd.isna(lower.iloc[-3]) else 0.0
        next_basis = float(basis.iloc[-1]) if not pd.isna(basis.iloc[-1]) else 0.0
        next_vol_mean = float(vol_mean.iloc[-1])
        w_bottom = bool(
            prev_lower > 0
            and prev_bar["low"] < prev_lower
            and curr_bar["low"] > prev_bar["low"]
            and next_bar["low"] > curr_bar["low"]
            and next_bar["close"] > next_basis
            and (not volume_available or next_bar["volume"] > next_vol_mean)
            and abs(curr_bar["low"] - prev_bar["low"]) >= min_price_change
        )
    results["W-Bottom"] = _norm(w_bottom, float(df.iloc[-2]["low"]) if n >= 3 else 0.0, min_price_change)

    # === Volume Divergence (bullish exhaustion) ===
    buy_vol_div = False
    vol_slope = 0.0
    volume_available = df["volume"].sum() > 0
    div_window = min(cfg.volume_div_window, n)
    if volume_available and div_window >= 3:
        tail = df.iloc[-div_window:]
        x = np.arange(div_window, dtype=float)
        price_slope = np.polyfit(x, tail["close"].values, 1)[0]
        vol_slope = np.polyfit(x, tail["volume"].values, 1)[0]
        buy_vol_div = bool(price_slope < 0 and vol_slope < 0)
    results["Buy Volume Div"] = _norm(buy_vol_div, vol_slope, 0.0)

    # === RVOL (used by volume confirmation gate) ===
    vol_mean = df["volume"].rolling(window=cfg.atr_window, min_periods=1).mean()
    last_vol_mean = float(vol_mean.iloc[-1]) if not pd.isna(vol_mean.iloc[-1]) else 0.0
    rvol = float(last["volume"]) / last_vol_mean if last_vol_mean > 0 else 0.0
    results["_RVOL"] = rvol

    # === Supertrend (Stage 1 regime filter) ===
    st_line, st_dir = _compute_supertrend(df, cfg.supertrend_period, cfg.supertrend_multiplier)
    st_direction = int(st_dir.iloc[-1]) if not pd.isna(st_dir.iloc[-1]) else 0
    st_value = float(st_line.iloc[-1]) if not pd.isna(st_line.iloc[-1]) else 0.0
    results["_Supertrend_dir"] = st_direction  # +1 uptrend, -1 downtrend, 0 unknown
    results["_Supertrend_line"] = st_value

    # === ADX (Stage 1 regime filter) ===
    results["_ADX"] = _compute_adx(df, cfg.adx_period)

    # === Diagnostics ===
    results["_upper"] = last_upper
    results["_lower"] = last_lower
    results["_MACD_Hist"] = last_hist

    return results
