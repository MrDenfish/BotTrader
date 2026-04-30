# Session: Backtest Alignment & Validation — 2026-04-09

## Overview
- **Started:** 2026-04-09 (second session of the day)
- **Priority:** P1 from `memory/open_work_items.md`

## Git Summary

**2 commits made** (b2e9e24 → 321daf8)
**2 files changed**

### Commits
1. `db46af3` — fix: Align backtest config to production — 30 parameter differences resolved
2. `321daf8` — docs: Mark backtest config drift resolved, add fee drag as active issue

## Key Accomplishments

### 1. Aligned backtest_composite.yaml to production
- 30 strategy/risk/sizing parameters aligned to kraken_paper_trading.yaml
- 6 infra params intentionally different (retries, post_only, stale tracking, etc.)
- Verified via automated YAML comparison — zero unintended differences

### 2. Out-of-sample overfitting validation (3 independent sets)
- **Training Set A** (BTC, ETH, SOL, XRP, DOGE, ADA, LINK, DOT, AVAX): 103 trades, 54.4% WR, -$62 net, PF 0.57
- **OOS Set B** (AAVE, APT, ATOM, BNB, FIL, ICP, LTC, NEAR, UNI): 113 trades, 64.6% WR, -$22 net, PF 0.85
- **OOS Set C** (ALGO, ARB, FET, HBAR, INJ, LDO, OP, SUI, TAO): 99 trades, 61.6% WR, -$33 net, PF 0.77

### 3. Overfitting verdict: NOT OVERFIT
- Out-of-sample sets outperformed training set on all metrics
- Exit distribution consistent across all 3 sets (trailing ~60%, hard ~25%, stale ~15%)
- The 5 data-derived changes (trend gate, ATR hard stops, peak tracking, volume features, regime filter) are validated as robust

### 4. Key finding: Fee drag is the #1 P&L lever
- All 3 sets are gross profitable or near-breakeven
- Fees (~$50/set) wipe out gains every time
- At $75 notional with 0.65% round-trip fees, avg win ($1.47-$1.80) barely covers costs
- This is now the top priority for next session

## Memory Files Updated
- Updated: `memory/open_work_items.md` — P1 marked done, fee/sizing added as new P1

## What Wasn't Completed
- Fee/sizing analysis deferred to next session
