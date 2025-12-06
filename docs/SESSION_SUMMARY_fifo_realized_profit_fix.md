# Session Summary: FIFO & realized_profit Fix

**Date:** 2025-12-05
**Branch:** `bugfix/single-fifo-engine`
**Session Objective:** Fix report accuracy by removing dual FIFO system and refactoring P&L calculations

---

## What We Accomplished ✅

### 1. **Root Cause Identified** ✅
- Discovered that `realized_profit` column = `pnl_usd` (line 344 in trade_recorder.py)
- Both columns populated by broken inline FIFO causing 100x-1000x P&L errors
- FIFO allocations table has correct values
- Position monitor does NOT use these columns → live trading is safe

### 2. **Comprehensive Audit** ✅
- Audited all 40+ files referencing `realized_profit` and `pnl_usd`
- Categorized into: Trade Recording, Reporting, Passive/Accumulation, Test/Debug
- Documented in `docs/REFACTORING_PLAN_pnl_columns.md`

### 3. **Core Fixes Implemented** ✅

#### A. trade_recorder.py (Line 344)
**Before:**
```python
"realized_profit": float(pnl_usd) if side == "sell" and pnl_usd is not None else None,
```

**After:**
```python
# SELL realized_profit deprecated - use fifo_allocations table
# Set to None; will be backfilled from FIFO for historical accuracy
"realized_profit": None,
```

#### B. New Utility: botreport/fifo_helpers.py
Created comprehensive utility with:
- `get_fifo_pnl_subquery()` - Subquery for single trade P&L
- `get_fifo_pnl_join()` - JOIN clause for fifo_allocations
- `get_fifo_pnl_cte()` - Common Table Expression for complex queries
- `get_fifo_stats_query()` - Complete stats query template
- `use_legacy_pnl()` - Migration helper for gradual rollout

#### C. analysis_symbol_performance.py (COMPLETE) ✅
**Lines 23-29:** Removed COL_PNL/COL_PNL_FALLBACK, added FIFO_VERSION

**Lines 91-126:** Completely rewrote query:
```sql
-- OLD: Used COALESCE(realized_profit, pnl_usd)
-- NEW: Uses FIFO allocations with CTE
WITH trade_pnl AS (
    SELECT
        tr.symbol AS symbol,
        tr.order_id,
        COALESCE(SUM(fa.pnl_usd), 0) AS pnl
    FROM trade_records tr
    LEFT JOIN fifo_allocations fa
        ON fa.sell_order_id = tr.order_id
        AND fa.allocation_version = :fifo_version
    WHERE tr.ts >= :start
      AND tr.ts < :end
      AND tr.symbol IS NOT NULL
      AND tr.side = 'sell'
    GROUP BY tr.symbol, tr.order_id
)
SELECT
    symbol,
    COUNT(*) AS total_trades,
    COUNT(*) FILTER (WHERE pnl > 0) AS wins,
    ... (all metrics now use FIFO P&L)
```

---

## What Still Needs to Be Done 🔧

### Priority 1: Remaining Report Files (Current Session)

#### aws_daily_report.py - 2 Critical SQL Queries

**Query 1: query_trigger_breakdown() - Line 555-570**
```sql
-- CURRENT (WRONG):
SELECT
    COALESCE(trigger->>'trigger', 'UNKNOWN') AS trigger_type,
    COUNT(*) AS order_count,
    COALESCE(SUM(realized_profit), 0) AS total_pnl,  -- ❌ WRONG
    COUNT(*) FILTER (WHERE realized_profit > 0) AS win_count,  -- ❌ WRONG
    COUNT(*) FILTER (WHERE realized_profit < 0) AS loss_count,  -- ❌ WRONG
    COUNT(*) FILTER (WHERE realized_profit = 0) AS breakeven_count  -- ❌ WRONG
FROM trade_records
WHERE ... AND side = 'sell'
GROUP BY trigger_type

-- NEEDS TO BE (using FIFO):
WITH trigger_pnl AS (
    SELECT
        COALESCE(tr.trigger->>'trigger', 'UNKNOWN') AS trigger_type,
        tr.order_id,
        COALESCE(SUM(fa.pnl_usd), 0) AS pnl
    FROM trade_records tr
    LEFT JOIN fifo_allocations fa
        ON fa.sell_order_id = tr.order_id
        AND fa.allocation_version = 2
    WHERE ... AND tr.side = 'sell'
    GROUP BY trigger_type, tr.order_id
)
SELECT
    trigger_type,
    COUNT(*) AS order_count,
    COALESCE(SUM(pnl), 0) AS total_pnl,
    COUNT(*) FILTER (WHERE pnl > 0) AS win_count,
    COUNT(*) FILTER (WHERE pnl < 0) AS loss_count,
    COUNT(*) FILTER (WHERE pnl = 0) AS breakeven_count
FROM trigger_pnl
GROUP BY trigger_type
ORDER BY total_pnl DESC
```

**Query 2: query_source_stats() - Line 607-619**
```sql
-- CURRENT (WRONG):
SELECT
    COALESCE(source, 'unknown') AS source_type,
    COUNT(*) FILTER (WHERE side = 'buy') AS buy_count,
    COUNT(*) FILTER (WHERE side = 'sell') AS sell_count,
    COALESCE(SUM(realized_profit) FILTER (WHERE side = 'sell'), 0) AS total_pnl  -- ❌ WRONG
FROM trade_records
WHERE ...
GROUP BY source_type

-- NEEDS TO BE (using FIFO):
SELECT
    COALESCE(tr.source, 'unknown') AS source_type,
    COUNT(*) FILTER (WHERE tr.side = 'buy') AS buy_count,
    COUNT(*) FILTER (WHERE tr.side = 'sell') AS sell_count,
    COALESCE(SUM(fa.pnl_usd) FILTER (WHERE tr.side = 'sell'), 0) AS total_pnl
FROM trade_records tr
LEFT JOIN fifo_allocations fa
    ON fa.sell_order_id = tr.order_id
    AND fa.allocation_version = 2
WHERE ...
GROUP BY source_type
ORDER BY (buy_count + sell_count) DESC
```

**Other usages in aws_daily_report.py:**
- Lines 751, 1080, 1116, 1159, 1875: Column picking in DataFrames
  - These will work automatically once realized_profit is backfilled
  - No code changes needed (will fall back gracefully)

#### metrics_compute.py

**Lines 37-38:** Update configuration
```python
# CURRENT:
COL_PNL = os.getenv("REPORT_COL_PNL") or "realized_profit"
COL_PNL_FALLBACK = "pnl_usd"

# CHANGE TO:
# Deprecated - use FIFO allocations
FIFO_VERSION = int(os.getenv("FIFO_ALLOCATION_VERSION", "2"))
```

**Line 622:** Update query in `compute_trade_stats()`
```sql
-- CURRENT:
SELECT COALESCE(realized_profit, pnl_usd)::numeric AS pnl

-- CHANGE TO:
SELECT COALESCE(
    (SELECT SUM(fa.pnl_usd)
     FROM fifo_allocations fa
     WHERE fa.sell_order_id = trade_records.order_id
       AND fa.allocation_version = 2), 0
)::numeric AS pnl
```

**Lines 707, 748:** Similar updates needed

---

### Priority 2: Backfill Script (Current Session)

Create `scripts/backfill_realized_profit_from_fifo.py`:

```python
#!/usr/bin/env python3
"""
Backfill realized_profit from FIFO allocations.

Usage:
    python -m scripts.backfill_realized_profit_from_fifo --version 2 --dry-run
    python -m scripts.backfill_realized_profit_from_fifo --version 2  # Actually update
"""

import argparse
import asyncio
from sqlalchemy import text

async def backfill(version: int, dry_run: bool = False):
    # Initialize database connection
    from scripts.compute_allocations import init_dependencies
    db, logger_manager, precision_utils, logger = await init_dependencies()

    print(f"Backfilling realized_profit from FIFO allocations (version {version})")
    print(f"Mode: {'DRY RUN' if dry_run else 'LIVE UPDATE'}")

    # Count affected rows
    async with db.async_session() as session:
        count_query = text("""
            SELECT COUNT(*)
            FROM trade_records tr
            WHERE tr.side = 'sell'
              AND (tr.realized_profit IS NULL
                   OR ABS(tr.realized_profit - COALESCE((
                       SELECT SUM(fa.pnl_usd)
                       FROM fifo_allocations fa
                       WHERE fa.sell_order_id = tr.order_id
                         AND fa.allocation_version = :version
                   ), 0)) > 0.01)
        """)
        result = await session.execute(count_query, {'version': version})
        count = result.scalar()
        print(f"Rows to update: {count:,}")

    if count == 0:
        print("No rows need updating!")
        return

    if dry_run:
        print("DRY RUN - no changes made")
        return

    # Perform update
    async with db.async_session() as session:
        update_query = text("""
            UPDATE trade_records tr
            SET realized_profit = (
                SELECT COALESCE(SUM(fa.pnl_usd), 0)
                FROM fifo_allocations fa
                WHERE fa.sell_order_id = tr.order_id
                  AND fa.allocation_version = :version
            )
            WHERE tr.side = 'sell'
              AND (tr.realized_profit IS NULL
                   OR ABS(tr.realized_profit - COALESCE((
                       SELECT SUM(fa.pnl_usd)
                       FROM fifo_allocations fa
                       WHERE fa.sell_order_id = tr.order_id
                         AND fa.allocation_version = :version
                   ), 0)) > 0.01)
        """)
        result = await session.execute(update_query, {'version': version})
        await session.commit()
        print(f"✅ Updated {result.rowcount:,} rows")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--version', type=int, default=2)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    asyncio.run(backfill(args.version, args.dry_run))

if __name__ == "__main__":
    main()
```

---

### Priority 3: Testing & Deployment

**Local Testing:**
```bash
# 1. Test backfill (dry run)
python -m scripts.backfill_realized_profit_from_fifo --version 2 --dry-run

# 2. Run backfill
python -m scripts.backfill_realized_profit_from_fifo --version 2

# 3. Verify accuracy
python -m verify_email_report

# 4. Generate test report
python -m botreport.aws_daily_report  # Check AVAX-USD shows ~-$0.27
```

**Deployment to Production:**
```bash
# 1. Commit all changes
git add .
git commit -m "fix: Replace realized_profit/pnl_usd with FIFO allocations

- Set trade_recorder.py to write NULL for realized_profit
- Created fifo_helpers.py utility for FIFO queries
- Updated analysis_symbol_performance.py to use FIFO
- Updated aws_daily_report.py critical queries (2 SQL queries)
- Updated metrics_compute.py to use FIFO
- Created backfill script for historical data
- Fixes #[issue-number] - Report showing 100x-1000x P&L errors"

# 2. Push to GitHub
git push origin bugfix/single-fifo-engine

# 3. Deploy to server
ssh bottrader-aws
cd /opt/bot
git pull origin bugfix/single-fifo-engine

# 4. Run backfill on server
docker exec sighook python3 -m scripts.backfill_realized_profit_from_fifo --version 2 --dry-run
docker exec sighook python3 -m scripts.backfill_realized_profit_from_fifo --version 2

# 5. Rebuild containers
docker compose -f docker-compose.aws.yml build --no-cache sighook
docker compose -f docker-compose.aws.yml restart sighook

# 6. Verify
docker exec sighook python3 -m verify_email_report

# 7. Monitor next report generation
tail -f /opt/bot/logs/bot_report.log
```

---

## What Remains for Next Session (Option B)

1. **passive_order_manager.py** (lines 972-980): Update passive trade stats
2. **accumulation_manager.py** (lines 102-144): Update profit allocation
3. **leader_board.py** (lines 45-78): Update leaderboard queries
4. **verify_email_report.py**: Update validation queries
5. **Table Schema Cleanup**: Discuss removing NULL columns from trade_records

---

## Success Criteria

✅ **Implemented:**
- trade_recorder.py no longer populates realized_profit
- analysis_symbol_performance.py uses FIFO
- fifo_helpers.py utility created

⏳ **Still Need:**
- aws_daily_report.py 2 SQL queries updated
- metrics_compute.py updated
- Backfill script created and tested
- All changes deployed to production
- AVAX-USD shows ~-$0.27 (not -$276.53) in next report

---

## Estimated Time Remaining

**To Complete Option A (Core Fixes):**
- Update aws_daily_report.py queries: 15 min
- Update metrics_compute.py: 10 min
- Create backfill script: 10 min
- Test locally: 15 min
- Commit and deploy: 20 min
- **Total: ~70 minutes**

**Current Progress: ~60% complete**

---

## Files Modified This Session

1. ✅ `SharedDataManager/trade_recorder.py` - Line 344
2. ✅ `botreport/fifo_helpers.py` - NEW FILE
3. ✅ `botreport/analysis_symbol_performance.py` - Complete rewrite of query
4. ✅ `docs/CRITICAL_BUG_ANALYSIS_realized_profit.md` - NEW FILE
5. ✅ `docs/REFACTORING_PLAN_pnl_columns.md` - NEW FILE
6. ⏳ `botreport/aws_daily_report.py` - 2 queries need updating
7. ⏳ `botreport/metrics_compute.py` - Needs updating
8. ⏳ `scripts/backfill_realized_profit_from_fifo.py` - Needs creation

---

*Session created: 2025-12-05*
*Status: IN PROGRESS - Core fixes implemented, final touches needed*
