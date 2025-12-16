# 🚨 CRITICAL BUG ANALYSIS: remaining_size Not Being Updated

**Date:** 2025-11-28
**Severity:** CRITICAL
**Impact:** Incorrect PnL calculations, wrong parent_id assignments, broken FIFO logic

---

## Executive Summary

The `trade_records.remaining_size` field is **NOT being updated** when sells occur, causing the old parent_id/FIFO system in `trade_recorder.py` to assign incorrect cost basis to sells. This creates massive PnL discrepancies (e.g., -$61.85 vs -$0.26 for recent trades).

---

## The Bug in Action

### Example: TOSHI-USD Sell (2025-11-28 13:13:49 UTC)

**The Sell:**
- Order ID: `29dc0de3-bdb5-4397-8aae-7f123d4efee9`
- Amount: 33,395 TOSHI
- Price: $0.000423

**Available Buy Orders (FIFO order):**
1. Oct 24 buy (`aea92187...`): 77,871 TOSHI @ $0.000773
2. Nov 28 buy (`871849d0...`): 78,069 TOSHI @ $0.000424 (21 min before sell!)

**What SHOULD happen (FIFO v2 - correct):**
- Match 33,393 TOSHI to Nov 28 buy @ $0.000424 → PnL: -$0.045
- Match 2 TOSHI to Oct 24 buy @ $0.000773 → PnL: -$0.0007
- **Total PnL: -$0.05**
- Update Nov 28 buy `remaining_size`: 78,069 → 44,676

**What ACTUALLY happened (trade_records - broken):**
- `parent_id` assigned to: Oct 24 buy (`aea92187...`)
- `realized_profit`: -$16.45
- Nov 28 buy `remaining_size`: **78,069 (NOT UPDATED!)**

### Verification Query Results

```sql
-- Expected after sell
SELECT 78069 - 33393 as expected_remaining;  -- 44,676

-- Actual in database
SELECT remaining_size FROM trade_records
WHERE order_id = '871849d0-7dc5-46d1-8152-16b736741448';  -- 78,069 ❌
```

---

## Root Cause Analysis

### Code Location
**File:** `SharedDataManager/trade_recorder.py`

### The Flow

1. **FIFO Computation** (lines 290-311)
   ```python
   fifo_result = await self.compute_cost_basis_and_sale_proceeds(...)
   parent_ids = fifo_result["parent_ids"]
   update_instructions = fifo_result["update_instructions"]
   parent_id = (parent_ids[0] if parent_ids else parent_id)  # Line 311
   ```

2. **Update Instructions Created** (lines 619-623)
   ```python
   update_instructions.append({
       "order_id": p.order_id,
       "remaining_size": float(q_base(new_rem)),  # Correct new value!
       "realized_profit_delta": float(q_usd(realized_profit_delta)),
   })
   ```

3. **Update Instructions SHOULD Be Applied** (lines 459-466)
   ```python
   for instruction in update_instructions:
       parent_record = await session.get(TradeRecord, instruction["order_id"])
       if not parent_record:
           self.logger.warning(f"⚠️ Parent BUY not found: {instruction['order_id']}")
           continue
       parent_record.remaining_size = instruction["remaining_size"]  # Line 464
   ```

### Why It's Failing

The code at lines 459-466 looks correct, but `remaining_size` is NOT being updated in the database. Possible causes:

1. **Session not committing** - The update might not be persisted
2. **Different session** - Updates happening in wrong database session
3. **Rollback occurring** - Transaction rolling back silently
4. **Concurrent writes** - Another process overwriting values
5. **Code not executing** - This block might be skipped

---

## Impact Analysis

### 1. Broken Cost Basis Tracking

All remaining_size values appear "full", so:
- FIFO picks oldest (highest cost) buys first
- Creates artificial large losses
- **Example:** -$16.45 loss reported vs actual -$0.05

### 2. Cascade Effect

Each subsequent sell uses wrong cost basis:
- Trade 1: Wrong parent → Wrong PnL
- Trade 2: Sees Trade 1's inventory as "available" → Wrong parent → Wrong PnL
- Trade 3: Compounds errors further...

### 3. Widening Discrepancy

**Last 24 hours:**
- `trade_records.realized_profit`: -$61.85
- `fifo_allocations.pnl_usd`: -$0.26
- **Discrepancy: $61.59** (99.6% error!)

### 4. Report Accuracy

When `USE_FIFO_ALLOCATIONS=0` (using trade_records):
- PnL completely wrong
- Win/loss classification wrong
- Performance metrics misleading

---

## Why FIFO Allocations v2 is Correct

The standalone FIFO allocation computation (`scripts/compute_allocations.py`) works correctly because:

1. **Reads from scratch** - Doesn't rely on `remaining_size`
2. **Proper FIFO logic** - Processes ALL buys/sells in order
3. **Independent calculation** - Not affected by real-time bugs
4. **Verified correct** - Matches actual inventory (small TOSHI portions to old buys, bulk to new buys)

---

## Immediate Recommendations

### 1. ✅ Use FIFO Allocations v2 for ALL Reports

**Already configured:**
```bash
# In .env
USE_FIFO_ALLOCATIONS=1
FIFO_ALLOCATION_VERSION=2
```

**Verify it's active:**
```bash
ssh bottrader-aws 'cat /opt/bot/.env | grep FIFO'
```

### 2. 🔍 Investigate Why remaining_size Isn't Updating

**Debug steps:**
1. Check sighook logs for errors during sell recording
2. Add debug logging around line 464 (remaining_size update)
3. Verify database session commits are succeeding
4. Check if there's a competing update process

**Query to check:**
```sql
-- Find sells where parent's remaining_size wasn't updated
SELECT
    s.order_id as sell_id,
    s.symbol,
    b.order_id as parent_id,
    b.size as buy_size,
    b.remaining_size,
    s.size as sell_size,
    b.remaining_size - s.size as expected_remaining
FROM trade_records s
JOIN trade_records b ON b.order_id = s.parent_id
WHERE s.side = 'sell'
    AND s.order_time >= NOW() - INTERVAL '7 days'
    AND b.remaining_size = b.size  -- Not reduced at all!
LIMIT 10;
```

### 3. 🛠️ Fix Options

**Option A: Fix the remaining_size update logic**
- Debug why line 464 isn't persisting
- Add transaction logging
- Ensure session.commit() is called

**Option B: Deprecate trade_records PnL fields** (RECOMMENDED)
- Stop using `parent_id`, `realized_profit`, `remaining_size`
- Use ONLY FIFO allocations for all PnL
- Keep `trade_records` as raw order data only
- Update reports to never use `trade_records.realized_profit`

**Option C: Run maintenance script periodically**
- Recompute `remaining_size` from FIFO allocations
- Sync `parent_id` and `realized_profit` from FIFO
- Bandaid solution, doesn't fix root cause

---

## Long-Term Strategy

### Phase 1: Immediate (This Week)
- ✅ Reports use FIFO v2 only
- ✅ Verify FIFO recomputation runs daily
- 🔲 Add monitoring for remaining_size discrepancies

### Phase 2: Investigation (Next Week)
- Debug remaining_size update failure
- Add comprehensive logging
- Test fix in development

### Phase 3: Deprecation (Next Month)
- Mark `parent_id`, `realized_profit`, `remaining_size` as deprecated
- Update all code to use FIFO allocations
- Add database migration to remove fields (optional)

---

## Testing Recommendations

### 1. Verify FIFO Computation is Scheduled

Check if daily FIFO recomputation is set up:
```bash
ssh bottrader-aws "crontab -l | grep compute_allocations"
```

### 2. Manual Recomputation Test

After new sells, immediately recompute:
```bash
ssh bottrader-aws "docker exec sighook python3 -m scripts.compute_allocations --version 2 --all-symbols --force"
```

Compare results with `trade_records.realized_profit`.

### 3. Add Automated Discrepancy Alerts

Create daily check:
```sql
-- Flag when trade_records vs FIFO differ by >10%
SELECT
    COUNT(*) as discrepant_trades
FROM (
    SELECT
        tr.order_id,
        tr.realized_profit as tr_pnl,
        COALESCE(SUM(fa.pnl_usd), 0) as fifo_pnl,
        ABS(tr.realized_profit - COALESCE(SUM(fa.pnl_usd), 0)) as diff
    FROM trade_records tr
    LEFT JOIN fifo_allocations fa ON fa.sell_order_id = tr.order_id AND fa.allocation_version = 2
    WHERE tr.side = 'sell' AND tr.realized_profit IS NOT NULL
    GROUP BY tr.order_id, tr.realized_profit
) AS comp
WHERE diff > ABS(tr_pnl * 0.10);  -- More than 10% difference
```

---

## Related Files

- `SharedDataManager/trade_recorder.py` - Contains buggy remaining_size update
- `scripts/compute_allocations.py` - Correct FIFO implementation
- `fifo_engine/engine.py` - FIFO allocation logic
- `botreport/aws_daily_report.py` - Report generation
- `Config/constants_report.py` - USE_FIFO_ALLOCATIONS setting

---

## Conclusion

**The `trade_records` table's PnL fields are fundamentally broken** due to the `remaining_size` not being updated. This has created a 99.6% error in recent PnL reporting.

**FIFO Allocations v2 is the correct source of truth** and should be used exclusively going forward.

**Immediate action required:** Investigate why `remaining_size` updates aren't persisting to the database (lines 459-466 in trade_recorder.py).

---

**Created:** 2025-11-28
**Author:** Claude Code Investigation
**Status:** Active Bug - Critical Priority
