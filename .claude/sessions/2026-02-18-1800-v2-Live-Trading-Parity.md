# v2 Live Trading Parity — 2026-02-18

## Session Overview
- **Start time:** ~18:00 UTC
- **Status:** Complete
- **Commit:** `3570a11` — deployed to AWS (v2-paper + v2-kraken)

## Goals
Complete 8-phase plan to bring v2 to feature parity with v1 for live trading readiness.

## Progress

### Phase 1: Fee-Aware P&L for Exit Decisions (CRITICAL)
- All exit decisions now use fee-aware P&L: `entry_cost = entry × (1 + maker_fee)`, `exit_revenue = price × (1 - taker_fee)`
- Fees fetched from exchange API with 1hr cache, fall back to config defaults
- Both raw and net P&L logged in exit metadata
- 14 new tests

### Phase 2: Market Orders for Hard Stops (CRITICAL)
- Hard stops always emit `OrderType.MARKET` (no post-only, no retries)
- Severe soft stops (>0.5% past threshold) also use MARKET
- Normal soft/trailing stays LIMIT
- MakerOnlyExecution routes market signals directly to exchange
- 9 new tests

### Phase 3: ATR-Based Trailing Stops
- `trailing_mode: "atr"` computes 14-period ATR from candle history
- Trail distance = `atr_mult × ATR%`, constrained to `[min, max]` (default 1-2%)
- Stop price ratchets up only, never down
- Falls back to fixed distance when no candle data
- 8 new tests

### Phase 4: Peak Tracking for ROC Momentum Trades
- SMA-smoothed peak price tracking for `roc_momo_20m` / `roc_momo_24h` trades
- Exit priority: time limit (24h) → activation gate (+6%) → breakeven protection → peak drawdown (-5%)
- Once activated, remains active even if P&L drops below threshold
- State persisted across restarts
- 12 new tests

### Phase 5: WebSocket Channel Activity Monitoring
- Per-channel `_channel_last_seen` tracking (Coinbase + Kraken)
- If ticker channel silent >120s while heartbeats alive → force reconnect
- 2 new tests

### Phase 6: Trigger-Based Order Sizing
- `notional_by_trigger` map: `{score: 50, roc_momo_20m: 30, roc_momo_24h: 30}`
- Priority: signal.qty > signal.notional > trigger notional > default_notional
- 7 new tests

### Phase 7: Min Order Validation
- Orders where `qty × price < min_order_fiat` ($10) silently skipped
- 4 new tests

### Phase 8: Minor Adjustments
- Per-side stale order drift: `cancel_drift_buy_pct` / `cancel_drift_sell_pct`
- Position restore on restart: loads positions from PostgreSQL on startup
- Position save on fill: upserts to `v2_positions` table after every fill
- `save_position()` added to StorageAdapter interface + PostgreSQL implementation
- 3 new tests

## Files Modified
- `v2/plugins/risk/exit_manager.py` — Phases 1-4 (fee P&L, market orders, ATR trailing, peak tracking)
- `v2/plugins/execution/maker_only.py` — Phases 2, 6, 7, 8 (market routing, trigger sizing, min order, per-side drift)
- `v2/plugins/data/websocket.py` — Phase 5 (per-channel monitoring)
- `v2/plugins/data/kraken_websocket.py` — Phase 5 (per-channel monitoring)
- `v2/plugins/persistence/postgres.py` — Phase 8 (save_position upsert)
- `v2/core/interfaces.py` — Phase 8 (save_position ABC method)
- `v2/core/app.py` — Phases 1, 3, 8 (exchange wiring, candle sub, position restore/save)
- `v2/paper_trading.yaml` — All new config params (Coinbase)
- `v2/kraken_paper_trading.yaml` — All new config params (Kraken)
- `v2/tests/test_exit_manager.py` — 34 new tests
- `v2/tests/test_milestone4_live.py` — 18 new tests
- `v2/tests/test_v1_feature_ports.py` — 2 new tests
- `v2/tests/test_app_lifecycle.py` — 1 new test + stub update

## Test Count
- **554 → 611** (57 new tests, all passing)

## Deployment
- Pushed to `main`, pulled on AWS
- `v2-paper` (Coinbase) rebuilt and running — ticker + volume data flowing
- `v2-kraken` rebuilt and running — ticker + heartbeat data flowing
- No errors in either container

## Key Design Decisions
- **Backward compatible**: All features opt-in via YAML config with defaults that preserve existing behavior
- **Peak tracking activation**: Once `breakeven_activated=True`, the min_profit gate is skipped so breakeven protection can fire if price tanks after being profitable
- **Fee-aware everywhere**: All exit thresholds (hard, soft, trailing, signal, peak) use net P&L after fees
- **ATR ratchet**: Stop price only moves up, preventing whipsaw in volatile markets

---

## Session End Summary

- **Duration:** ~18:00 – ~19:00 UTC (continuation session from earlier Kraken integration work)
- **Commits this session:** 1 (`3570a11`)
- **Total commits since session start (including prior Kraken work):** 3

### Git Summary
- **13 files changed** in the live parity commit (+1,715 / -37 lines)
- Modified: `exit_manager.py`, `maker_only.py`, `websocket.py`, `kraken_websocket.py`, `postgres.py`, `interfaces.py`, `app.py`, `paper_trading.yaml`, `kraken_paper_trading.yaml`
- Modified (tests): `test_exit_manager.py`, `test_milestone4_live.py`, `test_v1_feature_ports.py`, `test_app_lifecycle.py`
- **Unstaged**: Old session files, docs, IDE config — not related to this work

### Task Summary
- **8/8 tasks completed:**
  1. Phase 1: Fee-Aware P&L for Exit Decisions
  2. Phase 2: Market Orders for Hard Stops
  3. Phase 3: ATR-Based Trailing Stops
  4. Phase 4: Peak Tracking for ROC Momentum Trades
  5. Phase 5: WebSocket Channel Activity Monitoring
  6. Phase 6: Trigger-Based Order Sizing
  7. Phase 7: Min Order Validation
  8. Phase 8: Minor Adjustments (per-side drift, position restore)
- **0 tasks remaining**

### Problems Encountered and Solutions
1. **Fee-shifted test thresholds**: Test prices for hard/soft stop were calculated assuming zero fees. Recalculated with fee-aware formula.
2. **Existing tests broke after fee defaults changed**: Two `_make_exit_manager` helpers in `test_v1_feature_ports.py` needed `maker_fee_pct: 0, taker_fee_pct: 0` to preserve deterministic behavior.
3. **Peak tracking vs soft stop priority**: Peak drawdown test failed because price drop triggered soft stop before peak tracking could act. Root cause: when pnl drops below `peak_min_profit_pct`, peak tracking defers to normal stops (correct v1 behavior). Fixed test to use a scenario where peak drawdown fires while pnl is still above min_profit.
4. **Breakeven protection unreachable**: In v1, the activation gate (`pnl >= 6%`) blocks breakeven (`pnl <= -fees`) from ever firing because they're mutually exclusive in a single check. Fixed by making activation gate one-time: once `breakeven_activated=True`, the min_profit gate is skipped on subsequent checks.

### Configuration Changes
Both `paper_trading.yaml` and `kraken_paper_trading.yaml` updated with:
- `maker_fee_pct` / `taker_fee_pct` (exit manager)
- `trailing_mode: "atr"`, `atr_period`, `atr_mult`, `trailing_min/max_distance_pct`
- `peak_tracking_enabled: true`, `peak_drawdown_pct`, `peak_min_profit_pct`, `peak_smoothing_prices`, `peak_max_hold_minutes`, `peak_triggers`
- `notional_by_trigger: {score: 50, roc_momo_20m: 30, roc_momo_24h: 30}`
- `min_order_fiat: 10`

### Deployment Steps
1. `git push origin main`
2. `ssh bottrader-aws "cd /opt/bot && git pull origin main"`
3. `docker compose -f docker-compose.aws.yml up -d --build v2-paper`
4. `docker compose -f docker-compose.aws.yml up -d --build v2-kraken`
5. Verified both containers streaming data with no errors after 2-min warmup

### What Wasn't Completed
- Nothing — all 8 phases fully implemented, tested, and deployed
- v2 is now at feature parity with v1 for live trading

### Tips for Future Developers
- **ALWAYS use `--build`** when deploying v2 containers — `docker compose restart` does NOT pick up code changes (code is baked into image via `COPY . /app`)
- When adding new exit_manager tests, default fees to 0 in `_make_exit_manager()` unless specifically testing fee behavior
- Peak tracking state is serialized with `datetime.isoformat()` — `load_state()` handles `fromisoformat()` deserialization
- `save_position()` is a non-abstract method on `StorageAdapter` (default no-op) so it doesn't break SQLite or other backends that don't implement it
- Per-side drift (`cancel_drift_buy_pct` / `cancel_drift_sell_pct`) falls back to `cancel_drift_pct` if not explicitly set
