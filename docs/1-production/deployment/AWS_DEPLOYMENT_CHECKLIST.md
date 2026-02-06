# AWS Deployment Quick Checklist

Quick reference for deploying FIFO allocations system to AWS production server.

## Pre-Deployment

```bash
# 1. Backup database
ssh your-server
pg_dump -h localhost -U bot_user -d bot_trader_db -F c -f /opt/bot/backups/bot_trader_db_pre_fifo_$(date +%Y%m%d).backup

# 2. Pull latest code
cd /opt/bot
git pull origin feature/fifo-allocations-redesign

# 3. Stop bot (optional)
sudo systemctl stop bottrader  # or docker stop, etc.
```

## Database Migration

```bash
# 4. Run migration
psql -h localhost -U bot_user -d bot_trader_db -f database/migrations/001_create_fifo_allocations_tables.sql

# 5. Verify tables created
psql -h localhost -U bot_user -d bot_trader_db -c "\dt" | grep fifo
```

## FIFO Bootstrap

```bash
# 6. Activate environment
cd /opt/bot
source venv/bin/activate

# 7. Compute Version 1
python -m scripts.compute_allocations --version 1 --all-symbols

# 8. Validate
python -m scripts.validate_allocations --version 1

# 9. Reconcile with exchange
python -m scripts.reconcile_with_exchange --version 1 --tier 1

# 10. Backfill if needed
python -m scripts.reconcile_with_exchange --version 1 --tier 1 --auto-backfill

# 11. Compute Version 2 (if backfilled)
python -m scripts.compute_allocations --version 2 --all-symbols

# 12. Validate Version 2
python -m scripts.validate_allocations --version 2
```

## Reconciliation Cron

```bash
# 13. Make script executable
chmod +x /opt/bot/scripts/weekly_reconciliation_aws.sh

# 14. Test manually
./scripts/weekly_reconciliation_aws.sh

# 15. Add to crontab
crontab -e
# Add: 15 9 * * 0 /opt/bot/scripts/weekly_reconciliation_aws.sh

# 16. Verify
crontab -l | grep reconciliation
```

## Post-Deployment

```bash
# 17. Restart bot
sudo systemctl start bottrader

# 18. Check health
psql -h localhost -U bot_user -d bot_trader_db -c "SELECT * FROM v_allocation_health ORDER BY allocation_version DESC LIMIT 2;"

# 19. View reconciliation log
cat /opt/bot/logs/reconciliation_latest.log
```

## Verification Queries

```sql
-- Count allocations by version
SELECT allocation_version, COUNT(*) FROM fifo_allocations GROUP BY allocation_version;

-- Check unmatched sells
SELECT * FROM v_unmatched_sells ORDER BY sell_time DESC LIMIT 10;

-- Compare old vs new PnL
SELECT 'old' as sys, SUM(pnl_usd) FROM trade_records WHERE side='sell';
SELECT 'new' as sys, SUM(pnl_usd) FROM fifo_allocations WHERE allocation_version=2;
```

## Rollback (If Needed)

```bash
psql -h localhost -U bot_user -d bot_trader_db -f database/migrations/001_rollback_fifo_allocations_tables.sql
# Type: YES (all caps)
```

## Full Documentation

- **AWS_DATABASE_MIGRATION.md** - Complete migration guide
- **AWS_RECONCILIATION_DEPLOY.md** - Reconciliation setup
- **RECONCILIATION_SETUP.md** - Monitoring and troubleshooting
- **database/migrations/README.md** - Migration details
- **FIFO_ALLOCATIONS_DESIGN.md** - Architecture

---

**Estimated Time:** 15-30 minutes (excluding compute time)
**Risk Level:** Low (non-destructive, fully reversible)
**Downtime Required:** None (but recommended to stop bot during first migration)
