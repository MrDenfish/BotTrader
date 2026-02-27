# Strategy Optimization: Trend Confirmation Gate
**Date**: 2026-02-27
**Start Time**: 08:00 UTC

## Overview
Diagnostic backtest analysis and strategy optimization session. Analyzed hard stop losses, tested multiple mitigation approaches, and deployed a trend confirmation gate.

## Goals
- [x] Analyze hard stop losses from diagnostic backtest data
- [x] Test ATR percentile lookback fix (regime filter) — REVERTED, made things worse
- [x] Test tighter 4% hard stop — REJECTED, doubled hard stop count
- [x] Implement trend confirmation gate for composite score buys
- [x] Deploy trend gate and sync configs between backtest and live
- [x] Run random baseline comparison to validate indicator edge

## Progress

### Regime Filter ATR Lookback Fix (REVERTED)
- Added `regime_atr_lookback: int = 40` to config, limited ATR percentile to 40-bar window
- Result: hard stops increased 18→21, P&L worse. Root cause: volatility spikes happen AFTER entry
- Committed `27b2e7c`, deployed, then reverted with `c753d38`

### Tighter Hard Stop 4% (REJECTED)
- Changed `hard_stop_pct` from 0.055 to 0.04
- Result: hard stops doubled 18→36 (-$61.32 vs -$45.10). Many trades dip 4-5% before recovering
- Restored to 0.055

### Trend Confirmation Gate (DEPLOYED)
- Root cause analysis: 12/18 hard stops entered with Buy Touch + Buy RSI + Buy Volume Div (all counter-trend, no momentum confirmation). All in downtrend regimes.
- Fix: `require_trend_for_buy: true` — requires ≥1 trend indicator (MACD/ROC/Swing) for score buys
- Implementation: `scoring.py` gate after `min_indicators_required` check, `config.py` new field
- Result: trades 139→79, hard stops 18→9, gross P&L +$7.22→+$5.88, ~$15 fee savings
- Committed `76ed529`, deployed

### Config Sync
- Live had stale params: `hard_stop_pct: 0.045` (should be 0.055), `trailing_activation_pct: 0.03` (should be 0.02)
- Synced live to match backtest-validated values
- Committed `d95b8e0`, deployed

### Random Baseline Comparison
- Real strategy: 79 trades, 53.2% WR, +$5.88, 11.4% hard stop rate
- Random baseline: 28 trades, 60.7% WR, -$6.22, 28.6% hard stop rate
- Conclusion: indicators add real value on entry selection (half the hard stop rate, positive P&L vs negative)
- Synced random baseline config, committed `05740ea`

## Key Commits
- `27b2e7c` — ATR lookback fix (reverted)
- `c753d38` — Revert ATR lookback
- `76ed529` — Trend confirmation gate
- `d95b8e0` — Config sync (live to backtest params)
- `05740ea` — Random baseline config sync

## Artifacts
- `backtest/diagnostic_output/diagnostic_trades_no_trend_filter_baseline.jsonl` — pre-trend-gate baseline (139 trades)
- `backtest/diagnostic_output/diagnostic_trades.jsonl` — current results with trend gate (79 trades)
- `backtest/diagnostic_output_random/diagnostic_trades.jsonl` — random baseline (28 trades)

---

## Session End Summary
**End Time**: ~08:15 UTC
**Duration**: ~30 minutes (continued from prior context window)

### Git Summary
- **Commits**: 4 (plus 1 from prior context that was reverted)
- **Files changed**: 7 (net: 23 insertions, 136 deletions — deletions from reverting ATR lookback tests)

| File | Change |
|---|---|
| `v2/plugins/strategies/composite_scoring/scoring.py` | Modified — added trend confirmation gate (13 lines) |
| `v2/plugins/strategies/composite_scoring/config.py` | Modified — added `require_trend_for_buy` field |
| `v2/plugins/strategies/composite_scoring/indicators.py` | Modified (revert only — net no change) |
| `v2/tests/test_composite_scoring.py` | Modified (revert only — removed ATR lookback tests) |
| `v2/kraken_paper_trading.yaml` | Modified — `hard_stop_pct` 0.045→0.055, `trailing_activation_pct` 0.03→0.02 |
| `v2/backtest_diagnostic.yaml` | Modified — sync timestamp updated |
| `v2/backtest_random_baseline.yaml` | Modified — synced exit manager params to match live |

### Deployments
- 5 deploys to AWS (`ssh bottrader-aws`, `git pull`, `docker compose up -d --build v2-kraken`)
- All verified clean via `docker logs` — no errors on any restart

### Features Implemented
1. **Trend confirmation gate** (`require_trend_for_buy: true`): Requires ≥1 trend-confirming indicator (Buy MACD, Buy ROC, or Buy Swing) for composite score buys. Prevents "falling knife" entries where only counter-trend indicators (Touch + RSI + Volume Div) fire.

### Problems Encountered and Solutions
1. **ATR lookback fix made things worse**: Regime filter can't prevent hard stops because volatility spikes happen after entry, not before. Solution: reverted, shifted focus to entry quality instead of pre-entry filtering.
2. **Tighter hard stop doubled losses**: 4% stop catches trades that naturally dip 4-5% before recovering. Solution: kept 5.5%, pursued trend gate instead.
3. **Config drift between backtest and live**: Live had `hard_stop_pct: 0.045` and `trailing_activation_pct: 0.03` while backtest used 0.055 and 0.02. Solution: synced live to match backtest-validated values. Added YAML comparison script pattern for future syncs.

### Important Findings
- **12/18 hard stops** shared the same entry pattern: Buy Touch + Buy RSI + Buy Volume Div (all counter-trend, zero trend confirmation). All entered during `downtrend_high_vol` regime.
- **Trend gate halved hard stops** (18→9) while only losing 19 trailing winners (55→36). Net gross P&L impact minimal (-$1.35) but ~$15 fee savings from 60 fewer trades.
- **Random baseline confirms indicator edge**: Real strategy has positive P&L (+$5.88) vs random's negative (-$6.22), and half the hard stop rate (11.4% vs 28.6%).
- **5.5% hard stop is near-optimal**: Tighter stops convert future winners into realized losses. Many trades naturally dip 4-5% before recovering.
- `bars_held` in diagnostic JSONL is 1-minute bars — divide by 60 for hours, not multiply by 5.

### Configuration Changes
- `require_trend_for_buy: true` — new default in `CompositeScoreConfig`
- Live `hard_stop_pct`: 0.045 → 0.055
- Live `trailing_activation_pct`: 0.03 → 0.02
- Random baseline synced: `hard_stop_pct`, `trailing_activation_pct`, `soft_stop_enabled`, `days`

### What Wasn't Completed
- No further optimization (intentionally deferred — need out-of-sample data first)
- Remaining P&L drags not addressed: stale exits (18 trades, -$13.48), hard stops (9 trades, -$22.53)

### Next Steps
1. Let live paper trader accumulate 2-4 weeks of trades with new config
2. Download fresh out-of-sample price data for proper train/test split
3. Then optimize with validation discipline (not on the same 60-day window)

### Lessons Learned
- Always check if the problem is pre-entry (regime) or post-entry (exit management) before building a fix
- Tighter stops are not always better — understand the natural MFE/MAE distribution first
- Use diagnostic entry metadata (`score_components`, `regime`) to find patterns in losing trades rather than guessing
- Sync configs with a YAML comparison script, not manual inspection
