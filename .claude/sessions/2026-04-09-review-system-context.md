# Session: SYSTEM_CONTEXT Review, Bug Fixes & Hardening — 2026-04-09

## Overview
- **Started:** 2026-04-09
- **Duration:** ~2 hours

## Git Summary

**4 commits made** (7451af6 → b2e9e24)
**5 files changed** (+126 / -12 lines)

### Commits
1. `a3f6198` — fix: Exit manager MARKET orders + pending exit cancel handling
2. `09514ee` — docs: Update SYSTEM_CONTEXT.md — 8 freshness/consistency fixes
3. `b2e9e24` — fix: Order persistence, symbol logging, indicator warmup

### Final Test Count: 685 passing (was 683)

## Key Accomplishments

### 1. Diagnosed and fixed TRU-USD stuck position (2 bugs)
- **Bug 1**: Trailing stop and peak tracking exits defaulted to LIMIT orders instead of MARKET. A stale LIMIT sell drifted away and was cancelled without filling.
- **Bug 2**: `_pending_exits` was never cleared on order cancellation (only on fill), permanently blocking exit re-evaluation. TRU-USD was stuck open for 3 days.
- **Fix**: All stop-type exits now use `OrderType.MARKET`; new `on_order_cancel()` handler clears `_pending_exits` on sell order cancellation.
- **Result on deploy**: TRU-USD immediately exited via hard stop at -16.56%.

### 2. SYSTEM_CONTEXT.md — 8 freshness fixes
- Test count 672→685+, hard stop description clarity, plugin table (all 30 plugins), sizing table (momo notionals), backtest config drift status, changelog gap, out-of-sample note, date bump.

### 3. Three code hardening fixes
- **Order persistence**: Wired `OrderEvent` → `record_order()` in app.py. `v2_orders` was empty.
- **Pair discovery logging**: Startup now logs full symbol list.
- **Indicator warmup**: `min_bars` 40→80 (6.7h at 5-min candles) for reliable MACD/RSI/ADX on newly discovered symbols.

### 4. Phase 1 behavior analysis (25 days of data)
- 13 post-deploy matched trades, 60% win rate (excluding roc_momo_20m bug exits)
- Stale exit conditioning working (0 stale exits post-deploy)
- Buy TTL active (2 cancellations visible in current log window)
- roc_momo_20m sell bug caused 3 unwanted exits before Apr 4 fix

## Deployments
1. `a3f6198` deployed — exit manager fix, TRU-USD cleared immediately
2. `b2e9e24` deployed — order persistence, symbol logging, warmup increase

## Memory Files Created/Updated
- Created: `memory/open_work_items.md` — prioritized backlog grouped by session
- Updated: `memory/MEMORY.md` — added session summary and backlog pointer

## What Wasn't Completed
- See `memory/open_work_items.md` for full prioritized backlog:
  - P1: Backtest config alignment + overfitting validation (one session)
  - P2: Hard stop tuning for volatile small-caps + adaptive TTL (one session)
  - P3: Streamlit dashboard (multi-session)
