# Trade-Strategy Linkage Integration - Deployment Guide

**Date**: December 29, 2025
**Branch**: `feature/strategy-optimization`
**Status**: ✅ Ready for Deployment

---

## Overview

Complete implementation of trade-strategy linkage system to enable parameter optimization. This deployment introduces metadata tracking that correlates executed trades with the strategy parameters that generated them.

**Primary Goal**: Achieve >90% linkage rate between trades and strategy parameters.

---

## What's Included

### 1. Metadata Capture (Sighook)
- **Files Modified**:
  - `sighook/trading_strategy.py`
  - `sighook/order_manager.py`
- **Changes**:
  - Generate `snapshot_id` per bot run
  - Include `snapshot_id` and `score` in webhook payload
- **Risk**: 🟢 LOW - Only adds metadata fields, no logic changes

### 2. Metadata Caching (Webhook)
- **Files Modified**:
  - `webhook/webhook_manager.py`
  - `webhook/listener.py`
- **Changes**:
  - Extract metadata from webhook requests
  - Cache in `shared_data_manager.market_data['strategy_metadata_cache']`
- **Risk**: 🟢 LOW - Caching only, no trading impact

### 3. Linkage Recording (Trade Recorder)
- **Files Modified**:
  - `SharedDataManager/trade_recorder.py`
- **Changes**:
  - Call `create_strategy_link()` when trades fill
  - Retrieve cached metadata and store in `trade_strategy_link` table
- **Risk**: 🟢 LOW - Graceful degradation if metadata missing

### 4. Reporting Integration
- **Files Modified**:
  - `botreport/aws_daily_report.py`
- **Changes**:
  - Added `compute_strategy_linkage_stats()` function
  - Added `render_linkage_section_html()` for testing visibility
  - Integrated into main report flow
- **Risk**: 🟢 LOW - Read-only reporting, no trading impact

---

## Pre-Deployment Checklist

- [x] All code changes reviewed and tested locally
- [x] Database table `trade_strategy_link` exists in production
- [x] No breaking changes to existing trade recording flow
- [x] Graceful degradation if metadata missing (won't break trading)
- [x] Report frequency update documented
- [ ] Git changes committed and pushed to `feature/strategy-optimization`
- [ ] AWS deployment planned

---

## Deployment Steps

### Step 1: Commit Changes

```bash
# From local machine
cd /Users/Manny/Python_Projects/BotTrader

git status  # Verify files changed
git add sighook/trading_strategy.py \
        sighook/order_manager.py \
        webhook/webhook_manager.py \
        webhook/listener.py \
        SharedDataManager/trade_recorder.py \
        botreport/aws_daily_report.py

git commit -m "$(cat <<'EOF'
feat: Implement trade-strategy linkage integration

Complete end-to-end linkage system for parameter optimization:

- Sighook: Generate snapshot_id and include in webhook payload
- Webhook: Cache strategy metadata (score, trigger, snapshot_id)
- TradeRecorder: Create/update linkage records when trades fill
- Reporting: Add linkage stats section for testing validation

Changes:
- sighook/trading_strategy.py: Add snapshot_id generation
- sighook/order_manager.py: Pass metadata through webhook payload
- webhook/webhook_manager.py: Extract metadata from requests
- webhook/listener.py: Cache metadata for trade recording
- SharedDataManager/trade_recorder.py: Integrate linkage calls
- botreport/aws_daily_report.py: Add linkage analytics

Target: >90% linkage rate for optimization readiness

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"

git push origin feature/strategy-optimization
```

### Step 2: Deploy to AWS

```bash
# SSH to server
ssh bottrader-aws

# Navigate to bot directory
cd /opt/bot

# Pull latest changes
git fetch origin
git checkout feature/strategy-optimization
git pull origin feature/strategy-optimization

# Verify correct commit
git log --oneline -1

# Rebuild affected containers
docker compose -f docker-compose.aws.yml build sighook webhook

# Restart services
docker compose -f docker-compose.aws.yml up -d sighook webhook
```

### Step 3: Verify Deployment

```bash
# Check containers are running
docker ps | grep -E "sighook|webhook"

# Check startup logs (no errors)
docker logs sighook 2>&1 | tail -50
docker logs webhook 2>&1 | tail -50

# Verify snapshot_id generation in logs
docker logs sighook 2>&1 | grep -i "snapshot"
```

### Step 4: Update Report Frequency (Testing)

```bash
# Check current cron
crontab -l | grep report

# Update to 6-hour frequency
crontab -l | grep -v 'run_daily_report\|botreport' | \
  { cat; echo '0 */6 * * * /opt/bot/scripts/run_daily_report.sh'; } | crontab -

# Verify
crontab -l | grep report
```

**Expected**: `0 */6 * * * /opt/bot/scripts/run_daily_report.sh`

### Step 5: Manual Test Report

```bash
# Trigger immediate report to test linkage section
cd /opt/bot
python3 -m botreport --hours 6 --send
```

**Check your email** for:
- ✅ New "Strategy Linkage Status (Testing)" section
- ✅ Shows linkage rate (may be 0% initially - normal)
- ✅ No errors in report generation

---

## Verification Timeline

### Immediate (0-1 Hour)
- ✅ Containers running without errors
- ✅ Logs show snapshot_id being generated
- ✅ First report runs successfully (linkage rate may be 0%)

### Short-term (6-12 Hours)
- ✅ Linkage rate increases from 0% → 25% → 50%
- ✅ Sample trades appear in report with scores
- ✅ Missing trades list helps identify cache issues

### Target (24-48 Hours)
- ✅ Linkage rate reaches >90%
- ✅ All sighook-originated trades have metadata
- ✅ Only manual/reconciled trades missing linkage (expected)

---

## Monitoring Commands

### Check Linkage Rate (Database)

```bash
ssh bottrader-aws "docker exec db psql -U bot_user -d bot_trader_db -c \"
SELECT
    COUNT(*) AS total_trades,
    COUNT(tsl.order_id) AS linked_trades,
    ROUND(COUNT(tsl.order_id)::numeric / NULLIF(COUNT(*), 0) * 100, 1) AS linkage_pct
FROM trade_records tr
LEFT JOIN trade_strategy_link tsl ON tr.order_id = tsl.order_id
WHERE tr.order_time >= NOW() - INTERVAL '6 hours'
  AND tr.status IN ('filled', 'done');
\""
```

### Check Metadata Cache (Logs)

```bash
# Check for cache operations
ssh bottrader-aws "docker logs webhook 2>&1 | grep -i 'STRATEGY_CACHE' | tail -20"

# Check for linkage creation
ssh bottrader-aws "docker logs webhook 2>&1 | grep -i 'STRATEGY_LINK' | tail -20"
```

### Inspect Sample Linked Trade

```bash
ssh bottrader-aws "docker exec db psql -U bot_user -d bot_trader_db -c \"
SELECT
    tr.symbol,
    tr.side,
    tr.order_time,
    tsl.buy_score,
    tsl.sell_score,
    tsl.trigger_type,
    SUBSTRING(tsl.snapshot_id::text, 1, 8) AS snapshot_id
FROM trade_records tr
INNER JOIN trade_strategy_link tsl ON tr.order_id = tsl.order_id
ORDER BY tr.order_time DESC
LIMIT 5;
\""
```

---

## Troubleshooting

### Linkage Rate Stuck at 0%

**Symptoms**: No trades showing linkage after 6+ hours

**Checks**:
1. Verify snapshot_id is being generated:
   ```bash
   docker logs sighook | grep snapshot_id
   ```

2. Verify metadata reaching webhook:
   ```bash
   docker logs webhook | grep "score\|snapshot_id"
   ```

3. Check cache operations:
   ```bash
   docker logs webhook | grep STRATEGY_CACHE
   ```

**Fix**: If none of the above show output, rebuild containers and restart.

---

### Cache Misses (50% Linkage Rate)

**Symptoms**: Half of trades have linkage, half don't

**Likely Cause**: Race condition or cache expiring before trade fills

**Check**:
```bash
docker logs webhook | grep "No metadata cached"
```

**Fix**: Extend cache TTL or investigate order fill delays.

---

### Report Generation Errors

**Symptoms**: Report email shows errors in linkage section

**Check**:
```bash
docker logs report-job | grep -i error
```

**Fix**: Verify `trade_strategy_link` table exists in database.

---

## Rollback Plan

If critical issues arise:

### Option A: Revert Code (Full Rollback)

```bash
ssh bottrader-aws
cd /opt/bot
git checkout main
docker compose -f docker-compose.aws.yml build sighook webhook
docker compose -f docker-compose.aws.yml up -d sighook webhook
```

### Option B: Disable Linkage (Partial)

Linkage is non-critical. If it fails, trading continues normally.

Just revert report frequency:
```bash
crontab -l | grep -v 'report' | \
  { cat; echo '0 9 * * * /opt/bot/scripts/run_daily_report.sh'; } | crontab -
```

---

## Success Criteria (3-7 Days)

- [ ] **Linkage Rate >90%** for sighook-originated trades
- [ ] **No trading disruptions** (linkage is passive)
- [ ] **No performance degradation** (minimal overhead)
- [ ] **Meaningful metadata** in database (scores, triggers)

Once achieved:
1. Revert report frequency to daily
2. Merge to main branch
3. Begin optimization work (next session)

---

## Next Steps After Deployment

1. **Monitor first 24 hours** - Watch linkage rate climb
2. **Review 6-hour reports** - Identify any cache miss patterns
3. **Validate metadata quality** - Ensure scores look reasonable
4. **Plan optimization work** - Use linked data for parameter tuning

---

**Deployed By**: ___________________
**Date**: December 29, 2025
**Time**: ___________________
**Linkage Rate After 24h**: ___________________

