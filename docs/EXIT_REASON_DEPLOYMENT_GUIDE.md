# Exit Reason Tracking - Deployment Guide

**Date**: 2025-12-01
**Purpose**: Track which exit mechanism triggered each sell order for performance analysis
**Branch**: feature/smart-limit-exits

---

## Overview

This feature adds `exit_reason` tracking to the trade_records table, enabling you to verify which exit path (SOFT_STOP, HARD_STOP, SIGNAL_EXIT, TRAILING_STOP, TAKE_PROFIT, MANUAL) triggered each sell order.

### Files Changed

1. **TableModels/trade_record.py** - Added `exit_reason` column to TradeRecord model
2. **migrations/add_exit_reason_column.sql** - Database migration script
3. **MarketDataManager/position_monitor.py** - Updated to pass exit_reason through trigger
4. **webhook/websocket_market_manager.py** - Extracts exit_reason from trigger and passes to trade_data
5. **SharedDataManager/trade_recorder.py** - Writes exit_reason to database
6. **Config/validators.py** - Fixed typo (`__main__` → `__name__`)

---

## Deployment Steps

### Step 1: Backup Database (CRITICAL)

```bash
# SSH to AWS
ssh bottrader-aws

# Backup trade_records table
docker exec -it postgres pg_dump -U bottrader -t trade_records bottrader > /tmp/trade_records_backup_$(date +%Y%m%d_%H%M%S).sql

# Verify backup
ls -lh /tmp/trade_records_backup_*.sql
```

### Step 2: Run Database Migration

```bash
# Copy migration script to server
scp migrations/add_exit_reason_column.sql bottrader-aws:/tmp/

# SSH to AWS
ssh bottrader-aws

# Run migration
docker exec -i postgres psql -U bottrader -d bottrader < /tmp/add_exit_reason_column.sql

# Verify column was added
docker exec -it postgres psql -U bottrader -d bottrader -c "\\d trade_records"

# Should see:
#  exit_reason | character varying(50) |
```

### Step 3: Deploy Code Changes

```bash
# On local machine - commit and push changes
git add .
git commit -m "feat: Add exit_reason tracking to trade_records

- Add exit_reason column to TradeRecord model
- Update position_monitor to pass exit_reason via trigger
- Extract and store exit_reason in websocket_market_manager
- Write exit_reason to database in trade_recorder
- Fix validators.py typo (__name__)

Enables tracking which exit mechanism (SOFT_STOP/HARD_STOP/SIGNAL_EXIT/TRAILING_STOP/TAKE_PROFIT/MANUAL) triggered each sell order."

git push origin feature/smart-limit-exits

# On AWS server
ssh bottrader-aws
cd /home/ubuntu/BotTrader
git pull origin feature/smart-limit-exits

# Restart containers
docker-compose restart webhook sighook
```

### Step 4: Verify Deployment

```bash
# Check logs for successful startup
docker logs -f webhook --tail 100

# Should see:
# ✅ TradeRecorder worker started
# ✅ Position monitor initialized
# No errors about unknown column 'exit_reason'
```

---

## Testing

### Test 1: Verify Schema

```sql
-- Check exit_reason column exists
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_name = 'trade_records' AND column_name = 'exit_reason';

-- Expected output:
-- exit_reason | character varying | YES
```

### Test 2: Monitor New Sells

```sql
-- Watch for new sells with exit_reason populated
SELECT
    order_time,
    symbol,
    side,
    pnl_usd,
    exit_reason,
    trigger->>'trigger' as trigger_type,
    trigger->>'trigger_note' as trigger_note
FROM trade_records
WHERE side = 'sell'
  AND order_time >= NOW() - INTERVAL '1 hour'
ORDER BY order_time DESC
LIMIT 20;
```

### Test 3: Exit Reason Distribution

After 24 hours of operation:

```sql
-- Count exits by reason
SELECT
    exit_reason,
    COUNT(*) as count,
    ROUND(AVG(pnl_usd), 2) as avg_pnl,
    ROUND(SUM(pnl_usd), 2) as total_pnl
FROM trade_records
WHERE side = 'sell'
  AND order_time >= NOW() - INTERVAL '24 hours'
  AND exit_reason IS NOT NULL
GROUP BY exit_reason
ORDER BY count DESC;

-- Expected output (example):
-- exit_reason   | count | avg_pnl | total_pnl
-- SOFT_STOP     |    15 |   -0.85 |   -12.75
-- SIGNAL_EXIT   |    23 |    0.45 |    10.35
-- TRAILING_STOP |     8 |    1.20 |     9.60
-- HARD_STOP     |     2 |   -1.50 |    -3.00
```

---

## Exit Reason Values

| Code | Description | Expected Trigger |
|------|-------------|------------------|
| `SOFT_STOP` | Soft stop loss (-2.5%) | position_monitor detects P&L <= -2.5% |
| `HARD_STOP` | Emergency hard stop (-5%) | position_monitor detects P&L <= -5% |
| `SIGNAL_EXIT` | buy_sell_matrix SELL signal | Phase 5 matrix indicates SELL and P&L >= 0% |
| `TRAILING_STOP` | ATR-based trailing stop triggered | Price drops from peak by 2×ATR |
| `TAKE_PROFIT` | Take profit target hit | position_monitor (currently unused) |
| `MANUAL` | Manual exit or unknown | Fallback for unrecognized exit reasons |
| `NULL` | Buy order or pre-feature exit | All buy orders, or sells before feature deployed |

---

## Analysis Queries

### Query 1: Win Rate by Exit Reason

```sql
SELECT
    exit_reason,
    COUNT(*) as total_exits,
    SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END) as wins,
    SUM(CASE WHEN pnl_usd <= 0 THEN 1 ELSE 0 END) as losses,
    ROUND(100.0 * SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) as win_rate_pct,
    ROUND(AVG(CASE WHEN pnl_usd > 0 THEN pnl_usd END), 2) as avg_win,
    ROUND(AVG(CASE WHEN pnl_usd <= 0 THEN pnl_usd END), 2) as avg_loss
FROM trade_records
WHERE side = 'sell'
  AND exit_reason IS NOT NULL
  AND order_time >= '2025-11-30'  -- Post-Phase-5
GROUP BY exit_reason
ORDER BY total_exits DESC;
```

### Query 2: Verify Soft Stop Working

```sql
-- Check if soft stops are preventing large losses
SELECT
    symbol,
    order_time,
    pnl_usd,
    exit_reason
FROM trade_records
WHERE side = 'sell'
  AND exit_reason = 'SOFT_STOP'
  AND pnl_usd < -1.00  -- Should be rare if -2.5% stop is working
  AND order_time >= '2025-11-30'
ORDER BY pnl_usd ASC
LIMIT 20;

-- If many results, soft stop isn't firing fast enough
```

### Query 3: Post-Phase-5 R:R Ratio

```sql
-- Calculate actual R:R ratio after Phase 5
WITH post_phase5 AS (
    SELECT
        CASE WHEN pnl_usd > 0 THEN pnl_usd ELSE 0 END as win_amt,
        CASE WHEN pnl_usd <= 0 THEN ABS(pnl_usd) ELSE 0 END as loss_amt
    FROM trade_records
    WHERE side = 'sell'
      AND order_time >= '2025-11-30'
      AND pnl_usd IS NOT NULL
)
SELECT
    ROUND(AVG(CASE WHEN win_amt > 0 THEN win_amt END), 2) as avg_win,
    ROUND(AVG(CASE WHEN loss_amt > 0 THEN loss_amt END), 2) as avg_loss,
    ROUND(
        AVG(CASE WHEN win_amt > 0 THEN win_amt END) /
        NULLIF(AVG(CASE WHEN loss_amt > 0 THEN loss_amt END), 0),
        2
    ) as risk_reward_ratio
FROM post_phase5;

-- Expected: R:R ratio closer to 2.5:1 than pre-Phase-5's 1.06:1
```

---

## Rollback Plan

If issues arise:

```bash
# Step 1: Stop bot
ssh bottrader-aws
cd /home/ubuntu/BotTrader
docker-compose stop webhook sighook

# Step 2: Rollback code
git checkout main
docker-compose restart webhook sighook

# Step 3: (Optional) Remove column if needed
docker exec -it postgres psql -U bottrader -d bottrader -c "ALTER TABLE trade_records DROP COLUMN exit_reason;"

# Step 4: Restore backup if data corruption
docker exec -i postgres psql -U bottrader -d bottrader < /tmp/trade_records_backup_YYYYMMDD_HHMMSS.sql
```

---

## Success Criteria

✅ **Deployment Successful** if:
1. Migration runs without errors
2. Containers restart cleanly
3. New sell orders populate `exit_reason` field
4. No errors in logs about unknown column

✅ **Feature Working** if (after 24 hours):
1. At least 80% of new sells have `exit_reason` populated
2. Exit reasons match expected triggers (e.g., SOFT_STOP at -2.5%)
3. Distribution shows SIGNAL_EXIT and SOFT_STOP as primary reasons
4. Hard stops are rare (< 5% of exits)

---

## Next Steps

After successful deployment and 7 days of data collection:

1. **Analyze Performance**:
   - Run Query 1 (Win Rate by Exit Reason)
   - Run Query 3 (Post-Phase-5 R:R Ratio)
   - Compare to pre-Phase-5 baseline

2. **Optimize Exit Strategy**:
   - If SOFT_STOP win rate is low → consider tightening stop
   - If SIGNAL_EXIT avg P&L is high → trust signals more
   - If TRAILING_STOP captures good wins → increase usage

3. **Investigate BCH Example**:
   - Check if BCH exit from your example has exit_reason populated
   - Verify reason matches expected behavior
   - If unexpected, investigate position_monitor logic

---

**Questions or Issues?**
Check logs: `docker logs webhook -f | grep -E "(exit_reason|position_monitor|trade_recorder)"`
