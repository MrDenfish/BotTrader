import numpy as np
import pandas as pd
import pytest

from backtest.rotation.carry_engine import CarryBacktest, CarryConfig


def bars_from(closes, idx):
    return pd.DataFrame(
        {"open": closes, "high": closes, "low": closes, "close": closes,
         "volume": [1e9] * len(idx)}, index=idx)


IDX = pd.date_range("2024-01-01", periods=320, freq="D", tz="UTC")


def _cfg(**kw):
    base = dict(scheme="equal", ma_len=5, vol_lookback=5,
                universe=("AAA-USD",), btc="BTC-USD")
    base.update(kw)
    return CarryConfig(**base)


class TestHandComputedFixture:
    """Single-asset universe, equal scheme (base weight = min(1/1, 0.5) = 0.5),
    exposure 1.0, ma_len=5. Cost per side c = 0.00325 + 0.0005 = 0.00375.

    Price path for AAA (after warmup at 100): jumps to 110 (above its 5d MA),
    holds, then drops to 90 (below MA -> exit signal), next bar 88 (lagged
    sell executes at 88).

    NOTE on the "holds" segment: a *perfectly* flat 110 for the whole
    plateau is unusable here. market_filter() is a strict `close > MA`
    check (v2/plugins/strategies/momentum_rotation/core.py, off-limits for
    this task); once a 5-day trailing window is entirely 110s (4 days
    after the jump), close == MA and the gate reads False -- every held
    sleeve would get exit-queued daily (rule 4) long before the day-310
    drop, contradicting "exactly one sell, at day 311". Confirmed
    empirically against the real market_filter(): with entry on day 259
    (position >= 255) the gate is already closed and no buy ever happens;
    with entry on day 252 (inside the still-transitional window) the
    position gets prematurely exited around day 255 once the window
    saturates. So AAA stays flat at 110 only for the transitional window
    (days 250-253, where the trailing window still contains pre-jump 100s
    and close > MA holds honestly), then ramps up by 1e-6/day from day 254
    through day 309 -- strictly increasing, so the current close is always
    the max of its own trailing window and the gate stays continuously
    True. Entry is pinned to day 252 (the Monday inside the transitional
    window) so the buy price is still exactly 110. The ramp increment is
    1e-6/day (56 days -> max drift 5.6e-5 above 110): small enough that it
    nudges the equity peak by ~2.5e-7 (units * max drift), safely under
    the max_drawdown assertion's 1e-6 tolerance, yet large enough that
    daily_vol()'s pct_change/std on the window stays representably
    positive in float64 (empirically checked at every rebalance Monday in
    the hold: vol ~1e-16, tiny but > 0). A smaller increment (tried 1e-8)
    makes consecutive daily returns indistinguishable in float64, so
    std() rounds to exactly 0.0 -> carry_targets() reads vol <= 0 ->
    AAA is treated as *ungated* despite gate[AAA] being True -> a bogus
    weekly-rebalance sell fires (target drops to 0, and sleeve_trades()
    always force-sells a held sleeve whose target is 0). The day-310
    price (90), day-311 price (88), and every exact-equity assertion
    below are untouched since they depend only on the entry price (110,
    unaffected) and the post-ramp prices (unaffected) -- not on any value
    the ramp actually touches.

    Hand math:
      buy Monday @110, weight 0.5 of equity 1.0:
        cash -> 0.5 ; units = 0.5*(1-0.00375)/110 = 0.498125/110
                            = 0.00452840909090...
        equity(110) = 0.5 + units*110 = 0.998125
      drop day @90 (signal day, still held):
        equity = 0.5 + units*90 = 0.5 + 0.40755681... = 0.90755681...
      lagged sell @88:
        proceeds = units*88*(1-0.00375) = 0.3985000000*0.99625
                 = 0.39700562500...  (units*88 = 0.39850000 exactly)
        final equity = 0.5 + 0.397005625 = 0.897005625
      max drawdown: peak 0.998125, trough = final 0.897005625
        dd = (0.998125-0.897005625)/0.998125 = 0.101119375/0.998125
           = 0.10130937...
    """

    def _world(self):
        # BTC rising forever: master gate open once MA warm
        btc = list(np.linspace(100, 200, len(IDX)))
        # AAA: 100 until day 249; 110 flat on days 250..253 (transitional
        # window, gate honestly open); ramps by 1e-6/day on days 254..309
        # (keeps gate open without ever tying the MA, and keeps daily_vol()
        # representably positive -- see note above -- while staying
        # negligibly close to 110); 90 on day 310; 88 on 311+.
        ramp = [110.0 + 1e-6 * (i + 1) for i in range(56)]
        aaa = [100.0] * 250 + [110.0] * 4 + ramp + [90.0] + [88.0] * (len(IDX) - 311)
        return {"BTC-USD": bars_from(btc, IDX), "AAA-USD": bars_from(aaa, IDX)}

    def test_exact_equity_path(self):
        bars = self._world()
        # first Monday with index position >= 250: lands on day 252, inside
        # the transitional window (250-253) where the gate is honestly open
        monday = next(d for d in IDX[250:] if d.weekday() == 0)
        bt = CarryBacktest(bars, _cfg())
        res = bt.run(monday, IDX[-1])
        eq = res.equity
        assert eq.iloc[0] == pytest.approx(0.998125, abs=1e-9)      # post-buy
        assert eq.loc[IDX[310]] == pytest.approx(0.90755681818, abs=1e-9)
        assert eq.iloc[-1] == pytest.approx(0.897005625, abs=1e-9)
        assert res.max_drawdown == pytest.approx(0.10130937, abs=1e-6)
        sells = [t for t in res.trades if t["side"] == "sell"]
        assert len(sells) == 1 and sells[0]["date"] == IDX[311]     # lagged 1 bar

    def test_master_gate_closes_whole_book(self):
        bars = self._world()
        # BTC crashes below its MA on day 300 -> master closes -> AAA sold
        bars["BTC-USD"].loc[IDX[300]:, ["open", "high", "low", "close"]] = 10.0
        monday = next(d for d in IDX[250:] if d.weekday() == 0)
        res = CarryBacktest(bars, _cfg()).run(monday, IDX[-1])
        sells = [t for t in res.trades if t["side"] == "sell"]
        assert sells and sells[0]["date"] == IDX[301]               # lag applies
        tail = res.equity.loc[IDX[302]:]
        assert tail.nunique() == 1                                  # flat in cash

    def test_no_entries_while_any_gate_closed(self):
        bars = self._world()
        bars["AAA-USD"].loc[:, ["open", "high", "low", "close"]] = 50.0  # never above MA... constant == MA -> closed
        monday = next(d for d in IDX[250:] if d.weekday() == 0)
        res = CarryBacktest(bars, _cfg()).run(monday, IDX[-1])
        assert res.n_trades == 0
        assert res.days_in_market == 0

    def test_queued_sell_executes_even_if_gate_reopens(self):
        bars = self._world()
        # dip below MA for exactly one day then back above
        bars["AAA-USD"].loc[IDX[310], ["open", "high", "low", "close"]] = 90.0
        bars["AAA-USD"].loc[IDX[311]:, ["open", "high", "low", "close"]] = 111.0
        monday = next(d for d in IDX[250:] if d.weekday() == 0)
        res = CarryBacktest(bars, _cfg()).run(monday, IDX[-1])
        sells = [t for t in res.trades if t["side"] == "sell"]
        assert len(sells) == 1 and sells[0]["date"] == IDX[311]

    def test_no_lookahead_truncation_invariance(self):
        bars = self._world()
        monday = next(d for d in IDX[250:] if d.weekday() == 0)
        cut_day = IDX[300]
        full = CarryBacktest(bars, _cfg()).run(monday, cut_day)
        cut = {s: df[df.index <= cut_day] for s, df in bars.items()}
        trunc = CarryBacktest(cut, _cfg()).run(monday, cut_day)
        pd.testing.assert_series_equal(full.equity, trunc.equity)

    def test_three_asset_universe_smoke(self):
        idx = pd.date_range("2023-01-01", periods=400, freq="D", tz="UTC")
        up = list(100 * np.linspace(1, 2, 400))
        cfg = CarryConfig(scheme="inverse_vol", ma_len=50, vol_lookback=20,
                          universe=("BTC-USD", "ETH-USD", "SOL-USD"))
        bars = {s: bars_from(up, idx) for s in cfg.universe}
        res = CarryBacktest(bars, cfg).run(idx[300], idx[-1])
        assert res.net_return > 0
        assert res.days_in_market > 0
