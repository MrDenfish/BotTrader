# Session: ROC Peak-Drawdown Strategy Complete Refactor

**Date**: January 28, 2026
**Branch**: `feature/roc-peak-drawdown-atr-refactor`
**Status**: 🚧 In Progress
**Goal**: Complete replacement of ROC_MOMO strategies with professional-grade peak-drawdown + dual ATR% system

---

## Executive Summary

### What We're Building

A complete replacement of the existing ROC_MOMO_20M and ROC_MOMO_24H strategies with a sophisticated momentum system based on:

1. **ROC Peak-Drawdown Exit** - Exit when momentum drops X% from its peak (not fixed thresholds)
2. **Dual ATR% Stops** - Volatility-normalized stops on two timeframes
   - `ATR_slow` (15m/1h): Catastrophic/survival stop
   - `ATR_fast` (1m): Profit-protection trailing stop
3. **Volume Confirmation** - Volume ratio filter replaces RSI gate
4. **Arming Mechanisms** - Prevents premature exits from noise

### Why Complete Refactor?

**Previous Implementation Issues:**
- ❌ Fixed ROC thresholds (-2%, -5%) exit too early
- ❌ Peak tracking was price-based, not momentum-based
- ❌ No volume confirmation
- ❌ Mixed 1m/5m data granularity
- ❌ Poor backtest results (all configs lost money)

**New Spec Advantages:**
- ✅ Peak-drawdown lets winners run until reversal
- ✅ ATR% normalization adapts to volatility
- ✅ Dual-stop system separates profit-taking from disaster prevention
- ✅ Volume filter reduces false signals
- ✅ Professional-grade risk management

---

## Project Plan

### Phase 1: Documentation & Setup ⏳

**Goal**: Archive old docs, update specs, create clean foundation

**Tasks**:
- [x] Create feature branch `feature/roc-peak-drawdown-atr-refactor`
- [x] Verify volume data availability (✅ 100% complete, no gaps)
- [x] Create project plan and session document
- [ ] Move old ROC docs to `docs/archive/`
- [ ] Add implementation notes to spec
- [ ] Create backtest/README.md explaining new architecture

**Deliverables**:
- Session document (this file)
- Updated spec with implementation details
- Clean documentation structure

---

### Phase 2: Build Backtest Engine (Proof of Concept) 🎯

**Goal**: Build complete backtest system to validate strategy logic

#### Task 2.1: Indicators Module

**File**: `backtest/indicators_roc_atr.py` (new)

**Components**:
1. **ROC with EMA smoothing**
   ```python
   def calculate_roc(close: pd.Series, length: int, ema_len: int = 1) -> pd.Series:
       roc_raw = close.pct_change(periods=length)
       if ema_len > 1:
           return roc_raw.ewm(span=ema_len, adjust=False).mean()
       return roc_raw
   ```

2. **Multi-timeframe ATR**
   ```python
   def calculate_atr_pct(df: pd.DataFrame, atr_len: int) -> pd.Series:
       # True Range
       tr1 = df['high'] - df['low']
       tr2 = abs(df['high'] - df['close'].shift())
       tr3 = abs(df['low'] - df['close'].shift())
       tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

       # Wilder's smoothing
       atr = tr.ewm(alpha=1/atr_len, adjust=False).mean()

       # Normalize to percentage
       return atr / df['close']
   ```

3. **Bar aggregation for higher-TF ATR**
   ```python
   def aggregate_to_timeframe(df_1m: pd.DataFrame, tf: str) -> pd.DataFrame:
       # Aggregate 1m bars to 15m/1h
       # Return OHLC bars for ATR calculation
   ```

4. **Volume ratio**
   ```python
   def calculate_volume_ratio(volume: pd.Series, sma_len: int) -> pd.Series:
       vol_sma = volume.rolling(window=sma_len).mean()
       return volume / vol_sma
   ```

#### Task 2.2: Strategy State Machine

**File**: `backtest/strategy_peak_drawdown.py` (new)

**Components**:
1. **Config dataclass**
   ```python
   @dataclass
   class PeakDrawdownConfig:
       # ROC params
       roc_len: int
       roc_ema_len: int
       roc_entry_threshold: float
       roc_arm_threshold: float

       # Volume filter
       vol_sma_len: int
       vol_ratio_min: float

       # ATR_fast
       atr_fast_len: int
       M_fast: float
       fast_trail_activation_k: float

       # ATR_slow
       tf_slow: str  # "15m" or "1h"
       atr_slow_len: int
       M_slow: float

       # ROC peak-drawdown
       X_dd: float  # drawdown threshold

       # Position sizing
       order_size: Decimal
       fee_rate: Decimal
   ```

2. **Position state**
   ```python
   @dataclass
   class PositionState:
       symbol: str
       entry_price: Decimal
       entry_time: datetime
       size: Decimal

       # Tracking
       bars_in_trade: int = 0
       high_since_entry: Decimal = Decimal("0")
       roc_peak: float = 0.0

       # Arming
       armed_roc_dd: bool = False
       armed_fast_trail: bool = False

       # Stops
       stop_slow: Decimal = Decimal("0")
       stop_fast: Decimal = Decimal("-inf")

       # Entry indicators (for logging)
       entry_roc: float = 0.0
       entry_atr_slow_pct: float = 0.0
   ```

3. **Entry/Exit logic**
   ```python
   def check_entry_signal(candle, indicators, config) -> bool:
       # ROC + volume filter

   def update_position_state(position, candle, indicators, config):
       # Update peaks, arming, stops

   def check_exit_conditions(position, candle, indicators, config) -> Optional[str]:
       # Priority: ATR_slow → ATR_fast → ROC_peak_dd
       # Return exit_reason or None
   ```

#### Task 2.3: Backtest Harness

**File**: `backtest/engine_peak_drawdown.py` (new)

**Integration**:
- Load 1m OHLCV from database
- Calculate all indicators (including higher-TF ATR)
- Run state machine per symbol
- Generate trade list + metrics
- Export to CSV

---

### Phase 3: Initial Backtest Runs 📊

**Goal**: Validate strategy logic and parameter defaults

#### Run 3.1: Proof of Concept (7-day sample)
- **Period**: Last 7 days (Jan 21-28, 2026)
- **Variant**: A only (20m-style, simpler)
- **Purpose**: Verify indicators calculate correctly, exits fire properly

**Success Criteria**:
- ✅ No errors in execution
- ✅ Trades generated
- ✅ Exit reasons make sense (mostly ROC_PEAK_DD, rare ATR_SLOW)
- ✅ Stops never go negative or infinite

#### Run 3.2: Full Backtest Matrix (60 days)
- **Period**: Nov 28, 2025 - Jan 27, 2026
- **Variants**: Both A and B
- **Configs to Test**:
  1. **Variant A (20m-style)**: Spec defaults
  2. **Variant B (24h-style)**: Spec defaults
  3. **Variant A Tuned**: Adjusted after initial results
  4. **Variant B Tuned**: Adjusted after initial results

**Metrics to Collect**:
- Total P&L
- Win rate
- Profit factor
- Exit reason distribution
- Average hold time
- MAE/MFE in ATR units

---

### Phase 4: Parameter Optimization (Grid Search) 🔍

**What is Grid Search?**

A systematic method to find optimal parameters by testing combinations:

**Example for Variant A**:
```python
param_grid = {
    'roc_len': [7, 9, 12],
    'roc_entry_threshold': [0.002, 0.003, 0.004],
    'X_dd': [0.30, 0.40, 0.50],
    'M_fast': [1.5, 1.8, 2.0],
    'M_slow': [2.5, 3.0, 3.5]
}
# Tests 3×3×3×3×3 = 243 combinations
```

**Process**:
1. Define parameter ranges to test
2. Run backtest for each combination
3. Rank by Sharpe ratio or profit factor
4. Analyze top 10 configs
5. Select robust parameters (not overfit)

**Tools**:
- `backtest/optimizer.py` - Grid search implementation
- Parallel execution (run multiple configs simultaneously)
- Results exported to CSV for analysis

**When to Run**:
- After initial backtest shows promise
- Only if P&L is positive but suboptimal
- Focus on most sensitive parameters (X_dd, M_fast, roc_entry_threshold)

---

### Phase 5: Production Implementation 🏭

**Goal**: Build production code matching backtest exactly

#### File Changes:

1. **`sighook/indicators.py`**
   - Add multi-TF ATR calculation
   - Add ROC with EMA smoothing
   - Add volume ratio calculation

2. **`sighook/signal_manager.py`**
   - Remove old ROC_MOMO logic (lines 342-440)
   - Add new peak-drawdown entry detection
   - Add volume filter
   - Return trigger types: `ROC_PEAK_DD_20M`, `ROC_PEAK_DD_24H`

3. **`webhook/position_monitor.py`** (or new file)
   - Add position state tracking
   - Implement dual-stop logic
   - Handle arming mechanisms
   - Fire exits based on priority order

4. **`config/config_manager.py`**
   - Add all new parameters
   - Separate configs for Variant A and B

5. **`webhook/webhook_order_manager.py`**
   - Map new trigger types to order sizes
   - Handle new exit reasons

---

### Phase 6: Testing & Deployment 🚀

**Pre-Deployment Checklist**:
- [ ] Backtest results validated
- [ ] All production code written
- [ ] Unit tests for indicators
- [ ] Integration test (paper trading mode if available)
- [ ] Documentation updated
- [ ] Config parameters match backtest
- [ ] Exit logic matches exactly

**Deployment Steps**:
1. Commit all changes to feature branch
2. Create PR for review
3. Deploy to AWS
4. Monitor for 7 days
5. Compare live results to backtest

**Monitoring Metrics**:
- Trade frequency (should match backtest ±30%)
- Exit reason distribution
- Hold time distribution
- P&L trajectory

---

## Risk Assessment

### Technical Risks

1. **Higher-TF ATR in Production**
   - Backtest: Easy to aggregate bars retrospectively
   - Production: Must maintain 15m/1h bar state in real-time
   - **Mitigation**: Use windowed calculation, store last N 1m bars

2. **State Management**
   - Complex position state (peaks, arming, dual stops)
   - **Mitigation**: Thorough testing, explicit state persistence

3. **Execution Slippage**
   - Backtest assumes perfect fills
   - Production has slippage/spread
   - **Mitigation**: Conservative fill model in backtest

### Strategy Risks

1. **ROC Peak May Not Form**
   - If entry catches top of move, `roc_peak` = entry ROC
   - Arming threshold prevents immediate exit
   - **Mitigation**: `roc_arm_threshold` parameter

2. **ATR_slow May Be Too Loose**
   - Catastrophic stop could be hit on large moves
   - **Mitigation**: Backtest will reveal if M_slow=3.0-3.5 is appropriate

3. **Volume Filter Excludes Opportunities**
   - Low-volume coins may not trigger
   - **Mitigation**: Tune `vol_ratio_min` per asset class

---

## Success Criteria

### Backtest Phase

**Minimum Viable**:
- ✅ Profit factor > 1.5
- ✅ Win rate > 35%
- ✅ >70% exits via ROC_PEAK_DD (primary exit working)
- ✅ <10% exits via ATR_SLOW (tail risk rare)

**Target**:
- 🎯 Profit factor > 2.0
- 🎯 Win rate > 40%
- 🎯 Avg win > 2× avg loss
- 🎯 Max drawdown < 15%

### Production Phase (7-day validation)

- ✅ Actual trade frequency within ±30% of backtest
- ✅ Exit reason distribution matches backtest
- ✅ No critical errors or infinite loops
- ✅ Position state updates correctly
- ✅ P&L trajectory plausible (not 10× better/worse than backtest)

---

## Notes & Decisions

### Volume Data Verification ✅
- Checked 60 days of history: **0% zero-volume bars**
- All symbols have complete, reliable volume data
- Safe to use volume ratio filter

### Branch Strategy
- Feature branch: `feature/roc-peak-drawdown-atr-refactor`
- Will not merge until backtest validates
- Production code stays clean during development

### Grid Search Decision
- Will use grid search IF initial backtest is positive but suboptimal
- Focus on: `X_dd`, `M_fast`, `roc_entry_threshold`, `vol_ratio_min`
- Use Sharpe ratio (not P&L) to avoid overfitting

---

## Timeline Estimate

**Optimistic** (focused 2 days):
- Day 1: Build backtest engine + POC run
- Day 2: Full backtest + analysis
- Day 3+: Production implementation

**Realistic** (1 week):
- Days 1-2: Build backtest engine, debug indicators
- Day 3: POC backtest, iterate on bugs
- Day 4: Full 60-day backtest
- Day 5: Analyze, potentially grid search
- Days 6-7: Production implementation
- Day 8+: Deploy and monitor

**Current**: Day 1 - Documentation and setup complete

---

## Next Actions

**Immediate** (next 2 hours):
1. Archive old ROC documentation
2. Create `backtest/indicators_roc_atr.py` with all indicator functions
3. Create `backtest/strategy_peak_drawdown.py` with config + state machine
4. Test indicators on sample data (verify calculations)

**Then** (next 4 hours):
1. Build `backtest/engine_peak_drawdown.py` harness
2. Create runner script for both variants
3. Run 7-day POC backtest
4. Debug any issues

**Status**: Ready to begin Phase 2 - Let's build! 🚀

---

**Last Updated**: 2026-01-28 08:00 PST
