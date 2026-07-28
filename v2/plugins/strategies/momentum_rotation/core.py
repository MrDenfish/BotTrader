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


def select_holdings(
    ranked: list[str], current: list[str], k: int = 4, band: int = 8
) -> list[str]:
    """Top-k selection with a hold band to bound churn (spec section 4)."""
    rank_of = {s: i + 1 for i, s in enumerate(ranked)}
    kept = [s for s in ranked if s in current and rank_of[s] <= band][:k]
    slots = k - len(kept)
    fresh = [s for s in ranked if s not in kept][:slots]
    return sorted(kept + fresh, key=lambda s: rank_of[s])


def inverse_vol_weights(
    vols: dict[str, float | None], cap: float = 0.30
) -> dict[str, float]:
    """Inverse-volatility weights, per-position cap, excess left as cash."""
    clean = {s: v for s, v in vols.items() if v is not None and v > 0}
    if not clean:
        return {}
    raw = {s: 1.0 / v for s, v in clean.items()}

    # Iteratively cap and redistribute among uncapped names.
    capped: set[str] = set()
    while True:
        free = [s for s in raw if s not in capped]
        if not free:
            # everything capped; remainder is cash
            return {s: cap for s in capped}

        budget = 1.0 - cap * len(capped)
        free_raw = sum(raw[s] for s in free)

        # Compute proportional weights; mark any that hit/exceed cap.
        weights = {}
        newly_capped = set()
        for s in free:
            w = budget * raw[s] / free_raw
            if w >= cap:
                weights[s] = cap
                newly_capped.add(s)
            else:
                weights[s] = w

        # If no new caps, we've converged; finalize and return.
        if not newly_capped:
            result = {s: cap for s in capped}
            result.update(weights)
            return result

        # Otherwise, mark new caps and loop.
        capped.update(newly_capped)
