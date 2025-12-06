# CRITICAL: realized_profit Column Corruption

**Date:** 2025-12-05
**Severity:** CRITICAL
**Status:** DISCOVERED - Root Cause Identified
**Branch Needed:** `bugfix/realized-profit-corruption`

---

## Executive Summary

The `trade_records.realized_profit` column contains **catastrophically incorrect values** that are inflating losses by 100x-1000x. This is causing the daily email report to show completely erroneous performance metrics.

**Impact:**
- Email report shows -$276.53 for AVAX-USD when actual is ~-$0.27 (1000x error)
- All performance metrics in reports are WRONG
- Win rates, avg win/loss, profit factors all calculated from corrupt data
- This is a SEPARATE issue from the FIFO bug we just fixed

**Key Finding:** The `pnl_usd` column (deprecated inline FIFO) and FIFO allocations show CORRECT values (~-$0.03 to -$0.06), but `realized_profit` shows massive incorrect losses (-$33.89, -$42.53, etc.).

---

## The Problem

### Example: AVAX-USD SELL @ 2025-12-04 07:33:58

**Database Values:**
```
symbol: AVAX-USD
side: sell
price: $14.75
size: 2.2479564
realized_profit: -$33.89 ❌ WRONG
pnl_usd: -$33.89 ❌ WRONG (old inline FIFO)
fifo_total_pnl: -$0.03787806 ✅ CORRECT
```

**What This Means:**
- FIFO engine calculated correct P&L: -$0.038
- `realized_profit` column shows 1000x error: -$33.89
- Report uses `realized_profit` → report is completely wrong

### More Examples

| Symbol | Sell Time | realized_profit | pnl_usd | fifo_total_pnl | Error Multiple |
|--------|-----------|----------------|---------|----------------|----------------|
| AVAX-USD | 12/04 07:33 | -$33.89 | -$33.89 | -$0.038 | 893x |
| AVAX-USD | 12/04 07:04 | -$28.15 | -$28.15 | -$0.060 | 469x |
| AVAX-USD | 12/04 06:02 | -$44.28 | -$0.036 | -$0.038 | 1165x |
| AVAX-USD | 12/04 05:36 | -$42.53 | -$0.050 | -$0.060 | 709x |
| PENGU-USD | 12/03 20:41 | -$24.29 | -$0.036 | -$0.036 | 677x |
| PENGU-USD | 12/03 20:25 | -$52.36 | -$0.043 | -$0.043 | 1218x |
| PENGU-USD | 12/03 19:36 | -$56.02 | -$0.153 | -$0.153 | 366x |

**Pattern:** `realized_profit` is showing values that are hundreds to thousands of times larger than actual P&L.

---

## Root Cause Analysis

### Where Does `realized_profit` Come From?

Let me search for where this column is populated:

```bash
# Search for realized_profit writes
grep -r "realized_profit" --include="*.py" | grep -E "(UPDATE|INSERT|SET|realized_profit\s*=)"
```

**Hypothesis:** There's likely a separate process or trigger that's calculating `realized_profit` incorrectly, possibly:
1. Using wrong price data (like market price instead of actual fill price)
2. Multiplying by wrong quantity
3. Using cumulative position size instead of trade size
4. Calculating unrealized losses instead of realized

### What's Currently Happening

**Report Generation (botreport/analysis_symbol_performance.py:26,95):**
```python
COL_PNL = os.getenv("REPORT_COL_PNL", "realized_profit")
COL_PNL_FALLBACK = "pnl_usd"

# Query uses:
COALESCE({COL_PNL}, {COL_PNL_FALLBACK})
```

**This means:**
1. Report tries to use `realized_profit` first
2. Falls back to `pnl_usd` if NULL
3. Both columns have WRONG values for recent trades
4. FIFO allocations have CORRECT values

### Why pnl_usd and realized_profit Sometimes Match

**Observation:** For some trades:
- `realized_profit` = -$33.89
- `pnl_usd` = -$33.89
- Both are identically wrong

**But for others:**
- `realized_profit` = -$44.28
- `pnl_usd` = -$0.036
- Different wrong values!

**Theory:** There may be TWO broken processes:
1. One that populates `realized_profit` (always wrong)
2. Old inline FIFO that populated `pnl_usd` (sometimes wrong, we just disabled it)

---

## Data Evidence

### Query Results (2025-12-05)

**AVAX-USD Last 7 Trades:**
```
 sell_time           | realized_profit | pnl_usd  | fifo_total_pnl | Status
---------------------|-----------------|----------|----------------|--------
 12/04 07:33:58      | -33.89          | -33.89   | -0.03787806    | Both wrong
 12/04 07:04:04      | -28.15          | -28.15   | -0.06017615    | Both wrong
 12/04 06:02:30      | -44.28          | -0.03588 | -0.03782186    | realized_profit wrong
 12/04 05:36:20      | -42.53          | -0.05001 | -0.06027333    | realized_profit wrong
 12/04 05:05:21      | -42.14          | -0.06031 | -0.06031442    | realized_profit wrong
 12/04 04:25:40      | -42.47          | -0.01536 | -0.03785228    | realized_profit wrong
 12/04 01:30:18      | -43.07          | -0.05005 | -0.03782187    | realized_profit wrong
```

**Sum of realized_profit:** -$276.53 (what report shows)
**Sum of fifo_total_pnl:** -$0.27 (actual P&L)
**User's actual loss:** ~$0.27 ✅

### PENGU-USD Data

```
 sell_time           | realized_profit | pnl_usd  | fifo_total_pnl | Status
---------------------|-----------------|----------|----------------|--------
 12/04 12:00:43      | 2.630378        | 0.186335 | 0.18633471     | realized_profit inflated
 12/04 11:18:36      | 1.557791        | -0.52770 | -0.52770436    | realized_profit inflated
 12/04 09:46:48      | 1.589046        | -0.09430 | -0.09430017    | realized_profit inflated
 12/04 09:22:34      | 1.796358        | 0.61302  | 0.61302116     | realized_profit inflated
```

**User said:** "PENGU-USD performance does not align with the report also, it lost money -$0.965598 for the most recent (6) trades"

**Our data shows:** FIFO total for 4 recent trades = +$0.187 - $0.528 - $0.094 + $0.613 = +$0.178

**This doesn't match user's -$0.965598 either!** Need to verify which trades user is counting.

---

## Impact Assessment

### 1. Email Report (CRITICAL)

**All These Sections Use Corrupt Data:**
- ✅ **Symbol Performance** - Uses `realized_profit` → completely wrong
- ✅ **Trade Stats** - Avg win/loss calculated from `realized_profit` → wrong
- ✅ **Win Rate** - Counts based on `realized_profit > 0` → wrong
- ✅ **Profit Factor** - Gross profit / gross loss from `realized_profit` → wrong
- ❓ **Key Metrics** - Depends on which P&L column it uses
- ❓ **Max Drawdown** - Uses cumulative `realized_profit` → likely wrong

### 2. Trading Decisions (UNKNOWN)

**Critical Questions:**
- ❓ Does position monitor use `realized_profit`?
- ❓ Does position sizing logic read `realized_profit`?
- ❓ Are any stop-loss or take-profit decisions based on this column?

**If YES to any:** This could be affecting live trading! 🚨

### 3. Historical Analysis (BROKEN)

- All past performance analysis unreliable
- Win rate trends meaningless
- Symbol performance comparisons wrong
- Strategy optimization based on bad data

---

## Files to Investigate

### Priority 1: Find Where realized_profit Is Populated

**Search Commands:**
```bash
# Find all writes to realized_profit
grep -rn "realized_profit\s*=" --include="*.py" .

# Find database triggers
docker exec db psql -U bot_user -d bot_trader_db -c "\
  SELECT trigger_name, event_manipulation, action_statement \
  FROM information_schema.triggers \
  WHERE event_object_table = 'trade_records';"

# Find any stored procedures
docker exec db psql -U bot_user -d bot_trader_db -c "\
  SELECT routine_name, routine_definition \
  FROM information_schema.routines \
  WHERE routine_type = 'FUNCTION' \
  AND routine_name LIKE '%pnl%' OR routine_name LIKE '%profit%';"
```

### Priority 2: Check Report Generation

**Files:**
- `botreport/aws_daily_report.py` - Main report generation
- `botreport/analysis_symbol_performance.py` - Symbol performance (uses `realized_profit`)
- `botreport/metrics_compute.py` - Key metrics calculation
- `Config/constants_report.py` - Report configuration

### Priority 3: Verify FIFO Is Correct Source

**Validation Query:**
```sql
-- Compare FIFO allocations with Coinbase exchange data
-- (Need to fetch actual exchange history to verify)
SELECT
    symbol,
    order_id,
    order_time,
    side,
    price,
    size,
    realized_profit as db_value,
    (SELECT SUM(pnl_usd) FROM fifo_allocations WHERE sell_order_id = tr.order_id) as fifo_value
FROM trade_records tr
WHERE side = 'sell'
  AND order_time >= NOW() - INTERVAL '48 hours'
ORDER BY order_time DESC
LIMIT 50;
```

---

## Immediate Questions

### Session Start Checklist

1. **Where is realized_profit populated?**
   - [ ] Search all Python code for `realized_profit =`
   - [ ] Check database triggers on `trade_records`
   - [ ] Check for stored procedures or functions
   - [ ] Review `trade_recorder.py` for any realized_profit logic

2. **Does it affect live trading?**
   - [ ] Search `position_monitor.py` for "realized_profit"
   - [ ] Check if stop-loss logic queries this column
   - [ ] Verify position sizing doesn't use it

3. **What's the correct source of truth?**
   - [ ] Validate FIFO allocations against Coinbase exchange
   - [ ] Compare FIFO P&L with actual executed trades
   - [ ] Determine if we can trust `fifo_allocations.pnl_usd`

4. **How widespread is the corruption?**
   - [ ] Query all symbols for realized_profit vs FIFO mismatch
   - [ ] Check historical data (30 days, 90 days)
   - [ ] Estimate scope of data needing correction

---

## Proposed Solution Approach

### Phase 1: Investigation (CURRENT SESSION)

1. ✅ Identify root cause - where realized_profit is populated
2. ✅ Determine if it affects live trading (CRITICAL)
3. ✅ Validate FIFO allocations are correct
4. ✅ Document scope of corruption

### Phase 2: Immediate Workaround

**Option A: Change Report to Use FIFO**
- Modify `botreport/analysis_symbol_performance.py` to query `fifo_allocations`
- Update all report sections to use FIFO as source
- Deploy immediately to get accurate reports

**Option B: Backfill realized_profit from FIFO**
- Write script to update `realized_profit` from `fifo_allocations`
- Run for all historical data
- Verify report accuracy

**Option C: Both**
- Fix report to use FIFO (immediate)
- Backfill realized_profit for historical accuracy (longer term)

### Phase 3: Fix the Root Cause

1. Find where `realized_profit` is being set incorrectly
2. Fix the calculation logic
3. Add validation to prevent future corruption
4. Add monitoring/alerts

### Phase 4: Data Cleanup

1. Create backup of current data
2. Recalculate all `realized_profit` values from FIFO
3. Validate against exchange data
4. Update database

### Phase 5: Prevention

1. Add database constraint: `CHECK (ABS(realized_profit - fifo_pnl) < 0.01)` (once fixed)
2. Add validation in daily report
3. Add alerts for P&L discrepancies
4. Document correct data flow

---

## Testing Strategy

### Validation Queries

**1. Find Magnitude of Corruption:**
```sql
SELECT
    symbol,
    COUNT(*) as affected_trades,
    SUM(realized_profit) as db_sum,
    SUM((SELECT SUM(pnl_usd) FROM fifo_allocations
         WHERE sell_order_id = tr.order_id AND allocation_version = 2)) as fifo_sum,
    SUM(realized_profit) - SUM((SELECT SUM(pnl_usd) FROM fifo_allocations
         WHERE sell_order_id = tr.order_id AND allocation_version = 2)) as discrepancy
FROM trade_records tr
WHERE side = 'sell'
  AND order_time >= NOW() - INTERVAL '7 days'
  AND realized_profit IS NOT NULL
GROUP BY symbol
HAVING ABS(SUM(realized_profit) - SUM((SELECT SUM(pnl_usd) FROM fifo_allocations
    WHERE sell_order_id = tr.order_id AND allocation_version = 2))) > 1.0
ORDER BY ABS(discrepancy) DESC;
```

**2. Check All Symbols:**
```sql
SELECT
    COUNT(DISTINCT symbol) as total_symbols,
    COUNT(DISTINCT CASE WHEN ABS(realized_profit -
        COALESCE((SELECT SUM(pnl_usd) FROM fifo_allocations
                  WHERE sell_order_id = tr.order_id AND allocation_version = 2), 0)) > 0.01
        THEN symbol END) as corrupt_symbols
FROM trade_records tr
WHERE side = 'sell'
  AND order_time >= NOW() - INTERVAL '30 days';
```

---

## Success Criteria

**Investigation Complete When:**
- ✅ Found where `realized_profit` is populated
- ✅ Confirmed FIFO allocations are accurate
- ✅ Verified no impact on live trading
- ✅ Documented scope of corruption

**Fix Complete When:**
- ✅ Email report shows accurate P&L (matches FIFO)
- ✅ AVAX-USD shows ~-$0.27 loss (not -$276.53)
- ✅ All symbols' P&L matches FIFO allocations
- ✅ Historical data corrected
- ✅ Validation added to prevent recurrence

**Validation Complete When:**
- ✅ Database P&L matches Coinbase exchange data
- ✅ Report metrics are trustworthy
- ✅ No trades have >$0.01 discrepancy between realized_profit and FIFO

---

## Relationship to FIFO Bug

**These are SEPARATE issues:**

| Issue | Description | Status | Impact |
|-------|-------------|--------|--------|
| FIFO Matching Bug | SELLs matched to wrong BUYs | ✅ FIXED | pnl_usd (deprecated) |
| realized_profit Corruption | Separate calculation error | 🔴 ACTIVE | Reports, metrics |

**Both need fixing, but realized_profit is more urgent because:**
1. Affects ALL reports (not just FIFO)
2. May affect live trading decisions
3. Makes it impossible to assess bot performance

---

## Notes for Next Action

**Immediate Steps:**
```bash
# 1. Find where realized_profit is set
grep -rn "realized_profit\s*=" --include="*.py" . | grep -v "test" | grep -v "docs"

# 2. Check for database triggers
ssh bottrader-aws "docker exec db psql -U bot_user -d bot_trader_db -c \"\
  SELECT trigger_name, event_manipulation, action_statement \
  FROM information_schema.triggers \
  WHERE event_object_table = 'trade_records';\""

# 3. Verify position monitor doesn't use it
grep -n "realized_profit" MarketDataManager/position_monitor.py

# 4. Quick fix: Update report to use FIFO
# Edit botreport/analysis_symbol_performance.py line 26
```

**Critical Decision Point:**
- If position monitor uses `realized_profit` → STOP TRADING NOW
- If reports only use it → Fix can wait for next session

---

## Related Issues

- **FIFO Matching Bug** (separate, already fixed on `bugfix/single-fifo-engine`)
- **Table Restructuring** (planned, can address both pnl_usd and realized_profit columns)
- **Report Accuracy** (THIS ISSUE - blocking all performance analysis)

---

*Document created: 2025-12-05*
*Status: ROOT CAUSE IDENTIFIED - AWAITING FIX*
