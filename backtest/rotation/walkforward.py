"""Era-based walk-forward protocol and pre-declared pass bar (spec section 7)."""
from __future__ import annotations

import logging
from itertools import product

import pandas as pd

from backtest.rotation.engine import BacktestResult, RotationBacktest, RotationConfig

logger = logging.getLogger(__name__)

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


def choose_fit_winner(fit_rows: list[dict]) -> dict | None:
    """Select the fit-era winner, applying the spec section 12 floor rule.

    fit_rows: [{"cfg": {...}, "net": float, "dd": float}, ...].

    Base rule: best net return among DD-eligible configs.

    Floor rule (spec section 12 pre-registered sensitivity check): the two
    volume_floor values (5e6 / 10e6) are a sensitivity pair. If the best
    config's sibling (identical lookback/skip/band, the *other* floor) is
    ALSO DD-eligible AND has a positive net return, then the result is
    robust at both floors, so the STRICTER (higher) floor wins — even if
    its net is lower. Otherwise the best-net config wins.

    Returns the chosen fit row, or None if no config is DD-eligible.
    """
    eligible = sorted(
        (r for r in fit_rows if r["dd"] <= MAX_DD),
        key=lambda r: r["net"], reverse=True,
    )
    if not eligible:
        return None
    best = eligible[0]
    bc = best["cfg"]

    # A sibling shares lookback/skip/band but has the other volume_floor.
    def is_sibling(r: dict) -> bool:
        c = r["cfg"]
        return (
            c["lookback"] == bc["lookback"]
            and c["skip"] == bc["skip"]
            and c["band"] == bc["band"]
            and c["volume_floor"] != bc["volume_floor"]
        )

    sibling = next((r for r in eligible if is_sibling(r)), None)
    if sibling is not None and sibling["net"] > 0:
        pair = [best, sibling]
        chosen = max(pair, key=lambda r: r["cfg"]["volume_floor"])
        logger.info(
            "floor rule FIRED: sibling eligible+positive (nets %.4f / %.4f); "
            "stricter floor %.0f wins over best-net floor %.0f",
            best["net"], sibling["net"],
            chosen["cfg"]["volume_floor"], bc["volume_floor"],
        )
        return chosen

    logger.info(
        "floor rule not applicable (no eligible positive sibling); "
        "best-net config wins (floor %.0f, net %.4f)",
        bc["volume_floor"], best["net"],
    )
    return best


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
