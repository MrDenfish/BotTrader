# CRITICAL BUG ANALYSIS: Stop Loss System Failure
**Date:** December 11, 2025
**Affected Assets:** XLM-USD, PEPE-USD
**Status:** 🚨 ACTIVE BUG - Positions unprotected

---

## 📋 Problem Summary

**Original Report:** Stop loss not triggering for PEPE-USD despite price dropping below -1% threshold.

**Actual Issue:** Exit orders are failing with "Insufficient balance in source account" error, causing positions to remain unprotected while system enters infinite backoff loop.

---

## 🔍 Root Cause Analysis

### Primary Issue: Insufficient Balance Error
```json
{
  "error": "INSUFFICIENT_FUND",
  "message": "Insufficient balance in source account",
  "preview_failure_reason": "PREVIEW_INSUFFICIENT_FUND"
}
```

**Symptoms:**
- Position monitor correctly detects stop loss conditions (-4.96% to -5.01% loss on XLM-USD)
- Exit orders are being placed with correct parameters
- Orders fail at Coinbase API level due to insufficient funds
- System retries 5 times, then enters 15-minute backoff
- Backoff loop repeats indefinitely without ever recovering

### Secondary Issue: Backoff Loop Design Flaw

**Current Behavior (`asset_monitor.py:290-318`):**
1. System tries to place exit order
2. Order fails due to insufficient funds
3. Retry counter increments (max 5 attempts)
4. After 5 failures, enters 15-minute backoff
5. **BUG:** Backoff expires, but retry counter is NOT reset
6. Next attempt immediately hits max retry limit again
7. System enters new 15-minute backoff
8. Loop repeats forever → positions never exit

**Code Location:** `MarketDataManager/asset_monitor.py:306-318`

```python
# If max retries exceeded and not in backoff, enter backoff period
if attempts >= self._oco_max_retries:
    backoff_until = now + timedelta(minutes=self._oco_backoff_minutes)
    self._oco_rearm_retries[symbol] = {
        'attempts': attempts,  # ❌ BUG: attempts not reset after backoff
        'last_attempt_time': now,
        'backoff_until': backoff_until
    }
    self.logger.warning(
        f"⚠️ [REARM_OCO] {symbol} hit max retry limit ({self._oco_max_retries} attempts). "
        f"Backing off for {self._oco_backoff_minutes} minutes. "
        f"OCO protection will NOT be placed until backoff expires."
    )
    return
```

---

## 📊 Evidence from Logs

### XLM-USD Exit Attempts (Last 5 hours)

**Continuous failures every ~30 seconds:**
```
07:08:26 WARNING: Exit order failed for XLM-USD: Insufficient balance in source account
07:08:27 WARNING: XLM-USD hit max retry limit (5 attempts). Backing off for 15 minutes.
07:08:50 INFO: XLM-USD exit triggered: SOFT_STOP (P&L: -4.96%, no bracket)
07:08:56 WARNING: Exit order failed for XLM-USD: Insufficient balance in source account
07:08:58 DEBUG: Skipping XLM-USD: in backoff period for 14.5 more minutes (failed 5 times)
[... repeats indefinitely ...]
```

**Position Details:**
- Entry Price: $0.2540
- Current Price: ~$0.2413-0.2421
- Loss: -4.69% to -5.01%
- Balance: 130.332280 XLM
- Attempted Sell Size: 130.33228 XLM ← Matches balance exactly

### PEPE-USD Status
```
07:08:27 DEBUG: Skipping PEPE-USD: in backoff period for 6.0 more minutes (failed 5 times)
06:43:04 DEBUG: PEPE-USD: P&L=-2.63% (entry=$0.0000, current=$0.0000, balance=6451614.000000)
```

**Note:** PEPE logs show P&L=-2.63% but entry/current prices show as $0.0000, suggesting precision/display issue with very small decimals.

---

## 💡 Hypothesis: Why "Insufficient Balance"?

### Possible Causes:

1. **Locked in Existing Sell Order** (MOST LIKELY)
   - Assets may already be locked in an open sell order
   - Position monitor tries to place new exit order
   - Coinbase rejects because balance is already committed
   - Need to check: Are there existing open SELL orders for XLM-USD/PEPE-USD?

2. **Precision/Rounding Mismatch**
   - Attempting to sell 130.33228 XLM
   - Available balance might be 130.33227999 XLM
   - Tiny difference causes "insufficient" error

3. **Balance Update Lag**
   - Recent buy created position
   - Balance not yet fully settled in Coinbase system
   - Sell attempt before settlement complete

4. **Post-Only Order Rejection**
   - Logs show `"post_only": true`
   - If bid/ask spread crossed, post-only order rejected
   - Error message may be misleading

---

## 🔧 Required Investigations

### 1. Check for Existing Open Orders
```bash
# Query Coinbase for open orders on XLM-USD and PEPE-USD
# Look for existing SELL orders that might be locking the balance
```

### 2. Compare Balances
```bash
# Check Coinbase balance vs. attempted sell size
# Look for precision mismatches
```

### 3. Review Post-Only Logic
```python
# position_monitor.py:775 shows post_only: true
# Verify if this is appropriate for emergency exits
```

---

## 🛠️ Proposed Fixes

### Fix 1: Reset Retry Counter After Backoff (CRITICAL)
**File:** `MarketDataManager/asset_monitor.py:290-318`

**Current Problem:** `attempts` never resets, causing infinite backoff loop

**Solution:**
```python
# After backoff expires, reset attempts counter
if backoff_until and now >= backoff_until:
    # Backoff period expired - reset counter for new attempt
    self._oco_rearm_retries[symbol] = {
        'attempts': 0,  # ✅ Reset counter
        'last_attempt_time': now,
        'backoff_until': None
    }
    self.logger.info(
        f"[REARM_OCO] {symbol} backoff expired, resetting retry counter"
    )
```

### Fix 2: Cancel Conflicting Orders Before Exit
**File:** `MarketDataManager/position_monitor.py:_place_exit_order()`

**Problem:** May be trying to place exit while existing sell order locks balance

**Solution:**
```python
# Before placing exit order, cancel any existing open sell orders for this symbol
existing_sells = [o for o in open_orders if o['symbol'] == product_id and o['side'] == 'sell']
for order in existing_sells:
    await self.trade_order_manager.cancel_order(order['order_id'])
    self.logger.info(f"[POS_MONITOR] Cancelled conflicting sell order {order['order_id']}")
```

### Fix 3: Use Market Orders for Emergency Exits
**File:** `MarketDataManager/position_monitor.py`

**Problem:** Post-only limit orders may fail in volatile conditions

**Solution:**
```python
# For losses > -3%, use market orders instead of limit
use_market = (pnl_pct < Decimal("-3.0"))
```

### Fix 4: Handle INSUFFICIENT_FUND Gracefully
**File:** `MarketDataManager/position_monitor.py:670`

**Problem:** Generic warning doesn't diagnose root cause

**Solution:**
```python
if "INSUFFICIENT_FUND" in error_message:
    # Log detailed balance comparison
    available_balance = await self._get_available_balance(symbol)
    self.logger.error(
        f"[POS_MONITOR] Balance mismatch for {product_id}: "
        f"Tried to sell {size}, available: {available_balance}. "
        f"Checking for conflicting open orders..."
    )
```

---

## ⚠️ Impact Assessment

**Risk Level:** 🔴 CRITICAL

**Current Impact:**
- XLM-USD: Losing ~$1.30+ (~5% of $26 position) and counting
- PEPE-USD: Losing ~$0.78 (~2.6% of $29.65 position) and counting
- No stop loss protection for hours
- Losses accumulating as prices continue to drop
- Other assets may be similarly affected

**Estimated Loss if Unaddressed:**
- If XLM-USD drops to -10%: Additional $3.25 loss
- If PEPE-USD drops to -10%: Additional $2.19 loss
- Pattern may affect all assets with stop loss triggers

---

## 📝 Next Steps (Priority Order)

1. **IMMEDIATE:** Check for existing open sell orders blocking XLM/PEPE
2. **IMMEDIATE:** Manually cancel any conflicting orders
3. **SHORT-TERM:** Implement Fix #1 (reset retry counter after backoff)
4. **SHORT-TERM:** Implement Fix #2 (cancel conflicting orders before exit)
5. **MEDIUM-TERM:** Implement Fix #3 (market orders for severe losses)
6. **MEDIUM-TERM:** Implement Fix #4 (better error diagnostics)
7. **TESTING:** Monitor next stop loss trigger to verify fixes

---

## 📌 Related Files

- `MarketDataManager/asset_monitor.py:290-318` - Backoff logic
- `MarketDataManager/position_monitor.py:600-676` - Exit order placement
- `docs/SESSION_DEC10_OPTIMIZATION_PREP_COMPLETE.md` - Previous session context

---

**Status:** Investigation in progress
**Last Updated:** Dec 11, 2025, 07:15 UTC
