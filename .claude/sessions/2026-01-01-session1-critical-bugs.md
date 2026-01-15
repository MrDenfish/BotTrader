# Session 1: Critical Bug Fixes
**Date**: January 1, 2026
**Priority**: 🔴 CRITICAL
**Status**: ✅ COMPLETE (with follow-up actions identified)
**Estimated Time**: 2-3 hours
**Actual Time**: ~3 hours

---

## Objectives

1. **Investigate RECALL-USD Dec 4 Disaster** - Understand why single trade lost -$73.35
2. **Fix Metadata Caching Bug** - Resolve why `_cache_strategy_metadata()` not executing

---

## Issue 1: RECALL-USD Disaster Investigation

### Background
- **Date**: December 4, 2025
- **Symbol**: RECALL-USD
- **Total Loss**: -$258.24 across 4 trades
- **Worst Single Loss**: -$73.35
- **Impact**: 70.5% of entire 30-day losses (-$366.56 total)

### Questions to Answer
1. What was the entry price and exit price for the -$73.35 loss?
2. What percentage loss does this represent?
3. Why didn't the hard stop (-6.0%) prevent this?
4. Was this a failed stop-loss order or extreme slippage?
5. What was the position size (should be ~$20)?

### Investigation Started
*Timestamp: 2026-01-01 19:55 UTC*

### Findings

#### Trade Details
All 4 Dec 4 trades showed tiny price movements but massive losses:

| Time (UTC) | Side | Size | Price | Price Δ% | Position Value | Cost Basis | PNL |
|------------|------|------|-------|----------|----------------|------------|-----|
| 05:54:32 | buy | 226.27 | $0.1373 | - | $31.07 | - | - |
| 05:58:25 | sell | 226.25 | $0.1357 | -1.17% | $30.70 | **$87.23** | -$56.57 |
| 06:19:28 | buy | 229.28 | $0.1359 | - | $31.16 | - | - |
| 06:20:35 | sell | 229.28 | $0.1360 | +0.07% | $31.18 | **$94.80** | -$63.66 |
| 08:34:25 | buy | 243.13 | $0.1273 | - | $30.95 | - | - |
| 08:35:32 | sell | 243.13 | $0.1278 | +0.39% | $31.07 | **$95.69** | -$64.66 |
| 23:17:33 | buy | 269.60 | $0.1228 | - | $33.11 | - | - |
| 23:19:00 | sell | 269.60 | $0.1221 | -0.57% | $32.92 | **$106.23** | -$73.35 |

**Total Loss**: -$258.24

#### Root Cause: Cost Basis Calculation Bug 🚨

**The Problem**: `cost_basis_usd` is **3x higher** than actual purchase cost!

- **Expected cost basis**: ~$31 (size × price = 226 × $0.137)
- **Actual cost_basis in database**: $87.23 to $106.23

**Why this happened**:
1. RECALL-USD crashed **-73%** between Oct 27 ($0.48) and Dec 4 ($0.13)
2. October trades had ~$60 position sizes at $0.48 price
3. Database may have incorrect FIFO inventory from October
4. Or reconciliation process assigned wrong cost basis

**Historical Context**:
- Oct 26-27: RECALL traded at $0.45-$0.49 (all positions closed properly, position = 0)
- Dec 4: Price crashed to $0.12-$0.14 (-73% decline)
- Position tracking shows 0.00 after Oct 27, so no old inventory should exist

#### This is NOT a stop-loss failure!

The stop-loss system worked correctly - the actual price movements were tiny (-1.17%, +0.07%, +0.39%, -0.57%).

**This is a data reconciliation/cost basis calculation bug** that assigned inflated cost_basis values to the Dec 4 sells, creating phantom losses.

#### Impact Assessment

- **Actual trading loss** (based on price movement): ~$4-5
- **Phantom loss** (due to bad cost_basis): ~$253
- **This accounts for 70% of your 30-day losses!**

#### Action Items

1. ✅ **Identified root cause**: Cost basis calculation bug
2. 🔧 **Fix required**: Review trade_recorder FIFO/cost basis logic
3. 📊 **Data correction**: May need to recalculate historical P&L
4. ⚠️ **Prevention**: Add validation that cost_basis ≈ size × avg_entry_price

---

## Issue 2: Metadata Caching Bug Investigation

### Background
- **System**: Trade-strategy linkage system
- **Function**: `_cache_strategy_metadata()` in `webhook/listener.py`
- **Problem**: Function appears to never execute despite webhooks being received
- **Impact**: 0% strategy linkage, optimization impossible

### Evidence
- Dec 30 webhooks received:
  - 20:54:36 UTC: ZRX-USD sell signal (ROC_MOMO, score 2.0)
  - 22:04:45 UTC: ZRX-USD buy signal (ROC_MOMO, score 2.0)
- Expected: `STRATEGY_CACHE_DEBUG` logs from lines 1009-1011, 1124
- Actual: **NO debug logs appeared**

### Debug Logging Added (Commit cc95e62)

**webhook/listener.py:1009-1011**:
```python
self.logger.warning(f"🔧 [DEBUG] About to call _cache_strategy_metadata for {trade_data.get('trading_pair')}")
self._cache_strategy_metadata(trade_data)
self.logger.warning(f"🔧 [DEBUG] _cache_strategy_metadata returned for {trade_data.get('trading_pair')}")
```

**webhook/listener.py:1124**:
```python
def _cache_strategy_metadata(self, trade_data: dict) -> None:
    self.logger.warning(f"🔧 [DEBUG] _cache_strategy_metadata ENTERED with trade_data keys: {list(trade_data.keys())}")
```

### Investigation Started
*Timestamp: 2026-01-01 20:10 UTC*

### Findings

#### Timeline Analysis
- **Dec 30, 12:27 UTC**: Debug logging deployed (commit cc95e62)
- **Dec 30, 20:54 UTC**: ZRX-USD SELL webhook received
- **Dec 30, 22:04 UTC**: ZRX-USD BUY webhook received
- **Jan 1, 00:45 UTC**: Webhook container restarted (current session)
- **Jan 1, 20:15 UTC**: No webhooks received since restart

#### Critical Discovery 🔍

**The Dec 30 webhooks occurred BEFORE the container restart!**

The debug logging was deployed to the codebase at 12:27 UTC, but the Docker container was still running the OLD code until it was restarted at 00:45 UTC on Jan 1.

Therefore:
- Dec 30 webhooks at 20:54 and 22:04 ran on PRE-debug-logging code
- No `STRATEGY_CACHE_DEBUG` logs expected for those webhooks
- **We haven't tested the debug logging yet** - no webhooks since Jan 1 restart

#### Current Status

**Cannot diagnose metadata caching bug without new webhook data.**

Slow market conditions mean:
- All ROC momentum scores below 2.5 threshold
- No BUY/SELL signals being generated
- Sighook not sending webhooks

#### Next Steps

**Option 1: Wait for Natural Webhook** ⏳
- Continue monitoring sighook for signals
- Market volatility may increase and trigger signals
- Could take hours/days

**Option 2: Send Test Webhook** 🧪
- Manually trigger webhook to test debug logging
- Verify `_cache_strategy_metadata()` execution
- Faster diagnostic turnaround

**Option 3: Lower Momentum Threshold Temporarily** 📉
- Change `ROC_MOMENTUM_THRESHOLD` from 2.5 to 2.0
- Increase chance of signals in slow market
- Requires sighook restart

**Recommendation**: Proceed with Sessions 2-3 (risk management + strategy optimization) which include lowering the momentum threshold. This will naturally generate webhooks to test the metadata caching debug logging.

---

## Session 1 Summary

### Completed ✅

1. **RECALL-USD Disaster Investigation**
   - Root cause: Cost basis calculation bug (not stop-loss failure)
   - $253 of $258 loss was phantom loss from inflated cost_basis values
   - Actual trading loss was only ~$4-5 based on tiny price movements
   - **Action required**: Fix trade_recorder FIFO/cost basis logic

2. **Metadata Caching Investigation**
   - Determined debug logging hasn't been tested yet (no webhooks since container restart)
   - Cannot diagnose until new webhook received
   - Debug logging is deployed and ready to test

### Pending ⏳

3. **Cost Basis Bug Fix**
   - Need to review `trade_recorder` FIFO calculation logic
   - Add validation: `cost_basis ≈ size × avg_entry_price`
   - May need to recalculate historical P&L
   - **Deferred to separate session** - requires code review and testing

4. **Metadata Caching Bug Fix**
   - **Blocked**: Waiting for webhook to test debug logging
   - Will be unblocked by Session 3 (lowering momentum threshold)

### CONFIRMATION: Coinbase Data Analysis ✅

User provided actual Coinbase transaction CSV showing **REAL** P&L:

| Trade | Database P&L | Coinbase P&L | Discrepancy | DB Cost Basis | Actual Cost |
|-------|--------------|--------------|-------------|---------------|-------------|
| Pair 1 | -$56.57 | -$0.44 | -$56.13 | $87.23 | $31.07 |
| Pair 2 | -$63.66 | -$0.05 | -$63.61 | $94.80 | $31.16 |
| Pair 3 | -$64.66 | +$0.04 | -$64.70 | $95.69 | $30.95 |
| Pair 4 | -$73.35 | -$0.27 | -$73.08 | $106.23 | $33.11 |
| **TOTAL** | **-$258.24** | **-$0.72** | **-$257.52** | | |

**CONFIRMED**: Database cost_basis is inflated by **~3x** (showing $87-106 when actual cost was $31-33).

**Actual RECALL-USD loss on Dec 4: -$0.72** (NOT -$258!)

### 30-Day Full Data Analysis ✅✅✅

User provided complete 30-day Coinbase transaction CSV (1,183 trades):

**Database shows**: -$366.56 total loss
**Coinbase actual**: **-$57.79 total loss**
**Discrepancy**: **$308.77 in phantom losses!**

Breakdown:
- Total Buy Cost: $14,531.23
- Total Sell Proceeds: $14,589.02 (received)
- Net P&L: **-$57.79**

This means **84% of your reported losses are phantom accounting errors!**

### Key Insights

1. **🚨 CRITICAL: 84% of losses were phantom**: The -$366 30-day loss is actually **-$58** real trading loss
2. **Stop-losses are working perfectly**: No evidence of stop-loss failures
3. **🔴 Cost basis system is CRITICALLY BROKEN**: Major accounting bug creating $308+ in phantom losses across all symbols
4. **Your trading strategy is nearly break-even**: -$58 on $14,500+ volume is only -0.4% total loss over 30 days
5. **The 24.95% win rate is REAL, but average losses are inflated by bad cost_basis**: Actual performance is MUCH better than database shows

### Next Steps

**Proceed to Session 2: Risk Management**
- Implement quick wins that don't require bug fixes
- Reduce position sizes and tighten stops
- Block unprofitable symbols

**Then Session 3: Strategy Optimization**
- Lower momentum threshold → generates webhooks → tests metadata caching
- Adjust profit targets for slow market

**Session Status**: ✅ COMPLETE (with follow-up actions identified)
**Time Spent**: ~1.5 hours
**Files Modified**: None (investigation only)
**Documentation Created**: This session file

