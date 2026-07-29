import numpy as np
import pandas as pd
import pytest

from backtest.rotation.carry_engine import CarryConfig
from backtest.rotation.carry_walkforward import (
    CARRY_MENU,
    FIT_END,
    FIT_START,
    VALIDATE_START,
    calibrate_exposure,
    carry_eras,
)


class TestCarryEras:
    def test_fixed_dates(self):
        idx = pd.date_range("2013-10-06", "2026-07-26", freq="D", tz="UTC")
        eras = carry_eras(idx)
        assert eras["fit"] == (FIT_START, FIT_END)
        assert eras["validate"][0] == VALIDATE_START
        assert eras["validate"][1] == idx[-1]

    def test_insufficient_coverage_raises(self):
        idx = pd.date_range("2019-01-01", "2026-07-26", freq="D", tz="UTC")
        with pytest.raises(ValueError):
            carry_eras(idx)
        idx2 = pd.date_range("2013-01-01", "2024-12-31", freq="D", tz="UTC")
        with pytest.raises(ValueError):
            carry_eras(idx2)


class TestCarryMenu:
    def test_exactly_two_configs_schemes_only(self):
        assert len(CARRY_MENU) == 2
        assert {c.scheme for c in CARRY_MENU} == {"equal", "inverse_vol"}
        for c in CARRY_MENU:
            assert c.ma_len == 200
            assert c.cap == 0.50
            assert c.drift_band == 0.20
            assert c.universe == ("BTC-USD", "ETH-USD", "SOL-USD")


class TestCalibrateExposure:
    """Synthetic world where DD scales exactly with exposure is impossible
    with a real engine, so use a volatile-but-trending fixture and assert
    the calibration CONTRACT, not exact values: returned DD <= 0.14, and
    exposure=1.0 short-circuit when full exposure is already tame."""

    def _world(self, amplitude):
        idx = pd.date_range("2016-01-01", periods=600, freq="D", tz="UTC")
        rng = np.random.default_rng(7)
        path = 100 * np.cumprod(1 + rng.normal(0.003, amplitude, 600))
        df = pd.DataFrame({"open": path, "high": path, "low": path,
                           "close": path, "volume": [1e9] * 600}, index=idx)
        return {"BTC-USD": df.copy(), "AAA-USD": df.copy()}, idx

    def _cfg(self):
        return CarryConfig(scheme="equal", ma_len=20, vol_lookback=10,
                           universe=("AAA-USD",), btc="BTC-USD")

    def test_dd_within_cap_after_calibration(self):
        bars, idx = self._world(amplitude=0.06)   # wild: needs scaling down
        expo, res = calibrate_exposure(bars, self._cfg(), (idx[200], idx[-1]))
        assert 0.0 < expo < 1.0
        assert res.max_drawdown <= 0.14 + 1e-9

    def test_short_circuit_at_full_exposure(self):
        bars, idx = self._world(amplitude=0.004)  # tame: no scaling needed
        expo, res = calibrate_exposure(bars, self._cfg(), (idx[200], idx[-1]))
        assert expo == 1.0
