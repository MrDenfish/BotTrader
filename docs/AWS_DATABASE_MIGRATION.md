# AWS Server Database Migration Guide

This guide walks through migrating the AWS production database to support the new FIFO allocations system.

## Overview

The desktop database has been updated with:
1. **New FIFO tables** (fifo_allocations, fifo_computation_log, etc.)
2. **FIFO Version 1** computed (3,446 allocations)
3. **FIFO Version 2** with backfilled missing trades (3,504 allocations - 11 buys added)
4. **Helper views** for monitoring and validation

---

## Prerequisites

### 1. Backup Production Database

**CRITICAL: Always backup before migrations!**

```bash
# SSH to AWS server
ssh your-server

# Create backup directory
mkdir -p /opt/bot/backups

# Full database backup
pg_dump -h localhost -U bot_user -d bot_trader_db -F c -f /opt/bot/backups/bot_trader_db_pre_fifo_$(date +%Y%m%d_%H%M%S).backup

# OR plain SQL backup (easier to inspect)
pg_dump -h localhost -U bot_user -d bot_trader_db -f /opt/bot/backups/bot_trader_db_pre_fifo_$(date +%Y%m%d_%H%M%S).sql
```

**Verify backup:**
```bash
ls -lh /opt/bot/backups/
```

### 2. Pull Latest Code

```bash
cd /opt/bot
git pull origin feature/fifo-allocations-redesign
```

### 3. Stop the Bot (Optional but Recommended)

For first migration, stop the bot to avoid concurrent writes:

```bash
# Check if bot is running
ps aux | grep python

# Stop bot (adjust based on your deployment)
# Option 1: If using systemd
sudo systemctl stop bottrader

# Option 2: If using docker
docker stop bottrader-container

# Option 3: If using screen/tmux
screen -r bottrader
# Then Ctrl+C to stop
```

---

## Migration Steps

### Step 1: Run Database Migration

```bash
cd /opt/bot

# Run migration script
psql -h localhost -U bot_user -d bot_trader_db -f database/migrations/001_create_fifo_allocations_tables.sql
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

### Step 2: Verify Migration

```bash
# Check tables were created
psql -h localhost -U bot_user -d bot_trader_db -c "\dt" | grep fifo

# Expected output:
# public | fifo_allocations         | table | bot_user
# public | fifo_computation_log     | table | bot_user
# public | fifo_inventory_snapshot  | table | bot_user
# public | manual_review_queue      | table | bot_user
```

```bash
# Check views were created
psql -h localhost -U bot_user -d bot_trader_db -c "\dv" | grep "v_"

# Expected output:
# public | v_allocation_health         | view | bot_user
# public | v_unmatched_sells           | view | bot_user
# public | v_pnl_by_symbol             | view | bot_user
# public | v_allocation_discrepancies  | view | bot_user
```

### Step 3: Bootstrap FIFO Allocations (Version 1)

```bash
cd /opt/bot
source venv/bin/activate  # or .venv/bin/activate

# Compute initial allocations
python -m scripts.compute_allocations --version 1 --all-symbols
```

**Expected output:**
```
🎯 Computing FIFO allocations for all symbols...
   Version: 1
   Batch ID: [uuid]

Processing symbol 1/[N]: BTC-USD
Processing symbol 2/[N]: ETH-USD
...

✅ FIFO allocation computation complete
   Total allocations: ~3,400-3,500
   Duration: [time]
```

### Step 4: Validate Allocations

```bash
python -m scripts.validate_allocations --version 1
```

**Expected output:**
```
🔍 Validating FIFO allocations...

✅ Allocation integrity checks passed
✅ No inventory violations detected
✅ Time ordering correct
⚠️  Unmatched sells: [count] (expected if missing buys)

Validation complete.
```

### Step 5: Run Reconciliation with Exchange

**IMPORTANT:** This checks for missing trades by comparing against Coinbase:

```bash
python -m scripts.reconcile_with_exchange --version 1 --tier 1
```

**Two possible outcomes:**

**Outcome A: No missing trades**
```
✅ Reconciliation complete
   Missing BUY orders found: 0
   Database is in sync with exchange
```

**Outcome B: Missing trades detected**
```
⚠️  Missing BUY orders found: [count]

Symbol: BTC-USD
  - Missing buy: [order_id]
  - Time: [timestamp]
  - Size: [amount]
...

Run with --auto-backfill to insert missing trades.
```

### Step 6: Backfill Missing Trades (If Needed)

If reconciliation found missing trades:

```bash
# Review what will be backfilled
python -m scripts.reconcile_with_exchange --version 1 --tier 1

# Backfill missing trades
python -m scripts.reconcile_with_exchange --version 1 --tier 1 --auto-backfill
```

**Output:**
```
🔄 Backfilling missing trades...
   Inserting [count] missing BUY orders

✅ Backfill complete
   [count] trades inserted
```

### Step 7: Recompute as Version 2 (After Backfill)

If you backfilled trades, recompute allocations:

```bash
python -m scripts.compute_allocations --version 2 --all-symbols
```

### Step 8: Validate Version 2

```bash
python -m scripts.validate_allocations --version 2
```

### Step 9: Compare Versions

```bash
python -m scripts.allocation_reports --version 1 --summary
python -m scripts.allocation_reports --version 2 --summary
```

Compare:
- Total allocations (Version 2 should have more if trades were backfilled)
- Total PnL (Version 2 should be more accurate)
- Unmatched sells (Version 2 should have fewer)

---

## Post-Migration

### 1. Restart the Bot

```bash
# Option 1: systemd
sudo systemctl start bottrader

# Option 2: docker
docker start bottrader-container

# Option 3: screen/tmux
screen -S bottrader
cd /opt/bot
source venv/bin/activate
python -m main  # or your bot entry point
```

### 2. Monitor Health

```bash
# Check allocation health
psql -h localhost -U bot_user -d bot_trader_db -c "SELECT * FROM v_allocation_health ORDER BY allocation_version DESC LIMIT 2;"
```

**Expected output:**
```
 allocation_version | total_allocations | matched_allocations | unmatched_sells | total_pnl_usd
--------------------+-------------------+---------------------+-----------------+---------------
                  2 |              3504 |                3491 |              13 |     -12345.67
                  1 |              3446 |                3427 |              19 |     -12355.08
```

### 3. Set Up Weekly Reconciliation

Follow the **AWS_RECONCILIATION_DEPLOY.md** guide to set up weekly cron job:

```bash
chmod +x /opt/bot/scripts/weekly_reconciliation_aws.sh
crontab -e
# Add: 15 9 * * 0 /opt/bot/scripts/weekly_reconciliation_aws.sh
```

---

## Verification Queries

### Check Migration Status

```sql
-- Tables
SELECT tablename
FROM pg_tables
WHERE schemaname='public' AND tablename LIKE 'fifo%'
ORDER BY tablename;

-- Views
SELECT viewname
FROM pg_views
WHERE schemaname='public' AND viewname LIKE 'v_%'
ORDER BY viewname;

-- Indexes
SELECT indexname
FROM pg_indexes
WHERE tablename LIKE 'fifo%'
ORDER BY tablename, indexname;
```

### Check Data

```sql
-- Allocation counts by version
SELECT
    allocation_version,
    COUNT(*) as total_allocations,
    COUNT(buy_order_id) as matched,
    COUNT(*) - COUNT(buy_order_id) as unmatched,
    SUM(pnl_usd) as total_pnl
FROM fifo_allocations
GROUP BY allocation_version
ORDER BY allocation_version;

-- Recent computation logs
SELECT
    symbol,
    allocation_version,
    computation_start,
    computation_duration_ms,
    total_allocations,
    unmatched_sells
FROM fifo_computation_log
ORDER BY computation_start DESC
LIMIT 5;

-- Unmatched sells (should investigate these)
SELECT * FROM v_unmatched_sells
WHERE allocation_version = 2
ORDER BY sell_time DESC
LIMIT 10;
```

### Compare with Old System

```sql
-- Old PnL (from trade_records.pnl_usd)
SELECT
    'old_system' as source,
    COUNT(*) as sell_count,
    SUM(pnl_usd) as total_pnl
FROM trade_records
WHERE side = 'sell' AND pnl_usd IS NOT NULL;

-- New PnL (from fifo_allocations)
SELECT
    'new_system_v' || allocation_version as source,
    COUNT(DISTINCT sell_order_id) as sell_count,
    SUM(pnl_usd) as total_pnl
FROM fifo_allocations
GROUP BY allocation_version
ORDER BY allocation_version;
```

---

## Rollback (If Needed)

If something goes wrong, you can rollback:

```bash
cd /opt/bot
psql -h localhost -U bot_user -d bot_trader_db -f database/migrations/001_rollback_fifo_allocations_tables.sql
```

**You will be prompted to confirm with `YES` (all caps).**

**Rollback is safe:**
- Does NOT modify `trade_records` table
- Old `parent_id`/`pnl_usd` columns remain intact
- Historical snapshot is preserved
- Bot will continue working with old system

After rollback, restore from backup if needed:
```bash
# If custom format backup
pg_restore -h localhost -U bot_user -d bot_trader_db -c /opt/bot/backups/[backup-file].backup

# If SQL backup
psql -h localhost -U bot_user -d bot_trader_db < /opt/bot/backups/[backup-file].sql
```

---

## Troubleshooting

### Problem: Migration fails with "permission denied"

```
ERROR:  permission denied for table trade_records
```

**Solution:**
- Ensure you're connecting as `bot_user`
- Check database permissions: `\du` in psql

### Problem: Tables already exist

```
ERROR:  relation "fifo_allocations" already exists
```

**Solution:**
- Tables already created (migration ran before)
- Check data: `SELECT COUNT(*) FROM fifo_allocations;`
- If empty, safe to continue with Step 3 (Bootstrap)
- If has data, migration already complete

### Problem: compute_allocations fails

```
ModuleNotFoundError: No module named 'fifo_engine'
```

**Solution:**
- Ensure code was pulled: `cd /opt/bot && git pull`
- Check branch: `git branch` (should show feature/fifo-allocations-redesign)
- Verify files exist: `ls -la fifo_engine/`

### Problem: Database connection fails

```
psql: could not connect to server
```

**Solution:**
- Check PostgreSQL is running: `sudo systemctl status postgresql`
- Check connection string in `.env`: `cat /opt/bot/.env | grep DATABASE_URL`
- Test connection: `psql -h localhost -U bot_user -d bot_trader_db -c "SELECT 1;"`

### Problem: Missing trades during reconciliation

```
⚠️  Missing BUY orders found: [large number]
```

**Solution:**
- This is expected if exchange data is incomplete
- Coinbase API only retains ~3 months of fills
- Review logs to understand date ranges
- Backfill only recent missing trades (< 90 days old)
- Older gaps may be unrecoverable

---

## Migration Checklist

- [ ] 1. Backup production database
- [ ] 2. Pull latest code from feature branch
- [ ] 3. Stop bot (optional but recommended)
- [ ] 4. Run migration script (001_create_fifo_allocations_tables.sql)
- [ ] 5. Verify tables and views created
- [ ] 6. Bootstrap Version 1 allocations
- [ ] 7. Validate Version 1
- [ ] 8. Run reconciliation (Tier 1)
- [ ] 9. Backfill missing trades if needed
- [ ] 10. Compute Version 2 (if backfilled)
- [ ] 11. Validate Version 2
- [ ] 12. Compare Version 1 vs 2
- [ ] 13. Restart bot
- [ ] 14. Monitor health (v_allocation_health)
- [ ] 15. Set up weekly reconciliation cron job
- [ ] 16. Document which version is production (1 or 2)

---

## Key Differences: Desktop vs AWS

| Feature | Desktop | AWS Server |
|---------|---------|------------|
| **Database Location** | localhost:5432 | localhost:5432 (same) |
| **Project Path** | `/Users/Manny/Python_Projects/BotTrader` | `/opt/bot` |
| **Virtual Env** | `venv/` or `.venv/` | Check on server |
| **Current State** | Version 2 computed (3,504 allocations) | No FIFO tables yet |
| **Missing Trades** | 11 backfilled in Version 2 | Unknown until reconciliation |

---

## Next Steps After Migration

1. **Monitor Daily:** Check `v_allocation_health` view daily
2. **Weekly Reconciliation:** Cron job will detect new gaps
3. **Compare Old vs New:** Run reports using both systems for 2-4 weeks
4. **Cutover Decision:** After validation period, choose production version
5. **Update Reports:** Switch daily reports to use `fifo_allocations` table
6. **Deprecate Old Columns:** After full cutover, deprecate `parent_id`/`pnl_usd` in `trade_records`

---

## Support

**Documentation:**
- `database/migrations/README.md` - Migration details
- `docs/FIFO_ALLOCATIONS_DESIGN.md` - Architecture design
- `docs/RECONCILIATION_SETUP.md` - Reconciliation setup
- `docs/AWS_RECONCILIATION_DEPLOY.md` - AWS cron deployment

**Common Commands:**
```bash
# Check health
psql -h localhost -U bot_user -d bot_trader_db -c "SELECT * FROM v_allocation_health;"

# View logs
cat /opt/bot/logs/reconciliation_latest.log

# Manual reconciliation
cd /opt/bot && python -m scripts.reconcile_with_exchange --version 2 --tier 1

# Recompute allocations
python -m scripts.compute_allocations --version 3 --all-symbols
```

---

**Created:** 2025-11-21
**Target:** AWS Production Server
**Status:** Ready for deployment
