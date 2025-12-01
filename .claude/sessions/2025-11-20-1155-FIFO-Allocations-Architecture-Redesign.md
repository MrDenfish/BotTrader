# Session: FIFO Allocations Architecture Redesign
**Start Time:** 2025-11-20 11:55
**Branch:** `feature/fifo-allocations-redesign`
**Status:** Active

---

## Session Overview

Ground-up redesign of the PnL calculation system to fix fundamental architectural flaws discovered during bug investigation. The current system stores computed values (parent_id, pnl_usd) as if they're immutable facts, causing cascading database corruption that patches cannot fix.

**Previous Context:**
- Months of PnL data corruption (sells matched to wrong parents from Aug/Sep)
- Three layers of bugs identified (Coinbase stale IDs, reconciliation overwrites, database state corruption)
- Patches insufficient - need architectural redesign approved

---

## Goals

### Primary Goal
Design and implement "Immutable Trade Ledger + Computed FIFO Allocations" architecture

### Phase 1: Design & Schema (Days 1-2)
- [ ] Create comprehensive design document
- [ ] Design `fifo_allocations` table schema
- [ ] Design `fifo_computation_log` table schema
- [ ] Plan migration strategy (incremental vs full)
- [ ] Define allocation algorithm (FIFO logic)
- [ ] Establish invariants and validation rules

### Phase 2: Core Implementation (Days 3-5)
- [ ] Create database migration scripts
- [ ] Implement `FifoAllocation` and `FifoComputationLog` models
- [ ] Build FIFO computation engine
- [ ] Rewrite trade_recorder (remove parent_id computation)
- [ ] Create allocation computation job/command

### Phase 3: Integration (Days 6-7)
- [ ] Update webhook/listener.py (simplify)
- [ ] Update websocket_market_manager.py (simplify)
- [ ] Update reporting queries to use allocations
- [ ] Add recomputation command
- [ ] Add validation queries

### Phase 4: Testing & Validation (Days 8+)
- [ ] Test with historical data
- [ ] Verify PnL calculations match expected
- [ ] Run parallel with old system
- [ ] Performance testing
- [ ] Bug fixes and refinement

### Immediate Next Steps
1. Create detailed design document
2. Define table schemas with all fields
3. Sketch out allocation algorithm logic
4. Plan proof-of-concept for single symbol

---

## Progress

### 2025-11-20 11:55 - Session Started
- Created session file
- Confirmed branch: `feature/fifo-allocations-redesign`
- Ready to begin Phase 1: Design & Schema

---

## Notes

### Design Principles
- **Immutability:** Trade records are facts, never update parent_id/pnl after insert
- **Separation:** Computed allocations separate from trade facts
- **Recomputability:** Can delete and recompute allocations anytime
- **Verifiability:** Can validate allocations sum correctly

### Key Invariants
```python
# For each SELL:
assert sum(allocation.allocated_size) == sell.size

# For each BUY:
assert sum(allocation.allocated_size) <= buy.size

# PnL calculation:
assert allocation.pnl == (sell_price - buy_price) * size - fees
```

---

## Issues & Decisions

_(Will be updated as we progress)_

---
