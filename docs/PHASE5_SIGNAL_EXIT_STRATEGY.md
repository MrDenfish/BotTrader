# Phase 5: Signal-Based Exit Strategy

**Date:** 2025-11-30
**Session:** limit-order-smart-exits
**Status:** In Progress

## Overview

Integrate buy_sell_matrix signals into position_monitor.py to enable intelligent exits based on market sentiment shifts, while maintaining a clear hierarchy of exit logic.

## Exit Priority Hierarchy

### 1. Risk Exits (Always Active)

**Hard Stop (-5%)**
- Purpose: Catastrophic loss prevention
- Trigger: P&L ≤ -5%
- Only for gaps, slippage, or system failures
- Action: Immediate LIMIT sell (0.1% below bid for fast fill)

**Soft Stop (-2.5%)**
- Purpose: Normal "I was wrong" exit
- Trigger: P&L ≤ -2.5%
- Main risk management tool
- Action: LIMIT sell (0.01% above bid)

### 2. Signal + Small Profit Exit (Sentiment-Based)

**SELL Signal + Breakeven or Better**
- Purpose: Exit when market sentiment flips while profitable
- Trigger: `SELL signal active AND P&L ≥ 0%`
- Logic: "If the indicator matrix says SELL and we're not losing, get flat"
- Action: LIMIT sell (0.01% above bid)
- **Condition:** `signal == 'sell' AND pnl_pct >= 0.0`

### 3. Profit Management (Trend Following)

**Take Profit Activation (+3.5%)**
- Purpose: Lock in profits for small moves OR activate trailing for big trends
- Trigger: P&L ≥ +3.5%
- Two sub-strategies:

#### Option A: Full Position Trailing (Default)
- At +3.5%: Set `trailing_active = True`
- Start ATR-based trailing stop
- Ignore SELL signals once trailing is active (let trends run)
- Trail at 2×ATR below highest price

#### Option B: Partial Profit Taking (Future Enhancement)
- At +3.5%: Sell 50% at market
- Set `trailing_active = True` on remaining 50%
- Start ATR-based trailing stop
- Maximize: lock some profit + let winners run

**Trailing Stop Exit (ATR-Based)**
- Purpose: Let big trends run while protecting profits
- Active: Only when `trailing_active = True`
- Trigger: `current_price <= stop_price` (2×ATR below high)
- **Signal Override:** Once trailing is active, IGNORE SELL signals
- Rationale: Strong trends often generate SELL signals on pullbacks

## Exit Decision Tree

```
Position Check Every 30 Seconds:
├─ P&L ≤ -5%?
│  └─ YES → EXIT (Hard Stop)
│
├─ P&L ≤ -2.5%?
│  └─ YES → EXIT (Soft Stop)
│
├─ trailing_active == True?
│  ├─ YES → Check trailing stop only
│  │  └─ current_price ≤ stop_price?
│  │     └─ YES → EXIT (Trailing Stop Hit)
│  │     └─ NO → Update stop if new high
│  │
│  └─ NO → Check signal-based exits
│     │
│     ├─ P&L ≥ +3.5%?
│     │  └─ YES → Activate trailing stop
│     │           Set trailing_active = True
│     │           Initialize stop_price
│     │           Continue monitoring
│     │
│     └─ SELL signal active AND P&L ≥ 0%?
│        └─ YES → EXIT (Signal Exit)
│        └─ NO → Continue monitoring
```

## Implementation Details

### Signal Source
- **Cache:** `shared_data_manager.market_data['buy_sell_matrix']`
- **Update Frequency:** Every ~60 seconds (from sighook)
- **Staleness:** Acceptable (swing trading timeframe)
- **Fallback:** If matrix unavailable, skip signal-based exits

### Signal Structure
```python
# buy_sell_matrix DataFrame indexed by asset symbol
asset = "BTC"
buy_signal = buy_sell_matrix.loc[asset, 'Buy Signal']   # (decision, score, threshold, reason)
sell_signal = buy_sell_matrix.loc[asset, 'Sell Signal'] # (decision, score, threshold, reason)

# SELL active when:
sell_signal[0] == 1  # decision == 1
```

### Position State Tracking

**New Field:** `trailing_active` (per position)
```python
self.trailing_stops = {
    'BTC-USD': {
        'last_high': Decimal('52000.00'),
        'stop_price': Decimal('50960.00'),
        'last_atr': Decimal('0.02'),
        'trailing_active': False  # NEW: tracks if trailing is activated
    }
}
```

### Configuration

**Environment Variables:**
```env
# Signal-Based Exits
SIGNAL_EXIT_ENABLED=true              # Enable signal-based exits
SIGNAL_EXIT_MIN_PROFIT_PCT=0.0        # Exit on SELL signal if P&L >= 0%

# Trailing Activation
TRAILING_ACTIVATION_PCT=0.035         # Activate trailing at +3.5%

# Partial Profits (Future)
PARTIAL_PROFIT_ENABLED=false          # Take partial profits at TP level
PARTIAL_PROFIT_PCT=0.50               # Sell 50% at +3.5%
```

## Exit Logging

**Format:**
```
[POS_MONITOR] <SYMBOL>: EXIT TRIGGERED | Reason: <TYPE> (P&L: X.XX%) | Price: $X.XX | Entry: $X.XX
```

**Exit Types:**
- `HARD_STOP` - P&L hit -5%
- `SOFT_STOP` - P&L hit -2.5%
- `SIGNAL_EXIT` - SELL signal + P&L ≥ 0%
- `TRAILING_STOP` - ATR trailing stop hit
- `TRAILING_ACTIVATED` - +3.5% reached, trailing now active

## Benefits

1. **Risk Protection:** Hard/soft stops prevent runaway losses
2. **Sentiment Awareness:** Exit profitable positions when indicators turn bearish
3. **Trend Following:** Trailing stops let winners run during strong trends
4. **No Premature Exits:** Don't exit on SELL signals during trailing (avoid whipsaws)
5. **Breakeven Protection:** Take any profit when market turns against us
6. **Clear Hierarchy:** Unambiguous exit priority eliminates conflicts

## Example Scenarios

### Scenario 1: Quick Profit, Signal Flips
```
T0: BUY at $100 (signal: BUY)
T1: Price $102 (+2%), signal still BUY → Monitor
T2: Price $103 (+3%), signal flips SELL, P&L = +3% → EXIT (Signal Exit)
Result: +3% profit, avoided potential reversal
```

### Scenario 2: Strong Trend, Trailing Locks Profit
```
T0: BUY at $100 (signal: BUY)
T1: Price $103.50 (+3.5%) → ACTIVATE TRAILING, ignore future SELL signals
T2: Price $108 (+8%), trailing stop at $105.84 → Monitor
T3: Signal flips SELL, price still $108 → IGNORE (trailing active)
T4: Price $110 (+10%), trailing stop raised to $107.80 → Monitor
T5: Price drops to $107.50 → EXIT (Trailing Stop)
Result: +7.5% profit vs +3% if exited on SELL signal at T3
```

### Scenario 3: Loss Position, Signal Flips
```
T0: BUY at $100 (signal: BUY)
T1: Price $98 (-2%), signal flips SELL → NO ACTION (P&L < 0%)
T2: Price $97.30 (-2.7%) → EXIT (Soft Stop)
Result: -2.7% loss (controlled by soft stop)
```

### Scenario 4: Breakeven Escape
```
T0: BUY at $100 (signal: BUY)
T1: Price $97 (-3%), signal flips SELL → Monitor
T2: Price recovers to $100.20 (+0.2%), signal still SELL → EXIT (Signal Exit)
Result: +0.2% profit (escaped near breakeven when sentiment bearish)
```

## Files to Modify

1. **sighook/sender.py** - Cache buy_sell_matrix in shared_data_manager
2. **MarketDataManager/position_monitor.py** - Implement signal-based exit logic
3. **Config/.env.template** - Add new configuration variables
4. **.env** - Update production config

## Testing Strategy

1. **Unit Tests:** Test signal query logic with mock matrix data
2. **Integration Tests:** Verify exit priority with simulated P&L scenarios
3. **Production Monitoring:** Track exit reason distribution (SIGNAL_EXIT, TRAILING_STOP, etc.)
4. **Metrics:** Measure win rate improvement with signal-based exits vs threshold-only

## Success Criteria

- ✅ Signal-based exits working in production
- ✅ Trailing activation at +3.5% working correctly
- ✅ No exits on SELL signals when trailing is active
- ✅ Exit reason logging shows distribution
- ✅ No degradation in system stability
- 📊 Track: % of exits by type (target: 20-30% signal-based for profitable exits)
