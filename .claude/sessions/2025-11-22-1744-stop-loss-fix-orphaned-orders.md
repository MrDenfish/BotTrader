# Stop Loss Fix - Orphaned Orders

**Started:** 2025-11-22 17:44

## Session Overview

Critical fix to resolve stop losses not triggering due to orphaned orders blocking OCO placement.

## Goals

- [x] Diagnose why stop losses aren't triggering for positions with significant losses
- [x] Identify orphaned orders blocking protective OCO placement
- [x] Implement automatic cancellation of orphaned orders in asset_monitor
- [x] Fix order fill lifecycle to properly track positions
- [x] Create diagnostic tooling and documentation
- [ ] Deploy fix to production
- [ ] Verify all positions have protective OCO orders

## Problem Summary

### Root Causes Identified:

1. **6 Orphaned SELL Orders Blocking OCO Placement**
   - Non-OCO limit orders in `order_tracker` for: TOWNS, ELA, CLANKER, ZORA, UNI, TRUST
   - `place_order()` returning early due to `has_open_order=True` check
   - Validation succeeding (`is_valid: True`) but placement failing (`order_id: None`)
   - Positions left unprotected with losses: TOWNS -76%, CLANKER -21%, ELA -19%, UNI -15%, ZORA -13%

2. **Broken Order Fill Lifecycle**
   - `_process_order_fill` had `pass` statement for BUY fills
   - No tracking of filled BUY orders as positions
   - No automatic protective OCO placement after fills

## Changes Implemented

### 1. MarketDataManager/asset_monitor.py (lines 309-364)

**Function Modified:** `_manage_untracked_position_exit`

**Changes:**
- Added orphaned order detection (non-OCO orders for same symbol)
- Automatic cancellation via Coinbase API before OCO placement
- Removal from `order_tracker` after cancellation
- Detailed logging of all cancellation attempts

**New Flow:**
```
1. Check for existing OCO bracket (existing)
2. NEW: Detect orphaned non-OCO orders
3. NEW: Cancel orphaned orders on Coinbase
4. NEW: Remove from order_tracker
5. Place protective OCO (existing)
```

### 2. webhook/listener.py (lines 685-747)

**Function Modified:** `_process_order_fill`

**Changes:**
- Complete rewrite of fill handling logic
- BUY fills: Remove from order_tracker, delegate to asset_monitor for OCO placement
- SELL fills: Clean up both order_tracker and positions
- Removed broken trailing stop logic

**New Flow:**
```
BUY FILL:
1. Remove from order_tracker
2. Log fill event
3. Return (asset_monitor handles protection within 3 seconds)

SELL FILL:
1. Remove from order_tracker
2. Remove from positions
3. Log position closure
4. Return
```

## Files Created/Modified

### Modified:
1. `MarketDataManager/asset_monitor.py` - Auto-cancel orphaned orders
2. `webhook/listener.py` - Fixed fill lifecycle

### Created:
1. `scripts/diagnose_oco_blocking.py` - Diagnostic tool for orphaned orders
2. `STOP_LOSS_FIX_SUMMARY.md` - Complete deployment guide

## Diagnostic Results

**Pre-Fix State:**
```
Order Tracker: 6 orders
  - TOWNS-USD: SELL, OPEN (orphaned)
  - ELA-USD: SELL, OPEN (orphaned)
  - CLANKER-USD: SELL, OPEN (orphaned)
  - ZORA-USD: SELL, OPEN (orphaned)
  - UNI-USD: SELL, OPEN (orphaned)
  - TRUST-USD: SELL, OPEN (orphaned)

Positions: 0 (empty)

Holdings: 8 non-zero
  - TOWNS: 536.6 total
  - UNI: 4.158 total
  - ATOM: 38.45 total
  - ELA: 20.23 total
  - CLANKER: 0.4542 total
  - ZORA: 497.0 total
  - UNFI: 12.0 total
  - TRUST: 94.4 total

Untracked Positions: 8
  - 6 blocked by orphaned orders
  - 2 with no orders (ATOM, UNFI)
```

## Deployment Plan

### Pre-Deployment:
```bash
# 1. Backup current state
python scripts/diagnose_oco_blocking.py > pre_fix_state.txt

# 2. Commit changes
git add MarketDataManager/asset_monitor.py webhook/listener.py
git add STOP_LOSS_FIX_SUMMARY.md scripts/diagnose_oco_blocking.py
git commit -m "fix: Auto-cancel orphaned orders blocking OCO placement + fix order fill lifecycle"
git push
```

### Deployment:
```bash
# 3. SSH to AWS and deploy
ssh aws-instance
cd /path/to/BotTrader
git pull
docker restart webhook

# 4. Monitor logs (first 5 minutes)
docker logs -f webhook --tail 100 | grep -i "orphaned\|rearmed\|untracked"
```

### Post-Deployment Verification:
```bash
# 5. Verify fix
python scripts/diagnose_oco_blocking.py > post_fix_state.txt

# Expected:
# - 0 orphaned orders
# - All positions have OCO protection
# - "🛡️ Rearmed protection" success messages in logs
```

## Expected Outcomes

### Immediate (within 5 minutes):
- 6 orphaned orders canceled on Coinbase
- 6 protective OCO orders placed with TP/SL
- All 8 positions protected

### Ongoing:
- All BUY fills get OCO protection within 3 seconds
- No orphaned orders accumulating
- Stop losses trigger at SL levels
- Proper position cleanup after SELL fills

## Key Log Messages

**Success:**
```
[UNTRACKED] Found N orphaned non-OCO order(s) for SYMBOL. Canceling to place protective OCO...
[UNTRACKED] Canceled orphaned order ORDER_ID for SYMBOL
🛡️ Rearmed protection for SYMBOL (untracked): {'success': True, 'order_id': '...'}
```

**Warning:**
```
⚠️ Rearmed protection for SYMBOL (untracked): {'is_valid': True, 'order_id': None}
[UNTRACKED] Failed to cancel orphaned order ORDER_ID
```

## Progress

- [x] Diagnosed root cause (orphaned orders blocking placement)
- [x] Implemented orphaned order cancellation in asset_monitor
- [x] Fixed order fill lifecycle in listener
- [x] Created diagnostic tooling
- [x] Created deployment documentation
- [ ] Committed changes to git
- [ ] Deployed to production
- [ ] Verified all positions protected
- [ ] Monitored for 24 hours

## Notes

- The fix addresses both immediate orphaned orders AND prevents future occurrences
- Asset monitor runs every 3 seconds, so protection is near-immediate
- Order fill lifecycle now properly delegates to asset_monitor instead of broken inline logic
- Diagnostic tool can be run anytime to check for orphaned orders

## Next Steps

1. Commit and push changes
2. Deploy to AWS
3. Monitor webhook logs for orphaned order cancellation
4. Verify all positions have OCO protection
5. Monitor for 24 hours to ensure no regressions
