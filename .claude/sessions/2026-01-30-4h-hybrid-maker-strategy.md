# 4H Hybrid Maker Strategy Implementation

**Date:** January 30, 2026
**Branch:** `feature/4h-hybrid-maker-strategy`
**Status:** 🚀 Active

## Session Goal

Implement and backtest the 4h hybrid swing momentum strategy designed to survive high Coinbase fees through:
- **Post-only maker orders** (lower fees)
- **4h timeframe** (lower turnover)
- **Fee-multiple profit targets** (ensure room for profit)
- **BTC-USD focus** (proven 60% win rate on momentum)

## Context: Why This Strategy

Previous work showed 15m momentum strategies fail at Coinbase fee levels:
- 60-day test: +$17.65 gross, -$424.91 fees, -$407.25 net
- Root cause: High turnover + 0.6% round-trip fees destroy edge
- **BTC was exception**: 0.21% mean return, 60% win rate

## Strategy Spec

Location: `/docs/planning/strategy_hybrid_4h_fee_aware_post_only.md`

### Key Features
1. **Signals**: 4h Donchian breakout OR vol-adjusted ROC score
2. **Regime filter**: 1D EMA200 (only trade uptrends)
3. **Entry**: Pullback-reclaim OR breakout-retest (maker-friendly, avoid exhaustion)
4. **Execution**: Post-only limits throughout (entry + TPs)
5. **Targets**: Scale out at 2× fees (40%), 4× fees (40%), trail runner (20%)
6. **Stops**: ATR%-based catastrophic stop
7. **Viability filter**: Only trade when ATR% ≥ 2× round-trip fees

## Implementation Plan

### Phase 1: Minimal Viable Strategy (Start Here)
- [ ] Config dataclass with all parameters
- [ ] Simple Donchian breakout setup
- [ ] Breakout-retest entry (simpler than pullback-reclaim)
- [ ] 1D EMA200 regime filter only
- [ ] Fixed TP targets (2× and 4× fees)
- [ ] ATR% stop and trail
- [ ] Post-only fill simulation (conservative)
- [ ] Run 60-day BTC-only backtest

### Phase 2: Full Features
- [ ] Add pullback-reclaim entry mode
- [ ] Add ROC-score setup mode
- [ ] Add EMA50 slope filter (optional)
- [ ] Adaptive TP (max of fee-multiple and ATR-multiple)

### Phase 3: Optimization
- [ ] Grid search on key parameters
- [ ] Test different scale-out ratios
- [ ] Test 6h and 8h timeframes

## Success Criteria

Strategy is viable only if:
- ✅ Net P&L positive under 0.4%/0.8% fee schedule
- ✅ Maker fill rate >70% (entries + TPs)
- ✅ >50% of trades reach TP1 (2× fees)
- ✅ Trade frequency: 2-4 trades/month (low churn)
- ✅ Win rate: >50%

## Files to Create

```
backtest/
  strategy_4h_hybrid.py          # Core strategy state machine
  engine_4h_hybrid.py             # Backtest engine with post-only fills
  config_4h_hybrid.py             # Configuration dataclass
  test_4h_hybrid_60d.py           # 60-day BTC backtest
```

## Previous Learnings to Apply

From 15m strategy failures:
1. ❌ Don't buy signal bar close (exhaustion)
2. ❌ Don't use taker entries (fees too high)
3. ❌ Don't trade when volatility < fee hurdle
4. ✅ Focus on BTC (proven edge)
5. ✅ Use ATR-normalized thresholds (adaptive)
6. ✅ Scale out at profit (lock gains)

## Next Actions

1. Build minimal config and strategy skeleton
2. Implement Donchian + breakout-retest
3. Add post-only fill simulation
4. Run 60-day BTC backtest
5. Analyze and iterate

---

**End of session plan**
