# Accumulation Manager Analysis - ETH Not Accumulating

**Date:** December 27, 2025
**Issue:** No new ETH accumulation since August 31, 2025

---

## Investigation Summary

### ✅ What's Working

1. **Accumulation Manager Exists:**
   - File: `AccumulationManager/accumulation_manager.py`
   - Status: ✅ Code looks correct

2. **Configuration Enabled:**
   - `main.py:878`: `enable_accumulation=True` (for sighook mode)
   - `main.py:730-741`: AccumulationManager properly instantiated
   - Default: `daily_pnl_based_enabled=True`

3. **Recent Positive PnL Available:**
   ```
   Dec 23: $14.90 positive PnL
   Dec 24: $11.76 positive PnL
   Dec 25: $1.82 positive PnL
   Dec 22: $0.95 positive PnL
   ```

4. **Task Creation Code:**
   - `main.py:645-648`: Task should be created if `enable_accumulation` is True
   ```python
   if enable_accumulation and accumulation_manager is not None:
       tasks.append(
           asyncio.create_task(accumulation_manager.start_daily_runner(),
                             name="Accumulation Daily Runner")
       )
   ```

---

## ❌ What's NOT Working

### Problem 1: Daily Runner Not Starting

**Evidence:**
- ❌ No log message: "🕒 Daily accumulation runner started" (should appear on startup)
- ❌ No accumulation logs in sighook container
- ❌ Last ETH buy: August 31, 2025 (almost 4 months ago!)

**Expected Log (Missing):**
```
🕒 Daily accumulation runner started. Scheduled for 00:05:00 UTC daily.
```

**This log should appear when `start_daily_runner()` is called** (line 57 of accumulation_manager.py)

---

### Problem 2: Signal-Based Accumulation Not Triggering

**Configuration:** `signal_based_enabled=True` (line 736)
**Amount Per Signal:** $25.00

**Expected:** Should buy $25 of ETH when `accumulate_on_signal(signal=True)` is called

**Issue:** No code appears to call `accumulate_on_signal()` anywhere!

---

## Root Cause Analysis

### Issue 1: Daily Runner Task May Not Be Created

**Hypothesis:** The accumulation manager task isn't being added to the background tasks.

**Verification Needed:**
1. Check if `enable_accumulation=True` reaches `make_webhook_tasks()`
2. Check if `accumulation_manager is not None`
3. Check if task creation succeeds without errors

**Debugging Steps:**
```python
# Add logging in main.py around line 645:
self.logger.info(f"🔍 enable_accumulation={enable_accumulation}, accumulation_manager={'exists' if accumulation_manager else 'None'}")

if enable_accumulation and accumulation_manager is not None:
    self.logger.info("✅ Creating Accumulation Daily Runner task")
    tasks.append(
        asyncio.create_task(accumulation_manager.start_daily_runner(),
                          name="Accumulation Daily Runner")
    )
else:
    self.logger.warning(f"❌ Accumulation task NOT created: enable={enable_accumulation}, manager={accumulation_manager}")
```

---

### Issue 2: Signal-Based Accumulation Never Called

**Code Search Results:** No calls to `accumulate_on_signal()` found in codebase!

**Expected Integration:** Should be called from trading strategy when certain conditions are met.

**Missing Code Example:**
```python
# In trading_strategy.py or similar:
async def execute_trade(self, symbol, signal_strength):
    # ... trade logic ...

    # After successful trade, trigger accumulation
    if signal_strength >= 4.0:  # Strong signal
        await self.accumulation_manager.accumulate_on_signal(signal=True)
```

---

## Code Issues Found

### Issue 3: Deprecated `pnl_usd` Column Used

**Location:** `accumulation_manager.py:144`

```python
daily_profit = sum(trade.pnl_usd for trade in daily_sells
                  if trade.pnl_usd and trade.pnl_usd > 0)
```

**Problem:** As of Dec 27, 2025, we implemented soft deprecation:
- `pnl_usd` is now set to `None` for all new trades
- P&L data is exclusively in `fifo_allocations` table

**Impact:** Even if daily runner works, it will find $0 profit because `pnl_usd` is NULL!

**Fix Required:** Update to use FIFO allocations:
```python
# Query fifo_allocations instead
daily_profit_query = """
    SELECT COALESCE(SUM(pnl_usd), 0)
    FROM fifo_allocations
    WHERE allocation_version = 2
      AND DATE(sell_time) = :target_date
      AND pnl_usd > 0
"""
```

---

### Issue 4: Missing Method `fetch_sells_by_date()`

**Location:** `accumulation_manager.py:141`

```python
daily_sells = await self.shared_data_manager.trade_recorder.fetch_sells_by_date(date_to_use)
```

**Problem:** This method likely doesn't exist on `trade_recorder`.

**Verification Needed:** Check if `TradeRecorder` has this method.

---

## Recommended Fixes

### Fix 1: Add Logging to Verify Task Creation

**File:** `main.py` around line 645

```python
# Before:
if enable_accumulation and accumulation_manager is not None:
    tasks.append(
        asyncio.create_task(accumulation_manager.start_daily_runner(),
                          name="Accumulation Daily Runner")
    )

# After (with logging):
logger = logger_manager.loggers['shared_logger']
logger.info(f"🔍 Accumulation check: enabled={enable_accumulation}, manager={'exists' if accumulation_manager else 'None'}")

if enable_accumulation and accumulation_manager is not None:
    logger.info("✅ Creating Accumulation Daily Runner task")
    tasks.append(
        asyncio.create_task(accumulation_manager.start_daily_runner(),
                          name="Accumulation Daily Runner")
    )
    logger.info(f"📊 Accumulation config: signal_based={accumulation_manager.signal_based_enabled}, daily_pnl={accumulation_manager.daily_pnl_based_enabled}")
else:
    logger.warning(f"❌ Accumulation NOT enabled: flag={enable_accumulation}, manager={'exists' if accumulation_manager else 'None'}")
```

---

### Fix 2: Update Daily PnL Accumulation to Use FIFO

**File:** `AccumulationManager/accumulation_manager.py`

**Replace lines 138-148:**

```python
async def accumulate_daily_from_realized_pnl(self):
    """
    Runs once per day. Allocates daily realized profit to ETH accumulation.
    Uses FIFO allocations table (NOT deprecated trade_records.pnl_usd).
    """
    if not self.daily_pnl_based_enabled:
        return

    today = datetime.utcnow().date()
    if self.last_daily_accumulation_date == today:
        return  # Already executed today

    try:
        # Fetch positive PnL from FIFO allocations for yesterday
        date_to_use = today - timedelta(days=1)

        # Query FIFO allocations directly
        from sqlalchemy import text
        async with self.shared_data_manager.db_session_manager.async_session() as session:
            query = text("""
                SELECT COALESCE(SUM(pnl_usd), 0) as daily_profit
                FROM fifo_allocations
                WHERE allocation_version = 2
                  AND DATE(sell_time) = :target_date
                  AND pnl_usd > 0
            """)
            result = await session.execute(query, {"target_date": date_to_use})
            row = result.fetchone()
            daily_profit = float(row[0]) if row else 0.0

        if daily_profit <= 0:
            self.logger.info(f"ℹ️ [Accumulation] No positive PnL for {date_to_use}. Skipping accumulation.")
            self.last_daily_accumulation_date = today
            return

        allocation = Decimal(str(daily_profit)) * self.daily_allocation_pct
        self.logger.info(f"📈 [Accumulation] Allocating ${allocation:.2f} to daily ETH accumulation from {date_to_use}'s profit")

        order = await self._place_accumulation_order(allocation)
        if order:
            self._record_accumulation(order, source="daily_pnl")
            self.logger.info(f"✅ [Accumulation] Bought {order['filled_size']} {self.accumulation_symbol} @ ${order['avg_fill_price']}")

        self.last_daily_accumulation_date = today

    except Exception as e:
        self.logger.error(f"❌ [Accumulation] Daily PnL-based accumulation failed: {e}", exc_info=True)
```

---

### Fix 3: Integrate Signal-Based Accumulation

**Location:** Find where trading decisions are made (likely `trading_strategy.py` or similar)

**Add:**
```python
# After placing a successful trade with strong signals
if hasattr(shared_data_manager, 'accumulation_manager'):
    if buy_score >= 4.0:  # or whatever threshold makes sense
        await shared_data_manager.accumulation_manager.accumulate_on_signal(signal=True)
```

---

## Testing Plan

### Step 1: Add Logging and Deploy

1. Add logging to `main.py` (Fix 1)
2. Commit and deploy
3. Restart sighook container
4. Check logs for:
   - "🔍 Accumulation check: enabled=True"
   - "✅ Creating Accumulation Daily Runner task"
   - "🕒 Daily accumulation runner started"

### Step 2: Update FIFO Integration

1. Implement Fix 2 (use FIFO allocations)
2. Deploy
3. Wait for next day at 00:05 UTC
4. Check for accumulation logs

### Step 3: Manual Test

Force a manual accumulation to test:

```python
# In sighook container or via Python shell:
await shared_data_manager.accumulation_manager.accumulate_daily_from_realized_pnl()
```

---

## Quick Diagnostic Commands

```bash
# Check if accumulation task is running
ssh bottrader-aws "docker logs sighook 2>&1 | grep -i 'accumulation runner started'"

# Check for any accumulation logs
ssh bottrader-aws "docker logs sighook 2>&1 | grep -i accumulation | tail -20"

# Check recent ETH buys
ssh bottrader-aws "docker exec db psql -U bot_user -d bot_trader_db -c \"SELECT order_id, side, size, price, order_time FROM trade_records WHERE symbol = 'ETH-USD' AND side = 'buy' ORDER BY order_time DESC LIMIT 5;\""

# Check daily positive PnL available
ssh bottrader-aws "docker exec db psql -U bot_user -d bot_trader_db -c \"SELECT DATE(sell_time) as date, SUM(CASE WHEN pnl_usd > 0 THEN pnl_usd ELSE 0 END) as positive_pnl FROM fifo_allocations WHERE allocation_version = 2 AND sell_time >= NOW() - INTERVAL '7 days' GROUP BY DATE(sell_time) ORDER BY date DESC;\""
```

---

## Summary

**Primary Issue:** The Accumulation Daily Runner task is likely not being created or started.

**Secondary Issue:** Even if it runs, it won't work because it uses deprecated `pnl_usd` column (now NULL) instead of FIFO allocations.

**Tertiary Issue:** Signal-based accumulation is enabled but never called from trading code.

**Action Required:**
1. Add logging to verify task creation
2. Update to use FIFO allocations instead of deprecated columns
3. Integrate signal-based calls into trading strategy
4. Deploy and monitor logs

---

**Created:** December 27, 2025
**Status:** Investigation complete, fixes required
**Priority:** Medium (feature not critical but should be working)
