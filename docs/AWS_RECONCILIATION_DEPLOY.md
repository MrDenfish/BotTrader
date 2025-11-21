# AWS Server - Reconciliation Deployment

Quick deployment guide for setting up weekly reconciliation on the AWS production server.

## Prerequisites

- SSH access to AWS server
- Server path: `/opt/bot`
- Existing cron jobs:
  - `5 9 * * *` - Daily report (`run_report_once.sh`)
  - `10 9 * * *` - Daily validation (`daily_validation.sh`)

---

## Deployment Steps

### 1. Upload Script to Server

From your local machine:

```bash
cd /Users/Manny/Python_Projects/BotTrader
scp scripts/weekly_reconciliation_aws.sh your-server:/opt/bot/scripts/
```

### 2. Set Permissions

SSH to the server:

```bash
ssh your-server
cd /opt/bot
chmod +x scripts/weekly_reconciliation_aws.sh
```

### 3. Test Script Manually

**IMPORTANT:** Test before adding to cron!

```bash
cd /opt/bot
./scripts/weekly_reconciliation_aws.sh
```

Expected output:
- ✅ Virtual environment activated
- ✅ Latest FIFO version detected
- ✅ Tier 1 reconciliation completed
- ✅ Log file created: `/opt/bot/logs/reconciliation_YYYYMMDD_HHMMSS.log`
- ✅ Symlink created: `/opt/bot/logs/reconciliation_latest.log`

### 4. Verify Log Output

```bash
# View full log
cat /opt/bot/logs/reconciliation_latest.log

# Check for missing trades
grep "Missing BUY orders found" /opt/bot/logs/reconciliation_latest.log
```

### 5. Add to Crontab

```bash
crontab -e
```

Add this line:
```
15 9 * * 0 /opt/bot/scripts/weekly_reconciliation_aws.sh
```

**Why 9:15 AM on Sundays?**
- Runs after daily report (9:05 AM)
- Runs after daily validation (9:10 AM)
- Weekly cadence catches gaps before exchange data expires (7-day retention)

### 6. Verify Cron Entry

```bash
crontab -l | grep reconciliation
```

Expected output:
```
15 9 * * 0 /opt/bot/scripts/weekly_reconciliation_aws.sh
```

---

## Monitoring

### View Latest Log

```bash
cat /opt/bot/logs/reconciliation_latest.log
```

### Check All Logs

```bash
ls -lth /opt/bot/logs/reconciliation_*.log
```

### Search for Issues

```bash
# Find missing trades in recent logs
grep "Missing BUY orders found" /opt/bot/logs/reconciliation_*.log

# Show only non-zero counts
grep "Missing BUY orders found" /opt/bot/logs/reconciliation_*.log | grep -v ": 0"
```

### Monitor During Test

```bash
tail -f /opt/bot/logs/reconciliation_latest.log
```

---

## Troubleshooting

### Script Fails to Find Python Modules

Check virtual environment:
```bash
cd /opt/bot
ls -la venv/  # or .venv/
```

If needed, update script to use absolute path:
```bash
/opt/bot/venv/bin/python -m scripts.reconcile_with_exchange ...
```

### Database Connection Issues

Verify `.env` file exists:
```bash
cat /opt/bot/.env | grep DATABASE_URL
```

Test database connection:
```bash
cd /opt/bot
source venv/bin/activate
python -c "from Config.config_manager import CentralConfig; print(CentralConfig(is_docker=False).database_url)"
```

### Cron Job Not Running

Check cron service (Linux):
```bash
sudo systemctl status cron
```

View cron logs:
```bash
grep CRON /var/log/syslog | tail -20
```

Test script manually to see errors:
```bash
cd /opt/bot
./scripts/weekly_reconciliation_aws.sh
```

### Missing Trades Detected

If reconciliation finds missing buys:

1. **Review the log:**
   ```bash
   cat /opt/bot/logs/reconciliation_latest.log
   ```

2. **Run manual backfill:**
   ```bash
   cd /opt/bot
   python -m scripts.reconcile_with_exchange --version 2 --tier 1 --auto-backfill
   ```

3. **Recompute FIFO:**
   ```bash
   python -m scripts.compute_allocations --version 3 --all-symbols
   ```

4. **Validate new version:**
   ```bash
   python -m scripts.validate_allocations --version 3
   ```

---

## Log Retention

- Logs kept for **12 weeks (84 days)**
- Automatic cleanup on each run
- Adjust retention in script if needed (line 112: `mtime +84`)

---

## Key Differences from Desktop Version

| Feature | Desktop | AWS Server |
|---------|---------|------------|
| **Project Path** | `/Users/Manny/Python_Projects/BotTrader` | `/opt/bot` |
| **Log Directory** | `.bottrader/logs` | `/opt/bot/logs` |
| **Cron Schedule** | Sunday 2:00 AM | Sunday 9:15 AM |
| **Script** | `weekly_reconciliation.sh` | `weekly_reconciliation_aws.sh` |

---

## Quick Reference

```bash
# Deploy
scp scripts/weekly_reconciliation_aws.sh your-server:/opt/bot/scripts/

# Set permissions
ssh your-server "chmod +x /opt/bot/scripts/weekly_reconciliation_aws.sh"

# Test
ssh your-server "cd /opt/bot && ./scripts/weekly_reconciliation_aws.sh"

# Add to cron
ssh your-server "crontab -e"
# Add: 15 9 * * 0 /opt/bot/scripts/weekly_reconciliation_aws.sh

# Monitor
ssh your-server "cat /opt/bot/logs/reconciliation_latest.log"
```

---

## Next Steps After Deployment

1. ✅ Deploy script to AWS
2. ✅ Test manually
3. ✅ Add to crontab
4. ⏳ Wait for first automated run (next Sunday at 9:15 AM)
5. ⏳ Review logs on Monday morning
6. ⏳ After 4 weeks, review effectiveness and adjust if needed
