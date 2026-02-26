"""Indicator calculations for the composite scoring strategy.

Ported from sighook/indicators.py. Each indicator returns a
(decision, value, threshold) tuple for the last bar of the DataFrame.

The ``compute_indicators`` function runs all indicators on a rolling
buffer DataFrame and returns a dict keyed by indicator name.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from v2.plugins.strategies.composite_scoring.config import CompositeScoreConfig


def _norm(decision: bool | int, value: float, threshold: float) -> tuple[int, float, float]:
    """Normalize to (int_decision, float_value, float_threshold)."""
    return (
        int(bool(decision)),
        float(value if value is not None else 0.0),
        float(threshold if threshold is not None else 0.0),
    )


def compute_indicators(df: pd.DataFrame, cfg: CompositeScoreConfig) -> dict:
    """Compute all indicator signals on *df*, return values for the last row.

    Parameters
    ----------
    df : DataFrame with columns: open, high, low, close, volume.
         Index should be datetime. Must have at least ``cfg.min_bars`` rows.
    cfg : Strategy configuration.

    Returns
    -------
    dict mapping indicator names (``"Buy RSI"``, etc.) to
    ``(decision, value, threshold)`` tuples.  Also includes raw values
    prefixed with ``_`` (``"_RSI"``, ``"_ROC"``, …) for momentum checks.
    """
    n = len(df)
    needed = max(cfg.bb_window, cfg.macd_slow + cfg.macd_signal, cfg.rsi_window + 1, 3)
    if n < needed:
        return {}

    results: dict = {}

    # === 1. BOLLINGER BANDS ===
    basis = df["close"].rolling(window=cfg.bb_window).mean()
    std = df["close"].rolling(window=cfg.bb_window).std()
    upper = basis + cfg.bb_std * std
    lower = basis - cfg.bb_std * std
    band_ratio = (upper / lower).fillna(1.0)

    last = df.iloc[-1]
    last_upper = float(upper.iloc[-1]) if not pd.isna(upper.iloc[-1]) else 0.0
    last_lower = float(lower.iloc[-1]) if not pd.isna(lower.iloc[-1]) else 0.0
    last_basis = float(basis.iloc[-1]) if not pd.isna(basis.iloc[-1]) else 0.0
    last_ratio = float(band_ratio.iloc[-1])

    results["Buy Touch"] = _norm(last["close"] <= last_lower, last["close"], last_lower)
    results["Sell Touch"] = _norm(last["close"] >= last_upper, last["close"], last_upper)
    results["Buy Ratio"] = _norm(last_ratio > cfg.buy_ratio, last_ratio, cfg.buy_ratio)
    results["Sell Ratio"] = _norm(last_ratio < cfg.sell_ratio, last_ratio, cfg.sell_ratio)

    # === 2. MACD ===
    ema_fast = df["close"].ewm(span=cfg.macd_fast, adjust=False).mean()
    ema_slow = df["close"].ewm(span=cfg.macd_slow, adjust=False).mean()
    macd = ema_fast - ema_slow
    signal_line = macd.ewm(span=cfg.macd_signal, adjust=False).mean()
    macd_hist = macd - signal_line

    last_macd = float(macd.iloc[-1])
    last_signal = float(signal_line.iloc[-1])
    last_hist = float(macd_hist.iloc[-1])

    # Crossover check (last 2 bars)
    if n >= 2:
        prev_macd = float(macd.iloc[-2])
        prev_signal = float(signal_line.iloc[-2])
        buy_macd = (prev_macd < prev_signal and last_macd > last_signal and last_macd > 0)
        sell_macd = (prev_macd > prev_signal and last_macd < last_signal and last_macd < 0)
    else:
        buy_macd = False
        sell_macd = False

    results["Buy MACD"] = _norm(buy_macd, last_hist, 0.0)
    results["Sell MACD"] = _norm(sell_macd, last_hist, 0.0)

    # === 3. RSI ===
    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.rolling(window=cfg.rsi_window).mean()
    avg_loss = loss.rolling(window=cfg.rsi_window).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = (100 - (100 / (1 + rs))).clip(0, 100).fillna(50)
    rsi_value = float(rsi.iloc[-1])

    results["Buy RSI"] = _norm(rsi_value < cfg.rsi_buy, rsi_value, cfg.rsi_buy)
    results["Sell RSI"] = _norm(rsi_value > cfg.rsi_sell, rsi_value, cfg.rsi_sell)
    results["_RSI"] = rsi_value

    # === 4. ROC ===
    roc = df["close"].pct_change(periods=cfg.roc_window) * 100
    roc_value = float(roc.iloc[-1]) if not pd.isna(roc.iloc[-1]) else 0.0

    results["Buy ROC"] = _norm(roc_value > cfg.roc_buy_threshold, roc_value, cfg.roc_buy_threshold)
    results["Sell ROC"] = _norm(roc_value < cfg.roc_sell_threshold, roc_value, cfg.roc_sell_threshold)
    results["_ROC"] = roc_value

    # === 5. W-BOTTOM & M-TOP ===
    # Pattern uses bars [i-1, i, i+1].  The *last* row (our scoring target)
    # is always (0, 0, 0) because there is no bar i+1 to confirm the pattern.
    # This matches v1 production behaviour.
    w_bottom = False
    m_top = False
    min_price_change = 0.0

    if n >= 3:
        atr_range = (
            df["high"].rolling(cfg.atr_window).max()
            - df["low"].rolling(cfg.atr_window).min()
        ).fillna(0)
        min_price_change = float(atr_range.median() * 0.065)
        vol_mean = df["volume"].rolling(window=cfg.atr_window, min_periods=1).mean().fillna(0)
        # When volume data is unavailable (WebSocket ticker_batch = all zeros),
        # bypass the volume confirmation to let patterns fire on price alone.
        volume_available = df["volume"].sum() > 0

        # Check pattern at index n-2 (prev=n-3, curr=n-2, nxt=n-1).
        # The pattern is confirmed with a 1-bar delay — acceptable on
        # multi-minute candles and avoids lookahead bias.
        prev_bar, curr_bar, next_bar = df.iloc[-3], df.iloc[-2], df.iloc[-1]
        prev_lower = float(lower.iloc[-3]) if not pd.isna(lower.iloc[-3]) else 0.0
        prev_upper = float(upper.iloc[-3]) if not pd.isna(upper.iloc[-3]) else 0.0
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
        m_top = bool(
            prev_upper > 0
            and prev_bar["high"] > prev_upper
            and curr_bar["high"] < prev_bar["high"]
            and next_bar["high"] < curr_bar["high"]
            and next_bar["close"] < next_basis
            and (not volume_available or next_bar["volume"] > next_vol_mean)
            and abs(curr_bar["high"] - prev_bar["high"]) >= min_price_change
        )

    results["W-Bottom"] = _norm(w_bottom, float(df.iloc[-2]["low"]) if n >= 3 else 0.0, min_price_change)
    results["M-Top"] = _norm(m_top, float(df.iloc[-2]["high"]) if n >= 3 else 0.0, min_price_change)

    # === 6. SWING TRADING ===
    # Use shift(1) to exclude the current bar — otherwise close can never
    # exceed its own rolling max (or fall below its own rolling min).
    rolling_high = df["close"].shift(1).rolling(window=cfg.swing_window).max()
    rolling_low = df["close"].shift(1).rolling(window=cfg.swing_window).min()

    last_rh = float(rolling_high.iloc[-1]) if not pd.isna(rolling_high.iloc[-1]) else 0.0
    last_rl = float(rolling_low.iloc[-1]) if not pd.isna(rolling_low.iloc[-1]) else 0.0

    buy_swing = (last["close"] > last_rh and last_macd > last_signal)
    sell_swing = (last["close"] < last_rl and last_macd < last_signal)

    results["Buy Swing"] = _norm(buy_swing, last["close"], 0.0)
    results["Sell Swing"] = _norm(sell_swing, last["close"], 0.0)

    # === 7. VOLUME DIVERGENCE ===
    # Detect when price and volume trends diverge — a leading reversal signal.
    # Bullish: price falling + volume declining (selling exhaustion).
    # Bearish: price rising + volume declining (buying on fading interest).
    buy_vol_div = False
    sell_vol_div = False
    vol_slope = 0.0

    volume_available = df["volume"].sum() > 0
    div_window = min(cfg.volume_div_window, n)

    if volume_available and div_window >= 3:
        tail = df.iloc[-div_window:]
        x = np.arange(div_window, dtype=float)
        # Linear regression slopes (price and volume)
        price_slope = np.polyfit(x, tail["close"].values, 1)[0]
        vol_slope = np.polyfit(x, tail["volume"].values, 1)[0]

        # Bullish divergence: price declining, volume declining
        buy_vol_div = bool(price_slope < 0 and vol_slope < 0)
        # Bearish divergence: price rising, volume declining
        sell_vol_div = bool(price_slope > 0 and vol_slope < 0)

    results["Buy Volume Div"] = _norm(buy_vol_div, vol_slope, 0.0)
    results["Sell Volume Div"] = _norm(sell_vol_div, vol_slope, 0.0)

    # Relative volume (RVOL) — used by volume confirmation gate in strategy.py
    vol_mean = df["volume"].rolling(window=cfg.atr_window, min_periods=1).mean()
    last_vol_mean = float(vol_mean.iloc[-1]) if not pd.isna(vol_mean.iloc[-1]) else 0.0
    rvol = float(last["volume"]) / last_vol_mean if last_vol_mean > 0 else 0.0
    results["_RVOL"] = rvol

    # === Raw values for diagnostics ===
    results["_upper"] = last_upper
    results["_lower"] = last_lower
    results["_MACD_Hist"] = last_hist

    # === 8. REGIME METRICS ===
    # SMA slope %: 20-bar linear regression on closes, normalized by mean price
    sma_slope_window = min(20, n)
    if sma_slope_window >= 3:
        tail_close = df["close"].iloc[-sma_slope_window:].values
        x = np.arange(sma_slope_window, dtype=float)
        slope = np.polyfit(x, tail_close, 1)[0]
        mean_price = tail_close.mean()
        sma_slope_pct = (slope / mean_price * 100) if mean_price > 0 else 0.0
    else:
        sma_slope_pct = 0.0
    results["_sma_slope_pct"] = sma_slope_pct

    # ATR percentile: rank current 5-bar ATR vs recent history (0-100)
    tr_high_low = df["high"] - df["low"]
    tr_high_close = (df["high"] - df["close"].shift(1)).abs()
    tr_low_close = (df["low"] - df["close"].shift(1)).abs()
    true_range = pd.concat([tr_high_low, tr_high_close, tr_low_close], axis=1).max(axis=1)
    atr_5 = true_range.rolling(window=5, min_periods=1).mean()
    if len(atr_5.dropna()) >= 2:
        current_atr = float(atr_5.iloc[-1])
        lookback = min(cfg.regime_atr_lookback, len(atr_5))
        atr_recent = atr_5.iloc[-lookback:]
        atr_percentile = float((atr_recent <= current_atr).sum() / len(atr_recent) * 100)
    else:
        atr_percentile = 50.0
    results["_atr_percentile"] = atr_percentile

    return results
