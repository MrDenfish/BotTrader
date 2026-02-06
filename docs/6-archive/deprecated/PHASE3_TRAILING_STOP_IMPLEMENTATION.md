# Phase 3: ATR-Based Trailing Stop Implementation

**Date:** 2025-11-29
**Session:** limit-order-smart-exits
**Status:** ✅ COMPLETE

## Overview

Implemented ATR-based trailing stop logic in `position_monitor.py` to protect profits on winning positions while allowing them to run during favorable price movements.

## Implementation Details

### File Modified
- `MarketDataManager/position_monitor.py` (lines 1-10, 221-223, 449-570)

### Key Features

1. **ATR Data Retrieval**
   - Gets ATR from `atr_pct_cache` (market_data)
   - Fallback to `atr_price_cache` calculation
   - Graceful handling when no ATR data available

2. **Per-Position State Tracking**
   - Dictionary: `self.trailing_stops[product_id]`
   - Tracks: `last_high`, `stop_price`, `last_atr`
   - Initialized on first position check

3. **Profitable Activation**
   - Only activates trailing stop when position is profitable (P&L > 0%)
   - Prevents stopping out at break-even or loss

4. **New High Tracking**
   - Updates `last_high` when current price exceeds previous high
   - Updates `last_atr` with most recent ATR value

5. **Stop Price Calculation**
   - Formula: `stop_price = last_high - (last_high × ATR × 2.0)`
   - Applies distance constraints: 1-2% from current price
   - Min stop: `current_price × (1 - 2%)` (max 2% below)
   - Max stop: `current_price × (1 - 1%)` (min 1% below)

6. **Raise-Only Logic**
   - Stop price only moves upward, never downward
   - Preserves highest stop even when price pulls back

7. **Step Size Control**
   - Updates stop when price moves by 0.5×ATR
   - OR when price makes new high
   - Prevents excessive stop adjustments

8. **Exit Trigger**
   - Returns `True` when `current_price <= stop_price`
   - Clears state after triggering exit
   - Position monitor places LIMIT sell order

## Configuration

All parameters configured in `.env`:

```env
TRAILING_STOP_ENABLED=true          # Enable/disable trailing stops
TRAILING_STOP_ATR_PERIOD=14         # ATR calculation period
TRAILING_STOP_ATR_MULT=2.0          # Stop distance: 2×ATR below high
TRAILING_STEP_ATR_MULT=0.5          # Step size: 0.5×ATR increments
TRAILING_MIN_DISTANCE_PCT=0.01      # Min 1% below current price
TRAILING_MAX_DISTANCE_PCT=0.02      # Max 2% below current price
```

## Integration

Trailing stop check integrated into `position_monitor.check_position()`:

```python
elif self.trailing_enabled:
    # Check ATR-based trailing stop logic
    trailing_exit = await self._check_trailing_stop(symbol, product_id, current_price, avg_entry_price)
    if trailing_exit:
        exit_reason = f"TRAILING_STOP (P&L: {pnl_pct:.2%})"
```

Exit priority (checked in order):
1. Hard Stop (-5%) - emergency exit
2. Stop Loss (-2.5%) - regular stop
3. Take Profit (+3.5%) - profit target
4. Trailing Stop - dynamic ATR-based

## Testing

### Unit Tests: `test_trailing_stop.py`

**6/6 Tests Passing:**

1. ✅ **Initialization**: State initialized with ATR data
2. ✅ **Activation**: Stop activated when position becomes profitable
3. ✅ **Raises on New High**: Stop raised when price makes new high
4. ✅ **Never Lowers**: Stop remains fixed when price drops
5. ✅ **Triggers Exit**: Exit triggered when price <= stop_price
6. ✅ **No ATR Handling**: Gracefully skips when no ATR data

### Example Test Scenario

```
Entry:     $49,000.00
Price:     $52,000.00  (+6.1% profit)
ATR:       2% ($1,040)
Stop:      $50,960.00  (= $52,000 - 2×$1,040)
Trigger:   Price drops to $50,860.00
Result:    ✅ Exit triggered, profit protected at ~3.6%
```

## Behavioral Examples

### Scenario 1: Profitable Run with Trailing Protection

```
T0:  Entry at $100, not profitable yet
T1:  Price $102 (+2%), stop activates at $99.96 (2% ATR = $2)
T2:  Price $105 (+5%), stop raised to $102.90
T3:  Price $108 (+8%), stop raised to $105.84
T4:  Price drops to $105.50, stop remains $105.84, monitoring continues
T5:  Price drops to $105.00 <= $105.84, EXIT triggered
     Final P&L: +5.0% (protected from $108 high)
```

### Scenario 2: Choppy Movement

```
T0:  Entry at $100
T1:  Price $101, stop not active (only +1%)
T2:  Price $103 (+3%), stop activates at $100.94
T3:  Price $102, stop remains $100.94 (never lowers)
T4:  Price $104, stop raised to $101.92
T5:  Price $103, stop remains $101.92
     Continues monitoring...
```

### Scenario 3: No ATR Data

```
T0:  Entry at $100, no ATR in cache
     Result: Trailing stop skipped, only TP/SL thresholds active
```

## Logs

Trailing stop actions logged with `[TRAILING]` prefix:

```
[TRAILING] BTC-USD: Initialized trailing stop state | Entry: $49000.00, Current: $50000.00, ATR: 2.00%
[TRAILING] BTC-USD: Activated trailing stop | Stop: $49000.00, High: $50000.00, Current: $50000.00, ATR: 2.00%
[TRAILING] BTC-USD: Raised stop | Old: $49000.00 → New: $50960.00, High: $52000.00, Current: $52000.00
[TRAILING] BTC-USD: STOP HIT! | Current: $50860.00 ≤ Stop: $50960.00, Entry: $49000.00, High: $52000.00
```

## Benefits

1. **Profit Protection**: Locks in gains as price moves favorably
2. **Trend Following**: Allows winners to run during uptrends
3. **Adaptive**: Stop distance adjusts based on volatility (ATR)
4. **Risk Management**: Prevents giving back all profits during reversals
5. **Lower Fees**: Still uses LIMIT orders (0.30% vs 0.55% taker)

## Production Readiness

- ✅ Code implemented and tested
- ✅ Configuration already in `.env`
- ✅ Integration with position_monitor complete
- ✅ Unit tests passing (6/6)
- ✅ Error handling implemented
- ✅ Logging for monitoring
- ✅ State management (per-position tracking)

## Next Steps (Phase 4)

1. Deploy to production (restart containers)
2. Monitor logs for trailing stop behavior
3. Track exit reasons distribution (TP/SL/TRAILING)
4. Measure actual R:R ratio vs target (1:1.4)
5. Validate win rate (target >42%)

## Files Changed

- `MarketDataManager/position_monitor.py` - Trailing stop implementation
- `test_trailing_stop.py` - Unit tests (new file)
- `.claude/sessions/2025-11-23-1129-limit-order-smart-exits.md` - Session tracking
- `docs/PHASE3_TRAILING_STOP_IMPLEMENTATION.md` - This document

## Commit Message

```
feat: Implement ATR-based trailing stops for position monitoring

- Add _check_trailing_stop() method with full ATR-based logic
- Track per-position state (last_high, stop_price, last_atr)
- Activate trailing stops only on profitable positions
- Enforce raise-only constraint (stops never lower)
- Apply 1-2% distance constraints from current price
- Update stops on 0.5×ATR moves or new highs
- Clear state after exit trigger
- Add comprehensive unit tests (6/6 passing)

Phase 3 of limit-order-smart-exits session complete.
```
