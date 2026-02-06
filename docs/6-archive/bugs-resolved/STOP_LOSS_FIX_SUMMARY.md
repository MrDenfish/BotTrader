# Stop Loss Fix Summary

**Date:** 2025-11-22
**Issue:** Stop losses not triggering for open positions with significant losses

## Root Cause Analysis

### Primary Issues Identified:

1. **Orphaned Orders Blocking OCO Placement**
   - 6 orphaned SELL limit orders existed in `order_tracker` without protective TP/SL
   - These orders blocked the `asset_monitor` from placing new protective OCO orders
   - Validation succeeded (`is_valid: True`) but placement failed due to `has_open_order` check
   - Positions remained unprotected, allowing unlimited losses

2. **Broken Order Fill Lifecycle**
   - `_process_order_fill` had a `pass` statement for BUY fills (line 703)
   - No code to track filled BUY orders as positions
   - No automatic placement of protective OCO orders after fills
   - Filled positions became "untracked" and vulnerable

### Affected Positions:
- TOWNS-USD: -76% loss
- CLANKER-USD: -21% loss
- ELA-USD: -19% loss
- UNI-USD: -15% loss
- ZORA-USD: -13% loss
- TRUST-USD: orphaned order present

## Changes Implemented

### 1. asset_monitor.py (MarketDataManager/asset_monitor.py:309-364)

**Modified Function:** `_manage_untracked_position_exit`

**Changes:**
- Added detection of orphaned non-OCO orders before attempting OCO placement
- Automatically cancels orphaned orders on Coinbase using `cancel_orders()` API
- Removes canceled orders from `order_tracker` in shared_data
- Proceeds to place protective OCO orders after cleanup
- Logs all cancellation attempts with detailed diagnostics

**Code Flow:**
```
1. Check for existing OCO bracket orders (existing logic)
2. NEW: Scan order_tracker for non-OCO orders on same symbol
3. NEW: Cancel orphaned orders via Coinbase API
4. NEW: Remove from order_tracker
5. Place protective OCO order (existing logic)
```

### 2. listener.py (webhook/listener.py:685-747)

**Modified Function:** `_process_order_fill`

**Changes:**
- Complete rewrite of order fill handling logic
- **BUY fills:** Remove from `order_tracker`, rely on `asset_monitor` to place protective OCO
- **SELL fills:** Clean up both `order_tracker` and `positions` entries
- Removed broken trailing stop logic
- Added clear logging for fill processing

**New Behavior:**
```
BUY FILL:
1. Remove order from order_tracker
2. Log the fill
3. Return (let asset_monitor handle protection)
4. asset_monitor detects new holding within 3 seconds
5. asset_monitor places protective OCO automatically

SELL FILL:
1. Remove from order_tracker
2. Remove from positions dict
3. Log position closure
4. Return
```

## How It Works Now

### Order Lifecycle:

1. **Strategy Triggers BUY Signal**
   - Signal handler places BUY order
   - Order added to `order_tracker` with status="OPEN"

2. **BUY Order Fills**
   - WebSocket receives fill notification
   - `_process_order_fill` removes order from `order_tracker`
   - Holding appears in `spot_positions` (from Coinbase balance sync)

3. **Asset Monitor Detects Untracked Position** (runs every 3 seconds)
   - `sweep_positions_for_exits` finds holding without protective order
   - Calls `_manage_untracked_position_exit`
   - NEW: Checks for orphaned orders, cancels if found
   - Places protective OCO order (TP/SL attached)
   - OCO order added to `order_tracker`

4. **Position Monitoring**
   - `asset_monitor` continuously monitors price vs TP/SL levels
   - Adjusts brackets if needed based on profitability
   - When TP or SL triggers on Coinbase, position auto-closes

5. **SELL Order Fills**
   - WebSocket receives fill notification
   - `_process_order_fill` removes from both `order_tracker` and `positions`
   - Position closed, cycle complete

## Testing & Deployment

### Pre-Deployment Checklist:

- [x] Code changes implemented
- [x] Diagnostic script created (`scripts/diagnose_oco_blocking.py`)
- [ ] Code review completed
- [ ] Backup of current database state
- [ ] Test on non-production environment (if available)

### Deployment Steps:

1. **Backup Database**
   ```bash
   python scripts/diagnose_oco_blocking.py > pre_fix_state.txt
   ```

2. **Deploy Changes to AWS**
   ```bash
   git add MarketDataManager/asset_monitor.py webhook/listener.py
   git commit -m "fix: Auto-cancel orphaned orders blocking OCO placement + fix order fill lifecycle"
   git push origin claude/parameter-tuning-reports-011CV4hhiR6CNdTgBUPLGM5u

   # SSH to AWS and pull changes
   # Restart webhook container
   docker restart webhook
   ```

3. **Monitor Logs** (first 5 minutes)
   ```bash
   docker logs -f webhook --tail 100 | grep -i "untracked\|orphaned\|rearmed"
   ```

4. **Expected Behavior:**
   - Should see 6 orders being canceled (TOWNS, ELA, CLANKER, ZORA, UNI, TRUST)
   - Should see protective OCO orders being placed for each symbol
   - Within 5 minutes, all positions should have OCO protection

5. **Verification**
   ```bash
   python scripts/diagnose_oco_blocking.py > post_fix_state.txt
   ```
   Should show:
   - No orphaned non-OCO orders
   - All positions have corresponding OCO orders in order_tracker

### Rollback Plan:

If issues occur:
```bash
git revert HEAD
git push origin claude/parameter-tuning-reports-011CV4hhiR6CNdTgBUPLGM5u
# SSH to AWS, pull, restart webhook container
```

## Expected Outcomes

### Immediate (within 5 minutes of deployment):
- 6 orphaned SELL orders canceled on Coinbase
- 6 protective OCO orders placed with TP/SL levels
- All 8 untracked positions now protected:
  - TOWNS, UNI, ATOM, ELA, CLANKER, ZORA, UNFI, TRUST

### Ongoing:
- All future BUY fills automatically get protective OCO orders within 3 seconds
- No more orphaned orders accumulating in order_tracker
- Stop losses will trigger when prices hit SL levels
- Positions properly cleaned up after SELL fills

## Monitoring Points

### Key Log Messages to Watch:

**Success Indicators:**
```
[UNTRACKED] Found N orphaned non-OCO order(s) for SYMBOL. Canceling to place protective OCO...
[UNTRACKED] Canceled orphaned order ORDER_ID for SYMBOL
🛡️ Rearmed protection for SYMBOL (untracked): {'success': True, 'order_id': '...'}
BUY order filled - asset_monitor will place protective OCO
```

**Warning Indicators:**
```
⚠️ Rearmed protection for SYMBOL (untracked): {'is_valid': True, 'order_id': None}
[UNTRACKED] Failed to cancel orphaned order ORDER_ID
[UNTRACKED] Failed to build protective OCO for SYMBOL
```

### Metrics to Track:

- Number of orphaned orders canceled per day (should approach 0)
- Number of OCO orders successfully placed
- Percentage of positions with protective orders (should be 100%)
- Average time from BUY fill to OCO placement (should be < 5 seconds)

## Files Modified

1. `MarketDataManager/asset_monitor.py` - Lines 309-364
2. `webhook/listener.py` - Lines 685-747

## Files Created

1. `scripts/diagnose_oco_blocking.py` - Diagnostic tool
2. `STOP_LOSS_FIX_SUMMARY.md` - This document

## Related Issues

- Positions not being tracked after fills
- Stop losses not triggering despite price movement
- Order lifecycle gaps in webhook processing
- Sync issues between Coinbase state and local tracking

## Future Improvements

1. **Position Reconciliation Service**
   - Periodic sync of Coinbase positions vs local tracking
   - Auto-cleanup of stale orders
   - Alerting on tracking mismatches

2. **Enhanced OCO Placement**
   - Calculate TP/SL based on ATR instead of fixed percentages
   - Dynamic adjustment based on market volatility
   - Position sizing based on account risk

3. **Fill Processing Improvements**
   - Direct position tracking in `order_management.positions`
   - Immediate OCO placement on BUY fill (don't wait for asset_monitor)
   - Partial fill handling

4. **Monitoring Dashboard**
   - Real-time view of positions vs protective orders
   - Alert on unprotected positions
   - Track orphaned order cleanup metrics
