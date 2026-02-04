# Phase 2 Spec — 4h Hybrid Maker Strategy (Compression Breakouts + Chase Entry)

**Audience:** Claude Code  
**Goal:** Modify the existing Phase 1 implementation to address the observed failure modes:
- Too few trades (retest-only misses momentum)
- Gross edge exists only in a narrow pocket (donch_len≈10, vol_min_mult≈1.0)
- Winners are too small relative to fees (TP2 capped too low; scale-out too early)
- Need better breakout quality selection (false breakouts in chop)

Phase 2 introduces **two structural upgrades**:
1) **Volatility Compression Filter** using **Bollinger Bandwidth percentile** on 4h bars  
2) **Maker-First Chase Entry** fallback if breakout-retest does not fill

It also expands the optimizer space to focus on the productive region and to pursue larger winners.

---

## 0) Assumptions / Existing Codebase

You already have:
- `backtest/config_4h_hybrid.py` — config dataclass + presets
- `backtest/strategy_4h_hybrid.py` — state machine using:
  - Donchian breakout setup (4h)
  - Breakout-retest post-only entry
  - Daily regime filter
  - Fee-multiple TP1/TP2
  - ATR trailing stop
- `backtest/engine_4h_hybrid.py` — loads 1m data, resamples to 4h/1d, calculates indicators
- Optimizer/grid search script producing CSV

Phase 2 must **fit into this structure** with minimal churn.

---

## 1) New Indicators (4h)

### 1.1 Bollinger Bands + Bandwidth (4h)
Compute on 4h closes:
- `bb_mid = SMA(close_4h, bb_len)`
- `bb_std = STD(close_4h, bb_len)` (population or sample OK, be consistent)
- `bb_upper = bb_mid + bb_k * bb_std`
- `bb_lower = bb_mid - bb_k * bb_std`

Bandwidth:
- `bb_width = (bb_upper - bb_lower) / bb_mid`
(If bb_mid == 0, skip; in crypto this should not occur.)

Config:
- `bb_len` (default 20)
- `bb_k` (default 2.0)

### 1.2 Bandwidth Percentile (Compression Score)
We need a rolling percentile rank of current `bb_width` compared to recent history:
- `bb_width_window` (number of 4h bars, default 180 ≈ 30 days)
- `bb_width_pct = percentile_rank(bb_width[-1], bb_width[-bb_width_window:])`
Where `percentile_rank` returns 0..100.

Compression condition:
- `bb_width_pct <= bb_width_pct_threshold` (e.g., 10/20/30)

Config:
- `use_compression_filter: bool`
- `bb_width_window` (default 180)
- `bb_width_pct_threshold` (default 20)

Implementation notes:
- Percentile rank can be computed by counting how many values in window are <= current and dividing by window size.
- Use the **last completed 4h bar** values (no lookahead).

---

## 2) New Strategy Gates (4h setup qualification)

### 2.1 Existing Gates (keep)
- Daily regime filter (unchanged in Phase 2)
- Viability filter `atr_pct_4h >= vol_min_mult * fee_rt_est` (keep, but bounds change)

### 2.2 New Compression Gate (Phase 2)
A Donchian breakout setup is allowed only if (when enabled):
- `compression_ok = (bb_width_pct <= bb_width_pct_threshold)`

Use this as an additional setup condition:
- `setup_trigger = donchian_breakout AND compression_ok AND regime_ok AND viability_ok`

Rationale:
- Donchian breakouts in chop create false signals; compression improves follow-through odds.

---

## 3) Maker-First Entry Enhancements (1m execution)

Phase 1 entry is breakout-retest only. Phase 2 adds a fallback **chase** entry that remains post-only.

### 3.1 Existing Retest Entry (keep)
- When setup triggers at a 4h close, create a `PendingSetup`.
- Place post-only limit buy near breakout level on retest/reclaim logic.
- If filled, open position.

### 3.2 New Retest TTL
Add a timer: if the retest entry does not fill within `retest_ttl_minutes`, escalate to chase entry.

Config:
- `retest_ttl_minutes` (default 180; optimizer will vary)

State changes:
- `PendingSetup` gains:
  - `setup_created_ts`
  - `retest_deadline_ts = setup_created_ts + retest_ttl_minutes`
  - `retest_entry_placed_count` (optional)
  - `chase_attempted: bool`

### 3.3 New Chase Entry (post-only)
If:
- pending setup still valid (not expired),
- entry not filled,
- current time >= `retest_deadline_ts`,
- and `enable_chase_entry=True`,

then place a **post-only bid** near current price:

- `p_buy = current_close_1m * (1 - chase_offset)`

Post-only placeability rule (conservative):
- Ensure `p_buy < current_close_1m` (it will be by construction)
- Optional: also require `p_buy <= current_low_1m`? (No; too strict. Allow resting.)

Fill rule:
- If a later 1m bar has `low_1m <= p_buy`, fill at `p_buy` with maker fee.

Chase order TTL:
- Cancel if not filled within `chase_ttl_minutes`.

Config:
- `enable_chase_entry: bool` (default True)
- `chase_offset` (default 0.0010 = 0.10%)
- `chase_ttl_minutes` (default 90)

Important:
- If chase expires, abandon the setup (do not keep chasing indefinitely).

---

## 4) Profit Capture Changes (Targets + Sizing)

Your optimizer showed best gross edge at highest TP2 you allowed (5.0× fees). Phase 2 expands targets upward and shifts size later.

### 4.1 Expanded TP Multiples
Add config:
- `tp1_fee_mult` ∈ {2.5, 3.0, 3.5, 4.0}
- `tp2_fee_mult` ∈ {6, 8, 10, 12}

(Keep your existing ATR-min guards if present; do not reduce them.)

### 4.2 Scale-out allocation
Add parameters (or update existing):
- `tp1_qty_frac` default 0.30 (search 0.25–0.35)
- `tp2_qty_frac` default 0.50 (search 0.40–0.50)
- Runner remainder default 0.20–0.30

Rationale:
- If you scale out too early, you “cap” gross edge and can’t clear fees.

---

## 5) Stop/Trail Changes (reduce taker exits)

Fees per trade suggest stops/trails may be exiting taker too often. Phase 2 should reduce stop-outs without blowing risk.

### 5.1 Stop multiple bounds
If `stop_mult` is currently fixed, expose it to optimizer:
- `stop_mult` ∈ {2.0, 2.5, 3.0, 3.5}

### 5.2 Trail activation after TP1
Only activate trailing after TP1 fills (if not already).
This avoids getting stopped early on noise.

(If already implemented, keep.)

Optional enhancement:
- “trail tightening” after TP2 (tighten multiplier a bit).

---

## 6) Optimizer / Grid Search Changes

### 6.1 Focus search where CSV showed potential
Restrict donchian and volatility multiplier ranges:

- `donch_len` ∈ {6, 8, 10, 12}
- `vol_min_mult` ∈ {0.75, 1.0, 1.25}

Keep `vol_min_mult` tied to `fee_rt_est` as before.

### 6.2 Add new dimensions (Phase 2)
- `use_compression_filter` ∈ {True, False}
- `bb_width_pct_threshold` ∈ {10, 20, 30}
- `retest_ttl_minutes` ∈ {60, 120, 180}
- `chase_offset` ∈ {0.0005, 0.0010, 0.0015, 0.0020}
- `chase_ttl_minutes` ∈ {30, 60, 90}
- `tp1_fee_mult` ∈ {2.5, 3.0, 3.5, 4.0}
- `tp2_fee_mult` ∈ {6, 8, 10, 12}
- `stop_mult` ∈ {2.0, 2.5, 3.0, 3.5}
- Optional: `tp1_qty_frac` ∈ {0.25, 0.30, 0.35}
- Optional: `tp2_qty_frac` ∈ {0.40, 0.50}

This is a big grid — use random search or staged sweeps.

### 6.3 Minimum trade count constraint
To avoid optimizing noise:
- Require `trades >= min_trades` for a config to be considered (default 20).
- If testing only 60 days yields too few trades, extend to 180–365 days for optimizer runs.

Config:
- `min_trades_for_rank` (default 20)

Ranking suggestion:
- primary: net_pnl
- secondary: trades_per_month within target band (2–6/month)
- penalize: excessive expired entries (fill realism)

---

## 7) Logging / Diagnostics Additions (must implement)

Add these fields to trade logs and/or per-config summaries:
- `entry_type` ∈ {RETEST, CHASE}
- `entry_wait_minutes` (from setup to fill)
- Fee breakdown per trade:
  - `fee_entry`, `fee_tp1`, `fee_tp2`, `fee_exit_stop`, `fee_exit_trail`
- Exit reason:
  - `TP1`, `TP2`, `STOP`, `TRAIL`, `TIMEOUT`
- `bb_width_pct_at_setup` (to validate compression effect)
- Setup-to-entry conversion rate:
  - `signals`, `entries`, `expired`, `filled`

---

## 8) Implementation Tasks by File

### 8.1 `backtest/engine_4h_hybrid.py`
- Compute and store 4h Bollinger bands and `bb_width`
- Compute rolling percentile `bb_width_pct` (no lookahead)
- Ensure these values are accessible to the strategy on 1m steps (forward-fill from last completed 4h)

### 8.2 `backtest/config_4h_hybrid.py`
Add parameters:
- `use_compression_filter: bool = True`
- `bb_len: int = 20`
- `bb_k: float = 2.0`
- `bb_width_window: int = 180`
- `bb_width_pct_threshold: int = 20`
- `retest_ttl_minutes: int = 180`
- `enable_chase_entry: bool = True`
- `chase_offset: Decimal/float = 0.0010`
- `chase_ttl_minutes: int = 90`
- Expanded TP/stop parameters + size fractions

Add presets:
- `PHASE2_BASELINE` (donch_len=10, vol_min_mult=1.0, compression on, chase on, tp1=3, tp2=8, stop_mult=2.5)

### 8.3 `backtest/strategy_4h_hybrid.py`
- Add compression gate check at setup creation:
  - if `use_compression_filter` and `bb_width_pct > threshold`: do not create setup
- Extend pending setup state with retest TTL and chase attempt
- Implement chase entry placement and TTL cancellation
- Record `entry_type` and `entry_wait_minutes`
- Ensure post-only rules are enforced in sim

### 8.4 Optimizer script
- Expand grid or implement random search
- Add minimum trade constraint in ranking
- Export the new columns

---

## 9) Suggested Phase 2 Baseline Parameters

Start with this before sweeping:
- `donch_len=10`
- `vol_min_mult=1.0`
- `use_compression_filter=True`
- `bb_width_pct_threshold=20`
- `retest_ttl_minutes=120`
- `enable_chase_entry=True`
- `chase_offset=0.0010`
- `chase_ttl_minutes=60`
- `tp1_fee_mult=3.0`
- `tp2_fee_mult=8.0`
- `tp1_qty_frac=0.30`
- `tp2_qty_frac=0.50`
- `stop_mult=2.5`

---

## 10) Success Criteria for Phase 2

A configuration is considered promising if (over a meaningful sample):
- Net P&L > 0 under closer-to-actual fees
- Trades/month in 2–6 band
- Profit factor (gross) > 1.2
- Majority of fills are maker
- TP2 hit rate is non-trivial (>= 15–25%) with meaningful runner profits

---

**End of Phase 2 Spec.**
