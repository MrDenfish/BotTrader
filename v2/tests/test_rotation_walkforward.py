import dataclasses

import pandas as pd
import pytest

from backtest.rotation.engine import BacktestResult
from backtest.rotation.walkforward import (
    MAX_DD,
    PARAM_MENU,
    choose_fit_winner,
    era_bounds,
    passes_bar,
)


def _result(net, dd):
    eq = pd.Series([1.0, 1.0 + net])
    return BacktestResult(equity=eq, net_return=net, max_drawdown=dd,
                          n_trades=1, days_in_market=1, days_total=2, trades=[])


def _realized_bars(idx, start, end):
    # RotationBacktest.run() slices `.loc[start:end]` inclusive on both
    # ends, so "547 days" in the spec means 547 REALIZED BARS under that
    # inclusive semantics, not a 547-day Timedelta between the bounds.
    return len(idx[(idx >= start) & (idx <= end)])


class TestEraBounds:
    def test_three_eras_cover_index(self):
        idx = pd.date_range("2020-01-01", "2026-07-01", freq="D", tz="UTC")
        eras = era_bounds(idx)
        assert set(eras) == {"fit", "validate", "holdout"}
        assert eras["holdout"][1] == idx[-1]
        assert _realized_bars(idx, *eras["holdout"]) == 547
        assert _realized_bars(idx, *eras["validate"]) == 547
        assert eras["fit"][0] == idx[0]
        assert eras["fit"][1] < eras["validate"][0]
        assert eras["validate"][1] < eras["holdout"][0]
        # the three eras partition the index exactly, no gap/overlap in bars
        fit_bars = _realized_bars(idx, *eras["fit"])
        assert fit_bars + 547 + 547 == len(idx)

    def test_insufficient_history_raises(self):
        idx = pd.date_range("2024-01-01", "2026-07-01", freq="D", tz="UTC")
        with pytest.raises(ValueError):
            era_bounds(idx)


class TestParamMenu:
    def test_exactly_the_declared_cross_product(self):
        # {30,60,90} x {2,3} x {6,8} x {5e6,10e6} = 3*2*2*2 = 24
        assert len(PARAM_MENU) == 24
        assert {c.lookback for c in PARAM_MENU} == {30, 60, 90}
        assert {c.skip for c in PARAM_MENU} == {2, 3}
        assert {c.band for c in PARAM_MENU} == {6, 8}
        assert {c.volume_floor for c in PARAM_MENU} == {5e6, 10e6}
        # nothing else varies
        assert {c.k for c in PARAM_MENU} == {4}
        assert {c.cap for c in PARAM_MENU} == {0.30}

    def test_exposure_replace_preserves_closed_menu(self):
        # run_gauntlet.py's Phase 1 sweep builds cfg_run = dataclasses.replace(
        # cfg, exposure=args.exposure) for every menu entry so the DD-eligibility
        # check runs at the CLI exposure, not the RotationConfig default (1.0).
        # This must not perturb the pre-registered 24-config menu in any other way.
        exposure = 0.75
        swept = [dataclasses.replace(cfg, exposure=exposure) for cfg in PARAM_MENU]

        assert len(swept) == 24
        assert {c.lookback for c in swept} == {30, 60, 90}
        assert {c.skip for c in swept} == {2, 3}
        assert {c.band for c in swept} == {6, 8}
        assert {c.volume_floor for c in swept} == {5e6, 10e6}
        # k/cap untouched by the replace
        assert {c.k for c in swept} == {4}
        assert {c.cap for c in swept} == {0.30}
        # every swept config carries the injected exposure
        assert {c.exposure for c in swept} == {exposure}
        # menu identity unchanged (dataclasses.replace does not mutate cfg)
        assert {c.exposure for c in PARAM_MENU} == {1.0}
        # the (lookback, skip, band, volume_floor) combinations are identical
        # to the original menu — replace only touches exposure
        orig_keys = {(c.lookback, c.skip, c.band, c.volume_floor) for c in PARAM_MENU}
        swept_keys = {(c.lookback, c.skip, c.band, c.volume_floor) for c in swept}
        assert orig_keys == swept_keys


class TestPassesBar:
    def test_all_good_passes(self):
        v = passes_bar({"fit": _result(0.30, 0.10),
                        "validate": _result(0.12, 0.12),
                        "holdout": _result(0.05, 0.09)})
        assert v["pass"] is True

    def test_negative_era_fails(self):
        v = passes_bar({"fit": _result(0.30, 0.10),
                        "validate": _result(-0.02, 0.12),
                        "holdout": _result(0.05, 0.09)})
        assert v["pass"] is False
        assert any("validate" in r for r in v["reasons"])

    def test_drawdown_breach_fails(self):
        v = passes_bar({"fit": _result(0.30, 0.10),
                        "validate": _result(0.12, 0.19),
                        "holdout": _result(0.05, 0.09)})
        assert v["pass"] is False


class TestChooseFitWinner:
    def _row(self, floor, net, dd, lookback=60, skip=2, band=8):
        return {
            "cfg": {"lookback": lookback, "skip": skip, "band": band,
                    "volume_floor": floor},
            "net": net, "dd": dd,
        }

    def test_no_eligible_returns_none(self):
        rows = [self._row(5e6, 0.30, MAX_DD + 0.01)]
        assert choose_fit_winner(rows) is None

    def test_sibling_eligible_positive_stricter_floor_wins(self):
        # Best net is the loose floor (5e6), but its 10e6 sibling is also
        # eligible + positive → stricter (higher) floor wins despite lower net.
        rows = [
            self._row(5e6, 0.30, 0.10),   # best net, loose floor
            self._row(10e6, 0.20, 0.10),  # sibling: eligible, positive, lower net
        ]
        chosen = choose_fit_winner(rows)
        assert chosen["cfg"]["volume_floor"] == 10e6
        assert chosen["net"] == 0.20

    def test_sibling_ineligible_best_net_wins(self):
        # Sibling breaches the DD cap → not eligible → best net wins.
        rows = [
            self._row(5e6, 0.30, 0.10),
            self._row(10e6, 0.25, MAX_DD + 0.05),
        ]
        chosen = choose_fit_winner(rows)
        assert chosen["cfg"]["volume_floor"] == 5e6
        assert chosen["net"] == 0.30

    def test_sibling_eligible_negative_best_net_wins(self):
        # Sibling is eligible (DD ok) but net <= 0 → floor rule not robust,
        # best net wins.
        rows = [
            self._row(5e6, 0.30, 0.10),
            self._row(10e6, -0.02, 0.10),
        ]
        chosen = choose_fit_winner(rows)
        assert chosen["cfg"]["volume_floor"] == 5e6
        assert chosen["net"] == 0.30

    def test_no_sibling_present_best_net_wins(self):
        # Different lookback → not a sibling; best net simply wins.
        rows = [
            self._row(5e6, 0.30, 0.10, lookback=60),
            self._row(10e6, 0.25, 0.10, lookback=30),
        ]
        chosen = choose_fit_winner(rows)
        assert chosen["cfg"]["volume_floor"] == 5e6
