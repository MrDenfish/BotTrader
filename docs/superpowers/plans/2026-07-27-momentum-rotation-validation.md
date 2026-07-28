# Momentum Rotation — Plan 1: Validation Foundation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the rotation core library, daily-bar data store, portfolio backtester, and walk-forward gauntlet runner, producing a pass/fail verdict against the spec's §7 bar.

**Architecture:** Pure strategy math lives in `v2/plugins/strategies/momentum_rotation/core.py` (no I/O, no event bus — the future live plugin and the backtester both import it). The backtester is a new self-contained package `backtest/rotation/` (daily-bar portfolio simulation is a different shape from the v1 5-minute candle engine; do not extend `backtest/engine.py`). Data comes from Kraken bulk OHLCVT CSVs (5+ years) plus Kraken public REST OHLC (interval=1440, max ~720 bars) to top up the recent gap.

**Tech Stack:** Python 3.11, pandas + numpy (already project deps), pytest, requests (already a dep) for REST. No new dependencies — cache format is CSV, not parquet.

**Spec:** `docs/superpowers/specs/2026-07-27-momentum-rotation-design.md` (§3 universe, §4 signal, §5 gate, §6 costs, §7 validation protocol, §12 parameter menu).

## Global Constraints

- Parameter menu is CLOSED (spec §12): `L ∈ {30, 60, 90}`, `skip ∈ {2, 3}`, `band B ∈ {6, 8}`, volume floor ∈ {$5M, $10M}, `K = 4`, position cap 30%, exposure scalar (drawdown-matched), chase count N (live-only, not in this plan). Nothing else may be swept.
- Costs in every backtest: 0.325% fee + 0.05% slippage **per side** (= 0.65% + 0.10% round trip, spec §6).
- Repo is PUBLIC: numeric backtest results must never be committed. All gauntlet output goes to `backtest/rotation/output/` which is gitignored in Task 6. Result summaries go to the private memory dir only.
- Never put `@registry.plugin` in an `__init__.py` (project rule). This plan adds no registry plugins at all — `core.py` is a plain module.
- Full existing test suite must stay green: `python -m pytest v2/tests/ -q` before every commit.
- All commits on branch `feature/momentum-rotation`. Commit messages follow existing conventions (`feat:`, `test:`, `docs:`).
- Timestamps: all daily bars are UTC; a bar's timestamp is the UTC open of its day. "Rebalance Monday 00:00 UTC" means: act on the ranking computed from bars up to and including Sunday's completed bar.

---

### Task 1: Rotation core — momentum score and raw return

**Files:**
- Create: `v2/plugins/strategies/momentum_rotation/__init__.py` (empty file)
- Create: `v2/plugins/strategies/momentum_rotation/core.py`
- Test: `v2/tests/test_strategies/test_momentum_rotation_core.py`

**Interfaces:**
- Consumes: nothing (pure functions).
- Produces (used by Tasks 2, 3, 4, and the engine in Task 5):
  - `momentum_score(closes: pd.Series, lookback: int, skip: int) -> float | None`
  - `raw_return(closes: pd.Series, lookback: int, skip: int) -> float | None`
  - `daily_vol(closes: pd.Series, lookback: int, skip: int) -> float | None`

Semantics: with `closes` ordered oldest→newest, the measurement window ends `skip` bars before the last bar. `raw_return = closes.iloc[-1-skip] / closes.iloc[-1-skip-lookback] - 1`. `daily_vol` = standard deviation (ddof=1) of daily pct changes within that same window. `momentum_score = raw_return / daily_vol`. All three return `None` when the series is too short (`len < lookback + skip + 1`) or when vol is 0/NaN.

- [ ] **Step 1: Write the failing tests**

```python
# v2/tests/test_strategies/test_momentum_rotation_core.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest v2/tests/test_strategies/test_momentum_rotation_core.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'v2.plugins.strategies.momentum_rotation'`

- [ ] **Step 3: Write the implementation**

Create empty `v2/plugins/strategies/momentum_rotation/__init__.py`, then:

```python
# v2/plugins/strategies/momentum_rotation/core.py
"""Pure rotation math — no I/O, no event bus.

Shared by the backtester (backtest/rotation/) and the future live
momentum_rotation strategy plugin. Everything operates on pandas Series
of daily closes ordered oldest -> newest.

Window convention: the measurement window ends `skip` bars before the
most recent bar (spec section 4: skip the most recent days to avoid
buying immediate spikes).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _window(closes: pd.Series, lookback: int, skip: int) -> pd.Series | None:
    """Return the lookback+1 closes ending `skip` bars before the end."""
    needed = lookback + skip + 1
    if len(closes) < needed:
        return None
    end = len(closes) - skip
    return closes.iloc[end - lookback - 1 : end]


def raw_return(closes: pd.Series, lookback: int, skip: int) -> float | None:
    w = _window(closes, lookback, skip)
    if w is None or w.iloc[0] <= 0:
        return None
    return float(w.iloc[-1] / w.iloc[0] - 1.0)


def daily_vol(closes: pd.Series, lookback: int, skip: int) -> float | None:
    w = _window(closes, lookback, skip)
    if w is None:
        return None
    changes = w.pct_change().dropna()
    if len(changes) < 2:
        return None
    v = float(changes.std(ddof=1))
    return v if np.isfinite(v) else None


def momentum_score(closes: pd.Series, lookback: int, skip: int) -> float | None:
    r = raw_return(closes, lookback, skip)
    v = daily_vol(closes, lookback, skip)
    if r is None or v is None or v == 0.0:
        return None
    return r / v
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest v2/tests/test_strategies/test_momentum_rotation_core.py -v`
Expected: all PASS. Also run `python -m pytest v2/tests/ -q` — existing suite stays green.

- [ ] **Step 5: Commit**

```bash
git add v2/plugins/strategies/momentum_rotation/ v2/tests/test_strategies/test_momentum_rotation_core.py
git commit -m "feat(rotation): momentum score core functions"
```

---

### Task 2: Rotation core — selection with hysteresis and inverse-vol weights

**Files:**
- Modify: `v2/plugins/strategies/momentum_rotation/core.py` (append)
- Test: `v2/tests/test_strategies/test_momentum_rotation_core.py` (append)

**Interfaces:**
- Consumes: nothing new.
- Produces (used by engine, Task 5):
  - `select_holdings(ranked: list[str], current: list[str], k: int = 4, band: int = 8) -> list[str]`
  - `inverse_vol_weights(vols: dict[str, float], cap: float = 0.30) -> dict[str, float]`

`select_holdings` semantics (spec §4): `ranked` is symbols best-first, already filtered to *eligible* names (universe + positive raw return). Keep a current holding while it appears within the top `band` ranks. Fill remaining slots (up to `k`) with the best-ranked non-held symbols. Result order: ranked order. A current holding absent from `ranked` entirely (ineligible/gate-failed) is dropped.

`inverse_vol_weights` semantics (spec §6): weights ∝ 1/vol, normalized to sum ≤ 1.0, each capped at `cap`; excess weight from capping is redistributed to uncapped names iteratively; when everything is capped, the remainder is implicitly cash (sum < 1). Symbols with vol ≤ 0 or None are excluded.

- [ ] **Step 1: Write the failing tests**

Append to `v2/tests/test_strategies/test_momentum_rotation_core.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest v2/tests/test_strategies/test_momentum_rotation_core.py -v -k "SelectHoldings or InverseVol"`
Expected: FAIL with `ImportError: cannot import name 'select_holdings'`

- [ ] **Step 3: Write the implementation**

Append to `core.py`:

```python
def select_holdings(
    ranked: list[str], current: list[str], k: int = 4, band: int = 8
) -> list[str]:
    """Top-k selection with a hold band to bound churn (spec section 4)."""
    rank_of = {s: i + 1 for i, s in enumerate(ranked)}
    kept = [s for s in ranked if s in current and rank_of[s] <= band][:k]
    slots = k - len(kept)
    fresh = [s for s in ranked if s not in kept][:slots]
    return sorted(kept + fresh, key=lambda s: rank_of[s])


def inverse_vol_weights(
    vols: dict[str, float | None], cap: float = 0.30
) -> dict[str, float]:
    """Inverse-volatility weights, per-position cap, excess left as cash."""
    clean = {s: v for s, v in vols.items() if v is not None and v > 0}
    if not clean:
        return {}
    raw = {s: 1.0 / v for s, v in clean.items()}
    total = sum(raw.values())
    weights = {s: r / total for s, r in raw.items()}

    # Iteratively cap and redistribute among uncapped names.
    capped: set[str] = set()
    while True:
        over = [s for s in weights if s not in capped and weights[s] > cap]
        if not over:
            break
        for s in over:
            weights[s] = cap
            capped.add(s)
        free = [s for s in weights if s not in capped]
        if not free:
            break  # everything capped; remainder is cash
        budget = 1.0 - cap * len(capped)
        free_raw = sum(raw[s] for s in free)
        for s in free:
            weights[s] = min(cap, budget * raw[s] / free_raw) if free_raw else 0.0
    return weights
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest v2/tests/test_strategies/test_momentum_rotation_core.py -v`
Expected: all PASS (Task 1 tests included).

- [ ] **Step 5: Commit**

```bash
git add v2/plugins/strategies/momentum_rotation/core.py v2/tests/test_strategies/test_momentum_rotation_core.py
git commit -m "feat(rotation): holdings selection with hysteresis and inverse-vol weights"
```

---

### Task 3: Rotation core — regime gate

**Files:**
- Modify: `v2/plugins/strategies/momentum_rotation/core.py` (append)
- Test: `v2/tests/test_strategies/test_momentum_rotation_core.py` (append)

**Interfaces:**
- Produces (used by engine, Task 5):
  - `market_filter(btc_closes: pd.Series, ma_len: int = 200) -> bool | None` — True = risk-on (last close strictly above the `ma_len`-day simple moving average of `btc_closes`). None if fewer than `ma_len` bars (callers must treat None as risk-OFF).

The absolute-momentum floor (spec §5 condition 2) needs no new function — the engine filters candidates by `raw_return(...) > 0` (Task 1).

- [ ] **Step 1: Write the failing tests**

Append:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest v2/tests/test_strategies/test_momentum_rotation_core.py -v -k MarketFilter`
Expected: FAIL with ImportError.

- [ ] **Step 3: Write the implementation**

Append to `core.py`:

```python
def market_filter(btc_closes: pd.Series, ma_len: int = 200) -> bool | None:
    """BTC close vs its long SMA. True = risk-on. None = not enough history.

    Callers MUST treat None as risk-off (spec section 5).
    """
    if len(btc_closes) < ma_len:
        return None
    ma = float(btc_closes.iloc[-ma_len:].mean())
    return bool(float(btc_closes.iloc[-1]) > ma)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest v2/tests/test_strategies/test_momentum_rotation_core.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add v2/plugins/strategies/momentum_rotation/core.py v2/tests/test_strategies/test_momentum_rotation_core.py
git commit -m "feat(rotation): market regime filter"
```

---

### Task 4: Daily-bar data store (bulk CSV import + REST top-up + universe screens)

**Files:**
- Create: `backtest/rotation/__init__.py` (empty)
- Create: `backtest/rotation/data_store.py`
- Create: `backtest/rotation/universe.py`
- Create: `backtest/rotation/output/.gitkeep` and add `backtest/rotation/output/*` (except `.gitkeep`) + `backtest/rotation/cache/` to `.gitignore`
- Test: `v2/tests/test_rotation_data.py`

**Interfaces:**
- Produces (used by Tasks 5–6):
  - `class DailyBarStore:`
    - `__init__(self, cache_dir: str | Path)`
    - `import_ohlcvt_csv(self, symbol: str, csv_path: str | Path) -> int` — import a Kraken bulk OHLCVT file (headerless CSV: `timestamp,open,high,low,close,volume,trades`, timestamp = unix seconds); merges into the symbol's cache; returns row count after merge.
    - `top_up_from_rest(self, symbol: str, kraken_pair: str) -> int` — fetch `https://api.kraken.com/0/public/OHLC?pair={kraken_pair}&interval=1440`, merge rows newer than the cache tail, return rows added. (Kraken returns ≤ ~720 daily bars — this fills the gap after a quarterly bulk file, it can NOT replace it.)
    - `load(self, symbol: str) -> pd.DataFrame | None` — DataFrame indexed by UTC date with columns `open, high, low, close, volume`, sorted ascending, deduped. None if no cache.
    - `symbols(self) -> list[str]`
  - `eligible_symbols(bars: dict[str, pd.DataFrame], asof: pd.Timestamp, volume_floor: float, min_age_days: int = 180, top_n: int = 25, vol_window: int = 30) -> list[str]` in `universe.py` — screens per spec §3, evaluated with data up to and including `asof` only (no lookahead): dollar volume = trailing `vol_window`-day median of `volume * close` ≥ `volume_floor`; age = `asof - first bar date ≥ min_age_days`; returns up to `top_n` symbols best-ranked by that dollar volume. Spread screen is live-only (spec §3) and deliberately absent here.

Cache layout: one CSV per symbol at `{cache_dir}/{symbol}.csv` with header `date,open,high,low,close,volume`, symbol in v2 notation (`BTC-USD`). Mapping to Kraken pair codes for REST uses `v2.utils.symbol_mapper` where needed by the caller — the store itself takes the pair code as an argument and does no mapping.

- [ ] **Step 1: Write the failing tests**

```python
# v2/tests/test_rotation_data.py
import pandas as pd
import pytest

from backtest.rotation.data_store import DailyBarStore
from backtest.rotation.universe import eligible_symbols


@pytest.fixture
def store(tmp_path):
    return DailyBarStore(tmp_path)


def _write_ohlcvt(path, rows):
    # Kraken bulk format: headerless ts,o,h,l,c,vol,trades
    with open(path, "w") as f:
        for r in rows:
            f.write(",".join(str(x) for x in r) + "\n")


DAY = 86400
T0 = 1577836800  # 2020-01-01 00:00 UTC


class TestDailyBarStore:
    def test_import_and_load(self, store, tmp_path):
        p = tmp_path / "XBTUSD_1440.csv"
        _write_ohlcvt(p, [
            (T0, 100, 110, 90, 105, 12.5, 300),
            (T0 + DAY, 105, 115, 100, 112, 9.1, 250),
        ])
        n = store.import_ohlcvt_csv("BTC-USD", p)
        assert n == 2
        df = store.load("BTC-USD")
        assert list(df.columns) == ["open", "high", "low", "close", "volume"]
        assert len(df) == 2
        assert df.index[0] == pd.Timestamp("2020-01-01", tz="UTC")
        assert df["close"].iloc[1] == 112

    def test_reimport_dedupes(self, store, tmp_path):
        p = tmp_path / "a.csv"
        _write_ohlcvt(p, [(T0, 1, 1, 1, 1, 1, 1)])
        store.import_ohlcvt_csv("BTC-USD", p)
        assert store.import_ohlcvt_csv("BTC-USD", p) == 1

    def test_load_missing_returns_none(self, store):
        assert store.load("NOPE-USD") is None

    def test_top_up_merges_only_newer(self, store, tmp_path, monkeypatch):
        p = tmp_path / "a.csv"
        _write_ohlcvt(p, [(T0, 100, 110, 90, 105, 12.5, 300)])
        store.import_ohlcvt_csv("BTC-USD", p)

        def fake_fetch(self, pair):
            # Kraken REST rows: [time, open, high, low, close, vwap, volume, count]
            return [
                [T0, "999", "999", "999", "999", "0", "999", 1],       # older/equal: ignored
                [T0 + DAY, "105", "115", "100", "112", "0", "9.1", 2], # newer: added
            ]

        monkeypatch.setattr(DailyBarStore, "_fetch_rest_ohlc", fake_fetch)
        added = store.top_up_from_rest("BTC-USD", "XBTUSD")
        assert added == 1
        df = store.load("BTC-USD")
        assert len(df) == 2
        assert df["close"].iloc[0] == 105  # bulk row NOT overwritten


class TestEligibleSymbols:
    def _bars(self, n_days, close, volume, end="2024-06-30"):
        idx = pd.date_range(end=end, periods=n_days, freq="D", tz="UTC")
        return pd.DataFrame(
            {"open": close, "high": close, "low": close, "close": close, "volume": volume},
            index=idx,
        )

    def test_volume_floor_and_age(self):
        bars = {
            "BIG-USD": self._bars(400, close=10.0, volume=2_000_000),   # $20M/day
            "THIN-USD": self._bars(400, close=1.0, volume=1_000_000),   # $1M/day
            "YOUNG-USD": self._bars(90, close=10.0, volume=2_000_000),  # too new
        }
        got = eligible_symbols(bars, pd.Timestamp("2024-06-30", tz="UTC"),
                               volume_floor=10_000_000)
        assert got == ["BIG-USD"]

    def test_no_lookahead(self):
        # Volume explodes AFTER asof; must not count
        b = self._bars(400, close=1.0, volume=1_000)
        b.loc[b.index > pd.Timestamp("2024-01-01", tz="UTC"), "volume"] = 1e9
        got = eligible_symbols({"X-USD": b}, pd.Timestamp("2024-01-01", tz="UTC"),
                               volume_floor=10_000_000)
        assert got == []

    def test_top_n_ranked_by_dollar_volume(self):
        bars = {f"S{i}-USD": self._bars(400, 10.0, (i + 2) * 1_000_000) for i in range(30)}
        got = eligible_symbols(bars, pd.Timestamp("2024-06-30", tz="UTC"),
                               volume_floor=5_000_000, top_n=3)
        assert got == ["S29-USD", "S28-USD", "S27-USD"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest v2/tests/test_rotation_data.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backtest.rotation'`

- [ ] **Step 3: Write the implementation**

Create empty `backtest/rotation/__init__.py`, then:

```python
# backtest/rotation/data_store.py
"""Daily-bar cache: Kraken bulk OHLCVT CSVs + REST top-up.

Kraken's REST OHLC endpoint returns at most ~720 bars, so multi-year
history MUST come from the quarterly bulk OHLCVT export
(support.kraken.com, "Downloadable historical OHLCVT data"); REST only
fills the tail since the last export.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
import requests

logger = logging.getLogger(__name__)

_COLS = ["open", "high", "low", "close", "volume"]
_REST_URL = "https://api.kraken.com/0/public/OHLC"


class DailyBarStore:
    def __init__(self, cache_dir: str | Path) -> None:
        self._dir = Path(cache_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, symbol: str) -> Path:
        return self._dir / f"{symbol}.csv"

    # -- loading ---------------------------------------------------------
    def load(self, symbol: str) -> pd.DataFrame | None:
        p = self._path(symbol)
        if not p.exists():
            return None
        df = pd.read_csv(p, parse_dates=["date"], index_col="date")
        df.index = pd.DatetimeIndex(df.index, tz="UTC") if df.index.tz is None else df.index
        return df[_COLS].sort_index()

    def symbols(self) -> list[str]:
        return sorted(p.stem for p in self._dir.glob("*.csv"))

    def _save(self, symbol: str, df: pd.DataFrame) -> None:
        out = df[~df.index.duplicated(keep="first")].sort_index()
        out.to_csv(self._path(symbol), index_label="date")

    # -- bulk import -----------------------------------------------------
    def import_ohlcvt_csv(self, symbol: str, csv_path: str | Path) -> int:
        raw = pd.read_csv(
            csv_path, header=None,
            names=["ts", "open", "high", "low", "close", "volume", "trades"],
        )
        df = raw.assign(
            date=pd.to_datetime(raw["ts"], unit="s", utc=True).dt.normalize()
        ).set_index("date")[_COLS]
        existing = self.load(symbol)
        merged = df if existing is None else pd.concat([existing, df])
        merged = merged[~merged.index.duplicated(keep="first")].sort_index()
        self._save(symbol, merged)
        return len(merged)

    # -- REST top-up -----------------------------------------------------
    def _fetch_rest_ohlc(self, kraken_pair: str) -> list[list]:
        resp = requests.get(
            _REST_URL, params={"pair": kraken_pair, "interval": 1440}, timeout=30
        )
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("error"):
            raise RuntimeError(f"Kraken OHLC error for {kraken_pair}: {payload['error']}")
        result = payload["result"]
        key = next(k for k in result if k != "last")
        return result[key]

    def top_up_from_rest(self, symbol: str, kraken_pair: str) -> int:
        rows = self._fetch_rest_ohlc(kraken_pair)
        df = pd.DataFrame(
            rows, columns=["ts", "open", "high", "low", "close", "vwap", "volume", "count"]
        )
        for c in ("open", "high", "low", "close", "volume"):
            df[c] = df[c].astype(float)
        df["date"] = pd.to_datetime(df["ts"], unit="s", utc=True).dt.normalize()
        df = df.set_index("date")[_COLS]
        existing = self.load(symbol)
        if existing is not None:
            df = df[df.index > existing.index.max()]
            merged = pd.concat([existing, df])
        else:
            merged = df
        self._save(symbol, merged)
        logger.info("top_up %s: +%d rows", symbol, len(df))
        return len(df)
```

```python
# backtest/rotation/universe.py
"""Historical universe screens (spec section 3), point-in-time safe.

The spread screen is live-only: historical spreads are unavailable, and
the volume floor + listing age are the binding quality bars (documented
survivorship note in spec section 7).
"""
from __future__ import annotations

import pandas as pd


def eligible_symbols(
    bars: dict[str, pd.DataFrame],
    asof: pd.Timestamp,
    volume_floor: float,
    min_age_days: int = 180,
    top_n: int = 25,
    vol_window: int = 30,
) -> list[str]:
    scored: list[tuple[float, str]] = []
    for sym, df in bars.items():
        hist = df[df.index <= asof]
        if hist.empty:
            continue
        age = (asof - hist.index[0]).days
        if age < min_age_days:
            continue
        window = hist.tail(vol_window)
        dollar_vol = float((window["volume"] * window["close"]).median())
        if dollar_vol < volume_floor:
            continue
        scored.append((dollar_vol, sym))
    scored.sort(reverse=True)
    return [sym for _, sym in scored[:top_n]]
```

Add to `.gitignore`:

```
backtest/rotation/cache/
backtest/rotation/output/*
!backtest/rotation/output/.gitkeep
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest v2/tests/test_rotation_data.py -v` — all PASS.
Run: `python -m pytest v2/tests/ -q` — suite green.

- [ ] **Step 5: Commit**

```bash
git add backtest/rotation/ v2/tests/test_rotation_data.py .gitignore
git commit -m "feat(rotation): daily-bar store with bulk OHLCVT import, REST top-up, universe screens"
```

---

### Task 5: Portfolio backtest engine

**Files:**
- Create: `backtest/rotation/engine.py`
- Test: `v2/tests/test_rotation_engine.py`

**Interfaces:**
- Consumes: `momentum_score`, `raw_return`, `daily_vol`, `select_holdings`, `inverse_vol_weights`, `market_filter` from `v2.plugins.strategies.momentum_rotation.core`; `eligible_symbols` from `backtest.rotation.universe`.
- Produces (used by Task 6):

```python
@dataclass
class RotationConfig:
    lookback: int = 60          # L, menu {30, 60, 90}
    skip: int = 2               # menu {2, 3}
    band: int = 8               # B, menu {6, 8}
    k: int = 4
    cap: float = 0.30
    exposure: float = 1.0       # portfolio scalar, drawdown-matched in Task 6
    volume_floor: float = 10e6  # menu {5e6, 10e6}
    fee_per_side: float = 0.00325
    slippage_per_side: float = 0.0005
    ma_len: int = 200
    rebalance_weekday: int = 0  # Monday
    min_age_days: int = 180
    top_n: int = 25

@dataclass
class BacktestResult:
    equity: pd.Series           # daily, starts at 1.0
    net_return: float           # equity[-1] - 1
    max_drawdown: float         # positive fraction, e.g. 0.14
    n_trades: int               # count of position entries + exits
    days_in_market: int
    days_total: int
    trades: list[dict]          # {date, symbol, side, weight}

class RotationBacktest:
    def __init__(self, bars: dict[str, pd.DataFrame], btc_symbol: str, cfg: RotationConfig): ...
    def run(self, start: pd.Timestamp, end: pd.Timestamp) -> BacktestResult: ...
```

**Simulation rules (all from spec §4–§6 — implement exactly):**
1. Iterate calendar days from `start` to `end`. Decisions on day D use bars with index ≤ D only (bar D is that day's completed close; trades execute at bar D's close ± costs).
2. Daily: compute `market_filter` on the BTC series up to D. If not True (False or None) and holdings exist → sell everything at D's close, each sale charged `fee_per_side + slippage_per_side`. No entries while risk-off ("fast out").
3. If D is `rebalance_weekday` and filter is True ("slow in"): build universe via `eligible_symbols(bars, D, volume_floor, ...)`; candidates = universe symbols with `raw_return > 0` (absolute-momentum floor) and a non-None score; rank by `momentum_score` descending; `select_holdings(ranked, current, k, band)`; target weights = `inverse_vol_weights({s: daily_vol(s)...}, cap) * exposure`.
4. Trade the weight delta: for each symbol whose target differs from current weight by more than 0.5% of equity, trade the difference; each traded notional is charged `fee_per_side + slippage_per_side`. Position values drift with daily close-to-close returns between rebalances.
5. Cash earns 0. Equity = cash + Σ position values, tracked daily; max drawdown from the running peak of the equity series.

- [ ] **Step 1: Write the failing tests**

```python
# v2/tests/test_rotation_engine.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest v2/tests/test_rotation_engine.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backtest.rotation.engine'`

- [ ] **Step 3: Write the implementation**

```python
# backtest/rotation/engine.py
"""Daily-loop portfolio backtest for the momentum rotation strategy.

Implements spec sections 4-6 exactly: daily 'fast out' gate check,
weekly 'slow in' rebalance, per-side fee + slippage on every traded
notional, cash earns zero.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from backtest.rotation.universe import eligible_symbols
from v2.plugins.strategies.momentum_rotation.core import (
    daily_vol,
    inverse_vol_weights,
    market_filter,
    momentum_score,
    raw_return,
    select_holdings,
)


@dataclass
class RotationConfig:
    lookback: int = 60
    skip: int = 2
    band: int = 8
    k: int = 4
    cap: float = 0.30
    exposure: float = 1.0
    volume_floor: float = 10e6
    fee_per_side: float = 0.00325
    slippage_per_side: float = 0.0005
    ma_len: int = 200
    rebalance_weekday: int = 0
    min_age_days: int = 180
    top_n: int = 25


@dataclass
class BacktestResult:
    equity: pd.Series
    net_return: float
    max_drawdown: float
    n_trades: int
    days_in_market: int
    days_total: int
    trades: list[dict] = field(default_factory=list)


class RotationBacktest:
    def __init__(
        self, bars: dict[str, pd.DataFrame], btc_symbol: str, cfg: RotationConfig
    ) -> None:
        self._bars = bars
        self._btc = btc_symbol
        self._cfg = cfg

    def _closes_upto(self, symbol: str, day: pd.Timestamp) -> pd.Series:
        df = self._bars[symbol]
        return df.loc[df.index <= day, "close"]

    def run(self, start: pd.Timestamp, end: pd.Timestamp) -> BacktestResult:
        cfg = self._cfg
        cost = cfg.fee_per_side + cfg.slippage_per_side
        days = self._bars[self._btc].loc[start:end].index

        cash = 1.0
        pos: dict[str, float] = {}       # symbol -> units
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

        def sell(sym: str, units: float, day: pd.Timestamp) -> None:
            nonlocal cash
            p = price(sym, day)
            if p is None:
                return
            notional = units * p
            cash += notional * (1.0 - cost)
            trades.append({"date": day, "symbol": sym, "side": "sell",
                           "weight": notional / max(equity(day), 1e-12)})

        def buy(sym: str, notional: float, day: pd.Timestamp) -> None:
            nonlocal cash
            p = price(sym, day)
            if p is None or notional <= 0 or notional > cash:
                return
            cash -= notional
            pos[sym] = pos.get(sym, 0.0) + (notional * (1.0 - cost)) / p
            trades.append({"date": day, "symbol": sym, "side": "buy",
                           "weight": notional / max(equity(day), 1e-12)})

        for day in days:
            risk_on = market_filter(self._closes_upto(self._btc, day), cfg.ma_len)

            # Fast out: any non-True gate liquidates immediately.
            if risk_on is not True and pos:
                for sym in list(pos):
                    sell(sym, pos.pop(sym), day)

            # Slow in: entries/rotation only on rebalance day while risk-on.
            if risk_on is True and day.weekday() == cfg.rebalance_weekday:
                universe = eligible_symbols(
                    self._bars, day, cfg.volume_floor,
                    min_age_days=cfg.min_age_days, top_n=cfg.top_n,
                )
                scores: dict[str, float] = {}
                for sym in universe:
                    closes = self._closes_upto(sym, day)
                    rr = raw_return(closes, cfg.lookback, cfg.skip)
                    sc = momentum_score(closes, cfg.lookback, cfg.skip)
                    if rr is not None and rr > 0 and sc is not None:
                        scores[sym] = sc
                ranked = sorted(scores, key=scores.get, reverse=True)
                target_syms = select_holdings(ranked, list(pos), cfg.k, cfg.band)
                vols = {
                    s: daily_vol(self._closes_upto(s, day), cfg.lookback, cfg.skip)
                    for s in target_syms
                }
                weights = {
                    s: w * cfg.exposure
                    for s, w in inverse_vol_weights(vols, cfg.cap).items()
                }

                eq = equity(day)
                # Sells first (including full exits), then buys with freed cash.
                for sym in list(pos):
                    tgt = weights.get(sym, 0.0) * eq
                    cur = pos[sym] * (price(sym, day) or 0.0)
                    if cur - tgt > 0.005 * eq:
                        units = (cur - tgt) / (price(sym, day) or 1.0)
                        pos[sym] -= units
                        if pos[sym] * (price(sym, day) or 0.0) < 1e-9:
                            del pos[sym]
                        sell_units = units
                        # re-add to execute the sale via sell() bookkeeping
                        cash_before = cash
                        p = price(sym, day)
                        cash += sell_units * p * (1.0 - cost)
                        trades.append({"date": day, "symbol": sym, "side": "sell",
                                       "weight": (sell_units * p) / max(eq, 1e-12)})
                        assert cash > cash_before
                for sym, w in weights.items():
                    tgt = w * eq
                    cur = pos.get(sym, 0.0) * (price(sym, day) or 0.0)
                    if tgt - cur > 0.005 * eq:
                        buy(sym, min(tgt - cur, cash), day)

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

Run: `python -m pytest v2/tests/test_rotation_engine.py -v`
Expected: all PASS. If `test_no_lookahead_smoke` fails, there is a lookahead bug — fix the engine, never the test. Run `python -m pytest v2/tests/ -q` — suite green.

- [ ] **Step 5: Refactor check**

The rebalance sell-branch above does its own cash bookkeeping (a wart). If all tests are green, refactor `sell()`/`buy()` to take target notionals so the rebalance branch uses them directly, and re-run the tests. Keep the refactor small; behavior must not change (tests are the referee).

- [ ] **Step 6: Commit**

```bash
git add backtest/rotation/engine.py v2/tests/test_rotation_engine.py
git commit -m "feat(rotation): daily-loop portfolio backtest engine"
```

---

### Task 6: Walk-forward eras, pass bar, and gauntlet runner

**Files:**
- Create: `backtest/rotation/walkforward.py`
- Create: `backtest/rotation/run_gauntlet.py` (CLI)
- Test: `v2/tests/test_rotation_walkforward.py`

**Interfaces:**
- Consumes: `RotationBacktest`, `RotationConfig`, `BacktestResult` from Task 5.
- Produces:

```python
def era_bounds(index: pd.DatetimeIndex) -> dict[str, tuple[pd.Timestamp, pd.Timestamp]]
    # {"fit": (...), "validate": (...), "holdout": (...)}
    # holdout = final 547 days (~18 months, spec §7); validate = the 547 days
    # before that; fit = everything earlier. Raises ValueError if fit era
    # would be < 365 days (not enough history for the protocol).

PARAM_MENU: list[RotationConfig]  # exactly the spec §12 cross product:
    # L ∈ {30,60,90} × skip ∈ {2,3} × band ∈ {6,8} × floor ∈ {5e6,10e6} = 36 configs

def run_walkforward(bars, btc_symbol, cfg) -> dict[str, BacktestResult]
    # one BacktestResult per era for a single config

def passes_bar(era_results: dict[str, BacktestResult]) -> dict
    # {"pass": bool, "reasons": [str, ...]} — spec §7 bar:
    #   net_return > 0 in EVERY era; max_drawdown <= 0.15 in every era;
    #   (cash benchmark = 0 return, so net_return > 0 covers "beats cash")
```

**Protocol discipline (spec §7):** fit-era results choose the single best config (highest fit-era net return subject to max_drawdown ≤ 0.15); that ONE config is then read on validate; the holdout is run LAST and ONCE. The runner enforces this by printing holdout results only behind an explicit `--unlock-holdout` flag.

- [ ] **Step 1: Write the failing tests**

```python
# v2/tests/test_rotation_walkforward.py
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
        assert len(PARAM_MENU) == 36
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest v2/tests/test_rotation_walkforward.py -v`
Expected: FAIL with ModuleNotFoundError.

- [ ] **Step 3: Write the implementation**

```python
# backtest/rotation/walkforward.py
"""Era-based walk-forward protocol and pre-declared pass bar (spec section 7)."""
from __future__ import annotations

from itertools import product

import pandas as pd

from backtest.rotation.engine import BacktestResult, RotationBacktest, RotationConfig

HOLDOUT_DAYS = 547     # ~18 months
VALIDATE_DAYS = 547
MIN_FIT_DAYS = 365
MAX_DD = 0.15

PARAM_MENU: list[RotationConfig] = [
    RotationConfig(lookback=lb, skip=sk, band=bd, volume_floor=fl)
    for lb, sk, bd, fl in product((30, 60, 90), (2, 3), (6, 8), (5e6, 10e6))
]


def era_bounds(index: pd.DatetimeIndex) -> dict[str, tuple[pd.Timestamp, pd.Timestamp]]:
    end = index[-1]
    holdout_start = end - pd.Timedelta(days=HOLDOUT_DAYS)
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
```

```python
# backtest/rotation/run_gauntlet.py
"""Gauntlet runner CLI.

Phase 1 (default): run PARAM_MENU on the fit era, pick the best config
(net return, subject to DD <= 15%), report fit + validate for that one
config. Phase 2 (--unlock-holdout): run the chosen config on holdout,
ONCE, and print the final verdict.

Results are written under backtest/rotation/output/ (gitignored — the
repo is public; numeric results never get committed).

Usage:
  python -m backtest.rotation.run_gauntlet --cache backtest/rotation/cache
  python -m backtest.rotation.run_gauntlet --cache ... --unlock-holdout
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from backtest.rotation.data_store import DailyBarStore
from backtest.rotation.walkforward import (
    MAX_DD,
    PARAM_MENU,
    era_bounds,
    passes_bar,
    run_walkforward,
)

BTC = "BTC-USD"
OUT = Path("backtest/rotation/output")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    ap.add_argument("--unlock-holdout", action="store_true")
    args = ap.parse_args()

    store = DailyBarStore(args.cache)
    bars = {s: store.load(s) for s in store.symbols()}
    bars = {s: df for s, df in bars.items() if df is not None and len(df) >= 200}
    if BTC not in bars:
        raise SystemExit(f"{BTC} missing from cache — it anchors the regime gate")

    eras = era_bounds(bars[BTC].index)
    OUT.mkdir(parents=True, exist_ok=True)

    # Phase 1: fit-era sweep over the closed menu.
    fit_rows = []
    for cfg in PARAM_MENU:
        res = run_walkforward(bars, BTC, cfg, {"fit": eras["fit"]})["fit"]
        fit_rows.append({"cfg": vars(cfg), "net": res.net_return,
                         "dd": res.max_drawdown, "trades": res.n_trades})
    fit_rows.sort(key=lambda r: r["net"], reverse=True)
    eligible = [r for r in fit_rows if r["dd"] <= MAX_DD]
    if not eligible:
        print("VERDICT: FAIL — no config met the drawdown cap on the fit era")
        (OUT / "gauntlet_fit.json").write_text(json.dumps(fit_rows, indent=2, default=str))
        return
    best = eligible[0]
    print(f"fit-era winner: {best['cfg']}  (details in {OUT}/gauntlet_fit.json)")
    (OUT / "gauntlet_fit.json").write_text(json.dumps(fit_rows, indent=2, default=str))

    from backtest.rotation.engine import RotationConfig
    chosen = RotationConfig(**{k: v for k, v in best["cfg"].items()})

    phases = {"fit": eras["fit"], "validate": eras["validate"]}
    if args.unlock_holdout:
        phases["holdout"] = eras["holdout"]
    results = run_walkforward(bars, BTC, chosen, phases)
    for era, res in results.items():
        print(f"{era:>9}: net {res.net_return:+.2%}  maxDD {res.max_drawdown:.2%}  "
              f"trades {res.n_trades}  in-market {res.days_in_market}/{res.days_total}")
    (OUT / "gauntlet_result.json").write_text(json.dumps(
        {era: {"net": r.net_return, "dd": r.max_drawdown, "trades": r.n_trades}
         for era, r in results.items()}, indent=2))

    if args.unlock_holdout:
        verdict = passes_bar(results)
        print(f"VERDICT: {'PASS' if verdict['pass'] else 'FAIL'}")
        for r in verdict["reasons"]:
            print(f"  - {r}")
    else:
        print("(holdout locked — rerun with --unlock-holdout for the final, "
              "one-time verdict)")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest v2/tests/test_rotation_walkforward.py -v` — all PASS.
Run: `python -m pytest v2/tests/ -q` — suite green.

- [ ] **Step 5: Commit**

```bash
git add backtest/rotation/walkforward.py backtest/rotation/run_gauntlet.py v2/tests/test_rotation_walkforward.py
git commit -m "feat(rotation): walk-forward eras, closed parameter menu, gauntlet runner"
```

---

### Task 7: Data acquisition + gauntlet execution (manual/operational — no new code)

**Files:**
- Create: `backtest/rotation/README.md` (data sourcing instructions, below)

This task is operational; the human (or agent with network access) drives it.

- [ ] **Step 1: Download Kraken bulk OHLCVT data.** From Kraken support → "Downloadable historical OHLCVT data", download the daily (1440) CSVs for all USD pairs. Save zips under `backtest/rotation/cache/_bulk/` (gitignored). If the bulk export turns out to be unavailable or stale beyond ~2 quarters, STOP and surface the gap — options (choose with the user): proceed with REST-only ~720-day history and a correspondingly shortened era structure (documenting the protocol deviation), or source another venue's daily data for the pre-REST period.

- [ ] **Step 2: Import + top-up.** Write a short throwaway import loop (or run in a Python shell): for each `<PAIR>_1440.csv`, map the Kraken pair code to v2 symbol notation with `v2.utils.symbol_mapper` (XBT→BTC etc.), call `store.import_ohlcvt_csv(symbol, path)`, then `store.top_up_from_rest(symbol, pair)`. Record how many symbols and the date span in the session notes.

- [ ] **Step 3: Sanity-check the cache.** For BTC-USD: ≥ 1,800 daily bars, no gaps > 3 days, last bar within 2 days of today. Spot-check 2 known prices against public charts.

- [ ] **Step 4: Run Phase 1** (`python -m backtest.rotation.run_gauntlet --cache backtest/rotation/cache`). Review fit + validate for the winner. Only if validate confirms fit's direction, run Phase 2 with `--unlock-holdout` — ONCE.

- [ ] **Step 5: Write `backtest/rotation/README.md`** documenting: data sourcing steps (Steps 1–2 above), the two-phase runner usage, and the public-repo rule (no numeric results committed; summaries go to the private memory dir). Commit the README only:

```bash
git add backtest/rotation/README.md
git commit -m "docs(rotation): data sourcing and gauntlet usage"
```

- [ ] **Step 6: Record the verdict.** Full numeric results → private memory dir (new file `july_2026_rotation_gauntlet.md`), including the exposure calibration chosen (the `exposure` scalar that brings fit-era max DD to ≤ 12–14%, spec §6 — set it in Phase 1 review before Phase 2). PASS → proceed to Plan 2 (live plugin). FAIL → stop, per spec §7; no post-hoc parameter rescue.

---

## Self-Review Notes (completed)

- **Spec coverage:** §3 → Task 4 (spread screen documented as live-only); §4 → Tasks 1–2 + engine rule 3; §5 → Task 3 + engine rule 2 ("fast out, slow in" implemented as daily gate check vs weekly rebalance); §6 costs/cap/cash-slots → Tasks 2, 5; §7 eras/menu/pass-bar/holdout-locking → Task 6; §7 survivorship documentation + data sourcing → Task 7. NOT in this plan (deliberately, gated on the verdict): §6 catastrophe stop & kill-switch, §8 rollout, §9 live plugin/dashboard/pair-discovery config — those are Plan 2/3.
- **Type consistency check:** `select_holdings(ranked, current, k, band)` and `inverse_vol_weights(vols, cap)` signatures match between Task 2 tests, Task 2 impl, and Task 5 engine usage. `BacktestResult` fields used by Task 6 (`net_return`, `max_drawdown`, `equity`, `n_trades`) all exist in Task 5's dataclass.
- **Known wart flagged in-plan:** Task 5 Step 5 refactor of sell-branch bookkeeping.
```
