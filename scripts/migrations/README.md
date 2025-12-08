# Database Migrations

This directory contains database migration scripts for BotTrader.

## Quick Reference

### Migration 001: Remove Deprecated P&L Columns

**Scheduled Date:** 2025-12-29 (3 weeks monitoring period)

**Quick Test:**
```bash
# Preview what will happen (safe)
python -m scripts.migrations.001_remove_deprecated_columns --dry-run
```

**Columns to be removed:**
- `pnl_usd` → Use `fifo_allocations.pnl_usd`
- `realized_profit` → Use `fifo_allocations.pnl_usd`
- `parent_id` → Use `parent_ids` array
- `cost_basis` → Use `fifo_allocations.cost_basis_usd`

**See also:**
- `docs/REMINDER_2025-12-29_schema_cleanup.md` - Detailed checklist
- `docs/NEXT_SESSION_SCHEMA_CLEANUP.md` - Migration plan
- `docs/REFACTORING_PLAN_pnl_columns.md` - Full analysis

## Migration Guidelines

### Before Running Any Migration:

1. **Backup database**
   ```bash
   ssh bottrader-aws "docker exec db pg_dump -U bot_user bot_trader_db > /tmp/backup_$(date +%Y%m%d).sql"
   ```

2. **Test in dry-run mode first**
   ```bash
   python -m scripts.migrations.XXX_migration_name --dry-run
   ```

3. **Test on snapshot before production**
   - Export production DB
   - Restore to test environment
   - Run migration
   - Verify everything works

4. **Have rollback plan ready**

### Migration Naming Convention

`NNN_descriptive_name.py` where:
- `NNN` = Sequential number (001, 002, etc.)
- `descriptive_name` = What the migration does
- Must include `--dry-run` and `--execute` modes
- Must verify prerequisites before execution
- Must include rollback instructions in docstring

## Example Migration Run

```bash
# 1. Backup first
ssh bottrader-aws "docker exec db pg_dump -U bot_user bot_trader_db > /tmp/backup.sql"

# 2. Preview changes
python -m scripts.migrations.001_remove_deprecated_columns --dry-run

# 3. Execute (will ask for confirmation)
python -m scripts.migrations.001_remove_deprecated_columns --execute

# 4. Verify
docker exec db psql -U bot_user -d bot_trader_db -c "\d trade_records"
```

## Current Migrations

| Number | Name | Status | Scheduled Date |
|--------|------|--------|----------------|
| 001 | Remove deprecated P&L columns | Pending | 2025-12-29 |
