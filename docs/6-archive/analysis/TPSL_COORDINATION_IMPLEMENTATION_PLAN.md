# TP/SL System Coordination Implementation Plan

**Date:** 2025-12-03
**Branch:** `feature/tpsl-coordination`
**Goal:** Add coordination between 3 stop loss systems to prevent conflicts while maintaining defense-in-depth protection

---

## Background

The bot currently has 3 stop loss systems that provide redundant protection:

1. **Exchange Bracket Orders** (webhook_order_manager.py) - OCO orders placed at entry
2. **Position Monitor** (position_monitor.py) - Active monitoring every 30s
3. **Emergency Hard Stop** (position_monitor.py) - Market exit as last resort

**Problem:** These systems currently fight each other instead of coordinating.

**Solution:** Add awareness and coordination between systems while keeping all 3 active.

---

## Architecture Philosophy: Defense-in-Depth

Keep all 3 systems as layers of protection:

```
Layer 1: Exchange Bracket Orders (Primary)
├─ Survives bot crashes
├─ Lower fees (0.60% maker)
├─ ATR-based volatility adjustment
└─ Set once at entry

Layer 2: Position Monitor (Secondary/Override)
├─ Smart exits (signals, trailing)
├─ Can override brackets for better exits
├─ Catches positions without brackets
└─ Active every 30 seconds

Layer 3: Emergency Hard Stop (Last Resort)
├─ Only triggers if Layers 1 & 2 fail
├─ Market order (expensive but necessary)
├─ Prevents catastrophic losses
└─ Set 1-2% wider than primary stops
```

---

## Implementation Tasks

### 1. Add Bracket Order Tracking

**File:** `webhook/webhook_order_manager.py`

**Task:** Store bracket order IDs in shared state when placing OCO orders

```python
# After placing bracket order, store IDs:
self.shared_data_manager.order_management['bracket_orders'][product_id] = {
    'entry_order_id': entry_id,
    'stop_order_id': stop_id,
    'tp_order_id': tp_id,
    'stop_price': stop_price,
    'tp_price': tp_price,
    'entry_time': datetime.now(),
    'status': 'active'
}
```

**Location:** After line 450 in `build_order_data()` method

---

### 2. Position Monitor: Check for Existing Brackets

**File:** `MarketDataManager/position_monitor.py`

**Task:** Before placing exit order, check if exchange bracket already exists

```python
async def _has_active_bracket_order(self, product_id: str) -> dict:
    """
    Check if position has active bracket orders on exchange.

    Returns:
        dict with 'has_bracket', 'stop_price', 'tp_price' or empty dict
    """
    try:
        bracket_orders = self.shared_data_manager.order_management.get('bracket_orders', {})
        bracket = bracket_orders.get(product_id)

        if not bracket:
            return {}

        # Verify bracket still active on exchange
        stop_id = bracket.get('stop_order_id')
        if stop_id:
            # Query exchange to confirm order still open
            order_status = await self._check_order_status(stop_id)
            if order_status in {'open', 'OPEN', 'new', 'NEW'}:
                return {
                    'has_bracket': True,
                    'stop_price': bracket['stop_price'],
                    'tp_price': bracket['tp_price']
                }

        # Bracket no longer active, clean up
        del bracket_orders[product_id]
        return {}

    except Exception as e:
        self.logger.debug(f"Error checking bracket for {product_id}: {e}")
        return {}
```

**Location:** After line 318 in `position_monitor.py`

---

### 3. Coordination Logic in Exit Decision

**File:** `MarketDataManager/position_monitor.py`

**Task:** Add bracket awareness to exit decision logic

```python
# After line 218 (after checking for open sell orders):

# Check if position has active bracket orders
bracket_info = await self._has_active_bracket_order(product_id)
has_bracket = bracket_info.get('has_bracket', False)

if has_bracket:
    self.logger.debug(
        f"[POS_MONITOR] {product_id} has active bracket "
        f"(SL: ${bracket_info['stop_price']:.4f}, TP: ${bracket_info['tp_price']:.4f})"
    )
```

**Then modify exit logic (line 225-270):**

```python
# Phase 5: Coordinated Exit Priority Logic
# Priority: Hard Stop → Soft Stop → (Check Bracket → Signal/Trailing)

exit_reason = None
use_market_order = False
override_bracket = False  # New flag

# 1. EMERGENCY HARD STOP (always override bracket)
if pnl_pct <= -self.hard_stop_pct:
    exit_reason = f"HARD_STOP (P&L: {pnl_pct:.2%})"
    use_market_order = True
    override_bracket = True

# 2. SOFT STOP (only if no bracket, or bracket far away)
elif pnl_pct <= -self.max_loss_pct:
    if has_bracket:
        # Bracket exists - check if it's at same level
        bracket_sl_pct = (bracket_info['stop_price'] - avg_entry_price) / avg_entry_price

        if abs(bracket_sl_pct - (-self.max_loss_pct)) < 0.005:  # Within 0.5%
            # Bracket will handle it, don't place redundant order
            self.logger.debug(
                f"[POS_MONITOR] {product_id} SOFT_STOP level matches bracket "
                f"(bracket: {bracket_sl_pct:.2%}, monitor: {-self.max_loss_pct:.2%}), "
                f"letting bracket handle exit"
            )
            return  # Let bracket do its job
        else:
            # Bracket exists but at different level - log warning
            self.logger.warning(
                f"[POS_MONITOR] {product_id} SOFT_STOP mismatch! "
                f"Bracket SL: {bracket_sl_pct:.2%}, Monitor SL: {-self.max_loss_pct:.2%}"
            )
            exit_reason = f"SOFT_STOP (P&L: {pnl_pct:.2%}, overriding bracket)"
            override_bracket = True
    else:
        # No bracket - position monitor handles exit
        exit_reason = f"SOFT_STOP (P&L: {pnl_pct:.2%}, no bracket)"

# 3. PROFIT MANAGEMENT
elif self.trailing_enabled:
    # Trailing stop logic (existing code)
    # Only override bracket if trailing triggers
    ...

elif not self.trailing_enabled and pnl_pct >= self.min_profit_pct:
    if has_bracket:
        # Check if bracket TP will handle it
        bracket_tp_pct = (bracket_info['tp_price'] - avg_entry_price) / avg_entry_price

        if abs(bracket_tp_pct - self.min_profit_pct) < 0.005:  # Within 0.5%
            self.logger.debug(
                f"[POS_MONITOR] {product_id} TP level matches bracket, "
                f"letting bracket handle exit"
            )
            return
        else:
            exit_reason = f"TAKE_PROFIT (P&L: {pnl_pct:.2%}, overriding bracket)"
            override_bracket = True
    else:
        exit_reason = f"TAKE_PROFIT (P&L: {pnl_pct:.2%}, no bracket)"

if not exit_reason:
    return  # No exit condition met

# Log coordination decision
if override_bracket and has_bracket:
    self.logger.warning(
        f"[POS_MONITOR] {product_id} overriding bracket order: {exit_reason}"
    )
elif has_bracket:
    self.logger.info(
        f"[POS_MONITOR] {product_id} deferring to bracket order: {exit_reason}"
    )
    return  # Let bracket handle it
else:
    self.logger.info(
        f"[POS_MONITOR] {product_id} placing exit (no bracket): {exit_reason}"
    )

# Place exit order...
```

---

### 4. Add Exit Source Logging

**File:** `MarketDataManager/position_monitor.py`

**Task:** Log which system actually exited the trade

```python
async def _place_exit_order(self, symbol, product_id, size, current_price, reason, use_market=False):
    """Enhanced with exit source tracking"""

    # Existing exit order placement code...

    # After successful exit, log source
    self.logger.info(
        f"[EXIT_SOURCE] {product_id} | Reason: {reason} | "
        f"Source: {'POSITION_MONITOR' if not use_market else 'EMERGENCY_STOP'} | "
        f"Order Type: {'MARKET' if use_market else 'LIMIT'}"
    )

    # Store exit metadata for reporting
    exit_metadata = {
        'product_id': product_id,
        'exit_source': 'POSITION_MONITOR' if not use_market else 'EMERGENCY_STOP',
        'exit_reason': reason,
        'exit_type': 'MARKET' if use_market else 'LIMIT',
        'exit_time': datetime.now(),
        'exit_price': current_price
    }

    # Store in shared state for daily report
    if 'exit_tracking' not in self.shared_data_manager.order_management:
        self.shared_data_manager.order_management['exit_tracking'] = []

    self.shared_data_manager.order_management['exit_tracking'].append(exit_metadata)
```

---

### 5. Cleanup Bracket State on Fill

**File:** `webhook/webhook_order_manager.py` or websocket fill handler

**Task:** Remove bracket from tracking when filled

```python
# When bracket order fills (stop or TP):
def _handle_bracket_fill(self, order_id, product_id):
    """Clean up bracket tracking when order fills"""
    bracket_orders = self.shared_data_manager.order_management.get('bracket_orders', {})

    if product_id in bracket_orders:
        bracket = bracket_orders[product_id]

        # Log which part of bracket filled
        if order_id == bracket['stop_order_id']:
            self.logger.info(f"[EXIT_SOURCE] {product_id} | Reason: BRACKET_STOP | Source: EXCHANGE_BRACKET | Order Type: LIMIT")
        elif order_id == bracket['tp_order_id']:
            self.logger.info(f"[EXIT_SOURCE] {product_id} | Reason: BRACKET_TP | Source: EXCHANGE_BRACKET | Order Type: LIMIT")

        # Clean up
        del bracket_orders[product_id]
        self.logger.debug(f"[BRACKET] Removed bracket tracking for {product_id}")
```

---

### 6. Add Configuration Validation

**File:** New file `Config/tpsl_validator.py`

**Task:** Validate TP/SL configuration on startup

```python
"""
TP/SL Configuration Validator
Ensures all stop loss systems are aligned to prevent conflicts.
"""

import os
from decimal import Decimal
import logging

logger = logging.getLogger(__name__)

def validate_tpsl_alignment():
    """
    Validate that TP/SL configuration is aligned across all 3 systems.
    Logs warnings if misalignment detected.
    """

    # Read configuration
    atr_multiplier = float(os.getenv('ATR_MULTIPLIER_STOP', 1.8))
    stop_min_pct = float(os.getenv('STOP_MIN_PCT', 0.012))
    max_loss_pct = float(os.getenv('MAX_LOSS_PCT', 0.025))
    hard_stop_pct = float(os.getenv('HARD_STOP_PCT', 0.05))

    # Estimate typical ATR stop (assume ATR ~2%)
    typical_atr = 0.02
    estimated_atr_stop = max(atr_multiplier * typical_atr, stop_min_pct)

    # Add typical cushions (spread + fee)
    estimated_atr_stop += 0.0015 + 0.0055  # SPREAD_CUSHION + TAKER_FEE

    # Validation checks
    issues = []

    # Check 1: MAX_LOSS_PCT should match ATR stop (within 0.5%)
    if abs(max_loss_pct - estimated_atr_stop) > 0.005:
        issues.append(
            f"⚠️  MAX_LOSS_PCT ({max_loss_pct:.2%}) doesn't match estimated ATR stop "
            f"({estimated_atr_stop:.2%}). This will cause conflicts!"
        )

    # Check 2: HARD_STOP should be wider than MAX_LOSS
    if hard_stop_pct <= max_loss_pct:
        issues.append(
            f"⚠️  HARD_STOP_PCT ({hard_stop_pct:.2%}) should be wider than "
            f"MAX_LOSS_PCT ({max_loss_pct:.2%})"
        )

    # Check 3: HARD_STOP should be 1-2% wider (not too far)
    gap = hard_stop_pct - max_loss_pct
    if gap > 0.025:
        issues.append(
            f"⚠️  HARD_STOP_PCT gap is large ({gap:.2%}). Consider tightening to "
            f"prevent large losses between SOFT and HARD stops."
        )

    # Log results
    if issues:
        logger.warning("=" * 60)
        logger.warning("TP/SL CONFIGURATION ISSUES DETECTED:")
        for issue in issues:
            logger.warning(issue)
        logger.warning("=" * 60)
        logger.warning("Review docs/TPSL_CONFIGURATION_AUDIT.md for guidance")
        logger.warning("=" * 60)
    else:
        logger.info("✅ TP/SL configuration validated - all systems aligned")

    return len(issues) == 0

# Run on import
validate_tpsl_alignment()
```

**Import this in `webhook/webhook_order_manager.py` startup**

---

### 7. Update Daily Report

**File:** `botreport/aws_daily_report.py`

**Task:** Add exit source breakdown to report

```python
def generate_exit_source_stats(conn, hours_back=24):
    """Generate statistics on which system exited trades"""

    # Query exit tracking data
    # This requires storing exit_source in database or reading from logs

    exit_sources = {
        'EXCHANGE_BRACKET_STOP': 0,
        'EXCHANGE_BRACKET_TP': 0,
        'POSITION_MONITOR_SOFT': 0,
        'POSITION_MONITOR_HARD': 0,
        'POSITION_MONITOR_TRAILING': 0,
        'POSITION_MONITOR_SIGNAL': 0,
        'UNKNOWN': 0
    }

    # Count exits by source...

    return f"""
    <h3>Exit Source Breakdown</h3>
    <table>
      <tr><th>Exit Source</th><th>Count</th><th>%</th></tr>
      <tr><td>Exchange Bracket SL</td><td>{exit_sources['EXCHANGE_BRACKET_STOP']}</td><td>...</td></tr>
      <tr><td>Exchange Bracket TP</td><td>{exit_sources['EXCHANGE_BRACKET_TP']}</td><td>...</td></tr>
      <tr><td>Position Monitor Soft Stop</td><td>{exit_sources['POSITION_MONITOR_SOFT']}</td><td>...</td></tr>
      <tr><td>Position Monitor Hard Stop</td><td>{exit_sources['POSITION_MONITOR_HARD']}</td><td>...</td></tr>
      <tr><td>Trailing Stop</td><td>{exit_sources['POSITION_MONITOR_TRAILING']}</td><td>...</td></tr>
    </table>
    """
```

---

## Testing Plan

### Phase 1: Code Integration (Day 1)
- [ ] Create branch `feature/tpsl-coordination`
- [ ] Implement changes 1-6 above
- [ ] Test locally with paper trading
- [ ] Verify logs show coordination decisions

### Phase 2: Monitoring (Day 2-3)
- [ ] Deploy to production
- [ ] Monitor logs for coordination messages
- [ ] Verify no double-exits occurring
- [ ] Check exit source tracking in logs

### Phase 3: Analysis (Day 4-7)
- [ ] Review daily reports with exit source breakdown
- [ ] Analyze which system exits most trades
- [ ] Tune MAX_LOSS_PCT if needed
- [ ] Document performance improvement

---

## Expected Outcomes

### Before Coordination:
- 20.2% win rate
- Profit factor: 0.06
- Avg loss: $-21.93
- Conflicts between systems

### After Coordination:
- 40-50% win rate (target)
- Profit factor: 0.4-0.6 (target)
- Avg loss: $-15 (target)
- No conflicts, logs show cooperation
- Clear attribution of which system exits trades

---

## Configuration Summary

**Aligned Configuration** (already set in .env):

```bash
# ATR-based entry stops
STOP_MODE=atr
ATR_MULTIPLIER_STOP=1.8
STOP_MIN_PCT=0.012

# Position monitor (ALIGNED with ATR)
MAX_LOSS_PCT=0.045        # Matches ATR calculation
MIN_PROFIT_PCT=0.035      # +3.5% TP
HARD_STOP_PCT=0.06        # Emergency fallback

# Phase 5 (disabled until proven)
SIGNAL_EXIT_ENABLED=false
TRAILING_STOP_ENABLED=true
```

---

## Files to Modify

1. `webhook/webhook_order_manager.py` - Add bracket tracking
2. `MarketDataManager/position_monitor.py` - Add coordination logic
3. `Config/tpsl_validator.py` - NEW - Validation on startup
4. `botreport/aws_daily_report.py` - Add exit source stats
5. WebSocket fill handler - Clean up bracket state on fill

---

## Success Criteria

- ✅ No duplicate exit orders for same position
- ✅ Clear logs showing which system makes exit decision
- ✅ Win rate improves from 20% to 40%+
- ✅ Exit source stats in daily report
- ✅ Configuration validator runs on startup
- ✅ All 3 systems remain active (defense-in-depth preserved)

---

## Notes for Next Session

**Branch to create:** `feature/tpsl-coordination`

**Start with:** Implementing bracket order tracking in webhook_order_manager.py

**Key principle:** Don't remove any systems - add coordination so they work together instead of fighting.

**Testing:** Use paper trading first to verify coordination before deploying to production.
