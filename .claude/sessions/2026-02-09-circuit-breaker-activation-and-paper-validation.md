# Session: Circuit Breaker Activation & v2-Paper Validation

**Date:** 2026-02-09
**Duration:** ~3 hours (extended session with multiple deploy-wait-check cycles)
**Branch:** main

---

## Git Summary

**4 commits made:**

| Commit | Description | Files Changed |
|--------|-------------|---------------|
| `32e0352` | Activate v2 circuit breaker — risk chaining, fill/rejection wiring | 10 files, +358/-26 |
| `c2043ed` | Match v2-paper symbol list with v1 (21 symbols) | 1 file, +22/-1 |
| `68c817a` | Loosen v2-paper params — enable red-day buys, $30 notional | 1 file, +2/-2 |
| `518ca7d` | PaperExchange uses last price as bid/ask fallback | 1 file, +13/+0 |

**Files changed (13 total):**
- `v2/core/app.py` — Risk manager chaining, FillEvent/OrderEvent wiring
- `v2/core/config.py` — Risk field changed from singleton to list[PluginRef]
- `v2/plugins/execution/maker_only.py` — OrderEvent publishing on rejection
- `v2/plugins/risk/circuit_breaker.py` — RiskEvent publishing on veto
- `v2/plugins/exchanges/paper.py` — Bid/ask fallback to last ticker price
- `v2/paper_trading.yaml` — Risk list, 21 symbols, red-day gate, $30 notional
- `v2/config.yaml` — Risk list format with both managers
- `v2/backtest_4h.yaml` — Risk list format
- `v2/backtest_composite.yaml` — Risk list format
- `v2/tests/test_config.py` — 3 new tests for risk config parsing
- `v2/tests/test_milestone5_risk.py` — 11 new tests for risk chaining/wiring

**Final git status:** Clean (all session changes committed and pushed)

---

## Key Accomplishments

### 1. Circuit Breaker Fully Wired (Task 1-3 of plan)
- **Risk manager chaining**: Changed `config.py` risk field from `PluginRef` (singleton) to `list[PluginRef]` with backward-compatible parsing
- **Signal chaining**: `app.py` chains `check_signal()` through all risk managers sequentially; first veto stops the chain
- **FillEvent wiring**: All risk managers now receive fills via `on_fill()` — enables daily P&L tracking (basic) and loss detection (circuit_breaker)
- **OrderEvent rejection propagation**: `maker_only.py` publishes OrderEvent on rejection; `app.py` forwards to risk managers' `on_rejection()`
- **Circuit breaker veto publishing**: Tripped circuit breaker now publishes RiskEvent (visible to alerting/observers)

### 2. v2-Paper Symbol Expansion
- Expanded from 2 symbols (BTC-USD, ETH-USD) to 21 symbols matching v1 production

### 3. Parameter Tuning for Validation
- `allow_buys_on_red_day: true` (was false, blocking all buys in down market)
- `default_notional: 30` (was 500, now matches v1's ORDER_SIZE_FIAT)

### 4. PaperExchange Bid/Ask Fix (Critical)
- Discovered Coinbase `ticker_batch` channel does NOT include `best_bid`/`best_ask` fields
- PaperExchange had no bid/ask data → all order execution failed silently with "No bid/ask available"
- Fixed by falling back to last known ticker price in `_on_ticker()`, `get_ticker()`, and `_get_market_fill_price()`

### 5. All Tests Passing
- 260 tests pass (246 existing + 14 new)
- 15 plugins still discovered across 7 categories

---

## Problems Encountered and Solutions

| Problem | Root Cause | Solution |
|---------|-----------|----------|
| Only one risk manager could run | `config.py` had `risk: PluginRef` (singleton) | Changed to `list[PluginRef]` with backward compat |
| `on_fill()` never called on risk managers | `app.py` didn't wire FillEvent to risk | Added FillEvent subscription in `_wire_events()` |
| Circuit breaker rejection tracking dead | `maker_only.py` silently returned None | Published OrderEvent on rejection |
| v2 had 0 signals vs v1's 834 | v2 only monitored 2 symbols vs v1's 21 | Expanded to 21 symbols |
| Docker restart didn't pick up YAML changes | YAML baked into Docker image at build time | Must `docker compose build` before restart |
| `allow_buys_on_red_day: false` blocked all buys | Down market = all 24h changes negative | Set to `true` for validation phase |
| 493 signals but 0 orders executed | `ticker_batch` doesn't provide bid/ask | PaperExchange falls back to ticker price |
| No new signals after restart (40min) | Market calm, no thresholds met | Expected behavior — WLFI was +4.37% (needs >8.5%) |

---

## Configuration Changes

**`v2/paper_trading.yaml`:**
```yaml
# Risk: single dict → list with both managers
risk:
  - type: "basic"
    pass_through: false
    hodl_symbols: []
  - type: "circuit_breaker"
    max_losses_in_window: 5
    loss_window_minutes: 30
    large_loss_threshold_usd: 50
    max_consecutive_rejections: 10
    cooldown_minutes: 30

# Symbols: 2 → 21 (matching v1)
# allow_buys_on_red_day: false → true
# default_notional: 500 → 30
```

---

## Deployment Steps

All 4 commits were deployed individually:
1. `git push origin main`
2. `ssh bottrader-aws "cd /opt/bot && git pull origin main"`
3. `ssh bottrader-aws "cd /opt/bot && docker compose -f docker-compose.aws.yml build v2-paper"`
4. `ssh bottrader-aws "cd /opt/bot && docker compose -f docker-compose.aws.yml up -d v2-paper"`
5. Verified container healthy, WebSocket connected, candles flowing

---

## Breaking Changes / Important Findings

- **`config.py` risk field type changed**: `PluginRef` → `list[PluginRef]`. Backward compatible — single dict auto-wrapped in list.
- **v2 has diverged from v1**: v2 includes `roc_momo_24h` and `roc_momo_20m` momentum strategies that v1 doesn't have. User decided to validate v2 on its own merits rather than comparing apples-to-apples.
- **Coinbase `ticker_batch` confirmed**: Does NOT provide `best_bid`/`best_ask` fields (per Coinbase docs). The PaperExchange fallback is the correct solution.

---

## What Wasn't Completed

- **Overnight signal validation**: v2-paper is running and needs to accumulate signals overnight. Check in the morning for:
  - `docker exec v2-paper wc -l /app/logs/v2_score_log.jsonl` (signal count)
  - `docker logs v2-paper 2>&1 | grep 'Paper fill'` (actual order fills)
  - `docker logs v2-paper 2>&1 | grep -i 'circuit'` (circuit breaker activity)
- **Live signal comparison**: v1 vs v2 signal comparison deferred — strategies have diverged
- **Live trading cutover**: Pending overnight paper validation

---

## Lessons Learned

1. **`ticker_batch` doesn't include bid/ask** — this is documented by Coinbase but easy to miss. Always verify what fields a WebSocket channel actually provides.
2. **Docker image rebuild required for config changes** — YAML files are baked into the image. `docker restart` alone won't pick up changes.
3. **40-candle warmup** — composite scoring strategy needs 40 one-minute candles before evaluating. Plan for ~40min cold start.
4. **Market conditions matter** — zero signals doesn't mean broken code. The roc_momo_24h trigger needs >8.5% 24h change, which only happens during significant moves.
5. **Risk manager chaining order matters** — basic (per-signal vetting) should run before circuit_breaker (systemic protection).

---

## Tips for Future Developers

- **Check signals**: `docker exec v2-paper tail -5 /app/logs/v2_score_log.jsonl`
- **Check fills**: `docker logs v2-paper 2>&1 | grep 'Paper fill'`
- **Check 24h changes**: The session includes a Python snippet that connects to Coinbase WS and prints current 24h changes for all symbols
- **Circuit breaker thresholds**: Conservative for paper mode — $50 large loss (10% of $500 default), 5 losses in 30 min, 10 consecutive rejections
- **To add more risk managers**: Just add another entry to the `risk:` list in YAML — the chaining is automatic
