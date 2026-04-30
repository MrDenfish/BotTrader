# Session: Hard Stop Reduction Backtests — 2026-04-11

## Overview
- **Started:** 2026-04-11 05:30 UTC
- **Ended:** 2026-04-12
- **Branch:** fee-sizing-analysis (analysis work), main (docs)

## Goals
1. Run backtest with `regime_max_atr_percentile` lowered from 60 to 40 (Run 1) ✅
2. Run backtest with ADX minimum threshold sweep at 20, 25, 30 (Run 2) ✅
3. Hard stop price path analysis ✅
4. Update SYSTEM_CONTEXT.md and memory for July 2026 pickup ✅

## Git Summary

**4 commits across 2 branches**

### fee-sizing-analysis branch
1. `3916ff4` — analysis: Run 1 — regime_max_atr_percentile 60→40
2. `6d3bfc0` — analysis: Run 2 — ADX minimum threshold sweep (25, 30)
3. `c227fe0` — analysis: Hard stop price path check — trailing stop simulation

### main branch
4. `efb6803` — docs: Update SYSTEM_CONTEXT with fee/sizing & hard stop analysis findings

## Key Findings

### Run 1: Regime Filter (ATR pctile 60→40)
- Blocks 129/320 trades (40%), PF 0.73→0.90, net P&L -$111→-$21
- Hard stop % unchanged: 22.5%→21.5% — filter doesn't discriminate
- Set B crosses into profit (PF 1.34), but Set A worsens (PF 0.62) — inconsistent

### Run 2: ADX Threshold Sweep
- ADX>=25: 167 trades, PF 0.82, HS 21.6%
- ADX>=30: 76 trades, PF 0.86, HS 21.1%
- Same pattern: HS% locked at ~21% regardless of filter. Blocks winners and losers equally.

### Price Path Analysis
- 83% of hard stops exit within 0.2% of MAE (genuine bottoms)
- Post-exit recovery: avg 1.03%, only 39% recover even 1%
- Trailing hard stop idea: +$0.15/trade EV (marginal, $10.56 total)
- Zero trades fell more than 1% further after stop level hit

### Strategic Conclusion
Entry-time indicators (regime, ADX) cannot predict which trades will hit hard stops. The adverse move happens AFTER entry and is indistinguishable from normal volatility that winners also experience. The hard stop is well-calibrated — it catches genuine bottoms, not cutting winners short. The ~22% hard stop rate appears to be intrinsic to the price dynamics.

## Decision
Collect 60-90 days of live paper trading data. No parameter changes. Re-analyze with real market data in **July 2026**.

## Files Updated
- `docs/SYSTEM_CONTEXT.md` — Sections 14, 16, 17, Active Issues, Changelog
- `memory/MEMORY.md` — Updated priorities, added analysis pointer
- `memory/open_work_items.md` — Closed fee/sizing and hard stop items, set July target
- `memory/fee_sizing_analysis.md` — NEW: detailed findings for future reference
- `analysis/fee_sizing/hard_stop_analysis.py` — NEW: backtest run comparison
- `analysis/fee_sizing/hard_stop_path_check.py` — NEW: price path analysis
- 9 backtest YAML configs (Run 1 + Run 2)
