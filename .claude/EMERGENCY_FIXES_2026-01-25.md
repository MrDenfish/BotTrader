# Emergency Fixes and Disabled Features
**Date**: January 25, 2026
**Session**: Performance Evaluation and Bug Fixes
**Status**: 🚨 IN PROGRESS

---

## Summary

This document tracks all emergency fixes, disabled features, and configuration changes made during the Jan 25, 2026 performance analysis session.

**Critical Issues Discovered**:
1. Position monitor has 0% win rate (18 exits, all losses)
2. Strategy mismatch: Running Multi-ROC instead of backtested Test 2
3. Trade frequency 52× higher than expected
4. Position monitor accounts for 73% of all losses

---

## Section 1: Emergency Disabling (TEMPORARY)

### 1.1 Position Monitor Exits - DISABLED
**File**: `MarketDataManager/position_monitor.py`
**Method**: `check_positions()` (line 340)
**Reason**: 0% win rate across all strategies, causing 73% of total losses (-$10.98 out of -$14.94)

**Evidence**:
- 18 position_monitor exits: 0 wins, 18 losses
- Avg loss: -$0.610 (vs -$0.036 for rearm_oco exits)
- Suspected bug: avg_entry_price calculation from API unrealized_pnl

**Disabled By**: Early return at start of `check_positions()`
**Disabled On**: January 25, 2026
**Will Re-enable**: After bug fix and local testing (estimated 24-48 hours)

**Mitigation**:
- Rearm_oco will handle all exits temporarily
- Rearm_oco performance: 37.8% win rate, -$0.036 avg loss (acceptable)

---

### 1.2 Multi-ROC Strategies - DISABLED
**Files**:
- `sighook/signal_manager.py` (ROC_MOMO_20M detection: ~lines 341-385)
- `sighook/signal_manager.py` (ROC_MOMO_24H detection: ~lines 387-440)

**Reason**: Strategy mismatch with backtest
- Backtest was for Test 2 (8.5% ROC threshold, RSI 45-55, 4% TP, 2% SL)
- Production was running Multi-ROC (10% 24h threshold, 2% 20m threshold)
- These are completely different strategies!

**Performance Before Disable**:
- ROC_MOMO_24H: 97 trades, 34.0% win rate, -$13.45 P&L
- ROC_MOMO_20M: 32 trades, 28.1% win rate, -$1.49 P&L
- Trade frequency: 15.5 trades/day (vs 0.3/day expected from backtest)

**Disabled By**: Commenting out signal detection blocks
**Disabled On**: January 25, 2026
**Will Re-enable**: After Test 2 evaluation period (7-14 days), review performance, may re-enable selectively

---

## Section 2: Configuration Alignment to Test 2 Backtest

### 2.1 Test 2 Parameters (ENABLED)
**Backtest Results**: -$1.94 loss over 60 days, 57.9% win rate, 19 trades
**Backtest File**: `.claude/sessions/2026-01-14-strategy-optimization-backtest.md`

**Required Configuration**:
```bash
# ROC Momentum Entry (stricter)
ROC_5MIN_BUY_THRESHOLD=8.5      # Was 7.5
ROC_5MIN_SELL_THRESHOLD=5.0     # Unchanged

# RSI Filter (industry standard + tighter neutral zone)
RSI_WINDOW=14                   # Was 7
# RSI neutral zone: 45-55 (changed in signal_manager.py)

# Exit Levels (wider to capture momentum)
TAKE_PROFIT=0.040               # Was 0.025 (4.0% vs 2.5%)
STOP_LOSS=-0.020                # Was -0.015 (2.0% vs 1.5%)

# Peak Tracking (lower activation threshold)
PEAK_TRACKING_ENABLED=true
PEAK_TRACKING_DRAWDOWN_PCT=0.05
PEAK_TRACKING_MIN_PROFIT_PCT=0.045    # Was 0.06 (4.5% vs 6.0%)
PEAK_TRACKING_BREAKEVEN_PCT=0.045     # Was 0.06 (4.5% vs 6.0%)
PEAK_TRACKING_SMOOTHING_MINS=5
PEAK_TRACKING_MAX_HOLD_MINS=1440
PEAK_TRACKING_TRIGGERS=ROC_MOMO,ROC_MOMO_OVERRIDE,ROC
```

**Code Changes**:
- `sighook/signal_manager.py`: RSI neutral zone 40-60 → 45-55 (already done in Test 2 deployment)

---

## Section 3: Deployment Checklist

### Pre-Deployment
- [x] Document current state (this file)
- [ ] Create emergency fix branch
- [ ] Disable position_monitor exits
- [ ] Disable Multi-ROC strategies
- [ ] Verify Test 2 config in .env
- [ ] Test locally (if time permits)
- [ ] Commit changes with detailed notes

### Deployment
- [ ] Push to GitHub
- [ ] SSH to AWS
- [ ] Pull latest changes
- [ ] Verify .env settings match Test 2
- [ ] Restart containers
- [ ] Verify deployment

### Post-Deployment Monitoring (24-48 hours)
- [ ] Monitor trade frequency (expect ~0.5 trades/day, not 15.5/day)
- [ ] Check exit triggers (should be 100% rearm_oco, 0% position_monitor)
- [ ] Verify ROC_MOMO_20M and ROC_MOMO_24H are not triggering
- [ ] Check win rate (target: 50%+ improvement from 26.7%)
- [ ] Monitor for any errors or unexpected behavior

### Analysis Checkpoints
- [ ] **24-hour check**: Quick query to verify no position_monitor exits
- [ ] **48-hour check**: Run performance analysis, compare to baseline
- [ ] **7-day check**: Full evaluation against Test 2 backtest expectations

---

## Section 4: Bug Investigation Notes

### 4.1 Position Monitor Bug (To Be Investigated)
**Suspected Root Cause**:
```python
# Line ~446-470 in position_monitor.py
# Calculates avg_entry_price from API unrealized_pnl:
avg_entry_price = current_price - (unrealized_pnl / total_balance_crypto)

# Known issue (fixed Jan 17): API returns garbage unrealized_pnl data
# Validation added: reject if entry > 2× current or < 0.5× current
# BUT: Position monitor still has 0% win rate after this fix!
```

**Investigation Plan**:
1. Review avg_entry_price calculation logic
2. Add extensive logging for next 24 hours:
   - Log every avg_entry_price calculation
   - Log P&L at time of exit decision
   - Log why position_monitor triggers vs rearm_oco
3. Check if validation threshold (2×/0.5×) is too wide
4. Look for other data corruption sources (bid/ask staleness, etc.)

**Expected Fix**: TBD after investigation

---

## Section 5: Expected Outcomes After Fixes

### Immediate (24 hours)
- Trade frequency drops: 15.5/day → ~0.5/day (97% reduction)
- Position monitor exits: 14% → 0% (disabled)
- Rearm_oco exits: 86% → 100% (handles all exits)

### Short-term (7 days)
- Win rate improvement: 26.7% → 50-60% (target from backtest: 57.9%)
- Avg win stabilization: $0.15 → $0.50-$1.00 (backtest: $1.08)
- Avg loss stabilization: -$0.28 → -$1.00-$1.50 (backtest: -$1.72)
- Total P&L: -$43/11 days → -$2 to -$5/7 days (backtest: -$1.94/60 days)

### Medium-term (14-30 days)
- Position monitor bug identified and fixed
- Re-enable position_monitor with extensive logging
- Evaluate Test 2 strategy performance
- Decision point: Keep Test 2 OR re-enable Multi-ROC selectively

---

## Section 6: Re-enabling Plan

### Position Monitor (Estimated: 2-3 days)
**Prerequisites**:
1. ✅ Bug identified and root cause understood
2. ✅ Fix implemented and tested locally
3. ✅ Extensive logging added
4. ✅ Test 2 baseline established (7 days of data)

**Re-enable Process**:
1. Deploy fix to production
2. Monitor for 24 hours with detailed logging
3. Verify win rate > 0% (target: 30-40%)
4. Compare position_monitor vs rearm_oco performance
5. If successful, keep enabled; if still broken, disable again

### Multi-ROC Strategies (Estimated: 14-30 days)
**Prerequisites**:
1. ✅ Test 2 evaluation complete (7-14 days)
2. ✅ Test 2 performance validated (close to backtest expectations)
3. ✅ Position monitor bug fixed and working
4. ✅ Decision made on strategy going forward

**Re-enable Options**:
- **Option A**: Keep Test 2 only (if performing well)
- **Option B**: Re-enable ROC_MOMO_24H only (34% win rate was best)
- **Option C**: Re-enable both Multi-ROC strategies with reduced frequency
- **Option D**: Run backtest on Multi-ROC, then decide

---

## Section 7: Files Modified

### Emergency Fix Files
- `MarketDataManager/position_monitor.py` - Disabled check_positions()
- `sighook/signal_manager.py` - Disabled ROC_MOMO_20M and ROC_MOMO_24H detection
- `.env` - Verified Test 2 parameters (no changes needed if already deployed)
- `.claude/EMERGENCY_FIXES_2026-01-25.md` - This tracking document

### Documentation Updates
- `.claude/sessions/2026-01-20-0218-performance-evaluation.md` - Updated with findings
- `docs/active/deployment/test-2-optimization.md` - Reference for Test 2 config

---

## Section 8: Contact and Escalation

**If Issues Arise**:
1. Check logs: `ssh bottrader-aws "docker compose -f docker-compose.aws.yml logs sighook webhook --tail 500"`
2. Verify containers: `ssh bottrader-aws "docker compose -f docker-compose.aws.yml ps"`
3. Emergency rollback: See Section 9

---

## Section 9: Emergency Rollback Plan

**If deployment causes critical issues**:

```bash
# SSH to AWS
ssh bottrader-aws

# Stop containers
cd /opt/bot
docker compose -f docker-compose.aws.yml down

# Rollback to previous commit
git log --oneline -5  # Find previous commit
git reset --hard <previous-commit-hash>

# Restart containers
docker compose -f docker-compose.aws.yml up -d

# Verify
docker compose -f docker-compose.aws.yml ps
```

**Rollback Triggers**:
- System crashes or repeated errors
- No trades for 48 hours (if market is active)
- Win rate drops below 20% after 24 hours
- Any critical bug preventing trading

---

## Section 10: Success Metrics

### 24-Hour Success Criteria
- ✅ No position_monitor exits
- ✅ No ROC_MOMO_20M or ROC_MOMO_24H entries
- ✅ Trade frequency < 3 trades
- ✅ No critical errors in logs
- ✅ Containers remain healthy

### 7-Day Success Criteria
- ✅ Win rate ≥ 45% (vs 26.7% baseline)
- ✅ Total P&L > -$10 (vs -$43/11 days = -$27/7 days projected)
- ✅ Trade frequency: 2-5 trades (vs 100+ without fix)
- ✅ Avg win > $0.30 (vs $0.15 baseline)
- ✅ No position_monitor exits

**If criteria met**: Continue with Test 2 strategy for additional 7 days, then full evaluation

**If criteria NOT met**: Investigate further, consider additional changes or rollback

---

**Document Status**: 🚨 IN PROGRESS
**Last Updated**: January 25, 2026 - Initial creation
**Next Update**: After deployment completion
