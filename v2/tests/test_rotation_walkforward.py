import pandas as pd
import pytest

from backtest.rotation.engine import BacktestResult
from backtest.rotation.walkforward import PARAM_MENU, era_bounds, passes_bar


def _result(net, dd):
    eq = pd.Series([1.0, 1.0 + net])
    return BacktestResult(equity=eq, net_return=net, max_drawdown=dd,
                          n_trades=1, days_in_market=1, days_total=2, trades=[])


class TestEraBounds:
    def test_three_eras_cover_index(self):
        idx = pd.date_range("2020-01-01", "2026-07-01", freq="D", tz="UTC")
        eras = era_bounds(idx)
        assert set(eras) == {"fit", "validate", "holdout"}
        assert eras["holdout"][1] == idx[-1]
        assert (eras["holdout"][1] - eras["holdout"][0]).days == 547
        assert (eras["validate"][1] - eras["validate"][0]).days == pytest.approx(547, abs=1)
        assert eras["fit"][0] == idx[0]
        assert eras["fit"][1] < eras["validate"][0]

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
