# Session: PnL Bug Investigation and Redesign Decision
**Date:** 2025-11-20
**Duration:** ~8 hours (continuation from previous session)
**Branch:** `fix/pnl-calculation-bug` → `feature/fifo-allocations-redesign` (created)
**Status:** Investigation complete, architectural redesign approved

---

## Session Overview

This session continued from the previous "Fix Critical PnL Calculation Bug" session. After implementing initial fixes and testing for several hours, we discovered that the patches were insufficient - the database corruption runs deeper than initially assessed. The session concluded with a decision to pursue a ground-up architectural redesign rather than continue with incremental patches.

---

## Git Summary

### Branch Activity
- **Started on:** `fix/pnl-calculation-bug` (commit 6b76a5e)
- **Ended on:** `feature/fifo-allocations-redesign` (newly created from main)
- **Commits made:** 1

### Commit Details
```
069c7b3 - fix: Add protection for SELL records during reconciliation
```

### Files Changed: 42 files, 8130 insertions(+)

**Key code changes:**
- `SharedDataManager/trade_recorder.py` - Added SELL record protection during reconciliation

**New diagnostic/fix scripts created:**
- `monitor_new_trades.sql` - Monitor recent trades for parent matching verification
- `verify_pnl_fix.sql` - Comprehensive PnL verification queries
- `fix_recent_bad_parents.sql` - Attempted FIFO recomputation for bad records
- `delete_bad_sells_for_rereconciliation.sql` - Delete and re-reconcile approach
- `check_pnl.py` - Python script for PnL verification
- `diagnostic_account_check.py` - Account state diagnostics

**Documentation created:**
- `PNL_BUG_ROOT_CAUSE_AND_FIX.md` - Detailed root cause analysis
- `DEPLOYMENT_NEEDED.md` - Deployment instructions and evidence
- `claude_scripts/` directory - Extensive debugging scripts and documentation

### Final Git Status
```
On branch fix/pnl-calculation-bug
Your branch is ahead of 'origin/fix/pnl-calculation-bug' by 1 commit.
nothing to commit, working tree clean
```

---

## Key Accomplishments

### 1. Deep Root Cause Analysis
**Discovery:** The PnL bug has THREE layers, not two:

1. **Layer 1 (Known):** Coinbase REST API returns stale `originating_order_id` values
   - **Fixed in:** `webhook/listener.py` (previous session)

2. **Layer 2 (Discovered):** Reconciliation was overwriting parent linkages on existing SELL records
   - **Fixed in:** `SharedDataManager/trade_recorder.py` (this session)
   - Added SELL records to exclusion list during UPSERT operations

3. **Layer 3 (Critical Discovery):** Historical database corruption
   - Old BUY records from August/September still show `remaining_size > 0`
   - Should have been fully consumed by sells months ago
   - FIFO logic finds these "zombie" parents as still available
   - Each new SELL gets matched to stale parents instead of recent BUYs

### 2. Testing Validation
After bot restart with both fixes, tested for several hours:

**Test Results:**
```
Time: 18:42:40 | TNSR-USD sell → parent: 11-20 13:31 (5 hours old, should be 18:42 buy)
Time: 18:25:04 | NMR-USD sell → parent: 09-11 04:29 (SEPTEMBER!)
Time: 18:24:05 | NMR-USD sell → parent: 09-11 04:29 (SEPTEMBER!)
Time: 18:22:20 | NMR-USD sell → parent: 09-11 04:29 (SEPTEMBER!)
Time: 18:19:46 | SOL-USD sell → parent: 09-08 23:32 (SEPTEMBER!)
```

**Conclusion:** Patches are insufficient. Each sell has corresponding BUY seconds earlier, but FIFO finds ancient parents instead.

### 3. Architectural Assessment
Analyzed three options:
- **Option 1:** Ground-up redesign (selected)
- **Option 2:** Nuclear database reset
- **Option 3:** Continue patching (rejected)

**Decision Rationale:**
- Current architecture stores computed values (parent_id, pnl_usd) as if they're immutable facts
- But these depend on mutable state (remaining_size)
- This creates cascading corruption that patches cannot fix
- Need separation of concerns: immutable trade facts vs. computed allocations

### 4. Proposed New Architecture
**"Immutable Trade Ledger + Computed FIFO Allocations"**

**Core principles:**
1. `trade_records` table - Immutable facts (what happened)
   - Never update parent_id or pnl_usd after insert
   - Source of truth for trade execution

2. `fifo_allocations` table - Computed matches (what it means)
   - Records which sells matched to which buys
   - Can be deleted and recomputed anytime
   - Decoupled from trade facts

3. Views/Reports - Join trades with allocations
   - PnL queries use allocations table
   - Can test different allocation algorithms
   - Historical data remains intact

**Benefits:**
- ✅ **Correctness:** Can recompute from immutable source
- ✅ **Debuggability:** Separate trade data from computed values
- ✅ **Flexibility:** Test allocation algorithms without touching trades
- ✅ **Reconciliation-safe:** REST API updates only touch facts

---

## Problems Encountered and Solutions

### Problem 1: Initial Fixes Didn't Work
**Symptom:** After bot restart with both fixes, new sells still matched to September parents

**Investigation:**
- Checked if bot loaded new code (✅ yes, commit 069c7b3)
- Verified fixes were active (✅ yes, code inspection confirmed)
- Ran monitoring queries (revealed September/October parents)

**Root Cause:** Database state corruption, not just code bugs

**Solution:** Decided patches insufficient, need architectural redesign

### Problem 2: SQL Fix Scripts Failed
**Attempted:** `fix_recent_bad_parents.sql` - Recompute FIFO for bad records

**Result:**
```
Before: STRK-USD sell → parent 11-07
After:  STRK-USD sell → parent 11-07 (unchanged!)
```

**Why it failed:**
- SQL query finds "earliest available" parent with remaining_size > 0
- But old parents (11-07, 10-04) shouldn't have remaining_size > 0
- They were never properly decremented months ago
- Simple SQL can't fix cascading corruption

**Lesson:** Can't fix corrupt data with more queries on corrupt data

### Problem 3: Historical Data Scope Unknown
**Challenge:** How many records are affected?

**Investigation:**
```sql
-- Found 9 bad sells from 11-19 to 11-20
-- But DASH-USD from 11-19 also has October parents
-- True scope: Possibly months of historical data
```

**Implication:** Nuclear reset or redesign required

---

## Breaking Changes & Important Findings

### Critical Finding: Months of Data Corruption
The PnL bug has been active for **months**, not days:
- September BUY records still show availability
- October/November records have wrong matches
- Every PnL report since August is potentially incorrect

**Impact:**
- Tax reporting affected
- Performance metrics unreliable
- Trading decisions based on false profit data

### Architectural Flaw Identified
**The fundamental problem:**
```python
# Current (broken) approach:
- Store parent_id in trade_records (seems immutable)
- But parent_id depends on remaining_size (mutable)
- Updates to remaining_size don't cascade
- Result: Inconsistent state, no way to verify/fix

# Correct approach:
- Store only immutable facts in trade_records
- Compute allocations separately, can recompute
- Verifiable: Does allocation sum = trade size?
```

---

## Implementation Plan Approved

### Timeline: 8-13 days to production
**Phase 1: Design & Schema (1-2 days)**
- Finalize table designs
- Create migration scripts
- Design allocation algorithm

**Phase 2: Core Implementation (3-5 days)**
- Implement `fifo_allocations` table and models
- Build FIFO computation engine
- Rewrite trade_recorder (remove parent_id computation)
- Add allocation computation job

**Phase 3: Integration (2-3 days)**
- Update listener.py (remove parent logic)
- Update websocket_market_manager.py
- Update reporting queries
- Add recomputation command

**Phase 4: Testing & Validation (2-3 days)**
- Test with historical data
- Verify PnL calculations
- Parallel run with old system
- Bug fixes

**To testing phase: 6-10 days**

### Risk Mitigation Strategy
1. **Parallel operation** - Keep old parent_id fields, run both systems
2. **Incremental rollout** - New reports first, keep old as backup
3. **Recomputation safety** - Can delete allocations without touching trades
4. **Version tracking** - Each batch gets version number for comparison

---

## What Wasn't Completed

### From Original Session Goals:
- ❌ **Fix historical PnL data** - Attempted, but patches insufficient
- ❌ **Verify fix works for new trades** - Tested, discovered deeper issues
- ❌ **Merge fix to main** - Not merging patches, doing redesign instead

### Deferred to Redesign:
- Complete FIFO recomputation
- Historical data cleanup
- PnL report accuracy
- Remaining_size consistency

---

## Configuration Changes

### New Database Tables Planned
```sql
CREATE TABLE fifo_allocations (
    id SERIAL PRIMARY KEY,
    sell_order_id VARCHAR NOT NULL,
    buy_order_id VARCHAR NOT NULL,
    symbol VARCHAR NOT NULL,
    allocated_size DECIMAL NOT NULL,
    -- ... PnL fields
    allocation_version INT DEFAULT 1
);

CREATE TABLE fifo_computation_log (
    id SERIAL PRIMARY KEY,
    symbol VARCHAR NOT NULL,
    computation_time TIMESTAMP NOT NULL,
    version INT NOT NULL,
    -- ... status tracking
);
```

### Migration Strategy
- **Incremental** (recommended) - Add new tables, keep old structure
- Run both systems in parallel during transition
- Gradually migrate reports to use allocations

---

## Lessons Learned

### Technical Lessons

1. **Patches Don't Fix Architectural Flaws**
   - We identified and fixed 3 bugs, but couldn't fix the architecture
   - Storing computed values as facts creates unfixable corruption
   - Sometimes ground-up redesign is the right answer

2. **Test Your Fixes, Then Test Again**
   - First fix looked good (historical PnL corrected)
   - Second test revealed it failed (new trades still broken)
   - Only extended testing reveals cascading issues

3. **Database Corruption Cascades**
   - One wrong `remaining_size` value affects all future FIFO matches
   - Can't fix with point updates
   - Need recomputation from clean state

4. **Separation of Concerns Matters**
   - Immutable facts vs computed values should be separate
   - Makes verification possible
   - Enables safe recomputation

### Process Lessons

1. **Know When to Stop Patching**
   - After 3 layers of bugs, recognize architectural issue
   - User's instinct to redesign was correct
   - Don't throw good money after bad code

2. **Clear Documentation Helps Decision Making**
   - `PNL_BUG_ROOT_CAUSE_AND_FIX.md` laid out the evidence
   - Options analysis showed why redesign wins
   - Timeline estimate made decision concrete

3. **Incremental Migration Reduces Risk**
   - Don't have to delete old system immediately
   - Parallel operation validates new approach
   - Can rollback if needed

---

## Tips for Future Developers

### Understanding the Current Bug

**If you see sells matched to old parents:**
1. Check `remaining_size` on the old parent BUY
2. It probably shows > 0 when it should be 0
3. This is database corruption, not a code bug you can fix with patches

**The corruption chain:**
```
Month 1: SELL gets wrong parent (bug in websocket)
  → Parent's remaining_size not decremented
Month 2: FIFO finds that parent still "available"
  → New SELL matches to Month 1 parent
Month 3: Chain continues, corruption spreads
```

### Working with the Redesign

**When implementing `fifo_allocations`:**
1. Start with one symbol (test case)
2. Compute allocations for that symbol only
3. Compare with expected PnL
4. Verify: SUM(allocated_size) = trade size
5. Once validated, scale to all symbols

**Critical invariants to maintain:**
```python
# For each SELL:
assert sum(allocation.allocated_size) == sell.size

# For each BUY:
assert sum(allocation.allocated_size) <= buy.size

# PnL should match:
assert allocation.pnl == (sell_price - buy_price) * size - fees
```

### Testing Strategy

**Don't just test happy path:**
1. Test with corrupted data (we have plenty)
2. Test with missing buys (what if FIFO can't match?)
3. Test with partial fills (one sell → multiple buys)
4. Test recomputation (delete allocations, recompute, compare)

**Validation queries:**
```sql
-- Should be zero or close to zero
SELECT symbol,
       SUM(CASE WHEN side='sell' THEN size ELSE -size END) as net
FROM trade_records
GROUP BY symbol;

-- Find orphaned allocations
SELECT * FROM fifo_allocations a
LEFT JOIN trade_records t ON t.order_id = a.sell_order_id
WHERE t.order_id IS NULL;
```

---

## Next Session: Ground-Up Redesign

**Starting branch:** `feature/fifo-allocations-redesign` (created, empty)
**First task:** Design document (detailed architecture spec)
**User approved:** Timeline (8-13 days) and incremental migration approach

**The redesign will:**
- Separate immutable trade facts from computed allocations
- Enable safe PnL recomputation anytime
- Fix months of historical data corruption
- Create maintainable, verifiable accounting system

---

## Session Metrics

- **Commits:** 1
- **Files changed:** 42 files, 8,130 insertions
- **SQL scripts created:** 8
- **Python scripts created:** 2
- **Documentation pages:** 3
- **Hours of testing:** ~4 hours (bot running with fixes)
- **Critical insights:** 3 (Layer 3 bug, architectural flaw, redesign need)
- **Decision made:** Pursue ground-up redesign (Option 1)

---

**Session ended:** 2025-11-20 18:45
**Next session:** Feature implementation - FIFO Allocations Redesign
**Branch ready:** `feature/fifo-allocations-redesign`
