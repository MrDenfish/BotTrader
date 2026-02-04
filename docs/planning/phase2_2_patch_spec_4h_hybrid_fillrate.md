# Phase 2.2 Patch Spec — Restore Fill Rate + Reach 2–4 Trades/Month (Maker-First)

**Audience:** Claude Code  
**Target:** Existing Phase 2.1 codebase (`config_4h_hybrid.py`, `strategy_4h_hybrid.py`, `engine_4h_hybrid.py`, optimizer scripts)

## Objective
Phase 2.1 produced high-quality trades but **too few trades** and **poor signal→entry conversion** over 180 days.

**Phase 2.2 goal:** Keep the same edge hypothesis (compression breakouts + fee-multiple exits + maker-first), while:
- Increasing trade frequency to **2–4 trades/month**
- Increasing signal→entry conversion (fill rate) without becoming taker-heavy
- Preserving fee survivability (avoid churn)

## Diagnosis Summary (from 180-day run)
- Signals/day dropped significantly vs the 60-day slice
- Only 1 of 3 setups filled; 2 expired → entry logic too strict/late
- The one filled trade had excellent MFE (+6.14%) and low MAE (-0.85%) → quality is good **when filled**

So we need:
1) **More setups** (looser compression gating and/or shorter donch lookback)
2) **Better fills** (resting retest bid immediately, earlier chase escalation, volatility-aware offsets)
3) **Better observability** (distance-to-fill diagnostics)

---

# A) Indicator / Gate Changes (More Setups)

## A1) Compression Gate: “Recent Compression” instead of “Only compressed at trigger bar”
Current behavior likely requires `bb_width_pct <= threshold` at the breakout bar, which can miss many valid “compression then release” sequences.

### Change
Define a boolean: `recent_compression_ok` on 4h bars:

- `recent_compression_ok = any(bb_width_pct <= bb_width_pct_threshold for the last K completed 4h bars)`
- K defaults to **6** (24 hours), test 6–12.

Then your setup condition becomes:
- `donch_breakout AND recent_compression_ok AND regime_ok AND viability_ok`

### New config params
- `compression_lookback_4h: int = 6`  # 6 bars = 24h

### Notes
- This should increase setups without loosening the compression percentile itself too much.
- Keep the percentile window (e.g. 120–180) as currently implemented.

## A2) Slightly Loosen Compression Threshold and Window (for 2–4/month target)
Phase 2.1 used `bb_width_pct_threshold=20` and got too few setups on 180 days.

### Change (defaults)
- `bb_width_pct_threshold`: **20 → 30**
- `bb_width_window`: **180 → 120** (more adaptive)

### New default preset
- `PHASE22_BASELINE`: threshold=30, window=120, lookback=6

## A3) Donchian Lookback: Shorten to increase breakout opportunities
Optimizer previously showed donch_len=10 is best pocket. For 2–4/month, include slightly faster lengths.

### Change
Expand/search:
- `donch_len`: **{8, 10, 12}** (default 10)

---

# B) Entry Changes (Higher Fill Rate, Still Maker-First)

## B1) Place a Resting Retest Bid Immediately at Setup (not later)
Current retest logic often waits for retest/reclaim conditions before placing the bid, which misses the first dip that would have filled you.

### Change
When a setup triggers (4h close), immediately place a post-only limit buy at:

- `p_retest = breakout_level * (1 - retest_offset)`

Where:
- `breakout_level = donch_high_prev` (or setup high, choose consistent definition)

This order **rests immediately** and stays active until:
- it fills, or
- retest TTL expires, or
- setup TTL expires

### New config params
- `retest_offset: float = 0.0015`  # 0.15% (test 0.10–0.25%)
- `retest_ttl_minutes: int = 45`   # earlier escalation (was 120)

### Post-only rule
Only place if `p_retest < current_close_1m` at placement time; otherwise skip/adjust downward.

## B2) Earlier Chase Escalation + Volatility-Aware Chase Offset
If retest doesn't fill quickly, the move may run away. But we still want maker-first.

### Change
If retest bid not filled by `retest_ttl_minutes`, switch to CHASE mode:

- `chase_offset = max(chase_offset_min, chase_atr_mult * atr_pct_4h)`
- `p_chase = current_close_1m * (1 - chase_offset)`

### New config params
- `enable_chase_entry: bool = True`
- `chase_offset_min: float = 0.0005`  # 0.05%
- `chase_atr_mult: float = 0.25`
- `chase_ttl_minutes: int = 120`

## B3) Chase Max-Extension Guard (avoid late, adverse entries)
Only allow CHASE if price hasn't extended too far above breakout level:

- `extension = (current_close_1m / breakout_level) - 1`
- Allow chase only if `extension <= chase_max_extension`

### New config param
- `chase_max_extension: float = 0.005`  # 0.5% (test 0.3–1.0%)

## B4) Chase Reprice Attempts (increase fills without crossing)
One chase order may miss. Allow a small number of reprices while still post-only.

### Change
During chase window:
- Every `chase_reprice_interval_minutes` (e.g. 30), if order not filled:
  - cancel old chase order
  - place a new post-only chase bid at updated `p_chase`

Stop after `chase_max_reprices` attempts or TTL expiry.

### New config params
- `chase_max_reprices: int = 3`
- `chase_reprice_interval_minutes: int = 30`

## B5) Setup TTL Increase (allow fills)
Setups expired too often. Increase the setup TTL.

### Change
- `setup_ttl_bars_4h`: **3 → 6** (12h → 24h)

---

# C) Diagnostics (Must Add)

Add per-setup metrics for every expired setup:
- `min_low_1m_after_setup` during setup lifetime
- `min_low_gap_to_retest = (p_retest - min_low) / breakout_level`
- `min_low_gap_to_chase_min = (min(p_chase_prices) - min_low) / breakout_level`
- `was_order_placeable` flags (post-only)
- `retest_placed`, `chase_placed`, `chase_reprices`

Add these columns to optimizer CSV export:
- `expired_setups`
- `avg_gap_to_fill_retest`
- `avg_gap_to_fill_chase`
- `entry_type_counts` (RETEST vs CHASE)
- `avg_entry_wait_minutes`

This will tell you if you’re bidding too low, too late, or your sim is overly strict.

---

# D) Profit Capture & Risk (Keep, with minor adjustment)

Keep:
- `tp1_fee_mult = 3`
- `tp2_fee_mult = 8`
- trailing activated after TP1

Optional small improvement:
- shift size later if not already:
  - `tp1_qty_frac = 0.30`
  - `tp2_qty_frac = 0.50`
  - runner = 0.20

Stop:
- keep current stop_mult baseline, but allow optimizer to vary: **2.0–3.5**
- Consider “catastrophic-only for first N minutes” only if stopouts remain frequent (Phase 2.3)

---

# E) Optimizer Changes (Hit 2–4 Trades/Month)

## E1) Update parameter bounds
Focus on the productive region and new Phase 2.2 knobs:

- `donch_len`: [8, 10, 12]
- `bb_width_pct_threshold`: [20, 25, 30, 35] (default 30)
- `bb_width_window`: [120, 180] (default 120)
- `compression_lookback_4h`: [6, 12]
- `retest_offset`: [0.0010, 0.0015, 0.0020, 0.0025]
- `retest_ttl_minutes`: [30, 45, 60]
- `chase_offset_min`: [0.0005, 0.0010]
- `chase_atr_mult`: [0.20, 0.25, 0.35]
- `chase_max_extension`: [0.003, 0.005, 0.008]
- `chase_ttl_minutes`: [60, 120, 180]
- `chase_max_reprices`: [2, 3, 4]
- `setup_ttl_bars_4h`: [6] (or [6, 9] if needed)
- `stop_mult`: [2.0, 2.5, 3.0, 3.5]

## E2) Trade frequency constraint
Add a soft constraint scoring for the target range:
- Target trades/month: **2–4**
- Penalize configs outside **[1, 6]**.
(Do not hard-filter unless you extend the backtest window.)

## E3) Minimum trades for ranking
If testing only 60 days:
- `min_trades_for_rank = 6` (to reduce noise)
If testing 180+ days:
- `min_trades_for_rank = 20`

---

# F) File-Level Patch Tasks

## F1) `backtest/config_4h_hybrid.py`
Add new fields:
- `compression_lookback_4h`
- `retest_offset`
- `setup_ttl_bars_4h` (if not already configurable)
- `chase_offset_min`
- `chase_atr_mult`
- `chase_max_extension`
- `chase_max_reprices`
- `chase_reprice_interval_minutes`
Update defaults for `PHASE22_BASELINE`.

## F2) `backtest/engine_4h_hybrid.py`
Ensure you compute and forward-fill:
- `bb_width`, `bb_width_pct`
- 4h Donchian levels
- `atr_pct_4h`
No changes beyond adding `recent_compression_ok` helper access to last K bb_width_pct values.

## F3) `backtest/strategy_4h_hybrid.py`
Changes:
1) Setup trigger uses `recent_compression_ok` when enabled.
2) On setup creation:
   - immediately place `p_retest` bid (post-only check)
3) Retest TTL:
   - if not filled by deadline, start chase mode
4) Chase mode:
   - compute `p_chase` using volatility-aware offset
   - enforce `chase_max_extension`
   - allow reprices up to `chase_max_reprices`
5) Setup TTL: extend to 6×4h by default
6) Add expired-setup diagnostics and entry wait tracking

## F4) Optimizer scripts
- Expand parameter grid as per E1
- Export new diagnostics columns
- Add scoring/penalty for trades/month outside target

---

# G) Suggested Baseline for Phase 2.2

Use this as the next “default run” before sweeping:

- `donch_len = 10`
- `bb_width_window = 120`
- `bb_width_pct_threshold = 30`
- `compression_lookback_4h = 6`
- `vol_min_mult = 1.0` (unchanged unless too restrictive)
- `setup_ttl_bars_4h = 6`
- `retest_offset = 0.0015`
- `retest_ttl_minutes = 45`
- `enable_chase_entry = True`
- `chase_offset_min = 0.0005`
- `chase_atr_mult = 0.25`
- `chase_max_extension = 0.005`
- `chase_ttl_minutes = 120`
- `chase_max_reprices = 3`
- `chase_reprice_interval_minutes = 30`
- `tp1_fee_mult = 3.0`, `tp2_fee_mult = 8.0`
- `tp1_qty_frac = 0.30`, `tp2_qty_frac = 0.50`
- `stop_mult = 2.5`

---

## Success Criteria (Phase 2.2)
Over 180–365 days:
- Trades/month in **2–4** band (acceptable 1–6)
- Net P&L positive under closer-to-actual fees
- Entry conversion improves (signals→filled entries)
- Win rate and PF remain reasonable (do not chase 90% win rate)
- Missed fill diagnostics show gaps shrinking (bids are “close enough”)

---

**End of Phase 2.2 Patch Spec**
