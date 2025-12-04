# Session: FIFO Allocation Bug Investigation

**Started:** 2025-12-04 02:20 PST

---

## Session Overview

Investigating critical bug in FIFO (First-In-First-Out) allocation system where SELL orders are being matched to incorrect BUY orders, causing wildly inaccurate P&L calculations (up to 100x errors).

**Context from Previous Session:**
- Deployed TP/SL coordination system successfully (branch: `feature/tpsl-coordination`)
- While analyzing trade performance, discovered FIFO matching bug
- Example: Dec 3 SELL matched to Sept 10 BUY instead of Dec 3 BUY from 2 minutes earlier
- Created comprehensive analysis document: `docs/CRITICAL_BUG_ANALYSIS_FIFO.md`

---

## Goals

### Primary Objective
Fix FIFO allocation logic to correctly match SELL orders to appropriate BUY orders based on `remaining_size` availability.

### Specific Goals
1. **Investigation Phase**
   - [ ] Determine if FIFO bug affects live trading decisions
   - [ ] Review `trade_recorder.py` FIFO allocation logic
   - [ ] Identify root cause of parent matching bug
   - [ ] Assess scope: SAPIEN-USD only or widespread?

2. **Fix Implementation**
   - [ ] Create branch: `bugfix/fifo-allocation-mismatch`
   - [ ] Fix FIFO logic to respect `remaining_size`
   - [ ] Add validation to prevent future mismatches
   - [ ] Write test cases for FIFO allocation

3. **Data Repair**
   - [ ] Backup current `trade_records` and `fifo_allocations`
   - [ ] Run FIFO recalculation on test data
   - [ ] Validate corrected P&L matches exchange reality
   - [ ] Run full recalculation if needed

4. **Validation**
   - [ ] Verify SAPIEN-USD shows -$0.14 loss (not -$14.91)
   - [ ] Confirm all recent trades have correct parent matches
   - [ ] Check that no trades have >24h parent gaps
   - [ ] Ensure position monitor uses correct data (if applicable)

---

## Progress

### Investigation Started
- Started: 2025-12-04 02:20
- Branch: TBD (will create `bugfix/fifo-allocation-mismatch`)
- Reference doc: `docs/CRITICAL_BUG_ANALYSIS_FIFO.md`

### Key Files to Investigate
- `MarketDataManager/trade_recorder.py` - FIFO allocation logic
- `MarketDataManager/position_monitor.py` - Check if uses database P&L
- `webhook/webhook_order_manager.py` - Check position sizing logic
- `fifo_engine/engine.py` - Core FIFO computation

### Current Status
- ⏳ Investigation phase
- 🔍 Need to determine if bug affects live trading
- 📋 Analysis document created and ready

---

## Notes

### Critical Context
- **Previous Session:** Deployed TP/SL coordination (separate issue, now resolved)
- **Current Branch:** `feature/tpsl-coordination` (running on production)
- **New Branch Needed:** `bugfix/fifo-allocation-mismatch`

### Severity Assessment
- **Data Integrity:** CRITICAL - All P&L calculations unreliable
- **Live Trading:** UNKNOWN - Needs immediate investigation
- **Tax/Accounting:** HIGH - Cost basis calculations wrong

### Example Bug
```
SELL: Dec 3, 23:13 @ $0.17357 (188.9 SAPIEN)
Wrong Parent: Sept 10, 04:46 @ $0.2349 (213.1 SAPIEN, remaining=0)
Correct Parent: Dec 3, 23:11 @ $0.17477 (188.9 SAPIEN, remaining=188.9)

Database P&L: -$14.91 (WRONG)
Actual P&L: -$0.14 (correct per exchange)
```

---

## Session Commands

Update progress: `/project:session-update`
End session: `/project:session-end`

---

*This session created automatically by /session-start*
