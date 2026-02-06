# Phase 2.2 State Machine Refactor - Detailed Guide

## Current Progress (Session 2)

✅ Updated `__init__` to use `StrategyState` enum
✅ Updated state initialization to `StrategyState.FLAT`
✅ Updated `get_state()` return type

## Remaining Changes (Too Large for One Session)

This document provides a complete roadmap for the refactor. Given complexity, recommend implementing in stages with testing between each.

---

## Stage 1: Update State Dispatcher (Next Step)

### File: `backtest/strategy_4h_hybrid.py`

**Current code (lines ~331-343):**
```python
if current_state == "FLAT":
    return self._handle_flat_state(...)
elif current_state == "PENDING_SETUP":
    return self._handle_pending_setup_state(...)
elif current_state == "ENTRY_ORDER_WORKING":
    return self._handle_entry_order_working_state(...)
elif current_state == "IN_POSITION":
    return self._handle_in_position_state(...)
```

**New code:**
```python
if current_state == StrategyState.FLAT:
    return self._handle_flat_state(...)
elif current_state == StrategyState.SETUP_ACTIVE:
    return self._handle_setup_active_state(...)
elif current_state == StrategyState.RETEST_ORDER_WORKING:
    return self._handle_retest_order_working_state(...)
elif current_state == StrategyState.CHASE_ORDER_WORKING:
    return self._handle_chase_order_working_state(...)
elif current_state == StrategyState.IN_POSITION:
    return self._handle_in_position_state(...)
```

**Note:** This splits `ENTRY_ORDER_WORKING` into `RETEST_ORDER_WORKING` and `CHASE_ORDER_WORKING`.

---

## Stage 2: Implement Helper Methods

### 2.1: `_check_recent_compression()` (A1)

Add after other helper methods (~line 700):

```python
def _check_recent_compression(
    self,
    bb_width_pct_history: list[float],
    threshold: float,
    lookback: int
) -> bool:
    """
    Check if BB was compressed in last K 4h bars.

    Phase 2.2 A1: Instead of requiring compression at trigger bar,
    allow if ANY of last K bars was compressed.
    """
    if len(bb_width_pct_history) < lookback:
        return False

    recent = bb_width_pct_history[-lookback:]
    return any(pct <= threshold for pct in recent)
```

### 2.2: `_calculate_chase_offset()` (B2)

```python
def _calculate_chase_offset(self, atr_pct_4h: float) -> float:
    """
    Calculate volatility-aware chase offset.

    Phase 2.2 B2: Dynamic offset based on ATR.
    """
    if not self.config.use_dynamic_chase_offset:
        return self.config.chase_offset

    if atr_pct_4h <= 0:
        return self.config.chase_offset_min

    dynamic_offset = self.config.chase_atr_mult * atr_pct_4h
    return max(self.config.chase_offset_min, dynamic_offset)
```

### 2.3: `_can_place_immediate_retest_bid()` (B1)

```python
def _can_place_immediate_retest_bid(
    self,
    bar: pd.Series,
    breakout_level: float
) -> tuple[bool, Optional[float]]:
    """
    Check if immediate retest bid is placeable (post-only).

    Phase 2.2 B1: Place bid immediately at setup creation.

    Returns:
        (placeable, bid_price)
    """
    retest_price = breakout_level * (1 - self.config.retest_offset)

    # Post-only check: bid must be below current close
    if retest_price >= bar['close']:
        return (False, None)

    return (True, retest_price)
```

### 2.4: `_record_expired_setup()` (Diagnostics)

```python
def _record_expired_setup(
    self,
    symbol: str,
    setup: Setup,
    reason: str
) -> None:
    """
    Record diagnostics for expired setup.

    Phase 2.2 diagnostics: Track why setups fail to fill.
    """
    expired = ExpiredSetup(
        symbol=symbol,
        setup_time=setup.setup_time,
        setup_type=setup.setup_type,
        breakout_level=setup.breakout_level,
        min_low_during_ttl=setup.min_low_during_ttl,
        max_high_during_ttl=setup.max_high_during_ttl,
        expiration_reason=reason,
        bb_width_pct_at_setup=setup.bb_width_pct_at_setup
    )

    # Add entry prices if order was active
    if setup.entry_order_active:
        expired.retest_limit_price = setup.entry_price if setup.entry_order_type == "RETEST" else None
        expired.chase_limit_price = setup.entry_price if setup.entry_order_type == "CHASE" else None

        # Calculate distance-to-fill
        if setup.entry_price and setup.min_low_during_ttl != float('inf'):
            gap = setup.entry_price - setup.min_low_during_ttl
            gap_pct = (gap / setup.breakout_level) * 100

            if setup.entry_order_type == "RETEST":
                expired.retest_gap_pct = gap_pct
            elif setup.entry_order_type == "CHASE":
                expired.chase_gap_pct = gap_pct

    self.expired_setups.append(expired)
```

---

## Stage 3: Refactor `_handle_flat_state()`

**Current behavior:**
- Checks setup conditions
- Creates Setup object
- Transitions to `"PENDING_SETUP"`

**Phase 2.2 behavior:**
- Checks setup conditions (with recent compression)
- Creates Setup object
- **Immediately attempts to place retest bid**
- If placeable → transition to `RETEST_ORDER_WORKING`
- If not placeable → transition to `SETUP_ACTIVE` (wait for placeability)

**Key changes:**
1. Use `_check_recent_compression()` instead of instant check
2. Calculate all deadlines immediately:
   - `setup.retest_deadline_ts`
   - `setup.chase_deadline_ts`
   - `setup.setup_deadline_ts`
3. Call `_can_place_immediate_retest_bid()`
4. If placeable:
   - Set `setup.entry_order_active = True`
   - Set `setup.entry_order_type = "RETEST"`
   - Set `setup.entry_price = retest_price`
   - Set `setup.entry_created_ts = bar.name`
   - Transition to `RETEST_ORDER_WORKING`
5. If not placeable:
   - Transition to `SETUP_ACTIVE`

---

## Stage 4: Implement New State Handlers

### 4.1: `_handle_setup_active_state()` (NEW)

**Purpose:** Setup exists but retest bid not yet placeable

**Logic:**
1. Track min/max price during TTL
2. Check if setup expired (setup_deadline_ts)
3. Try to place retest bid on each bar
4. If placeable → transition to `RETEST_ORDER_WORKING`
5. If setup expires → record diagnostics, transition to `FLAT`

### 4.2: `_handle_retest_order_working_state()` (Replaces part of old ENTRY_ORDER_WORKING)

**Purpose:** Retest bid is resting, waiting for fill or escalation

**Logic:**
1. Track min/max price during TTL
2. Check if filled (`bar['low'] <= setup.entry_price`)
   - If filled → create position, transition to `IN_POSITION`
3. Check if retest deadline reached (`now >= setup.retest_deadline_ts`)
   - If yes and chase enabled → transition to `CHASE_ORDER_WORKING`
   - If yes and chase disabled → record expired, transition to `FLAT`
4. Check if setup expired (`now >= setup.setup_deadline_ts`)
   - Record expired, transition to `FLAT`

### 4.3: `_handle_chase_order_working_state()` (NEW)

**Purpose:** Chase bid is resting, may be repriced

**Logic:**
1. Track min/max price during TTL
2. Check if filled (`bar['low'] <= setup.entry_price`)
   - If filled → create position, transition to `IN_POSITION`
3. Check chase max-extension guard
   - `extension = (bar['close'] / setup.breakout_level) - 1`
   - If `extension > chase_max_extension` → record expired, transition to `FLAT`
4. Check if should reprice:
   - Time since last update >= `chase_reprice_interval_minutes`
   - Reprices remaining < `chase_max_reprices`
   - Calculate new chase price
   - If new price > old price (more aggressive):
     - Update `setup.entry_price`
     - Increment `setup.entry_reprices`
     - Update `setup.entry_last_update_ts`
     - Append to `setup.entry_price_history`
5. Check if chase expired (`now >= setup.chase_deadline_ts`)
   - Record expired, transition to `FLAT`
6. Check if setup expired (`now >= setup.setup_deadline_ts`)
   - Record expired, transition to `FLAT`

---

## Stage 5: Remove Deprecated Code

After verifying Phase 2.2 works:

1. Remove `EntryOrder` dataclass
2. Remove `self.entry_orders` dict
3. Remove old `_handle_pending_setup_state()`
4. Remove old `_handle_entry_order_working_state()`
5. Remove `_check_breakout_retest()` (replaced by immediate placement)
6. Remove `_place_chase_order()` (integrated into chase state handler)

---

## Stage 6: Update Engine

**File:** `backtest/engine_4h_hybrid.py`

Ensure bb_width_pct history is being forward-filled and passed correctly.

Current implementation likely already does this, but verify:
- `bb_width_pct` is calculated and forward-filled from 4h to 1m
- Passed to strategy in `indicators_4h` dict

---

## Testing Checklist

After each stage:
- [ ] Code runs without syntax errors
- [ ] Simple import test passes (`python3 -c "from backtest.strategy_4h_hybrid import Hybrid4hStrategy"`)

After Stage 4:
- [ ] 60-day backtest runs without errors
- [ ] At least one trade executes
- [ ] Retest fills work
- [ ] Chase fills work (if applicable)
- [ ] Expired setups are recorded

After Stage 5:
- [ ] 60-day results match pre-refactor (if using same config)
- [ ] 180-day results show improved frequency
- [ ] Diagnostics CSV exports correctly

---

## Estimated Timeline

- **Stage 1:** 15 minutes (update dispatcher)
- **Stage 2:** 30 minutes (implement helpers)
- **Stage 3:** 45 minutes (refactor flat state)
- **Stage 4:** 90 minutes (implement new state handlers)
- **Stage 5:** 30 minutes (remove deprecated code)
- **Stage 6:** 15 minutes (verify engine)
- **Testing:** 60 minutes

**Total: ~4 hours** of focused implementation time.

**Recommendation:** Split across 2-3 sessions with testing between stages.

---

## Current Session Decision Point

**Token Usage:** ~125K / 200K

**Options:**
1. **Continue with Stage 1** (update dispatcher) - Low risk, quick win
2. **Save and resume next session** - Start fresh with full token budget for Stages 1-4

**Recommendation:** Continue with Stage 1 now (15 mins), then save detailed plan for next session to tackle Stages 2-4.
