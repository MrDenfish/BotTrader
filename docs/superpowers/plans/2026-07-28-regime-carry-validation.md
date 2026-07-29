# Regime-Gated Carry — Plan 1: Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the carry allocation math, a daily-loop backtest engine with lagged exits, and a fixed-era two-config gauntlet, producing a pass/fail verdict against spec §7.

**Architecture:** Pure allocation math in a new `v2/plugins/strategies/regime_carry/core.py` (imported later by the live plugin, Plan 2). A dedicated `CarryBacktest` engine and walk-forward module live in the existing `backtest/rotation/` package (they reuse its `BacktestResult`, cost model, and output conventions but do NOT modify the validated rotation engine). Gates reuse `market_filter` and `daily_vol` from `v2/plugins/strategies/momentum_rotation/core.py`.

**Tech Stack:** Python 3.11, pandas + numpy, pytest, existing `DailyBarStore` cache (BTC/ETH/SOL history already present through 2026-07-26). No new dependencies.

**Spec:** `docs/superpowers/specs/2026-07-28-regime-gated-carry-design.md` — §2 gates, §3 allocation, §4 evaluation, §5 sizing, §7 protocol, §8 menu.

## Global Constraints

- Parameter menu is CLOSED (spec §8): weight scheme ∈ {equal, inverse_vol(60d)} — 2 configs total. Fixed: MA length 200 (both gate layers), per-sleeve cap 0.50, drift band 0.20, universe (BTC-USD, ETH-USD, SOL-USD), costs 0.325% fee + 5 bps slippage per side. Exposure is continuous, calibrated on fit only to a 12–14% fit-era max DD.
- Eras are FIXED DATES (spec §7): fit 2017-01-01 → 2025-01-25; validate 2026-01-26 boundary — exactly: validate 2025-01-26 → last cached bar. No holdout era exists; forward paper is the holdout.
- Honesty rules bind every backtest: no staking yield anywhere; ALL sells execute one bar after their signal (exit lag — a conservative superset of spec §4, covering drift-trim sells too); buys execute at signal close. Queued sells always execute even if the gate reopens meanwhile (whipsaw cost is kept, never optimized away).
- No-redistribution rule (spec §3): base weights are computed over the FULL universe, then gated-out sleeves are zeroed. Vacant weight is cash.
- Master gate not True (False OR None) → entire book exits. Asset gate not True → that sleeve exits. Insufficient history (< ma_len bars) = gate closed (`market_filter` returns None; treat as closed).
- Repo is PUBLIC: numeric results only under gitignored `backtest/rotation/output/` (prefix `carry_`); summaries go to the private memory dir.
- Tests run with the tradebot conda env: `conda run -n tradebot python -m pytest v2/tests/ -q`. Baseline: 761 passed / 0 failed. Suite must stay green; commit per task on branch `feature/regime-carry` (create from main at plan start).
- Timestamps: UTC daily bars; "Monday rebalance from Sunday's completed bar" = on the Monday-dated bar's close using data ≤ that bar (consistent with the rotation engine convention; live/backtest parity reconciliation is a Plan 2 item, per spec §10).

---

### Task 1: Carry core — targets and sleeve trades

**Files:**
- Create: `v2/plugins/strategies/regime_carry/__init__.py` (empty)
- Create: `v2/plugins/strategies/regime_carry/core.py`
- Test: `v2/tests/test_strategies/test_regime_carry_core.py`

**Interfaces:**
- Consumes: `inverse_vol_weights` from `v2.plugins.strategies.momentum_rotation.core`.
- Produces (used by Task 2's engine and Plan 2's live plugin):
  - `carry_targets(vols: dict[str, float | None], eligible: set[str], universe: tuple[str, ...], scheme: str, cap: float = 0.50, exposure: float = 1.0) -> dict[str, float]` — base weights over the FULL universe (`equal`: 1/len(universe) each, capped at `cap`; `inverse_vol`: `inverse_vol_weights` over all universe vols with `cap`), then sleeves not in `eligible` (or with vol None/<=0 under either scheme) are set to 0.0, then all scaled by `exposure`. Every universe symbol appears in the result. Raises `ValueError` on unknown `scheme`.
  - `sleeve_trades(current: dict[str, float], target: dict[str, float], band: float = 0.20) -> dict[str, float]` — per-symbol weight delta to trade. Rules: if `target == 0` and `current > 1e-9` → full sell (`-current`, band ignored — exits always execute). If `target > 0` and `abs(current - target) > band * target` → delta `target - current`. Otherwise 0.0 (suppressed by band). Symbols come from the union of both dicts; missing keys read as 0.0.

- [ ] **Step 1: Write the failing tests**

```python
# v2/tests/test_strategies/test_regime_carry_core.py
import pytest

from v2.plugins.strategies.regime_carry.core import carry_targets, sleeve_trades

UNIVERSE = ("BTC-USD", "ETH-USD", "SOL-USD")
VOLS = {"BTC-USD": 0.02, "ETH-USD": 0.03, "SOL-USD": 0.05}


class TestCarryTargets:
    def test_equal_all_eligible(self):
        w = carry_targets(VOLS, set(UNIVERSE), UNIVERSE, "equal")
        for s in UNIVERSE:
            assert w[s] == pytest.approx(1 / 3)

    def test_equal_no_redistribution_when_sleeve_gated_out(self):
        # SOL gated out: its 1/3 goes to cash, NOT to BTC/ETH
        w = carry_targets(VOLS, {"BTC-USD", "ETH-USD"}, UNIVERSE, "equal")
        assert w["SOL-USD"] == 0.0
        assert w["BTC-USD"] == pytest.approx(1 / 3)
        assert w["ETH-USD"] == pytest.approx(1 / 3)
        assert sum(w.values()) == pytest.approx(2 / 3)

    def test_inverse_vol_base_over_full_universe(self):
        # Base inverse-vol weights over ALL THREE (1/.02 : 1/.03 : 1/.05
        # = 50:33.3:20 -> 0.4839/0.3226/0.1935, none capped), then zero SOL.
        w = carry_targets(VOLS, {"BTC-USD", "ETH-USD"}, UNIVERSE, "inverse_vol")
        assert w["SOL-USD"] == 0.0
        assert w["BTC-USD"] == pytest.approx(0.48387, abs=1e-4)
        assert w["ETH-USD"] == pytest.approx(0.32258, abs=1e-4)

    def test_cap_binds(self):
        vols = {"BTC-USD": 0.001, "ETH-USD": 0.05, "SOL-USD": 0.05}
        w = carry_targets(vols, set(UNIVERSE), UNIVERSE, "inverse_vol", cap=0.50)
        assert w["BTC-USD"] == pytest.approx(0.50)

    def test_exposure_scales_everything(self):
        w = carry_targets(VOLS, set(UNIVERSE), UNIVERSE, "equal", exposure=0.4)
        for s in UNIVERSE:
            assert w[s] == pytest.approx(0.4 / 3)

    def test_none_vol_is_ineligible_even_if_gated_in(self):
        vols = {"BTC-USD": 0.02, "ETH-USD": None, "SOL-USD": 0.05}
        w = carry_targets(vols, set(UNIVERSE), UNIVERSE, "inverse_vol")
        assert w["ETH-USD"] == 0.0
        w2 = carry_targets(vols, set(UNIVERSE), UNIVERSE, "equal")
        assert w2["ETH-USD"] == 0.0

    def test_empty_eligible_all_cash(self):
        w = carry_targets(VOLS, set(), UNIVERSE, "equal")
        assert all(v == 0.0 for v in w.values())

    def test_unknown_scheme_raises(self):
        with pytest.raises(ValueError):
            carry_targets(VOLS, set(UNIVERSE), UNIVERSE, "momentum")


class TestSleeveTrades:
    def test_full_exit_ignores_band(self):
        t = sleeve_trades({"BTC-USD": 0.05}, {"BTC-USD": 0.0}, band=0.20)
        assert t["BTC-USD"] == pytest.approx(-0.05)

    def test_within_band_suppressed(self):
        # target 0.30, current 0.25: |delta| 0.05 < 0.20*0.30=0.06 -> no trade
        t = sleeve_trades({"BTC-USD": 0.25}, {"BTC-USD": 0.30}, band=0.20)
        assert t["BTC-USD"] == 0.0

    def test_outside_band_trades_delta(self):
        # target 0.30, current 0.22: |delta| 0.08 > 0.06 -> trade +0.08
        t = sleeve_trades({"BTC-USD": 0.22}, {"BTC-USD": 0.30}, band=0.20)
        assert t["BTC-USD"] == pytest.approx(0.08)

    def test_new_position_from_zero(self):
        # current 0 vs target 0.30: |0.30| > 0.06 -> buy full target
        t = sleeve_trades({}, {"ETH-USD": 0.30}, band=0.20)
        assert t["ETH-USD"] == pytest.approx(0.30)

    def test_zero_current_zero_target_no_trade(self):
        t = sleeve_trades({"SOL-USD": 0.0}, {"SOL-USD": 0.0}, band=0.20)
        assert t["SOL-USD"] == 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `conda run -n tradebot python -m pytest v2/tests/test_strategies/test_regime_carry_core.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'v2.plugins.strategies.regime_carry'`

- [ ] **Step 3: Write the implementation**

Create empty `v2/plugins/strategies/regime_carry/__init__.py` (project rule: no registry decorators in `__init__.py`; the live plugin file comes in Plan 2), then:

```python
# v2/plugins/strategies/regime_carry/core.py
"""Pure carry allocation math — no I/O, no event bus.

Shared by the carry backtest engine (backtest/rotation/carry_engine.py)
and the future live regime_carry strategy plugin.

No-redistribution rule (spec section 3): base weights are computed over
the FULL universe, then gated-out sleeves are zeroed. Weight freed by a
gated-out asset becomes cash, never a larger allocation to survivors.
"""
from __future__ import annotations

from v2.plugins.strategies.momentum_rotation.core import inverse_vol_weights


def carry_targets(
    vols: dict[str, float | None],
    eligible: set[str],
    universe: tuple[str, ...],
    scheme: str,
    cap: float = 0.50,
    exposure: float = 1.0,
) -> dict[str, float]:
    if scheme == "equal":
        base = {s: min(1.0 / len(universe), cap) for s in universe}
    elif scheme == "inverse_vol":
        base = inverse_vol_weights({s: vols.get(s) for s in universe}, cap=cap)
    else:
        raise ValueError(f"unknown scheme: {scheme!r}")

    out: dict[str, float] = {}
    for s in universe:
        v = vols.get(s)
        gated_in = s in eligible and v is not None and v > 0
        out[s] = base.get(s, 0.0) * exposure if gated_in else 0.0
    return out


def sleeve_trades(
    current: dict[str, float],
    target: dict[str, float],
    band: float = 0.20,
) -> dict[str, float]:
    """Per-symbol weight delta to trade, with drift-band suppression.

    Exits (target == 0, current held) always trade — the band never
    suppresses a gate-mandated exit.
    """
    out: dict[str, float] = {}
    for s in set(current) | set(target):
        cur = current.get(s, 0.0)
        tgt = target.get(s, 0.0)
        if tgt <= 0.0:
            out[s] = -cur if cur > 1e-9 else 0.0
        elif abs(cur - tgt) > band * tgt:
            out[s] = tgt - cur
        else:
            out[s] = 0.0
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `conda run -n tradebot python -m pytest v2/tests/test_strategies/test_regime_carry_core.py -v` — all PASS.
Run: `conda run -n tradebot python -m pytest v2/tests/ -q` — suite green (761 + new).

- [ ] **Step 5: Commit**

```bash
git add v2/plugins/strategies/regime_carry/ v2/tests/test_strategies/test_regime_carry_core.py
git commit -m "feat(carry): allocation targets and drift-band sleeve trades"
```

---

### Task 2: Carry backtest engine with lagged exits

**Files:**
- Create: `backtest/rotation/carry_engine.py`
- Test: `v2/tests/test_carry_engine.py`

**Interfaces:**
- Consumes: `carry_targets`, `sleeve_trades` (Task 1); `market_filter`, `daily_vol` from `v2.plugins.strategies.momentum_rotation.core`; `BacktestResult` dataclass from `backtest.rotation.engine`.
- Produces (used by Task 3/4):

```python
@dataclass
class CarryConfig:
    scheme: str = "inverse_vol"        # menu: {"equal", "inverse_vol"}
    ma_len: int = 200                  # fixed in menu; overridable for tests
    cap: float = 0.50
    drift_band: float = 0.20
    exposure: float = 1.0
    vol_lookback: int = 60
    fee_per_side: float = 0.00325
    slippage_per_side: float = 0.0005
    rebalance_weekday: int = 0         # Monday
    universe: tuple = ("BTC-USD", "ETH-USD", "SOL-USD")
    btc: str = "BTC-USD"               # master-gate anchor (may also be in universe)

class CarryBacktest:
    def __init__(self, bars: dict[str, pd.DataFrame], cfg: CarryConfig): ...
    def run(self, start: pd.Timestamp, end: pd.Timestamp) -> BacktestResult: ...
```

**Simulation rules (spec §2–§5; implement exactly):**
1. Iterate the master-anchor (`cfg.btc`) bar index from `start` to `end`; decisions on day D use bars ≤ D; trades at D's close.
2. **Start of each day: execute queued sells first** (they were queued on D-1) at D's close, charging `fee_per_side + slippage_per_side` on the notional. This is the 1-bar exit lag.
3. Daily gates from data ≤ D: `master = market_filter(btc closes, ma_len)`; per-asset `gate[s] = market_filter(s closes, ma_len)`. `None` counts as closed everywhere.
4. Daily exits: if `master is not True` → queue full sell of every held sleeve not already queued. Else, for each held `s` with `gate[s] is not True` → queue full sell of that sleeve.
5. Weekly entries: if `D.weekday() == rebalance_weekday` AND `master is True`: eligible = universe symbols with `gate[s] is True`; vols via `daily_vol(closes, vol_lookback, skip=0)`; `targets = carry_targets(vols, eligible, universe, scheme, cap, exposure)`; current weights from position values / equity(D); `deltas = sleeve_trades(current, targets, drift_band)`. Negative deltas → queue sell of that notional (lagged, rule 2). Positive deltas → buy at D's close (immediate), skipping any symbol with a queued sell, spending `min(delta*equity, cash)`, charging costs on the notional.
6. Cash earns 0; equity = cash + Σ units·price daily; `BacktestResult` fields exactly as the rotation engine produces them (`equity` starting at 1.0, `net_return`, `max_drawdown`, `n_trades`, `days_in_market`, `days_total`, `trades` list of {date, symbol, side, weight}).

- [ ] **Step 1: Write the failing tests**

```python
# v2/tests/test_carry_engine.py
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
        # AAA: 100 until day 249; 110 on days 250..309; 90 on day 310; 88 on 311+
        aaa = [100.0] * 250 + [110.0] * 60 + [90.0] + [88.0] * (len(IDX) - 311)
        return {"BTC-USD": bars_from(btc, IDX), "AAA-USD": bars_from(aaa, IDX)}

    def test_exact_equity_path(self):
        bars = self._world()
        # find the first Monday with index position >= 255 (MA fully at 110)
        monday = next(d for d in IDX[255:] if d.weekday() == 0)
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
        monday = next(d for d in IDX[255:] if d.weekday() == 0)
        res = CarryBacktest(bars, _cfg()).run(monday, IDX[-1])
        sells = [t for t in res.trades if t["side"] == "sell"]
        assert sells and sells[0]["date"] == IDX[301]               # lag applies
        tail = res.equity.loc[IDX[302]:]
        assert tail.nunique() == 1                                  # flat in cash

    def test_no_entries_while_any_gate_closed(self):
        bars = self._world()
        bars["AAA-USD"].loc[:, ["open", "high", "low", "close"]] = 50.0  # never above MA... constant == MA -> closed
        monday = next(d for d in IDX[255:] if d.weekday() == 0)
        res = CarryBacktest(bars, _cfg()).run(monday, IDX[-1])
        assert res.n_trades == 0
        assert res.days_in_market == 0

    def test_queued_sell_executes_even_if_gate_reopens(self):
        bars = self._world()
        # dip below MA for exactly one day then back above
        bars["AAA-USD"].loc[IDX[310], ["open", "high", "low", "close"]] = 90.0
        bars["AAA-USD"].loc[IDX[311]:, ["open", "high", "low", "close"]] = 111.0
        monday = next(d for d in IDX[255:] if d.weekday() == 0)
        res = CarryBacktest(bars, _cfg()).run(monday, IDX[-1])
        sells = [t for t in res.trades if t["side"] == "sell"]
        assert len(sells) == 1 and sells[0]["date"] == IDX[311]

    def test_no_lookahead_truncation_invariance(self):
        bars = self._world()
        monday = next(d for d in IDX[255:] if d.weekday() == 0)
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `conda run -n tradebot python -m pytest v2/tests/test_carry_engine.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backtest.rotation.carry_engine'`

- [ ] **Step 3: Write the implementation**

```python
# backtest/rotation/carry_engine.py
"""Daily-loop backtest for the regime-gated carry strategy.

Spec: docs/superpowers/specs/2026-07-28-regime-gated-carry-design.md
sections 2-5. Key mechanics: dual-layer 200d gates evaluated daily
("fast out"), entries only at the weekly rebalance ("slow in"), ALL
sells lagged one bar (manual-unstake handicap), no-redistribution
sleeve weights, cash earns zero. No staking yield is modeled anywhere.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from backtest.rotation.engine import BacktestResult
from v2.plugins.strategies.momentum_rotation.core import daily_vol, market_filter
from v2.plugins.strategies.regime_carry.core import carry_targets, sleeve_trades


@dataclass
class CarryConfig:
    scheme: str = "inverse_vol"
    ma_len: int = 200
    cap: float = 0.50
    drift_band: float = 0.20
    exposure: float = 1.0
    vol_lookback: int = 60
    fee_per_side: float = 0.00325
    slippage_per_side: float = 0.0005
    rebalance_weekday: int = 0
    universe: tuple = ("BTC-USD", "ETH-USD", "SOL-USD")
    btc: str = "BTC-USD"


class CarryBacktest:
    def __init__(self, bars: dict[str, pd.DataFrame], cfg: CarryConfig) -> None:
        self._bars = bars
        self._cfg = cfg

    def _closes_upto(self, symbol: str, day: pd.Timestamp) -> pd.Series:
        df = self._bars[symbol]
        return df.loc[df.index <= day, "close"]

    def run(self, start: pd.Timestamp, end: pd.Timestamp) -> BacktestResult:
        cfg = self._cfg
        cost = cfg.fee_per_side + cfg.slippage_per_side
        days = self._bars[cfg.btc].loc[start:end].index

        cash = 1.0
        pos: dict[str, float] = {}                 # symbol -> units
        queued_sells: dict[str, float] = {}        # symbol -> units to sell next bar
        trades: list[dict] = []
        equity_curve: dict[pd.Timestamp, float] = {}
        in_market_days = 0

        def price(sym: str, day: pd.Timestamp) -> float | None:
            s = self._closes_upto(sym, day)
            return float(s.iloc[-1]) if len(s) else None

        def equity(day: pd.Timestamp) -> float:
            total = cash
            for sym, units in pos.items():
                p = price(sym, day)
                if p is not None:
                    total += units * p
            return total

        for day in days:
            # 1) execute sells queued on the previous bar (exit lag)
            if queued_sells:
                eq_before = equity(day)
                for sym, units in list(queued_sells.items()):
                    p = price(sym, day)
                    if p is None:
                        continue
                    units = min(units, pos.get(sym, 0.0))
                    if units <= 0:
                        queued_sells.pop(sym)
                        continue
                    notional = units * p
                    cash += notional * (1.0 - cost)
                    pos[sym] = pos.get(sym, 0.0) - units
                    if pos[sym] <= 1e-12:
                        pos.pop(sym, None)
                    trades.append({"date": day, "symbol": sym, "side": "sell",
                                   "weight": notional / max(eq_before, 1e-12)})
                    queued_sells.pop(sym)

            # 2) gates from data <= day
            master = market_filter(self._closes_upto(cfg.btc, day), cfg.ma_len)
            gate = {
                s: market_filter(self._closes_upto(s, day), cfg.ma_len)
                for s in cfg.universe
            }

            # 3) daily exit signals -> queue for next bar
            for sym in list(pos):
                if master is not True or gate.get(sym) is not True:
                    if sym not in queued_sells:
                        queued_sells[sym] = pos[sym]

            # 4) weekly entries / drift rebalance
            if day.weekday() == cfg.rebalance_weekday and master is True:
                eligible = {s for s in cfg.universe if gate.get(s) is True}
                vols = {
                    s: daily_vol(self._closes_upto(s, day), cfg.vol_lookback, 0)
                    for s in cfg.universe
                }
                targets = carry_targets(
                    vols, eligible, cfg.universe, cfg.scheme, cfg.cap, cfg.exposure
                )
                eq = equity(day)
                current = {
                    s: (pos.get(s, 0.0) * (price(s, day) or 0.0)) / max(eq, 1e-12)
                    for s in cfg.universe
                }
                deltas = sleeve_trades(current, targets, cfg.drift_band)
                for sym, dw in deltas.items():
                    p = price(sym, day)
                    if p is None or abs(dw) < 1e-12:
                        continue
                    if dw < 0:
                        units = min((-dw) * eq / p, pos.get(sym, 0.0))
                        if units > 0 and sym not in queued_sells:
                            queued_sells[sym] = units
                    elif sym not in queued_sells:
                        notional = min(dw * eq, cash)
                        if notional > 0:
                            cash -= notional
                            pos[sym] = pos.get(sym, 0.0) + notional * (1.0 - cost) / p
                            trades.append({"date": day, "symbol": sym, "side": "buy",
                                           "weight": notional / max(eq, 1e-12)})

            if pos:
                in_market_days += 1
            equity_curve[day] = equity(day)

        eq_series = pd.Series(equity_curve).sort_index()
        peak = eq_series.cummax()
        max_dd = float(((peak - eq_series) / peak).max()) if len(eq_series) else 0.0
        return BacktestResult(
            equity=eq_series,
            net_return=float(eq_series.iloc[-1] - 1.0),
            max_drawdown=max_dd,
            n_trades=len(trades),
            days_in_market=in_market_days,
            days_total=len(days),
            trades=trades,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `conda run -n tradebot python -m pytest v2/tests/test_carry_engine.py -v` — all PASS. If the hand fixture fails, re-derive the arithmetic before touching anything; fix the engine, never the fixture numbers.
Run: `conda run -n tradebot python -m pytest v2/tests/ -q` — suite green.

- [ ] **Step 5: Commit**

```bash
git add backtest/rotation/carry_engine.py v2/tests/test_carry_engine.py
git commit -m "feat(carry): daily-loop backtest engine with lagged exits"
```

---

### Task 3: Fixed eras, two-config menu, exposure calibration

**Files:**
- Create: `backtest/rotation/carry_walkforward.py`
- Test: `v2/tests/test_carry_walkforward.py`

**Interfaces:**
- Consumes: `CarryBacktest`, `CarryConfig` (Task 2); `passes_bar`, `MAX_DD` from `backtest.rotation.walkforward`.
- Produces (used by Task 4):

```python
CARRY_MENU: list[CarryConfig]   # exactly [CarryConfig(scheme="equal"), CarryConfig(scheme="inverse_vol")]
FIT_START = pd.Timestamp("2017-01-01", tz="UTC")
FIT_END = pd.Timestamp("2025-01-25", tz="UTC")
VALIDATE_START = pd.Timestamp("2025-01-26", tz="UTC")

def carry_eras(index: pd.DatetimeIndex) -> dict[str, tuple[pd.Timestamp, pd.Timestamp]]
    # {"fit": (FIT_START, FIT_END), "validate": (VALIDATE_START, index[-1])}
    # ValueError if index[0] > FIT_START or index[-1] < VALIDATE_START.

def calibrate_exposure(bars, cfg: CarryConfig, fit: tuple, lo: float = 0.02,
                       hi: float = 1.0, target: tuple = (0.12, 0.14),
                       max_iter: int = 10) -> tuple[float, BacktestResult]
    # Bisection on exposure. If fit max DD at hi=1.0 is already < target[0],
    # return (1.0, result) — never lever up. Else bisect until DD in
    # [target[0], target[1]] or max_iter reached; return the last exposure
    # whose DD <= target[1] (conservative side) and its BacktestResult.
```

- [ ] **Step 1: Write the failing tests**

```python
# v2/tests/test_carry_walkforward.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `conda run -n tradebot python -m pytest v2/tests/test_carry_walkforward.py -v`
Expected: FAIL with ModuleNotFoundError.

- [ ] **Step 3: Write the implementation**

```python
# backtest/rotation/carry_walkforward.py
"""Fixed eras, closed two-config menu, and exposure calibration for the
regime-gated carry gauntlet (spec sections 7-8).

There is NO holdout era: forward paper trading is the holdout. The
validate era's contamination (its character was observed during
momentum-rotation testing) is disclosed in the spec.
"""
from __future__ import annotations

import pandas as pd

from backtest.rotation.carry_engine import CarryBacktest, CarryConfig
from backtest.rotation.engine import BacktestResult

FIT_START = pd.Timestamp("2017-01-01", tz="UTC")
FIT_END = pd.Timestamp("2025-01-25", tz="UTC")
VALIDATE_START = pd.Timestamp("2025-01-26", tz="UTC")

CARRY_MENU: list[CarryConfig] = [
    CarryConfig(scheme="equal"),
    CarryConfig(scheme="inverse_vol"),
]

DD_TARGET = (0.12, 0.14)


def carry_eras(index: pd.DatetimeIndex) -> dict[str, tuple[pd.Timestamp, pd.Timestamp]]:
    if index[0] > FIT_START:
        raise ValueError(f"data starts {index[0].date()} — need <= {FIT_START.date()}")
    if index[-1] < VALIDATE_START:
        raise ValueError(f"data ends {index[-1].date()} — need >= {VALIDATE_START.date()}")
    return {"fit": (FIT_START, FIT_END), "validate": (VALIDATE_START, index[-1])}


def calibrate_exposure(
    bars: dict[str, pd.DataFrame],
    cfg: CarryConfig,
    fit: tuple[pd.Timestamp, pd.Timestamp],
    lo: float = 0.02,
    hi: float = 1.0,
    target: tuple[float, float] = DD_TARGET,
    max_iter: int = 10,
) -> tuple[float, BacktestResult]:
    """Bisect exposure so fit-era max DD lands in `target`. Never levers up."""
    import dataclasses

    def run_at(expo: float) -> BacktestResult:
        return CarryBacktest(bars, dataclasses.replace(cfg, exposure=expo)).run(*fit)

    res_hi = run_at(hi)
    if res_hi.max_drawdown < target[0]:
        return hi, res_hi          # already tame at full exposure
    if res_hi.max_drawdown <= target[1]:
        return hi, res_hi          # already in window

    best: tuple[float, BacktestResult] | None = None
    for _ in range(max_iter):
        mid = (lo + hi) / 2.0
        res = run_at(mid)
        if res.max_drawdown > target[1]:
            hi = mid
        else:
            best = (mid, res)      # DD <= upper bound: candidate
            if res.max_drawdown >= target[0]:
                return mid, res    # in window
            lo = mid
    if best is None:
        # nothing under the cap found: return the lowest probe, conservative
        res_lo = run_at(lo)
        return lo, res_lo
    return best
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `conda run -n tradebot python -m pytest v2/tests/test_carry_walkforward.py -v` — all PASS.
Run: `conda run -n tradebot python -m pytest v2/tests/ -q` — suite green.

- [ ] **Step 5: Commit**

```bash
git add backtest/rotation/carry_walkforward.py v2/tests/test_carry_walkforward.py
git commit -m "feat(carry): fixed eras, two-config menu, exposure calibration"
```

---

### Task 4: Carry gauntlet runner CLI

**Files:**
- Create: `backtest/rotation/run_carry_gauntlet.py`
- Modify: `backtest/rotation/README.md` (append a "Carry gauntlet" section, text in Step 3)
- Test: `v2/tests/test_carry_runner.py`

**Interfaces:**
- Consumes: everything from Tasks 2–3; `DailyBarStore` from `backtest.rotation.data_store`; `passes_bar` from `backtest.rotation.walkforward`.
- Produces: `python -m backtest.rotation.run_carry_gauntlet --cache backtest/rotation/cache` and pure helper `pick_winner(rows: list[dict]) -> dict | None` (testable without backtests: rows are `{"cfg": CarryConfig, "exposure": float, "fit": BacktestResult, "validate": BacktestResult}`; winner = highest fit net among rows whose fit max DD ≤ 0.15; None if none qualify).

- [ ] **Step 1: Write the failing test**

```python
# v2/tests/test_carry_runner.py
import pandas as pd

from backtest.rotation.carry_engine import CarryConfig
from backtest.rotation.engine import BacktestResult
from backtest.rotation.run_carry_gauntlet import pick_winner


def _res(net, dd):
    return BacktestResult(equity=pd.Series([1.0, 1.0 + net]), net_return=net,
                          max_drawdown=dd, n_trades=1, days_in_market=1,
                          days_total=2, trades=[])


def _row(scheme, net, dd):
    return {"cfg": CarryConfig(scheme=scheme), "exposure": 0.5,
            "fit": _res(net, dd), "validate": _res(net / 2, dd / 2)}


class TestPickWinner:
    def test_higher_fit_net_wins(self):
        rows = [_row("equal", 0.20, 0.13), _row("inverse_vol", 0.30, 0.13)]
        assert pick_winner(rows)["cfg"].scheme == "inverse_vol"

    def test_dd_ineligible_excluded(self):
        rows = [_row("equal", 0.20, 0.13), _row("inverse_vol", 0.90, 0.40)]
        assert pick_winner(rows)["cfg"].scheme == "equal"

    def test_none_when_nothing_qualifies(self):
        rows = [_row("equal", 0.20, 0.30), _row("inverse_vol", 0.10, 0.40)]
        assert pick_winner(rows) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n tradebot python -m pytest v2/tests/test_carry_runner.py -v`
Expected: FAIL with ModuleNotFoundError.

- [ ] **Step 3: Write the implementation**

```python
# backtest/rotation/run_carry_gauntlet.py
"""Regime-gated carry gauntlet (spec 2026-07-28, sections 7-9).

Single-phase: calibrate exposure per config on the fit era, run fit +
validate, pick the winner, print the verdict against the pre-registered
pass bar. There is NO holdout phase — forward paper trading is the
holdout. Outputs land in backtest/rotation/output/ (gitignored; the
repo is public — never commit numeric results).

Usage:
  conda run -n tradebot python -m backtest.rotation.run_carry_gauntlet \
      --cache backtest/rotation/cache
"""
from __future__ import annotations

import argparse
import dataclasses
import json
from pathlib import Path

from backtest.rotation.carry_engine import CarryBacktest, CarryConfig
from backtest.rotation.carry_walkforward import (
    CARRY_MENU,
    calibrate_exposure,
    carry_eras,
)
from backtest.rotation.data_store import DailyBarStore
from backtest.rotation.walkforward import MAX_DD, passes_bar

OUT = Path("backtest/rotation/output")


def pick_winner(rows: list[dict]) -> dict | None:
    eligible = [r for r in rows if r["fit"].max_drawdown <= MAX_DD]
    if not eligible:
        return None
    return max(eligible, key=lambda r: r["fit"].net_return)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    args = ap.parse_args()

    store = DailyBarStore(args.cache)
    universe = CARRY_MENU[0].universe
    btc = CARRY_MENU[0].btc
    bars = {}
    for sym in set(universe) | {btc}:
        df = store.load(sym)
        if df is None:
            raise SystemExit(f"{sym} missing from cache")
        bars[sym] = df

    eras = carry_eras(bars[btc].index)
    OUT.mkdir(parents=True, exist_ok=True)

    rows = []
    for cfg in CARRY_MENU:
        expo, fit_res = calibrate_exposure(bars, cfg, eras["fit"])
        cfg_run = dataclasses.replace(cfg, exposure=expo)
        val_res = CarryBacktest(bars, cfg_run).run(*eras["validate"])
        rows.append({"cfg": cfg_run, "exposure": expo,
                     "fit": fit_res, "validate": val_res})
        print(f"{cfg.scheme:>12}: exposure {expo:.3f}  "
              f"fit net {fit_res.net_return:+.2%} DD {fit_res.max_drawdown:.2%}  "
              f"validate net {val_res.net_return:+.2%} DD {val_res.max_drawdown:.2%}")
        fit_res.equity.to_csv(OUT / f"carry_equity_fit_{cfg.scheme}.csv")
        val_res.equity.to_csv(OUT / f"carry_equity_validate_{cfg.scheme}.csv")

    winner = pick_winner(rows)
    if winner is None:
        print("VERDICT: FAIL — no config met the fit-era drawdown cap")
        return
    era_results = {"fit": winner["fit"], "validate": winner["validate"]}
    verdict = passes_bar(era_results)
    print(f"winner: {winner['cfg'].scheme}  exposure {winner['exposure']:.3f}")
    print(f"VERDICT: {'PASS' if verdict['pass'] else 'FAIL'}")
    for r in verdict["reasons"]:
        print(f"  - {r}")
    print("NOTE: spec criterion 3 (2018 + 2022 bear-leg avoidance) is a manual "
          "check — inspect carry_equity_fit_*.csv before treating PASS as final.")
    (OUT / "carry_result.json").write_text(json.dumps({
        "winner": winner["cfg"].scheme, "exposure": winner["exposure"],
        "fit": {"net": winner["fit"].net_return, "dd": winner["fit"].max_drawdown,
                "trades": winner["fit"].n_trades},
        "validate": {"net": winner["validate"].net_return,
                     "dd": winner["validate"].max_drawdown,
                     "trades": winner["validate"].n_trades},
        "pass": verdict["pass"], "reasons": verdict["reasons"],
    }, indent=2))


if __name__ == "__main__":
    main()
```

Append to `backtest/rotation/README.md`:

```markdown
## Carry gauntlet

Single-phase runner for the regime-gated carry strategy
(spec `docs/superpowers/specs/2026-07-28-regime-gated-carry-design.md`):

    conda run -n tradebot python -m backtest.rotation.run_carry_gauntlet \
        --cache backtest/rotation/cache

Eras are fixed dates (fit 2017-01-01 → 2025-01-25; validate 2025-01-26 →
last cached bar). Exposure is bisection-calibrated per config on the fit
era to a 12–14% max-DD window. There is NO holdout phase — forward paper
trading is the holdout. Bear-leg avoidance (2018, 2022) is a manual check
against `output/carry_equity_fit_*.csv`. Outputs are gitignored; never
commit numeric results.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `conda run -n tradebot python -m pytest v2/tests/test_carry_runner.py -v` — PASS.
Run: `conda run -n tradebot python -m backtest.rotation.run_carry_gauntlet --help` — imports cleanly, argparse works.
Run: `conda run -n tradebot python -m pytest v2/tests/ -q` — suite green.

- [ ] **Step 5: Commit**

```bash
git add backtest/rotation/run_carry_gauntlet.py backtest/rotation/README.md v2/tests/test_carry_runner.py
git commit -m "feat(carry): gauntlet runner with pre-registered pass bar"
```

---

### Task 5: Data refresh + gauntlet execution (operational)

No new code. The human/controller drives it.

- [ ] **Step 1: Top up the cache.** BTC/ETH/SOL daily bars exist through 2026-07-26. Refresh to the latest completed bar: for each of the three symbols call `store.top_up_from_rest(symbol, pair)` (pairs via `v2.utils.symbol_mapper.KrakenSymbolMapper().to_kraken_rest`, e.g. BTC-USD → XBTUSD). The partial-candle guard (F2 fix) is already in `_fetch_rest_ohlc`.

- [ ] **Step 2: Sanity check.** Each of the three symbols: last bar within 2 days of today, no gap > 3 days in the trailing year, BTC history reaches back before 2017-01-01.

- [ ] **Step 3: Run the gauntlet** (`python -m backtest.rotation.run_carry_gauntlet --cache backtest/rotation/cache` under the tradebot env). It is single-phase and idempotent — no holdout to protect.

- [ ] **Step 4: Criterion-3 manual check.** Load the winner's `carry_equity_fit_*.csv`; verify flat/near-flat equity through the 2018 and 2022 bear windows (the same check pattern used for the rotation: % of flat days + segment drawdown).

- [ ] **Step 5: Record the verdict** in the private memory dir (new file `carry_gauntlet_2026-07.md`): winner scheme, calibrated exposure, per-era net/DD/trades, criterion-3 evidence, PASS/FAIL against spec §7. PASS → write Plan 2 (live plugin + paper deploy). FAIL → stop per spec; no rescue.

---

## Self-Review Notes (completed)

- **Spec coverage:** §2 gates → Task 2 rules 3–4; §3 allocation/no-redistribution/drift → Task 1 + engine rule 5; §4 daily-fast-out/weekly-slow-in + exit lag → engine rules 2–5 (lag applied to ALL sells, a disclosed conservative superset); §5 exposure calibration → Task 3 `calibrate_exposure`; §7 eras/honesty/pass bar → Tasks 3–5 (no yield anywhere; criterion-3 manual step); §8 menu → `CARRY_MENU` + closure test. NOT in this plan (Plan 2, gated on PASS): §5 risk rails in the live chain, §6 live staking alerts/yield accrual, §9 deployment, §10 live plugin/poller/observer/dashboard/parity items.
- **Type consistency:** `carry_targets(vols, eligible, universe, scheme, cap, exposure)` matches between Task 1 tests/impl and Task 2 engine call. `CarryConfig` fields consumed by Tasks 3–4 (`scheme`, `exposure`, `universe`, `btc`, `ma_len`, `cap`, `drift_band`) all exist in Task 2's dataclass. `BacktestResult` is reused from `backtest.rotation.engine`, fields referenced (`net_return`, `max_drawdown`, `equity`, `n_trades`) all exist. `passes_bar`/`MAX_DD` imported from the existing `walkforward` module (already merged to main).
- **Known modeling choices stated in-plan:** sells always lagged (superset of spec); queued sells execute even on gate-reopen; buys skip symbols with queued sells.
```
