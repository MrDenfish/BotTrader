# Optimization Readiness Progress Report
**Report Date**: December 28, 2025
**Evaluation Window**: December 10 - December 28 (18 days into 28-day plan)
**Original Plan**: 4-week data collection ending January 7, 2026
**Session Reference**: December 10, 2025 - Optimization Preparation

---

## Executive Summary

**Status**: ⚠️ **NOT READY** - Critical infrastructure gaps identified
**Data Collected**: ✅ 4,830 trades (exceeds 500 minimum)
**Infrastructure Status**: ⚠️ Partially complete (3/7 components working)
**Recommendation**: **Complete missing infrastructure before proceeding to optimization**

---

## Readiness Checklist - Detailed Results

### ✅ 1. Data Volume: PASS
- **Target**: 500+ trades in fifo_allocations
- **Actual**: **4,830 trades** (August 30 - December 27, 2025)
- **Last 28 days**: 744 trades
- **Unique symbols**: 184
- **Status**: ✅ **EXCELLENT** - Well above minimum threshold

### ⚠️ 2. Win Rate Analysis: CONCERNING
**All-Time Performance** (4,830 trades):
- Win Rate: **30.97%**
- Total PnL: **-$1,328.87**
- Status: ⚠️ **NEEDS IMPROVEMENT** - Below breakeven

**Last 28 Days** (744 trades):
- Win Rate: **24.87%** ⬇️ (declining trend)
- Total PnL: **-$99.82**
- Avg Daily PnL: **-$3.56**
- Status: 🚨 **DETERIORATING** - Win rate dropped ~6%

**Daily Breakdown (Last 28 Days)**:
| Date | Trades | Wins | Win Rate | Daily PnL |
|------|--------|------|----------|-----------|
| Dec 27 | 8 | 2 | 25.00% | -$1.33 |
| Dec 26 | 10 | 3 | 30.00% | -$2.21 |
| Dec 25 | 30 | 5 | 16.67% | -$15.33 |
| Dec 24 | 28 | 16 | 57.14% | **+$4.70** ⭐ |
| Dec 23 | 47 | 23 | 48.94% | **+$2.25** ⭐ |
| Dec 22 | 14 | 6 | 42.86% | -$3.11 |
| Dec 12 | 28 | 2 | 7.14% | -$20.46 🚨 |
| Dec 08 | 53 | 5 | 9.43% | +$0.64 |
| Dec 04 | 96 | 18 | 18.75% | -$6.49 |
| Dec 02 | 118 | 30 | 25.42% | -$18.47 🚨 |

**Analysis**:
- ✅ Two good days (Dec 23-24) with 48-57% win rate
- 🚨 Several very poor days (<10% win rate)
- ⚠️ High variance - strategy needs stabilization before optimization

### ❌ 3. Consistently Losing Symbols: IDENTIFIED BUT NOT ACTING

**Top 20 Worst Performers (Last 28 Days, ≥5 trades)**:
| Symbol | Trades | Win Rate | Total Loss |
|--------|--------|----------|------------|
| AVT-USD | 43 | 32.56% | -$10.16 |
| FARM-USD | 11 | 0.00% | -$8.40 🚨 |
| UNI-USD | 18 | 22.22% | -$5.93 |
| PRIME-USD | 33 | 27.27% | -$5.23 |
| XLM-USD | 5 | 0.00% | -$5.00 🚨 |
| AVAX-USD | 32 | 12.50% | -$4.25 |
| PENGU-USD | 44 | 15.91% | -$4.22 |
| A8-USD | 27 | 22.22% | -$3.55 |
| TAO-USD | 32 | 25.00% | -$3.20 |
| OMNI-USD | 6 | 0.00% | -$3.69 🚨 |

**Current Blacklist**: Only 2 symbols (A8-USD, PENGU-USD) in latest snapshot
**Issue**: ❌ **Still trading PENGU-USD** despite blacklist (-$4.22 loss in 28 days)
**Action Required**:
1. Verify blacklist is being enforced
2. Add FARM-USD, XLM-USD, OMNI-USD, AVT-USD immediately (all have 0% or very low win rates)

### ❌ 4. Strategy Stability: FAILED - CRITICAL ISSUE

**Strategy Snapshots**:
- Total snapshots in database: **2**
- Latest snapshot: `487f9a95-42a9-48b2-b13c-40acdecc5f12` (Dec 27, 2025)
- Previous snapshot: `92a2e91b-3a58-42cc-b2cc-50a3356e865d` (Dec 10, 2025 - from optimization prep session)

**Latest Snapshot Details**:
- Score Buy/Sell Target: 2.5 / 2.5
- RSI Thresholds: 25.0 / 75.0
- RSI Weight: 1.5 (reduced from 2.5)
- MACD Weight: 1.8
- TP/SL: 3.5% / 4.5%
- Min Indicators Required: 2
- Excluded Symbols: A8-USD, PENGU-USD
- Config Hash: `1f6a7d7c117aa5b1732832b2821c81e65d79acd5c554400bfcbdb9b3d3e77035`

**Status**: ⚠️ Strategy parameters exist but...

### 🚨 5. Trade Strategy Linkage: COMPLETELY BROKEN

**Critical Finding**:
```
Total Trades (last 28 days): 902
Linked to Strategy: 0
Link Rate: 0.0%
```

**Impact**: 🚨 **BLOCKING ISSUE**
- Cannot correlate trades with strategy parameters
- Cannot analyze which parameter changes improve/hurt performance
- **Optimization is IMPOSSIBLE without this data**

**Root Cause**: `trade_strategy_link` table is not being populated when trades execute.

**Required Fix**: Ensure `TradingStrategy` class creates link records when:
1. Buy order is placed (capture buy_score, indicator_breakdown)
2. Sell order is placed (capture sell_score, trigger_type)

### ❌ 6. Strategy Performance Summary: EMPTY

**Status**:
- Table exists: ✅ Yes
- Records in table: ❌ **0**
- Last populated: Never

**Impact**: No daily aggregated performance metrics available.

**Expected Data**: Should have ~18 rows (one per day since Dec 10)

**Required**: Build/run daily aggregation job to populate this table from fifo_allocations.

### ⚠️ 7. Weekly Analysis Infrastructure: PARTIALLY WORKING

**Weekly Query Files**: ✅ Created and deployed to AWS
- `weekly_symbol_performance.sql` ✅
- `weekly_signal_quality.sql` ✅
- `weekly_timing_analysis.sql` ✅

**Automated Script**: ⚠️ **Relocated but not tested**
- Script moved to: `scripts/analytics/weekly_strategy_review.sh`
- Deployed to AWS: ❌ Unknown (needs verification)
- Cron job: ❌ Unknown (likely not configured)

**Manual Query Results Available**: ✅ Yes (queries work when run manually)

---

## Infrastructure Assessment

### ✅ What's Working:
1. **Data Collection**: FIFO allocations capturing all trades with P&L
2. **Strategy Snapshots**: Table exists, baseline created
3. **Market Conditions**: Table exists (2 entries)
4. **Analysis Queries**: SQL queries created and functional
5. **Symbol Performance Data**: Can identify losing symbols

### 🚨 What's Broken (BLOCKING):
1. **Trade-Strategy Linkage**: 0% of trades linked to strategy parameters
2. **Performance Summary**: No daily aggregations
3. **Weekly Automation**: Script not deployed/tested

### ⚠️ What's Incomplete:
1. **Blacklist Enforcement**: Not preventing trades on excluded symbols
2. **Market Conditions**: Only 2 entries (should have ~18)
3. **Daily Reports**: Not generating or not accessible

---

## Can We Proceed to Optimization?

### Answer: **NO - Not Ready**

**Reasons**:
1. 🚨 **Critical Blocker**: Trade-strategy linkage is completely broken (0% link rate)
   - **Cannot** analyze which parameters lead to wins/losses
   - **Cannot** compare strategy variations
   - **Cannot** build optimizer without this correlation

2. ⚠️ **Strategy Unstable**: Win rate declining (30.97% → 24.87%)
   - Need to stabilize performance before optimization
   - Current parameters are losing money

3. ❌ **Missing Infrastructure**:
   - No daily performance summaries
   - No weekly automation
   - Blacklist not enforced

---

## Recommended Action Plan

### Phase 1: Fix Critical Blockers (Week 1 - Do Now)

#### Priority 1: Fix Trade-Strategy Linkage
**Location**: `sighook/trading_strategy.py:205`

**Required Changes**:
```python
# When placing buy order (after order confirmed):
await self.shared_data_manager.trade_recorder.create_strategy_link(
    order_id=buy_order['order_id'],
    snapshot_id=self.current_snapshot_id,
    buy_score=buy_score,
    indicator_breakdown=indicator_contributions,
    indicators_fired=len([i for i in indicator_contributions if i > 0])
)

# When closing position:
await self.shared_data_manager.trade_recorder.update_strategy_link(
    order_id=sell_order['order_id'],
    sell_score=sell_score,
    trigger_type='stop_loss' | 'take_profit' | 'signal_flip'
)
```

**Verification**:
```sql
-- Should show 100% after fix
SELECT
    COUNT(DISTINCT tr.order_id) as total_trades,
    COUNT(DISTINCT tsl.order_id) as linked_trades,
    ROUND((COUNT(DISTINCT tsl.order_id)::decimal / COUNT(DISTINCT tr.order_id) * 100), 1) as link_rate_pct
FROM trade_records tr
LEFT JOIN trade_strategy_link tsl ON tsl.order_id = tr.order_id
WHERE tr.order_time >= NOW() - INTERVAL '7 days';
```

#### Priority 2: Fix Blacklist Enforcement
**Issue**: PENGU-USD still being traded despite being in excluded_symbols

**Check**: `sighook/trading_strategy.py:58`
```python
# Verify this check exists:
if symbol in self.excluded_symbols:
    self.logger.info(f"⛔ {symbol} is blacklisted, skipping")
    return
```

**Test**: Deploy and verify no PENGU-USD, A8-USD trades occur

#### Priority 3: Expand Blacklist
Add these symbols immediately (0% or <15% win rate):
```python
EXCLUDED_SYMBOLS = [
    'A8-USD', 'PENGU-USD',  # Existing
    'FARM-USD',  # 0% win rate, -$8.40
    'XLM-USD',   # 0% win rate, -$5.00
    'OMNI-USD',  # 0% win rate, -$3.69
    'AVT-USD',   # 32% win rate but -$10.16 loss
    'AVAX-USD',  # 12.5% win rate, -$4.25
]
```

**Expected Savings**: ~$30-40/month

### Phase 2: Complete Infrastructure (Week 2)

#### Task 1: Build Daily Performance Aggregation Job
**Purpose**: Populate `strategy_performance_summary` table

**Create**: `jobs/daily_performance_summary.py`
```python
# Run daily at 00:10 UTC (after daily accumulation)
# Aggregate previous day's performance from fifo_allocations
# Insert into strategy_performance_summary
```

**Cron**: `10 0 * * * /opt/bot/run_daily_summary.sh`

#### Task 2: Deploy Weekly Review Script
**File**: `scripts/analytics/weekly_strategy_review.sh`

**Steps**:
```bash
# Verify script exists locally
test -f scripts/analytics/weekly_strategy_review.sh && echo "EXISTS"

# Copy to server if missing
scp scripts/analytics/weekly_strategy_review.sh bottrader-aws:/opt/bot/scripts/analytics/
ssh bottrader-aws "chmod +x /opt/bot/scripts/analytics/weekly_strategy_review.sh"

# Set up cron (Monday 9am PT = 17:00 UTC)
ssh bottrader-aws "crontab -l | { cat; echo '0 17 * * 1 /opt/bot/scripts/analytics/weekly_strategy_review.sh'; } | crontab -"
```

#### Task 3: Populate Market Conditions
**Missing**: 16 days of market condition data

**Script to Create**: `scripts/backfill_market_conditions.py`
- Fetch BTC historical data for Dec 10-28
- Calculate volatility (7-day ATR)
- Classify trend (7-day SMA direction)
- Insert into market_conditions table

### Phase 3: Stabilize Strategy (Weeks 3-4)

**Goal**: Get win rate above 35% before optimization

**Actions**:
1. **Increase Score Threshold**: Test raising SCORE_BUY_TARGET from 2.5 → 3.0
2. **Analyze Signal Quality**: Once linkage is fixed, see which signal strengths perform best
3. **Test Tighter Stop-Loss**: Current 4.5% may be too wide in current market
4. **Review Cooldown**: 7 bars may not be enough to avoid whipsaws

**Create New Snapshot** after each parameter change to track experiments.

---

## Optimization Timeline - Revised

| Phase | Date Range | Status | Tasks |
|-------|-----------|--------|-------|
| **Setup** | Dec 10-28 | ⚠️ 50% | Fix linkage, enforce blacklist, deploy automation |
| **Stabilization** | Dec 29 - Jan 12 | Pending | Get win rate >35%, test parameter variations |
| **Data Collection** | Jan 13 - Jan 26 | Pending | Collect 2 weeks of stable performance |
| **Evaluation** | Jan 27 - Feb 2 | Pending | Review 4 weeks of linked trade data |
| **Build Optimizer** | Feb 3 - Feb 16 | Pending | If win rate >35% and data quality good |
| **Backtesting** | Feb 17+ | Pending | Test optimized parameters |

**Original Target**: January 7, 2026
**Revised Target**: **February 17, 2026** (6-week delay due to infrastructure gaps)

---

## Questions from Original Plan - Answered

### 1. Is win rate stable/improving?
**Answer**: ❌ **NO** - Declining from 30.97% (all-time) to 24.87% (last 28 days)

### 2. Do we have 500+ trades in fifo_allocations?
**Answer**: ✅ **YES** - 4,830 trades total, 744 in last 28 days

### 3. Any consistently losing symbols to blacklist?
**Answer**: ✅ **YES** - Identified 6 symbols with 0-15% win rates
**Status**: ⚠️ Blacklist exists but not enforced (still trading PENGU-USD)

### 4. Ready to automate parameter testing?
**Answer**: ❌ **NO** - Trade-strategy linkage is broken (0% link rate)

---

## Immediate Next Steps (This Week)

1. ✅ **Fix trade_strategy_link** - Modify TradingStrategy to populate links
2. ✅ **Enforce blacklist** - Verify excluded symbols are actually excluded
3. ✅ **Add worst performers** - Expand blacklist with 4 more symbols
4. ✅ **Deploy weekly script** - Verify weekly_strategy_review.sh on server
5. ✅ **Test linkage** - Place test trade and verify link is created
6. ⚠️ **Create daily aggregation job** - Build performance summary populator

**Expected Impact**:
- Stop bleeding $30-40/month on bad symbols
- Start collecting parameter correlation data
- Establish baseline for future optimization

---

## Conclusion

**Data Volume**: ✅ Excellent (4,830 trades)
**Infrastructure**: 🚨 Critical gaps (0% strategy linkage)
**Performance**: ⚠️ Concerning (declining win rate)
**Ready for Optimization**: ❌ **NO**

**Recommendation**: **Delay optimization 6 weeks**. Focus on:
1. Fix infrastructure (trade-strategy linkage is critical)
2. Stabilize strategy (get win rate above 35%)
3. Enforce blacklist (stop losing trades)
4. Collect high-quality linked data for 2-4 weeks

**Only then** will optimization be data-driven and effective.

---

**Report Generated**: December 28, 2025
**Next Review**: January 11, 2026 (after infrastructure fixes deployed)
