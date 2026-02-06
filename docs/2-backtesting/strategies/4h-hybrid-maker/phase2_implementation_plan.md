# Phase 2 Implementation Plan - 4h Hybrid Maker Strategy

**Date:** January 30, 2026
**Status:** In Progress
**Related Spec:** `phase2_spec_4h_hybrid_compression_chase.md`

## Overview

This document provides the detailed implementation plan for Phase 2 of the 4h Hybrid Maker Strategy, including specific code changes, file locations, and execution order.

---

## Completed Work

### ✅ Part 1: Config Updates (`backtest/config_4h_hybrid.py`)

**Changes Made:**
- Added Bollinger compression parameters:
  - `use_compression_filter: bool = True`
  - `bb_len: int = 20`
  - `bb_k: float = 2.0`
  - `bb_width_window: int = 180`
  - `bb_width_pct_threshold: int = 20`

- Added chase entry parameters:
  - `retest_ttl_minutes: int = 180`
  - `enable_chase_entry: bool = True`
  - `chase_offset: float = 0.0010`
  - `chase_ttl_minutes: int = 90`

- Created Phase 2 baseline preset:
  - `get_phase2_baseline_config()` - donch_len=10, vol_min_mult=1.0, compression enabled, chase enabled, TP1=3×, TP2=8×

**Status:** Complete

---

### ✅ Part 2: Engine Indicator Updates (`backtest/engine_4h_hybrid.py`)

**Changes Made:**

1. **_calculate_4h_indicators() method** (lines 97-128):
   - Added Bollinger Band calculation:
     ```python
     bb_len = self.config.bb_len
     bb_k = self.config.bb_k
     df['bb_mid'] = df['close'].rolling(bb_len).mean()
     df['bb_std'] = df['close'].rolling(bb_len).std()
     df['bb_upper'] = df['bb_mid'] + bb_k * df['bb_std']
     df['bb_lower'] = df['bb_mid'] - bb_k * df['bb_std']
     df['bb_width'] = (df['bb_upper'] - df['bb_lower']) / df['bb_mid']
     ```

   - Added bandwidth percentile (compression score):
     ```python
     bb_width_window = self.config.bb_width_window
     df['bb_width_pct'] = df['bb_width'].rolling(bb_width_window).apply(
         lambda x: (x <= x.iloc[-1]).sum() / len(x) * 100 if len(x) > 0 else 0,
         raw=False
     )
     ```

2. **_align_indicators() method** (lines 158-181):
   - Updated to include `bb_width_pct` in 4h indicators
   - Changed: `df_4h_ind = df_4h[['atr_pct', 'donch_high_prev', 'bb_width_pct']].copy()`

3. **run_backtest() method** (lines 210-225):
   - Added `bb_width_pct` to indicators_4h dict passed to strategy

**Status:** Complete

---

## Remaining Work

### ⏳ Part 3: Strategy Updates (`backtest/strategy_4h_hybrid.py`)

**File:** `backtest/strategy_4h_hybrid.py` (~700 lines)

#### 3.1 Data Structure Updates

**Setup dataclass** (currently lines 25-42):

Add new fields:
```python
@dataclass
class Setup:
    symbol: str
    setup_time: datetime
    setup_type: Literal["donchian", "roc_score"]
    breakout_level: float
    ttl_bars_4h: int = 3
    expired: bool = False

    # Phase 2: Compression tracking
    bb_width_pct_at_setup: float = 0.0

    # Phase 2: Chase entry tracking
    setup_created_ts: Optional[datetime] = None
    retest_deadline_ts: Optional[datetime] = None
    chase_attempted: bool = False
    chase_order_placed_ts: Optional[datetime] = None
    chase_deadline_ts: Optional[datetime] = None
```

**Trade dataclass** (currently lines 76-104):

Add new fields at the end (before defaults):
```python
@dataclass
class Trade:
    # ... existing fields ...

    # Phase 2: Entry diagnostics
    entry_type: Literal["RETEST", "CHASE"] = "RETEST"
    entry_wait_minutes: float = 0.0
```

#### 3.2 Compression Filter Implementation

**Location:** `_check_donchian_setup()` method (currently lines 242-285)

**Add after viability filter** (after line ~275):
```python
# Phase 2: Compression filter
if self.config.use_compression_filter:
    bb_width_pct = indicators_4h.get('bb_width_pct', 100)
    if bb_width_pct > self.config.bb_width_pct_threshold:
        # Bandwidth too wide - skip setup
        return
```

**Store compression value in Setup**:
When creating setup, add:
```python
setup = Setup(
    # ... existing params ...
    bb_width_pct_at_setup=indicators_4h.get('bb_width_pct', 0),
    setup_created_ts=bar.name,  # timestamp from index
    retest_deadline_ts=bar.name + timedelta(minutes=self.config.retest_ttl_minutes),
)
```

#### 3.3 Chase Entry Implementation

**New method to add** (insert after `_check_breakout_retest()`):

```python
def _place_chase_order(
    self,
    symbol: str,
    bar: pd.Series,
    setup: Setup
) -> None:
    """
    Place chase entry order (Phase 2 fallback).

    If retest doesn't fill within TTL, chase at current price.
    Still post-only for maker fees.
    """
    if not self.config.enable_chase_entry:
        return

    if setup.chase_attempted:
        return  # Already tried

    # Calculate chase price
    chase_price = bar['close'] * (1.0 - self.config.chase_offset)

    # Mark chase attempted
    setup.chase_attempted = True
    setup.chase_order_placed_ts = bar.name
    setup.chase_deadline_ts = bar.name + timedelta(minutes=self.config.chase_ttl_minutes)

    # Store chase order info in entry_order
    self.entry_order.chase_price = chase_price

    print(f"  [CHASE] Placed @ {chase_price:.2f} (offset={self.config.chase_offset:.2%})")
```

**Update EntryOrder dataclass** to add:
```python
chase_price: Optional[float] = None
```

**Add chase fill logic** in `process_bar_1m()` (in ENTRY_ORDER_WORKING state):

```python
# Check chase fill (if chase order active)
if setup.chase_attempted and self.entry_order.chase_price is not None:
    if bar['low'] <= self.entry_order.chase_price:
        # Chase filled!
        entry_wait_minutes = (bar.name - setup.setup_created_ts).total_seconds() / 60

        self._create_position(
            symbol=symbol,
            bar=bar,
            entry_price=self.entry_order.chase_price,
            atr_pct_4h=indicators_4h['atr_pct'],
            setup_type=setup.setup_type,
            entry_mode="CHASE",
            entry_wait_minutes=entry_wait_minutes,
        )

        # Clear entry order and setup
        self.entry_order = None
        self.pending_setup = None
        return
```

**Add chase TTL expiry check**:
```python
# Check chase expiry
if setup.chase_attempted and bar.name > setup.chase_deadline_ts:
    print(f"  [CHASE_EXPIRED] {symbol}")
    self.stats['expired'] += 1
    self.pending_setup = None
    self.entry_order = None
    return
```

**Trigger chase when retest TTL expires** (in ENTRY_ORDER_WORKING state):
```python
# Check if retest TTL expired -> escalate to chase
if bar.name >= setup.retest_deadline_ts and not setup.chase_attempted:
    self._place_chase_order(symbol, bar, setup)
```

#### 3.4 Update _create_position()

**Add entry_type parameter**:
```python
def _create_position(
    self,
    symbol: str,
    bar: pd.Series,
    entry_price: float,
    atr_pct_4h: float,
    setup_type: str,
    entry_mode: str,
    entry_wait_minutes: float = 0.0,  # New
) -> None:
```

**Store in Trade object**:
```python
trade = Trade(
    # ... existing fields ...
    entry_type=entry_mode,  # "RETEST" or "CHASE"
    entry_wait_minutes=entry_wait_minutes,
)
```

#### 3.5 Enhanced Statistics

**Add to __init__** (stats dict):
```python
self.stats = {
    # ... existing ...
    'retest_fills': 0,
    'chase_fills': 0,
    'chase_attempts': 0,
}
```

**Update counters**:
- Increment `retest_fills` when entry_type="RETEST"
- Increment `chase_fills` when entry_type="CHASE"
- Increment `chase_attempts` when `_place_chase_order()` called

**Update get_statistics()**:
```python
stats['retest_fills'] = self.stats.get('retest_fills', 0)
stats['chase_fills'] = self.stats.get('chase_fills', 0)
stats['chase_attempts'] = self.stats.get('chase_attempts', 0)

if stats['chase_attempts'] > 0:
    stats['chase_success_rate'] = stats['chase_fills'] / stats['chase_attempts']
else:
    stats['chase_success_rate'] = 0.0
```

**Status:** Not started

---

### ⏳ Part 4: Optimizer Updates (`backtest/optimizer_4h_hybrid.py`)

#### 4.1 Update Parameter Grid

**Replace `run_quick_optimization()` grid** (lines 207-213):

```python
param_grid = {
    # Focus on productive region from Phase 1
    'donch_len': [6, 8, 10, 12],
    'vol_min_mult': [0.75, 1.0, 1.25],

    # Phase 2: Compression filter
    'use_compression_filter': [True, False],
    'bb_width_pct_threshold': [10, 20, 30],

    # Phase 2: Chase entry (only test if compression=True)
    'retest_ttl_minutes': [60, 120, 180],
    'chase_offset': [0.0005, 0.0010, 0.0015],
    'chase_ttl_minutes': [30, 60, 90],

    # Phase 2: Expanded targets
    'tp1_fee_mult': [2.5, 3.0, 3.5, 4.0],
    'tp2_fee_mult': [6, 8, 10, 12],

    # Phase 2: Wider stops
    'stop_mult': [2.0, 2.5, 3.0, 3.5],
}
```

**Problem:** This is ~15,552 combinations (too many).

**Solution:** Use staged approach or random sampling.

**Quick grid** (manageable size ~216 combos):
```python
param_grid = {
    'donch_len': [8, 10, 12],
    'vol_min_mult': [0.75, 1.0, 1.25],
    'use_compression_filter': [True, False],
    'bb_width_pct_threshold': [20],  # Fixed at baseline
    'retest_ttl_minutes': [120],  # Fixed at baseline
    'chase_offset': [0.0010, 0.0015],
    'chase_ttl_minutes': [60],  # Fixed at baseline
    'tp1_fee_mult': [3.0, 3.5],
    'tp2_fee_mult': [8, 10],
    'stop_mult': [2.5, 3.0],
}
# Total: 3 * 3 * 2 * 1 * 1 * 2 * 1 * 2 * 2 * 2 = 288 combinations
```

#### 4.2 Update Results Export

**Add new columns** to result dict (lines 77-90):
```python
result = {
    **params,
    'trades': stats.get('trades', 0),
    # ... existing ...
    'retest_fills': stats.get('retest_fills', 0),
    'chase_fills': stats.get('chase_fills', 0),
    'chase_attempts': stats.get('chase_attempts', 0),
    'chase_success_rate': stats.get('chase_success_rate', 0),
}
```

#### 4.3 Add Minimum Trade Filter

**In print_summary()** (lines 161-168):
```python
# Viable configurations (meet success criteria + minimum trades)
viable = df[
    (df['trades'] >= 20) &  # NEW: Minimum 20 trades
    (df['net_pnl'] > 0) &
    (df['trades_per_month'] >= 2) &
    (df['trades_per_month'] <= 6) &
    (df['win_rate'] > 0.50) &
    (df['tp1_hit_pct'] > 50)
]
```

**Status:** Not started

---

### ⏳ Part 5: Baseline Test

**Create new test script:** `backtest/test_4h_phase2_baseline.py`

```python
"""
Phase 2 Baseline Test - 4h Hybrid Maker Strategy

Tests the Phase 2 baseline configuration:
- donch_len=10, vol_min_mult=1.0 (from Phase 1 best)
- Compression filter enabled (bb_width_pct <= 20)
- Chase entry enabled (retest_ttl=120m, chase_offset=0.10%)
- Expanded targets (TP1=3×, TP2=8×)
- Wider stops (stop_mult=2.5)

Compares to Phase 1 best to measure improvement.
"""

from backtest.config_4h_hybrid import get_phase2_baseline_config
from backtest.engine_4h_hybrid import Hybrid4hBacktestEngine

def run_phase2_baseline_test():
    print("=" * 80)
    print("PHASE 2 BASELINE TEST - 4H HYBRID MAKER STRATEGY")
    print("=" * 80)
    print()

    config = get_phase2_baseline_config()
    print(f"Config: {config}")
    print()

    engine = Hybrid4hBacktestEngine(config)
    stats = engine.run_backtest("BTC-USD", days=60)

    engine.print_results(stats, "BTC-USD")
    engine.export_trades("trades_phase2_baseline_btc.csv")

    # Comparison
    print("\n" + "=" * 80)
    print("PHASE 1 vs PHASE 2 COMPARISON")
    print("=" * 80)
    print()
    print("Phase 1 Best (from optimizer):")
    print("  - donch_len=10, vol_min_mult=1.0")
    print("  - NO compression filter")
    print("  - NO chase entry")
    print("  - TP1=2.0×, TP2=5.0×")
    print("  - Result: 7 trades, -$5.42 net P&L")
    print()
    print("Phase 2 Baseline:")
    print("  - donch_len=10, vol_min_mult=1.0")
    print("  - Compression filter (bb_width_pct <= 20)")
    print("  - Chase entry (retest_ttl=120m, chase=0.10%)")
    print("  - TP1=3.0×, TP2=8.0×, stop=2.5×")
    print(f"  - Result: {stats.get('trades', 0)} trades, "
          f"${stats.get('net_pnl', 0):.2f} net P&L")
    print()
    print(f"  Entry breakdown:")
    print(f"    Retest fills: {stats.get('retest_fills', 0)}")
    print(f"    Chase fills: {stats.get('chase_fills', 0)}")
    print(f"    Chase success: {stats.get('chase_success_rate', 0):.1%}")
    print("=" * 80)

if __name__ == "__main__":
    run_phase2_baseline_test()
```

**Status:** Not started

---

## Implementation Order

1. ✅ Config updates
2. ✅ Engine indicator calculation and alignment
3. ⏳ **Strategy updates** (NEXT):
   - 3a. Update Setup dataclass
   - 3b. Update Trade dataclass
   - 3c. Add compression filter
   - 3d. Implement chase entry
   - 3e. Update statistics
4. ⏳ Optimizer updates
5. ⏳ Baseline test
6. ⏳ Run Phase 2 optimizer sweep

---

## Success Criteria

Phase 2 is successful if the baseline test shows:
- Net P&L > $0 (break-even or better)
- More trades than Phase 1 (compression should help quality, chase should help quantity)
- Chase fills > 0 (mechanism working)
- Majority of fills remain maker (not too many taker stops)

Optimizer sweep is successful if it finds configurations that:
- Net P&L > $10-20 over 60 days
- Trade frequency 2-6/month
- Win rate > 50%
- Profit factor > 1.2

---

## Notes

- Strategy file is ~700 lines - changes are focused but must be careful with state machine logic
- Chase entry requires careful TTL tracking across multiple 1m bars
- Entry type tracking is critical for diagnostics to understand what's working
- Optimizer grid must be manageable (<500 combos for quick test, <2000 for comprehensive)
- Phase 2 may still fail if market conditions unsuitable, but should show improvement over Phase 1

---

**Last Updated:** January 30, 2026
