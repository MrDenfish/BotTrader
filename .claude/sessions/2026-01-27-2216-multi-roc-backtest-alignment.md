# Session: Multi-ROC Strategy Backtest Alignment
**Date**: January 27, 2026, 22:16 PST
**Status**: 🚀 In Progress
**Goal**: Systematically backtest and optimize ROC_MOMO_20M and ROC_MOMO_24H strategies

---

## Executive Summary

**Problem Identified:**
- Current backtest framework tests a simplified ROC strategy (5-min, single timeframe)
- Production Multi-ROC strategies (ROC_MOMO_20M and ROC_MOMO_24H) were never backtested
- These strategies are currently DISABLED (since Jan 25, 2026)
- BUY_SELL_MATRIX (score-based) is running but has never been backtested
- We're operating blind with no performance expectations

**Solution:**
Systematic, methodical approach:
1. Disable BUY_SELL_MATRIX temporarily
2. Update backtest framework to test actual Multi-ROC strategies
3. Backtest ROC_MOMO_20M and ROC_MOMO_24H with current settings
4. Optimize parameters based on results
5. Deploy optimized Multi-ROC to production
6. Later: Separately backtest and optimize BUY_SELL_MATRIX

---

## Strategy Clarifications

### ROC_MOMO_20M (20-Minute Momentum Scalps)
- **Data Source**: Calculated ROC from 1-minute candles (20-period lookback)
- **Entry**: ROC > 2.0%, RSI 45-60
- **Exit**: Follow price up, exit when ROC drops below -2.0%
- **Position Size**: $15
- **Status**: Currently DISABLED

### ROC_MOMO_24H (24-Hour Momentum Runners)
- **Data Source**: 24-hour % change (calculated from OHLCV: `(close_now - close_24h_ago) / close_24h_ago × 100`)
- **Entry**: 24h% > 10.0%, RSI 45-55
- **Exit**: Follow price up, exit when 24h% drops below -5.0%
- **Position Size**: $20
- **Status**: Currently DISABLED

### BUY_SELL_MATRIX (Score-Based)
- **Data Source**: Multiple indicators (ROC, MACD, RSI, BB, etc.)
- **Entry**: Score >= 2.0, 3+ indicators firing
- **Exit**: TP/SL or score reversal
- **Status**: Currently ACTIVE (but never backtested!)

---

## Key Decisions Made

1. **24h% Data**: Calculate from OHLCV (`Option A`) - close enough for backtesting
2. **Exit Logic**: Use ROC_24H_SELL_THRESHOLD=-5.0 and ROC_20M_SELL_THRESHOLD=-2.0 from .env
3. **Parameters**: Use current .env settings as baseline
4. **Approach**: One strategy at a time - test Multi-ROC first, BUY_SELL_MATRIX later

---

## Phase 1: Environment Check & Planning

### Step 1.1: Check Current .env Settings ✅
**Status**: COMPLETE
**Goal**: Document all Multi-ROC parameters currently configured

**Current Production Settings (AWS):**
```bash
# ROC_MOMO_20M (20-Minute Momentum Scalps)
ROC_20M_BUY_THRESHOLD=2.0          # Entry: +2.0% in 20 minutes
ROC_20M_SELL_THRESHOLD=-2.0        # Exit: -2.0% in 20 minutes
ORDER_SIZE_20M=10.00               # $10 per trade

# ROC_MOMO_24H (24-Hour Momentum Runners)
ROC_24H_BUY_THRESHOLD=10.0         # Entry: +10.0% in 24 hours
ROC_24H_SELL_THRESHOLD=-5.0        # Exit: -5.0% in 24 hours
ORDER_SIZE_ROC=20.00               # $20 per trade

# ROC Calculation
ROC_WINDOW=20                      # 20-period lookback for calculated ROC
ROC_5MIN_BUY_THRESHOLD=8.5         # Different ROC strategy (Test 2)
ROC_5MIN_SELL_THRESHOLD=5.0

# RSI Filter
RSI_WINDOW=14                      # 14-period RSI (Wilder standard)
RSI_OVERSOLD=25
RSI_OVERBOUGHT=75

# Exit Strategy (Test 2 Parameters)
TAKE_PROFIT=0.040                  # 4.0% take profit
STOP_LOSS=-0.020                   # -2.0% stop loss

# Peak Tracking (applies to ROC_MOMO strategies)
PEAK_TRACKING_ENABLED=true
PEAK_TRACKING_DRAWDOWN_PCT=0.08    # Exit at 8% drop from peak
PEAK_TRACKING_MIN_PROFIT_PCT=0.045 # Activate at +4.5% profit
PEAK_TRACKING_BREAKEVEN_PCT=0.045  # Move stop to breakeven at +4.5%
PEAK_TRACKING_SMOOTHING_MINS=5     # 5-min SMA smoothing
PEAK_TRACKING_MAX_HOLD_MINS=2880   # Max hold: 48 hours
PEAK_TRACKING_TRIGGERS=ROC_MOMO,ROC_MOMO_OVERRIDE,ROC,ROC_MOMO_24H,ROC_MOMO_20M

# Trailing Stop (for ROC_MOMO_20M)
TRAILING_STOP_ENABLED=true
TRAILING_STOP_TIMEFRAME=2h         # 2-hour candles for ATR
TRAILING_STOP_ATR_PERIOD=14        # 14-period ATR
TRAILING_STOP_ATR_MULT=2.5         # Trail at 2.5× ATR distance
TRAILING_STEP_ATR_MULT=0.75        # Adjust every 0.75× ATR
TRAILING_MIN_DISTANCE_PCT=0.02     # Min: 2%
TRAILING_MAX_DISTANCE_PCT=0.04     # Max: 4%
TRAILING_ACTIVATION_PCT=0.03       # Activate at +3%

# ATR Settings
ATR_WINDOW=8                       # 8-period ATR
ATR_MULTIPLIER_STOP=1.8            # Stop = 1.8× ATR
```

### Step 1.2: Exit Philosophy Decision ✅
**Status**: COMPLETE
**Decision**: **Aggressive Pure Momentum**

**Philosophy:**
- Let ROC thresholds and peak tracking handle exits
- NO fixed take profit caps (contradicts momentum strategy)
- Test multiple configurations and let data decide optimal settings

**Exit Mechanisms:**
- **Primary**: ROC threshold reversals (-5% for 24H, -2% for 20M)
- **Secondary**: Peak tracking (drawdown from peak)
- **Safety**: Max hold time, emergency stop loss for catastrophic drops

**Backtest Plan:**
Test 4 exit configurations side-by-side:
1. **Current Baseline**: TP=4%, SL=-2% (what's deployed now)
2. **Pure Momentum**: No TP/SL, ROC + peak tracking only
3. **Wide Safety Net**: TP=20% (24H) / TP=10% (20M), SL=-3%
4. **Hybrid**: No TP for 24H, TP=8% for 20M

**Goal**: Let historical data determine which exit strategy performs best

---

## Phase 2: Update Backtest Framework

### Step 2.1: Add ROC_MOMO_20M Detection ⏳
**File**: `backtest/engine.py`
**Changes Needed**:
- Calculate 20-period ROC from 1-min candles
- Detect entry: ROC > threshold, RSI 45-60
- Detect exit: ROC < -2.0%

### Step 2.2: Add ROC_MOMO_24H Detection ⏳
**File**: `backtest/engine.py`
**Changes Needed**:
- Calculate 24h% from OHLCV: `(close - close_24h_ago) / close_24h_ago × 100`
- Detect entry: 24h% > 10%, RSI 45-55
- Detect exit: 24h% < -5.0%

### Step 2.3: Add Strategy-Specific Sizing ⏳
**File**: `backtest/config.py`
**Changes Needed**:
- Add `order_size_20m` parameter
- Add `order_size_24h` parameter
- Map trigger type to position size

### Step 2.4: Update Exit Logic ⏳
**File**: `backtest/engine.py`
**Changes Needed**:
- Check ROC-based exit thresholds by strategy type
- Differentiate between 20M and 24H exit conditions

---

## Phase 3: Run Multi-Configuration Backtests

**Period**: 60 days (Nov 28, 2025 - Jan 27, 2026)
**Test Each Strategy**: ROC_MOMO_20M alone, ROC_MOMO_24H alone, Both combined

### Configuration 1: BASELINE (Current Production) ⏳
**Entry:**
- ROC_MOMO_20M: ROC > 2.0%, RSI 45-60
- ROC_MOMO_24H: 24h% > 10.0%, RSI 45-55

**Exit:**
- Take Profit: +4.0% (HARD CAP - contradicts momentum!)
- Stop Loss: -2.0%
- Peak Tracking: 8% drawdown @ 4.5% activation (never reached!)
- ROC Thresholds: -2% (20M), -5% (24H)

**Expected Result**: Caps all wins at 4%, misses big momentum moves

---

### Configuration 2: PURE MOMENTUM (Aggressive) ⏳
**Entry:** Same as baseline

**Exit:**
- **NO Take Profit cap** - let winners run!
- **NO Stop Loss** - trust ROC reversal detection
- Peak Tracking: 8% drawdown @ 3% activation (24H only)
- Trailing Stop: 2.5× ATR @ 3% activation (20M only)
- ROC Thresholds: -2% (20M), -5% (24H) - PRIMARY EXIT
- Max Hold: 48 hours (safety net)

**Expected Result**: Some huge wins (20-50%+), but also larger drawdowns

---

### Configuration 3: WIDE SAFETY NET (Conservative) ⏳
**Entry:** Same as baseline

**Exit:**
- Take Profit: 20% (24H), 10% (20M) - safety cap only
- Stop Loss: -3% (emergency only)
- Peak Tracking: 8% drawdown @ 5% activation
- ROC Thresholds: -2% (20M), -5% (24H)

**Expected Result**: Captures big moves, prevents extreme outliers

---

### Configuration 4: HYBRID (Balanced) ⏳
**Entry:** Same as baseline

**Exit:**
- ROC_MOMO_24H: NO TP (pure momentum), peak tracking only
- ROC_MOMO_20M: TP at 8% (scalps don't run as far)
- Stop Loss: -2.5%
- Peak Tracking: 6% drawdown @ 4% activation (24H)
- Trailing Stop: 2.0× ATR @ 3% activation (20M)
- ROC Thresholds: -2% (20M), -5% (24H)

**Expected Result**: Best of both worlds - let 24H run, cap 20M scalps

---

### Backtest Matrix (12 total runs)

| Config | ROC_20M Solo | ROC_24H Solo | Both Combined |
|--------|-------------|-------------|---------------|
| 1. Baseline | ⏳ | ⏳ | ⏳ |
| 2. Pure Momentum | ⏳ | ⏳ | ⏳ |
| 3. Wide Safety Net | ⏳ | ⏳ | ⏳ |
| 4. Hybrid | ⏳ | ⏳ | ⏳ |

**Output for each**: P&L, win rate, avg win/loss, max drawdown, trade count, fee impact

---

## Phase 4: Analysis & Optimization

### Step 4.1: Analyze Baseline Results ⏳
**Metrics to Evaluate**:
- Total P&L
- Win rate
- Trade frequency
- Average win/loss
- Max drawdown
- Profit factor

### Step 4.2: Identify Issues ⏳
**Questions to Answer**:
- Are strategies profitable?
- Which performs better?
- Are exit thresholds appropriate?
- Is position sizing optimal?

### Step 4.3: Optimize Parameters ⏳
**Test Variations**:
- Entry thresholds
- Exit thresholds
- RSI ranges
- Position sizes

---

## Phase 5: Deployment

### Step 5.1: Update Production Config ⏳
**Actions**:
- Update .env with optimal parameters
- Re-enable ROC_MOMO_20M and ROC_MOMO_24H in signal_manager.py
- Disable BUY_SELL_MATRIX temporarily

### Step 5.2: Deploy to AWS ⏳
**Actions**:
- Commit changes
- Push to GitHub
- Deploy using deployment script
- Verify containers

### Step 5.3: Monitor & Validate ⏳
**Duration**: 7 days
**Metrics**: Compare actual vs backtest expectations

---

## Success Criteria

**Backtest Phase:**
- ✅ Backtest runs without errors
- ✅ Results match expected trade frequency
- ✅ At least one strategy shows >40% win rate
- ✅ Combined P&L better than -$50 over 60 days

**Deployment Phase:**
- ✅ Actual trade frequency matches backtest ±30%
- ✅ Actual win rate matches backtest ±15%
- ✅ No critical errors in logs
- ✅ Containers remain healthy

---

## Timeline

**Day 1 (Jan 27-28):**
- Check .env settings
- Update backtest framework
- Run initial tests

**Day 2 (Jan 28-29):**
- Complete baseline backtests
- Analyze results
- Begin optimization

**Day 3 (Jan 29-30):**
- Complete optimization
- Prepare deployment
- Deploy to production

**Day 4-10 (Jan 30 - Feb 5):**
- Monitor production performance
- Validate against backtest
- Document results

---

## Files to Modify

**Backtest Framework:**
- `backtest/engine.py` - Add Multi-ROC detection and exit logic
- `backtest/config.py` - Add 20M/24H position sizing
- `backtest/models.py` - Add trade type enums if needed

**Production (Later):**
- `sighook/signal_manager.py` - Re-enable ROC_MOMO strategies
- `.env` - Update with optimal parameters
- `docs/active/features/roc-multi-strategy-system.md` - Update with results

---

## Notes & Questions

**Confirmed:**
- 24h% calculation: Use OHLCV data (Option A)
- Exit logic: ROC threshold-based (-5% for 24H, -2% for 20M)
- Parameters: Use current .env as baseline

**To Determine:**
- Exact current .env values for all Multi-ROC parameters
- Whether Test 2 parameters (TP 4%, SL 2%) apply to Multi-ROC
- Expected trade frequency based on documentation

---

## Next Action

🎯 **IMMEDIATE**: Check current .env settings for Multi-ROC parameters

**Command to run:**
```bash
ssh bottrader-aws "cd /opt/bot && grep -E '^(ROC_|RSI_|ORDER_SIZE_|TAKE_PROFIT|STOP_LOSS|PEAK_TRACKING)' .env | sort"
```

---

**Session Status**: Ready to begin Phase 1
**Next Update**: After .env check complete
