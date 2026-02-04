# Phase 2.3 Optimization Plan — Parameter Strategy + Evaluation Workflow (Fee-Aware, Maker-First)

**Audience:** Claude Code / project documentation  
**Context:** This plan amends Phase 2.2/2.2b specs with an explicit approach to optimize parameters without wasting compute or overfitting small samples.

> This document is written to be actionable inside your current code structure (`config_4h_hybrid.py`, `strategy_4h_hybrid.py`, `engine_4h_hybrid.py`, optimizer scripts).

---

## 0) What Phase 2.2 Taught Us (Summary)

From the Phase 2.2 assessment you provided:

- The strategy logic is working end-to-end, but **trade counts can be too sparse** depending on market regime and gates.
- A 60-day optimizer window is frequently **too short** for a low-frequency breakout-from-compression system.
- With sparse trades, parameter sweeps become noise-driven, and conclusions about parameter impact are unreliable.

This plan addresses that by:
1) Using a **two-tier backtest resolution** (5m for optimization, 1m for validation)
2) Using **longer windows** (>= 365 days) for ranking configs
3) Enforcing **minimum trade count constraints**
4) Splitting optimization into **stages** (gates/entries first, exits second)
5) Adding a second setup mode (**ROC-score setup**) to avoid “single setup mode scarcity”

---

## 1) Resolution Strategy (Speed vs Realism)

### 1.1 Use 5m base candles for optimizer runs
**Why:** Phase 2 trades often last days; the decision structure is 4h/1D-driven. 1m granularity is expensive and not necessary for ranking thousands of configs.

**Optimizer base timeframe:** **5m OHLCV**  
**Resample from 5m to:** 4h + 1D bars/indicators as usual.

**Fill modeling on 5m:**
- Buy limit fills if `low_5m <= bid_price`
- Sell limit fills if `high_5m >= limit_price`

### 1.2 Use 1m base candles only for final validation
After optimization, re-run top configs on 1m to confirm:
- signal→entry conversion
- chase behavior
- stop/trail realism
- fee breakdown sanity

**Rule:** Optimize on 5m, validate on 1m.

---

## 2) Window Length and Constraints (Make Optimization Meaningful)

### 2.1 Increase ranking window
For Phase 2.3, rank configs on at least:
- **365 days**, ideally **730 days (2 years)**

Reason: your setup frequency in some regimes is low. You need enough samples to estimate expectancy.

### 2.2 Minimum trade constraint
Do not “rank” configs with tiny sample size.

Suggested:
- For 365+ day runs: `min_trades_for_rank = 30`
- If you must run shorter windows: use `min_trades_for_rank = 10` and treat results as exploratory only.

### 2.3 Trade frequency target
Keep your operational target:
- **2–4 trades/month** (acceptable band 1–6/month)

Add a **soft penalty** outside [1, 6] rather than hard filtering, unless you have very long windows.

---

## 3) Optimization Should Be Staged (Avoid Mixing Causes)

### Stage A — Signal generation + fill conversion (most important first)
Objective: achieve enough trades **and** acceptable conversion while staying maker-first.

Tune these first:
- `bb_width_pct_threshold` (compression)
- `bb_width_window`
- `compression_lookback_4h` (recent compression ANY/quorum)
- `donch_len`
- `vol_min_mult`
- Entry: `retest_offset`, `retest_ttl_minutes`, chase parameters (`chase_offset_min`, `chase_atr_mult`, `chase_max_extension`, reprices)

**Stage A success criteria:**
- Trades/month in target band
- signal→filled entry conversion not collapsing
- maker fill rate still dominant

### Stage B — Profit capture and risk
Once Stage A yields enough trades:
- `tp1_fee_mult`, `tp2_fee_mult`
- `tp1_qty_frac`, `tp2_qty_frac`
- `stop_mult`, `trail_mult`, trail activation rules

**Stage B success criteria:**
- Positive net expectancy
- Profit factor improves
- Drawdown acceptable

---

## 4) Add Setup Mode Diversity (Reduce “scarcity risk”)

Phase 2.2 is currently Donchian breakout based. Add a second setup mode and let the optimizer choose which works best in the window.

### 4.1 New: ROC-score setup (4h)
Compute:
- `roc_raw_4h = (close_4h - close_4h[L]) / close_4h[L]`
- `roc = EMA(roc_raw_4h, roc_ema_len)`
- `roc_score = roc / atr_pct_4h`

Setup triggers when:
- `roc_score >= roc_score_thresh`

This setup is still gated by:
- regime filter
- viability filter
- compression / recent compression filter

### 4.2 Optimizer includes setup_mode
Add parameter:
- `setup_mode in {DONCHIAN, ROC_SCORE}`

and allow `roc_score_thresh` to vary (e.g., 0.8–1.8).


### 4.3 Implementation sequence (Option A: ROC-score as separate setup mode)
Implement ROC-score as a **new setup_mode** inside the 4h Hybrid strategy, but **test it independently first** before mixing it with Donchian in the optimizer.

**Step 1 — Add setup_mode = ROC_SCORE**
- Implement ROC-score setup trigger in `strategy_4h_hybrid.py` (or where setup triggers live)
- Reuse the *same* gates and execution machinery:
  - recent compression gate
  - viability (ATR% vs fees) gate
  - maker-first entry state machine (retest bid + chase + repricing)
  - fee-multiple TP ladder + trailing stop

**Step 2 — ROC-score-only baseline run**
- Run ROC-score mode by itself on **365 days** (or longer), using:
  - 5m base candles for speed (optimizer/screening)
  - 1m base candles for final validation on top configs
- Confirm it produces enough samples:
  - Target: **>= 30 trades** in 365 days
  - Target frequency: **2–6 trades/month** (soft target band)

**Step 3 — ROC-score-only Stage A/Stage B optimization**
- Stage A: tune gates + entry conversion for ROC-score mode
- Stage B: tune exits once trade count is adequate

**Step 4 — Only then combine in optimizer**
After ROC-score-only behavior is understood and stable, extend optimizer to include:
- `setup_mode in {DONCHIAN, ROC_SCORE}`

This avoids exploding the search space prematurely and prevents confounding parameter effects.


------

## 5) Suggested Parameter Search Spaces (Phase 2.3)

**Keep spaces small and intentional.** The goal is not a million-point grid; it is finding robust regions.

### 5.1 Gates
- `bb_width_window`: [120, 180, 240]
- `bb_width_pct_threshold`: [20, 25, 30, 35, 40]
- `compression_lookback_4h`: [6, 12]
- Optional quorum later: [any, 0.34] (start with ANY)

- `vol_min_mult`: [0.75, 1.0, 1.25, 1.5]
- Optional absolute ATR floor: `atr_floor_pct`: [0.006, 0.008, 0.010] (if needed)

### 5.2 Setup modes
- Donchian: `donch_len`: [6, 8, 10, 12]
- ROC-score:
  - `roc_len_4h`: [4, 6, 8]
  - `roc_ema_len`: [1, 3]
  - `roc_score_thresh`: [0.8, 1.0, 1.2, 1.4, 1.6]

### 5.3 Entry (maker-first)
- `retest_offset`: [0.0010, 0.0015, 0.0020, 0.0025]
- `retest_ttl_minutes`: [30, 45, 60]
- `setup_ttl_bars_4h`: [6, 9]

Chase:
- `chase_offset_min`: [0.0005, 0.0010]
- `chase_atr_mult`: [0.20, 0.25, 0.35]
- `chase_max_extension`: [0.003, 0.005, 0.008]
- `chase_ttl_minutes`: [60, 120, 180]
- `chase_max_reprices`: [2, 3, 4]
- `chase_reprice_interval_minutes`: [30, 60]
- `max_step_up_per_reprice`: [0.0010, 0.0020, 0.0030]

### 5.4 Exits (Stage B)
- `tp1_fee_mult`: [2.5, 3.0, 3.5, 4.0]
- `tp2_fee_mult`: [6, 8, 10, 12]
- `tp1_qty_frac`: [0.25, 0.30, 0.35]
- `tp2_qty_frac`: [0.40, 0.50]
- `stop_mult`: [2.0, 2.5, 3.0, 3.5]
- `trail_mult`: [2.0, 2.5, 3.0]

---

## 6) Ranking / Scoring Function (Avoid “single-metric traps”)

For each config (only if trades >= min_trades_for_rank):

Primary:
- **net_pnl** (closer-to-actual fees)

Secondary (penalties):
- Trades/month outside [1, 6]
- Excessive expired setups (fill issues)
- Excessive taker exits (stop/trail frequency)
- Large drawdown

Recommended outputs:
- `net_pnl`, `gross_pnl`, `fees`
- `trades`, `trades_per_month`
- `signals`, `entries`, `expired`, conversion rate
- `entry_type_counts` (RETEST vs CHASE)
- `avg_entry_wait_minutes`
- fee breakdown and taker incidence

---

## 7) Walk-forward Validation (Make It Real)

After selecting top configs on a long window:
1) Run walk-forward:
   - Train period: first 60–70%
   - Test period: last 30–40%
2) Run 1m validation on top 3–10 configs:
   - Compare conversion rates (5m vs 1m)
   - Ensure repricing & post-only assumptions remain realistic

---

## 8) Implementation Checklist (Claude Tasks)

### 8.1 Engine / Data
- Add 5m base data loader (if currently only 1m)
- Cache resampled 4h/1D series once per dataset
- Ensure BB bandwidth percentile uses completed 4h bars only

### 8.2 Config / Strategy
- Add `setup_mode` and implement ROC-score setup
- Keep Phase 2.2 state enum and repricing policy
- Maintain deadline-only timing (retest/chase/setup deadlines)

### 8.3 Optimizer
- Add staged optimization mode:
  - Stage A grid
  - Stage B grid
- Enforce min trade constraints
- Implement walk-forward option

---

## 9) Immediate Next Step (Practical)

**Do this next before expanding the optimizer:**

1) Switch optimizer base data to **5m**
2) Run a **365-day** baseline test with **ROC-score setup mode only** first.
   - If ROC-score achieves adequate trade counts and stable behavior, then run Donchian-only and finally the combined setup_mode test.
3) Verify you can achieve **>= 30 trades** in 365 days in at least one mode while remaining maker-first.

If you cannot reach that sample size, loosen gates (compression threshold/window, vol_min_mult) until you can—then proceed to Stage B.

---

## Appendix: Source Assessment Text (for reference)

# Phase 2.2 Parameter Optimization - Detailed Assessment

**Date:** 2026-01-31
**Test Period:** 60 days (Dec 2025 - Jan 2026)
**Symbol:** BTC-USD
**Strategy:** 4h Hybrid with Phase 2.2 Enhancements

---

## Executive Summary

**Optimization Objective:** Achieve 2-4 trades/month with positive returns

**Result:** ❌ Target NOT achieved
- **Best performance:** 0.5 trades/month (1 trade in 60 days)
- **Configurations tested:** 48 parameter combinations
- **Configurations meeting target:** 0

**Key Finding:** The bottleneck is NOT the implementation or Phase 2.2 features—it's the **Donchian breakout setup combined with recent market conditions** (choppy/sideways period with few clean breakouts).

**Code Quality Assessment:** ✅ EXCELLENT
- Zero runtime errors across all 48+ tests
- 100% fill rate when signals generated
- Phase 2.2 features working as designed

---

## 1. Parameter Ranges Tested

### Grid Search Configuration

```python
# Total combinations: 4 × 4 × 3 = 48 tests

bb_width_pct_threshold = [30, 40, 50, 60]
# Compression filter percentile
# LOWER = looser filter = more signals expected
# Range: 30 (very loose) to 60 (moderate)

donch_len = [5, 7, 10, 15]
# Donchian breakout period (bars)
# SHORTER = more breakouts expected
# Range: 5 bars (20h) to 15 bars (60h)

vol_min_mult = [0.5, 1.0, 1.5]
# Volatility filter multiplier
# LOWER = looser filter = more signals expected
# Range: 0.5 (very loose) to 1.5 (strict)
```

### Baseline Configuration (Phase 2.2)

```python
# Compression Filter
bb_width_window = 120          # Adaptive window
bb_width_pct_threshold = 30    # 30th percentile (OPTIMIZED)
compression_lookback_4h = 6    # 24-hour lookback
use_compression_filter = True

# Donchian Breakout
donch_len = 10                 # 10-bar breakout (OPTIMIZED)

# Volatility Filter
vol_min_mult = 1.0             # Baseline multiplier (OPTIMIZED)
vol_lookback = 30              # 30-bar lookback
vol_pct_threshold = 50         # 50th percentile

# Entry Logic (Phase 2.2)
retest_offset = 0.0015         # 0.15% below breakout
retest_ttl_minutes = 45        # Early escalation

chase_offset_min = 0.0005      # 0.05% minimum
chase_atr_mult = 0.25          # 0.25× ATR%
chase_max_extension = 0.050    # 5.0% (TUNED)
chase_max_reprices = 3         # Up to 3 reprices
chase_reprice_interval_minutes = 30

# Exit Logic
tp1_pct = 0.015                # 1.5% target
tp2_pct = 0.025                # 2.5% target
stop_loss_pct = 0.010          # 1.0% stop
runner_pct = 0.50              # 50% runner position
```

---

## 2. Optimization Results Summary

### Overall Statistics

| Metric | Value |
|--------|-------|
| Total tests completed | 48/48 (100%) |
| Tests with ≥1 trade | 24/48 (50%) |
| Tests with 0 trades | 24/48 (50%) |
| **Configs in target range (2-4 trades/mo)** | **0/48 (0%)** |
| Maximum trades/month achieved | 0.5 (1 trade in 60 days) |
| Maximum trades achieved | 1 |
| Best net P&L | +$3.95 |
| Worst net P&L | -$3.19 |

### Performance Distribution

**Trade Frequency Distribution:**
- 0.5 trades/month (1 trade): 24 configurations (50%)
- 0.0 trades/month (0 trades): 24 configurations (50%)

**P&L Distribution (for configs with trades):**
- Positive P&L: 12 configurations (+$3.95 each)
- Negative P&L: 12 configurations (-$3.19 each)
- Zero P&L: 24 configurations (no trades)

---

## 3. Top Configurations by Trade Frequency

All top configurations achieved only **0.5 trades/month** (1 trade in 60 days):

| Rank | BB% | Donch | Vol | Trd/mo | Signals | Fill% | Net P&L | WR | PF |
|------|-----|-------|-----|--------|---------|-------|---------|----|----|
| 1 | 30 | 5 | 0.5 | 0.5 | 1 | 100.0 | +$3.95 | 100% | ∞ |
| 2 | 30 | 5 | 1.0 | 0.5 | 1 | 100.0 | -$3.19 | 0% | 0.0 |
| 3 | 30 | 7 | 0.5 | 0.5 | 1 | 100.0 | +$3.95 | 100% | ∞ |
| 4 | 30 | 7 | 1.0 | 0.5 | 1 | 100.0 | -$3.19 | 0% | 0.0 |
| 5 | 30 | 10 | 0.5 | 0.5 | 1 | 100.0 | +$3.95 | 100% | ∞ |
| 6 | 30 | 10 | 1.0 | 0.5 | 1 | 100.0 | -$3.19 | 0% | ∞ |
| 7-24 | (various) | (various) | 0.5-1.0 | 0.5 | 1 | 100.0 | ±$3-4 | 0-100% | varies |

**Key Observations:**
- Looser compression (bb=30) generates signals, but still only 1
- Shorter Donchian periods (5-10) don't increase signal count
- Volatility filter determines P&L outcome: vol=0.5 wins, vol=1.0 loses
- All vol=1.5 configs resulted in 0 trades (filter too strict)

---

## 4. Top Configurations by Net P&L

Best performing configurations (positive P&L):

| Rank | BB% | Donch | Vol | Trd/mo | Net P&L | WR | Entry | Outcome |
|------|-----|-------|-----|--------|---------|----|----|---------|
| 1 | 30 | 5 | 0.5 | 0.5 | +$3.95 | 100% | CHASE | Win |
| 2 | 30 | 7 | 0.5 | 0.5 | +$3.95 | 100% | CHASE | Win |
| 3 | 30 | 10 | 0.5 | 0.5 | +$3.95 | 100% | CHASE | Win |
| 4 | 40 | 5 | 0.5 | 0.5 | +$3.95 | 100% | CHASE | Win |
| 5 | 40 | 7 | 0.5 | 0.5 | +$3.95 | 100% | CHASE | Win |

**Pattern:** All winning configs share `vol_min_mult = 0.5` (loose volatility filter)

Worst performing configurations (negative P&L):

| Rank | BB% | Donch | Vol | Trd/mo | Net P&L | WR | Entry | Outcome |
|------|-----|-------|-----|--------|---------|----|----|---------|
| 1 | 30 | 5 | 1.0 | 0.5 | -$3.19 | 0% | CHASE | Loss |
| 2 | 30 | 7 | 1.0 | 0.5 | -$3.19 | 0% | CHASE | Loss |
| 3 | 30 | 10 | 1.0 | 0.5 | -$3.19 | 0% | CHASE | Loss |
| 4 | 40 | 5 | 1.0 | 0.5 | -$3.19 | 0% | CHASE | Loss |
| 5 | 40 | 7 | 1.0 | 0.5 | -$3.19 | 0% | CHASE | Loss |

**Pattern:** All losing configs share `vol_min_mult = 1.0` (baseline volatility filter)

---

## 5. Parameter Impact Analysis

### 5.1 Compression Filter (bb_width_pct_threshold)

**Expected Behavior:** Lower threshold → More signals

**Actual Results:**

| Threshold | Configs with Trades | Avg Trades/mo | Impact |
|-----------|---------------------|---------------|--------|
| 30 (loosest) | 8/12 (67%) | 0.33 | Most signals |
| 40 | 8/12 (67%) | 0.33 | Same as 30 |
| 50 | 4/12 (33%) | 0.17 | Fewer signals |
| 60 | 4/12 (33%) | 0.17 | Fewer signals |

**Finding:** Compression filter has **minimal impact** on signal count. Even loosest setting (30) only generates 1 signal in 60 days.

### 5.2 Donchian Period (donch_len)

**Expected Behavior:** Shorter period → More breakouts → More signals

**Actual Results:**

| Period | Configs with Trades | Avg Trades/mo | Impact |
|--------|---------------------|---------------|--------|
| 5 bars (20h) | 6/12 (50%) | 0.25 | No advantage |
| 7 bars (28h) | 6/12 (50%) | 0.25 | Same |
| 10 bars (40h) | 6/12 (50%) | 0.25 | Same |
| 15 bars (60h) | 6/12 (50%) | 0.25 | Same |

**Finding:** Donchian period has **ZERO impact** on signal count. All periods generate same 1 signal.

**Critical Insight:** This proves the issue is NOT the breakout detection sensitivity—it's the **absence of qualifying breakouts** in this 60-day period.

### 5.3 Volatility Filter (vol_min_mult)

**Expected Behavior:** Lower multiplier → More signals

**Actual Results:**

| Multiplier | Configs with Trades | Avg Trades/mo | Win Rate | Avg P&L |
|------------|---------------------|---------------|----------|---------|
| 0.5 (loose) | 16/16 (100%) | 0.5 | 100% | +$3.95 |
| 1.0 (baseline) | 8/16 (50%) | 0.25 | 0% | -$3.19 |
| 1.5 (strict) | 0/16 (0%) | 0.0 | N/A | $0.00 |

**Finding:** Volatility filter is the **ONLY parameter with significant impact**:
- vol=0.5: Allows 1 signal, which wins
- vol=1.0: Blocks the winning trade in half the configs, losing trade in other half
- vol=1.5: Blocks ALL signals (too strict)

**Critical Insight:** The volatility filter is acting as the **quality control** mechanism, not the signal generator.

---

## 6. Signal Detection Analysis

### Verification Test: Compression Filter Disabled

To isolate the root cause, I ran an additional test with **compression filter completely disabled**:

```python
config.use_compression_filter = False
config.vol_min_mult = 0.5  # Loose volatility filter
```

**Result:** Still only **1 signal** in 60 days

**Conclusion:** The compression filter is NOT the bottleneck. The issue is the **Donchian breakout setup itself** combined with recent market conditions.

### Signal Timeline

**60-day test (Dec 2025 - Jan 2026):**
- 1 signal generated: Jan 12, 2026 @ 04:00
- Entry filled: Jan 12, 2026 @ 06:01 (CHASE, 121 minutes wait)
- Exit: Jan 12, 2026 @ 14:24 (stop loss hit)

**180-day test (Jul 2025 - Jan 2026):**
- 1 signal generated: Sep 18, 2025 @ 03:00
- Entry filled: Sep 18, 2025 @ 05:44 (CHASE, 104 minutes wait)
- Exit: Sep 22, 2025 @ 02:32 (stop loss hit)

**Key Observation:** Only 1 signal per 60-90 day period, regardless of parameter settings. This indicates a **market regime issue**, not a configuration issue.

---

## 7. Market Condition Analysis

### Recent 60-Day Period (Dec 2025 - Jan 2026)

**Characteristics:**
- **Choppy/sideways market:** Price ranging between $90k-$105k
- **Few clean breakouts:** Limited Donchian high breaks with follow-through
- **High volatility but no trend:** Volatility present but directionless

**Why This Matters:**
The Donchian breakout setup requires:
1. Price compression (Bollinger Bands narrow)
2. Clean breakout above N-bar high
3. Sufficient volatility for profitable move
4. Directional follow-through

Recent market provided #3 (volatility) but NOT #2 or #4 (clean breakouts with follow-through).

### Historical Comparison

**180-day test:** 1 signal in Sep 2025 (0.2 trades/month)
**60-day test:** 1 signal in Jan 2026 (0.5 trades/month)

Both periods show similar low signal generation, suggesting this is a **persistent market characteristic**, not an anomaly.

---

## 8. Phase 2.2 Feature Validation

Despite low signal count, Phase 2.2 features performed **flawlessly**:

### A1: Recent Compression Check ✅
- Correctly identified compression in 24h lookback window
- No false signals from instant compression spikes

### B1: Immediate Retest Bid Placement ✅
- Attempted retest placement at setup creation
- Correctly transitioned to CHASE when retest not placeable

### B2: Volatility-Aware Chase Offset ✅
- Dynamically calculated chase offset based on ATR%
- Chase offsets ranged from 0.05% to 0.25% as expected

### B4: Chase Repricing ✅
- Successfully repriced chase orders upward
- Filled within 121 minutes (100% fill rate when signals generated)

### H1: Expired Setup Diagnostics ✅
- Accurately tracked distance-to-fill metrics
- Provided actionable diagnostic data (led to chase_max_extension tuning from 0.5% → 5.0%)

**Fill Rate Achievement:** 100% when signals generated (target: >30%)

---

## 9. Root Cause Assessment

### What's Working ✅

1. **Implementation Quality:** Zero errors, clean state transitions
2. **Entry Logic:** 100% fill rate with Phase 2.2 repricing
3. **Diagnostics:** Expired setup tracking provided tuning insights
4. **Code Reliability:** Processed 258,833 bars without issues

### What's NOT Working ❌

1. **Signal Generation Frequency:** 0.5 trades/month vs target 2-4
2. **Market Dependency:** Donchian breakout setup requires trending markets
3. **Recent Market Regime:** Choppy/sideways conditions with few qualifying breakouts

### The Actual Bottleneck

**It's the market, not the code.**

The optimization revealed:
- ✅ Loosening filters didn't increase signals (even when disabled entirely)
- ✅ Shortening Donchian period didn't increase signals
- ✅ The ONE signal that occurred was captured and filled perfectly

**Conclusion:** The Donchian breakout setup is **working as designed**, but the recent market hasn't provided many qualifying setups.

---

## 10. Recommendations

### Option 1: Accept Conservative Performance ✅ RECOMMENDED

**Rationale:** Quality over quantity
- Phase 2.2 implementation is production-ready
- 100% fill rate demonstrates execution excellence
- Low signal count is appropriate for conservative filters in choppy markets
- Historical performance will vary with market regimes

**Action:** Deploy Phase 2.2 as-is, monitor over longer timeframes

### Option 2: Test Alternative Time Periods

**Rationale:** Validate market dependency hypothesis
- Test on 2023-2024 data (trending bull market)
- Test on 2022 data (trending bear market)
- Compare signal generation across regimes

**Expected Outcome:** Higher signal counts in trending periods, confirming market dependency

### Option 3: Explore Alternative Setup Modes

**Rationale:** Diversify signal sources
- Test `setup_mode = "roc_score"` instead of "donchian"
- Use ROC-based compression+momentum signals
- May provide more signals in sideways markets

**Risk:** Different entry characteristics, requires separate validation

### Option 4: Consider Shorter Timeframe

**Rationale:** Increase signal frequency
- Test 1h or 2h timeframes instead of 4h
- More breakout opportunities per day
- Higher execution frequency

**Risk:** More noise, higher fee impact, requires separate backtest infrastructure

### Option 5: Further Loosen Filters (NOT RECOMMENDED)

**Rationale:** Force more signals
- Remove compression filter entirely ✅ Already tested—no impact
- Use vol_min_mult < 0.5 (may allow low-quality setups)
- Use donch_len < 5 (may generate false breakouts)

**Risk:** Lower quality signals, worse win rate, not solving root cause

---

## 11. Statistical Confidence Analysis

### Sample Size Consideration

**60-day test:** 1 signal (N=1)
- Statistically insufficient for performance evaluation
- Cannot draw conclusions about win rate, profit factor, etc.
- Need minimum N=30 trades for statistical significance

**180-day test:** 1 signal (N=1)
- Same limitation
- Extends observation period but not sample size

### Recommended Validation Approach

1. **Extend backtest period:** Test on 2+ years of data across multiple market regimes
2. **Multi-symbol testing:** Test on ETH-USD, SOL-USD to increase sample size
3. **Regime classification:** Identify trending vs. choppy periods, measure performance separately
4. **Monte Carlo analysis:** Simulate performance across varying market conditions

---

## 12. Final Assessment

### Implementation Quality: A+ ✅

- ✅ Phase 2.2 features fully implemented and validated
- ✅ Zero runtime errors across 48+ tests
- ✅ 100% fill rate when signals generated
- ✅ Clean state machine transitions
- ✅ Effective diagnostic tooling
- ✅ Production-ready code quality

### Trade Frequency Target: F ❌

- ❌ Target: 2-4 trades/month
- ❌ Achieved: 0.5 trades/month (1 trade in 60 days)
- ❌ Gap: 75-87.5% below target
- ❌ No parameter combination achieved target

### Root Cause: Market-Dependent Signal Generation

**The Donchian breakout setup requires specific market conditions:**
1. Compression phase (consolidation)
2. Clean breakout with volume
3. Directional follow-through

**Recent 60-day period (Dec 2025 - Jan 2026) lacked these conditions:**
- Choppy/sideways price action
- False breakouts without follow-through
- Volatility without trend

**This is NOT an implementation failure—it's an expected characteristic of a conservative, quality-focused strategy.**

### Recommended Path Forward

**RECOMMENDATION: Deploy Phase 2.2 with realistic expectations**

1. ✅ Code is production-ready
2. ✅ Execution quality is excellent (100% fill rate)
3. ⚠️ Signal frequency is market-dependent
4. ✅ Strategy is designed for quality over quantity
5. ✅ Performance will improve in trending market regimes

**Alternative:** If 2-4 trades/month is a hard requirement, consider:
- Alternative setup modes (ROC-based)
- Shorter timeframes (1h/2h)
- Multi-symbol portfolio approach
- Accept that this requires fundamental strategy changes, not parameter tuning

---

## Appendix A: Complete Test Results Table

[Available in `optimize_phase2_2_results.log`]

Summary:
- 48 configurations tested
- 24 with 1 trade (0.5/month)
- 24 with 0 trades
- 0 configurations achieving 2-4 trades/month target

---

## Appendix B: Diagnostic Data

### Single Signal Analysis (60-day test)

**Setup Details:**
- Timestamp: 2026-01-12 04:00:00
- Breakout level: $91,173.12
- Entry type: CHASE
- Entry price: $91,918.85
- Entry wait: 121 minutes (2 reprices)
- Exit: Stop loss @ $90,856.40
- Duration: 8.4 hours
- Outcome: Loss (-$3.19 with vol=1.0, Win +$3.95 with vol=0.5)

**Key Insight:** The volatility filter determined whether this setup was taken and whether it won/lost. This single setup accounts for ALL variation in the 48 test results.

---

**END OF ASSESSMENT**

