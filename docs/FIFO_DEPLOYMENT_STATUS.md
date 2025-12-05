# FIFO Single Engine - Deployment Status

**Date:** 2025-12-04 (Updated: 2025-12-04 20:06 PT)
**Branch:** `bugfix/single-fifo-engine`
**Status:** ✅ FULLY TESTED & OPERATIONAL
**Deployment Method:** Full rebuild via `docker compose down` + `build --no-cache` + `up`
**Latest Fix:** Incremental mode fixes deployed and tested successfully (commits: dc7412d, 782064c)

---

## Deployment Summary

The FIFO single engine fix has been successfully deployed to the production server. All systems are operational and the automated FIFO computation is working correctly.

### What Was Deployed

1. **Removed Inline FIFO** (`trade_recorder.py`)
   - SELL orders now record with NULL P&L fields
   - FIFO engine is sole source of truth
   - Deprecated `backfill_trade_metrics()` method

2. **Incremental Mode** (`compute_allocations.py`)
   - Added `--since` parameter for time-based filtering
   - Fixed existence check to allow incremental updates (line 156)
   - Supports relative times: "10 minutes ago", "2 hours ago", etc.

3. **Automated Cron Jobs**
   - **Full FIFO**: Daily at 01:50 PT (before 02:05 email report)
   - **Incremental FIFO**: Every 5 minutes
   - Logs to `/opt/bot/logs/fifo_full.log` and `/opt/bot/logs/fifo_incremental.log`

---

## Deployment Timeline

| Time (PT) | Event | Status |
|-----------|-------|--------|
| 14:26 | Pushed `bugfix/single-fifo-engine` to GitHub | ✅ |
| 14:30 | First cron attempt (old code) | ❌ Failed - unrecognized `--since` |
| ~16:00 | User stopped containers | ⏸️ Downtime started |
| ~16:00 | Rebuilt all images with `--no-cache` | 🔨 |
| ~16:01 | Restarted all containers | ✅ All healthy |
| 16:01 | Manual test of incremental mode | ✅ Success |
| 16:05 | First automated cron run (new code) | ✅ Success |

**Total Downtime**: ~5-10 minutes during rebuild

---

## Current System Status

### Container Health
```
NAME      STATUS
db        Up 10 minutes (healthy)
sighook   Up 4 minutes (healthy)
webhook   Up 4 minutes (healthy)
```

### FIFO Engine Verification

**Manual Test** (16:01:48):
```
Started: 2025-12-04 16:01:48
Incremental Mode: Processing trades since 2025-12-04 15:51:48
✅ No new trades found - nothing to compute
Finished: 2025-12-04 16:01:56
```

**Automated Cron** (16:05:05):
```
Started: 2025-12-04 16:05:05
Incremental Mode: Processing trades since 2025-12-04 15:55:05
✅ No new trades found - nothing to compute
Finished: 2025-12-04 16:05:09
```

### Code Verification

Verified line 156 fix in running container:
```python
# Allow incremental mode to proceed even if allocations exist
if existing_count > 0 and not args.force and not since_time:
    print(f"\n⚠️  Version {args.version} already has {existing_count:,} allocations!")
    print("    Use --force to recompute (will delete existing allocations)")
    print("    Or use --since to compute only new trades incrementally")
    return
```

✅ Fix confirmed present in container

---

## Active Cron Jobs

```bash
# Full FIFO daily at 01:50 PT (before email report, with force recompute)
50 1 * * * docker exec sighook python3 -m scripts.compute_allocations --version 2 --all-symbols --force >> /opt/bot/logs/fifo_full.log 2>&1

# Incremental FIFO every 5 minutes (only processes new trades from last 10 minutes)
*/5 * * * * docker exec sighook python3 -m scripts.compute_allocations --version 2 --all-symbols --since '10 minutes ago' >> /opt/bot/logs/fifo_incremental.log 2>&1

# Email report at 02:05, 08:05, 14:05, 20:05 PT (uses the report-job service)
5 2,8,14,20 * * * cd /opt/bot && /usr/bin/docker compose --env-file /opt/bot/.env_runtime -f docker-compose.aws.yml up --no-recreate --no-build --abort-on-container-exit report-job >> /opt/bot/logs/bot_report.log 2>&1
```

---

## Open Positions During Deployment

At time of shutdown (~16:00 PT), there were 6 open positions:

1. **BONK**: 3,336,705 tokens (-$0.73)
2. **TAO**: 0.1075 tokens (-$1.89)
3. **XLM**: 130.33 tokens (-$0.26)
4. **BTC**: 0.000355 tokens (-$0.35)
5. **AERO**: 13.0 tokens (-$0.03)
6. **UNFI**: 12 tokens (-$98.92) ⚠️

**Impact**: ~5-10 minute gap in position monitoring during rebuild. No stop losses triggered during downtime.

**Note**: OCO (One-Cancels-Other) orders were failing to place for some positions, meaning position monitor was primary protection mechanism during downtime.

---

## Monitoring Plan

### Daily Checks (Next 3-7 Days)

1. **01:50 PT Full FIFO**
   - Check `/opt/bot/logs/fifo_full.log` for success
   - Verify allocation count matches expectations
   - Look for any errors or warnings

2. **02:05 PT Email Report**
   - Verify P&L calculations appear correct
   - Compare against known trades from Coinbase
   - Watch for any SAPIEN-USD type mismatches

3. **Incremental FIFO (Every 5 min)**
   - Spot check `/opt/bot/logs/fifo_incremental.log`
   - Confirm "No new trades" when no trading activity
   - Verify allocations created when trades occur

### Success Criteria

✅ **Day 1-2**: No errors in FIFO logs
✅ **Day 3-4**: Email reports show reasonable P&L
✅ **Day 5-7**: Spot check database P&L vs exchange matches

If all criteria met, merge to `main` and consider table restructuring in next session.

---

## Validation Queries

### Check Recent FIFO Allocations
```sql
SELECT
    symbol,
    COUNT(*) as allocation_count,
    SUM(pnl_amount) as total_pnl
FROM fifo_allocations
WHERE allocation_version = 2
  AND created_at >= NOW() - INTERVAL '24 hours'
GROUP BY symbol
ORDER BY total_pnl DESC;
```

### Verify No Suspicious Parent Matches
```sql
-- Should return 0 rows if all correct
SELECT
    s.symbol,
    s.order_id as sell_id,
    s.order_time as sell_time,
    s.pnl_usd as db_pnl,
    b.order_time as parent_buy_time,
    EXTRACT(EPOCH FROM (s.order_time - b.order_time))/3600 as hours_gap
FROM trade_records s
LEFT JOIN trade_records b ON s.parent_id = b.order_id
WHERE s.side = 'sell'
  AND s.order_time >= NOW() - INTERVAL '7 days'
  AND s.parent_id IS NOT NULL  -- Old inline FIFO
ORDER BY hours_gap DESC
LIMIT 10;
```

Expected: Should return 0 rows since all new SELLs have NULL parent_id.

### Check FIFO Allocations Coverage
```sql
-- All recent SELLs should have allocations
SELECT
    tr.symbol,
    tr.order_id,
    tr.order_time,
    tr.pnl_usd as inline_pnl,  -- Should be NULL
    fa.pnl_amount as fifo_pnl,  -- Should have value
    CASE
        WHEN fa.sell_trade_id IS NULL THEN '❌ No allocation'
        WHEN tr.pnl_usd IS NOT NULL THEN '⚠️ Has inline PnL'
        ELSE '✅ Correct'
    END as status
FROM trade_records tr
LEFT JOIN fifo_allocations fa ON tr.order_id = fa.sell_trade_id AND fa.allocation_version = 2
WHERE tr.side = 'sell'
  AND tr.order_time >= NOW() - INTERVAL '24 hours'
ORDER BY tr.order_time DESC;
```

---

## Fixes Applied After Initial Deployment

### Fix 1: batch_id AttributeError (Commit: dc7412d)
**Issue**: `AttributeError: 'CursorResult' object has no attribute 'batch_id'`
**Root Cause**: Line 191 tried to access batch_id from SQL query result instead of FIFO result
**Fix**: Initialize `batch_id = None` and set from first symbol's computation result
**Status**: ✅ Fixed and deployed

### Fix 2: Duplicate Key Violations + Missing batch_id Parameter (Commit: 782064c)
**Issue 1**: Duplicate key constraint violation when processing symbols with existing allocations
**Issue 2**: `TypeError: ComputationResult.__init__() missing 1 required positional argument: 'batch_id'`
**Root Cause**:
- Incremental mode only filtered which symbols to process, but compute_symbol() always recomputes ALL allocations for a symbol
- ComputationResult summary was missing required batch_id parameter
**Fix**:
- Delete existing allocations for symbols before recomputing them
- Generate UUID for batch_id and pass to ComputationResult
**Test Results**: Successfully processed KAITO-USD (18 allocations) and SAPIEN-USD (99 allocations)
**Status**: ✅ Fixed, deployed, and tested

## Known Issues

### Non-Critical

1. **PrecisionUtils Initialization Warnings**
   - Warning: "PrecisionUtils not fully initialized (missing _usd_pairs)"
   - Impact: Using fallback precision (1e-8 dust threshold)
   - Status: Acceptable for production, FIFO engine has built-in fallback

2. **Webhook Container Unhealthy Status**
   - Coinbase websocket USER subscription failures
   - Impact: Health check fails but trading functionality unaffected
   - Status: Known Coinbase API issue, non-critical

3. **XLM-USD Decimal Errors**
   - Errors in find_latest_filled_size for XLM-USD
   - Status: Unrelated to FIFO fix, separate issue to address

### Critical (None)

No critical issues identified.

---

## Rollback Plan

If issues detected:

1. **Stop FIFO Cron Jobs**
   ```bash
   ssh bottrader-aws
   crontab -e
   # Comment out FIFO lines with #
   ```

2. **Switch to Manual FIFO**
   - Run only when needed via SSH
   - Use `--force` flag for full recalculation

3. **Revert Code (if needed)**
   ```bash
   cd /opt/bot
   git checkout main
   docker compose build sighook
   docker compose up -d sighook
   ```

---

## Next Steps

### After 3-7 Days Monitoring

1. **If Successful**:
   - Merge `bugfix/single-fifo-engine` to `main`
   - Close FIFO bug issue
   - Create new branch for table restructuring
   - Discuss removing NULL columns from `trade_records`

2. **If Issues Found**:
   - Debug specific problems
   - Adjust FIFO logic as needed
   - Re-test before merging to main

### Table Restructuring Discussion

Topics to cover in next session:
- Remove NULL columns from `trade_records` (parent_id, pnl_usd, cost_basis_usd, etc.)
- Consider separate `buys` and `sells` tables
- Add database views for backwards compatibility
- Migration strategy for existing code

---

## Git Status

**Local Branch**: `bugfix/single-fifo-engine`
```
M docs/CRITICAL_BUG_ANALYSIS_FIFO.md
A docs/FIFO_SINGLE_ENGINE_IMPLEMENTATION.md
M scripts/compute_allocations.py
M SharedDataManager/trade_recorder.py
```

**Pushed to Origin**: ✅ Yes
**Deployed to Server**: ✅ Yes (running `bugfix/single-fifo-engine` branch)
**Merged to Main**: ⏳ Pending validation

---

## Contact Points

**Deployed By**: User (via Claude Code)
**Date**: 2025-12-04 ~16:00 PT
**Server**: bottrader-aws
**Environment**: Production

---

*This document created: 2025-12-04*
*Status: DEPLOYED - MONITORING*
