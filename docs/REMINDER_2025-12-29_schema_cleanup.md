# ⏰ REMINDER: Schema Cleanup - Hard Removal of Deprecated Columns

**Action Date:** 2025-12-29 (3 weeks from 2025-12-08)
**Status:** SCHEDULED
**Priority:** MEDIUM
**Type:** Database Migration

---

## What to Do

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
  - PEPE-USD: ~$4.44 profit (from FIFO)
  - AVAX-USD: ~-$0.27 (not -$276.53)
- [ ] **All Recent Trades Have NULL:** Check recent trades
  ```bash
  ssh bottrader-aws "docker exec db psql -U bot_user -d bot_trader_db -c \"SELECT COUNT(*) FROM trade_records WHERE order_time > NOW() - INTERVAL '21 days' AND (pnl_usd IS NOT NULL OR realized_profit IS NOT NULL);\""
  ```
  Expected result: 0

---

## Migration Steps

### Step 1: Backup Database

```bash
# Create backup before migration
ssh bottrader-aws "docker exec db pg_dump -U bot_user bot_trader_db > /tmp/pre_schema_cleanup_backup_$(date +%Y%m%d).sql"

# Copy backup locally
scp bottrader-aws:/tmp/pre_schema_cleanup_backup_*.sql ./backups/
```

### Step 2: Run Migration Script

The migration script is already created: `scripts/migrations/001_remove_deprecated_columns.py`

```bash
# Dry run first (verify what will happen)
ssh bottrader-aws "cd /opt/bot && python -m scripts.migrations.001_remove_deprecated_columns --dry-run"

# Review output carefully

# Execute migration
ssh bottrader-aws "cd /opt/bot && python -m scripts.migrations.001_remove_deprecated_columns --execute"
```

### Step 3: Update TableModels

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
git push origin bugfix/single-fifo-engine
```

### Step 4: Deploy Updated Code

```bash
# Pull updated code on AWS
ssh bottrader-aws "cd /opt/bot && git pull origin bugfix/single-fifo-engine"

# Restart containers with updated models
ssh bottrader-aws "cd /opt/bot && docker compose -f docker-compose.aws.yml restart webhook sighook"
```

### Step 5: Verification

```bash
# 1. Check schema
ssh bottrader-aws "docker exec db psql -U bot_user -d bot_trader_db -c \"\\d trade_records\""
# Verify pnl_usd, realized_profit, parent_id, cost_basis are gone

# 2. Test PEPE trade (should still work via FIFO)
ssh bottrader-aws "docker exec db psql -U bot_user -d bot_trader_db -c \"SELECT pnl_usd FROM fifo_allocations WHERE sell_order_id = 'c7bbcc34-fd05-407b-bdb6-6c3733fd4c67';\""
# Expected: 4.43673737

# 3. Generate email report
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
git push origin bugfix/single-fifo-engine
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

- **Migration Plan:** `docs/NEXT_SESSION_SCHEMA_CLEANUP.md`
- **Refactoring Plan:** `docs/REFACTORING_PLAN_pnl_columns.md`
- **Migration Script:** `scripts/migrations/001_remove_deprecated_columns.py`
- **FIFO Helpers:** `botreport/fifo_helpers.py`

---

## Notes

- **Soft deprecation started:** ~2025-12-07 (recent trades have NULL values)
- **FIFO allocations working:** Verified with PEPE-USD trade ($4.44 profit)
- **Reports using FIFO:** All reporting code updated to use `fifo_allocations` table
- **Position monitor safe:** Does NOT use deprecated columns

---

## Contact/Context

- **User:** Manny
- **Project:** BotTrader (Coinbase trading bot)
- **Environment:** AWS EC2, Docker containers
- **Database:** PostgreSQL (bot_trader_db)
- **Current Branch:** bugfix/single-fifo-engine

---

**Created:** 2025-12-08
**Due Date:** 2025-12-29 (3 weeks)
**Status:** PENDING - Check prerequisites before proceeding
