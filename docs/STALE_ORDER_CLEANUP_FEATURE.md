# Stale Order Cleanup Feature

**Date:** December 11, 2025
**Status:** Implemented - Ready for deployment
**Purpose:** Prevent capital from being locked in unfillable orders

---

## Overview

The stale order cleanup feature automatically cancels orders that are unlikely to fill, freeing up capital for new trades. This addresses issues like the TAO-USD case where a buy order at $294.87 never filled because price moved up to $301.04 (+2.09%), locking $30 in an unfillable order.

---

## How It Works

The system runs every **5 minutes** and checks all open orders against two criteria:

### Cancellation Criteria (OR logic)

An order is cancelled if **either** condition is met:

1. **Time-Based:** Order is older than **30 minutes**
2. **Price-Distance Based:** Current price has moved **> 1.5%** away from order price

### Examples

**Example 1: Stale Buy Order (TAO-USD)**
- Order: BUY 0.102 TAO @ $294.87
- Current Price: $301.04
- Price Distance: 2.09% (> 1.5% threshold)
- **Result:** Order cancelled due to price distance

**Example 2: Time-Based Cancellation**
- Order: SELL 10 XRP @ $2.15 (placed 32 minutes ago)
- Current Price: $2.14
- Price Distance: 0.47% (< 1.5% threshold)
- Age: 32 minutes (> 30 minute threshold)
- **Result:** Order cancelled due to age

**Example 3: Order Kept Active**
- Order: SELL 5 SOL @ $95.50 (placed 10 minutes ago)
- Current Price: $95.20
- Price Distance: 0.31% (< 1.5% threshold)
- Age: 10 minutes (< 30 minute threshold)
- **Result:** Order remains active (neither criteria met)

---

## Configuration Parameters

Located in `MarketDataManager/asset_monitor.py:28-31`:

```python
# ── Stale Order Cleanup config ──
STALE_ORDER_MAX_AGE_MINUTES = 30       # Cancel orders older than 30 minutes
STALE_ORDER_PRICE_DISTANCE_PCT = Decimal("0.015")  # Cancel if price moved 1.5% away
STALE_ORDER_CLEANUP_INTERVAL_SEC = 300  # Run cleanup every 5 minutes
```

**Tuning Guidance:**
- **Increase `MAX_AGE_MINUTES`** (e.g., 45) for less aggressive cleanup in ranging markets
- **Decrease `PRICE_DISTANCE_PCT`** (e.g., 0.01 = 1%) for tighter cleanup in volatile markets
- **Adjust `CLEANUP_INTERVAL_SEC`** (e.g., 600 = 10 min) to reduce cleanup frequency

---

## Implementation Details

### File: `MarketDataManager/asset_monitor.py`

**Key Components:**

1. **Configuration** (lines 28-31)
   - Defines max age, price distance threshold, and cleanup interval

2. **State Tracking** (line 65)
   - `self._last_stale_order_cleanup` tracks last cleanup time

3. **Cleanup Method** (lines 1239-1348)
   - `cleanup_stale_orders()` - Main cleanup logic
   - Checks each open order against age and price criteria
   - Logs detailed cancellation reasons

4. **Integration** (lines 100-105)
   - Called at start of `monitor_all_orders()` every monitoring cycle
   - Only executes every 5 minutes (controlled by `CLEANUP_INTERVAL_SEC`)

---

## Log Output Examples

### Successful Cleanup
```
[STALE_ORDER_CLEANUP] Starting periodic stale order cleanup
[STALE_ORDER_CLEANUP] Cancelling stale BUY order for TAO-USD: order_price=$294.87, current_price=$301.04, price distance=2.09% (max 1.50%)
[STALE_ORDER_CLEANUP] ✅ Successfully cancelled stale order abc123def456
[STALE_ORDER_CLEANUP] Cleanup complete: checked 8 orders, cancelled 1 stale orders
```

### No Stale Orders
```
[STALE_ORDER_CLEANUP] Starting periodic stale order cleanup
[STALE_ORDER_CLEANUP] Cleanup complete: checked 8 orders, cancelled 0 stale orders
```

### Age-Based Cancellation
```
[STALE_ORDER_CLEANUP] Cancelling stale SELL order for XRP-USD: order_price=$2.1477, current_price=$2.0073, age=35.2min (max 30min)
[STALE_ORDER_CLEANUP] ✅ Successfully cancelled stale order xyz789abc012
```

### Combined Criteria
```
[STALE_ORDER_CLEANUP] Cancelling stale BUY order for PEPE-USD: order_price=$0.00000455, current_price=$0.00000490, age=45.8min (max 30min), price distance=7.69% (max 1.50%)
[STALE_ORDER_CLEANUP] ✅ Successfully cancelled stale order def456ghi789
```

---

## Benefits

1. **Capital Efficiency:** Frees up locked capital for new trading opportunities
2. **Risk Management:** Prevents orders from executing at unfavorable prices after significant market moves
3. **Reduced Manual Intervention:** Automatically handles stale orders without user action
4. **Dust Prevention:** Complements existing dust cleanup mechanisms
5. **Resource Optimization:** Reduces number of tracked orders in system memory

---

## Monitoring

### Verify Feature is Active
```bash
# Check for cleanup logs (runs every 5 minutes)
ssh bottrader-aws "docker logs webhook 2>&1 | grep STALE_ORDER_CLEANUP | tail -20"
```

### Check Cancelled Orders
```bash
# View most recent cancelled orders
ssh bottrader-aws "docker logs webhook 2>&1 | grep 'Successfully cancelled stale order' | tail -10"
```

### Monitor Specific Symbol
```bash
# Watch for TAO-USD cleanup
ssh bottrader-aws "docker logs webhook --follow 2>&1" | grep "STALE_ORDER_CLEANUP.*TAO-USD"
```

---

## Testing Checklist

Before declaring feature complete:

- [ ] Deploy to AWS
- [ ] Verify cleanup runs every 5 minutes
- [ ] Confirm TAO-USD stale buy order gets cancelled
- [ ] Check logs for proper cancellation reasons
- [ ] Verify no false positives (active orders not cancelled)
- [ ] Monitor for any exceptions or errors

---

## Future Enhancements

Potential improvements for future sessions:

1. **Symbol-Specific Thresholds:** Different timeout/distance for high vs low volatility assets
2. **Order Source Awareness:** More aggressive cleanup for take-profit orders vs entry orders
3. **Market Condition Adjustment:** Tighter thresholds during high volatility
4. **User Notifications:** Alert when stale orders are cancelled
5. **Database Tracking:** Record stale order cancellations for analytics

---

## Related Issues

- **TAO-USD Stale Buy:** Buy 0.102 TAO @ $294.87, current $301.04 (triggered feature request)
- **XRP-USD Stale Take-Profit:** Sell order at $2.1477 when entry was $2.1474 (blocked stop loss)

---

## Git Commit

**Branch:** `feature/smart-limit-exits`

**Commit Message:**
```
feat: Add stale order cleanup with time and price-distance checks

- Automatically cancel orders older than 30 minutes
- Cancel orders when price moves > 1.5% away from order price
- Runs every 5 minutes as part of asset monitoring
- Prevents capital from being locked in unfillable orders
- Addresses TAO-USD case (buy @ $294.87, price now $301.04)

Files modified:
- MarketDataManager/asset_monitor.py
  - Added config params (lines 28-31)
  - Added cleanup method (lines 1239-1348)
  - Integrated into monitor loop (line 102)
```

---

**Documentation Created:** December 11, 2025
**Ready for Deployment:** Yes
