# Phase 2.2 Implementation Progress

## Status: Session 2 Complete (Partial State Machine) ✅

### Completed (Step 1: Config & Dataclasses)

**Files Modified:**
1. `backtest/config_4h_hybrid.py`
2. `backtest/strategy_4h_hybrid.py` (dataclasses and enum only)
3. `docs/planning/phase2_2_patch_spec_4h_hybrid_fillrate_v5.md` (E2 penalty formula added)

**Changes Made:**

#### 1. Config Parameters Added
- `compression_lookback_4h = 6` - Recent compression window (24h)
- `retest_offset = 0.0015` - Immediate retest bid offset
- `chase_offset_min = 0.0005` - Min chase offset
- `chase_max_reprices = 3` - Max repricing attempts
- `chase_reprice_interval_minutes = 30` - Reprice interval
- `max_step_up_per_reprice = 0.0020` - Max bid increase per reprice
- `maker_safety_offset = 0.0005` - Post-only safety offset
- Updated `setup_ttl_bars_4h = 6` (24h)

#### 2. Phase 2.2 Baseline Preset Created
`get_phase2_2_baseline_config()` with all Phase 2.2 defaults

#### 3. Setup Dataclass Enhanced
Added unified entry order tracking:
- `entry_order_active: bool`
- `entry_order_type: Optional[Literal["RETEST", "CHASE"]]`
- `entry_price: Optional[float]`
- `entry_created_ts`, `entry_last_update_ts`
- `entry_reprices: int`
- `entry_price_history: list[float]`
- `setup_deadline_ts: Optional[datetime]`

#### 4. State Enum Added
```python
class StrategyState(Enum):
    FLAT = "FLAT"
    SETUP_ACTIVE = "SETUP_ACTIVE"
    RETEST_ORDER_WORKING = "RETEST_ORDER_WORKING"
    CHASE_ORDER_WORKING = "CHASE_ORDER_WORKING"
    IN_POSITION = "IN_POSITION"
```

---

## Completed (Session 2: Partial State Machine Refactor)

**Files Modified:**
1. `backtest/strategy_4h_hybrid.py` - Core state machine refactor
2. `docs/planning/phase2_2_state_machine_refactor_guide.md` - Created comprehensive implementation guide

**Changes Made:**

#### 1. State Machine Dispatcher Updated
- Updated dispatcher to use `StrategyState` enum values
- Changed state flow:
  - `FLAT` → `SETUP_ACTIVE` or `RETEST_ORDER_WORKING`
  - `SETUP_ACTIVE` → wait for order placeability
  - `RETEST_ORDER_WORKING` → retest bid active
  - `CHASE_ORDER_WORKING` → chase bid active (with repricing)
  - `IN_POSITION` → position management

#### 2. Symbol-Level BB Width History
- Added `self.bb_width_history: dict[str, list[float]]` in `__init__`
- Tracks bb_width_pct on each 4h close
- Maintains recent history for compression lookback check

#### 3. Phase 2.2 Helper Methods Implemented
All four helper methods added:
- `_check_recent_compression()` - Checks if ANY of last K bars was compressed
- `_calculate_chase_offset()` - Volatility-aware chase offset calculation
- `_can_place_immediate_retest_bid()` - Post-only check for immediate placement
- `_record_expired_setup()` - Diagnostics for expired setups

#### 4. Updated `_check_donchian_setup()`
- Now accepts `bb_width_pct_history` parameter
- Uses `_check_recent_compression()` helper for Phase 2.2 A1 feature
- Checks last K bars (default 6 = 24h) instead of instant check

#### 5. Refactored `_handle_flat_state()`
Major Phase 2.2 changes:
- Passes bb_width_history to `_check_donchian_setup()`
- Calculates ALL deadlines upfront:
  - `setup.retest_deadline_ts`
  - `setup.chase_deadline_ts`
  - `setup.setup_deadline_ts`
- **Immediate retest bid placement** (Phase 2.2 B1):
  - Calls `_can_place_immediate_retest_bid()` at setup creation
  - If placeable → transition to `RETEST_ORDER_WORKING`
  - If not placeable → transition to `SETUP_ACTIVE`
- Embeds order tracking in Setup object:
  - Sets `entry_order_active = True`
  - Sets `entry_order_type = "RETEST"`
  - Sets `entry_price`
  - Tracks creation/update timestamps
  - Initializes price history

**Code Verified:**
✅ Strategy imports successfully without syntax errors

---

## Remaining Work (Session 3+)

This is a substantial refactor. Current implementation uses:
- String-based states: `"FLAT"`, `"PENDING_SETUP"`, `"ENTRY_ORDER_WORKING"`, `"IN_POSITION"`
- Separate `entry_orders` dictionary with `EntryOrder` objects
- Conditional retest bid placement (waits for `_check_breakout_retest()`)

Phase 2.2 consolidates:
- Enum-based states
- Order tracking embedded in Setup object
- Immediate retest bid placement
- Unified repricing logic

### Implementation Approach Options

**Option A: Complete Rewrite (Clean but Risky)**
- Replace entire state machine logic
- Remove `EntryOrder` dataclass
- Implement all Phase 2.2 features at once
- Pro: Clean, matches spec exactly
- Con: High risk of breaking everything, hard to test incrementally

**Option B: Incremental Migration (Safer)**
- Keep both old and new states temporarily
- Add Phase 2.2 logic alongside existing logic
- Use config flag to switch between modes
- Test thoroughly before removing old code
- Pro: Can validate correctness incrementally
- Con: More code temporarily, requires cleanup phase

**Option C: Hybrid Approach (RECOMMENDED)**
1. Update state type to use StrategyState enum (backward compatible with string values)
2. Add immediate retest bid placement logic
3. Add repricing logic for chase orders
4. Consolidate entry order tracking into Setup
5. Remove `EntryOrder` dataclass and `entry_orders` dict
6. Test after each change
7. Add diagnostics integration throughout

### Recommended Next Session Plan

Given token limits and complexity, suggest:

**Session 1 (Current)**: ✅ Complete
- Config & dataclasses

**Session 2 (Next)**:
- Update strategy class `__init__` to use StrategyState enum
- Implement `_check_recent_compression()` helper (A1)
- Implement `_place_immediate_retest_bid()` helper (B1)
- Update `_handle_flat_state()` to use new logic

**Session 3**:
- Implement `_handle_setup_active_state()`
- Implement `_handle_retest_order_working_state()`
- Implement deadline-based expiry checks

**Session 4**:
- Implement `_handle_chase_order_working_state()`
- Implement chase repricing logic
- Add `_record_expired_setup()` diagnostics

**Session 5**:
- Update engine to pass new indicators
- Run 60-day correctness test
- Debug and fix issues

**Session 6**:
- Run 180-day validation
- Analyze results
- Tune parameters if needed

---

## Critical Design Decisions Documented

### 1. State Machine Mapping
Old → New:
- `"FLAT"` → `StrategyState.FLAT`
- `"PENDING_SETUP"` → `StrategyState.SETUP_ACTIVE` (setup exists, order may not be placeable)
- `"ENTRY_ORDER_WORKING"` (retest) → `StrategyState.RETEST_ORDER_WORKING`
- `"ENTRY_ORDER_WORKING"` (chase) → `StrategyState.CHASE_ORDER_WORKING`
- `"IN_POSITION"` → `StrategyState.IN_POSITION`

### 2. Order Tracking Consolidation
- Remove: `EntryOrder` dataclass, `self.entry_orders` dict
- Use: Setup object fields (`entry_order_active`, `entry_price`, etc.)
- Benefit: Simpler, matches spec exactly

### 3. Deadline-Based Timers
Remove per-order TTL, use only:
- `setup.retest_deadline_ts` = setup_time + retest_ttl_minutes
- `setup.chase_deadline_ts` = setup_time + retest_ttl_minutes + chase_ttl_minutes
- `setup.setup_deadline_ts` = setup_time + setup_ttl_bars_4h × 4h

### 4. Repricing Policy
- Only reprice UP (more aggressive) by default
- Safety-down only if post-only would be violated
- Track all reprices in `entry_price_history`

---

## Testing Strategy

### Unit Tests (If Time Permits)
- `_check_recent_compression()` - various lookback scenarios
- `_calculate_chase_offset()` - ATR-based calculation
- `_should_reprice()` - reprice interval and count limits

### Integration Tests (Required)
1. **60-day sanity check** - ensure strategy runs without errors
2. **Single trade validation** - verify fill logic works correctly
3. **Repricing validation** - ensure reprices occur as expected
4. **Expiry validation** - ensure diagnostics capture expired setups

### Validation Metrics
- Trades/month should increase toward 2-4
- Fill rate should improve vs Phase 2.1
- No regressions in P&L quality
- Diagnostics provide actionable insights

---

## Risk Mitigation

1. **Preserve Phase 2.1 code** - Don't delete, comment with "# Phase 2.1 (deprecated)"
2. **Git commits after each major change** - Enable easy rollback
3. **Test frequently** - Don't accumulate untested changes
4. **Verbose logging** - Add state transition logs for debugging

---

## Current Token Usage: ~100K / 200K

Recommendation: Continue implementation in next session with fresh context window.
