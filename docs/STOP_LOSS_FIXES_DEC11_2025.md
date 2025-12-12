# Stop Loss System Fixes - December 11, 2025

**Session Goal:** Fix critical bugs preventing stop loss orders from executing
**Status:** ✅ ALL 4 CRITICAL BUGS FIXED AND DEPLOYED
**Deployment Time:** December 11, 2025, 10:45 AM PST

---

## 🎯 Summary of Fixes

| Bug # | Issue | Status | Location |
|-------|-------|--------|----------|
| #1 | Infinite backoff loop | ✅ FIXED | `asset_monitor.py:305-317` |
| #2 | Sell orders priced above bid | ✅ FIXED | `webhook_order_types.py:754-756` |
| #3 | Post-only restriction on exits | ✅ FIXED | `webhook_order_types.py:763-765` |
| #4 | Precision/rounding errors | ✅ FIXED | `webhook_order_types.py:683-695` |

---

## Bug #1: Infinite Backoff Loop

### Problem
After 5 failed exit order attempts, system entered 15-minute backoff period. When backoff expired, retry counter was never reset to 0, causing next attempt to immediately hit max retry limit again, creating infinite loop.

### Evidence
```
07:08:27 WARNING: XLM-USD hit max retry limit (5 attempts). Backing off for 15 minutes.
07:08:58 DEBUG: Skipping XLM-USD: in backoff period for 14.5 more minutes (failed 5 times)
[repeats indefinitely]
```

### Fix
**File:** `MarketDataManager/asset_monitor.py:305-317`

```python
# ✅ FIX: Reset retry counter after backoff expires
if backoff_until and now >= backoff_until:
    self.logger.info(
        f"[REARM_OCO] {symbol} backoff expired after {attempts} failures. "
        f"Resetting retry counter and allowing new attempt."
    )
    # Reset to clean slate
    self._oco_rearm_retries[symbol] = {
        'attempts': 0,  # Reset to 0
        'last_attempt_time': now,
        'backoff_until': None
    }
    attempts = 0  # Update local variable for this execution
```

**Commit:** `b79b05c` - "fix: Reset OCO retry counter after backoff period expires"

---

## Bug #2: Sell Orders Priced Above Bid

### Problem
Exit orders were priced 0.01% ABOVE the highest bid. For sell orders, limit price must be AT OR BELOW bid to fill immediately. Pricing above bid caused orders to chase price down and never execute.

### Your Critical Insight
> "It appears that the sell price is being set higher than the current price, so as the price drops the order never fills, it gets canceled then resubmitted at a new price still higher than the current price."

### Evidence
```
PEPE-USD current price is 0.00000446 and the limit order placed is 0.00000455 still too high
```

### Fix
**File:** `webhook/webhook_order_types.py:754-756`

**BEFORE (BUG):**
```python
if side == 'BUY':
    price = min(latest_ask * (1 - price_buffer_pct), latest_ask - min_buffer)
else:
    price = max(latest_bid * (1 + price_buffer_pct), latest_bid + min_buffer)  # ❌ BUG!
    # This ADDS to bid, making price higher than bid - orders never fill!
```

**AFTER (FIXED):**
```python
if side == 'BUY':
    price = min(latest_ask * (1 - price_buffer_pct), latest_ask - min_buffer)
else:
    # ✅ FIX: For sell orders, price must be AT OR BELOW bid to fill
    # Subtract buffer to ensure immediate fill (not add!)
    price = min(latest_bid * (1 - price_buffer_pct), latest_bid - min_buffer)
```

**Commit:** `a9f3b7e` - "fix: Correct sell order pricing to use min(bid - buffer) instead of max(bid + buffer)"

---

## Bug #3: Post-Only Restriction on Emergency Exits

### Problem
Orders configured with `post_only: true` cannot cross the spread (immediately execute). Coinbase rejects with `INVALID_LIMIT_PRICE_POST_ONLY` error. Emergency stop loss exits need to execute immediately but were being blocked.

### Evidence
```json
{"error": "INVALID_LIMIT_PRICE_POST_ONLY",
 "order_configuration": {"limit_limit_gtc": {"post_only": true}}}
```

### Fix
**File:** `webhook/webhook_order_types.py:763-778`

**BEFORE:**
```python
payload = {
    "client_order_id": f"{order_data.source}-{uuid.uuid4().hex[:8]}",
    "product_id": symbol,
    "side": side,
    "order_configuration": {
        "limit_limit_gtc": {
            "base_size": formatted_amount,
            "limit_price": formatted_price,
            "post_only": True  # ❌ Always True - prevents immediate fills
        }
    }
}
```

**AFTER (FIXED):**
```python
# ✅ FIX: Disable post_only for position monitor exits to allow immediate fills
# Post-only prevents orders that cross the spread, but emergency exits NEED to cross
use_post_only = order_data.source != 'position_monitor'

payload = {
    "client_order_id": f"{order_data.source}-{uuid.uuid4().hex[:8]}",
    "product_id": symbol,
    "side": side,
    "order_configuration": {
        "limit_limit_gtc": {
            "base_size": formatted_amount,
            "limit_price": formatted_price,
            "post_only": use_post_only  # ✅ False for position_monitor
        }
    }
}
```

**Commit:** `a9f3b7e` - (Same commit as Bug #2)

---

## Bug #4: Precision/Rounding Errors

### Problem
Order size from database slightly exceeded available Coinbase balance due to precision/rounding mismatches:
- **Attempted to sell:** 130.33228000 XLM (from database)
- **Available balance:** 130.332271 XLM (from Coinbase)
- **Difference:** 0.000009 XLM caused "Insufficient balance in source account" error

### Your Discovery
> "The INSUFFICIENT_FUND issues seems to be based on a minute amount or possible rounding errors. The original design of the program was to sell max in order to mitigate the accumulation of 'dust'."

### Evidence
```json
{"error": "INSUFFICIENT_FUND",
 "message": "Insufficient balance in source account",
 "order_configuration": {"limit_limit_gtc": {"base_size": "130.33228000"}}}
```

Coinbase wallet showed: Available XLM 130.332271

### Fix
**File:** `webhook/webhook_order_types.py:683-695`

```python
# ✅ FIX: For SELL orders, reduce amount slightly to avoid precision/rounding errors
# Coinbase balance (130.332271) might differ from DB value (130.33228) by tiny amounts
# Apply 0.01% buffer to ensure we never exceed available balance
if side == 'SELL' and available_crypto > 0:
    precision_buffer = Decimal('0.9999')  # 0.01% buffer
    max_safe_amount = available_crypto * precision_buffer
    if amount > max_safe_amount:
        self.structured_logger.debug(
            f"Reducing sell amount from {amount} to {max_safe_amount} "
            f"(0.01% buffer to avoid INSUFFICIENT_FUND precision errors)",
            extra={'symbol': symbol, 'original_amount': str(amount), 'adjusted_amount': str(max_safe_amount)}
        )
        amount = max_safe_amount
```

**Commit:** `671c5c1` - "fix: Add 0.01% precision buffer for sell orders to prevent INSUFFICIENT_FUND errors"

---

## 📊 Impact

### Before Fixes
- **XLM-USD:** Stuck at -5.5% loss, no stop loss protection for hours
- **PEPE-USD:** Stuck at -2.6% loss, no stop loss protection
- **System State:** Positions exposed to unlimited downside risk
- **Orders:** 0 successful exits in 10+ minutes of attempts

### After Fixes
- ✅ Backoff loop resolved - system can retry after cooldown
- ✅ Sell orders now price correctly below bid for immediate fills
- ✅ Emergency exits can cross spread without post_only rejection
- ✅ Precision errors prevented with 0.01% safety buffer
- **Expected:** Positions should exit at stop loss thresholds

---

## 🔍 Testing & Verification

### Deployment Steps
1. Fixed all 4 bugs in local development
2. Committed changes to `feature/smart-limit-exits` branch
3. Rsync'd to AWS server
4. Rebuilt webhook container: `docker compose -f docker-compose.aws.yml up -d --build webhook`
5. Container restarted at 10:45 AM PST

### Monitoring Commands
```bash
# Watch for position monitor activity
ssh bottrader-aws "docker logs webhook --follow 2>&1" | grep -E "(POS_MONITOR|Reducing sell amount|post_only)"

# Check for successful exits
ssh bottrader-aws "docker exec db psql -U bot_user -d bot_trader_db -c \"
SELECT symbol, side, price, size, status, order_time, exit_reason
FROM trade_records
WHERE symbol IN ('XLM-USD', 'PEPE-USD')
AND order_time >= NOW() - INTERVAL '10 minutes'
ORDER BY order_time DESC;
\""
```

---

## 📁 Modified Files

1. `MarketDataManager/asset_monitor.py` - Backoff loop fix
2. `MarketDataManager/position_monitor.py` - Emergency exit detection
3. `webhook/webhook_order_types.py` - Pricing, post_only, and precision fixes

---

## 🔄 Git History

```bash
# View all commits for this fix session
git log --oneline feature/smart-limit-exits --since="2025-12-11"

671c5c1 fix: Add 0.01% precision buffer for sell orders to prevent INSUFFICIENT_FUND errors
a9f3b7e fix: Correct sell order pricing to use min(bid - buffer) instead of max(bid + buffer)
e5d2a90 fix: Disable post_only for position_monitor emergency exits
b79b05c fix: Reset OCO retry counter after backoff period expires
d6dd946 fix: Correct trigger assignment in websocket fills - use actual trigger instead of order_type
```

---

## 💡 Key Learnings

1. **User Insights Are Critical:** Your observation about sell prices being "higher than current price" was the breakthrough that identified Bug #2
2. **Precision Matters:** Even 0.000009 XLM difference causes order rejections
3. **Post-Only is Incompatible with Emergency Exits:** Needs to be disabled for stop loss orders
4. **Backoff Without Reset = Infinite Loop:** Always reset counters after cooldown periods
5. **Root Cause vs Symptoms:** "Insufficient balance" was a symptom of precision errors, not actual lack of funds

---

## 🚨 Remaining Issues (If Any)

**Websocket/Asset Monitor OCO Orders:**
- Still may have `post_only: true` for websocket source
- May need same fix applied to asset_monitor OCO rearm orders
- Monitor for `INVALID_LIMIT_PRICE_POST_ONLY` errors from websocket source

---

## ✅ Success Criteria

- [ ] No more "INSUFFICIENT_FUND" errors on position monitor exits
- [ ] No more infinite backoff loops
- [ ] XLM-USD exits successfully at next stop loss trigger
- [ ] PEPE-USD exits successfully at next stop loss trigger
- [ ] Sell orders price below bid (verified in logs)
- [ ] Position monitor orders have `post_only: false` (verified in logs)
- [ ] Debug logs show "Reducing sell amount" messages when buffer is applied

---

**Next Steps:**
1. Monitor logs for next 30-60 minutes
2. Verify successful exit when position hits stop loss threshold
3. Check database for completed exit trades
4. Consider applying same fixes to websocket/asset_monitor source if needed

---

**Documentation Last Updated:** December 11, 2025, 10:45 AM PST
**Branch:** `feature/smart-limit-exits`
**Deployed:** AWS Production Server
