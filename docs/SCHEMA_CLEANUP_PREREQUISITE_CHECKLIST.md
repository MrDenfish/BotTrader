# Schema Cleanup Migration - Prerequisite Checklist

**Due Date:** December 29, 2025
**Status:** READY FOR PREREQUISITE VERIFICATION
**Last Updated:** December 27, 2025

---

## Overview

Before executing the hard removal of deprecated columns from `trade_records` table, verify all prerequisites are met.

---

## Prerequisite Checks

### ✅ Check 1: No Deprecated Column Warnings in Logs

**Purpose:** Verify no code is accessing the deprecated columns

**Commands:**
```bash
# Check webhook logs
ssh bottrader-aws "docker logs webhook 2>&1 | grep 'DEPRECATED:' | tail -50"

# Check sighook logs
ssh bottrader-aws "docker logs sighook 2>&1 | grep 'DEPRECATED:' | tail -50"
```

**Expected Result:** No output (no DEPRECATED warnings)

**Status:** [ ] PASS / [ ] FAIL

---

### ✅ Check 2: Recent Trades Have NULL Deprecated Columns

**Purpose:** Verify soft deprecation is working (no new data written to deprecated columns)

**Command:**
```bash
ssh bottrader-aws "docker exec db psql -U bot_user -d bot_trader_db -c \"
SELECT COUNT(*) as recent_trades_with_deprecated_values
FROM trade_records
WHERE order_time > NOW() - INTERVAL '21 days'
  AND (pnl_usd IS NOT NULL OR realized_profit IS NOT NULL);
\""
```

**Expected Result:** `0` (zero trades with non-NULL deprecated values)

**Status:** [ ] PASS / [ ] FAIL

---

### ✅ Check 3: Sample Recent Trades

**Purpose:** Visual verification that recent trades have NULL in deprecated columns

**Command:**
```bash
ssh bottrader-aws "docker exec db psql -U bot_user -d bot_trader_db -c \"
SELECT order_id, symbol, side, order_time, pnl_usd, realized_profit, parent_id, cost_basis
FROM trade_records
WHERE order_time > NOW() - INTERVAL '7 days'
ORDER BY order_time DESC
LIMIT 10;
\""
```

**Expected Result:** All rows show `NULL` for `pnl_usd`, `realized_profit`, `parent_id`, `cost_basis`

**Status:** [ ] PASS / [ ] FAIL

---

### ✅ Check 4: FIFO Allocations Working

**Purpose:** Verify FIFO allocations table is being populated and used

**Command:**
```bash
ssh bottrader-aws "docker exec db psql -U bot_user -d bot_trader_db -c \"
SELECT
    COUNT(DISTINCT sell_order_id) as total_sell_orders,
    COUNT(*) as total_allocations,
    SUM(pnl_usd) as total_pnl,
    AVG(pnl_usd) as avg_pnl
FROM fifo_allocations
WHERE allocation_version = 2
  AND sell_time > NOW() - INTERVAL '21 days';
\""
```

**Expected Result:**
- `total_sell_orders` > 0
- `total_allocations` > 0
- `total_pnl` shows reasonable value

**Status:** [ ] PASS / [ ] FAIL

---

### ✅ Check 5: Email Reports Accurate

**Purpose:** Verify reports show correct P&L using FIFO allocations

**Manual Check:**
- Review most recent daily email report
- Verify Risk & Capital metrics look reasonable:
  - Max Drawdown: Should be ~20-30% (NOT 99,000%+)
  - Cash: Should show actual balance (NOT $0.00)
  - Invested %: Should be reasonable (NOT 0.0%)

**Expected Result:** All metrics look accurate and reasonable

**Status:** [ ] PASS / [ ] FAIL

---

### ✅ Check 6: Monitoring Period Complete

**Purpose:** Verify soft deprecation has been running long enough

**Command:**
```bash
ssh bottrader-aws "docker exec db psql -U bot_user -d bot_trader_db -c \"
SELECT
    MIN(order_time) as first_null_trade,
    MAX(order_time) as latest_null_trade,
    AGE(NOW(), MIN(order_time)) as monitoring_duration
FROM trade_records
WHERE pnl_usd IS NULL
  AND realized_profit IS NULL
  AND order_time > '2025-12-01';
\""
```

**Expected Result:** `monitoring_duration` >= 21 days

**Status:** [ ] PASS / [ ] FAIL

---

## Migration Readiness Decision

**All checks must PASS before proceeding with migration.**

### If ALL Checks PASS:
✅ **READY TO MIGRATE** - Proceed with migration on December 29, 2025

### If ANY Check FAILS:
❌ **NOT READY** - Investigate failures before proceeding:
1. Review failed check(s)
2. Identify root cause
3. Fix issues
4. Re-run all prerequisite checks
5. Postpone migration if needed

---

## Next Steps (After All Checks Pass)

1. **Create Database Backup**
   ```bash
   ssh bottrader-aws "docker exec db pg_dump -U bot_user bot_trader_db > /tmp/pre_schema_cleanup_backup_$(date +%Y%m%d).sql"
   scp bottrader-aws:/tmp/pre_schema_cleanup_backup_*.sql ./backups/
   ```

2. **Run Migration Script (Dry Run)**
   ```bash
   ssh bottrader-aws "cd /opt/bot && python -m scripts.migrations.001_remove_deprecated_columns --dry-run"
   ```

3. **Execute Migration**
   ```bash
   ssh bottrader-aws "cd /opt/bot && python -m scripts.migrations.001_remove_deprecated_columns --execute"
   ```

4. **Update TableModels** - See `docs/reminders/REMINDER_2025-12-29_schema_cleanup.md` for details

5. **Deploy and Verify** - Restart containers and verify reports still work

---

## Reference Documents

- **Full Migration Plan:** `docs/reminders/REMINDER_2025-12-29_schema_cleanup.md`
- **Refactoring Plan:** `docs/planning/REFACTORING_PLAN_pnl_columns.md`
- **Schema Cleanup Tasks:** `docs/planning/NEXT_SESSION_SCHEMA_CLEANUP.md`
- **Migration Script:** `scripts/migrations/001_remove_deprecated_columns.py`

---

**Created:** December 27, 2025
**AWS Server:** bottrader-aws (54.187.252.72)
**Note:** AWS server was unreachable during checklist creation. Run checks when server is accessible.
