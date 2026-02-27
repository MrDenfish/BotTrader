# Phantom Position Restore Fix - Starting Balance Bug
**Started:** 2026-02-27 18:19 UTC

## Overview
Investigated and fixed a critical bug where the email report's "Starting Balance" kept rising from $10,000 to $12,399.53 despite the paper trading program losing money.

## Goals
- [x] Investigate why starting balance keeps rising in 4-hour email reports
- [x] Identify root cause
- [x] Implement fix
- [x] Deploy to AWS
- [x] Verify fix is working
- [x] Clean up stale DB data
- [x] Update memory file

## Root Cause
When a position was fully sold, `app.py:_on_fill_portfolio()` deleted it from the in-memory dict **before** calling `save_position()`. The guard `if symbol in portfolio.positions` was already `False`, so `v2_positions` was never updated to `qty=0`. On container restart, stale positions were restored into the paper exchange via `restore_positions()`, allowing phantom sells of crypto that had already been sold.

**Impact:** 67 phantom sells inflated cash by $2,514. Starting balance rose from $10k to $12.4k despite actual P&L of -$57.

## Progress

### Investigation
- Traced portfolio tracker flow: `PortfolioTracker` in `daily_report_v2/collectors/portfolio_tracker.py` computes starting balance as `cash + MTM positions` at each period boundary
- Created diagnostic script `scripts/diagnose_portfolio.py` to replay fills from DB
- **Key finding:** 70 buys ($2,796) vs 130 sells ($5,039) — sells far exceed buys, impossible in closed system
- FIFO analysis confirmed 67 unmatched sells totaling $2,485 (sells with no matching buy lots)
- Per-symbol analysis showed patterns like ADA: BUY 195.5 → SELL 195.5 → SELL 195.5 (phantom)
- Traced to `app.py:347-363` — position deleted from memory before DB save

### Fixes Applied (commit `7bdbd40`)
1. **`v2/core/app.py`**: Save position with `qty=0` to DB before deleting from in-memory dict
2. **`v2/plugins/observability/daily_report_v2/collectors/portfolio_tracker.py`**: Cap sell qty at available position during `replay_fills()` to ignore phantom sells in historical data
3. **DB cleanup**: Reset 20 stale `v2_positions` entries via SQL to fill-computed values

### Verification
- 662 tests passing (no regressions)
- Fixed replay shows: cash=$9,743, positions=$210, total=$9,953, net change=-$47 (correct)
- Before fix: cash=$12,219, total=$12,457, net change=+$2,457 (wrong)
- Deployed to AWS with `--build`, container healthy

## Files Changed
- `v2/core/app.py` — Save zeroed position to DB before memory deletion
- `v2/plugins/observability/daily_report_v2/collectors/portfolio_tracker.py` — Phantom sell guard in replay
- `scripts/diagnose_portfolio.py` — New diagnostic script

## Commits
- `f283454` — Add portfolio diagnostic script
- `7bdbd40` — fix: Prevent phantom position restores inflating portfolio starting balance

---

## Session End Summary
**Ended:** 2026-02-27 18:21 UTC
**Duration:** ~2 minutes (session file created after work was complete)
**Actual investigation duration:** ~45 minutes (work done before session was formally started)

### Git Summary
- **Commits:** 2
- **Files changed:** 3 (1 added, 2 modified)
  - `scripts/diagnose_portfolio.py` — **Added** (151 lines) — Diagnostic tool to replay fills and detect portfolio discrepancies
  - `v2/core/app.py` — **Modified** (+7 lines) — Save zeroed position to DB before deleting from memory
  - `v2/plugins/observability/daily_report_v2/collectors/portfolio_tracker.py` — **Modified** (+10/-2 lines) — Cap sell qty at available position during replay to skip phantom sells
- **Final git status:** Clean (all session work committed and pushed)

### Task Summary
- **Completed:** 7/7
  - [x] Investigate why starting balance keeps rising
  - [x] Identify root cause
  - [x] Implement fix
  - [x] Deploy to AWS
  - [x] Verify fix is working
  - [x] Clean up stale DB data
  - [x] Update memory file

### Key Accomplishments
1. Identified critical bug in `app.py:_on_fill_portfolio()` where fully-sold positions were never persisted as qty=0
2. Traced the full chain: stale DB → phantom position restore → phantom sells → cash inflation → rising starting balance
3. Quantified impact: 67 phantom sells, $2,514 cash inflation, 20 stale DB positions
4. Implemented two-layer fix (code + replay guard) and cleaned production DB
5. Verified portfolio now shows correct -$47 net change (was showing +$2,457)

### Problems Encountered and Solutions
- **Problem:** Starting balance rising from $10k to $12.4k despite losing money
- **Investigation path:** Portfolio tracker → fill volumes (sells >> buys) → FIFO queue exhaustion → phantom sells → stale v2_positions → app.py save_position ordering bug
- **Solution:** Save qty=0 position to DB before in-memory deletion; guard replay against phantom sells

### Breaking Changes
- None. Fix is backward-compatible.

### Configuration Changes
- None. All changes are code-level.

### Deployment Steps Taken
1. `git push origin main`
2. `ssh bottrader-aws "cd /opt/bot && git pull origin main"`
3. DB cleanup: SQL UPDATE to fix 20 stale v2_positions entries
4. `docker compose -f docker-compose.aws.yml up -d --build v2-kraken`
5. Verified container healthy, diagnostic confirms correct portfolio values

### Lessons Learned
- **Always persist state changes to DB before removing from memory** — the ordering of delete-from-memory vs save-to-DB matters critically when the DB drives restoration on restart
- **Paper exchange position restore creates a "shadow inventory"** that can diverge from fill history — the portfolio tracker's replay_fills() must be resilient to this
- **Diagnostic scripts are invaluable** — `scripts/diagnose_portfolio.py` made the bug immediately visible by replaying fills and showing the cash/position discrepancy
- The `v2_positions` table is an optimization for startup speed but is a source of truth risk — fills in `v2_fills` are the true source of truth

### What Wasn't Completed
- Nothing — all goals achieved

### Tips for Future Developers
- Run `scripts/diagnose_portfolio.py` if portfolio values seem wrong — it replays all fills and shows discrepancies
- The portfolio tracker in the report observer is a **shadow** of the real portfolio — it can diverge if fills are corrupted
- When modifying `_on_fill_portfolio()` in `app.py`, always ensure DB persistence happens for ALL code paths (buy, sell, zero-out)
- The `v2_positions` table entry_time reflects the last upsert, not the original position open time
