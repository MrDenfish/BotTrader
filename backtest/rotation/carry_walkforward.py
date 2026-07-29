"""Fixed eras, closed two-config menu, and exposure calibration for the
regime-gated carry gauntlet (spec sections 7-8).

There is NO holdout era: forward paper trading is the holdout. The
validate era's contamination (its character was observed during
momentum-rotation testing) is disclosed in the spec.
"""
from __future__ import annotations

import pandas as pd

from backtest.rotation.carry_engine import CarryBacktest, CarryConfig
from backtest.rotation.engine import BacktestResult

FIT_START = pd.Timestamp("2017-01-01", tz="UTC")
FIT_END = pd.Timestamp("2025-01-25", tz="UTC")
VALIDATE_START = pd.Timestamp("2025-01-26", tz="UTC")

CARRY_MENU: list[CarryConfig] = [
    CarryConfig(scheme="equal"),
    CarryConfig(scheme="inverse_vol"),
]

DD_TARGET = (0.12, 0.14)


def carry_eras(index: pd.DatetimeIndex) -> dict[str, tuple[pd.Timestamp, pd.Timestamp]]:
    if index[0] > FIT_START:
        raise ValueError(f"data starts {index[0].date()} — need <= {FIT_START.date()}")
    if index[-1] < VALIDATE_START:
        raise ValueError(f"data ends {index[-1].date()} — need >= {VALIDATE_START.date()}")
    return {"fit": (FIT_START, FIT_END), "validate": (VALIDATE_START, index[-1])}


def calibrate_exposure(
    bars: dict[str, pd.DataFrame],
    cfg: CarryConfig,
    fit: tuple[pd.Timestamp, pd.Timestamp],
    lo: float = 0.02,
    hi: float = 1.0,
    target: tuple[float, float] = DD_TARGET,
    max_iter: int = 10,
) -> tuple[float, BacktestResult]:
    """Bisect exposure so fit-era max DD lands in `target`. Never levers up."""
    import dataclasses

    def run_at(expo: float) -> BacktestResult:
        return CarryBacktest(bars, dataclasses.replace(cfg, exposure=expo)).run(*fit)

    res_hi = run_at(hi)
    if res_hi.max_drawdown < target[0]:
        return hi, res_hi          # already tame at full exposure
    if res_hi.max_drawdown <= target[1]:
        return hi, res_hi          # already in window

    best: tuple[float, BacktestResult] | None = None
    for _ in range(max_iter):
        mid = (lo + hi) / 2.0
        res = run_at(mid)
        if res.max_drawdown > target[1]:
            hi = mid
        else:
            best = (mid, res)      # DD <= upper bound: candidate
            if res.max_drawdown >= target[0]:
                return mid, res    # in window
            lo = mid
    if best is None:
        # nothing under the cap found: return the lowest probe, conservative
        res_lo = run_at(lo)
        return lo, res_lo
    return best
