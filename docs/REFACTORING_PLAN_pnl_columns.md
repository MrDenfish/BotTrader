# Refactoring Plan: Remove pnl_usd and realized_profit Dependencies

**Date:** 2025-12-05
**Branch:** `bugfix/single-fifo-engine` (extending)
**Status:** IN PROGRESS

---

## Executive Summary

After auditing the entire codebase, we've identified all usages of `pnl_usd` and `realized_profit` columns. Since the FIFO engine is now the sole source of truth, we need to:

1. **Stop populating** these columns in `trade_recorder.py`
2. **Update reporting** to use `fifo_allocations` table
3. **Backfill** `realized_profit` from FIFO for historical accuracy
4. **Leave deprecated columns** in database schema (for backwards compatibility)

---

## Safety Check Results

✅ **Position Monitor:** Does NOT use `realized_profit` or `pnl_usd`
✅ **Live Trading Safe:** No trading decisions depend on these columns
✅ **Impact:** Reports and analytics only

---

## Usage Categories

### Category 1: MUST FIX - Trade Recording (Source of Corruption)

**File: SharedDataManager/trade_recorder.py**

| Line | Current Behavior | Required Fix |
|------|------------------|--------------|
| 344 | `realized_profit = pnl_usd` | Change to `realized_profit = None` |
| 266 | Sets `pnl_usd = None` | ✅ Already NULL (our previous fix) |
| 330 | Inserts `pnl_usd` into database | Keep as-is (inserts NULL) |
| 385 | Excludes from updates | ✅ Correct |
| 916-917 | Backfill method (deprecated) | Keep as-is (already deprecated) |

**Fix:**
```python
# Line 343-344: Change from:
# SELL realized_profit equals pnl_usd; BUY has None
"realized_profit": float(pnl_usd) if side == "sell" and pnl_usd is not None else None,

# To:
# SELL realized_profit will be NULL; use FIFO engine for P&L
"realized_profit": None,  # Deprecated - use fifo_allocations table
```

---

### Category 2: MUST UPDATE - Reporting (Primary Users)

#### botreport/aws_daily_report.py

**Direct SQL Queries:**
- Line 559-562: `COALESCE(SUM(realized_profit), 0)`
- Line 612: `COALESCE(SUM(realized_profit) FILTER (WHERE side = 'sell'), 0)`
- Line 476, 484: Uses `pnl_usd` from FIFO query ✅ (already correct)
- Line 751: Falls back to `pnl_usd` if `realized_profit` unavailable
- Line 1080, 1116, 1159, 1875: Column picking logic

**Strategy:**
Replace all `realized_profit` SQL queries with FIFO allocation joins.

#### botreport/metrics_compute.py

- Line 37: `COL_PNL = "realized_profit"` → Change to use FIFO
- Line 622: `COALESCE(realized_profit, pnl_usd)` → Use FIFO
- Line 707, 748: Direct `realized_profit` usage → Use FIFO

#### botreport/analysis_symbol_performance.py

- Line 26: `COL_PNL = "realized_profit"` → Change to use FIFO
- Line 95-111: Query uses `COALESCE(realized_profit, pnl_usd)` → Use FIFO

---

### Category 3: KEEP AS-IS - Correct FIFO Usage

These files correctly query FIFO allocations and should NOT be changed:

**fifo_engine/engine.py**
- Lines 451-471: Computes `pnl_usd` for FIFO allocations ✅
- Line 703: Sums FIFO `pnl_usd` ✅

**fifo_engine/models.py**
- Line 40: FIFO allocation `pnl_usd` field ✅

**scripts/allocation_reports.py**
- Lines 120-121: Queries FIFO `pnl_usd` ✅

---

### Category 4: OK - Comments/Documentation

**webhook/listener.py**
- Lines 1284, 1293, 1464: Comments explaining NOT to touch these fields ✅

---

### Category 5: UNRELATED - Unrealized Profit

These use `unrealized_profit` (different concept - live position P&L):

- sighook/holdings_process_manager.py: Lines 145-188 ✅
- sighook/profit_manager.py: Line 125 ✅
- ProfitDataManager/profit_data_manager.py: Lines 364-367 ✅

**Action:** None needed - these are correct.

---

### Category 6: TEST/DEBUG FILES

**verify_email_report.py**
- Lines 98-167: Uses `realized_profit` for validation
- **Action:** Update to use FIFO for validation

**TestDebugMaintenance/trade_record_maintenance.py**
- Lines 255, 286, 349, 353, 364: Maintenance scripts
- **Action:** Update to handle NULL values correctly

**diagnostic_performance_analysis.py**
- Lines 109-340: Uses `pnl_usd` for diagnostics
- **Action:** Keep as-is (diagnostic tool can read old data)

---

### Category 7: PASSIVE/ACCUMULATION MANAGERS

**MarketDataManager/passive_order_manager.py**
- Lines 972-980: Reads `pnl_usd` for passive trade stats
- **Action:** Update to query FIFO allocations

**AccumulationManager/accumulation_manager.py**
- Lines 102-144: Uses `pnl_usd` for profit-based allocation
- **Action:** Update to query FIFO allocations

**SharedDataManager/leader_board.py**
- Lines 45-78: Queries `pnl_usd` for leaderboard
- **Action:** Update to query FIFO allocations

---

### Category 8: SCHEMA - Keep for Compatibility

**TableModels/trade_record.py**
- Line 20: `pnl_usd = Column(Float, nullable=True)`
- Line 30: `realized_profit = Column(Float, nullable=True)`

**Action:** KEEP columns in schema (don't drop). Reasons:
1. Historical data preservation
2. Backwards compatibility
3. Future cleanup in separate migration

---

## Implementation Plan

### Phase 1: Stop Populating (This Session)

1. **Update trade_recorder.py line 344:**
   ```python
   "realized_profit": None,  # Deprecated - use fifo_allocations
   ```

2. **Verify pnl_usd is already NULL** (done in previous fix)

3. **Test:** Verify new trades have NULL in both columns

---

### Phase 2: Update Reports (This Session)

**Strategy:** Create helper function to query FIFO P&L, update all report queries.

**New Helper Function:** `botreport/fifo_helpers.py`
```python
def get_fifo_pnl_query(time_filter=""):
    """
    Generate SQL to get P&L from FIFO allocations.

    Returns query that can be used in place of:
    COALESCE(realized_profit, pnl_usd)
    """
    return f"""
    (
        SELECT COALESCE(SUM(fa.pnl_usd), 0)
        FROM fifo_allocations fa
        WHERE fa.sell_order_id = trade_records.order_id
          AND fa.allocation_version = 2
          {time_filter}
    )
    """
```

**Files to Update:**

1. **botreport/aws_daily_report.py:**
   - Line 559: Replace with FIFO join
   - Line 612: Replace with FIFO join

2. **botreport/metrics_compute.py:**
   - Line 622: Use FIFO helper
   - Line 707, 748: Use FIFO helper

3. **botreport/analysis_symbol_performance.py:**
   - Lines 95-111: Rewrite query to join fifo_allocations

---

### Phase 3: Backfill realized_profit (This Session)

**Script:** `scripts/backfill_realized_profit_from_fifo.py`

```python
#!/usr/bin/env python3
"""
Backfill realized_profit column from FIFO allocations.

This script populates trade_records.realized_profit with correct values
from fifo_allocations table for historical accuracy.
"""

UPDATE trade_records tr
SET realized_profit = (
    SELECT COALESCE(SUM(fa.pnl_usd), 0)
    FROM fifo_allocations fa
    WHERE fa.sell_order_id = tr.order_id
      AND fa.allocation_version = 2
)
WHERE tr.side = 'sell'
  AND (tr.realized_profit IS NULL
       OR ABS(tr.realized_profit - COALESCE((
           SELECT SUM(fa.pnl_usd)
           FROM fifo_allocations fa
           WHERE fa.sell_order_id = tr.order_id
             AND fa.allocation_version = 2
       ), 0)) > 0.01);
```

---

### Phase 4: Update Other Components (This Session)

1. **passive_order_manager.py** (lines 972-980):
   - Update to query FIFO instead of `pnl_usd`

2. **accumulation_manager.py** (lines 102-144):
   - Update profit allocation logic to use FIFO

3. **leader_board.py** (lines 45-78):
   - Update leaderboard query to use FIFO

4. **verify_email_report.py**:
   - Update validation queries to use FIFO

---

## Testing Plan

### Test 1: Verify New Trades Have NULL

```sql
-- After trade_recorder.py fix
SELECT order_id, symbol, side, realized_profit, pnl_usd
FROM trade_records
WHERE order_time >= NOW() - INTERVAL '1 hour'
  AND side = 'sell';

-- Expected: Both columns NULL
```

### Test 2: Verify Report Accuracy

```bash
# Generate report with FIFO queries
python -m botreport.aws_daily_report

# Compare with known values from database
```

### Test 3: Verify Backfill

```sql
-- After backfill script
SELECT
    symbol,
    COUNT(*) as total_sells,
    COUNT(*) FILTER (WHERE realized_profit IS NOT NULL) as backfilled,
    COUNT(*) FILTER (WHERE ABS(realized_profit - (
        SELECT COALESCE(SUM(pnl_usd), 0)
        FROM fifo_allocations
        WHERE sell_order_id = trade_records.order_id
          AND allocation_version = 2
    )) < 0.01) as matching
FROM trade_records
WHERE side = 'sell'
  AND order_time >= NOW() - INTERVAL '90 days'
GROUP BY symbol;

-- Expected: backfilled = matching = total_sells
```

---

## Deployment Checklist

- [ ] Update trade_recorder.py (line 344)
- [ ] Create fifo_helpers.py with query generator
- [ ] Update aws_daily_report.py
- [ ] Update metrics_compute.py
- [ ] Update analysis_symbol_performance.py
- [ ] Update passive_order_manager.py
- [ ] Update accumulation_manager.py
- [ ] Update leader_board.py
- [ ] Create backfill script
- [ ] Test locally with recent trades
- [ ] Commit all changes
- [ ] Push to GitHub
- [ ] Pull on server
- [ ] Run backfill script on server
- [ ] Rebuild Docker containers
- [ ] Verify report accuracy
- [ ] Monitor for 24 hours
- [ ] Merge to main

---

## Success Criteria

✅ New trades have NULL in realized_profit and pnl_usd
✅ Reports show accurate P&L from FIFO allocations
✅ AVAX-USD shows ~-$0.27 (not -$276.53)
✅ All historical data backfilled correctly
✅ No errors in logs
✅ All tests pass

---

## Rollback Plan

If issues arise:

1. **Revert trade_recorder.py change** (restore line 344)
2. **Revert report changes** (restore old queries)
3. **Rebuild containers**
4. **Reports will show old (wrong) values** but system will function

---

*Document created: 2025-12-05*
*Status: READY TO IMPLEMENT*
