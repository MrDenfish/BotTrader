# BotTrader Troubleshooting & Fixes Session
**Started:** 2025-12-21 19:06 PT (2025-12-22 03:06 UTC)

## Session Overview
Comprehensive troubleshooting session to resolve multiple issues preventing the BotTrader from making trades and sending email reports.

## Goals
- [x] Investigate why no trades were made for 6+ days
- [x] Fix the ROC sell threshold bug causing constant sell signals
- [x] Fix the daily email report not being sent
- [x] Deploy all fixes to AWS production

---

## Issues Identified & Fixed

### 1. ROC Sell Threshold Bug (Critical - No Trades)

**Symptom:** Bot hadn't made any trades since December 15 (6+ days)

**Root Cause:** In `sighook/signal_manager.py`, the ROC sell threshold was using a positive value directly instead of negating it:
```python
# BEFORE (broken):
self.roc_sell_threshold = Decimal(str(self.config.roc_sell_24h or -2.0))
# With ROC_SELL_24H=1, this set threshold to +1.0
# Condition `roc < 1.0` was almost always true, triggering constant SELL signals
```

**Fix Applied:**
```python
# AFTER (fixed):
self.roc_sell_threshold = -Decimal(str(self.config.roc_sell_24h or 2.0))
# Now correctly negates to -1.0, so `roc < -1.0` only triggers on actual downtrends
```

**File:** `sighook/signal_manager.py:39`
**Commit:** `bc3b532`

---

### 2. Cron Jobs Failing - Missing .env_runtime

**Symptom:** Email reports and leaderboard jobs not running

**Root Cause:** Root crontab referenced `.env_runtime` which no longer exists:
```bash
# BEFORE (broken):
--env-file /opt/bot/.env_runtime  # File doesn't exist!
```

**Diagnosis:** Checked `/opt/bot/logs/bot_report.log`:
```
couldn't find env file: /opt/bot/.env_runtime
couldn't find env file: /opt/bot/.env_runtime
... (repeated for every cron execution)
```

**Fix Applied:** Updated root crontab to use `.env`:
```bash
ssh bottrader-aws 'sudo crontab -l -u root | sed "s/.env_runtime/.env/g" | sudo crontab -u root -'
```

**Cron Schedule (all working now):**
| Job | Schedule (PT) |
|-----|--------------|
| Email Report | 02:05, 08:05, 14:05, 20:05 |
| Leaderboard | Every 6 hours at :05 |
| FIFO Incremental | Every 5 minutes |
| FIFO Full | 2:00 AM daily |

---

### 3. CSV Reports Not Persisted

**Symptom:** No new CSV report files since September 2025

**Root Cause:** `save_report_copy()` was saving to `/tmp` inside the container instead of the mounted volume:
```python
# BEFORE (broken):
def save_report_copy(csv_bytes: bytes, out_dir="/tmp"):  # was "/app/logs"
```

**Fix Applied:**
```python
# AFTER (fixed):
def save_report_copy(csv_bytes: bytes, out_dir="/app/logs"):
```

**File:** `botreport/aws_daily_report.py:2132`
**Commit:** `4bb24a0`

---

### 4. Webhook Container Health Check (Transient)

**Symptom:** Webhook container showing "unhealthy", blocking sighook startup

**Root Cause:** Health endpoint was intermittently returning HTTP 503

**Resolution:** Health check recovered on its own; container became healthy after investigation

---

### 5. Dynamic Symbol Filter - Wrong Attribute Name

**Symptom:** Error in both webhook and sighook logs:
```
AttributeError: 'SharedDataManager' object has no attribute 'db_session_manager'
```

**Root Cause:** `dynamic_symbol_filter.py` was using incorrect attribute name and method:
```python
# BEFORE (broken):
async with self.shared_data_manager.db_session_manager.session() as session:
```

**Fix Applied:**
```python
# AFTER (fixed):
async with self.shared_data_manager.database_session_manager.async_session() as session:
```

**File:** `Shared_Utils/dynamic_symbol_filter.py:190` (2 occurrences)
**Commit:** `db152ee`

---

## Deployment Steps Performed

1. **Local commits:**
   ```bash
   git commit -m "fix: Negate ROC sell threshold..."  # bc3b532
   git commit -m "fix: Restore CSV report save path..."  # 4bb24a0
   git push origin main
   ```

2. **AWS deployment:**
   ```bash
   ssh bottrader-aws 'cd /opt/bot && git pull'
   ssh bottrader-aws 'cd /opt/bot && docker compose -f docker-compose.aws.yml build sighook --quiet'
   ssh bottrader-aws 'cd /opt/bot && docker compose -f docker-compose.aws.yml up -d sighook'
   ssh bottrader-aws 'cd /opt/bot && docker compose -f docker-compose.aws.yml build report-job'
   ```

3. **Crontab fix:**
   ```bash
   ssh bottrader-aws 'sudo crontab -l -u root | sed "s/.env_runtime/.env/g" | sudo crontab -u root -'
   ```

---

## Verification

### Tests Performed:
- [x] Sighook container running and healthy
- [x] Webhook container healthy
- [x] Report job completes with exit code 0
- [x] Email successfully received
- [x] CSV file created: `trading_report_2025-12-22_030637_UTC.csv`
- [x] Crontab updated and verified

### Container Status (Final):
```
NAME      STATUS
db        Up (healthy)
webhook   Up (healthy)
sighook   Up (healthy)
```

---

## Files Modified

| File | Change |
|------|--------|
| `sighook/signal_manager.py` | Negated ROC sell threshold |
| `botreport/aws_daily_report.py` | Fixed CSV save path to /app/logs |
| `Shared_Utils/dynamic_symbol_filter.py` | Fixed db session manager attribute name |
| Root crontab (AWS) | Changed .env_runtime to .env |

---

## Rollback Instructions

If issues persist, rollback with:
```bash
# Revert commits
git revert 4bb24a0 bc3b532

# Or reset to previous state
git reset --hard 9faf376

# Redeploy
ssh bottrader-aws 'cd /opt/bot && git pull && docker compose -f docker-compose.aws.yml build sighook report-job && docker compose -f docker-compose.aws.yml up -d sighook'
```

---

## Progress Log

- **19:06 PT** - Session started, investigating no-trade issue
- **19:15 PT** - Identified ROC sell threshold bug in signal_manager.py
- **19:20 PT** - Fixed and committed ROC threshold fix
- **19:25 PT** - Deployed ROC fix to AWS, sighook restarted
- **19:35 PT** - Discovered cron jobs failing due to missing .env_runtime
- **19:40 PT** - Updated root crontab to use .env
- **19:45 PT** - Found CSV save path bug (/tmp instead of /app/logs)
- **19:50 PT** - Fixed and deployed CSV path fix
- **19:55 PT** - Verified email received, CSV created
- **20:00 PT** - Session documented, all fixes verified
- **20:20 PT** - Found dynamic_symbol_filter db_session_manager error
- **20:22 PT** - Fixed attribute name, deployed to AWS, verified no errors

---

## Notes for Future Reference

1. **ROC Threshold Config:** `ROC_SELL_24H` in .env is stored as a positive number (e.g., `1`). The code must negate it for the sell threshold comparison.

2. **Cron Environment:** All cron jobs should use `--env-file /opt/bot/.env` (not `.env_runtime`)

3. **Docker Volume Mounts:** Report CSV files save to `/app/logs` which maps to `/opt/bot/logs` on the host

4. **Health Checks:** If webhook shows unhealthy, check `/health` endpoint manually:
   ```bash
   ssh bottrader-aws 'docker exec webhook curl -s http://127.0.0.1:5003/health'
   ```
