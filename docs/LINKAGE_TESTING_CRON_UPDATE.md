# Strategy Linkage Testing - Cron Frequency Update

**Date**: December 29, 2025
**Purpose**: Increase report frequency to 6 hours during linkage testing phase
**Duration**: 3-7 days (until linkage rate >90% consistently)

---

## Current Setup

The daily report runs via cron on the AWS server.

**Current frequency**: Once per day
**Testing frequency**: Every 6 hours (4 reports/day)

---

## Update Commands

### 1. Check Current Cron Setup

```bash
ssh bottrader-aws "crontab -l | grep -i report"
```

**Expected output** (example):
```
0 9 * * * /opt/bot/scripts/run_daily_report.sh
```

---

### 2. Update to 6-Hour Frequency (Testing)

```bash
ssh bottrader-aws "crontab -l | grep -v 'run_daily_report\|botreport' | { cat; echo '0 */6 * * * /opt/bot/scripts/run_daily_report.sh'; } | crontab -"
```

**New schedule**: Runs at 00:00, 06:00, 12:00, 18:00 UTC daily

---

### 3. Verify Update

```bash
ssh bottrader-aws "crontab -l | grep report"
```

**Expected**:
```
0 */6 * * * /opt/bot/scripts/run_daily_report.sh
```

---

## Alternative: If Docker-Compose Manages Cron

If the report runs as a docker service with internal cron:

### Check docker-compose.aws.yml

Look for `report-job` service cron configuration.

### Update Dockerfile.report

If cron is defined in the Dockerfile, update the crontab entry there and rebuild:

```bash
ssh bottrader-aws "cd /opt/bot && docker compose -f docker-compose.aws.yml build report-job"
ssh bottrader-aws "cd /opt/bot && docker compose -f docker-compose.aws.yml up -d report-job"
```

---

## Revert to Daily After Testing (3-7 Days)

Once linkage rate is consistently >90%:

```bash
ssh bottrader-aws "crontab -l | grep -v 'run_daily_report\|botreport' | { cat; echo '0 9 * * * /opt/bot/scripts/run_daily_report.sh'; } | crontab -"
```

**Back to**: Daily at 09:00 UTC

---

## What to Monitor in Reports

### Success Indicators
- **Linkage Rate**: Should increase from 0% → 50% → 90% over first few reports
- **Missing Trades**: Should decrease to <10% of total
- **Sample Trades**: Should show valid buy_score, sell_score, snapshot_id values

### Failure Indicators
- **Linkage Rate stays at 0%**: Check sighook logs for snapshot_id generation
- **Cache misses**: Check webhook logs for metadata caching
- **No trades at all**: Unrelated to linkage (check trading system)

---

## Rollback Plan

If reports cause issues (unlikely - read-only operation):

```bash
# Disable report cron temporarily
ssh bottrader-aws "crontab -l | grep -v 'run_daily_report' | crontab -"

# Re-enable after investigation
ssh bottrader-aws "crontab -l | { cat; echo '0 9 * * * /opt/bot/scripts/run_daily_report.sh'; } | crontab -"
```

---

## Notes

- Report generation is **read-only** (no trading impact)
- 6-hour frequency provides 4 data points/day for faster debugging
- Each report queries the last 6 hours of trades (windowed by report lookback)
- Email frequency will increase to 4/day during testing (consider filtering)

---

**Status**: Ready for deployment
**Next Step**: Deploy linkage integration code, then update cron frequency
