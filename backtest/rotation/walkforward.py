"""Era-based walk-forward protocol and pre-declared pass bar (spec section 7)."""
from __future__ import annotations

from itertools import product

import pandas as pd

from backtest.rotation.engine import BacktestResult, RotationBacktest, RotationConfig

HOLDOUT_DAYS = 547     # ~18 months
VALIDATE_DAYS = 547
MIN_FIT_DAYS = 365
MAX_DD = 0.15

PARAM_MENU: list[RotationConfig] = [
    RotationConfig(lookback=lb, skip=sk, band=bd, volume_floor=fl)
    for lb, sk, bd, fl in product((30, 60, 90), (2, 3), (6, 8), (5e6, 10e6))
]


def era_bounds(index: pd.DatetimeIndex) -> dict[str, tuple[pd.Timestamp, pd.Timestamp]]:
    # RotationBacktest.run() slices `.loc[start:end]` inclusive on both ends,
    # so a start built as `end - Timedelta(days=N)` realizes N+1 bars. The
    # holdout era's start therefore needs the same -1-day compensation the
    # fit/validate boundary already applies via `validate_start - one`, so
    # that holdout also realizes exactly HOLDOUT_DAYS (547) bars, not 548.
    end = index[-1]
    holdout_start = end - pd.Timedelta(days=HOLDOUT_DAYS - 1)
    validate_start = holdout_start - pd.Timedelta(days=VALIDATE_DAYS)
    fit_days = (validate_start - index[0]).days
    if fit_days < MIN_FIT_DAYS:
        raise ValueError(
            f"fit era would be {fit_days}d (<{MIN_FIT_DAYS}d) — need more history"
        )
    one = pd.Timedelta(days=1)
    return {
        "fit": (index[0], validate_start - one),
        "validate": (validate_start, holdout_start - one),
        "holdout": (holdout_start, end),
    }


def run_walkforward(
    bars: dict[str, pd.DataFrame], btc_symbol: str, cfg: RotationConfig,
    eras: dict[str, tuple[pd.Timestamp, pd.Timestamp]],
) -> dict[str, BacktestResult]:
    bt = RotationBacktest(bars, btc_symbol, cfg)
    return {name: bt.run(start, end) for name, (start, end) in eras.items()}


def passes_bar(era_results: dict[str, BacktestResult]) -> dict:
    reasons: list[str] = []
    for era, res in era_results.items():
        if res.net_return <= 0:
            reasons.append(f"{era}: net_return {res.net_return:+.2%} <= 0")
        if res.max_drawdown > MAX_DD:
            reasons.append(f"{era}: max_drawdown {res.max_drawdown:.2%} > {MAX_DD:.0%}")
    return {"pass": not reasons, "reasons": reasons}
