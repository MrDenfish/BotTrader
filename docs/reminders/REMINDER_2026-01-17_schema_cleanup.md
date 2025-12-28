# ⏰ REMINDER: Schema Cleanup - Hard Removal of Deprecated Columns

**Action Date:** 2026-01-17 (21 days from 2025-12-27)
**Status:** SCHEDULED
**Priority:** MEDIUM
**Type:** Database Migration

---

## What Happened

**Original Plan:** Hard removal scheduled for Dec 29, 2025
**Issue Found:** Soft deprecation was never implemented - code was still writing to deprecated columns
**Action Taken:** Implemented soft deprecation on Dec 27, 2025
**New Timeline:** 21-day monitoring period → eligible for hard removal on Jan 17, 2026

---

## Soft Deprecation Status

**Deployed:** December 27, 2025 @ 11:09 AM PST
**Commit:** a251bb9 - "refactor: Implement soft deprecation of P&L columns in trade_records"

**Changes Made:**
- `sell_trade.pnl_usd = None` (instead of FIFO result)
- `sell_trade.realized_profit = None` (instead of FIFO result)
- Removed `realized_profit` updates on parent trades
- All P&L data now exclusively managed in `fifo_allocations` table

**Containers Restarted:** webhook, sighook @ 11:09 AM PST

---

## What to Do on January 17, 2026

Execute **Phase 2: Hard Removal** of deprecated columns from `trade_records` table.

### Deprecated Columns to Remove:
1. `pnl_usd` - Replaced by `fifo_allocations.pnl_usd`
2. `realized_profit` - Replaced by `fifo_allocations.pnl_usd`
3. `parent_id` - Replaced by `parent_ids` array
4. `cost_basis` - Replaced by `fifo_allocations.cost_basis_usd`

---

## Prerequisites Checklist

Before proceeding, verify:

- [ ] **Monitoring Period Complete:** Soft deprecation has been running for 21+ days
- [ ] **No Warnings in Logs:** No "DEPRECATED" warnings for deprecated column access
  ```bash
  ssh bottrader-aws "docker logs webhook 2>&1 | grep 'DEPRECATED:' | tail -50"
  ```
- [ ] **Reports Accurate:** Daily email reports showing correct P&L values
  - Check that metrics look reasonable
  - Verify no sudden drops or spikes
- [ ] **All Recent Trades Have NULL:** Check recent trades
  ```bash
  ssh bottrader-aws "docker exec db psql -U bot_user -d bot_trader_db -c \"SELECT COUNT(*) FROM trade_records WHERE order_time > '2025-12-27' AND (pnl_usd IS NOT NULL OR realized_profit IS NOT NULL);\""
  ```
  Expected result: 0

**IMPORTANT:** All trades since Dec 27, 2025 should have NULL in deprecated columns.

---

## Migration Steps

### Step 1: Run Prerequisite Checks

Use the checklist: `docs/SCHEMA_CLEANUP_PREREQUISITE_CHECKLIST.md`

All 6 checks must PASS before proceeding.

---

### Step 2: Backup Database

```bash
# Create backup before migration
ssh bottrader-aws "docker exec db pg_dump -U bot_user bot_trader_db > /tmp/pre_schema_cleanup_backup_$(date +%Y%m%d).sql"

# Copy backup locally
scp bottrader-aws:/tmp/pre_schema_cleanup_backup_*.sql ./backups/
```

---

### Step 3: Run Migration Script

The migration script is already created: `scripts/migrations/001_remove_deprecated_columns.py`

```bash
# Dry run first (verify what will happen)
ssh bottrader-aws "cd /opt/bot && python -m scripts.migrations.001_remove_deprecated_columns --dry-run"

# Review output carefully

# Execute migration
ssh bottrader-aws "cd /opt/bot && python -m scripts.migrations.001_remove_deprecated_columns --execute"
```

---

### Step 4: Update TableModels

Update `TableModels/trade_record.py` to remove deprecated columns:

```python
class TradeRecord(Base):
    __tablename__ = 'trade_records'

    # Remove these lines:
    # pnl_usd = Column(Float, nullable=True)
    # realized_profit = Column(Float, nullable=True)
    # parent_id = Column(String, nullable=True)
    # cost_basis = Column(Float, nullable=True)
```

Commit and push the changes:
```bash
git add TableModels/trade_record.py
git commit -m "refactor: Remove deprecated P&L columns from TradeRecord model"
git push origin main
```

---

### Step 5: Deploy Updated Code

```bash
# Pull updated code on AWS
ssh bottrader-aws "cd /opt/bot && git pull origin main"

# Restart containers with updated models
ssh bottrader-aws "cd /opt/bot && docker compose -f docker-compose.aws.yml restart webhook sighook"
```

---

### Step 6: Verification

```bash
# 1. Check schema - verify columns are gone
ssh bottrader-aws "docker exec db psql -U bot_user -d bot_trader_db -c \"\\d trade_records\""

# 2. Test FIFO allocations still work
ssh bottrader-aws "docker exec db psql -U bot_user -d bot_trader_db -c \"SELECT COUNT(*), ROUND(SUM(pnl_usd)::numeric, 2) as total_pnl FROM fifo_allocations WHERE allocation_version = 2 AND sell_time > NOW() - INTERVAL '7 days';\""

# 3. Generate email report (test end-to-end)
ssh bottrader-aws "cd /opt/bot && python -m botreport.aws_daily_report"

# 4. Monitor logs for errors
ssh bottrader-aws "docker logs webhook --tail 100 -f"
```

---

## Rollback Plan

If any issues are detected:

```bash
# Option 1: Restore from backup
ssh bottrader-aws "docker exec -i db psql -U bot_user -d bot_trader_db < /tmp/pre_schema_cleanup_backup_YYYYMMDD.sql"

# Option 2: Re-add columns
ssh bottrader-aws "docker exec db psql -U bot_user -d bot_trader_db -c \"
ALTER TABLE trade_records
ADD COLUMN pnl_usd FLOAT,
ADD COLUMN realized_profit FLOAT,
ADD COLUMN parent_id VARCHAR,
ADD COLUMN cost_basis FLOAT;
\""

# Revert TableModel changes
git revert <commit-hash>
git push origin main
ssh bottrader-aws "cd /opt/bot && git pull && docker compose -f docker-compose.aws.yml restart webhook sighook"
```

---

## Expected Outcomes

After successful migration:

- ✅ `trade_records` table reduced by 4 columns
- ✅ Table size reduced by ~5-10%
- ✅ Schema cleaner and easier to understand
- ✅ No risk of using wrong column for P&L
- ✅ All P&L queries use `fifo_allocations` (single source of truth)
- ✅ Reports continue showing accurate values

---

## Reference Documentation

- **Prerequisite Checklist:** `docs/SCHEMA_CLEANUP_PREREQUISITE_CHECKLIST.md`
- **Migration Plan:** `docs/planning/NEXT_SESSION_SCHEMA_CLEANUP.md`
- **Refactoring Plan:** `docs/planning/REFACTORING_PLAN_pnl_columns.md`
- **Migration Script:** `scripts/migrations/001_remove_deprecated_columns.py`
- **FIFO Helpers:** `botreport/fifo_helpers.py`

---

## Timeline Summary

```
Dec 27, 2025  ✅ Soft deprecation implemented and deployed
Dec 28 - Jan 16  ⏳ Monitoring period (21 days)
Jan 17, 2026  🔴 Eligible for hard removal (run prerequisite checks first)
```

---

## Notes

- **Soft deprecation commit:** a251bb9 (Dec 27, 2025)
- **Old deadline (missed):** Dec 29, 2025 - soft deprecation not implemented
- **New deadline:** Jan 17, 2026 (earliest eligible date)
- **May postpone further if needed** - no rush, monitoring is most important

---

## Contact/Context

- **User:** Manny
- **Project:** BotTrader (Coinbase trading bot)
- **Environment:** AWS EC2, Docker containers
- **Database:** PostgreSQL (bot_trader_db)
- **Current Branch:** main

---

**Created:** 2025-12-27
**Due Date:** 2026-01-17 (or later)
**Status:** SCHEDULED - Monitoring period active
