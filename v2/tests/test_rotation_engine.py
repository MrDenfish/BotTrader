import numpy as np
import pandas as pd
import pytest

from backtest.rotation.engine import BacktestResult, RotationBacktest, RotationConfig


def make_bars(idx, path):
    """Deterministic price path with constant huge volume (always eligible)."""
    return pd.DataFrame(
        {"open": path, "high": path, "low": path, "close": path,
         "volume": [1e9] * len(idx)},
        index=idx,
    )


@pytest.fixture
def world():
    """400 days; BTC strongly rising (risk-on); UP doubles, FLAT flat."""
    idx = pd.date_range("2023-01-01", periods=400, freq="D", tz="UTC")
    up = list(100 * np.linspace(1, 2, 400))
    flat = [100.0] * 400
    btc = list(100 * np.linspace(1, 3, 400))
    return {
        "BTC-USD": make_bars(idx, btc),
        "UP-USD": make_bars(idx, up),
        "FLAT-USD": make_bars(idx, flat),
    }, idx


def _cfg(**kw):
    base = dict(lookback=30, skip=2, band=8, k=2, volume_floor=1e6,
                min_age_days=100, exposure=1.0)
    base.update(kw)
    return RotationConfig(**base)


class TestRotationBacktest:
    def test_risk_on_uptrend_is_profitable(self, world):
        bars, idx = world
        bt = RotationBacktest(bars, "BTC-USD", _cfg())
        res = bt.run(idx[250], idx[-1])
        assert isinstance(res, BacktestResult)
        assert res.net_return > 0
        assert res.days_in_market > 0
        assert res.equity.iloc[0] == pytest.approx(1.0)

    def test_flat_coin_never_selected(self, world):
        # FLAT has raw_return == 0, fails the absolute-momentum floor (> 0)
        bars, idx = world
        bt = RotationBacktest(bars, "BTC-USD", _cfg())
        res = bt.run(idx[250], idx[-1])
        assert not any(t["symbol"] == "FLAT-USD" for t in res.trades)

    def test_risk_off_liquidates_and_stays_cash(self, world):
        bars, idx = world
        # Crash BTC below its 200d MA for the last 60 days
        bars["BTC-USD"].loc[idx[-60]:, ["open", "high", "low", "close"]] = 10.0
        bt = RotationBacktest(bars, "BTC-USD", _cfg())
        res = bt.run(idx[250], idx[-1])
        # After the crash day there must be sell trades and zero buys
        crash = idx[-60]
        post = [t for t in res.trades if t["date"] >= crash]
        assert all(t["side"] == "sell" for t in post)
        # Equity must be flat (cash) over the final 30 days
        tail = res.equity.loc[idx[-30]:]
        assert tail.nunique() == 1

    def test_fees_reduce_returns(self, world):
        bars, idx = world
        free = RotationBacktest(bars, "BTC-USD",
                                _cfg(fee_per_side=0.0, slippage_per_side=0.0))
        paid = RotationBacktest(bars, "BTC-USD", _cfg())
        r_free = free.run(idx[250], idx[-1])
        r_paid = paid.run(idx[250], idx[-1])
        assert r_free.net_return > r_paid.net_return

    def test_no_lookahead_smoke(self, world):
        """Truncating unseen future data must not change the past equity curve."""
        bars, idx = world
        full = RotationBacktest(bars, "BTC-USD", _cfg()).run(idx[250], idx[300])
        cut = {s: df[df.index <= idx[300]] for s, df in bars.items()}
        trunc = RotationBacktest(cut, "BTC-USD", _cfg()).run(idx[250], idx[300])
        pd.testing.assert_series_equal(full.equity, trunc.equity)

    def test_max_drawdown_positive_fraction(self, world):
        bars, idx = world
        bars["UP-USD"].loc[idx[300]:idx[320], ["open", "high", "low", "close"]] *= 0.5
        res = RotationBacktest(bars, "BTC-USD", _cfg()).run(idx[250], idx[-1])
        assert 0.0 <= res.max_drawdown <= 1.0
