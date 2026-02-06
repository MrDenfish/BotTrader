# Next Session: Schema Cleanup & Final FIFO Migration

**Date Created:** 2025-12-05
**Branch:** `bugfix/single-fifo-engine`
**Prerequisites:** Current session's FIFO fixes deployed and verified

---

## What Was Accomplished in Previous Session

### Core Fixes Completed ✅

1. **trade_recorder.py (line 344)** - Set `realized_profit = None` (stops data corruption)
2. **fifo_helpers.py** - Created reusable FIFO query utilities
3. **analysis_symbol_performance.py** - Complete rewrite using FIFO allocations
4. **aws_daily_report.py** - Updated 2 critical SQL queries:
   - Trigger breakdown (lines 554-581)
   - Source stats (lines 618-633)
5. **metrics_compute.py** - Updated all P&L queries:
   - query_trade_pnl function
   - TRADE_STATS_SQL_TR
   - SHARPE_TRADE_SQL_TR
   - MDD_SQL_TR
6. **Deployment** - All changes committed, pushed, and deployed to production

### What This Fixed
- AVAX-USD: Now shows ~-$0.27 instead of -$276.53 (1000x error)
- PENGU-USD: Now shows actual -$0.97 loss instead of false profit
- All new trades write NULL to `realized_profit` (no more corruption)
- All reports use FIFO allocations as single source of truth

---

## What Remains for This Session

### Priority 1: Update Remaining Report Files

**Reference Document:** `docs/REFACTORING_PLAN_pnl_columns.md`

#### File 1: passive_order_manager.py (lines 972-980)
**Current Code:**
```python
# Lines 972-980: Passive trade stats query
stats_query = text("""
    SELECT
        COUNT(*) as total_trades,
        SUM(CASE WHEN realized_profit > 0 THEN 1 ELSE 0 END) as winning_trades,
        SUM(CASE WHEN realized_profit < 0 THEN 1 ELSE 0 END) as losing_trades,
        AVG(realized_profit) as avg_profit,
        SUM(realized_profit) as total_profit
    FROM trade_records
    WHERE source = 'passive'
""")
```

**Needs to Change To:**
```python
# Use FIFO allocations for passive trade stats
stats_query = text("""
    SELECT
        COUNT(DISTINCT tr.order_id) as total_trades,
        COUNT(DISTINCT tr.order_id) FILTER (WHERE COALESCE(SUM(fa.pnl_usd), 0) > 0) as winning_trades,
        COUNT(DISTINCT tr.order_id) FILTER (WHERE COALESCE(SUM(fa.pnl_usd), 0) < 0) as losing_trades,
        AVG(COALESCE(SUM(fa.pnl_usd), 0)) as avg_profit,
        SUM(COALESCE(fa.pnl_usd, 0)) as total_profit
    FROM trade_records tr
    LEFT JOIN fifo_allocations fa
        ON fa.sell_order_id = tr.order_id
        AND fa.allocation_version = 2
    WHERE tr.source = 'passive'
        AND tr.side = 'sell'
    GROUP BY tr.order_id
""")
```

#### File 2: accumulation_manager.py (lines 102-144)
**Current Code:**
```python
# Lines 102-144: Profit allocation for DCA
def calculate_profit_allocation(self, symbol: str):
    query = text("""
        SELECT
            SUM(realized_profit) as total_profit,
            COUNT(*) as trade_count
        FROM trade_records
        WHERE symbol = :symbol
            AND source = 'accumulation'
            AND realized_profit > 0
    """)
```

**Needs to Change To:**
```python
def calculate_profit_allocation(self, symbol: str):
    query = text("""
        SELECT
            SUM(fa.pnl_usd) as total_profit,
            COUNT(DISTINCT tr.order_id) as trade_count
        FROM trade_records tr
        LEFT JOIN fifo_allocations fa
            ON fa.sell_order_id = tr.order_id
            AND fa.allocation_version = 2
        WHERE tr.symbol = :symbol
            AND tr.source = 'accumulation'
            AND tr.side = 'sell'
            AND fa.pnl_usd > 0
    """)
```

#### File 3: leader_board.py (lines 45-78)
**Current Code:**
```python
# Lines 45-78: Leaderboard ranking query
def get_symbol_rankings(self):
    query = text("""
        SELECT
            symbol,
            COUNT(*) as trades,
            SUM(realized_profit) as total_pnl,
            AVG(realized_profit) as avg_pnl,
            (SUM(CASE WHEN realized_profit > 0 THEN 1 ELSE 0 END)::float /
             COUNT(*)::float * 100) as win_rate
        FROM trade_records
        WHERE ts >= NOW() - INTERVAL '30 days'
        GROUP BY symbol
        ORDER BY total_pnl DESC
    """)
```

**Needs to Change To:**
```python
def get_symbol_rankings(self):
    query = text("""
        WITH symbol_pnl AS (
            SELECT
                tr.symbol,
                tr.order_id,
                COALESCE(SUM(fa.pnl_usd), 0) as pnl
            FROM trade_records tr
            LEFT JOIN fifo_allocations fa
                ON fa.sell_order_id = tr.order_id
                AND fa.allocation_version = 2
            WHERE tr.ts >= NOW() - INTERVAL '30 days'
                AND tr.side = 'sell'
            GROUP BY tr.symbol, tr.order_id
        )
        SELECT
            symbol,
            COUNT(*) as trades,
            SUM(pnl) as total_pnl,
            AVG(pnl) as avg_pnl,
            (COUNT(*) FILTER (WHERE pnl > 0)::float /
             COUNT(*)::float * 100) as win_rate
        FROM symbol_pnl
        GROUP BY symbol
        ORDER BY total_pnl DESC
    """)
```

#### File 4: verify_email_report.py
**Current:** Uses `COALESCE(realized_profit, pnl_usd)` in validation queries

**Needs to Update:**
- All validation queries to use FIFO allocations
- Compare FIFO values against expected results
- Add tests for AVAX-USD showing ~-$0.27
- Add tests for PENGU-USD showing -$0.97

---

### Priority 2: Schema Cleanup Strategy

#### Option A: Soft Deprecation (Recommended)
**Pros:** Safe, reversible, maintains backward compatibility
**Cons:** Columns remain in table, taking up space

**Implementation:**
1. Set all deprecated columns to NULL:
   ```sql
   UPDATE trade_records
   SET realized_profit = NULL,
       pnl_usd = NULL,
       parent_id = NULL,
       cost_basis = NULL
   WHERE realized_profit IS NOT NULL
      OR pnl_usd IS NOT NULL;
   ```

2. Add deprecation warnings in code:
   ```python
   # In trade_recorder.py or database layer
   DEPRECATED_COLUMNS = ['realized_profit', 'pnl_usd', 'parent_id', 'cost_basis']

   def warn_if_deprecated_column_accessed(column_name):
       if column_name in DEPRECATED_COLUMNS:
           logger.warning(
               f"DEPRECATED: Column '{column_name}' is deprecated. "
               f"Use FIFO allocations table instead."
           )
   ```

3. Monitor logs for any unexpected access

4. After 30-60 days with no issues, proceed to Option B

#### Option B: Hard Removal
**Pros:** Clean schema, no wasted space
**Cons:** Irreversible, requires migration

**Implementation:**
1. Create database migration script
2. Drop columns:
   ```sql
   ALTER TABLE trade_records
   DROP COLUMN IF EXISTS realized_profit,
   DROP COLUMN IF EXISTS pnl_usd,
   DROP COLUMN IF EXISTS parent_id,
   DROP COLUMN IF EXISTS cost_basis;
   ```
3. Update SQLAlchemy models
4. Test thoroughly before deployment

#### Recommended Approach
Start with **Option A** (soft deprecation), then move to **Option B** after verification period.

---

### Priority 3: Testing & Validation

#### Before Deployment:
1. **Run verify_email_report.py**:
   ```bash
   python -m verify_email_report
   ```

2. **Compare specific symbols**:
   - AVAX-USD: Should show ~-$0.27
   - PENGU-USD: Should show -$0.97 loss
   - All other symbols: Check for reasonable values

3. **Test on local database first**:
   ```bash
   # Export production DB snapshot
   # Run updates locally
   # Verify results
   # Then deploy to production
   ```

#### After Deployment:
1. Monitor next daily report generation
2. Save report and compare to previous (erroneous) version
3. Check logs for any errors
4. Verify all P&L metrics look reasonable

---

## Estimated Time

- **Updating remaining files**: 30-45 minutes
- **Schema cleanup (soft deprecation)**: 20-30 minutes
- **Testing & validation**: 30-45 minutes
- **Deployment**: 20-30 minutes

**Total: ~2-2.5 hours**

---

## Success Criteria

✅ **All report files use FIFO allocations**
✅ **No code references deprecated columns**
✅ **Email reports show accurate P&L**
✅ **AVAX-USD shows ~-$0.27 (not -$276.53)**
✅ **PENGU-USD shows -$0.97 loss (not false profit)**
✅ **All validation tests pass**
✅ **No errors in production logs**

---

## Pre-Session Checklist

Before starting the next session, verify:
- [ ] Current FIFO fix has been running for at least 24 hours
- [ ] Daily report has generated successfully
- [ ] P&L numbers in report look accurate
- [ ] No errors in webhook or sighook logs
- [ ] Saved copy of latest email report for comparison

---

## Questions to Decide Before Next Session

1. **Schema cleanup approach**: Soft deprecation first, or go straight to removal?
2. **Timeline**: How long to monitor before removing columns? (30 days? 60 days?)
3. **Backfill**: Skip entirely (recommended) or run for historical data?
4. **Migration testing**: Test on production snapshot first, or directly on production?

---

## Files to Reference During Next Session

1. `docs/REFACTORING_PLAN_pnl_columns.md` - Complete audit of all files
2. `docs/CRITICAL_BUG_ANALYSIS_realized_profit.md` - Root cause analysis
3. `docs/SESSION_SUMMARY_fifo_realized_profit_fix.md` - What was done in previous session
4. `botreport/fifo_helpers.py` - Reusable FIFO query patterns to follow

---

## Notes

- **Backfill script exists** (`scripts/backfill_realized_profit_from_fifo.py`) but is **not recommended** to run
- Better to set deprecated columns to NULL and eventually drop them
- All new code should use FIFO allocations table directly
- Position monitor confirmed safe (doesn't use deprecated columns)

---

*Session plan created: 2025-12-05*
*Status: Ready for next session*
*Branch: bugfix/single-fifo-engine*
