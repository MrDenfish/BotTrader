# Expired Setup Diagnostic - Implementation Guide

## Changes Required

### 1. Add price tracking in `_handle_pending_setup_state()` (line ~368)

```python
def _handle_pending_setup_state(
    self,
    symbol: str,
    bar: pd.Series,
    indicators_4h: dict
) -> Optional[dict]:
    """Check if setup triggers entry"""

    setup = self.pending_setups[symbol]

    # Phase 2.1c: Track min/max price during TTL
    setup.min_low_during_ttl = min(setup.min_low_during_ttl, bar['low'])
    setup.max_high_during_ttl = max(setup.max_high_during_ttl, bar['high'])

    # Check expiry
    if setup.expired:
        self._record_expired_setup(symbol, setup, reason="SETUP_TTL_EXPIRED")
        self.expired_setup_count += 1
        self.state[symbol] = "FLAT"
        del self.pending_setups[symbol]
        return {"action": "SETUP_EXPIRED"}

    # ... rest of method
```

### 2. Add price tracking in `_handle_entry_order_working_state()` (line ~413)

```python
def _handle_entry_order_working_state(
    self,
    symbol: str,
    bar: pd.Series,
    indicators_4h: dict
) -> Optional[dict]:
    """Check if entry order filled"""

    order = self.entry_orders[symbol]
    setup = self.pending_setups[symbol]

    # Phase 2.1c: Track min/max price during TTL
    setup.min_low_during_ttl = min(setup.min_low_during_ttl, bar['low'])
    setup.max_high_during_ttl = max(setup.max_high_during_ttl, bar['high'])

    # Check order expiry
    if order.expired:
        del self.entry_orders[symbol]
        # Setup remains pending, can try again
        self.state[symbol] = "PENDING_SETUP"
        return {"action": "ENTRY_ORDER_EXPIRED"}

    # Check if setup expired
    if setup.expired:
        self._record_expired_setup(symbol, setup, reason="SETUP_TTL_EXPIRED_WITH_ORDER", order=order)
        self.expired_setup_count += 1
        del self.entry_orders[symbol]
        del self.pending_setups[symbol]
        self.state[symbol] = "FLAT"
        return {"action": "SETUP_EXPIRED_WHILE_ORDER_WORKING"}

    # ... rest of method (including chase expiry check)
```

### 3. Update chase expiry to record diagnostics (line ~441)

```python
# Phase 2: Check chase fill (if chase order active)
if setup.chase_attempted and order.chase_price is not None:
    # Check chase TTL expiry first
    if bar.name >= setup.chase_deadline_ts:
        # Chase expired
        self._record_expired_setup(symbol, setup, reason="CHASE_TTL_EXPIRED", order=order)
        self.expired_setup_count += 1
        del self.entry_orders[symbol]
        del self.pending_setups[symbol]
        self.state[symbol] = "FLAT"
        return {"action": "CHASE_EXPIRED"}
```

### 4. Add helper method `_record_expired_setup()` (add after `_place_chase_order`)

```python
def _record_expired_setup(
    self,
    symbol: str,
    setup: Setup,
    reason: str,
    order: Optional[EntryOrder] = None
) -> None:
    """
    Record diagnostics for an expired setup.

    Tracks why setups fail to fill and how close price came to filling.
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

    # Add entry prices if order was placed
    if order is not None:
        expired.retest_limit_price = order.limit_price
        expired.chase_limit_price = order.chase_price

        # Calculate distance-to-fill (retest)
        if order.limit_price is not None and setup.min_low_during_ttl != float('inf'):
            gap = order.limit_price - setup.min_low_during_ttl
            expired.retest_gap_pct = (gap / setup.breakout_level) * 100

        # Calculate distance-to-fill (chase)
        if order.chase_price is not None and setup.min_low_during_ttl != float('inf'):
            gap = order.chase_price - setup.min_low_during_ttl
            expired.chase_gap_pct = (gap / setup.breakout_level) * 100

    self.expired_setups.append(expired)
```

### 5. Update `_place_chase_order` to record when blocked by hardening

In `_place_chase_order()` where we return early due to max-extension or expansion checks:

```python
# Phase 2.1: Max-extension guard
extension = (bar['close'] / setup.breakout_level) - 1.0
if extension > self.config.chase_max_extension:
    # Price has run too far - don't chase runaway moves
    # Note: Don't record as expired here, will be recorded when setup/chase TTL expires
    self.expired_setup_count += 1
    return

# Phase 2.1: Expansion confirmation
if self.config.require_expansion_for_chase:
    # ... expansion check ...
    if not is_expanding:
        # BB not expanding - don't chase
        # Note: Don't record as expired here, will be recorded when setup/chase TTL expires
        self.expired_setup_count += 1
        return
```

### 6. Add export method in engine (`backtest/engine_4h_hybrid.py`)

```python
def export_expired_setups(self, filename: str = "expired_setups.csv"):
    """Export expired setup diagnostics to CSV"""

    if not self.strategy.expired_setups:
        print("No expired setups to export")
        return

    expired_data = []
    for exp in self.strategy.expired_setups:
        expired_data.append({
            'symbol': exp.symbol,
            'setup_time': exp.setup_time,
            'setup_type': exp.setup_type,
            'breakout_level': exp.breakout_level,
            'retest_limit_price': exp.retest_limit_price,
            'chase_limit_price': exp.chase_limit_price,
            'min_low_during_ttl': exp.min_low_during_ttl,
            'max_high_during_ttl': exp.max_high_during_ttl,
            'retest_gap_pct': exp.retest_gap_pct,
            'chase_gap_pct': exp.chase_gap_pct,
            'expiration_reason': exp.expiration_reason,
            'bb_width_pct_at_setup': exp.bb_width_pct_at_setup
        })

    df_expired = pd.DataFrame(expired_data)
    df_expired.to_csv(filename, index=False)
    print(f"📊 Expired setups exported: {filename}")
```

### 7. Update test script to export expired setups

In `test_4h_180d_validation.py`, after each backtest:

```python
engine_baseline.export_expired_setups("expired_setups_baseline_180d.csv")
engine_hardened.export_expired_setups("expired_setups_hardened_180d.csv")
```

## Usage

After implementation, the CSV will show for each expired setup:
- **retest_gap_pct**: How far below the retest bid did price go?
  - Negative = price crossed the bid but didn't fill (fill logic issue)
  - Small positive (< 0.1%) = bidding slightly too low
  - Large positive (> 0.5%) = bidding way too low / price never came close
- **expiration_reason**: Why it expired (SETUP_TTL, CHASE_TTL, etc.)
- **bb_width_pct_at_setup**: Was compression too strict?

This single diagnostic tells us exactly what to fix!
