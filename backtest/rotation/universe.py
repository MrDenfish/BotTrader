"""Historical universe screens (spec section 3), point-in-time safe.

The spread screen is live-only: historical spreads are unavailable, and
the volume floor + listing age are the binding quality bars (documented
survivorship note in spec section 7).
"""
from __future__ import annotations

import pandas as pd


def eligible_symbols(
    bars: dict[str, pd.DataFrame],
    asof: pd.Timestamp,
    volume_floor: float,
    min_age_days: int = 180,
    top_n: int = 25,
    vol_window: int = 30,
) -> list[str]:
    scored: list[tuple[float, str]] = []
    for sym, df in bars.items():
        hist = df[df.index <= asof]
        if hist.empty:
            continue
        age = (asof - hist.index[0]).days
        if age < min_age_days:
            continue
        window = hist.tail(vol_window)
        dollar_vol = float((window["volume"] * window["close"]).median())
        if dollar_vol < volume_floor:
            continue
        scored.append((dollar_vol, sym))
    scored.sort(reverse=True)
    return [sym for _, sym in scored[:top_n]]
