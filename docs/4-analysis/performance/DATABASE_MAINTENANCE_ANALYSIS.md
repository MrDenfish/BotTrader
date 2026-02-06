# Database Maintenance Check Analysis

**File**: `TestDebugMaintenance/trade_record_maintenance.py`
**Function**: `run_maintenance_if_needed()`
**Called From**: `main.py` line 804 (before starting webhook/sighook)

## What the Maintenance Check Does

### 1. **Detection Phase** (Lines 236-268)
Checks if maintenance is needed by looking for incomplete trades:
- BUYs missing `parent_id` or `parent_ids`
- BUYs missing/zero `remaining_size`
- SELLs missing: `cost_basis_usd`, `sale_proceeds_usd`, `pnl_usd`, `realized_profit`, `parent_id`

**If no incomplete trades found**: Exits immediately without changes

### 2. **Batched Fixes** (Lines 313-360)
If incomplete trades exist, applies these fixes:

**a) BUY Parent Fix**:
```sql
UPDATE trade_records
SET parent_id = order_id, parent_ids = NULL
WHERE side='buy' AND (parent_id IS NULL OR parent_ids IS NULL)
```
- Sets BUY orders to be their own parent
- **Does NOT modify historical data** - only fills missing fields

**b) BUY Remaining Size Fix**:
```sql
UPDATE trade_records
SET remaining_size = size
WHERE side='buy' AND (remaining_size IS NULL OR remaining_size = 0)
```
- Resets `remaining_size` to original `size`
- **CRITICAL**: This resets the FIFO inventory for buys

**c) SELL Reset**:
```sql
UPDATE trade_records
SET cost_basis_usd = NULL,
    sale_proceeds_usd = NULL,
    net_sale_proceeds_usd = NULL,
    pnl_usd = NULL,
    realized_profit = NULL,
    parent_ids = NULL,
    parent_id = NULL
WHERE side='sell' AND (cost_basis_usd IS NULL OR ...)
```
- **Clears SELL allocation fields** to prepare for FIFO recompute
- **DOES modify data** - resets P&L calculations

### 3. **Backfill Phase** (Lines 371-373)
Runs two backfill operations:
- `backfill_trade_metrics()` - Fills missing trade metrics
- `fix_unlinked_sells()` - Links sells to their buy parents

### 4. **FIFO Recompute** (Lines 375-392)
**THE CRITICAL PART** - This is where P&L is recalculated:

**Function**: `recompute_fifo_for_symbol()` (Lines 48-162)

**What it does**:
1. **Loads all BUYs and SELLs** for the symbol in chronological order
2. **Resets BUY buckets**: `remaining_size = original size`
3. **Clears SELL allocations**: Sets `parent_id`, `pnl_usd`, etc. to NULL
4. **Replays SELLs chronologically** against BUY inventory (FIFO matching)
5. **Calculates P&L** for each SELL:
   ```python
   cost_basis_usd = sum(allocated_buy_costs with fees)
   sale_proceeds_usd = sell_price * sell_size
   net_sale_proceeds_usd = sale_proceeds_usd - sell_fees
   pnl_usd = net_sale_proceeds_usd - cost_basis_usd
   ```

**Triggered when**:
- SELLs without parents exist but BUYs came before them
- BUYs have negative `remaining_size`
- Manually forced via `FIFO_FORCE_SYMBOLS` env var

## CRITICAL FINDINGS

### Does Maintenance Modify Historical Data?

**YES** - In these ways:

1. **Resets `remaining_size`** on BUYs if NULL/zero → **Changes inventory tracking**
2. **Recalculates `pnl_usd`** on SELLs via FIFO → **Changes P&L values**
3. **Re-links parent_ids** for SELLs → **Changes allocation relationships**

### Why This Matters for Our Analysis

**The `pnl_usd` values in `trade_records` are CALCULATED, not logged!**

From line 151-158:
```python
pnl_usd = net_sale_proceeds_usd - cost_basis_usd
s.pnl_usd = pnl_usd  # Assigned by maintenance, NOT from exchange
```

**What this means**:
- `pnl_usd` is **NOT the actual exit price** - it's a FIFO-calculated profit
- This value gets **recalculated every startup** if trades are incomplete
- The calculation includes **fees** but not necessarily the exact TP/SL trigger point

### Trigger Field Analysis

From our earlier check, all sells show:
```json
{"trigger": "LIMIT"}
```

**This does NOT tell us**:
- Whether it hit TP (take profit limit)
- Whether it hit SL (stop loss limit)
- Whether it was a signal-based exit
- Whether it was manually closed

**The trigger field only indicates the ORDER TYPE, not the EXIT REASON.**

## Impact on Our TP/SL Analysis

### Problem 1: We Can't Determine Exit Reason from Database

The `trigger` field doesn't distinguish between:
- TP hit (+2.5% target)
- SL hit (-1% target)
- Signal exit (Phase 5)
- Manual exit
- Time-based exit

**All show as**: `{"trigger": "LIMIT"}`

### Problem 2: `pnl_usd` Doesn't Reflect TP/SL Targets

The FIFO-calculated `pnl_usd` includes:
- Multiple partial fills from different buy orders
- Fee allocations
- FIFO cost basis averaging

**It does NOT directly correspond to**:
- Entry price + 2.5% (TP target)
- Entry price - 1% (SL target)

### Problem 3: Phase 5 Timing

You mentioned most data is **pre-Phase 5**. We need to:
1. **Find the Phase 5 deployment date**
2. **Filter analysis to post-Phase-5 only**
3. **Look at recent logs** for actual exit reasons

## Recommended Next Steps

1. **Determine Phase 5 Start Date**:
   ```sql
   SELECT MIN(order_time) FROM trade_records WHERE /* Phase 5 indicator */
   ```

2. **Check Recent Logs for Exit Reasons**:
   - Look for position_monitor log entries
   - Check for "TP", "SL", "SIGNAL" in exit logs
   - This is the ONLY source of truth for exit reasons

3. **Analyze Post-Phase-5 Data Only**:
   - Filter by date range after Phase 5 deployment
   - Compare exit logs to trade_records

4. **Investigate Trigger Logging**:
   - Find where triggers are set (position_monitor, order placement)
   - Verify if TP/SL orders log differently than signal exits

## Questions to Answer

1. **When was Phase 5 deployed?** (from git commits or logs)
2. **Do TP/SL orders log different triggers?** (need to check order placement code)
3. **Are exit reasons logged separately?** (check position_monitor logs)
4. **Is the maintenance check running on every startup?** (check production logs)

The maintenance check is working correctly - it's ensuring FIFO integrity. But it means we **cannot trust `pnl_usd` to tell us if TP/SL is working**. We need to look at **actual exit logs and order placement**.
