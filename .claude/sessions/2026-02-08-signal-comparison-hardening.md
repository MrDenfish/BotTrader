# Session: Signal Comparison Hardening & Deploy

**Date:** 2026-02-08 (continuation session)
**Duration:** ~15 minutes (context continuation from prior session)
**Branch:** `main`

---

## Git Summary

### Commits Made (1 in this session)
| Commit | Message |
|--------|---------|
| `1aa5d5a` | feat: Add symbol filtering and HODL docs to compare_signals.py |

### Files Changed
| File | Change Type | Details |
|------|------------|---------|
| `scripts/compare_signals.py` | Modified | +41/-14 lines — symbol filtering, indexed alignment, HODL docs |

### Final Git Status
- Clean working tree (no uncommitted changes to tracked files)
- Several untracked session files and archive/ directory

---

## Key Accomplishments

1. **Committed and deployed** the updated `compare_signals.py` that was edited but uncommitted from the prior session
2. **Pushed to `main`** and deployed to AWS via `git pull`
3. **Updated MEMORY.md** with HODL behavior docs and Docker build caching note

## Features Implemented

### compare_signals.py Hardening
- **`--symbols` flag**: Defaults to `BTC-USD ETH-USD` (the symbols v2 tracks), filters v1's 31-symbol log
- **Indexed alignment**: Changed from O(n*m) to O(n+m) per symbol using `v2_by_symbol` dict
- **HODL documentation**: Docstring explains v1's `HODL=ATOM,ETH,BTC` blocks sell *execution* but not signal *generation*, making signal-layer comparison valid
- **Report improvements**: Shows which symbols were compared in the header

## Deployment Steps Taken

1. `git add scripts/compare_signals.py && git commit`
2. `git push origin main`
3. `ssh bottrader-aws "cd /opt/bot && git pull origin main"` — fast-forward deploy
4. No container restart needed (CLI script only)

## Problems Encountered

- **Branch mismatch**: Initial push tried `refactor/plugin-architecture` but we're on `main` now (branch was deleted and merged in earlier session). Quick fix: push to `main` instead.

## Important Findings

- **HODL behavior confirmed**: v1's HODL list (`ATOM, ETH, BTC`) only blocks sell execution at the order_manager.py and profit_manager.py level. Sell signals are still generated in `scores.jsonl`. This means comparing v1 vs v2 at the signal generation layer is valid for all tracked symbols.

## What Wasn't Completed

- **24h+ parallel signal collection**: v2-paper is running alongside v1, but needs a volatile market period to generate meaningful signals for comparison
- **v2 HODL gate**: Before switching v2 to live, it needs a HODL mechanism at the execution layer to match v1's behavior
- **Backlog**: exit_reason persistence, validators.py typo, security issues from code review

## Tips for Future Developers

- Current branch is `main` — `refactor/plugin-architecture` was deleted and fully merged
- The compare script filters by symbol, so it won't be confused by v1's 31-symbol output
- v2-paper is running on AWS (`v2-paper` container) — check with `docker compose -f docker-compose.aws.yml ps`
- Signal logs: v1 writes `logs/scores.jsonl`, v2 writes `logs/v2_score_log.jsonl`
- Run comparison: `python scripts/compare_signals.py` (uses sensible defaults)

---
---

# Session 2: v2 HODL Gate + v1 Backlog Fixes

**Date:** 2026-02-08 (continuation session)
**Duration:** ~30 minutes
**Branch:** `main`

---

## Git Summary

### Commits Made (1 in this session)
| Commit | Message |
|--------|---------|
| `9927b11` | feat: Add v2 HODL gate + v1 exit_reason persistence + security fixes |

### Files Changed (9 files, +80/-8 lines)
| File | Change Type | Details |
|------|------------|---------|
| `v2/plugins/risk/basic.py` | Modified | +10 lines — HODL gate: blocks BUY+SELL for configured symbols |
| `v2/paper_trading.yaml` | Modified | +2/-1 — pass_through=false, hodl_symbols=[] |
| `v2/tests/test_milestone5_risk.py` | Modified | +42 lines — 5 new HODL gate tests |
| `TableModels/trade_record.py` | Modified | +1 line — exit_reason column |
| `SharedDataManager/trade_recorder.py` | Modified | +4 lines — exit_reason in trade_dict |
| `MarketDataManager/position_monitor.py` | Modified | +9 lines — _pending_exit_reasons dict + pop_exit_reason() |
| `webhook/websocket_market_manager.py` | Modified | +10 lines — exit_reason lookup before enqueue |
| `Config/validators.py` | Modified | +1/-1 — typo fix: `if __main__` → `if __name__` |
| `main.py` | Modified | +1/-6 — removed API key length logging + PYTHONASYNCIODEBUG=1 |

### Final Git Status
- Clean working tree (no uncommitted changes to tracked files)
- Untracked: session files, archive/, backtest_results/

---

## Key Accomplishments

1. **v2 HODL gate** — BasicRiskManager now blocks BUY+SELL for `hodl_symbols` list, matching v1 behavior
2. **exit_reason persistence** — Full pipeline wired: position_monitor stores reason by order_id → websocket_market_manager pops on fill → trade_recorder writes to DB
3. **validators.py typo** — CLI entry point now works (`python -m Config.validators`)
4. **Security fixes** — No more API key metadata in logs, no asyncio debug overhead in production
5. **246 tests passing** — 241 existing + 5 new HODL gate tests
6. **Deployed to AWS** — All containers restarted and verified clean

## Features Implemented

### v2 HODL Gate (`v2/plugins/risk/basic.py`)
- `hodl_symbols` config param (set of symbol strings)
- Placed after `pass_through` check, before daily loss and other risk checks
- Blocks both BUY and SELL signals for HODL symbols (publishes RiskEvent veto)
- Paper mode: `hodl_symbols: []` (no blocking); live mode: set to `["BTC-USD", "ETH-USD", "ATOM-USD"]`
- Signal comparison observer logs signals *before* risk gate, so HODL-gated signals still captured

### exit_reason Persistence (v1)
- `_pending_exit_reasons: dict[str, str]` in position_monitor — keyed by order_id
- `pop_exit_reason(order_id)` — retrieves and removes on fill receipt
- WebSocket fill handler strips `-FILL-N` suffix to match original order_id
- Only attached to SELL trades (exit reasons don't apply to buys)
- DB column: `exit_reason VARCHAR` (nullable) on `trade_records` table
- Exit reason codes: HARD_STOP, TRAILING_STOP, TAKE_PROFIT, SIGNAL_EXIT, SOFT_STOP

## Configuration Changes

- `v2/paper_trading.yaml`: `pass_through` changed from `true` to `false`
- `v2/paper_trading.yaml`: Added `hodl_symbols: []`

## Deployment Steps Taken

1. `git add` 9 files → `git commit`
2. `git push origin main`
3. `ssh bottrader-aws "cd /opt/bot && git pull origin main"`
4. `ssh bottrader-aws "cd /opt/bot && docker compose -f docker-compose.aws.yml restart"`
5. Verified deployment: `git log --oneline -3` on AWS
6. DB migration (`ALTER TABLE trade_records ADD COLUMN exit_reason VARCHAR`) — auto-applied by SQLAlchemy on container restart
7. Verified all 4 containers running cleanly:
   - **v2-paper**: WebSocket connected, candle aggregation at minute boundaries, heartbeats healthy
   - **webhook**: ticker_batch streaming 365 symbols, position monitor cycling 30s, HODL assets skipped
   - **sighook**: Signal generation active, scoring matrix across 18 symbols, shared data refreshing 60s

## Problems Encountered

- **DB migration wrong user**: First attempt used `psql -U tradebot` (nonexistent). Checked `docker-compose.aws.yml` → correct user is `bot_user`, db is `bot_trader_db`. Moot point — SQLAlchemy auto-created the nullable column on container restart.

## Breaking Changes / Important Findings

- **pass_through=false** now active in v2 paper mode — risk checks are live (but hodl_symbols is empty, so no blocking in paper)
- **SQLAlchemy auto-migration**: Adding a nullable column to a model auto-creates it when the app starts — no manual `ALTER TABLE` needed for simple column additions
- **exit_reason is SELL-only**: Only populated for sell fills; buy trades will have `NULL` exit_reason

## What Wasn't Completed

- **24h+ parallel signal collection**: v2-paper running alongside v1, needs time to accumulate
- **Circuit breaker in sender.py**: Deferred to future task (larger scope)

## Completed Backlog Items (from 2026-02-04 code review)

| Item | Status |
|------|--------|
| exit_reason persistence | DONE |
| validators.py typo | DONE |
| API key metadata logging | DONE |
| Debug mode in production | DONE |
| Silent WebSocket exceptions | Already handled (leave as-is) |
| `if False:` blocks in signal_manager.py | Already resolved |
| Circuit breaker in sender.py | Deferred (future task) |

## Tips for Future Developers

- v2 HODL gate test: `python -m pytest v2/tests/test_milestone5_risk.py -k hodl -v`
- For live mode, set `hodl_symbols: ["BTC-USD", "ETH-USD", "ATOM-USD"]` in v2 config
- exit_reason values come from `position_monitor._place_exit_order()` — search for `reason =` assignments
- The `-FILL-N` suffix stripping in websocket_market_manager.py is critical — Coinbase appends fill suffixes to order IDs
- Next milestone: Run 24h+ parallel, compare v1 vs v2 signals, then switch v2 to live
