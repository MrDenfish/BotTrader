# BotTrader Performance Analysis
**Date:** 2025-12-03 09:05 UTC
**Runtime:** ~14 hours
**Analyst:** Claude Code
**Based on:** Latest daily email report

---

## 🚨 CRITICAL FINDINGS - IMMEDIATE ATTENTION REQUIRED

### 1. **Catastrophic Win Rate: 20.2% (19 wins / 94 trades)**
**Status:** 🔴 **CRITICAL**

**What This Means:**
- You're losing **4 out of every 5 trades**
- This is FAR below the break-even win rate (~50% for similar win/loss sizes)
- The bot is bleeding money steadily

**Expected vs Actual:**
- **Minimum acceptable win rate:** 45-50%
- **Actual win rate:** 20.2%
- **Gap:** -25 to -30 percentage points

**Root Cause Analysis (from architecture knowledge):**

Based on the ARCHITECTURE_DEEP_DIVE.md and current system state:

1. **Exit Logic May Be Too Aggressive**
   - Stop losses triggering before take profits
   - The system has 6 different exit paths (see architecture doc)
   - **Avg Loss: $-21.93** vs **Avg Win: $4.67**
   - **Loss is 4.7x larger than win** (should be inverse)

2. **Profit Factor: 0.06**
   - This means for every $1 you win, you lose $16.67
   - **Healthy profit factor:** >1.5 (you win $1.50 for every $1 lost)
   - **Current state:** 0.06 (losing $16 for every $1 won)

3. **Break-Even Analysis:**
   - Round-trip taker break-even: 1.10%
   - Round-trip maker break-even: 0.60%
   - **Your average LOSS is -21.93%** - way beyond fees
   - Something is fundamentally wrong with stop-loss placement

---

## 2. **Max Drawdown: 319.7%**
**Status:** 🔴 **CATASTROPHIC**

**What This Means:**
- You've lost **more than 3x your starting capital**
- If you started with $500, you're down by $1,598.50
- Current equity is likely in the negative or near zero

**Comparison:**
- **Healthy drawdown:** <20% (lose no more than $100 on $500)
- **Acceptable drawdown:** <30%
- **Danger zone:** >50%
- **Your drawdown:** 319.7% ⚠️

**Why This Happened:**
According to the architecture documentation, the system just deployed **Phase 5 (signal-based exits)** on Nov 30, 2025. This is BRAND NEW code that hasn't been properly validated.

From `ARCHITECTURE_DEEP_DIVE.md` Section 6:
> **Critical Risk Areas**
> 🚨 Exit logic verification - Multiple exit paths need consolidation
> 🚨 Phase 5 just deployed - Signal-based exits are brand new (Nov 30, 2025)

---

## 3. **Trigger Breakdown Shows System Confusion**
**Status:** 🟡 **WARNING**

```
Trigger       Orders  Wins  Losses  Win Rate  Total PnL
UNKNOWN       1       0     1       0.0%      $-7.73
LIMIT         78      14    64      17.9%     $-1,343.96
```

**Issues:**
1. **UNKNOWN trigger:** System doesn't know why 1 trade executed
   - This suggests the `trigger` field tracking is incomplete (per architecture doc)
   - From architecture: *"trigger field doesn't capture exit reason"*

2. **LIMIT-only exits:** All 78 real trades came from LIMIT exits
   - Where are the emergency stop-losses you fixed yesterday?
   - Where are the POSITION_MONITOR exits?
   - **This suggests emergency stop logic may not be active**

---

## 4. **FIFO Health: 17 Unmatched Sells**
**Status:** 🟡 **WARNING**

```
Version: 2
Total Allocations: 3,827
Sells Matched: 3,061
Buys Used: 3,045
Status: ⚠ 17 unmatched sells
Total PnL: $-1,202.39
```

**What This Means:**
- 17 sell orders have no corresponding buy order in the FIFO system
- This could indicate:
  1. Incomplete FIFO calculation (some buys missing)
  2. Reconciliation issues with Coinbase API
  3. Data corruption from earlier bugs

**Impact:**
- P&L calculations may be slightly inaccurate
- Not critical (only 17/3,827 = 0.4% of allocations)
- But should be investigated

**From Architecture Knowledge:**
The system runs `run_maintenance_if_needed()` on startup which:
- Recalculates FIFO allocations
- Resets `pnl_usd` for incomplete trades
- This is why old data analysis may be incorrect

---

## 5. **TP/SL Decision Quality Looks Good (But Not Helping)**
**Status:** ✅ **GOOD** (but irrelevant given losses)

```
Total: 1,364 decisions
Avg R:R: 1.59
Median R:R: 1.58
R:R < 1.0: 0.44% (only 6 bad decisions)
```

**What This Means:**
- The bot is PLANNING trades well (good risk:reward ratios)
- 99.56% of TP/SL decisions have R:R > 1.0
- Average R:R of 1.59 means plan to win $1.59 for every $1 risked

**The Problem:**
- **PLANNING is good, EXECUTION is terrible**
- Good R:R means nothing if stop losses trigger 80% of the time
- This confirms the issue is in **exit logic**, not entry selection

---

## 6. **Position Sizing Appears Conservative**
**Status:** ✅ **GOOD**

```
Total Notional: $246.16
Top 3 Exposures:
- BCH-USD: $33.82 (13.7%) - SHORT
- XLM-USD: $33.07 (13.4%) - LONG
- BONK-USD: $32.90 (13.4%) - LONG
```

**What This Means:**
- No single position exceeds 14% of portfolio
- Diversified across 3+ symbols
- Position sizing is responsible

**Good News:**
- You're not over-leveraging
- The losses are from **poor execution**, not reckless position sizes

---

## 7. **Near-Instant Roundtrips: Mixed Results**
**Status:** 🟡 **NEUTRAL**

```
Count: 4 trades
Median hold: 46 seconds
Total PnL: $-0.47

Examples:
- FARTCOIN-USD: 33s hold, -0.19% (-$0.06)
- CLANKER-USD: 57s hold, +0.12% (+$0.04)  ✅
- A8-USD: 59s hold, -1.05% (-$0.35)
- PUMP-USD: 35s hold, -0.28% (-$0.09)
```

**What This Means:**
- 3 losses, 1 win in fast roundtrips
- These are likely signal reversals or quick stop-outs
- Consistent with the 20% overall win rate

---

## WHAT'S PERFORMING WELL (Limited Bright Spots)

### ✅ 1. System Stability
- Bot ran for 14 hours without crashes
- All containers healthy
- Database connectivity stable
- WebSocket feeds operational

### ✅ 2. FIFO Accounting
- 99.6% of trades properly allocated (3,810/3,827)
- P&L calculation accurate (within FIFO margin)
- Database integrity maintained

### ✅ 3. Order Execution
- Orders placed and filled successfully
- No evidence of rejected orders
- Latency appears acceptable (roundtrips in 30-60s)

### ✅ 4. Risk:Reward Planning
- Avg R:R of 1.59 is good
- Only 0.44% of decisions have R:R < 1.0
- Entry selection appears sound

### ✅ 5. Infrastructure
- Email reports generating successfully
- CSV exports working
- Monitoring systems operational

---

## WHAT NEEDS IMMEDIATE ATTENTION

### 🔴 1. EXIT LOGIC (HIGHEST PRIORITY)

**Problem:** Stop losses triggering 4x more often than take profits

**Evidence:**
- Win rate: 20.2% (should be ~50%+)
- Avg loss ($-21.93) is 4.7x larger than avg win ($4.67)
- Profit factor: 0.06 (should be >1.5)

**Root Cause (from architecture):**

From `ARCHITECTURE_DEEP_DIVE.md`, Section 6 - Exit Logic Paths:

The system has **6 different exit mechanisms:**

1. **POSITION_MONITOR** (emergency stops)
   - HARD_STOP at -5%
   - SOFT_STOP at -2.5%
   - Trailing stops
   - Signal-based exits (Phase 5 - NEW)

2. **Take Profit Limits**
   - OCO (One-Cancels-Other) orders
   - Limit orders at TP price

3. **Stop Loss Orders**
   - OCO stop orders
   - Manual stop placement

4. **Signal Reversals**
   - Phase 5 feature (BRAND NEW)
   - Exits when buy signal reverses to sell

5. **Manual Exits**
   - User-triggered

6. **Liquidations/Forced Exits**
   - Exchange-triggered

**The Issue:**
Your trigger breakdown shows **ONLY LIMIT exits** (78 trades). This means:
- Emergency stop losses (HARD_STOP/SOFT_STOP) **NOT executing**
- Take profit limits ARE executing (but rarely hitting)
- Something is fundamentally broken in the exit hierarchy

**Recommended Actions:**

1. **IMMEDIATELY** review position_monitor.py:2026-2090
   - Verify emergency stop logic is actually running
   - Check if stops are being placed on exchange
   - Confirm trigger field is being set correctly

2. **Review Phase 5 signal-based exits:**
   - From architecture: "Phase 5 just deployed - Signal-based exits are brand new"
   - This was deployed Nov 30, only 3 days ago
   - May be causing premature exits

3. **Validate exit hierarchy:**
   - TP should trigger BEFORE SL in profitable trades
   - Current ratio suggests SL firing first almost always
   - Check if TP/SL distances are correct

4. **Audit the buy_sell_matrix:**
   - From architecture: "Phase 5 uses buy_sell_matrix for signal tracking"
   - If signals are flipping rapidly, exits trigger too soon
   - May need signal stability filters

**Specific Files to Investigate:**

Per `ARCHITECTURE_DEEP_DIVE.md`:

```
MarketDataManager/position_monitor.py:690-780
  - Emergency stop logic (HARD_STOP, SOFT_STOP)
  - verify_position_exits() function

webhook/websocket_market_manager.py:850-900
  - Exit reason tracking
  - Trigger field assignment

SharedDataManager/trade_recorder.py:450-550
  - FIFO allocation
  - Exit reason capture
```

---

### 🔴 2. WIN RATE ANALYSIS (CRITICAL)

**Current:** 20.2% (19 wins, 75 losses)
**Target:** 50%+ minimum
**Gap:** -30 percentage points

**Possible Causes:**

**A) Stop Loss Too Tight:**
- If SL is at 2% but market volatility is 3%, stops will trigger constantly
- Check if SOFT_STOP (-2.5%) is too close to entry
- Compare SL distance to symbol volatility (ATR)

**B) Take Profit Too Far:**
- If TP is at 5% but average move is 2%, will rarely hit
- R:R of 1.59 suggests TP is 1.59x further than SL
- With 80% loss rate, TP is clearly out of reach

**C) Signal Reversals (Phase 5):**
- New feature exits on signal flip
- If signals are noisy, exits happen prematurely
- May need signal confirmation (wait for 2-3 bars)

**D) Market Conditions:**
- Choppy/sideways markets kill trend-following strategies
- Check if volatility has changed recently
- May need different strategy for range-bound markets

**Recommended Actions:**

1. **Widen stop losses to 5% (HARD_STOP only)**
   - Keep SOFT_STOP disabled temporarily
   - Let trades breathe more room

2. **Tighten take profits to 3%**
   - Lower R:R but higher win rate
   - Easier to hit targets

3. **Add signal confirmation filter:**
   - Don't exit immediately on signal flip
   - Wait for 2-3 consecutive opposite signals
   - Reduces whipsaw

4. **Disable Phase 5 temporarily:**
   - Test if signal-based exits are the culprit
   - Revert to classic TP/SL only for 24 hours
   - Compare results

---

### 🟡 3. DATA INTEGRITY (MEDIUM PRIORITY)

**Issue:** 17 unmatched sells in FIFO

**Investigation Steps:**

1. **Check database logs:**
   ```sql
   SELECT * FROM trade_records
   WHERE order_id NOT IN (
     SELECT DISTINCT unnest(parent_ids) FROM trade_records WHERE side='sell'
   )
   AND side='buy'
   LIMIT 20;
   ```
   This finds buys that were never allocated to sells

2. **Review reconciliation logs:**
   - Check if Coinbase API reconciliation is working
   - From architecture: runs every 300 seconds (5 min)
   - May be missing some fills

3. **Run FIFO maintenance manually:**
   - The system auto-runs on startup
   - May need manual trigger to fix unmatched sells

---

### 🟡 4. MONITORING GAPS (MEDIUM PRIORITY)

**Issues Identified:**

1. **No score data found:**
   - Signal scoring system not populating `scores.jsonl`
   - May impact Phase 5 exit decisions
   - Check why scores aren't being logged

2. **Strategy column missing:**
   - Report shows "missing strategy-like column"
   - Can't analyze performance by strategy
   - Add strategy field to trade_records?

3. **Cash table not found:**
   - "table not found: public.report_balances"
   - Can't track account balance accurately
   - Create missing table or update report to use alternative source

---

## ARCHITECTURAL INSIGHTS (from ARCHITECTURE_DEEP_DIVE.md)

### System Strengths Confirmed:

1. **Clean Separation of Concerns:**
   ✅ Sighook (signal generation) and Webhook (execution) are properly separated
   ✅ No evidence of signal/execution coupling issues

2. **FIFO Accounting:**
   ✅ 99.6% allocation success rate
   ✅ Tax compliance maintained
   ✅ Database-first design working well

3. **Health Checks:**
   ✅ All containers showing healthy
   ✅ WebSocket reconnections handled gracefully
   ✅ Database connections stable

4. **Structured Logging:**
   ✅ Comprehensive logs available
   ✅ Easy debugging via log queries
   ✅ Performance metrics captured

### Critical Risks Realized:

1. **Exit Logic Verification:** ⚠️ **CONFIRMED ISSUE**
   - Architecture warned: "Multiple exit paths need consolidation"
   - Reality: 80% loss rate suggests exit logic is broken
   - **This is your #1 problem**

2. **Phase 5 Deployment:** ⚠️ **HIGH RISK**
   - Architecture warned: "Phase 5 just deployed - brand new (Nov 30)"
   - Only 3 days old, likely not properly tested
   - Coincides with terrible win rate

3. **Order Loop Prevention:** ✅ **SEEMS OK**
   - No evidence of infinite order loops
   - Position sizing is conservative
   - No runaway trades observed

4. **Data Integrity:** 🟡 **MINOR ISSUES**
   - 17 unmatched sells (0.4% of total)
   - Not critical but needs monitoring
   - FIFO maintenance should address

---

## RECOMMENDATIONS BY PRIORITY

### 🔴 IMMEDIATE (Do Today)

1. **Disable Phase 5 Signal-Based Exits Temporarily**
   ```python
   # In main.py or config
   ENABLE_SIGNAL_EXITS = False
   ```
   - Test if this improves win rate
   - Run for 24 hours and compare

2. **Widen Stop Losses**
   ```python
   HARD_STOP_PCT = 0.05  # -5% (from -2.5%)
   SOFT_STOP_PCT = None  # Disable soft stop
   ```
   - Give trades more breathing room
   - Monitor if this reduces loss frequency

3. **Verify Emergency Stops Are Active**
   - SSH to server: `docker logs webhook | grep "HARD_STOP\|SOFT_STOP"`
   - Should see stop triggers in logs
   - If nothing, emergency stops aren't running

4. **Check Live Positions**
   - Verify stop-loss orders exist on Coinbase
   - Use REST API or web interface
   - If no stops on exchange, they're not being placed

### 🟡 SHORT-TERM (This Week)

5. **Adjust R:R Ratios**
   - Current planning: TP at 1.59x SL distance
   - Try: TP at 1.2x SL distance (closer targets)
   - Example: If SL is -4%, set TP at +4.8% (not +6.36%)

6. **Add Signal Confirmation**
   - Don't exit immediately on signal flip
   - Require 2-3 consecutive opposite signals
   - Reduces whipsaw in choppy markets

7. **Implement Symbol Performance Filters**
   - Good news: Symbol performance analysis now in reports!
   - Use it to **blacklist losing symbols**
   - Focus on symbols with >40% win rate

8. **Fix Data Integrity Issues**
   - Investigate 17 unmatched sells
   - Create missing `report_balances` table
   - Add strategy column to `trade_records`

### 🟢 LONG-TERM (Next 2 Weeks)

9. **Backtest Exit Logic Changes**
   - Before deploying new exit parameters live
   - Use historical data to validate improvements
   - Aim for 50%+ win rate in backtest

10. **Consolidate Exit Paths**
    - Per architecture: "Multiple exit paths need consolidation"
    - Simplify to: Emergency Stops + TP/SL only
    - Remove Phase 5 if it continues underperforming

11. **Add Market Regime Detection**
    - Identify trending vs ranging markets
    - Use different strategies for each regime
    - Current strategy seems optimized for trends only

12. **Implement Position Monitoring Dashboard**
    - Real-time view of open positions
    - See which stops/TPs are set
    - Monitor exit trigger distribution

---

## SUMMARY SCORECARD

| Metric | Score | Status | Target |
|--------|-------|--------|--------|
| **Win Rate** | 20.2% | 🔴 CRITICAL | 50%+ |
| **Profit Factor** | 0.06 | 🔴 CRITICAL | >1.5 |
| **Max Drawdown** | 319.7% | 🔴 CATASTROPHIC | <30% |
| **Avg Win/Loss Ratio** | 0.21 | 🔴 CRITICAL | >1.0 |
| **System Stability** | 100% | ✅ EXCELLENT | >95% |
| **FIFO Accuracy** | 99.6% | ✅ EXCELLENT | >99% |
| **R:R Planning** | 1.59 | ✅ GOOD | >1.5 |
| **Position Sizing** | Conservative | ✅ GOOD | Balanced |
| **Data Integrity** | 99.6% | 🟡 GOOD | 100% |
| **Exit Logic** | Broken | 🔴 CRITICAL | Fixed |

**Overall Grade: F** (Critical Failure in Core Trading Logic)

---

## FINAL ASSESSMENT

### What's Working:
✅ Infrastructure is rock solid
✅ Order execution is reliable
✅ FIFO accounting is accurate
✅ Planning (R:R ratios) is good
✅ Position sizing is responsible

### What's Broken:
🔴 **Exit logic is catastrophically broken**
🔴 **80% of trades are losers**
🔴 **Losing $16 for every $1 won**
🔴 **319% drawdown (lost 3x starting capital)**
🔴 **Phase 5 signal exits may be culprit**

### Root Cause:
Based on architecture analysis and report data:

**The bot is entering trades with good planning (R:R 1.59), but exiting too early via:**
1. Stop losses that are too tight (-2.5% SOFT_STOP)
2. Phase 5 signal-based exits firing on noise
3. Take profits that are too far away (missing)

**The system is well-built but mis-configured.**

### What To Do:
1. **IMMEDIATELY:** Disable Phase 5, widen stops, verify emergency stop logic
2. **THIS WEEK:** Adjust R:R, add signal filters, fix data issues
3. **NEXT 2 WEEKS:** Backtest, consolidate exits, add regime detection

### Prognosis:
**RECOVERABLE** - The infrastructure is solid, you just need to fix the exit logic. With the recommended changes, you should be able to get to 45-50% win rate and positive profit factor within 1-2 weeks of testing.

---

**Report Generated:** 2025-12-03
**Based On:** 14 hours runtime, 94 trades, 3,827 FIFO allocations
