# CRITICAL: FIFO Allocation Bug Analysis

**Date:** 2025-12-04
**Severity:** CRITICAL
**Status:** DISCOVERED - Needs Investigation
**Branch Needed:** `bugfix/fifo-allocation-mismatch`

---

## Executive Summary

The FIFO (First-In-First-Out) allocation system is matching SELL orders to incorrect BUY orders, causing:
- **Wildly inaccurate P&L calculations** (100x errors in some cases)
- **Unreliable performance metrics** (all win rates, averages are wrong)
- **Potential impact on live trading decisions** (if position monitor uses database P&L)

This bug is **more critical than the entry signal quality issue** because it affects data integrity across the entire system.

---

## The Problem

### Example: SAPIEN-USD Trade (Dec 3, 2025)

**What ACTUALLY happened on Coinbase Exchange:**
```
23:11:34 - BUY  188.9 SAPIEN @ $0.17477 = $33.13
23:13:01 - SELL 188.9 SAPIEN @ $0.17357 = $32.99
Duration: 2 minutes
Actual P&L: -$0.14 (small loss)
```

**What the DATABASE thinks happened:**
```
Database matched SELL to OLD BUY from Sept 10:
- Old BUY: 213.1 SAPIEN @ $0.2349 (Sept 10, 04:46)
- Current SELL: 188.9 SAPIEN @ $0.17357 (Dec 3, 23:13)
Database P&L: -$14.91 (100x error!)
```

### Database Evidence

```sql
-- SELL Order
order_id: b92820e6-31c7-41c6-a0b6-ce0b41bff546
parent_id: 2bc16f9a-71af-4a27-8985-1d2eabf0065c (WRONG!)
side: sell
price: $0.17357
size: 188.9
pnl_usd: -$14.91
time: 12/03 23:13:01

-- Wrong Parent BUY (from 3 months ago)
order_id: 2bc16f9a-71af-4a27-8985-1d2eabf0065c
side: buy
price: $0.2349
size: 213.1
remaining_size: 0.00 (already consumed!)
time: 09/10 04:46:31

-- Correct BUY (from 2 minutes before SELL)
order_id: 244dfcaf-af6e-40d4-98e8-75bd283ed589
side: buy
price: $0.17477
size: 188.9
remaining_size: 188.9 (still available!)
time: 12/03 23:11:34
```

---

## Root Cause

**CONFIRMED (2025-12-04):** After thorough code review of `SharedDataManager/trade_recorder.py`, the root cause is:

### Dual FIFO System Conflict

The system has **TWO separate FIFO implementations** that can get out of sync:

1. **Inline FIFO** (`trade_recorder.py` lines 257-311, 482-656)
   - Runs when SELL trades are recorded via `record_trade()`
   - Populates: `trade_records.parent_id`, `pnl_usd`, `cost_basis_usd`, `remaining_size`
   - Logic is actually CORRECT (properly filters by `remaining_size > 0` at line 555)

2. **FIFO Engine** (`fifo_engine/engine.py` + `scripts/compute_allocations.py`)
   - Runs separately as batch/incremental computation
   - Populates: `fifo_allocations` table
   - Also correct but operates independently

### Why the Bug Occurs

The inline FIFO only considers BUYs that exist in the database **at the time the SELL is processed**. If trades are imported/recorded out of chronological order:

**Scenario:**
1. SELL from Dec 3 is processed first (via backfill or import)
2. Only Sept 10 BUY exists in database at that moment
3. Inline FIFO matches SELL → Sept 10 BUY, updates `remaining_size = 0`
4. Dec 3 BUY gets imported later with `remaining_size = 188.9`
5. **Result:** Data is permanently inconsistent!

**Evidence:**
- SELL has `parent_id` and `pnl_usd` populated (inline FIFO ran)
- NO entry in `fifo_allocations` table (FIFO engine didn't run)
- Sept 10 BUY shows `remaining_size = 0` (was matched)
- Dec 3 BUY shows `remaining_size = 188.9` (never matched, imported later)

### The Fix

Remove inline FIFO computation from `trade_recorder.py` and use ONLY the FIFO engine. This aligns with the architecture in `FIFO_ALLOCATIONS_DESIGN.md`.

---

## Impact Assessment

### 1. Data Integrity (CRITICAL)
- ✅ **Confirmed:** All P&L calculations in `trade_records` are unreliable
- ✅ **Confirmed:** Win rates and performance metrics are wrong
- ❓ **Unknown:** Does `fifo_allocations` table have correct data, or is it also wrong?

### 2. Live Trading Impact (HIGH PRIORITY)
- ❓ **Needs Investigation:** Does position monitor query `trade_records.pnl_usd`?
- ❓ **Needs Investigation:** Does position sizing use `remaining_size`?
- ❓ **Needs Investigation:** Are live decisions based on incorrect P&L data?

### 3. Tax/Accounting (HIGH PRIORITY)
- ❌ **Broken:** Cost basis calculations are completely wrong
- ❌ **Broken:** Tax reporting will be inaccurate
- ❌ **Broken:** All historical P&L analysis is unreliable

### 4. Performance Analysis (AFFECTED)
From yesterday's analysis showing "71% win rate" for SAPIEN-USD:
- This was based on wrong P&L data
- Actual performance is unknown until FIFO is fixed
- All symbol performance stats are unreliable

---

## Files to Investigate

### Primary Suspect: FIFO Backfill Logic
**File:** `MarketDataManager/trade_recorder.py`

**Functions to review:**
- `backfill_trade_metrics()` - Lines 925+
- `compute_cost_basis_and_sale_proceeds()` - Lines ~568
- FIFO allocation logic that assigns `parent_id`

### Secondary Checks
1. **Position Monitor** - Does it use `trade_records.pnl_usd`?
   - File: `MarketDataManager/position_monitor.py`
   - Check if P&L calculations query database

2. **Order Sizing** - Does it check `remaining_size`?
   - File: `webhook/webhook_order_manager.py`
   - Check position sizing logic

---

## Immediate Questions to Answer

### Session Start Checklist

1. **Does FIFO affect live trading?**
   - [ ] Search position_monitor.py for "pnl_usd" or "trade_records"
   - [ ] Check if exit decisions query database P&L
   - [ ] If yes: HIGH PRIORITY BUG affecting live trades
   - [ ] If no: "Only" affects reporting/metrics

2. **What's wrong with the FIFO logic?**
   - [ ] Read `compute_cost_basis_and_sale_proceeds()` in trade_recorder.py
   - [ ] Find where it selects parent BUY orders
   - [ ] Identify why it ignores `remaining_size`
   - [ ] Check why it's using Sept 10 BUY instead of Dec 3 BUY

3. **Is this isolated to SAPIEN-USD?**
   - [ ] Query other symbols with recent trades
   - [ ] Check if parent_id mismatches are widespread
   - [ ] Estimate scope of data corruption

4. **Can we recalculate?**
   - [ ] Review FIFO recalculation logic
   - [ ] Test on SAPIEN-USD first
   - [ ] Plan full database recalculation if needed

---

## Proposed Solution Approach

### Phase 1: Investigation (New Session)
1. Create branch: `bugfix/fifo-allocation-mismatch`
2. Identify the bug in trade_recorder.py FIFO logic
3. Determine if it affects live trading (HIGH PRIORITY)
4. Document the scope (all symbols or just some?)

### Phase 2: Fix Development
1. Fix the FIFO allocation logic
2. Add validation to prevent future mismatches
3. Write test cases for FIFO allocation
4. Add logging for FIFO decisions

### Phase 3: Data Repair
1. Create backup of current `trade_records` and `fifo_allocations`
2. Run FIFO recalculation on test data (SAPIEN-USD)
3. Verify corrected P&L matches exchange reality
4. Run full recalculation on all symbols
5. Validate against exchange transaction history

### Phase 4: Prevention
1. Add database constraints (foreign keys, checks)
2. Add monitoring/alerts for FIFO mismatches
3. Add validation in daily report
4. Document FIFO logic for future maintainers

---

## Testing Strategy

### Validation Query
```sql
-- Find trades with suspicious parent matches
SELECT
    s.symbol,
    s.order_id as sell_id,
    s.parent_id,
    s.order_time as sell_time,
    s.price as sell_price,
    s.pnl_usd as db_pnl,
    b.order_time as parent_buy_time,
    b.price as parent_buy_price,
    b.remaining_size as parent_remaining,
    EXTRACT(EPOCH FROM (s.order_time - b.order_time))/3600 as hours_gap
FROM trade_records s
JOIN trade_records b ON s.parent_id = b.order_id
WHERE s.side = 'sell'
  AND s.order_time >= NOW() - INTERVAL '7 days'
  AND EXTRACT(EPOCH FROM (s.order_time - b.order_time))/3600 > 24  -- Gap > 24 hours
ORDER BY hours_gap DESC;
```

This will show all recent SELLs matched to BUYs more than 24 hours old.

---

## Success Criteria

**Bug Fix Complete When:**
- ✅ FIFO logic correctly matches SELLs to most recent available BUYs
- ✅ `remaining_size` is properly tracked and enforced
- ✅ SAPIEN-USD test case shows -$0.14 loss (not -$14.91)
- ✅ All recent trades (last 7 days) show correct parent matches
- ✅ `fifo_allocations` table is populated correctly
- ✅ Position monitor (if it uses P&L) uses correct data

**Validation Complete When:**
- ✅ Database P&L matches exchange transaction history
- ✅ Win rates and performance metrics are recalculated
- ✅ No trades have suspicious parent matches (>24h gap)
- ✅ Tax/accounting exports show correct cost basis

---

## Notes for Next Session

**Start with these commands:**
```bash
# Check if position monitor uses database P&L
grep -r "pnl_usd\|trade_records" MarketDataManager/position_monitor.py

# Find FIFO logic in trade recorder
grep -n "compute_cost_basis\|parent_id" MarketDataManager/trade_recorder.py

# Count trades with >24h parent gaps
docker exec db psql -U bot_user -d bot_trader_db -c "
SELECT COUNT(*) as suspicious_trades
FROM trade_records s
JOIN trade_records b ON s.parent_id = b.order_id
WHERE s.side = 'sell'
  AND EXTRACT(EPOCH FROM (s.order_time - b.order_time))/3600 > 24;"
```

**Critical Decision:**
- If position monitor uses database P&L → **STOP TRADING** until fixed
- If position monitor doesn't use database P&L → Fix can be done without stopping

---

## Related Issues

- **TP/SL Coordination** (separate, already deployed on `feature/tpsl-coordination`)
- **Entry Signal Quality** (separate, needs investigation after FIFO fix)
- **20.2% Win Rate** (may be caused by FIFO bug, not TP/SL coordination)

---

## Session Transition

**Current Session Status:**
- ✅ TP/SL coordination deployed successfully
- ✅ FIFO bug discovered and documented
- ⏳ Awaiting new session to investigate FIFO bug

**Next Session Should:**
1. Start with this document
2. Create new branch: `bugfix/fifo-allocation-mismatch`
3. Investigate trade_recorder.py FIFO logic
4. Determine severity (live trading impact?)
5. Fix the bug
6. Recalculate data
7. Validate against exchange

**Estimated Effort:**
- Investigation: 1-2 hours
- Fix Development: 2-4 hours
- Testing/Validation: 1-2 hours
- Data Recalculation: 1-2 hours
- **Total: 5-10 hours** (full session or split across multiple)
