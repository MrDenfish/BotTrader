# Performance Optimization Plan: 12-Point Action Plan
**Created**: January 1, 2026
**Status**: 📋 Planning Phase
**Context**: 30-day analysis revealed -$366.56 net loss with critical issues

---

## Executive Summary

Based on 30-day performance analysis (Dec 2 - Jan 1, 2026), the bot has:
- **Net P&L**: -$366.56 (469 trades)
- **Win Rate**: 24.95% (need 35%+ minimum)
- **Risk/Reward**: 1:2 inverted (losing $2 for every $1 won)
- **Mode**: 100% passive trades, 0% sighook momentum trades
- **Critical Event**: RECALL-USD disaster (-$258.24 on Dec 4)

This plan addresses 12 optimization points across 5 work sessions.

---

## Session Breakdown

### Session 1: Critical Bug Fixes (URGENT)
**Priority**: 🔴 CRITICAL
**Estimated Time**: 2-3 hours
**File**: `2026-01-01-session1-critical-bugs.md`

#### Point 1: Investigate RECALL-USD Dec 4 Disaster 🚨
- Query RECALL-USD trades on Dec 4
- Analyze why stop-loss allowed -$73.35 single loss
- Check if position sizing was incorrect
- Verify stop-loss execution timing
- **Success Criteria**: Understand why loss exceeded -6% hard stop

#### Point 5: Fix Metadata Caching Mystery 🔍
- Investigate why Dec 30 webhooks didn't trigger `STRATEGY_CACHE_DEBUG` logs
- Review `webhook/listener.py:1009-1011` execution path
- Check if condition preventing `_cache_strategy_metadata()` call
- Add additional debug logging if needed
- Test with manual webhook
- **Success Criteria**: Metadata caching executes and logs appear

**Deliverables**:
- Root cause analysis for RECALL-USD disaster
- Fix for metadata caching bug
- Updated debug logging if needed
- Git commit with fixes

---

### Session 2: Risk Management Tightening
**Priority**: 🟠 HIGH
**Estimated Time**: 1-2 hours
**File**: `2026-01-01-session2-risk-management.md`

#### Point 2: Reduce Position Sizes in Slow Markets 💰
- Current: `BUY_AMOUNT_FIAT=20.0`
- Proposed: `BUY_AMOUNT_FIAT=15.0` (25% reduction)
- Rationale: Reduce exposure with 25% win rate
- **Success Criteria**: Update `.env` local and AWS

#### Point 3: Tighten Stop-Losses 🛡️
- Current: `MAX_LOSS_PCT=-4.5%`, `HARD_STOP_PCT=-6.0%`
- Proposed: `MAX_LOSS_PCT=-3.0%`, `HARD_STOP_PCT=-4.5%`
- After fees: ~-3.75% and ~-5.25% net
- Target: Reduce avg loss from -$1.39 to ~-$0.95
- **Success Criteria**: Update `.env` local and AWS

#### Point 4: Block Unprofitable Symbols 🚫
- Add `BLOCKED_SYMBOLS` to `.env`
- Block list: XRP-USD, SAPIEN-USD, FARTCOIN-USD, MON-USD, WET-USD, SOL-USD, PUMP-USD, ICP-USD, RLS-USD
- Implement symbol blocking logic in code (if not exists)
- **Success Criteria**: Blocked symbols no longer traded

**Deliverables**:
- Updated `.env` with tightened risk parameters
- Implemented symbol blocking
- AWS deployment with new settings
- Git commit with risk management updates

---

### Session 3: Strategy Parameter Optimization
**Priority**: 🟡 MEDIUM
**Estimated Time**: 2 hours
**File**: `2026-01-01-session3-strategy-optimization.md`

#### Point 6: Reduce Take-Profit Threshold 📈
- Current: `MIN_PROFIT_PCT=3.5%`
- Proposed: `MIN_PROFIT_PCT=2.0%`
- Net after fees: +1.25%
- Rationale: More frequent exits in slow market
- **Success Criteria**: Update `.env` and test

#### Point 7: Adjust Trailing Stop 📉
- Current: `TRAILING_ACTIVATION_PCT=3.5%`
- Proposed: `TRAILING_ACTIVATION_PCT=2.0%`
- Keep: `TRAILING_DISTANCE_PCT=1.5%`
- Rationale: Capture smaller moves in ranging market
- **Success Criteria**: Update `.env` and test

#### Point 9: Enable Sighook Momentum Trading 🎯
- Current: All trades are passive (0% sighook)
- Check: Why no sighook BUY signals despite `ALLOW_BUYS_ON_RED_DAY=true`
- Test: Lower `ROC_MOMENTUM_THRESHOLD` from 2.5 to 2.0
- Rationale: Enable momentum strategy in slow market
- **Success Criteria**: Sighook trades start appearing

**Deliverables**:
- Updated profit/trailing stop thresholds
- Lowered momentum threshold for testing
- Documentation of expected behavior changes
- Git commit with strategy optimizations

---

### Session 4: Advanced Architecture Improvements
**Priority**: 🟢 MEDIUM-LOW
**Estimated Time**: 3-4 hours
**File**: `2026-01-01-session4-architecture.md`

#### Point 8: Implement Volatility-Based Position Sizing 📊
- Create volatility calculation module
- Implement adaptive sizing:
  - Low volatility (<2%): $10
  - Medium volatility (2-5%): $15
  - High volatility (>5%): $20
- Add to position monitor or trade order manager
- **Success Criteria**: Dynamic position sizing operational

#### Point 10: Dual-Mode Strategy (Active + Passive)
- Design mode detection logic
- Slow market mode: Reduce passive market-making, focus on quality signals
- Fast market mode: Increase position sizes, widen stops
- Implement mode switching
- **Success Criteria**: Bot adapts behavior to market conditions

#### Point 11: Fee Optimization 💸
- Current tier: Advanced 1 (0.25% maker / 0.50% taker = 0.75% round-trip)
- Calculate volume needed for tier upgrade
- Analyze if maker-only orders feasible
- Consider limit-only strategy vs market orders
- **Success Criteria**: Strategy to reduce effective fees to <0.50%

**Deliverables**:
- Volatility-based position sizing module
- Dual-mode market detection
- Fee optimization strategy
- Git commit with architecture improvements

---

### Session 5: Win Rate Improvement & Monitoring
**Priority**: 🟢 LOW (Long-term)
**Estimated Time**: 2-3 hours
**File**: `2026-01-01-session5-win-rate-improvement.md`

#### Point 12: Win Rate Improvement Focus 🎯
- Current: 25% win rate with 1:2 risk/reward (unsustainable)
- Target: 35%+ win rate with 2:1 risk/reward
- Strategies:
  - Better entry timing (momentum confirmation)
  - Better exit timing (don't exit too early)
  - Symbol selection (trade only high-momentum pairs)
  - Time-of-day filtering (avoid low-liquidity periods)
- **Success Criteria**: 7-day win rate >30%

#### Monitoring & Validation
- Create performance monitoring dashboard
- Track key metrics:
  - Daily win rate
  - Daily P&L
  - Sighook vs passive trade ratio
  - Symbol-level performance
  - Stop-loss execution rate
- Compare baseline (last 7 days) vs optimized (next 7 days)
- **Success Criteria**: Metrics show improvement

**Deliverables**:
- Win rate improvement strategies implemented
- Performance monitoring queries/dashboard
- 7-day comparison report
- Final optimization recommendations

---

## Success Metrics (7-Day Test Period)

After implementing all changes, measure these metrics for 7 days:

| Metric | Baseline (Last 7d) | Target (Next 7d) | Status |
|--------|-------------------|------------------|--------|
| **Win Rate** | 24.95% | >30% | ⏳ Pending |
| **Net P&L** | -$12.14 | >$0 (break-even+) | ⏳ Pending |
| **Avg Win** | $0.69 | >$0.75 | ⏳ Pending |
| **Avg Loss** | -$1.39 | <-$1.00 | ⏳ Pending |
| **Risk/Reward** | 1:2.01 (inverted) | 1.5:1 or better | ⏳ Pending |
| **Sighook %** | 0% | >20% | ⏳ Pending |
| **Trades/Day** | ~5 | 5-15 | ⏳ Pending |

**Break-even Win Rate Calculation**:
- With 2:1 risk/reward (win $1.38, lose $0.95): Need 41% win rate
- With 1.5:1 risk/reward (win $1.04, lose $0.95): Need 48% win rate
- Current 1:2 inverted: Need 67% win rate (impossible to achieve)

---

## Session Execution Order

1. ✅ **Session 1**: Critical bugs (RECALL disaster + metadata caching) - START HERE
2. ✅ **Session 2**: Risk management (position sizing, stops, symbol blocking)
3. ✅ **Session 3**: Strategy optimization (profit targets, trailing stops, momentum)
4. ⏳ **Session 4**: Architecture (volatility sizing, dual-mode, fees) - Optional
5. ⏳ **Session 5**: Win rate improvement & monitoring - After 7-day test

**Minimum Viable Optimization**: Complete Sessions 1-3 (estimated 5-7 hours total)
**Full Optimization**: Complete all 5 sessions (estimated 10-15 hours total)

---

## Risk Assessment

### High-Risk Changes (Test Carefully)
- Lowering stop-losses too much (could increase loss frequency)
- Lowering momentum threshold too much (could increase false signals)
- Blocking too many symbols (could reduce opportunities)

### Low-Risk Changes (Safe to Deploy)
- Reducing position size (always reduces risk)
- Adding more debug logging (no trading impact)
- Blocking demonstrably unprofitable symbols

### Changes Requiring Validation
- New profit/trailing thresholds (need 3-7 days to assess)
- Volatility-based sizing (need market cycle to test)
- Dual-mode strategy (need volatile + slow periods)

---

## Rollback Plan

If changes make performance worse:

1. **Immediate rollback** (critical issues):
   ```bash
   cd /opt/bot
   git checkout HEAD~1  # Revert to previous commit
   docker compose -f docker-compose.aws.yml restart webhook sighook
   ```

2. **Selective rollback** (specific parameters):
   - Keep: Bug fixes, debug logging, symbol blocking
   - Revert: Position sizing, stop-losses, profit targets
   - Update `.env` with original values

3. **Monitor for 24 hours** after any change:
   - Check win rate didn't drop below 20%
   - Check no single loss >$10
   - Check sighook trades executing properly

---

## Related Files

- Analysis: `.claude/sessions/2026-01-01-30day-performance-analysis.md` (this session)
- Session 1: `.claude/sessions/2026-01-01-session1-critical-bugs.md` (to be created)
- Session 2: `.claude/sessions/2026-01-01-session2-risk-management.md` (to be created)
- Session 3: `.claude/sessions/2026-01-01-session3-strategy-optimization.md` (to be created)
- Session 4: `.claude/sessions/2026-01-01-session4-architecture.md` (to be created)
- Session 5: `.claude/sessions/2026-01-01-session5-win-rate-improvement.md` (to be created)

---

## Next Steps

1. **User Decision**: Review this plan and approve execution order
2. **Start Session 1**: Create session file and begin critical bug investigation
3. **Sequential Execution**: Complete each session before moving to next
4. **Testing**: Deploy changes to AWS and monitor for 24 hours between sessions
5. **Final Review**: After 7 days, compare metrics and create summary report

---

**Plan Status**: 📋 Awaiting User Approval
**Last Updated**: 2026-01-01 19:52 UTC
