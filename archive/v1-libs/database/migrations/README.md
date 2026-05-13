# Database Migrations

This directory contains SQL migration scripts for database schema changes.

## FIFO Allocations Migration (001)

### Overview

Migration `001` introduces the FIFO allocations architecture - a ground-up redesign of the PnL calculation system to fix fundamental architectural flaws.

**Problem Being Solved:**
- Current system stores computed values (parent_id, pnl_usd) as if they're immutable facts
- These depend on mutable state (remaining_size), causing unfixable database corruption
- Historical data shows incorrect PnL (sells matched to wrong buys from months ago)

**Solution:**
- Separate immutable trade facts from computed allocations
- New `fifo_allocations` table records buy→sell matches
- Allocations can be deleted and recomputed anytime
- Source of truth (trade_records) remains intact

### Files

- **`001_create_fifo_allocations_tables.sql`** - Forward migration (creates new tables)
- **`001_rollback_fifo_allocations_tables.sql`** - Rollback script (removes new tables)

### Running the Migration

**Prerequisites:**
1. Database backup completed
2. Bot stopped (optional, but recommended for first run)
3. PostgreSQL client installed

**Execute migration:**
```bash
psql postgresql://bot_user:@127.0.0.1:5432/bot_trader_db -f database/migrations/001_create_fifo_allocations_tables.sql
```

**Expected output:**
```
================================================================================
FIFO ALLOCATIONS MIGRATION - Part 1: Creating New Tables
================================================================================

✅ fifo_allocations table created
✅ fifo_computation_log table created
✅ fifo_inventory_snapshot table created
✅ manual_review_queue table created

================================================================================
FIFO ALLOCATIONS MIGRATION - Part 2: Creating Views
================================================================================

✅ v_allocation_health view created
✅ v_unmatched_sells view created
✅ v_pnl_by_symbol view created
✅ v_allocation_discrepancies view created

================================================================================
FIFO ALLOCATIONS MIGRATION - Part 3: Historical Data Snapshot
================================================================================

✅ Historical snapshot created: historical_pnl_snapshot_20251120
   Rows preserved: [count]

================================================================================
FIFO ALLOCATIONS MIGRATION COMPLETE
================================================================================
```

### What Gets Created

#### Tables

1. **`fifo_allocations`** - Core allocations table
   - Records how each SELL matched to BUY(s)
   - Stores computed PnL per allocation
   - Versioned (allows parallel operation)

2. **`fifo_computation_log`** - Audit trail
   - Tracks each allocation computation run
   - Stores errors, statistics, duration
   - Useful for debugging and monitoring

3. **`fifo_inventory_snapshot`** - Performance optimization
   - Periodic snapshots of inventory state
   - Enables fast incremental computation
   - Optional (can recompute from scratch)

4. **`manual_review_queue`** - Issue tracking
   - Tracks unmatched sells requiring investigation
   - Workflow management (pending → resolved)
   - Severity levels (low, medium, high, critical)

5. **`historical_pnl_snapshot_20251120`** - Forensic backup
   - Preserves old corrupted PnL data
   - Useful for tax reporting (what was originally calculated)
   - Compare old vs. new allocations

#### Views

1. **`v_allocation_health`** - System health at a glance
2. **`v_unmatched_sells`** - Sells with no matching buy
3. **`v_pnl_by_symbol`** - Current PnL aggregated by symbol
4. **`v_allocation_discrepancies`** - Find allocation errors

### Rollback

If you need to rollback the migration:

```bash
psql postgresql://bot_user:@127.0.0.1:5432/bot_trader_db -f database/migrations/001_rollback_fifo_allocations_tables.sql
```

You will be prompted to confirm (`YES` in all caps).

**Rollback is safe:**
- No destructive changes to existing `trade_records` table
- Old `parent_id`/`pnl_usd` columns remain unchanged
- Historical snapshot is preserved even after rollback

### After Migration

**Next steps:**

1. **Bootstrap allocations** (compute Version 1):
   ```bash
   python -m scripts.compute_allocations --all-symbols --version 1
   ```

2. **Validate** allocations:
   ```bash
   python -m scripts.validate_allocations --version 1
   ```

3. **Compare** with old system:
   ```sql
   -- Compare total PnL
   SELECT 'old' as system, SUM(pnl_usd) FROM trade_records WHERE side='sell';
   SELECT 'new' as system, SUM(pnl_usd) FROM fifo_allocations WHERE allocation_version=1;
   ```

4. **Parallel operation** (run both systems for validation period)

5. **Cutover** (update reports to use new allocations)

6. **Deprecate old columns** (after validation period)

### Migration Safety

✅ **Non-destructive:**
- Does not modify existing `trade_records` table
- Does not drop any columns
- Adds new tables alongside existing structure

✅ **Reversible:**
- Clean rollback script provided
- Historical data preserved
- Can switch back to old system anytime

✅ **Testable:**
- Run on staging first
- Compare old vs. new PnL
- Validate invariants before production

### Monitoring Queries

**Check migration status:**
```sql
-- Verify tables exist
SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename LIKE 'fifo%';

-- Check views
SELECT viewname FROM pg_views WHERE schemaname='public' AND viewname LIKE 'v_%';

-- Count allocations
SELECT allocation_version, COUNT(*) FROM fifo_allocations GROUP BY allocation_version;
```

**Health check:**
```sql
SELECT * FROM v_allocation_health ORDER BY allocation_version DESC LIMIT 1;
```

**Find problems:**
```sql
-- Unmatched sells
SELECT * FROM v_unmatched_sells;

-- Discrepancies
SELECT * FROM v_allocation_discrepancies;
```

### Troubleshooting

**Problem: Migration fails with "relation already exists"**
```
ERROR:  relation "fifo_allocations" already exists
```

**Solution:** Tables already created. Either:
1. Continue (migration is idempotent for most operations)
2. Rollback first, then re-run migration

**Problem: Permission denied**
```
ERROR:  permission denied for table trade_records
```

**Solution:** Ensure you're connecting as `bot_user` or a user with appropriate permissions.

**Problem: Historical snapshot is empty**
```
Rows preserved: 0
```

**Solution:** This is expected if no trades exist yet, or if all sells have `parent_id=NULL`.

### Design Documentation

For complete architectural details, see:
- **`docs/FIFO_ALLOCATIONS_DESIGN.md`** - Full design specification
- **`.claude/sessions/2025-11-20-1155-FIFO-Allocations-Architecture-Redesign.md`** - Development session notes

---

**Created:** 2025-11-20
**Version:** 001
**Status:** Ready for testing
