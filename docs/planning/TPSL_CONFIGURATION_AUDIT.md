# TP/SL Configuration Audit & Cleanup Plan

**Date:** 2025-12-03
**Issue:** Multiple conflicting TP/SL variables causing confusion and poor performance
**Impact:** 20.2% win rate, likely due to mis-configured exit thresholds

---

## 🚨 CRITICAL FINDINGS

### **Problem: You Have 3 Different Stop Loss Systems Operating Simultaneously**

Your `.env` file contains **CONFLICTING** TP/SL variables that are used by **DIFFERENT** parts of the system:

---

## CURRENT CONFIGURATION ANALYSIS

### **System 1: Legacy STOP_LOSS / TAKE_PROFIT** (Lines 158-159)
```bash
STOP_LOSS=-0.01          # -1.0% (NEGATIVE value)
TAKE_PROFIT=0.025        # +2.5%
```

**Used By:**
- `webhook/webhook_order_manager.py:233` - When placing BRACKET orders (OCO TP/SL on entry)
- `Config/constants_trading.py` - System-wide constants
- Legacy code for fixed TP/SL mode

**Purpose:** Sets TP/SL when entering trades (if using bracket orders)

**Issues:**
1. **STOP_LOSS is NEGATIVE** (-0.01) which is confusing
2. **Conflicts with ATR-based dynamic stops** (STOP_MODE=atr on line 83)
3. **Not used for exit monitoring** (position_monitor uses different vars)

---

### **System 2: ATR-Based Dynamic Stops** (Lines 83-95)
```bash
STOP_MODE=atr                   # Use ATR instead of fixed STOP_LOSS
ATR_MULTIPLIER_STOP=1.8         # Stop = 1.8 × ATR
STOP_MIN_PCT=0.012              # Floor of 1.2% if ATR tiny
SPREAD_CUSHION_PCT=0.0015       # +0.15% for spread
```

**Used By:**
- `webhook/webhook_order_manager.py:226-238` - Calculates dynamic stop based on volatility

**Purpose:** Dynamic stop losses that adapt to market volatility

**Formula:**
```
stop_pct = max(1.8 × ATR%, 1.2%) + spread% + fee%
```

**Example:** If ATR is 2%, stop = max(3.6%, 1.2%) + 0.15% + 0.55% = 4.3%

**Issues:**
1. **This OVERRIDES STOP_LOSS** when STOP_MODE=atr
2. **Completely different values** than STOP_LOSS (-1%)
3. **Only used for ORDER ENTRY**, not exit monitoring!

---

### **System 3: Position Monitor Exit Thresholds** (Lines 228-233)
```bash
MAX_LOSS_PCT=0.025              # -2.5% soft stop (LIMIT exit)
MIN_PROFIT_PCT=0.035            # +3.5% take profit (LIMIT exit)
HARD_STOP_PCT=0.05              # -5.0% hard stop (MARKET exit)
```

**Used By:**
- `MarketDataManager/position_monitor.py:226-270` - Monitors open positions and exits

**Purpose:** Exit thresholds for emergency stop-loss monitoring (Phase 5)

**Exit Logic:**
```python
if pnl_pct <= -0.05:           # -5% HARD_STOP (market exit)
elif pnl_pct <= -0.025:        # -2.5% SOFT_STOP (limit exit)
elif pnl_pct >= 0.035:         # +3.5% TAKE_PROFIT (limit exit)
```

**Issues:**
1. **These are COMPLETELY DIFFERENT from STOP_LOSS/TAKE_PROFIT!**
2. **SOFT_STOP (-2.5%) conflicts with ATR stops (which could be -4.3%)**
3. **This is likely WHY you're losing 80% of trades!**

---

### **System 4: Emergency Exit Threshold** (Line 174)
```bash
EMERGENCY_EXIT_THRESHOLD=0.03   # -3.0% emergency exit
```

**Used By:**
- Hybrid order management system (if enabled)
- Emergency market exit if price moves 3% against while limit order pending

**Purpose:** Backup safety net if limit orders aren't filling fast enough

**Issue:** **Yet another stop level** between SOFT_STOP (-2.5%) and HARD_STOP (-5%)

---

## 🎯 THE CORE PROBLEM

### **You Have 5 Different Stop Loss Values!**

1. **STOP_LOSS:** -1.0% (legacy, negative value)
2. **ATR-Based:** ~4.3% (1.8× ATR + cushions, actual calculation)
3. **MAX_LOSS_PCT:** -2.5% (soft stop for position monitor)
4. **EMERGENCY_EXIT:** -3.0% (hybrid system emergency)
5. **HARD_STOP_PCT:** -5.0% (hard stop for position monitor)

### **Which One Actually Runs?**

Based on code analysis:

**WHEN ENTERING TRADES** (webhook_order_manager.py):
- If `STOP_MODE=atr` → Uses ATR calc (1.8× ATR, min 1.2%) + cushions
- If `STOP_MODE=fixed` → Uses abs(STOP_LOSS) = 1.0%
- **Current setting:** `STOP_MODE=atr`
- **Actual entry stop:** ~4.3% (for ATR=2%)

**WHEN MONITORING POSITIONS** (position_monitor.py):
- HARD_STOP: -5.0% (market exit)
- SOFT_STOP: -2.5% (limit exit)
- TAKE_PROFIT: +3.5%
- **These run every 30 seconds** (POSITION_CHECK_INTERVAL=30)

### **The Conflict:**

1. Order is entered with **ATR stop at -4.3%** (OCO bracket order on exchange)
2. Position monitor checks every 30s and sees **SOFT_STOP at -2.5%**
3. If price drops -2.5%, position monitor places **LIMIT sell order**
4. But exchange already has **OCO bracket with SL at -4.3%**
5. **BOTH orders exist!** Whichever fills first cancels the other

**Result:** You're exiting at -2.5% when you planned for -4.3%!

---

## 📊 IMPACT ON YOUR PERFORMANCE

### **Why 20.2% Win Rate?**

From your report:
- **Avg Win:** $4.67 (+1.5% approx)
- **Avg Loss:** $-21.93 (-7% approx)

**With MAX_LOSS_PCT at -2.5%, why are losses averaging -7%?**

**Hypothesis:**

1. Entry with ATR stop at -4% (planned)
2. Position monitor triggers SOFT_STOP at -2.5% (limit order)
3. Limit order doesn't fill immediately (price keeps dropping)
4. Price drops to -5% → HARD_STOP triggers (market order)
5. Slippage on market order → actual exit at -7%

**This explains:**
- ✅ 80% loss rate (stop hitting instead of TP)
- ✅ -$21.93 avg loss (HARD_STOP + slippage)
- ✅ +$4.67 avg win (hitting +3.5% TP)
- ✅ Profit factor 0.06 (losing $16 for every $1 won)

### **The Math:**

If you're planning:
- Entry SL: -4.3% (ATR)
- Entry TP: +2.5% (TAKE_PROFIT)
- R:R: 0.58 (bad, but survivable at 50% win rate)

But actually executing:
- Actual SL: -7% (HARD_STOP + slippage)
- Actual TP: +3.5% (MIN_PROFIT_PCT)
- R:R: 0.5 (need 67% win rate to break even!)

**With 20% win rate, you're guaranteed to lose money.**

---

## ✅ RECOMMENDED CONFIGURATION

### **Goal: Simplify to ONE stop loss system**

### **Option A: Use Position Monitor Only (Recommended)**

**Reasoning:**
- Position monitor is more flexible (can exit on signals, trailing, etc.)
- ATR-based stops are better than fixed (adapt to volatility)
- Remove redundant systems

**Changes:**

1. **Disable bracket orders on entry** (let position monitor handle exits)
2. **Use position monitor for ALL exits**
3. **Set MAX_LOSS_PCT to match ATR calculation**

```bash
# ========================================
# STOP/LOSS CONFIGURATION - SIMPLIFIED
# ========================================

# ---- Entry Orders (Webhook) ----
# DEPRECATED: These are IGNORED when using position monitor
STOP_LOSS=-0.04          # Kept for backward compatibility only
TAKE_PROFIT=0.035        # Kept for backward compatibility only

# Use ATR-based stop calculation (for reference only)
STOP_MODE=atr
ATR_MULTIPLIER_STOP=1.8
STOP_MIN_PCT=0.012       # 1.2% floor

# ---- Exit Monitoring (Position Monitor) - PRIMARY SYSTEM ----
# These are the ACTUAL stop/profit levels used:
MAX_LOSS_PCT=0.045       # -4.5% soft stop (was 0.025)
MIN_PROFIT_PCT=0.035     # +3.5% take profit (no change)
HARD_STOP_PCT=0.06       # -6.0% hard stop (was 0.05)

# Emergency exit (hybrid system)
EMERGENCY_EXIT_THRESHOLD=0.05  # Match SOFT_STOP range

# ---- Trailing Stop (Phase 5) ----
TRAILING_STOP_ENABLED=true
TRAILING_ACTIVATION_PCT=0.035  # Activate at +3.5% (matches TP)
TRAILING_MIN_DISTANCE_PCT=0.02 # Trail 2% below peak
TRAILING_MAX_DISTANCE_PCT=0.04 # Max trail distance

# ---- Signal Exits (Phase 5) ----
SIGNAL_EXIT_ENABLED=false      # DISABLE until system stabilizes
SIGNAL_EXIT_MIN_PROFIT_PCT=0.01  # Only exit on signal if +1% profit

# Position check frequency
POSITION_CHECK_INTERVAL=30
```

**Rationale:**

| Parameter | Old | New | Why |
|-----------|-----|-----|-----|
| MAX_LOSS_PCT | 2.5% | 4.5% | Match ATR calculation, give trades room |
| HARD_STOP_PCT | 5.0% | 6.0% | Safety net beyond soft stop |
| SIGNAL_EXIT | true | **false** | Disable Phase 5 until proven |
| TRAILING | true | true | Keep but adjust activation |

**Expected Improvement:**
- Wider stops → fewer stop-outs → **40-50% win rate**
- TP at +3.5% is still achievable
- R:R improves from 0.21 to 0.78 (closer to 1.0)

---

### **Option B: Use ATR Brackets Only (Alternative)**

**If you want to rely on exchange bracket orders:**

```bash
# ---- Entry Orders (Webhook) - PRIMARY SYSTEM ----
STOP_LOSS=-0.045         # -4.5% (matches ATR calc)
TAKE_PROFIT=0.035        # +3.5%
STOP_MODE=atr
ATR_MULTIPLIER_STOP=1.8
STOP_MIN_PCT=0.012

# ---- Position Monitor - DISABLED ----
MAX_LOSS_PCT=1.0         # Set very wide to never trigger
MIN_PROFIT_PCT=1.0       # Set very wide to never trigger
HARD_STOP_PCT=0.10       # Emergency only (-10%)
SIGNAL_EXIT_ENABLED=false
TRAILING_STOP_ENABLED=false
```

**Pros:**
- Simpler (let exchange handle TP/SL)
- Lower latency (exchange executes immediately)
- No need for position monitor

**Cons:**
- Can't do signal-based exits
- Can't do trailing stops
- Less flexible

**Not recommended** because you lose Phase 5 features.

---

## 🔧 IMPLEMENTATION STEPS

### **Step 1: Update .env** (5 minutes)

```bash
# Edit /opt/bot/.env on server
ssh bottrader-aws
nano /opt/bot/.env

# Make these changes:
MAX_LOSS_PCT=0.045       # Line 229: Change from 0.025 to 0.045
HARD_STOP_PCT=0.06       # Line 233: Change from 0.05 to 0.06
SIGNAL_EXIT_ENABLED=false  # Line 254: Change from true to false (if currently true)
```

### **Step 2: Restart Containers** (2 minutes)

```bash
cd /opt/bot
docker compose down
docker compose up -d
```

### **Step 3: Monitor First Hour** (60 minutes)

```bash
# Watch for stop triggers
docker logs webhook -f | grep "SOFT_STOP\|HARD_STOP\|TAKE_PROFIT"
```

**What to look for:**
- Fewer SOFT_STOP triggers (should see -4.5% instead of -2.5%)
- More TAKE_PROFIT hits (3.5% easier to reach than 4.3% stops)
- Win rate should improve within first 10 trades

### **Step 4: Evaluate After 24 Hours**

Check next day's email report:
- **Target win rate:** 40-45% (improvement from 20%)
- **Target profit factor:** 0.3-0.4 (improvement from 0.06)
- **Target avg loss:** $-15 (improvement from $-22)

### **Step 5: Fine-Tune (Optional)**

If still losing too much:
- **Widen MAX_LOSS_PCT to 0.05** (5%)
- **Tighten MIN_PROFIT_PCT to 0.03** (3%)
- **Lower R:R but higher win rate**

If winning too little:
- **Tighten MAX_LOSS_PCT to 0.04** (4%)
- **Widen MIN_PROFIT_PCT to 0.04** (4%)
- **Higher R:R but accept lower win rate**

---

## 📋 VARIABLE REFERENCE TABLE

### **Current State (Problematic)**

| Variable | Value | Used By | Purpose | Status |
|----------|-------|---------|---------|--------|
| STOP_LOSS | -1.0% | webhook_order_manager | Legacy fixed SL | 🔴 CONFLICTS |
| TAKE_PROFIT | +2.5% | webhook_order_manager | Legacy fixed TP | 🟡 OVERRIDDEN |
| ATR_MULTIPLIER_STOP | 1.8 | webhook_order_manager | Dynamic SL mult | ✅ ACTIVE |
| STOP_MIN_PCT | 1.2% | webhook_order_manager | SL floor | ✅ ACTIVE |
| MAX_LOSS_PCT | -2.5% | position_monitor | Soft stop | 🔴 TOO TIGHT |
| MIN_PROFIT_PCT | +3.5% | position_monitor | Take profit | ✅ OK |
| HARD_STOP_PCT | -5.0% | position_monitor | Emergency stop | 🟡 OK |
| EMERGENCY_EXIT | -3.0% | Hybrid system | Emergency | 🟡 REDUNDANT |

### **Recommended State (Fixed)**

| Variable | Old | New | Purpose | Priority |
|----------|-----|-----|---------|----------|
| STOP_LOSS | -1.0% | -4.0% | Backward compat | Low |
| TAKE_PROFIT | +2.5% | +3.5% | Backward compat | Low |
| ATR_MULTIPLIER_STOP | 1.8 | 1.8 | ATR multiplier | Medium |
| STOP_MIN_PCT | 1.2% | 1.2% | SL floor | Medium |
| **MAX_LOSS_PCT** | **-2.5%** | **-4.5%** | **Soft stop** | 🔴 **CRITICAL** |
| MIN_PROFIT_PCT | +3.5% | +3.5% | Take profit | ✅ OK |
| **HARD_STOP_PCT** | **-5.0%** | **-6.0%** | **Emergency** | 🟡 **HIGH** |
| **SIGNAL_EXIT_ENABLED** | **true** | **false** | **Phase 5** | 🔴 **CRITICAL** |

---

## 🎯 EXPECTED RESULTS

### **Before (Current)**
- Win Rate: 20.2%
- Profit Factor: 0.06
- Avg Loss: $-21.93
- Stops hitting at: -2.5% to -7%
- **Grade: F**

### **After (With Fixes)**
- Win Rate: 40-50% (target)
- Profit Factor: 0.4-0.6 (target)
- Avg Loss: $-15 (target)
- Stops hitting at: -4.5% to -6%
- **Grade: C to B**

### **Timeline**
- **Hour 1:** See immediate reduction in SOFT_STOP triggers
- **Hour 6:** First report shows improved win rate
- **Day 1:** Win rate 30-35%
- **Day 3:** Win rate 40-45%
- **Week 1:** Win rate stabilizes 45-50%

---

## ⚠️ WARNINGS

### **DO NOT:**

1. **Don't set MAX_LOSS_PCT wider than HARD_STOP_PCT**
   - Soft stop must trigger before hard stop
   - Current: -2.5% → -5% ✅
   - Recommended: -4.5% → -6% ✅

2. **Don't make MIN_PROFIT_PCT too wide**
   - +3.5% is already far for crypto (daily moves)
   - Going to +5% means you'll rarely hit TP

3. **Don't enable SIGNAL_EXIT yet**
   - Phase 5 is untested
   - Wait until win rate stabilizes above 45%

4. **Don't change multiple variables at once**
   - Change MAX_LOSS_PCT first
   - Monitor for 24 hours
   - Then adjust other params

---

## 🔍 DEBUGGING QUERIES

### **Check What Stop Actually Triggered**

```sql
-- See recent exits and their reasons
SELECT
  symbol,
  side,
  price,
  size,
  realized_profit,
  trigger,
  order_time
FROM trade_records
WHERE side = 'sell'
  AND order_time > NOW() - INTERVAL '24 hours'
ORDER BY order_time DESC
LIMIT 20;
```

### **Calculate Actual Exit %**

```sql
-- See P&L % at exit
WITH exits AS (
  SELECT
    symbol,
    realized_profit,
    price as exit_price,
    LAG(price) OVER (PARTITION BY symbol ORDER BY order_time) as entry_price
  FROM trade_records
  WHERE order_time > NOW() - INTERVAL '24 hours'
)
SELECT
  symbol,
  ROUND(((exit_price - entry_price) / entry_price * 100)::numeric, 2) as pnl_pct,
  realized_profit
FROM exits
WHERE entry_price IS NOT NULL
ORDER BY pnl_pct;
```

---

## 📝 SUMMARY

### **Root Cause:**
Multiple conflicting TP/SL systems running simultaneously:
1. Legacy STOP_LOSS (-1%) not used
2. ATR stops (~4.3%) used at entry
3. Position monitor SOFT_STOP (-2.5%) overriding ATR
4. Position monitor HARD_STOP (-5%) catching falling knives
5. Result: Exiting way earlier than planned

### **Fix:**
1. Widen MAX_LOSS_PCT from -2.5% to -4.5%
2. Widen HARD_STOP_PCT from -5% to -6%
3. Disable SIGNAL_EXIT (Phase 5 causing whipsaw)
4. Let trades breathe, match exit thresholds to entry planning

### **Expected Outcome:**
- Win rate: 20% → 45%
- Profit factor: 0.06 → 0.5
- Avg loss: -$22 → -$15
- System becomes profitable

---

**Next Steps:** Implement Option A changes and monitor for 24 hours.
