"""Pure rotation math — no I/O, no event bus.

Shared by the backtester (backtest/rotation/) and the future live
momentum_rotation strategy plugin. Everything operates on pandas Series
of daily closes ordered oldest -> newest.

Window convention: the measurement window ends `skip` bars before the
most recent bar (spec section 4: skip the most recent days to avoid
buying immediate spikes).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _window(closes: pd.Series, lookback: int, skip: int) -> pd.Series | None:
    """Return the lookback+1 closes ending `skip` bars before the end."""
    needed = lookback + skip + 1
    if len(closes) < needed:
        return None
    end = len(closes) - skip
    return closes.iloc[end - lookback - 1 : end]


def raw_return(closes: pd.Series, lookback: int, skip: int) -> float | None:
    w = _window(closes, lookback, skip)
    if w is None or w.iloc[0] <= 0:
        return None
    return float(w.iloc[-1] / w.iloc[0] - 1.0)


def daily_vol(closes: pd.Series, lookback: int, skip: int) -> float | None:
    w = _window(closes, lookback, skip)
    if w is None:
        return None
    changes = w.pct_change().dropna()
    if len(changes) < 2:
        return None
    v = float(changes.std(ddof=1))
    return v if np.isfinite(v) else None


def momentum_score(closes: pd.Series, lookback: int, skip: int) -> float | None:
    r = raw_return(closes, lookback, skip)
    v = daily_vol(closes, lookback, skip)
    if r is None or v is None or v == 0.0:
        return None
    return r / v
