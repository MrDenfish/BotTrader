# Pair Discovery Plugin - Dynamic Volume-Based Trading Pair Selection for v2

## Session Overview
- **Started:** 2026-02-10 10:29
- **Branch:** main
- **Context:** v2-paper deployed and generating signals/fills, but uses a static 21-symbol YAML list. v1 dynamically discovers pairs from Coinbase API and filters by 24h volume. Need to implement equivalent functionality as a v2 plugin.

## Goals
1. Implement a `PairDiscovery` plugin that ports v1's dynamic pair discovery and volume filtering to v2's plugin architecture
2. Add `get_products()` method to CoinbaseExchange adapter
3. Create new `PairDiscoveryProvider` ABC in interfaces.py
4. Create `CoinbasePairDiscovery` plugin with v1's two-pass volume filter
5. Add `SymbolListUpdatedEvent` for dynamic symbol list updates via EventBus
6. Wire into app.py so WebSocket provider reacts to symbol changes
7. Add YAML configuration (min_quote_volume, refresh interval, shill coins)
8. Write tests and deploy to AWS

## Progress

### All tasks completed.

---

## Session Summary

- **Duration:** ~2 hours (10:29 — ~12:30)
- **Branch:** main
- **Outcome:** Pair discovery plugin fully implemented, tested, deployed, and verified on AWS

---

### Git Summary

**Commits:** 2
1. `13f7745` — `feat: Add pair discovery plugin — dynamic volume-based pair selection`
2. `b1a81aa` — `fix: Pass inner config dict to pair discovery configure()`

**Total files changed:** 10 (3 added, 7 modified)

| File | Change |
|------|--------|
| `v2/core/types.py` | Modified — added `SymbolsUpdatedEvent` |
| `v2/core/interfaces.py` | Modified — added `PairDiscovery` ABC |
| `v2/core/registry.py` | Modified — added `pair_discovery` category + factory |
| `v2/core/config.py` | Modified — added optional `pair_discovery` config field |
| `v2/plugins/pair_discovery/__init__.py` | **Added** — new plugin package |
| `v2/plugins/pair_discovery/coinbase.py` | **Added** — CoinbasePairDiscovery plugin (300 lines) |
| `v2/plugins/data/websocket.py` | Modified — SymbolsUpdatedEvent handler + flag-based reconnect |
| `v2/core/app.py` | Modified — pair discovery lifecycle (discover, start, stop) |
| `v2/paper_trading.yaml` | Modified — added pair_discovery section, trimmed fallback symbols |
| `v2/tests/test_pair_discovery.py` | **Added** — 26 tests (482 lines) |

**Stats:** +905 insertions, -9 deletions

**Final git status:** Working tree clean for v2/ (all pair discovery changes committed and pushed). Unrelated uncommitted changes exist in docs/ and .claude/sessions/.

---

### Todo Summary

**6/6 tasks completed, 0 remaining**

1. Add SymbolsUpdatedEvent and PairDiscovery ABC — completed
2. Implement CoinbasePairDiscovery plugin — completed
3. Add WebSocket SymbolsUpdatedEvent handling — completed
4. Wire pair discovery into app lifecycle — completed
5. Update YAML config and write tests — completed
6. Run tests, commit, and deploy — completed

---

### Key Accomplishments

1. **New plugin category**: `PairDiscovery` is the 8th plugin ABC in v2's architecture
2. **v1 algorithm ported**: Two-pass volume filter (average 24h USD quote volume) matches v1's `filter_volume_for_market_data()`
3. **Dynamic pair selection**: v2-paper now discovers 18 symbols from 872 Coinbase products (was 12 static)
4. **Live on AWS**: Container rebuilt, restarted, and verified working with correct config
5. **16 plugins discovered** across 8 categories (up from 15 across 7)

---

### Features Implemented

- **CoinbasePairDiscovery plugin**: Own REST client with JWT auth, fetches all products from Coinbase API, applies two-pass volume filter, excludes shill coins, always includes seed symbols, sorts by volume descending, caps at max_pairs
- **Periodic refresh**: Every 30 minutes, re-discovers pairs and publishes `SymbolsUpdatedEvent` if the set changed
- **WebSocket reconnect**: Flag-based reconnect pattern — `_on_symbols_updated` sets an `asyncio.Event`, the `_ws_loop` checks it after each message and breaks cleanly to reconnect with new symbols
- **Graceful fallback**: If API fails at startup, uses YAML `app.symbols` as fallback; if refresh fails, keeps current symbols
- **Config**: `pair_discovery` is optional in YAML — backward compatible, `cfg.pair_discovery is None` if absent

---

### Problems Encountered and Solutions

1. **Config nesting issue**: `_parse_plugin_ref()` puts YAML's `config:` block under `PluginRef.config["config"]`, so `configure()` wasn't seeing shill_coins/seed_symbols. Fixed by extracting inner config in app.py: `cfg.pair_discovery.config.get("config", cfg.pair_discovery.config)`.

2. **Registry test isolation**: `discover_plugins()` can't re-register plugins that were already imported but cleared by `registry.clear()` in other tests (Python module cache). Fixed by using direct `registry.register()` in the pair discovery registry tests.

3. **Docker restart vs rebuild**: `docker compose restart` reuses the old image — changes weren't picked up. Had to `docker compose build v2-paper` then `up -d`.

---

### Configuration Changes

**`v2/paper_trading.yaml`:**
```yaml
pair_discovery:
  type: "coinbase"
  key_file: "/app/Config/websocket_api_info.json"
  config:
    refresh_interval_minutes: 30
    shill_coins: ["UNFI", "TRUMP", "MATIC"]
    seed_symbols: ["BTC-USD", "ETH-USD"]
    max_pairs: 50
```
- `app.symbols` trimmed from 21 to 12 (fallback only)

---

### Deployment Steps

1. `git push origin main`
2. `ssh bottrader-aws "cd /opt/bot && git pull origin main"`
3. `ssh bottrader-aws "cd /opt/bot && docker compose -f docker-compose.aws.yml build v2-paper"`
4. `ssh bottrader-aws "cd /opt/bot && docker compose -f docker-compose.aws.yml up -d v2-paper"`
5. Verified startup logs: 872 products fetched, 369 USD pairs, avg volume $5.1M, 18 symbols discovered

---

### Lessons Learned

- Always `build` Docker images after code changes, not just `restart`
- `_parse_plugin_ref()` nests `config:` blocks — any plugin that uses `configure()` needs to unwrap the inner dict
- Registry tests that call `discover_plugins()` are fragile in isolation — prefer direct `register()` calls in tests
- The plugin has its own REST client independent of the exchange adapter (works in paper mode where there's no CoinbaseExchange)

### Future Optimization

**Shared Coinbase REST Client**: JWT auth + aiohttp REST client code is duplicated across CoinbaseExchange, WebSocket provider, and CoinbasePairDiscovery. Extract to `v2/utils/coinbase_client.py` shared utility. Low priority — each plugin makes minimal API calls and the duplication is ~30 lines per plugin. (Noted in MEMORY.md)

---

### What Wasn't Completed

- Nothing left incomplete — all planned tasks finished and deployed

### Tips for Future Developers

- To add a new shill coin, update `paper_trading.yaml` → `pair_discovery.config.shill_coins`
- To force specific symbols, add them to `seed_symbols` (always included regardless of volume)
- To disable pair discovery entirely, remove the `pair_discovery:` section from YAML — `cfg.pair_discovery` will be `None` and the static `app.symbols` list is used
- The 30-min refresh publishes `SymbolsUpdatedEvent` which triggers WebSocket reconnect — check logs for "Symbols changed — reconnecting WebSocket"
- Tests: `python -m pytest v2/tests/test_pair_discovery.py -v` (26 tests)
