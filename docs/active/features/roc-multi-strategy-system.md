# Multi-Strategy ROC System Executive Overview

**Date**: January 17, 2026
**Status**: Active Production (Three-Tier System)
**Last Updated**: January 17, 2026
**Version**: 8.2.0

---

## Executive Summary

The **Multi-Strategy ROC System** implements three independent momentum strategies that work together to capture market moves across different timeframes. Each strategy uses distinct data sources, thresholds, exit logic, and position sizing for optimal performance.

**System Architecture**:
1. **20-Minute Momentum Scalps** - Fast intraday moves (2%+ in 20 minutes)
2. **24-Hour Momentum Runners** - Big daily movers (10%+ in 24 hours)
3. **Calculated ROC Scoring** - Weighted scoring contribution (all timeframes)

**Key Performance Indicators**:
- **Strategy #1 Triggers**: `ROC_MOMO_20M` → $15 orders, 2% threshold, tight trailing stops
- **Strategy #2 Triggers**: `ROC_MOMO_24H` → $20 orders, 10% threshold, peak tracking exits
- **Strategy #3 Contribution**: `Buy ROC` / `Sell ROC` → Scoring weight 2.0 (supports all strategies)
- **Priority Order**: 20-minute → 24-hour → Weighted scoring

---

## Strategy #1: 20-Minute Momentum Scalps

### Overview
Captures fast intraday pumps by detecting 2%+ price moves within 20-minute windows. Uses calculated ROC indicator from 1-minute OHLCV candles with 20-period lookback.

### Signal Detection (sighook/signal_manager.py:341-385)

**BUY Signal Conditions**:
```python
buy_signal_20m = (
    roc_20m_value > roc_20m_buy_threshold    # ROC > +2.0% (from config)
    AND (45.0 <= rsi_value <= 60.0)          # RSI neutral-to-bullish
)
```

**SELL Signal Conditions**:
```python
sell_signal_20m = (
    roc_20m_value < roc_20m_sell_threshold   # ROC < -2.0% (from config)
    AND (40.0 <= rsi_value <= 55.0)          # RSI neutral-to-bearish
)
```

**Key Parameters** (from .env):
```bash
# Data source: Calculated ROC from 1-minute candles
ROC_WINDOW=20                  # 20-period lookback (20 minutes of 1-min data)
ROC_20M_BUY_THRESHOLD=2.0      # Entry: +2.0% in 20 minutes
ROC_20M_SELL_THRESHOLD=-2.0    # Exit: -2.0% in 20 minutes

# Position sizing
ORDER_SIZE_20M=15.00           # $15 per trade (smaller for fast scalps)

# RSI gate: 45-60 range (wider than 24h strategy for faster moves)
RSI_WINDOW=14                  # Standard RSI calculation
```

### Calculation Method (sighook/indicators.py:128-135)

The ROC indicator is calculated by `indicators.py` and added to the OHLCV dataframe:
```python
# Rate of Change (percentage change over N periods)
df['ROC'] = df['close'].pct_change(periods=ROC_WINDOW) * 100

# For ROC_WINDOW=20 and 1-minute candles:
# ROC = ((current_close - close_20_minutes_ago) / close_20_minutes_ago) × 100
```

### Execution Details

**Trigger Type**: `ROC_MOMO_20M`
**Order Size**: $15.00 (webhook/webhook_order_manager.py:609)
**Order Type**: Limit orders at current market price
**Exit Strategy**: ATR-based trailing stop (1.5× ATR on 5-minute candles)

**Strategy Rationale**:
- **Goal**: Catch sudden pumps/breakouts before they're exhausted
- **Timeframe**: Fast-moving (minutes to hours)
- **Exit Logic**: Tight trailing stops prevent giving back gains
- **Risk Management**: Smaller position size ($15) for higher frequency trades

---

## Strategy #2: 24-Hour Momentum Runners

### Overview
Captures big daily movers by detecting markets that gained 10%+ in 24 hours and are still in neutral RSI zone. Uses exchange ticker's `price_percentage_change_24h` (not calculated ROC indicator).

### Signal Detection (sighook/signal_manager.py:387-440)

**BUY Signal Conditions**:
```python
buy_signal_roc = (
    roc_24h_value > roc_24h_buy_threshold    # 24h% > +10.0% (from ticker)
    AND (45.0 <= rsi_value <= 55.0)          # RSI neutral zone (tighter than 20m)
)
```

**SELL Signal Conditions**:
```python
sell_signal_roc = (
    roc_24h_value < roc_24h_sell_threshold   # 24h% < -5.0% (from ticker)
    AND (45.0 <= rsi_value <= 55.0)          # RSI neutral zone
)
```

**Key Parameters** (from .env):
```bash
# Data source: Exchange ticker 24h% change (NOT calculated ROC)
ROC_24H_BUY_THRESHOLD=10.0     # Entry: +10.0% in 24 hours
ROC_24H_SELL_THRESHOLD=-5.0    # Exit: -5.0% in 24 hours (currently unused)

# Position sizing
ORDER_SIZE_ROC=20.00           # $20 per trade (larger for bigger moves)

# RSI gate: 45-55 range (stricter to avoid exhausted pumps)
RSI_WINDOW=14
```

### Data Source

The 24-hour percentage change is retrieved from the exchange ticker (not calculated):
```python
# sighook/signal_manager.py:395-402
roc_24h_value = None
if self.usd_pairs is not None:
    usd_pairs_df = self.usd_pairs.set_index("asset")
    asset_name = symbol.split('-')[0]
    if asset_name in usd_pairs_df.index:
        roc_24h_value = float(usd_pairs_df.loc[asset_name, 'price_percentage_change_24h'])
```

**Important**: This is **NOT** the same as the calculated ROC indicator. It uses live ticker data from the exchange.

### Execution Details

**Trigger Type**: `ROC_MOMO_24H`
**Order Size**: $20.00 (webhook/webhook_order_manager.py:608)
**Order Type**: Limit orders at current market price
**Exit Strategy**: Peak tracking (8% drawdown from peak profit)

**Peak Tracking Exit Configuration** (from .env):
```bash
PEAK_TRACKING_ENABLED=true
PEAK_TRACKING_DRAWDOWN_PCT=0.08        # Exit at 8% drop from peak
PEAK_TRACKING_MIN_PROFIT_PCT=0.03      # Activate at +3% profit
PEAK_TRACKING_BREAKEVEN_PCT=0.03       # Move stop to break-even at +3%
PEAK_TRACKING_SMOOTHING_MINS=5         # 5-min SMA to reduce noise
PEAK_TRACKING_MAX_HOLD_MINS=2880       # Max hold: 48 hours
PEAK_TRACKING_TRIGGERS=ROC_MOMO,ROC_MOMO_OVERRIDE,ROC  # Applies to these triggers
```

**Strategy Rationale**:
- **Goal**: Enter markets already in motion, ride momentum as it continues
- **Entry Logic**: 10%+ daily gain with neutral RSI = strength, not exhaustion
- **Exit Logic**: Peak tracking lets big moves run (20-50%+), exits on reversal
- **Risk Management**: Larger position ($20) for higher conviction, wider stops

---

## Strategy #3: Calculated ROC in Weighted Scoring

### Overview
The calculated ROC indicator (same calculation as Strategy #1) contributes to the weighted scoring system when neither Strategy #1 nor Strategy #2 triggers. This supports all other trading strategies by providing momentum confirmation.

### Integration (sighook/indicators.py:136-141)

The ROC indicator generates Buy/Sell signals for scoring:
```python
# Generate Buy ROC signal
df['Buy ROC'] = df['ROC'].apply(
    lambda r: (1, r, roc_buy_threshold) if r > roc_buy_threshold else (0, r, roc_buy_threshold)
)

# Generate Sell ROC signal
df['Sell ROC'] = df['ROC'].apply(
    lambda r: (1, r, roc_sell_threshold) if r < roc_sell_threshold else (0, r, roc_sell_threshold)
)
```

### Scoring Weight (sighook/signal_manager.py:65-70)

ROC contributes to the overall score with moderate weight:
```python
strategy_weights = {
    'Buy ROC': 2.0,    # Weight: 2.0 (moderate priority)
    'Sell ROC': 2.0,   # Weight: 2.0 (moderate priority)
    # ... other indicators (MACD, RSI, BB, etc.)
}
```

### Scoring Parameters (from .env)
```bash
# Score targets (sum of weighted indicators)
SCORE_BUY_TARGET=2.0           # Need 2.0 points to trigger buy
SCORE_SELL_TARGET=2.0          # Need 2.0 points to trigger sell

# ROC thresholds for scoring (inherited from 24h config)
ROC_BUY_24H=2                  # +2% threshold for scoring contribution
ROC_SELL_24H=1                 # -1% threshold for scoring contribution

# Guardrails
MIN_INDICATORS_REQUIRED=3      # Need 3+ indicators to confirm
FLIP_HYSTERESIS_PCT=0.10       # +10% buffer to flip sides
COOLDOWN_BARS=7                # Ignore opposite side for 7 bars after flip
```

**ROC Contribution to Score**:
- If calculated ROC > +2%: Adds **+2.0 points** to buy score
- If calculated ROC < -1%: Adds **+2.0 points** to sell score
- Since SCORE_TARGET=2.0, ROC alone can trigger a score-based order (if 3+ indicators confirm)

---

## Strategy Priority and Execution Flow

### Signal Processing Order (sighook/signal_manager.py:341-480)

The system evaluates strategies in priority order:

```
Priority 1: 20-Minute Momentum Scalps
  ├─ Check: Is calculated ROC > 2% AND RSI 45-60?
  ├─ If YES: Return BUY signal, trigger='ROC_MOMO_20M'
  └─ If NO: Continue to Priority 2

Priority 2: 24-Hour Momentum Runners
  ├─ Check: Is ticker 24h% > 10% AND RSI 45-55?
  ├─ If YES: Return BUY signal, trigger='ROC_MOMO_24H'
  └─ If NO: Continue to Priority 3

Priority 3: Weighted Scoring
  ├─ Calculate score from all indicators (including ROC)
  ├─ Check: Is score >= 2.0 AND 3+ indicators?
  ├─ If YES: Return BUY signal, trigger='SCORE'
  └─ If NO: Return no signal
```

**Why This Order?**
1. **20-minute first**: Highest frequency, most time-sensitive (needs fast execution)
2. **24-hour second**: Lower frequency, less urgent (daily moves develop slowly)
3. **Scoring last**: Fallback for non-momentum opportunities

### Order Sizing by Trigger (webhook/webhook_order_manager.py:598-612)

Each strategy maps to a specific order size:
```python
trigger_size_map = {
    "ROC_MOMO_20M": self.config.order_size_20m,  # $15 - 20-minute scalps
    "ROC_MOMO_24H": self.config.order_size_roc,  # $20 - 24-hour runners
    "SCORE": self.config.order_size_signal,      # $15 - Standard scoring
    "SIGNAL": self.config.order_size_signal,     # $15 - Legacy scoring
    "WEBHOOK": self.config.order_size_webhook,   # $25 - External signals
    # ... other triggers
}
```

**Position Size Summary**:
- **20-minute scalps**: $15 (smaller for fast trades)
- **24-hour runners**: $20 (larger for conviction)
- **Score-based**: $15 (standard size)

---

## Exit Strategy Details

### 20-Minute Strategy: ATR-Based Trailing Stop

**Configuration** (from .env):
```bash
# ATR trailing stop (optimized for fast moves)
TRAILING_STOP_ENABLED=true
TRAILING_STOP_TIMEFRAME=2h         # 2-hour candles for ATR calculation
TRAILING_STOP_ATR_PERIOD=14        # 14-period ATR
TRAILING_STOP_ATR_MULT=2.5         # Trail at 2.5 × ATR distance
TRAILING_STEP_ATR_MULT=0.75        # Adjust every 0.75 × ATR move
TRAILING_MIN_DISTANCE_PCT=0.02     # Min distance: 2%
TRAILING_MAX_DISTANCE_PCT=0.04     # Max distance: 4%
TRAILING_ACTIVATION_PCT=0.03       # Activate at +3% profit
```

**How It Works**:
1. Position enters at market price
2. Once profit reaches +3%, trailing stop activates
3. Stop trails at distance = 2.5 × ATR (minimum 2%, maximum 4%)
4. Stop adjusts upward every 0.75 × ATR price movement
5. Exit triggers when price drops below trailing stop

**Rationale**: Tight trailing stops lock in profits quickly for fast-moving scalps.

### 24-Hour Strategy: Peak Tracking Exit

**Configuration** (from .env):
```bash
# Peak tracking exit (optimized for big runners)
PEAK_TRACKING_ENABLED=true
PEAK_TRACKING_DRAWDOWN_PCT=0.08        # Exit at 8% drop from peak
PEAK_TRACKING_MIN_PROFIT_PCT=0.03      # Activate at +3% profit
PEAK_TRACKING_BREAKEVEN_PCT=0.03       # Move stop to break-even at +3%
PEAK_TRACKING_SMOOTHING_MINS=5         # 5-min SMA reduces noise
PEAK_TRACKING_MAX_HOLD_MINS=2880       # Max hold: 48 hours (2 days)
PEAK_TRACKING_TRIGGERS=ROC_MOMO,ROC_MOMO_OVERRIDE,ROC
```

**How It Works**:
1. Position enters on 10%+ daily momentum
2. System tracks peak price (smoothed with 5-min SMA)
3. Once profit reaches +3%, peak tracking activates
4. Hard stop moves to break-even at +3% profit
5. Exit triggers when price drops 8% from peak profit
6. Max hold time: 48 hours (force exit if no reversal)

**Rationale**: Wide drawdown tolerance (8%) lets big moves run 20-50%+ before exiting on reversal.

### Score-Based Strategy: Standard Exits

**Configuration** (from .env):
```bash
# Standard P&L-based exits
MAX_LOSS_PCT=0.03              # LIMIT sell at -3.0% loss
MIN_PROFIT_PCT=0.02            # LIMIT sell at +2.0% profit
HARD_STOP_PCT=0.045            # MARKET sell at -4.5% emergency loss

# ATR-based stop loss
STOP_MODE=atr
ATR_MULTIPLIER_STOP=1.8        # Stop = 1.8 × ATR
STOP_MIN_PCT=0.012             # Floor: 1.2% minimum stop
```

---

## Critical Bug Fixes (January 17, 2026)

### Bug #1: Average Entry Price Calculation
**Location**: MarketDataManager/position_monitor.py:446-470

**Problem**:
- System calculated avg_entry_price from Coinbase API's unrealized_pnl
- When API returned garbage data, entry price became wildly incorrect
- Example: SAND-USD showed entry=$0.3655 when actual=$0.1560 (2.35× error)
- Result: False -58% loss triggered exit when position was actually at -0.76%

**Fix Implemented**:
```python
# Calculate avg_entry_price from unrealized_pnl
if current_price > 0 and total_balance_crypto > 0:
    avg_entry_price = current_price - (unrealized_pnl / total_balance_crypto)

    # CRITICAL FIX: Validate avg_entry_price is reasonable
    # If avg_entry > 2× current_price or < 0.5× current_price, it's garbage data
    if avg_entry_price > current_price * Decimal('2') or avg_entry_price < current_price * Decimal('0.5'):
        self.logger.warning(
            f"[POS_MONITOR] {symbol} INVALID avg_entry from API: "
            f"entry={avg_entry_price:.4f}, current={current_price:.4f}. "
            f"Using current price as entry (conservative estimate)."
        )
        # Fallback: assume entry ≈ current price (neutral P&L)
        avg_entry_price = current_price
```

**Impact**: Prevents catastrophic false exits due to API data corruption.

### Bug #2: Order Sizing Configuration
**Location**: Config/config_manager.py + webhook/webhook_order_manager.py

**Problem**:
- ORDER_SIZE_ROC=20.00 existed in .env but wasn't being properly accessed
- Trigger mapping used generic "ROC_MOMO" instead of specific strategy triggers
- Result: All ROC orders fell back to ORDER_SIZE_FIAT=$35 instead of $20

**Fix Implemented**:
1. Added `_order_size_20m` property to config_manager.py
2. Added ORDER_SIZE_20M to environment variable mapping
3. Updated trigger_size_map to use specific triggers:
   - `ROC_MOMO_24H` → $20 (24-hour strategy)
   - `ROC_MOMO_20M` → $15 (20-minute strategy)

**Impact**: Each strategy now uses correct position sizing.

---

## Configuration Summary

### Complete .env Settings for Multi-Strategy ROC

```bash
# ========================================
# ROC INDICATOR CALCULATION
# ========================================
ROC_WINDOW=20                          # 20-period lookback (used by indicators.py)

# ========================================
# STRATEGY #1: 20-MINUTE MOMENTUM SCALPS
# ========================================
ROC_20M_BUY_THRESHOLD=2.0              # Entry: +2.0% in 20 minutes
ROC_20M_SELL_THRESHOLD=-2.0            # Exit: -2.0% in 20 minutes
ORDER_SIZE_20M=15.00                   # Position size: $15

# ========================================
# STRATEGY #2: 24-HOUR MOMENTUM RUNNERS
# ========================================
ROC_24H_BUY_THRESHOLD=10.0             # Entry: +10.0% in 24 hours (ticker data)
ROC_24H_SELL_THRESHOLD=-5.0            # Exit: -5.0% in 24 hours (currently unused)
ORDER_SIZE_ROC=20.00                   # Position size: $20

# Peak tracking exit (applies to ROC_MOMO_24H triggers)
PEAK_TRACKING_ENABLED=true
PEAK_TRACKING_DRAWDOWN_PCT=0.08        # Exit at 8% drop from peak
PEAK_TRACKING_MIN_PROFIT_PCT=0.03      # Activate at +3% profit
PEAK_TRACKING_BREAKEVEN_PCT=0.03       # Move stop to break-even at +3%
PEAK_TRACKING_SMOOTHING_MINS=5
PEAK_TRACKING_MAX_HOLD_MINS=2880       # 48 hours max hold
PEAK_TRACKING_TRIGGERS=ROC_MOMO,ROC_MOMO_OVERRIDE,ROC

# ========================================
# STRATEGY #3: CALCULATED ROC SCORING
# ========================================
ROC_BUY_24H=2                          # +2% threshold for scoring contribution
ROC_SELL_24H=1                         # -1% threshold for scoring contribution
SCORE_BUY_TARGET=2.0                   # Score target to trigger buy
SCORE_SELL_TARGET=2.0                  # Score target to trigger sell
MIN_INDICATORS_REQUIRED=3              # Require 3+ indicators to confirm

# ========================================
# SUPPORTING INDICATORS
# ========================================
RSI_WINDOW=14                          # RSI calculation period
RSI_OVERSOLD=25                        # RSI oversold threshold (not used in ROC)
RSI_OVERBOUGHT=75                      # RSI overbought threshold (not used in ROC)

ATR_WINDOW=8                           # ATR calculation period
ATR_MULTIPLIER_STOP=1.8                # Stop = 1.8 × ATR
STOP_MIN_PCT=0.012                     # 1.2% floor when ATR is tiny

# ========================================
# TRAILING STOP (20-MINUTE STRATEGY)
# ========================================
TRAILING_STOP_ENABLED=true
TRAILING_STOP_TIMEFRAME=2h             # 2-hour candles for ATR
TRAILING_STOP_ATR_PERIOD=14
TRAILING_STOP_ATR_MULT=2.5             # Trail at 2.5 × ATR
TRAILING_STEP_ATR_MULT=0.75            # Adjust every 0.75 × ATR
TRAILING_MIN_DISTANCE_PCT=0.02         # Min: 2%
TRAILING_MAX_DISTANCE_PCT=0.04         # Max: 4%
TRAILING_ACTIVATION_PCT=0.03           # Activate at +3%

# ========================================
# STANDARD EXITS (SCORE-BASED TRADES)
# ========================================
MAX_LOSS_PCT=0.03                      # LIMIT sell at -3.0%
MIN_PROFIT_PCT=0.02                    # LIMIT sell at +2.0%
HARD_STOP_PCT=0.045                    # MARKET sell at -4.5%

# ========================================
# FEE CONFIGURATION
# ========================================
MAKER_FEE=0.006                        # 0.6% maker fee
TAKER_FEE=0.012                        # 1.2% taker fee
```

---

## Performance Expectations

### Strategy #1: 20-Minute Scalps
- **Trade Frequency**: High (multiple per day)
- **Hold Time**: Minutes to hours
- **Target P&L**: +2-3% per trade (after 1.2% fees)
- **Win Rate Goal**: 40-50% (tight stops, fast exits)
- **Risk Profile**: Lower risk (smaller size, quick exits)

### Strategy #2: 24-Hour Runners
- **Trade Frequency**: Low (1-3 per week)
- **Hold Time**: Hours to days (max 48h)
- **Target P&L**: +8-15% per trade (let big moves run)
- **Win Rate Goal**: 30-40% (wider stops, bigger targets)
- **Risk Profile**: Higher risk (larger size, longer holds)

### Strategy #3: Score-Based
- **Trade Frequency**: Medium (daily)
- **Hold Time**: Hours to days
- **Target P&L**: +2-3% per trade (standard targets)
- **Win Rate Goal**: 25-35% (current system baseline)
- **Risk Profile**: Medium risk (standard sizing)

### Fee Impact Analysis

**All Strategies**:
- Maker fee: 0.6% (entry)
- Taker fee: 1.2% (exit)
- **Round-trip fee**: 1.8% (was 1.2% on Intro 2 tier, now Intro 3 tier)

**Break-Even Requirements**:
- Must achieve >1.8% gross profit to net positive
- 20-minute: 2% target → +0.2% net (tight margin)
- 24-hour: 8-15% target → +6.2-13.2% net (healthy margin)
- Score-based: 2% target → +0.2% net (tight margin)

---

## Risk Analysis

### Position Sizing Risk
**Monthly Volume Estimate** (assuming ~700 trades/month total):
- 20-minute: ~20% of trades → 140 trades × $15 = **$2,100/month**
- 24-hour: ~5% of trades → 35 trades × $20 = **$700/month**
- Score-based: ~75% of trades → 525 trades × $15 = **$7,875/month**

**Total ROC Exposure**: $2,800/month across both momentum strategies

### Strategy-Specific Risks

**20-Minute Scalps**:
- ⚠️ **Fee drag**: 1.8% fees on 2% target = 90% of gross profit consumed
- ⚠️ **Noise risk**: 1-min data can generate false signals on low volume
- ✅ **Mitigation**: Smaller size ($15), RSI gate filters extremes

**24-Hour Runners**:
- ⚠️ **Reversal risk**: Enter markets already up 10%+, may be exhausted
- ⚠️ **Exit timing**: 8% drawdown may give back too much profit
- ✅ **Mitigation**: Peak tracking locks in gains, 48h max hold prevents unlimited losses

**Score-Based**:
- ⚠️ **Low win rate**: Historical 25% (3 losses for every win)
- ⚠️ **Fee drag**: Same as 20-minute (1.8% on 2% target)
- ✅ **Mitigation**: Multi-indicator confirmation reduces false signals

---

## Recommendations

### Immediate Actions (No Code Changes)
1. ✅ **Deploy current configuration** - Already deployed to AWS
2. 📊 **Monitor for 7-14 days** - Track each strategy separately
3. 📈 **Measure strategy-specific metrics**:
   - Win rate per trigger type (ROC_MOMO_20M vs ROC_MOMO_24H vs SCORE)
   - Average P&L per trigger type
   - Hold time distribution
   - False signal rate

### Week 1 Analysis
Run these queries after 7 days:

```sql
-- 20-Minute Strategy Performance
SELECT
    COUNT(*) as total_trades,
    SUM(CASE WHEN realized_profit > 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*) as win_rate_pct,
    AVG(realized_profit) as avg_pnl,
    SUM(realized_profit) as total_pnl
FROM trade_records
WHERE trigger_type = 'ROC_MOMO_20M'
    AND order_time >= NOW() - INTERVAL '7 days';

-- 24-Hour Strategy Performance
-- (Same query with trigger_type = 'ROC_MOMO_24H')

-- Score-Based Performance
-- (Same query with trigger_type IN ('SCORE', 'SIGNAL'))
```

### Decision Criteria (After 7-14 Days)

**20-Minute Strategy**:
- If win rate > 45% AND avg P&L > $0.25: ✅ Keep, consider increasing size
- If win rate 35-45% AND avg P&L > $0: ⚠️ Keep, monitor closely
- If win rate < 35% OR avg P&L < $0: ❌ Disable, add volume filter

**24-Hour Strategy**:
- If win rate > 35% AND avg P&L > $1.00: ✅ Keep, consider increasing size
- If win rate 25-35% AND avg P&L > $0: ⚠️ Keep, consider tighter drawdown (6%)
- If win rate < 25% OR avg P&L < $0: ❌ Reduce size to $15 or disable

### Future Enhancements (If Validated)
1. **Volume Filter**: Add volume confirmation to 20-minute strategy (reduce false signals)
2. **Time-of-Day Filter**: Restrict to high-liquidity hours (9am-4pm ET)
3. **Asymmetric Thresholds**: Test 3% buy threshold for 20-minute (reduce noise)
4. **Peak Tracking Tuning**: Adjust drawdown % based on actual move sizes

---

## Summary

### Current State ✅
- **Three independent strategies** working together
- **Priority-based execution** (20m → 24h → scoring)
- **Strategy-specific sizing** ($15, $20, $15)
- **Distinct exit logic** per strategy (trailing, peak, standard)
- **Critical bugs fixed** (avg_entry validation, order sizing)

### Strengths 💪
- **Multi-timeframe coverage**: Scalps (20m) + runners (24h) + fallback (scoring)
- **Smart prioritization**: Fastest signals execute first
- **Flexibility**: Each strategy tuned for its market behavior
- **Safety**: Average entry validation prevents false exits

### Weaknesses ⚠️
- **Fee drag**: 1.8% round-trip consumes most of tight targets
- **Unknown performance**: Need 7-14 days to validate win rates
- **Tight margins**: 20-minute and score-based have minimal profit buffer
- **Risk asymmetry**: 24-hour enters already-pumped markets

### Next Step 🎯
**Monitor production performance for 7-14 days**, then analyze strategy-specific metrics to determine which strategies are profitable and which need adjustment or disabling.

---

**Status**: Documentation complete and accurate
**Location**: Will be moved to `docs/active/features/roc-multi-strategy-system.md`
**Last Verified**: January 17, 2026 against production code
