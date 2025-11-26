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

### Phase 1: LIMIT-Only Orders
- [x] Switch BUY orders to LIMIT-only (no TP/SL brackets)
- [ ] Verify all new buy orders are simple LIMIT (no attached_order_configuration)
- [ ] Test order placement and fills

### Phase 2: Position Monitoring
- [ ] Create position_monitor.py with P&L threshold logic
- [ ] Implement stop loss threshold: -2.5%
- [ ] Implement take profit threshold: +3.5%
- [ ] Implement hard stop (emergency exit): -5%
- [ ] Add LIMIT sell placement logic
- [ ] Integrate with asset_monitor sweep cycle

### Phase 3: ATR-Based Trailing Stops
- [ ] Implement ATR calculation (1-4 hour timeframe, period=14)
- [ ] Add trailing stop logic: 2×ATR distance
- [ ] Add step logic: 0.5×ATR increments
- [ ] Enforce constraints: only raise stop, never lower
- [ ] Add distance limits: 1-2% from current price
- [ ] Track per-position state: last_high, trail_stop_price, last_atr

### Phase 4: Testing & Validation
- [ ] Deploy to production
- [ ] Monitor for 24-48 hours
- [ ] Verify: Lower fees (0.60% vs 0.85%)
- [ ] Verify: Better R:R (1:1.4 vs 1:0.71)
- [ ] Track: Win rate (target >42% for break-even)

### Future (Phase 5): Buy/Sell Matrix Integration
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

### Phase 1: LIMIT-Only Orders (In Progress)

#### Step 1: Modify Order Type Selection
**File:** webhook/webhook_order_manager.py:852-863

**Change:**
```python
def order_type_to_use(self, side, order_data):
    if order_data.trigger and order_data.trigger.get("trigger") == "passive_buy":
        return 'limit'
    if side == 'buy':
        return 'limit'  # ← Changed from 'tp_sl'
    elif side == 'sell':
        return 'limit'
```

## Notes

- Original TP/SL logic preserved in git history (commit: 6c4f433)
- CentralConfig is Singleton - config changes require `docker-compose down && up -d`
- Position monitoring will run in asset_monitor sweep cycle (every 3 seconds)
- LIMIT orders use maker fees (0.30%) vs taker fees (0.55%) for TP/SL exits
