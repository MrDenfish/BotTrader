# TP/SL Optimization Analysis

**Date**: 2025-11-30
**Branch**: feature/tpsl-optimization
**Analysis Period**: Last 30 days
**Data Source**: `fifo_allocations` table

## Executive Summary

**CRITICAL ISSUE IDENTIFIED**: The system is losing money primarily due to asymmetric win/loss distribution. Large losses outnumber large wins 2:1 (955 vs 512 allocations).

**Current Performance**:
- Win Rate: 33.4%
- Total P&L: -$804.34 (LOSING)
- Avg Win: $1.15
- Avg Loss: -$1.09
- **Actual R:R Ratio: 1.06:1** (almost 1:1)

**Root Cause**: Despite configured 2.5% TP / 1% SL (2.5:1 ratio), positions are NOT achieving this ratio in practice. 40.4% of allocations become large losses (>$1), while only 21.7% become large wins.

## Current TP/SL Configuration

From `/opt/bot/.env`:
```
TAKE_PROFIT=0.025   # +2.5%
STOP_LOSS=-0.01     # -1.0%
```

**Target R:R Ratio**: 2.5:1
**Actual R:R Ratio**: 1.06:1

**Gap**: Configured parameters are NOT being enforced in practice.

## P&L Distribution Analysis

### Allocation Breakdown

```
Category             | Count | %     | Avg P&L | Total P&L  | Issue
---------------------|-------|-------|---------|------------|------------------------
Large Wins (>$1)     |   512 | 21.7% |  $1.71  |   +$874.16 | Good - hitting TP
Small/Medium Wins    |   276 | 11.7% |  $0.12  |    +$34.06 | Exiting too early
Small Losses         |   581 | 24.6% | -$0.08  |    -$43.79 | Good - quick exits
Medium Losses        |    38 |  1.6% | -$0.77  |    -$29.28 | Should trigger SL
LARGE LOSSES (>$1)   |   955 | 40.4% | -$1.73  | -$1,639.49 | 🚨 PROBLEM - SL not working
```

### Key Findings

1. **Large Losses Dominate**: 40.4% of all allocations lose >$1
   - This should be impossible with -1% SL
   - Suggests SL is not triggering or is being bypassed

2. **Large Wins Underrepresented**: Only 21.7% achieve >$1 profit
   - Many winning positions exit early (276 allocations with <$1 profit)
   - TP may be set too conservative or signal exits interfering

3. **Asymmetric Distribution**:
   - Large Losses: 955 allocations (-$1,640)
   - Large Wins: 512 allocations (+$874)
   - **Net Impact: -$766 from large trades alone**

## Hypothesis: Why Is This Happening?

### Possible Causes

1. **SL Not Triggering**:
   - Positions may be exiting via signals instead of SL
   - Phase 5 signal-based exits may override SL
   - Slippage on SL orders causing worse fills

2. **TP Exiting Too Early**:
   - Signal-based exits may close winners before hitting TP
   - Trailing stop may be too tight

3. **Position Sizing Issues**:
   - Partial fills causing incorrect TP/SL calculations
   - FIFO allocations with different cost basis

## Recommended Solutions

### Option 1: Tighten Stop Loss (Reduce Loss Size)

**Current**: SL = -1%
**Proposed**: SL = -0.5%

**Expected Impact**:
- Reduce large loss allocations from 955 to ~500
- Save approximately $800/month
- Win rate stays same, but R:R improves to ~2:1

**Risks**:
- May increase number of stopped-out trades (lower win rate)
- Could trigger SL during normal volatility

### Option 2: Widen Take Profit (Increase Win Size)

**Current**: TP = +2.5%
**Proposed**: TP = +4.0%

**Expected Impact**:
- Increase large win allocations from 512 to ~700
- Capture more upside on winning trades
- R:R improves to ~3.5:1

**Risks**:
- Fewer trades hit TP (win rate may decrease)
- Need higher win rate to compensate

### Option 3: Enforce Strict TP/SL (Disable Signal Exits)

**Current**: Signal exits can override TP/SL
**Proposed**: TP/SL takes priority over signals

**Expected Impact**:
- Enforce configured 2.5:1 ratio
- Eliminate early exits
- More predictable outcomes

**Risks**:
- May miss optimal exit signals
- Reversal signals won't trigger exits

### Option 4: Dynamic TP/SL Based on Volatility (ATR)

**Current**: Fixed 2.5% TP / 1% SL for all symbols
**Proposed**: Calculate TP/SL based on Average True Range (ATR)

```python
atr_multiplier_tp = 3.0
atr_multiplier_sl = 1.5

take_profit = entry_price + (ATR * atr_multiplier_tp)
stop_loss = entry_price - (ATR * atr_multiplier_sl)
```

**Expected Impact**:
- Volatile symbols get wider TP/SL
- Stable symbols get tighter TP/SL
- Better adaptation to market conditions
- R:R ratio maintained at 2:1

**Benefits**:
- Reduces SL triggers during normal volatility
- Captures more gains on trending moves
- Adapts to each symbol's characteristics

## Recommended Action Plan

### Phase 1: Immediate Fix (Deploy This Week)

1. **Tighten Stop Loss**: -1% → -0.75%
   - Reduce catastrophic losses
   - Expected savings: ~$400/month

2. **Enforce TP/SL Priority**:
   - Modify position_monitor to check TP/SL before signals
   - Ensure SL actually triggers

3. **Monitor for 7 days**:
   - Track large loss percentage (target < 25%)
   - Verify SL is triggering

### Phase 2: Optimization (Next Week)

1. **Implement ATR-based Dynamic TP/SL**:
   - Calculate ATR for each symbol
   - Set TP = entry + (3 * ATR)
   - Set SL = entry - (1.5 * ATR)

2. **Backtest on Historical Data**:
   - Test on last 60 days of trades
   - Compare win rate and P&L vs current approach

3. **Deploy if Improvement > 10%**

### Phase 3: Continuous Optimization

1. **A/B Test Different Ratios**:
   - Test 2:1, 2.5:1, 3:1 R:R ratios
   - Find optimal for current market conditions

2. **Monthly Review**:
   - Re-analyze P&L distribution
   - Adjust TP/SL based on performance

## Expected Outcomes

### Conservative Estimate (Phase 1 Only)

- Win Rate: 33.4% → 35-38% (reduce large losses)
- R:R Ratio: 1.06:1 → 1.5:1 (tighter SL)
- Monthly P&L: -$804 → -$400 (50% improvement)

### Optimistic Estimate (Phase 1 + 2)

- Win Rate: 33.4% → 40-45% (better exits)
- R:R Ratio: 1.06:1 → 2:1 (dynamic TP/SL)
- Monthly P&L: -$804 → **+$200 to +$400 (PROFITABLE)**

## Next Steps

1. ✅ Analyze current TP/SL impact (DONE)
2. 🔄 Investigate why SL is not triggering
3. ⏳ Implement tightened SL (-0.75%)
4. ⏳ Add TP/SL enforcement logic
5. ⏳ Deploy and monitor for 7 days
6. ⏳ Implement ATR-based dynamic TP/SL
7. ⏳ Backtest and optimize

## Critical Question

**Why are 955 allocations (40.4%) losing >$1 when SL is set at -1%?**

This requires immediate investigation of:
1. Position monitor exit logic
2. Signal-based exit priority
3. Actual SL order placement
4. Slippage on SL fills

Without fixing this, the system will continue losing money regardless of other optimizations.
