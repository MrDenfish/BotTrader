# PnL Bug: Root Cause Analysis and Complete Fix

## Discovery

After bot restart with the initial fix, we observed that **new trades were STILL getting stale parent IDs**:

- 9 SELL records from symbols: STRK-USD, FET-USD, SOL-USD, BCH-USD, CLANKER-USD
- All matched to parents from August-November (weeks/months old)
- Example: STRK sell at 06:32:23 matched to Nov 15 parent, but corresponding buy was at 06:31:32 (51 seconds earlier!)

## Root Cause

The initial fix in `webhook/listener.py` was **necessary but insufficient**. Here's what was happening:

### Two-Stage Problem:

1. **Initial Ingest (via Websocket)**
   - SELL order arrives via websocket
   - Gets recorded with Coinbase's stale `originating_order_id` as parent
   - Record created in database with WRONG parent

2. **Later Reconciliation (via REST API)**
   - Bot runs hourly REST API reconciliation
   - Finds the same SELL order
   - **BUG**: UPSERT logic was UPDATING the parent_id/pnl_usd fields
   - Even though `listener.py` now sets `parent_id=None`, the UPSERT would recompute FIFO
   - But by then, the correct parent BUYs might be consumed by other sells
   - Result: Either keeps stale parent OR finds wrong parent

### Code Analysis

In `SharedDataManager/trade_recorder.py` lines 431-434:

```python
# For BUY updates, never touch derived/linkage fields
if side == "buy":
    exclude_from_update.update({"remaining_size", "realized_profit", "pnl_usd", "parent_id", "parent_ids"})
```

**Problem**: This protection only applied to BUY records! SELL records had NO protection against reconciliation overwriting their parent linkages.

## Complete Fix

### Part 1: Prevent Stale IDs at Ingest (Already Done)
**File**: `webhook/listener.py` lines 1152-1165

Force `parent_id=None` and `parent_ids=None` for all SELL orders from REST API, ignoring Coinbase's stale `originating_order_id`.

### Part 2: Protect SELL Records from Reconciliation Updates (NEW)
**File**: `SharedDataManager/trade_recorder.py` lines 436-441

```python
# 🔧 FIX: For SELL updates (reconciliation), also preserve parent linkages and PnL
# If SELL already exists, don't overwrite its FIFO-computed parent matching
if side == "sell" and existing_source is not None:
    # Record already exists - preserve its parent linkages and derived PnL
    exclude_from_update.update({"parent_id", "parent_ids", "pnl_usd", "cost_basis_usd",
                               "sale_proceeds_usd", "net_sale_proceeds_usd", "realized_profit"})
```

**Logic**: Once a SELL is recorded with its parent linkage and PnL, those values become IMMUTABLE. Reconciliation should only update metadata (like `last_reconciled_at`), not financial data.

### Part 3: Fix Historical Bad Data
**File**: `fix_recent_bad_parents.sql`

Recompute FIFO parent linkages for the 9 affected SELL records that already have stale parents.

## Why This is the Correct Approach

### Financial Data Immutability Principle

Once a SELL is executed and recorded:
1. Its parent BUY match is determined (FIFO)
2. Its cost basis is calculated
3. Its PnL is computed
4. Parent BUY's `remaining_size` is decremented

These values should NEVER change, even during reconciliation. They represent a historical financial fact: "This sell was matched to this specific buy, resulting in this specific PnL."

Allowing reconciliation to update these fields would:
- Create inconsistent accounting (parent BUY already had remaining_size decremented)
- Make historical PnL reports unstable (same trade showing different PnL over time)
- Violate FIFO principles (can't retroactively change which buy a sell matched to)

## Implementation Steps

1. ✅ Apply fix to `listener.py` (already done)
2. ✅ Apply fix to `trade_recorder.py` (just completed)
3. ⏳ Restart bot to load new code
4. ⏳ Run `fix_recent_bad_parents.sql` to fix 9 bad records
5. ⏳ Verify new trades get correct parent matching
6. ⏳ Commit both fixes together

## Testing Verification

After restart and SQL fix, verify:
- New SELL orders match to same-day/same-hour BUY orders
- PnL values are small (cents to few dollars), not +$10-13 false profits
- Run `verify_pnl_fix.sql` - discrepancy should be near $0.00

## Affected Records

**Before fix**: 9 SELL records from 11-19 22:55 to 11-20 06:32
- STRK-USD: 3 sells (false profit ~$10-12 each)
- FET-USD: 1 sell (false loss -$6.33)
- SOL-USD: 2 sells (false loss ~$14 each)
- BCH-USD: 1 sell (false loss -$1.44)
- CLANKER-USD: 1 sell (false profit +$12.19)

**Total impact**: ~$40+ in false PnL calculations

---

**Date**: 2025-11-20
**Branch**: `fix/pnl-calculation-bug`
**Files Modified**:
- `webhook/listener.py`
- `SharedDataManager/trade_recorder.py`
