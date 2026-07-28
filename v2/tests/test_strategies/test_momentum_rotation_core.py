import numpy as np
import pandas as pd
import pytest

from v2.plugins.strategies.momentum_rotation.core import (
    daily_vol,
    momentum_score,
    raw_return,
)


def _series(values):
    return pd.Series(values, dtype=float)


class TestRawReturn:
    def test_simple_return_with_skip(self):
        # 10 bars: window is bars [-1-skip-lookback .. -1-skip] = idx 2 .. 7
        closes = _series([100, 100, 100, 110, 120, 115, 118, 125, 999, 999])
        # lookback=5, skip=2: 125 / 100 - 1 = 0.25; the two 999s are skipped
        assert raw_return(closes, lookback=5, skip=2) == pytest.approx(0.25)

    def test_too_short_returns_none(self):
        closes = _series([100.0] * 7)  # needs lookback+skip+1 = 8
        assert raw_return(closes, lookback=5, skip=2) is None

    def test_negative_return(self):
        closes = _series([100, 80, 100, 100])
        # lookback=1, skip=2: closes[-3]/closes[-4] - 1 = 80/100 - 1
        assert raw_return(closes, lookback=1, skip=2) == pytest.approx(-0.20)


class TestDailyVol:
    def test_constant_prices_zero_vol(self):
        closes = _series([100.0] * 40)
        assert daily_vol(closes, lookback=30, skip=2) == pytest.approx(0.0)

    def test_known_vol(self):
        # Alternating +10%/-10% has a computable std
        vals, p = [], 100.0
        for i in range(40):
            p = p * (1.10 if i % 2 == 0 else 0.90)
            vals.append(p)
        v = daily_vol(_series(vals), lookback=30, skip=2)
        assert v == pytest.approx(0.1017, abs=0.005)


class TestMomentumScore:
    def test_score_is_return_over_vol(self):
        rng = np.random.default_rng(42)
        vals = list(100 * np.cumprod(1 + rng.normal(0.002, 0.03, 80)))
        closes = _series(vals)
        r = raw_return(closes, 60, 3)
        v = daily_vol(closes, 60, 3)
        assert momentum_score(closes, 60, 3) == pytest.approx(r / v)

    def test_zero_vol_returns_none(self):
        closes = _series([100.0] * 40)
        assert momentum_score(closes, lookback=30, skip=2) is None

    def test_too_short_returns_none(self):
        closes = _series([100.0, 101.0, 102.0])
        assert momentum_score(closes, lookback=30, skip=2) is None


from v2.plugins.strategies.momentum_rotation.core import (
    inverse_vol_weights,
    select_holdings,
)


class TestSelectHoldings:
    RANKED = ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J"]

    def test_cold_start_takes_top_k(self):
        assert select_holdings(self.RANKED, current=[], k=4, band=8) == ["A", "B", "C", "D"]

    def test_holding_inside_band_is_kept(self):
        # E is rank 5 (< band 8): kept, so only 3 fresh slots -> A, B, C
        got = select_holdings(self.RANKED, current=["E"], k=4, band=8)
        assert got == ["A", "B", "C", "E"]

    def test_holding_outside_band_is_replaced(self):
        # I is rank 9 (> band 8): dropped
        got = select_holdings(self.RANKED, current=["I"], k=4, band=8)
        assert got == ["A", "B", "C", "D"]

    def test_holding_missing_from_ranked_is_dropped(self):
        got = select_holdings(self.RANKED, current=["ZZZ"], k=4, band=8)
        assert got == ["A", "B", "C", "D"]

    def test_no_churn_when_holdings_still_in_band(self):
        current = ["A", "C", "E", "G"]
        got = select_holdings(self.RANKED, current=current, k=4, band=8)
        assert got == ["A", "C", "E", "G"]

    def test_fewer_eligible_than_k(self):
        assert select_holdings(["A", "B"], current=[], k=4, band=8) == ["A", "B"]


class TestInverseVolWeights:
    def test_two_assets_inverse_proportion_capped(self):
        # 1/0.02 : 1/0.04 = 2 : 1 -> 0.667/0.333 uncapped; cap 0.30 binds both
        w = inverse_vol_weights({"A": 0.02, "B": 0.04}, cap=0.30)
        assert w["A"] == pytest.approx(0.30)
        assert w["B"] == pytest.approx(0.30)

    def test_equal_vols_equal_weights_under_cap(self):
        w = inverse_vol_weights({s: 0.03 for s in "ABCD"}, cap=0.30)
        for s in "ABCD":
            assert w[s] == pytest.approx(0.25)

    def test_cap_redistributes_then_leaves_cash(self):
        # A very low vol grabs the cap; B and C split the rest by inverse vol
        w = inverse_vol_weights({"A": 0.001, "B": 0.03, "C": 0.03}, cap=0.30)
        assert w["A"] == pytest.approx(0.30)
        assert w["B"] == pytest.approx(w["C"])
        assert sum(w.values()) <= 1.0 + 1e-9

    def test_bad_vols_excluded(self):
        w = inverse_vol_weights({"A": 0.02, "B": 0.0, "C": None}, cap=0.30)
        assert set(w) == {"A"}

    def test_empty_input(self):
        assert inverse_vol_weights({}, cap=0.30) == {}

    def test_cap_cascades_through_multiple_names(self):
        # Regression: 3+ asymmetric vols should iterate to convergence.
        # A: 0.001 (1000), B: 0.01 (100), C: 0.1 (10) -> raw inverse vols
        # Iter 1: A capped at 0.30 (budget was 1.0, A*1000/1110 >> 0.30)
        # Iter 2: B capped at 0.30 (budget 0.70, B*100/110 >> 0.30)
        # Iter 3: C capped at 0.30 (budget 0.40, C*10/10 == 0.40 >= 0.30)
        w = inverse_vol_weights({"A": 0.001, "B": 0.01, "C": 0.1}, cap=0.30)
        assert w["A"] == pytest.approx(0.30)
        assert w["B"] == pytest.approx(0.30)
        assert w["C"] == pytest.approx(0.30)
        assert sum(w.values()) == pytest.approx(0.90)


from v2.plugins.strategies.momentum_rotation.core import market_filter


class TestMarketFilter:
    def test_above_ma_is_risk_on(self):
        closes = _series([100.0] * 200 + [150.0])
        assert market_filter(closes, ma_len=200) is True

    def test_below_ma_is_risk_off(self):
        closes = _series([100.0] * 200 + [50.0])
        assert market_filter(closes, ma_len=200) is False

    def test_insufficient_history_returns_none(self):
        closes = _series([100.0] * 150)
        assert market_filter(closes, ma_len=200) is None

    def test_exactly_at_ma_is_risk_off(self):
        closes = _series([100.0] * 250)
        assert market_filter(closes, ma_len=200) is False
