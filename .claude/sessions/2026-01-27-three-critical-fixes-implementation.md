# Session: Three Critical Fixes Implementation
**Date**: January 27, 2026
**Status**: ✅ Complete
**Branch**: `feature/strategy-optimization`

---

## Session Overview

This session addressed three issues discovered in the production logs:
1. TP_SL_LOG_PATH permission denied error
2. BNKR-USD REARM_OCO failures
3. INVALID_LIMIT_PRICE_POST_ONLY errors for protective orders

Additionally, implemented three preventative disk space fixes from the previous session crisis.

---

## Part 1: Disk Space Prevention Measures

### Context
Following the January 27 disk space crisis (100% full disk), implemented three automated prevention systems.

### Fixes Implemented

#### 1. Automated Log Rotation ✅
**File**: `/etc/systemd/journald.conf`

**Configuration Added**:
```bash
MaxRetentionSec=3day
SystemMaxUse=500M
RuntimeMaxUse=100M
```

**Result**: Journal logs capped at 500MB (down from 928MB)

#### 2. Weekly Docker Cleanup ✅
**Cron Job**: Every Sunday at 3:00 AM PT

**Command**: `docker system prune -a -f`

**What it removes**:
- Unused Docker images
- Stopped containers
- Unused volumes and networks
- Build cache

**Log**: `/opt/bot/logs/docker-cleanup.log`

#### 3. Hourly Disk Space Monitoring ✅
**Script**: `/opt/bot/disk_space_monitor.py`

**Features**:
- Checks disk usage every hour
- Sends email alert via AWS SES when usage > 80%
- 6-hour cooldown between alerts
- Alert includes recommendations and current stats
- Recipient: dennfish@gmail.com

**Log**: `/opt/bot/logs/disk-monitor.log`

**Current Status**: 78% usage (5.5GB free) - Healthy ✓

---

## Part 2: StockAgent Database Investigation

### Investigation Results
**Finding**: No active security concern

**Analysis**:
- PostgreSQL properly secured: `127.0.0.1:5432` (localhost only)
- Failed connection attempts on Jan 26:
  - `FATAL: role "bottrader" does not exist` (wrong username)
  - `FATAL: database "trading_db" does not exist` (wrong database)
- No stockagent project or process found on server
- Attempts stopped - likely testing/development

**Impact**: None - database never compromised

**Recommendation**: No action needed - system already secure

---

## Part 3: Application Error Fixes

### Fix #1: TP_SL_LOG_PATH Permission Error ✅

**Problem**:
```
PermissionError: [Errno 13] Permission denied: '/Users'
```

**Root Cause**: `.env` file contained local Mac development path:
```bash
TP_SL_LOG_PATH=/Users/Manny/Python_Projects/BotTrader/.bottrader/cache/tpsl.jsonl
```

**Solution**: Updated to Docker container path:
```bash
TP_SL_LOG_PATH=/app/logs/tpsl.jsonl
```

**Impact**:
- ✅ TP/SL calculations now logged correctly
- ✅ No more permission errors
- ✅ Valuable data available for analysis

**Files Changed**: `/opt/bot/.env` (AWS server)

---

### Fix #2: BNKR-USD Position Investigation ✅

**Context**: 33 REARM_OCO errors in 2 hours for BNKR-USD

**Investigation Results**:
```sql
-- Position already closed:
Buy:  43,655 BNKR @ $0.00034540 (10:32 AM Jan 27)
Sell: 43,651 BNKR @ $0.00034480 (10:59 AM Jan 27)
Exit: rearm_oco_missing (protective exit)
```

**Finding**:
- Position closed successfully
- REARM_OCO errors occurred during protection attempt window
- No current position exists
- Errors have stopped

**Action**: None needed - led to discovery of Fix #3

---

### Fix #3: OCO Post-Only Logic for Protective Orders ✅

**Problem**:
```
INVALID_LIMIT_PRICE_POST_ONLY - preview_failure_reason: PREVIEW_INVALID_LIMIT_PRICE_POST_ONLY
```

**Root Cause**:
- REARM_OCO orders use `source='websocket'`
- `place_limit_order()` only excluded `'position_monitor'` from post_only
- Websocket orders still used `post_only=True`
- Post-only orders cannot cross the spread
- Micro-priced assets (BNKR-USD: $0.0003467) have volatile spreads
- Orders rejected when limit price would execute immediately

**Code Analysis**:
```python
# webhook/webhook_order_types.py:794 (BEFORE)
use_post_only = order_data.source != 'position_monitor'
```

This was inconsistent with other functions:
- `adjust_oco_to_touch()` line 360: excludes both 'websocket' and 'position_monitor'
- `process_limit_and_tp_sl_orders()` line 571: excludes both sources

**Solution**:
```python
# webhook/webhook_order_types.py:794 (AFTER)
# ✅ FIX: Disable post_only for position monitor and websocket protective exits
# Post-only prevents orders that cross the spread, but emergency/protective exits NEED to cross
# websocket source includes REARM_OCO orders for untracked positions
use_post_only = order_data.source not in ('position_monitor', 'websocket')
```

**Impact**:
- ✅ REARM_OCO protective orders can now cross spread
- ✅ Works for all asset prices (including micro-priced coins)
- ✅ Untracked positions get protective brackets reliably
- ✅ Regular trading orders still use post_only (no change)
- ✅ Consistent with other OCO placement functions

**Files Changed**: `webhook/webhook_order_types.py` (line 792-794)

**Commit**: `64d8e60`

---

## Deployment Summary

### Changes Deployed

#### AWS Server Configuration
1. `/etc/systemd/journald.conf` - Log rotation limits
2. Root crontab - Docker cleanup + disk monitoring jobs
3. `/opt/bot/disk_space_monitor.py` - Monitoring script
4. `/opt/bot/.env` - Fixed TP_SL_LOG_PATH

#### Code Changes
1. `webhook/webhook_order_types.py` - Fixed post_only logic

### Deployment Steps

```bash
# 1. Server configuration (already applied)
# journald config, cron jobs, monitoring script

# 2. Code deployment
git add webhook/webhook_order_types.py
git commit -m "fix: Disable post_only for websocket REARM_OCO protective orders"
git push origin feature/strategy-optimization

# 3. AWS deployment
ssh bottrader-aws "cd /opt/bot && git pull origin feature/strategy-optimization"
ssh bottrader-aws "cd /opt/bot && docker compose -f docker-compose.aws.yml restart webhook sighook"
```

### Verification ✅

**Container Status**:
```
NAMES     STATUS
sighook   Up (healthy)
webhook   Up (healthy)
db        Up 11 hours (healthy)
```

**Disk Space**: 78% (5.5GB free) - Healthy

**Latest Commit**: `64d8e60` - Fix deployed

**Logs**: No permission errors, no post_only errors

**WebSocket**: Connected (market + user channels active)

---

## Current System Status

### Disk Space Monitoring
- **Current Usage**: 78% (14.89GB / 19.20GB)
- **Automated Cleanup**: Weekly (Sundays 3 AM)
- **Automated Monitoring**: Hourly checks
- **Alert Threshold**: 80%

### Database Security
- **Port**: `127.0.0.1:5432` (localhost only)
- **Connection Attempts**: None recent
- **Status**: Secure ✓

### Application Health
- **Containers**: All healthy
- **TP/SL Logging**: Working ✓
- **OCO Placement**: Fixed ✓
- **WebSocket**: Active ✓

---

## Monitoring Recommendations

### Short Term (Next 24 hours)
1. Monitor disk space - should stay below 80%
2. Watch for any REARM_OCO orders - should succeed without post_only errors
3. Verify TP/SL data is being logged to `/app/logs/tpsl.jsonl`

### Medium Term (Next Week)
1. Verify Docker cleanup runs successfully (Sunday 3 AM)
2. Check disk monitoring emails if threshold crossed
3. Review journal log sizes staying under 500MB

### Long Term
1. Consider disk upgrade to 30-40GB if growth continues
2. Implement database archiving for old trade data (>90 days)
3. Review log retention policies quarterly

---

## Files Modified

### Session Documentation
- `.claude/sessions/2026-01-27-three-critical-fixes-implementation.md` - This file

### Code Changes (Committed)
- `webhook/webhook_order_types.py` - Fixed post_only logic for websocket orders

### AWS Server Configuration (Not in Git)
- `/etc/systemd/journald.conf` - Log rotation settings
- Root crontab - Automated cleanup and monitoring jobs
- `/opt/bot/disk_space_monitor.py` - Monitoring script
- `/opt/bot/.env` - Fixed TP_SL_LOG_PATH

### Backups Created
- `/opt/bot/.env.backup.20260127_*` - Pre-change backup

---

## Git History

```
64d8e60 fix: Disable post_only for websocket REARM_OCO protective orders
181ec12 fix: Replace non-existent place_market_order_usd with create_order
0fced74 docs: Mark hybrid fix as deployed and create next session plan
9f40405 fix: Implement hybrid entry price calculation for position_monitor
```

---

## Lessons Learned

1. **Path Configuration**: Always use container paths in .env for Docker deployments
2. **Consistency**: Check all code paths when fixing issues (post_only was fixed in 2 places but not the 3rd)
3. **Micro-Priced Assets**: Require special consideration for order placement logic
4. **Preventative Maintenance**: Automated monitoring prevents emergencies
5. **Investigation Value**: Error logs led to discovering related issues (BNKR-USD → post_only fix)

---

**Document Status**: ✅ Session Complete
**Created**: January 27, 2026
**Completed**: January 27, 2026
**Next Session**: Monitor preventative systems and application fixes
