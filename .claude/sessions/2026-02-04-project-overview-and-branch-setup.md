# Project Overview and Collaborative Branch Setup

**Date**: 2026-02-04
**Branch**: `backtest/4h-hybrid-development` (newly created)
**Status**: ✅ Complete
**Previous Session**: 2026-02-03 (Option 1 Observability + Stage C)

---

## Session Objectives

1. ✅ Create comprehensive BotTrader project overview
2. ✅ Document production vs backtest strategy differences
3. ✅ Verify fee structure in backtesting
4. ✅ Check repository visibility (public/private)
5. ✅ Create new branch for collaborative development with ChatGPT and Claude Code

---

## Key Deliverables

### 1. BotTrader Project Overview (docs/BOTTRADER_OVERVIEW.md)

Created comprehensive documentation covering:
- Production architecture (webhook, sighook, database)
- Current trading strategies (production vs backtest)
- 4h Hybrid Maker strategy design and development timeline
- Fee structure analysis
- Historical strategies (archived)
- Key architectural patterns (FIFO, cross-container communication, WebSocket)
- Recent development focus (Phase 5 signal exits, Stage C runner policy)

**Key Metrics** (180d baseline):
- Total 4h bars: 1,078
- Regime OK: 302 (28.0%)
- Setups created: 12
- Entries filled: 11 (91.7% conversion)
- State occupancy: 85.7% FLAT, 14.3% IN_POSITION

---

### 2. Fee Structure Verification

**Backtest Configuration** (config_4h_hybrid.py):
```python
maker_fee: 0.004  # 0.4% (Coinbase actual rate)
taker_fee: 0.008  # 0.8% (Coinbase actual rate)
round_trip_maker_fee: 0.008  # 2 × maker (0.8%)
```

**Fee Application**:
- All entries: Maker (0.4%)
- TP1/TP2 exits: Maker (0.4%)
- Runner/stop exits: Taker (0.8%)

**Viability Filter**: ATR% ≥ 1.0 × 0.008 = 0.8% minimum volatility

**Truth Line Output**: Every backtest prints fee configuration at start for verification

---

### 3. Production vs Backtest Strategy Comparison

**CRITICAL FINDING**: ⚠️ **Complete Mismatch**

| Aspect | Production (sighook) | Backtest (4h Hybrid) |
|--------|---------------------|---------------------|
| **Timeframe** | Real-time + 5-min OHLCV | 4h bar closes only |
| **Frequency** | Every 30 seconds | 6× per day |
| **Signal Type** | Composite scoring (7 indicators) | ROC-score momentum |
| **Regime Filter** | ❌ None | ✅ 1D EMA200 |
| **Viability Filter** | ❌ None | ✅ ATR% ≥ 0.8% |
| **Entry Style** | Immediate on signal | Setup → Retest → Chase |

**Production Indicators**:
- Buy Ratio (Bollinger Band position)
- Buy Touch (BB lower band touch)
- W-Bottom (reversal pattern)
- Buy RSI (< 20 oversold)
- Buy ROC (5-min momentum > 10%)
- Buy MACD (histogram cross)
- Buy Swing (swing low pattern)

**Backtest Indicators**:
- ROC-score (6-period ROC / ATR%)
- 1D EMA200 (regime)
- 4h ATR% (viability)
- BB width percentile (compression)

**Recommendation**: Production and backtest are **independent strategies**. Backtest results do NOT predict production performance. Consider either:
- **Option A**: Deploy 4h Hybrid to production
- **Option B**: Backtest current production strategy on 5-min data

---

### 4. Repository Visibility Check

**Status**: ⚠️ **PUBLIC**

```json
{
  "owner": "MrDenfish",
  "visibility": "PUBLIC",
  "isPrivate": false
}
```

**Security Implications**:
- Trading strategies and algorithms publicly visible
- Infrastructure details (AWS, database) exposed
- `.env` files protected by `.gitignore` but referenced in code

**Recommendation**: Consider making repository private to protect:
- Proprietary trading logic
- Architecture and deployment details
- Any accidentally committed sensitive data

**Command to Make Private**:
```bash
gh repo edit MrDenfish/BotTrader --visibility private
```

---

### 5. Collaborative Branch Setup

**Created**: `backtest/4h-hybrid-development`

**Commit**: `ce85964` - 106 files, 1.5M+ insertions

**Branch URL**: https://github.com/MrDenfish/BotTrader/tree/backtest/4h-hybrid-development

**Contents**:
- Complete 4h Hybrid Maker backtesting framework
- Test runners (180d, 360d) with diagnostics
- Historical data (BTC, ETH, SOL, ADA, XRP, DOGE, DOT, LINK, AVAX)
- Session documentation (7 sessions from Jan 27 - Feb 3)
- Archived legacy strategies
- BOTTRADER_OVERVIEW.md
- Implementation status docs and CLI commands

**Accessibility**:
- ✅ Public GitHub repository (both ChatGPT and Claude Code can access)
- ✅ Pushed to origin with tracking
- ✅ Ready for collaborative development

**Quick Start**:
```bash
# Clone the branch
git clone -b backtest/4h-hybrid-development git@github.com:MrDenfish/BotTrader.git

# Run a backtest
cd backtest
python3 run_single_180d.py
```

---

## Bug Fixes This Session

### ROC Percentile NaN Fix (strategy_4h_hybrid.py:2525-2588)

**Problem**: 360d backtest showed all NaN percentiles despite having 217 ROC-OK bars

**Root Cause**: Early bars had NaN `roc_score_4h` values from warmup period. `np.percentile()` returns NaN when array contains NaN values.

**Fix**: Filter NaN values before percentile computation
```python
import math
roc_arr_raw = np.array(self.roc_score_viable)
roc_arr = np.array([x for x in roc_arr_raw if not (isinstance(x, float) and math.isnan(x))])
```

**Verification**:
- 360d run: Filtered 5 NaN values, computed valid percentiles (Min=-3.093, P50=0.083, Max=4.223)
- 180d run: No NaN filtering needed (all values valid)

**Created Test**: `backtest/test_roc_percentile_nan_fix.py`

---

## Open Sessions (Not Closed)

### Recent (Backtest-Related)
1. ❌ **2026-01-30-4h-hybrid-maker-strategy.md** - Initial strategy session (incomplete)
2. ❌ **2026-01-28-roc-peak-drawdown-refactor.md** - Legacy strategy refactor (incomplete)
3. ❌ **2026-01-27-2216-multi-roc-backtest-alignment.md** - Multi-ROC alignment (incomplete)

### Older (Production-Related)
4. ❌ **2026-01-26-0135-post-hybrid-fix-monitoring.md** - Post-deployment monitoring (incomplete)
5. ❌ **2026-01-20-0218-performance-evaluation.md** - Performance review (incomplete)
6. ❌ **2026-01-12-1000-backtesting.md** - Early backtest exploration (incomplete)
7. ❌ **2026-01-05-0800-convert-crypto-to-crypto.md** - Crypto conversion feature (incomplete)

### Archived/Obsolete (2025)
8. ❌ **2025-12-30-1217-http-server-startup-bug.md**
9. ❌ **2025-12-29-1145-trade-strategy-linkage-integration.md**
10. ❌ **2025-12-27-eth-accumulation-fix.md**
11. ❌ **2025-12-24-0830-review-roc-strategy.md**
12. ❌ **2025-12-22-0800-follow-up-bottrader-performance-analysis.md**
13. ❌ **2025-12-14-1930-fee-aware-pnl.md**
14. ❌ **2025-11-20-1845-PnL-Bug-Investigation-and-Redesign-Decision.md**
15. ❌ **2025-11-20-1155-FIFO-Allocations-Architecture-Redesign.md**

**Note**: Many older sessions may be effectively complete but lack formal closure markers. Recent backtest sessions (Jan 27-30) should be reviewed and closed if work is done.

---

## Current State Summary

### Backtest Development
- **Branch**: `backtest/4h-hybrid-development` (active)
- **Phase**: 2.4 (Stage C compression-based runner policy)
- **Recent Work**: Option 1 observability, ROC percentile fix, Stage C warmup logging
- **Next Steps**: A/B test Stage C, finalize ROC threshold calibration

### Production Deployment
- **Branch**: `feature/strategy-optimization` (deployed to AWS)
- **Strategy**: Composite scoring (RSI, MACD, Bollinger, patterns)
- **Status**: Stable, signal-based exits active
- **Misalignment**: ⚠️ Production strategy completely different from backtest

### Repository Management
- **Visibility**: PUBLIC (consider making private)
- **Collaboration**: Branch ready for ChatGPT and Claude Code
- **Documentation**: BOTTRADER_OVERVIEW.md created

---

## Recommendations

### Immediate
1. **Review repository visibility**: Decide if strategies should remain public
2. **Close old sessions**: Review and formally close 2025 sessions if work complete
3. **Align strategies**: Decide whether to:
   - Deploy 4h Hybrid to production, OR
   - Backtest current production strategy

### Near-Term
1. **Stage C A/B Testing**: Compare compressed vs normal entry performance
2. **ROC Threshold Tuning**: Calibrate for 60-120 setups/year
3. **Production Strategy Backtest**: Validate current composite scoring on historical data

### Long-Term
1. **Strategy Alignment**: Converge production and backtest approaches
2. **Multi-Symbol Expansion**: Test 4h Hybrid on ETH, SOL, ADA
3. **Fee Tier Optimization**: Analyze if volume qualifies for better Coinbase tiers

---

## Files Modified This Session

| File | Purpose |
|------|---------|
| `docs/BOTTRADER_OVERVIEW.md` | Comprehensive project documentation |
| `backtest/strategy_4h_hybrid.py` | ROC percentile NaN fix (lines 2525-2588) |
| `backtest/test_roc_percentile_nan_fix.py` | Test for NaN filtering |
| `.claude/sessions/2026-02-04-project-overview-and-branch-setup.md` | This session summary |

---

## Branch Commit Details

**Commit**: `ce85964`
**Message**: "feat: Add 4h Hybrid Maker Strategy backtesting framework"
**Files**: 106 changed, 1,520,532 insertions(+)

**Major Additions**:
- 4h Hybrid strategy (strategy, engine, config)
- Test runners (180d, 360d, optimizer)
- Historical data (10 symbols)
- Session documentation (7 sessions)
- Archived legacy strategies
- Implementation status docs

---

## References

- **Project Overview**: `/docs/BOTTRADER_OVERVIEW.md`
- **Latest Backtest Session**: `.claude/sessions/2026-02-03-option1-observability-stage-c.md`
- **4h Hybrid Branch**: https://github.com/MrDenfish/BotTrader/tree/backtest/4h-hybrid-development
- **Repository Settings**: https://github.com/MrDenfish/BotTrader/settings

---

**Session Status**: ✅ **COMPLETE**

All objectives achieved:
- ✅ Project overview documented
- ✅ Fee structure verified
- ✅ Strategy misalignment identified
- ✅ Repository visibility checked
- ✅ Collaborative branch created and pushed
- ✅ ROC percentile NaN bug fixed

Ready for collaborative development with ChatGPT and Claude Code on the new branch.
