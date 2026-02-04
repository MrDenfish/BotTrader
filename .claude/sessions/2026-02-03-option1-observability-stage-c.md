# Option 1 Observability + Stage C Runner Policy Implementation

**Date**: 2026-02-03
**Branch**: `feature/4h-hybrid-maker-strategy`
**Status**: ✅ Complete - Ready for A/B testing
**Session Plan**: Phase 2.4 - ATR Backtesting (Coinbase fee-aware 4h Hybrid maker-first strategy)

---

## Executive Summary

Implemented **Option 1 Observability** metrics and **Stage C compression-based runner policy** per the "production now, upgrade door later" plan. All guardrails maintained: no setup queue, no concurrent setups, no multiple positions per symbol.

**Key Outcomes**:
- ✅ State occupancy, setup lifecycle, and opportunity loss metrics now visible
- ✅ Stage C captures compression context at entry, applies adaptive runner/trail parameters
- ✅ Trade breakdown by compression context enables data-driven Stage C tuning
- ✅ All metrics verified on 180d BTC-USD window with differential parameter test
- ✅ Surprising finding: Compressed entries outperform normal entries (60% vs 17% win rate)

---

## 1. Option 1 Observability Implementation

### A) State Occupancy Metrics (strategy_4h_hybrid.py:2540-2568)

**What**: Tracks 4h bars spent in each state (FLAT, SETUP_ACTIVE, RETEST_ORDER_WORKING, CHASE_ORDER_WORKING, IN_POSITION)

**Implementation**:
- Counter incremented at each 4h bar close: `self.state_occupancy[current_state] += 1`
- Stored in `__init__`: `self.state_occupancy = {StrategyState.FLAT: 0, ...}`

**Output**:
```
A) STATE OCCUPANCY (4h bars with state as of bar close):

NOTE: State is sampled at 4h bar close. Intra-bar transitions count as final state only.

  FLAT                       924 bars ( 85.7%)  =  221,760 minutes
  SETUP_ACTIVE                 0 bars (  0.0%)  =        0 minutes
  RETEST_ORDER_WORKING         0 bars (  0.0%)  =        0 minutes
  CHASE_ORDER_WORKING          0 bars (  0.0%)  =        0 minutes
  IN_POSITION                154 bars ( 14.3%)  =   36,960 minutes

  Total 4h bars: 1078
  Non-FLAT time: 154 bars (14.3%)
```

**Key Insight**: 0% setup states explained by intra-bar transitions (FLAT→SETUP_ACTIVE→RETEST_ORDER_WORKING within same 4h bar). Final state counted only.

### B) Setup Lifecycle Metrics (strategy_4h_hybrid.py:2570-2605)

**What**: Tracks setup creation, expiration, fills, and order submission with explicit conversion rates

**Implementation**:
- Existing counters: `self.gate_audit['setup_created']`, `self.expired_setup_count`, `self.entry_submitted_count`
- Duration tracking: `self.setup_active_durations` (populated on setup completion)
- Entry type breakdown: `self.retest_fill_count`, `self.chase_fill_count`

**Output**:
```
B) SETUP LIFECYCLE METRICS:

  Setups created:                12
  Setups expired (no fill):       1  (8.3%)
  Entries filled (total):        11

  Entry orders submitted:        17

  Conversion rates:
    Setup → Entry:             11/12 = 91.7%
    Order Submitted → Filled:  11/17 = 64.7%

  Entry type breakdown:
    Retest fills:                 7
    Chase fills:                  4
```

**Key Insight**: High setup→entry (91.7%) but lower order→filled (64.7%) suggests orders get repriced/canceled during chase escalation.

### C) Opportunity Loss Tracking (strategy_4h_hybrid.py:2613-2690)

**What**: Tracks ROC-OK and compression bars missed due to state machine lockout (state != FLAT)

**Implementation**:
- Counters incremented on every 4h bar in `process_bar_1m()`:
  ```python
  if current_state == StrategyState.SETUP_ACTIVE:
      self.opp_loss['roc_ok_while_setup_active'] += 1
  elif current_state == StrategyState.IN_POSITION:
      self.opp_loss['roc_ok_while_position_open'] += 1
  ```
- Denominator clarified: compression_recent_12 on BASE_OK bars (regime + viability passed)

**Output**:
```
C) OPPORTUNITY LOSS WHILE LOCKED (bars missed due to state != FLAT):

  ROC-OK bars (total on BASE_OK bars):      68
    Missed while SETUP_ACTIVE:               0  (0.0%)
    Missed while IN_POSITION:               56  (82.4%)
  Total ROC-OK bars locked out:             56  (82.4%)

  Compression_recent_12 bars:
    Total on BASE_OK bars:                 490  (denominator for % below)
    Total on ALL 4h bars (context):        610
    Missed while SETUP_ACTIVE:               0  (0.0%)
    Missed while IN_POSITION:               21  (4.3%)
  Total compression bars locked out:        21  (4.3%)
```

**Key Insight**: 82.4% of ROC-OK bars are locked out by IN_POSITION state, not SETUP_ACTIVE. Option 2 (queue) won't help much since bottleneck is position holding, not setup processing.

---

## 2. Stage C Runner Policy Implementation

### Core Logic (strategy_4h_hybrid.py:1649-1711)

**Compression Context Capture** (at entry fill time):
```python
# Check compression_now, compression_recent_6, compression_recent_12
bb_width_history = self.bb_width_history.get(symbol, [])
entry_compressed_recent_12 = self._check_recent_compression(
    bb_width_pct_history=bb_width_history,
    threshold=self.config.bb_width_pct_threshold,
    lookback=12  # 48h lookback
)
```

**Policy Selection**:
```python
if self.config.use_compression_runner_policy and entry_compressed_recent_12:
    # Compressed entry: use compressed parameters
    runner_qty_frac = self.config.runner_qty_frac_compressed
    trail_mult = self.config.trail_mult_compressed
else:
    # Normal entry OR policy disabled: use normal parameters
    runner_qty_frac = self.config.runner_qty_frac_normal
    trail_mult = self.config.trail_mult_normal
```

**Position Creation**:
```python
runner_qty = qty * runner_qty_frac
position = Position(..., runner_qty=runner_qty, trail_mult=trail_mult)
position.entry_compressed_recent_12 = entry_compressed_recent_12
```

**Adaptive Trailing** (strategy_4h_hybrid.py:1833):
```python
# Use position's trail_mult (set at entry time) instead of config default
trail_price = position.highest_close_4h_since_entry * (1 - position.trail_mult * atr_pct)
```

### Configuration Parameters (config_4h_hybrid.py:168-185)

```python
# Phase 2.4: Stage C - Compression-Based Runner Policy
use_compression_runner_policy: bool = False  # Feature flag (DISABLED by default)

# Compressed entries (tighter ranges expected)
runner_qty_frac_compressed: float = 0.20
trail_mult_compressed: float = 2.0

# Normal entries (wider ranges expected)
runner_qty_frac_normal: float = 0.20
trail_mult_normal: float = 2.0
```

### Verification Logging (strategy_4h_hybrid.py:1668-1682)

When `use_compression_runner_policy=True`, prints at each fill:
```
🔍 STAGE C VERIFICATION - Fill @ 2025-08-28 12:31:00
   Symbol: BTC-USD
   Entry price: $112786.80
   Entry type: RETEST
   Compression context:
     - compression_now (current bar):    True
     - compression_recent_6 (24h):       True
     - compression_recent_12 (48h):      True
   BB width history (last 12 bars): [35.8, 22.5, 20.0, 18.3, 19.2, ...]
   Threshold: 30
   → Using COMPRESSED runner policy:
     - runner_qty_frac: 30.00%
     - trail_mult: 1.5x
```

### Data Model Updates

**Position** (strategy_4h_hybrid.py:143, 162):
```python
trail_mult: float = 2.0  # Adaptive trail multiplier (stored at entry time)
entry_compressed_recent_12: bool = False  # Compression context flag
```

**Trade** (strategy_4h_hybrid.py:217):
```python
entry_compressed_recent_12: bool = False  # Stored for post-run analysis
```

---

## 3. Trade Breakdown by Compression Context (strategy_4h_hybrid.py:2692-2762)

**What**: Post-run comparison of compressed vs normal entry performance

**Output** (180d BTC-USD test):
```
STAGE C: TRADE BREAKDOWN BY ENTRY COMPRESSION CONTEXT
================================================================================

Total trades: 11
  Compressed entries (compression_recent_12=True): 5
  Normal entries (compression_recent_12=False):    6

Performance Comparison:

Metric                         Compressed          Normal      Difference
--------------------------------------------------------------------------------
Count                                   5               6              -1
Win Rate                           60.0%          16.7%          43.3%
Avg Net P&L               $         -0.74 $         -3.07 $          2.33
Median Net P&L            $          0.10 $         -3.64 $          3.74
TP1 Hit Rate                       60.0%          16.7%          43.3%
TP2 Hit Rate                       20.0%          16.7%           3.3%
Avg Hold (hours)                    77.5           40.3           37.2
```

**Key Finding**: 🚨 **Compressed entries OUTPERFORM normal entries**
- Win rate: 60% vs 17% (+43%)
- Median net P&L: $0.10 vs -$3.64 (+$3.74)
- TP1 hit: 60% vs 17% (+43%)
- Hold time: 77.5h vs 40.3h (+37h longer!)

**Implication**: Original hypothesis WRONG. Compressed entries hold LONGER and WIN MORE, suggesting they benefit from WIDER trails and LARGER runners (not tighter/smaller).

---

## 4. Bug Fixes

### State Enum Consistency (strategy_4h_hybrid.py:730, 752, 781, 789, 804, 833, 873, 1755, 1902)

**Problem**: Legacy string-based state assignments (`"FLAT"`, `"IN_POSITION"`) conflicted with StrategyState enum dict keys
**Fix**: Replaced all 9 occurrences with enum values:
```python
# Before
self.state[symbol] = "FLAT"

# After
self.state[symbol] = StrategyState.FLAT
```

---

## 5. Testing & Validation

### Test 1: Observability Metrics (180d BTC-USD)
```bash
python3 run_single_180d.py
```

**Results**:
- ✅ State occupancy: 924 FLAT, 154 IN_POSITION, 0 setup states (explained)
- ✅ Fill rates: Setup→Entry 91.7%, Order→Filled 64.7%
- ✅ Opportunity loss: 56/68 ROC-OK bars (82.4%) locked by IN_POSITION
- ✅ Compression denominator: 490 on BASE_OK bars, 610 on ALL bars

### Test 2: Stage C Differential Parameters (180d BTC-USD)
```bash
python3 test_stage_c_runner_policy.py
```

**Config**:
- Compressed: 30% runner, 1.5x trail
- Normal: 10% runner, 2.5x trail

**Results**:
- ✅ 11 fills total: 5 compressed, 6 normal
- ✅ All fills logged with 🔍 verification markers
- ✅ Policy switched correctly based on compression_recent_12
- ✅ Runner/trail values matched config at each fill

### Test 3: Trade Breakdown Output
```bash
python3 run_single_180d.py | grep -A 40 "STAGE C: TRADE BREAKDOWN"
```

**Results**:
- ✅ Compressed vs normal comparison table generated
- ✅ Win rate, P&L, TP rates, hold time all computed correctly
- ✅ Interpretation guidance provided

---

## 6. Files Modified

| File | Changes | Lines |
|------|---------|-------|
| `strategy_4h_hybrid.py` | Observability output, Stage C logic, state enum fixes, trade breakdown | ~200 |
| `config_4h_hybrid.py` | Stage C parameters, test config function | ~40 |
| `models.py` | Position/Trade dataclass updates (trail_mult, entry_compressed) | ~5 |
| `test_stage_c_runner_policy.py` | Stage C verification test script | ~150 (new) |
| `STAGE_C_AB_TEST_PLAN.md` | A/B test plan documentation | ~300 (new) |

---

## 7. Next Steps (Recommended Order)

### Immediate (Production Path)
1. **Run A/B Test**: Stage C OFF (baseline) vs Stage C ON (conservative params)
   - Use plan in `STAGE_C_AB_TEST_PLAN.md`
   - Compare net P&L, Sharpe, max drawdown
   - Validate compressed vs normal entry differences

2. **Revise Stage C Hypothesis**: Based on 180d data showing compressed entries outperform:
   - **Option A**: Invert parameters (compressed gets WIDER trail, LARGER runner)
   - **Option B**: Keep Stage C disabled, use compression as setup filter only
   - **Option C**: Parameter sweep to find optimal compressed/normal split

3. **ROC Threshold Tuning**: Use outcome-driven metrics (expectancy, drawdown) not just annualized setup counts
   - Current: P70 (1.323) for 21 setups/year
   - Target: 60-120 setups/year with positive expectancy

### Future (Option 2 Path)
4. **Setup Queue Analysis**: If opportunity loss remains >70% after ROC tuning
   - Design 1-deep queue (not full concurrent evaluation)
   - Prioritize by roc_score or compression_recent_12
   - Estimate incremental complexity vs benefit

5. **Entry Price Optimization**: Tune retest_offset, chase_offset based on expired setup gap analysis
   - Check `expired_setups` list for distance-to-fill metrics
   - Widen chase offsets if gaps are consistently >0.5%

---

## 8. Production Deployment Checklist

Before deploying Stage C to AWS:
- [ ] Verify `use_compression_runner_policy=False` in production config (safe default)
- [ ] Run A/B test on 180d + 365d windows
- [ ] Document A/B test results in backtest archive
- [ ] If deploying Stage C ON: commit conservative parameters, tag release
- [ ] Update `.claude/DEPLOYMENT.md` with Stage C deployment notes
- [ ] Monitor live performance vs backtest expectations for 30 days

---

## 9. Key Metrics Summary (180d Baseline)

| Metric | Value | Notes |
|--------|-------|-------|
| Total 4h bars | 1,078 | Processable boundaries (df_4h=1080 includes edge bars) |
| Regime OK bars | 302 (28.0%) | EMA200 regime filter |
| BASE_OK bars | 204 (18.9%) | Regime + viability |
| ROC-OK bars | 68 (6.3%) | BASE_OK + roc_score >= threshold |
| Structure-OK bars | 12 (1.1%) | ROC-OK + compression filter |
| Setups created | 12 | 100% conversion from structure_ok |
| Entries filled | 11 | 91.7% setup→entry conversion |
| Entry orders submitted | 17 | 64.7% order→filled conversion |
| State occupancy (FLAT) | 924 bars (85.7%) | ~221k minutes |
| State occupancy (IN_POSITION) | 154 bars (14.3%) | ~37k minutes |
| ROC-OK bars locked | 56/68 (82.4%) | Missed due to IN_POSITION state |
| Compression bars locked | 21/490 (4.3%) | Missed due to IN_POSITION state |
| Compressed entry trades | 5/11 (45.5%) | Win rate: 60%, Hold: 77.5h |
| Normal entry trades | 6/11 (54.5%) | Win rate: 16.7%, Hold: 40.3h |

---

## 10. Lessons Learned

1. **State Sampling Matters**: Intra-bar transitions invisible when sampling at 4h close. Added NOTE to clarify.

2. **Compression Context Reversed**: Original hypothesis (compressed = tighter trail) was WRONG. Data shows compressed entries hold longer and win more. Suggests compression identifies higher-quality setups, not just tighter ranges.

3. **Opportunity Loss Driver**: 82.4% of ROC-OK bars blocked by IN_POSITION, not SETUP_ACTIVE. Option 2 queue won't help much unless we allow concurrent positions or reduce hold time.

4. **Fill Rate Decomposition**: Setup→Entry (91.7%) is high, but Order→Filled (64.7%) shows slippage due to chase repricing. Future work: analyze order cancellation reasons.

5. **Denominator Clarity**: Always label what denominators represent (BASE_OK vs ALL bars). Avoids confusion during analysis.

---

## 11. References

- **Session Plan**: `.claude/sessions/2026-01-30-4h-hybrid-maker-strategy.md`
- **A/B Test Plan**: `backtest/STAGE_C_AB_TEST_PLAN.md`
- **Test Script**: `backtest/test_stage_c_runner_policy.py`
- **Config**: `backtest/config_4h_hybrid.py:168-185, 478-501`
- **Strategy**: `backtest/strategy_4h_hybrid.py:1649-1711, 2540-2762`

---

**End of Session** - Option 1 observability and Stage C runner policy implementation complete. Ready for A/B testing. 🎯
