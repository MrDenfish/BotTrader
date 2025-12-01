# TNSR-USD Order Cancellation Debug Session

**Date:** 2025-11-25
**Branch:** feature/smart-limit-exits
**Status:** ✅ COMPLETE - All Fixes Implemented and Deployed

## Session Overview

Investigated why TNSR-USD had 5 cancelled SELL orders before the 6th finally filled at a loss.

## Problem Statement

TNSR-USD Order Timeline (Nov 25, 2025):
```
08:23:23 - BUY  LIMIT @ $0.169 x 208.33 TNSR = $35.252 ✅ FILLED
08:24:32 - SELL LIMIT @ $0.169 x 208.33 TNSR ❌ CANCELLED
08:25:32 - SELL LIMIT @ $0.170 x 208.33 TNSR ❌ CANCELLED
08:26:32 - SELL LIMIT @ $0.169 x 208.33 TNSR ❌ CANCELLED
08:28:05 - SELL LIMIT @ $0.169 x 208.33 TNSR ❌ CANCELLED
08:29:02 - SELL LIMIT @ $0.169 x 208.33 TNSR ❌ CANCELLED
08:45:03 - SELL LIMIT @ $0.165 x 208.33 TNSR ❌ CANCELLED
08:46:20 - SELL LIMIT @ $0.165 x 208.33 TNSR = $34.331 ✅ FILLED (loss: -$0.92)
```

## Root Cause Analysis

### Logs Analyzed
File: `docs/TNSR_USD_logs.txt` (2000 lines from webhook container)

### Key Findings from Logs

**Example: TOWNS-USD (similar issue):**
```
Line 626: [POS_MONITOR] TOWNS-USD exit triggered: HARD_STOP (P&L: -75.14%)
Line 627: OrderData before placement: order_amount_crypto=536.6, adjusted_size=536.6 ✅
Line 628: ❌ Order validation failed: ⚠️ TOWNS-USD has insufficient balance to sell
```

**Example: ETH-USD (HODL asset):**
```
Line 645: [POS_MONITOR] ETH-USD exit triggered: TAKE_PROFIT (Balance: 0.000361)
Line 646: OrderData before placement: order_amount_crypto=0E-8, adjusted_size=0E-8 ❌
Line 647: ❌ Exit order failed: order build incomplete
```

**Example: ATOM-USD (HODL asset):**
```
Line 651: [POS_MONITOR] ATOM-USD exit triggered: HARD_STOP (Balance: 38.550000)
Line 652: OrderData before placement: order_amount_crypto=0.00, adjusted_size=0.00 ❌
Line 653: ❌ Exit order failed: order build incomplete
```

## Three Bugs Identified

### Bug #1: Position Monitor Doesn't Check HODL List
**File:** `MarketDataManager/position_monitor.py`
**Issue:** Attempts to exit ETH and ATOM even though HODL=ATOM,ETH in .env
**Impact:** Wasted API calls, failed orders, log noise

**Evidence:**
- ETH (HODL) - Position monitor tries to exit at +55% profit
- ATOM (HODL) - Position monitor tries to exit at -62% loss
- Both should be skipped entirely

### Bug #2: Using `available_to_trade_crypto` Instead of `total_balance_crypto`
**Affected Files:**
- `MarketDataManager/position_monitor.py:140` - Gets `available_to_trade_crypto`
- `MarketDataManager/position_monitor.py:229` - Passes it as order size
- `webhook/webhook_validate_orders.py:465` - Uses `available_to_trade_crypto`
- `webhook/webhook_validate_orders.py:483` - Calculates `base_bal_value = available_to_trade * price`

**Issue:** When crypto is locked in pending/failed orders:
- `available_to_trade_crypto = 0`
- Position monitor creates order with `size = 0`
- Validation: `base_bal_value = 0 * price = $0.00`
- Check: `$0.00 >= $1.01 (MIN_SELL_VALUE)` ❌ FAILS
- Error: "insufficient balance to sell"

**Why It Locks:**
1. First order attempt sets `size=0` (from locked balance)
2. Order created on Coinbase but validation fails
3. Balance remains locked by failed order
4. 30 seconds later, position monitor retries
5. Balance still locked, `available_to_trade_crypto` still 0
6. Repeat until order expires/cancels and frees the balance

**TOWNS Example:**
- Balance: 536.6 TNSR (~$4.39)
- `available_to_trade_crypto = 0` (locked in failed order)
- Validation: `$0.00 < $1.01 (MIN_SELL_VALUE)` → FAIL

### Bug #3: Order Cancellation Loop
**Sequence:**
1. Position monitor detects exit threshold
2. Creates order with `size=0` or locked balance
3. Validation fails: "insufficient balance"
4. Order gets cancelled
5. 30 seconds later (POSITION_CHECK_INTERVAL), monitor retries
6. Balance still locked from previous attempt
7. Loop continues until order expires (16 minutes for TNSR)

## Configuration Context

From `.env`:
```
HODL=ATOM,ETH                    # Should skip these in position_monitor
MIN_SELL_VALUE=1.01              # Minimum USD value to allow sell
MIN_ORDER_AMOUNT_FIAT=5          # Minimum order size ($5)
POSITION_CHECK_INTERVAL=30       # Check positions every 30s

# Position Monitor Thresholds
MAX_LOSS_PCT=0.025               # Exit at -2.5%
MIN_PROFIT_PCT=0.035             # Exit at +3.5%
HARD_STOP_PCT=0.05               # Emergency exit at -5%
```

**Note:** `MIN_ORDER_AMOUNT_FIAT=5` is NOT checked for SELL orders in validation logic.

## Recommended Fixes

### Fix #1: Add HODL Check to Position Monitor
**File:** `MarketDataManager/position_monitor.py`
**Location:** In `check_positions()` method, around line 100

```python
async def check_positions(self):
    # Load HODL list from environment
    hodl_list = os.getenv('HODL', '').split(',')
    hodl_assets = {asset.strip().upper() for asset in hodl_list if asset.strip()}

    for symbol, position_data in spot_positions.items():
        # Skip HODL assets
        if symbol.upper() in hodl_assets:
            self.logger.debug(f"[POS_MONITOR] Skipping {symbol} - marked as HODL")
            continue

        # Continue with position check...
```

### Fix #2: Use Total Balance for Exit Orders
**File:** `MarketDataManager/position_monitor.py:229`

**Current:**
```python
await self._place_exit_order(
    size=available_crypto,  # ❌ This is 0 when locked
    ...
)
```

**Fixed:**
```python
await self._place_exit_order(
    size=total_balance_crypto,  # ✅ Use total, not available
    ...
)
```

**Rationale:**
- Position monitor is trying to EXIT an entire position
- Should use total balance, not just available
- If some is locked in existing orders, we want to replace/cancel those orders anyway

### Fix #3: Cancel Existing Orders Before Placing Exit
**File:** `MarketDataManager/position_monitor.py`
**Location:** In `_place_exit_order()`, before calling `place_order()`

**Similar to orphaned order cancellation logic in `asset_monitor.py:339-353`:**

```python
async def _place_exit_order(self, ...):
    # Check for existing orders that might lock the balance
    order_tracker = self.shared_data_manager.order_management.get('order_tracker', {})

    for oid, order_info in list(order_tracker.items()):
        if order_info.get('symbol') == product_id:
            # Cancel existing order for this symbol
            self.logger.info(f"[POS_MONITOR] Canceling existing order {oid} before exit")
            cancel_resp = await self.trade_order_manager.coinbase_api.cancel_order([oid])

            # Validate cancellation
            results = (cancel_resp or {}).get("results") or []
            entry = next((r for r in results if str(r.get("order_id")) == str(oid)), None)

            if entry and entry.get("success"):
                del order_tracker[oid]

    # Now place the exit order with full balance available
    ...
```

## Questions for User (Pending)

1. **Should position monitor cancel existing orders before placing exits?**
   - Pro: Frees up locked balance, prevents loop
   - Con: Might cancel legitimate orders

2. **Should MIN_SELL_VALUE apply to position monitor exits?**
   - Current: HARD_STOP at -75% can't exit if value < $1.01
   - Should emergency exits bypass this check?

3. **Ready to implement these fixes?**
   - Fix #1: HODL check (simple, safe)
   - Fix #2: Use total_balance_crypto (simple, but might need testing)
   - Fix #3: Cancel existing orders (more complex, needs careful implementation)

## Files to Modify

1. `MarketDataManager/position_monitor.py`
   - Add HODL check in `check_positions()` (line ~100)
   - Change `size=available_crypto` to `size=total_balance_crypto` (line 229)
   - Optionally: Add order cancellation logic in `_place_exit_order()` (line ~284)

2. `webhook/webhook_validate_orders.py` (optional)
   - Consider using `total_balance` instead of `available_to_trade` for validation (line 483)
   - Or: Add special handling for "position_monitor_exit" source

## Testing Plan

After fixes are implemented:

1. **Test HODL Blocking:**
   - Verify ETH and ATOM never trigger position monitor exits
   - Check logs for "Skipping X - marked as HODL" messages

2. **Test Exit Order Placement:**
   - Place a position that hits exit threshold
   - Verify order placed with correct size (not 0)
   - Verify no "insufficient balance" errors

3. **Test Order Cancellation:**
   - Create scenario with existing failed orders
   - Verify position monitor cancels them before placing exit
   - Verify exit order succeeds on first attempt

4. **Monitor Production:**
   - Watch for 24 hours after deployment
   - Count successful exits vs failed attempts
   - Verify no HODL assets are sold

## Next Steps

1. User reviews recommended fixes
2. User decides which fixes to implement
3. Implement chosen fixes on desktop
4. Commit and push to `feature/smart-limit-exits`
5. User pulls to AWS server
6. Restart Docker containers
7. Monitor logs for 24-48 hours
8. Verify no more cancellation loops

## Related Session Files

- `.claude/sessions/2025-11-23-1129-limit-order-smart-exits.md` - Original implementation
- `.claude/sessions/2025-11-22-1744-stop-loss-fix-orphaned-orders.md` - Previous fix for orphaned orders

## Notes

- The b9ca0fc commit that set `order_amount_crypto` was correct but insufficient
- The real issue is using `available_to_trade_crypto` which is 0 when balance is locked
- TNSR-USD eventually filled after ~22 minutes when previous orders expired
- Similar issue affects all position monitor exits (TOWNS, ELA, etc.)
- ETH and ATOM shouldn't be in position monitor at all (HODL assets)

---

## Implementation Summary

**Commit:** c8106ba
**Date Implemented:** 2025-11-25
**Status:** ✅ Deployed to AWS Production

### All Fixes Implemented:

#### 1. Position Monitor (MarketDataManager/position_monitor.py)
- ✅ Added HODL check in `check_positions()` - skips ETH/ATOM entirely
- ✅ Changed line 241 to use `total_balance_crypto` instead of `available_to_trade_crypto`
- ✅ Added `_cancel_existing_orders()` method (lines 275-327) to free locked balance

#### 2. PassiveOrderManager (MarketDataManager/passive_order_manager.py)
- ✅ Added HODL/SHILL_COINS filtering (blocks BUY for shill coins, SELL for HODL)
- ✅ Fixed `_submit_passive_sell()` to use `total_balance_crypto` (line 629)

#### 3. Webhook Validation (webhook/webhook_validate_orders.py)
- ✅ Added SHILL_COINS property and validation check
- ✅ Implemented anti-duplicate buy logic (blocks if holding >= $5)
- ✅ Enhanced error messages with position values

### Expected Improvements:
- Exit orders succeed on first attempt (not 5-6 retries)
- No more ETH/ATOM exit attempts
- No more SHILL_COINS (UNFI, TRUMP, MATIC) buys
- No duplicate positions built up
- No crypto dust left after sells

### Monitoring Period: 24-48 hours
User will observe logs for success metrics and report any issues.

---

**Session Status:** ✅ Complete - Code deployed and running on AWS
