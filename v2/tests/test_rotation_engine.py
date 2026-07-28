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

    def test_hand_computed_single_rebalance_fixture(self):
        # ---------------------------------------------------------------
        # A fully hand-derivable one-rebalance scenario. Two tradable
        # symbols (A, B) + BTC (regime anchor only, screened out of the
        # tradable universe by tiny volume). k=1, cap=1.0, exposure=1.0,
        # so the single holding gets the FULL 100% target weight (w=1).
        #
        # Costs: c = fee_per_side + slippage_per_side = 0.002 + 0.0005
        #          = 0.0025 per side (buy only here).
        #
        # Run window = exactly [Mon, Tue, Wed] → ONE rebalance (Monday).
        #   B is flat  → raw_return == 0 → fails the >0 momentum floor →
        #                never scored → A is the sole candidate → picked.
        #
        # Day 0 (Mon, rebalance) at price A=100:
        #   start all cash, equity_pre = 1.0, threshold = 0.005.
        #   buy notional = w*equity = 1.0 ; cash -> 1 - 1 = 0.
        #   units = notional*(1-c)/price = 1*(0.9975)/100 = 0.009975.
        #   post-rebalance equity = cash + units*price
        #                         = 0 + 0.009975*100 = 0.997500
        #   (== 1 - w*c = 1 - 0.0025).  <-- assert to 6 dp
        #
        # Day 1 (Tue) A dips 100 -> 90 (a clean -10% move):
        #   equity = units*90 = 0.009975*90 = 0.897750
        #   cash is 0, so equity tracks price exactly; a -10% price move
        #   is a -10% equity move regardless of c.
        #
        # Day 2 (Wed) A recovers to 105:
        #   equity = units*105 = 0.009975*105 = 1.047375
        #   net_return = 1.047375 - 1 = 0.047375
        #
        # equity series = [0.997500, 0.897750, 1.047375]
        #   peak (cummax) = [0.997500, 0.997500, 1.047375]
        #   drawdown      = [0, (0.9975-0.89775)/0.9975, 0] = [0, 0.1, 0]
        #   max_drawdown  = 0.100000   <-- exact, assert to 6 dp
        # ---------------------------------------------------------------
        idx = pd.date_range("2023-01-01", periods=400, freq="D", tz="UTC")
        reb = pd.Timestamp("2023-12-25", tz="UTC")  # a Monday
        assert reb.weekday() == 0
        pos = int(idx.get_indexer([reb])[0])
        assert pos + 2 < len(idx)  # room for Mon/Tue/Wed

        a = np.empty(len(idx))
        a[: pos + 1] = np.linspace(50.0, 100.0, pos + 1)  # rising -> 100 at Mon
        a[pos + 1] = 90.0   # Tue dip
        a[pos + 2:] = 105.0  # Wed onward (only Wed is valued)

        def mk(close, vol):
            close = np.asarray(close, dtype=float)
            return pd.DataFrame(
                {"open": close, "high": close, "low": close, "close": close,
                 "volume": [vol] * len(idx)},
                index=idx,
            )

        bars = {
            "A-USD": mk(a, 1e9),
            "B-USD": mk([100.0] * len(idx), 1e9),          # flat -> never picked
            "BTC-USD": mk(np.linspace(100.0, 200.0, len(idx)), 1.0),  # regime only
        }
        cfg = RotationConfig(
            lookback=3, skip=0, band=8, k=1, cap=1.0, exposure=1.0,
            volume_floor=1e6, min_age_days=100, ma_len=5,
            fee_per_side=0.002, slippage_per_side=0.0005, rebalance_weekday=0,
        )
        res = RotationBacktest(bars, "BTC-USD", cfg).run(reb, reb + pd.Timedelta(days=2))

        assert len(res.equity) == 3
        assert round(float(res.equity.iloc[0]), 6) == 0.997500   # post-rebalance
        assert round(float(res.equity.iloc[1]), 6) == 0.897750
        assert round(float(res.equity.iloc[-1]), 6) == 1.047375
        assert round(res.net_return, 6) == 0.047375
        assert round(res.max_drawdown, 6) == 0.100000
        # A was bought, B never traded
        assert any(t["symbol"] == "A-USD" for t in res.trades)
        assert not any(t["symbol"] == "B-USD" for t in res.trades)
