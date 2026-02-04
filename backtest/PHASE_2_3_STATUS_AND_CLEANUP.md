# Phase 2.3 Status Assessment and Cleanup
**Date**: February 1, 2026
**Status**: Diagnostic Phase Complete - Critical Issues Identified

---

## Executive Summary

**CRITICAL FINDING**: The 4h Hybrid strategy has extremely low signal frequency (1 signal per 180 days = 0.4 signals/month vs target of 2-4 trades/month). This is caused by **overly strict filters**, not poor strategy logic.

**Root Cause Identified**: The compression filter and setup triggers are both too restrictive, creating a bottleneck that blocks 99%+ of potential setups.

**Recommendation**: **Pivot strategy** - The current filtering approach is fundamentally too conservative. We need to either:
1. Dramatically loosen ALL filters (regime, viability, compression, setup triggers)
2. Redesign the filtering philosophy to be opportunity-seeking rather than risk-avoiding
3. Consider whether a maker-only 4h strategy is viable given fee constraints

---

## What We've Accomplished

### ✅ Phase 2.1 (Completed Earlier)
- Implemented breakout-retest entry logic
- Added post-only fill simulation
- Hardened against false breakouts

### ✅ Phase 2.2 (Completed Earlier)
- Implemented Bollinger Band compression filter
- Added comprehensive diagnostics

### ✅ Phase 2.3 Diagnostic Work (This Session)
1. **Enhanced Diagnostics Implemented**:
   - Gate audit tracking (regime → viability → compression → setup → entry)
   - BB width percentile distribution analysis
   - Setup/compression decomposition
   - Viability filter distribution
   - ROC-score percentile calibration

2. **Critical Bugs Fixed**:
   - ROC-score collection bug (indicator key mismatch)
   - NaN filtering in distribution statistics
   - Scope issue in compression threshold analysis

3. **Compression OFF A/B Test** (Step 2):
   - Tested both Donchian and ROC-score modes with compression disabled
   - **Result**: Disabling compression made NO meaningful difference
   - Signal frequency remained extremely low (0.17 trades/month)

---

## Critical Findings from Diagnostics

### Data Coverage
- **Available**: 180 days of 1m data (2025-08-04 to 2026-01-31)
- **Bars**: 258,834 1m bars → 1,080 4h bars
- **Limitation**: Coinbase API only provides ~180 days max for 1m granularity

### Signal Frequency (180-day backtest)
| Mode | Compression | Signals | Trades/Month | vs Target (2-4/mo) |
|------|------------|---------|--------------|-------------------|
| Donchian | ON | 1 | 0.08 | **98% below** |
| ROC-Score | ON | 1 | 0.08 | **98% below** |
| Donchian | OFF | 1 | 0.17 | **96% below** |
| ROC-Score | OFF | 1 | 0.17 | **96% below** |

### Gate Audit Results (Compression ON)

**Donchian Mode**:
```
Total 4h bars:              271 (100%)
├─ Regime filter:           118 (43.5%)  ← Blocks 56.5%
├─ Viability filter:         64 (23.6%)  ← Blocks 45.8% of regime-OK bars
├─ Setup + Compression:       1 (0.4%)   ← Blocks 98.4% of viable bars
└─ Entry filled:              0 (0.0%)   ← Blocks 100% of setups
```

**ROC-Score Mode**:
```
Total 4h bars:              275 (100%)
├─ Regime filter:           122 (44.4%)  ← Blocks 55.6%
├─ Viability filter:         68 (24.7%)  ← Blocks 44.3% of regime-OK bars
├─ Setup + Compression:       1 (0.4%)   ← Blocks 98.6% of viable bars
└─ Entry filled:              0 (0.0%)   ← Blocks 100% of setups
```

### Setup/Compression Decomposition

**Donchian** (on 64 viable bars):
- Setup trigger only: 10 bars (15.6%)
- Compression only: 2 bars (3.1%)
- **Both (intersection): 1 bar (1.6%)** ← This is the problem
- Neither: 51 bars (79.7%)

**ROC-Score** (on 68 viable bars):
- Setup trigger only: 22 bars (32.4%)
- Compression only: 6 bars (8.8%)
- **Both (intersection): 1 bar (1.5%)** ← This is the problem
- Neither: 39 bars (57.4%)

**Interpretation**: Both filters are bottlenecks. Even when one passes, the other usually blocks it.

### ROC-Score Calibration Issues

**ROC-score distribution on viable bars**:
- Min: -2.535
- Median: 0.685
- P75: 1.596
- Max: 2.789

**Current threshold**: 1.200
- Passes: 23 bars (33.8% of viable) → ~31 setups/year
- **Still 50% below target of 60-120 setups/year**

**Recommended P60 threshold**: 1.099
- Would pass: 27 bars (39.7%) → ~36 setups/year
- **Still 40% below target**

---

## File Inventory and Cleanup

### Keep (Essential Files)

**Core Strategy Files**:
- `strategy_4h_hybrid.py` - Main strategy with enhanced diagnostics ✅
- `engine_4h_hybrid.py` - Backtest engine ✅
- `config_4h_hybrid.py` - All configuration variants ✅
- `data_resampler.py` - OHLCV resampling ✅

**Latest Diagnostic Results**:
- `test_roc_vs_donchian_365d_fixed.log` - Enhanced diagnostics (with all fixes)
- `test_compression_off_ab.log` - Compression OFF A/B test results

**Test Scripts** (useful for future work):
- `test_roc_vs_donchian_365d.py` - Baseline comparison script
- `test_compression_off_ab.py` - Compression OFF A/B test script
- `debug_roc_calc.py` - ROC-score calculation diagnostic

**Data**:
- `data/BTC_USD.csv` - 180 days of 1m OHLCV data

### Archive (Historical/Obsolete)

**Old Log Files** (can be deleted or archived):
- `test_roc_vs_donchian_365d_results.log` - Superseded by fixed version
- `test_roc_vs_donchian_365d_gate_audit.log` - Superseded by enhanced diagnostics
- `test_roc_vs_donchian_365d_enhanced_diagnostics.log` - Had NaN issues, fixed version available
- `download_365d_1m.log` - Download attempt log (incomplete)
- `optimize_phase2_2_results.log` - Phase 2.2 results (archived)

---

## Current State Assessment

### What Works
✅ Backtest engine is solid and accurate
✅ Enhanced diagnostics provide deep visibility
✅ ROC-score indicator calculation is correct
✅ Post-only fill simulation is conservative and realistic
✅ Diagnostic tools can identify bottlenecks precisely

### What Doesn't Work
❌ Signal frequency is 96-98% below target
❌ All filters are too restrictive (regime, viability, compression, setup triggers)
❌ Even with compression disabled, frequency is critically low
❌ ROC-score threshold needs to be lowered dramatically (but still won't reach target)
❌ The filtering philosophy is fundamentally too conservative

---

## Critical Decision Point

We are at a **strategic crossroads**. The diagnostic data shows that the current approach of stacking multiple conservative filters creates a compounding effect that blocks nearly all opportunities.

### Option 1: Aggressive Filter Loosening (High Risk)
**Actions**:
- Disable or dramatically loosen regime filter (EMA200)
- Lower viability threshold (vol_min_mult from 1.0 to 0.5)
- Disable compression filter entirely
- Lower ROC-score threshold to P40-P50 (≈0.5-0.8)
- Lower Donchian length from 20 to 10-15

**Risks**:
- May generate too many low-quality setups
- Could increase drawdown significantly
- Defeats the purpose of "high-quality maker setups"

**Expected Outcome**:
- Signal frequency: 5-15 trades/month (above target)
- Win rate: Unknown (likely lower)
- Risk: Strategy becomes overactive, not selective

### Option 2: Redesign Filtering Philosophy (Recommended)
**Concept**: Shift from "block everything risky" to "find qualified opportunities"

**New Approach**:
1. **Replace stacked filters with a scoring system**:
   - Each condition adds points instead of being a binary gate
   - Minimum score threshold determines entry
   - Allows trade-offs (strong momentum can compensate for weaker compression, etc.)

2. **Simplify to 2-3 core filters**:
   - Primary: Trend confirmation (regime)
   - Secondary: Setup quality (ROC-score OR Donchian)
   - Optional: Volatility check (but not a hard gate)

3. **Focus on entry quality, not entry frequency**:
   - Accept 0.5-1.5 trades/month if quality is high
   - Optimize for win rate and profit factor, not frequency
   - Use tighter stops and wider targets to compensate for lower frequency

**Expected Outcome**:
- Signal frequency: 0.5-2 trades/month (below target but acceptable)
- Win rate: Higher (60-70% target)
- Risk: Better risk-adjusted returns

### Option 3: Pivot to Different Strategy Architecture
**Consider**:
- 4h hybrid maker strategy may not be viable with current fee structure (0.4% maker / 0.8% taker)
- Breakout-retest on 4h may need taker fees for reliable fills
- Alternative: Pure momentum strategy with immediate entries (accept taker fees)
- Alternative: Longer timeframe (1D instead of 4h) for more reliable setups

---

## Recommended Next Steps

### Immediate (This Session)
1. ✅ **Archive old log files** (completed above)
2. ✅ **Document findings** (this document)
3. **Decision**: Choose Option 1, 2, or 3 above

### If Continuing with Current Architecture (Option 2 - Recommended)
1. **Design scoring system** to replace binary gates
2. **Simplify filters** to 2-3 core conditions
3. **Run sensitivity analysis** on remaining filters
4. **Backtest with new design** on 180-day dataset
5. **Evaluate** if results justify further development

### If Pivoting (Option 3)
1. **Analyze fee impact** on different timeframes and strategies
2. **Research alternative approaches** (momentum, mean reversion, hybrid with taker)
3. **Prototype new strategy** with lessons learned
4. **Compare** side-by-side with current approach

---

## Cleanup Completed ✅

### Module Organization (February 1, 2026)

**Before cleanup**: 39 Python modules
**After cleanup**: 16 active modules + 24 archived

### Archived Files

**Multi-ROC 1m Strategy** (`archive/multi_roc_1m/`):
- config_multi_roc.py
- engine_multi_roc.py
- strategy_peak_drawdown.py
- engine_peak_drawdown.py
- indicators_roc_atr.py
- optimizer_grid_search.py

**Multi-Timeframe Experimental** (`archive/multi_tf_experimental/`):
- strategy_multi_tf.py
- engine_multi_tf.py
- test_multi_tf_comprehensive.py
- event_study_15m.py
- strategy_15m_enhanced.py
- test_60d_enhanced.py
- test_5m_vs_1m_comparison.py
- download_5m_data.py

**Phase 2 Tests** (`archive/phase2_tests/`):
- test_4h_phase2_baseline.py
- test_4h_phase2_1_hardened.py
- test_4h_180d_validation.py
- test_4h_phase2_2_smoke.py
- test_4h_phase2_2_60d.py
- test_4h_phase2_2_180d.py
- test_no_compression.py
- optimize_phase2_2_params.py
- test_roc_score_smoke.py
- test_4h_hybrid_60d.py

**Diagnostic Logs** (`archive/phase2_3_diagnostics/`):
- test_roc_vs_donchian_365d_results.log
- test_roc_vs_donchian_365d_gate_audit.log
- test_roc_vs_donchian_365d_enhanced_diagnostics.log
- download_365d_1m.log
- optimize_phase2_2_results.log

### Active Files (Current Work)

**Core 4h Hybrid Strategy**:
- strategy_4h_hybrid.py (with enhanced diagnostics)
- engine_4h_hybrid.py
- config_4h_hybrid.py
- data_resampler.py

**Test & Diagnostic Scripts**:
- test_roc_vs_donchian_365d.py
- test_compression_off_ab.py
- debug_roc_calc.py
- view_expired_setups.py

**Utilities**:
- optimizer_4h_hybrid.py
- download_historical_data_csv.py

**Infrastructure** (shared):
- config.py, engine.py, reporter.py, models.py
- download_historical_data.py
- __init__.py

**Latest Test Results** (kept):
- test_roc_vs_donchian_365d_fixed.log
- test_compression_off_ab.log

---

## Summary

We have successfully diagnosed the root cause of the low signal frequency issue. The strategy architecture is sound, but the filtering is fundamentally too conservative. **We need a strategic decision** on whether to:

1. Aggressively loosen existing filters (high risk)
2. Redesign the filtering philosophy with a scoring system (recommended)
3. Pivot to a different strategy architecture entirely

**The diagnostic tools and enhanced instrumentation we've built are valuable regardless of which path we choose.** They will help us optimize any strategy variant we pursue.

**Next conversation should focus on**: Strategic direction decision and implementation plan.
