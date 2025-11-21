# Exchange Reconciliation - Setup Guide

## Overview

The exchange reconciliation system automatically detects missing trades by comparing your database against Coinbase exchange data. This prevents gaps in your trading history and ensures accurate FIFO PnL calculations.

---

## Components

### 1. **Reconciliation Script** (`scripts/reconcile_with_exchange.py`)
- **Tier 1 (Lightweight):** Checks unmatched sells only (~1-2 min)
- **Tier 2 (Weekly):** Count-based reconciliation (not yet implemented)
- **Tier 3 (Deep Audit):** Comprehensive order-by-order comparison (not yet implemented)

### 2. **Weekly Cron Job** (`scripts/weekly_reconciliation.sh`)
- Runs every Sunday at 2:00 AM
- Logs results to `.bottrader/logs/`
- Optionally alerts on missing trades

---

## Setup Instructions

### Step 1: Make Script Executable

```bash
cd /Users/Manny/Python_Projects/BotTrader
chmod +x scripts/weekly_reconciliation.sh
```

### Step 2: Test the Script Manually

```bash
# Dry run - check if script works
./scripts/weekly_reconciliation.sh
```

This will:
- Detect the latest FIFO version
- Run Tier 1 reconciliation
- Log results to `.bottrader/logs/reconciliation_YYYYMMDD_HHMMSS.log`
- Create symlink to latest log

### Step 3: Add to Crontab

**Option A: AWS Server Deployment (Production)**

For AWS server at `/opt/bot/`:

```bash
# SSH to AWS server
ssh your-server

# Edit crontab
crontab -e

# Add this line (every Sunday at 9:15 AM, after daily reports)
15 9 * * 0 /opt/bot/scripts/weekly_reconciliation_aws.sh
```

**Note:** The AWS script uses:
- Project directory: `/opt/bot`
- Log directory: `/opt/bot/logs`
- Scheduled at 9:15 AM (after existing 9:05 AM report and 9:10 AM validation)

**Option B: Local Development (Desktop)**

```bash
# Edit your crontab
crontab -e

# Add this line (every Sunday at 2:00 AM):
0 2 * * 0 /Users/Manny/Python_Projects/BotTrader/scripts/weekly_reconciliation.sh
```

**Option C: System-wide Cron (requires sudo)**

```bash
# Create cron file
sudo nano /etc/cron.d/bottrader-reconciliation

# Add this content:
0 2 * * 0 manny /Users/Manny/Python_Projects/BotTrader/scripts/weekly_reconciliation.sh
```

### Step 4: Verify Cron Setup

```bash
# List your cron jobs
crontab -l

# Check cron logs (macOS)
log show --predicate 'process == "cron"' --last 1h

# Check cron logs (Linux)
grep CRON /var/log/syslog
```

---

## Cron Schedule Options

```bash
# Every Sunday at 2:00 AM (recommended)
0 2 * * 0 /path/to/weekly_reconciliation.sh

# Every day at 3:00 AM (more frequent monitoring)
0 3 * * * /path/to/weekly_reconciliation.sh

# First day of month at 1:00 AM (monthly deep audit)
0 1 1 * * /path/to/weekly_reconciliation.sh

# Every Saturday at midnight
0 0 * * 6 /path/to/weekly_reconciliation.sh
```

**Cron Syntax:**
```
* * * * * command
│ │ │ │ │
│ │ │ │ └─── Day of week (0-7, 0 and 7 = Sunday)
│ │ │ └───── Month (1-12)
│ │ └─────── Day of month (1-31)
│ └───────── Hour (0-23)
└─────────── Minute (0-59)
```

---

## Monitoring & Logs

### View Latest Reconciliation Log

```bash
# Latest log (symlink)
cat .bottrader/logs/reconciliation_latest.log

# Or tail in real-time
tail -f .bottrader/logs/reconciliation_latest.log
```

### Check for Missing Trades

```bash
# Search recent logs for issues
grep "Missing BUY orders found" .bottrader/logs/reconciliation_*.log

# Count missing trades over time
grep "Missing BUY orders found" .bottrader/logs/reconciliation_*.log | awk '{print $NF}'
```

### Log Retention

- Logs are kept for **12 weeks (84 days)**
- Older logs are automatically deleted
- Adjust retention in `weekly_reconciliation.sh` (line with `mtime +84`)

---

## Manual Reconciliation

### Run Reconciliation Anytime

```bash
# Check latest version (read-only)
python -m scripts.reconcile_with_exchange --version 2 --tier 1

# With auto-backfill (caution!)
python -m scripts.reconcile_with_exchange --version 2 --tier 1 --auto-backfill
```

### After Finding Missing Trades

If missing trades are detected:

1. **Review the log** to understand what's missing
2. **Decide:** Manual backfill or auto-backfill?
   - **Manual:** Safer, review each order
   - **Auto:** Faster, but trust the reconciliation
3. **Recompute FIFO** with new version:
   ```bash
   python -m scripts.compute_allocations --version 3 --all-symbols
   ```
4. **Validate new version:**
   ```bash
   python -m scripts.validate_allocations --version 3
   ```

---

## Alerting (Optional)

### Email Alerts

To receive email alerts when missing trades are detected, uncomment the alert lines in `weekly_reconciliation.sh`:

```bash
# Uncomment these lines (around line 66-68):
echo "Sending alert..." | tee -a "${LOG_FILE}"
python -m scripts.send_alert --type reconciliation --log "${LOG_FILE}"
```

Then create `scripts/send_alert.py`:

```python
#!/usr/bin/env python3
"""Send alerts for reconciliation issues."""
import sys
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

def send_email(subject, body):
    """Send email alert."""
    # Configure your SMTP settings
    sender = "bottrader@example.com"
    receiver = "your-email@example.com"
    password = "your-smtp-password"

    msg = MIMEMultipart()
    msg['From'] = sender
    msg['To'] = receiver
    msg['Subject'] = subject

    msg.attach(MIMEText(body, 'plain'))

    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(sender, password)
        server.send_message(msg)
        server.quit()
        print("✅ Alert sent successfully")
    except Exception as e:
        print(f"❌ Failed to send alert: {e}")

if __name__ == "__main__":
    send_email(
        subject="BotTrader: Missing Trades Detected",
        body="Check reconciliation log for details"
    )
```

### Slack Alerts

Or integrate with Slack webhook:

```python
import requests

def send_slack_alert(message):
    webhook_url = "https://hooks.slack.com/services/YOUR/WEBHOOK/URL"
    requests.post(webhook_url, json={"text": message})
```

---

## Troubleshooting

### Cron Job Not Running

**Check if cron service is running (Linux):**
```bash
sudo systemctl status cron
sudo systemctl start cron
```

**Check if cron service is running (macOS):**
```bash
sudo launchctl list | grep cron
```

**Test script manually:**
```bash
cd /Users/Manny/Python_Projects/BotTrader
./scripts/weekly_reconciliation.sh
```

**Check cron environment:**
```bash
# Add to crontab for debugging:
* * * * * env > /tmp/cron-env.txt
```

### Missing Python Packages

If cron fails due to missing packages:

```bash
# Option 1: Use absolute path to Python
/Users/Manny/Python_Projects/BotTrader/venv/bin/python -m scripts.reconcile_with_exchange ...

# Option 2: Ensure virtual environment is activated in script
source /Users/Manny/Python_Projects/BotTrader/venv/bin/activate
```

### Database Connection Issues

Ensure `.env` file is accessible:

```bash
# Test database connection
python -c "from Config.config_manager import CentralConfig; print(CentralConfig(is_docker=False).database_url)"
```

---

## Best Practices

1. **Monitor Weekly:** Check logs every Monday morning
2. **Act Fast:** If missing trades detected, backfill within 7 days (while exchange still has data)
3. **Version Control:** Increment FIFO version after backfilling
4. **Test Changes:** Run manual reconciliation after any database schema changes
5. **Keep Logs:** Don't reduce retention below 12 weeks (matches exchange API data retention)

---

## Future Enhancements

- [ ] Implement Tier 2 (count-based reconciliation)
- [ ] Implement Tier 3 (deep audit)
- [ ] Add email/Slack alerting
- [ ] Dashboard for reconciliation history
- [ ] Automatic backfill with approval workflow
- [ ] Integration with daily reports

---

## AWS Server Deployment

### Deployment Steps

1. **Upload AWS script to server:**
   ```bash
   # From local machine
   scp scripts/weekly_reconciliation_aws.sh your-server:/opt/bot/scripts/
   ```

2. **SSH to server and set permissions:**
   ```bash
   ssh your-server
   cd /opt/bot
   chmod +x scripts/weekly_reconciliation_aws.sh
   ```

3. **Test the script manually:**
   ```bash
   cd /opt/bot
   ./scripts/weekly_reconciliation_aws.sh
   ```

4. **Verify log output:**
   ```bash
   # Check latest log
   cat /opt/bot/logs/reconciliation_latest.log

   # List all reconciliation logs
   ls -lh /opt/bot/logs/reconciliation_*.log
   ```

5. **Add to crontab:**
   ```bash
   crontab -e
   # Add: 15 9 * * 0 /opt/bot/scripts/weekly_reconciliation_aws.sh
   ```

6. **Verify cron entry:**
   ```bash
   crontab -l | grep reconciliation
   ```

### Existing AWS Cron Schedule

The AWS server already has these cron jobs:
```
5 9 * * * /opt/bot/run_report_once.sh
10 9 * * * /opt/bot/scripts/daily_validation.sh >> /opt/bot/logs/validation.log 2>&1
```

The weekly reconciliation runs at **9:15 AM on Sundays** to follow this pattern and avoid conflicts.

### Monitoring AWS Logs

```bash
# View latest reconciliation
cat /opt/bot/logs/reconciliation_latest.log

# Tail in real-time during test
tail -f /opt/bot/logs/reconciliation_latest.log

# Search for issues
grep "Missing BUY orders found" /opt/bot/logs/reconciliation_*.log

# Check recent logs
ls -lt /opt/bot/logs/reconciliation_*.log | head -5
```

---

## Support

If you encounter issues:

**Local Development:**
1. Check latest log: `.bottrader/logs/reconciliation_latest.log`
2. Run manual reconciliation to debug
3. Verify cron schedule: `crontab -l`
4. Test script execution: `./scripts/weekly_reconciliation.sh`

**AWS Server:**
1. Check latest log: `/opt/bot/logs/reconciliation_latest.log`
2. SSH to server and run: `cd /opt/bot && ./scripts/weekly_reconciliation_aws.sh`
3. Verify cron schedule: `crontab -l`
4. Check server environment variables and virtual environment paths
