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
