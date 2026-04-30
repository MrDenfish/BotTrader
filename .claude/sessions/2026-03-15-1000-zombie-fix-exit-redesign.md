# Session: Zombie Position Fix & Exit Strategy Redesign
**Date:** 2026-03-15 ~10:00–18:30 PDT
**Duration:** ~8.5 hours

## Git Summary

**2 commits made** (925eee6 → 671118c)
**7 files changed** (+286 / -8 lines)

| File | Change Type |
|------|------------|
| `v2/core/app.py` | Modified — merge position symbols at startup |
| `v2/plugins/pair_discovery/kraken.py` | Modified — always publish SymbolsUpdatedEvent |
| `v2/plugins/risk/exit_manager.py` | Modified — stale_exit conditioned on negative P&L |
| `v2/plugins/execution/maker_only.py` | Modified — buy order TTL feature |
| `v2/kraken_paper_trading.yaml` | Modified — added buy_order_ttl_seconds config |
| `v2/tests/test_exit_manager.py` | Modified — 7 new TestStaleExit tests + timedelta import |
| `v2/tests/test_v1_feature_ports.py` | Modified — 5 new buy TTL tests |

### Commits
1. `ac112ca` — fix: Zombie position prevention at startup and during stable refreshes
2. `671118c` — feat: Buy order TTL and condition stale_exit on negative P&L

### Final Test Count: 683 passing (was 671)

## Key Accomplishments

### 1. Diagnosed and fixed zombie position bug
- **Problem:** 4 of 5 open positions (ZRO-USD, DOT-USD, RIVER-USD, XLM-USD) had zero ticker data — exit manager couldn't fire any stops. ZRO-USD was stuck open 22 days.
- **Root cause:** Two gaps in the zombie prevention from commit 925eee6:
  - Startup path didn't merge position symbols into the pair discovery list
  - Pair discovery skipped publishing `SymbolsUpdatedEvent` when symbol list was unchanged (every 30 min), so the WS handler's zombie merge code never ran
- **Fix:** (1) Merge position symbols into startup list in `app.py`, (2) Always publish event in pair discovery refresh even when unchanged
- **Result on deploy:** 4 zombie positions immediately cleared — RIVER-USD +69.7%, ZRO-USD +22.2%, XLM-USD +0.03%, DOT-USD -7.4% (hard stop)

### 2. Redesigned stale exit semantics
- **User insight:** "Stale" should apply to unfilled orders, not open positions. Positions always move and should exit via hard/trailing stops.
- **Change:** `max_hold_hours: 48` stale_exit now only fires when fee-aware P&L is negative. Positive P&L positions ride until stops handle them.
- **Evidence:** ZRO-USD would have been stale_exited at +22% instead of letting trailing capture more upside.

### 3. Added buy order TTL
- **Problem:** Existing `stale_timeout_seconds` requires both time AND drift to cancel. An unfilled buy sitting at the limit price indefinitely wastes capital.
- **Solution:** `buy_order_ttl_seconds: 600` — unconditional 10-minute hard TTL for unfilled buy orders. Drift-based cancellation stays as a faster path. Sell orders unaffected.

## Problems Encountered and Solutions

| Problem | Solution |
|---------|----------|
| `v2_fills` schema doesn't have `notional` column | Queried schema first, used correct columns |
| Tests subscribed to bus with string `"signal"` instead of `SignalEvent` type | Changed to `bus.subscribe(SignalEvent, ...)` |
| Soft stop firing before stale_exit in tests (Layer 3 before 3.5) | Set `soft_stop_pct=0.99` in stale exit tests |
| Peak state missing `peak_price` and `price_history` keys in test | Matched real state structure from `exit_manager.py:724` |
| Missing `timedelta` import in test file | Added to existing datetime import |

## Configuration Changes
- `v2/kraken_paper_trading.yaml`: Added `buy_order_ttl_seconds: 600`

## Deployments
1. `ac112ca` deployed — zombie fix, 4 positions cleared immediately
2. `671118c` deployed — buy TTL + stale exit P&L condition

Both deployed via standard git workflow: push → pull on AWS → `docker compose up -d --build v2-kraken`

## Memory Files Created/Updated
- Created: `memory/exit_strategy_redesign.md` — full plan for Phase 1 (done) + Phase 2 (adaptive TTL, future)
- Updated: `memory/MEMORY.md` — added Exit Strategy Redesign section

## What Wasn't Completed
- **Adaptive TTL (Phase 2):** Scale buy order TTL by ATR (high volatility = shorter TTL). Deferred until Phase 1 behavior is observed in production.

## Tips for Future Developers
- The `EventBus.subscribe()` takes a **type** (e.g., `SignalEvent`), not a string. Tests using string subscriptions silently receive nothing.
- When testing exit manager layers, disable earlier layers (hard_stop, soft_stop) by setting them to 0.99 to isolate the layer under test.
- Peak state dict must include: `peak_price`, `price_history`, `entry_time`, `trigger_type`, `breakeven_activated`
- The zombie fix has two layers: (1) startup merge in `app.py`, (2) refresh-time merge in WS handler via `SymbolsUpdatedEvent`. Both are needed.
- `v2_positions` table: `avg_entry_price` and `cost_basis` may be stale after partial sells. The exit manager uses in-memory portfolio (correct).
