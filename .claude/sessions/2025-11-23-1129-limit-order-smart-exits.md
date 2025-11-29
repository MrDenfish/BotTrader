# LIMIT Order Smart Exits Implementation

**Started:** 2025-11-23 11:29

## Session Overview

Implementing a smart LIMIT-only order strategy with P&L-based exits and ATR-based trailing stops to replace the current TP/SL bracket system that's losing money due to poor risk/reward ratio.

## Background

Previous analysis revealed:
- Current TP/SL orders have R:R of 1:0.71 (losing proposition)
- Stop loss too wide: ~-3.5% (1.8×ATR + fees + spread) vs take profit +2.5%
- Higher fees: 0.85% round-trip vs 0.60% with LIMIT-only
- Strategy mismatch: buy_sell_matrix identifies swing trades, but TP/SL forces premature exits

## Goals

### Phase 1: LIMIT-Only Orders ✅ COMPLETE (100%)
- [x] Switch BUY orders to LIMIT-only (no TP/SL brackets)
- [x] Verify all new buy orders are simple LIMIT (no attached_order_configuration)
- [x] Test order placement and fills

### Phase 2: Position Monitoring ✅ COMPLETE (100%)
- [x] Create position_monitor.py with P&L threshold logic
- [x] Implement stop loss threshold: -2.5%
- [x] Implement take profit threshold: +3.5%
- [x] Implement hard stop (emergency exit): -5%
- [x] Add LIMIT sell placement logic
- [x] Integrate with asset_monitor sweep cycle

### Phase 3: ATR-Based Trailing Stops ✅ COMPLETE (100%)
- [x] Implement ATR calculation (1-4 hour timeframe, period=14)
- [x] ATR infrastructure and caching
- [x] Add distance limits: 1-2% from current price
- [x] Track per-position state: last_high, trail_stop_price, last_atr
- [x] Add trailing stop logic: 2×ATR distance
- [x] Add step logic: 0.5×ATR increments
- [x] Enforce constraints: only raise stop, never lower
- [x] Unit tests: 6 tests passing

### Phase 4: Testing & Validation 🔄 IN PROGRESS (80%)
- [x] Deploy to production
- [x] Monitor for 24-48 hours
- [x] Verify: Lower fees (0.60% vs 0.85%)
- [x] Multiple bug fixes and refinements (19+ commits)
- [ ] Verify: Better R:R (1:1.4 vs 1:0.71)
- [ ] Track: Win rate (target >42% for break-even)

### Future (Phase 5): Buy/Sell Matrix Integration ❌ NOT STARTED (0%)
- [ ] Query buy_sell_matrix for current signal on each position check
- [ ] Exit immediately if matrix flips to SELL and position profitable
- [ ] Use smart exit logic if matrix says SELL but position at loss

## Configuration Parameters

```env
# Position Exit Thresholds
MAX_LOSS_PCT=0.025          # Stop loss at -2.5%
MIN_PROFIT_PCT=0.035        # Take profit at +3.5%
HARD_STOP_PCT=0.05          # Emergency exit at -5%

# Trailing Stop Configuration
TRAILING_STOP_ENABLED=true
TRAILING_STOP_TIMEFRAME=1h  # 1-4 hour candles
TRAILING_STOP_ATR_PERIOD=14
TRAILING_STOP_ATR_MULT=2.0  # Trail at 2×ATR distance
TRAILING_STEP_ATR_MULT=0.5  # Adjust every 0.5×ATR move
TRAILING_MIN_DISTANCE_PCT=0.01  # Don't trail closer than 1%
TRAILING_MAX_DISTANCE_PCT=0.02  # Don't trail further than 2%

# Position Monitoring
POSITION_CHECK_INTERVAL=30  # Check every 30 seconds
```

## Progress

### Phase 1: LIMIT-Only Orders ✅ COMPLETE

**File:** `webhook/webhook_order_manager.py:923-935`

```python
def order_type_to_use(self, side, order_data):
    if order_data.trigger and order_data.trigger.get("trigger") == "passive_buy":
        validation_result = 'limit'
        return validation_result
    if side == 'buy':
        validation_result = 'limit'  # Changed from 'tp_sl' - use LIMIT-only for lower fees
        return validation_result
    elif side == 'sell':
        validation_result = 'limit'
        return validation_result
```

**Impact:**
- Reduces fees from ~0.85% (TP/SL) to ~0.60% (LIMIT-only)
- No TP/SL brackets attached to BUY orders
- All orders now use maker fees (0.30%) instead of taker fees (0.55%)

---

### Phase 2: Position Monitoring ✅ COMPLETE

**File:** `MarketDataManager/position_monitor.py` (478 lines)

**Implementation:**
- P&L calculation from `unrealized_pnl` field
- Three threshold levels:
  - Hard Stop (-5%): Places limit 0.1% below bid for emergency exit
  - Stop Loss (-2.5%): Places limit 0.01% above bid
  - Take Profit (+3.5%): Places limit 0.01% above bid
- HODL asset protection (respects HODL config)
- Open sell order detection (prevents duplicates)
- Existing order cancellation before new placement
- Fallback pricing when bid/ask unavailable

**Integration:** `asset_monitor.py:60-67, 1376-1378`
```python
# Initialize position monitor for smart LIMIT exits
self.position_monitor = PositionMonitor(
    shared_data_manager=shared_data_manager,
    trade_order_manager=trade_order_manager,
    shared_utils_precision=shared_utils_precision,
    logger=self.logger
)

# Called in sweep_positions_for_exits (runs every 3 seconds)
await self.position_monitor.check_positions()
```

**LIMIT Sell Placement Logic:** `position_monitor.py:329-447`
- Uses bid/ask spread for optimal pricing
- Precision-adjusted sizing for safe exchange submission
- Cancels blocking orders before placement
- Emergency exits use 0.1% buffer for fast fills
- Normal exits use 0.01% buffer for better pricing

---

### Phase 3: ATR-Based Trailing Stops ⚠️ PARTIAL (70% Complete)

**What's Done:**

1. **ATR Calculation** - `profit_data_manager.py:37-53`
```python
def _atr_pct_from_ohlcv(ohlcv: list | None, entry_price: Decimal, period: int = 14) -> Decimal | None:
    """Calculate ATR% from OHLCV data with period-based True Range averaging."""
    # ... calculates ATR as percentage of entry price ...
    atr = sum(trs) / Decimal(len(trs))
    return atr / entry_price
```

2. **ATR in Order Building** - `webhook_order_manager.py:195-216, 301-316`
- ATR cached per trading pair
- Used in stop loss calculations
- Integrated with order data structure

3. **ATR-Based Stop Loss** - `webhook_order_manager.py:218-238`
```python
if mode == "atr":
    atr_mult = self._get_env_pct("ATR_MULTIPLIER_STOP", 1.8)
    min_pct = self._get_env_pct("STOP_MIN_PCT", 0.012)
    atr_pct = self._compute_atr_pct_from_ohlcv(ohlcv, entry_price) or Decimal("0")
    base_pct = max(min_pct, atr_pct * atr_mult)
```

4. **Configuration** - All environment variables set:
```env
ATR_MULTIPLIER_STOP=1.8
STOP_MIN_PCT=0.012
TRAILING_STOP_ENABLED=true
TRAILING_STOP_ATR_PERIOD=14
TRAILING_STOP_ATR_MULT=2.0
TRAILING_STEP_ATR_MULT=0.5
TRAILING_MIN_DISTANCE_PCT=0.01
TRAILING_MAX_DISTANCE_PCT=0.02
```

5. **Per-Position State Structure** - `position_monitor.py:41`
```python
self.trailing_stops = {}  # {symbol: {last_high, stop_price, last_atr}}
```

**Trailing Stop Decision Logic** - `position_monitor.py:449-570` ✅ COMPLETE

**Key Features Implemented:**
1. **ATR Data Retrieval**: Gets ATR from `atr_pct_cache` or calculates from `atr_price_cache`
2. **State Initialization**: Tracks per-position state in `self.trailing_stops` dict
3. **Profitable Activation**: Only activates trailing stop when position is profitable
4. **New High Tracking**: Updates `last_high` when price makes new peaks
5. **Stop Calculation**: `stop_price = last_high - (2 × ATR)`
6. **Distance Constraints**: Enforces 1-2% limits from current price
7. **Raise-Only Logic**: Only updates stop upward (never lowers)
8. **Step Size**: Updates when price moves 0.5×ATR or makes new high
9. **Exit Trigger**: Returns `True` when `current_price <= stop_price`
10. **State Cleanup**: Clears trailing stop state after trigger

**Test Coverage:** `test_trailing_stop.py` - 6/6 tests passing
- Initialization with ATR data
- Activation when position becomes profitable
- Stop raises on new highs
- Stop never lowers on price drops
- Exit triggers when stop is hit
- Graceful handling when no ATR data

**Status:** Fully implemented and tested.

---

### Phase 4: Testing & Validation 🔄 IN PROGRESS

**Deployment:**
- ✅ Position monitor integrated and running in production
- ✅ Running in asset_monitor sweep cycle (every 3 seconds)
- ✅ 19+ commits with bug fixes and refinements

**Key Fixes Applied:**
- Commit `842d183`: Fetch avg_entry_price dynamically in position_monitor
- Commit `52b01a0`: Access bid_ask_spread via market_data dict
- Commit `b9ca0fc`: Set order_amount_crypto for position monitor exit orders
- Commit `e1491cb`: Check available_to_trade_crypto before REARM_OCO

**Monitoring:**
- ✅ Email reports enhanced (commit `9e7904d`)
- ✅ Structured logging for diagnostics
- 🔄 Ongoing performance monitoring

---

### Phase 5: Buy/Sell Matrix Integration ❌ NOT STARTED

**Required Work:**
- Query `signal_manager.py` for current matrix signal on each position check
- Exit logic when matrix flips to SELL:
  - If position profitable → exit immediately
  - If position at loss → apply threshold-based logic

**Current Exit Triggers:**
Position exits currently triggered ONLY by P&L thresholds:
- Stop loss: -2.5%
- Take profit: +3.5%
- Hard stop: -5%

No consideration for active market signals yet.

---

## Implementation Summary

**Overall Progress: ~90% Complete**

| Phase | Status | Completion | Key Files |
|-------|--------|-----------|-----------|
| 1: LIMIT-Only Orders | Complete | 100% | `webhook_order_manager.py:923-935` |
| 2: Position Monitoring | Complete | 100% | `position_monitor.py`, `asset_monitor.py:1376-1378` |
| 3: ATR-Based Trailing | Complete | 100% | `profit_data_manager.py:37-53`, `position_monitor.py:449-570` |
| 4: Testing & Validation | In Progress | 80% | `test_trailing_stop.py` (6/6 tests), production deployment |
| 5: Matrix Integration | Not Started | 0% | Would require `signal_manager.py` integration |

## Notes

- Original TP/SL logic preserved in git history (commit: 6c4f433)
- CentralConfig is Singleton - config changes require `docker-compose down && up -d`
- Position monitoring runs in asset_monitor sweep cycle (every 3 seconds)
- LIMIT orders use maker fees (0.30%) vs taker fees (0.55%) for TP/SL exits
- Fee savings: 0.60% total vs 0.85% with TP/SL brackets

## Next Steps

**Phase 3: ✅ COMPLETE**
All trailing stop functionality implemented and tested.

**Phase 4: Testing & Validation (Remaining)**
1. Deploy updated `position_monitor.py` to production
2. Enable `TRAILING_STOP_ENABLED=true` in production `.env`
3. Monitor trailing stop behavior in production logs
4. Track performance metrics:
   - Win rate (target >42% for break-even)
   - Actual R:R vs target (1:1.4)
   - Exit reasons distribution (TP vs SL vs TRAILING)
5. Validate trailing stops protect profits during pullbacks

**Phase 5: Buy/Sell Matrix Integration (Future)**
- Integrate signal_manager.py for real-time signal checking
- Exit immediately when matrix flips SELL on profitable positions
- Apply smart exits when matrix says SELL but position at loss

**Next Session:** `NEXT_SESSION_FIFO_IMPLEMENTATION.md` (after this session completes)
