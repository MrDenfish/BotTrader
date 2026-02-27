# v2.0 Milestones 1-6: Full Implementation & AWS Deployment

**Started**: 2026-02-07 07:57
**Ended**: 2026-02-07 ~19:15 (PST)
**Duration**: ~11 hours (multi-session, spanned context compactions)
**Branch**: `refactor/plugin-architecture` (merged into `main`)
**Status**: COMPLETE

---

## Overview

This session encompassed the implementation of v2.0 Milestones 1-6, culminating in a fully operational paper trading deployment on AWS running alongside v1. The session spanned multiple context windows due to its length and included implementation, debugging, deployment, and live troubleshooting.

## Git Summary

**Total commits**: 10 (on `main` after merge)
**Files changed**: ~40+ across v2/, docker/, scripts/

### Commits (chronological):
1. `e9d804a` feat: Implement v2.0 core framework (Milestone 1)
2. `7d64576` feat: Implement v2.0 backtest mode with 7 plugins (Milestone 2)
3. `0d246d8` feat: Implement composite scoring strategy plugin (Milestone 3)
4. `52a591b` feat: Implement live exchange plugins (Milestone 4)
5. `7985645` feat: Implement risk, persistence, and observability plugins (Milestone 5)
6. `52ba63b` feat: Add v2 paper mode deployment alongside v1 (Milestone 6)
7. `629c325` fix: Add pyyaml to requirements for v2 config loading
8. `4875031` fix: Load Coinbase credentials from JSON key file in websocket plugin
9. `5f21114` feat: Add 1-minute candle aggregation to WebSocket data provider
10. `0a7ad5c` fix: Add default_notional to execution manager for paper trading

### Key files created/modified:
- `v2/core/` — types.py, event_bus.py, interfaces.py, registry.py, config.py, app.py
- `v2/plugins/exchanges/` — backtest.py, coinbase.py, paper.py
- `v2/plugins/data/` — csv_replay.py, websocket.py (+ candle aggregation)
- `v2/plugins/strategies/` — hybrid_4h_maker/, composite_scoring/
- `v2/plugins/risk/` — basic.py, circuit_breaker.py
- `v2/plugins/execution/` — maker_only.py (+ default_notional), bracket.py
- `v2/plugins/persistence/` — sqlite.py, postgres.py (+ DSN normalization)
- `v2/plugins/observability/` — structured_log.py, daily_report.py, alerting.py, heartbeat.py, signal_comparison.py
- `v2/tests/` — 7 test files, 239 tests total
- `v2/paper_trading.yaml` — Production paper trading config
- `docker/Dockerfile.v2` — v2 container image
- `docker/entrypoint/entrypoint.v2.sh` — v2 entrypoint script
- `docker-compose.aws.yml` — Added v2-paper service
- `scripts/compare_signals.py` — Signal comparison CLI
- `requirements.in` / `requirements.txt` — Added pyyaml

### Final git status:
- Clean on `main` (all v2 changes committed and pushed)
- Untracked: session files, archive/, backtest_results/, log files

## Tasks Completed

- [x] Milestone 1: Core framework (types, event_bus, interfaces, registry, config, app)
- [x] Milestone 2: Backtest mode with 7 plugins, trade-for-trade validation (33 trades match)
- [x] Milestone 3: Composite scoring strategy plugin
- [x] Milestone 4: Live exchange plugins (coinbase, paper, websocket, bracket, maker_only)
- [x] Milestone 5: Risk, persistence, observability plugins
- [x] Milestone 6: Production cutover (heartbeat, signal_comparison, Dockerfile, entrypoint, docker-compose)
- [x] Candle aggregation in WebSocket data provider (post-deployment fix)
- [x] Default notional for execution manager (post-deployment fix)
- [x] AWS deployment and verification

## Key Accomplishments

1. **15 plugins** across 7 categories, all auto-discovered via registry
2. **239 tests** passing (event_bus, config, registry, types, app lifecycle, backtest validation, composite scoring, live exchange, risk, persistence, observability, deployment, candle aggregation)
3. **Trade-for-trade validation**: v2 backtest produces identical results to Phase 1 (33 trades exact match)
4. **Live paper trading**: v2-paper container running on AWS t3.medium alongside v1, receiving real market data, generating signals
5. **Signal comparison**: 42 signals logged to JSONL in v1-compatible format for comparison

## Problems Encountered and Solutions

| Problem | Solution |
|---------|----------|
| `ModuleNotFoundError: yaml` | Added `pyyaml==6.0.2` to requirements.in, ran pip-compile |
| API credentials not loading (env vars empty) | Added `key_file` param to WebSocketDataProvider to read CDP JSON key files directly |
| Multiline PEM key won't survive bash export | Abandoned entrypoint credential extraction, moved to in-code key_file loading |
| JWT auth type confusion (build_rest_jwt vs build_ws_jwt) | Reverted to `build_rest_jwt` — v1 also uses it for WebSocket auth |
| Auth failure on both v1 and v2 | Root cause: Elastic IP changed during t3.small->t3.medium upgrade. User updated Coinbase API IP allowlist |
| No signals generated after warmup | WebSocket only emitted TickerEvent, strategy needs CandleEvent. Implemented 1-minute candle aggregation |
| Signals generated but orders rejected (qty=None) | Strategy signals don't carry qty/notional. Added `default_notional` config to maker_only execution |
| Docker build "no space left on device" | Ran `docker system prune -af && docker builder prune -af` (freed 6.1 GB) |
| Test failures (registry API) | `list_plugins()` returns `dict[str, list[str]]` not list of dicts; `get_class()` not `get()` |

## Deployment Steps Taken

1. **Cleanup**: Deleted old rotated logs (~1GB), removed LXD snap (625MB), docker prune
2. **Instance upgrade**: User upgraded t3.small -> t3.medium (4GB RAM), new Elastic IP 44.238.14.228
3. **Merged** `refactor/plugin-architecture` into `main`, pushed
4. **Built** v2-paper container: `docker compose build v2-paper`
5. **Started** v2-paper: `docker compose up -d v2-paper`
6. **Multiple fix iterations**: pyyaml, key_file, JWT auth, candle aggregation, default_notional
7. **Final state**: All 4 containers healthy (db, webhook, sighook, v2-paper)

## Configuration Changes

- `v2/paper_trading.yaml`: storage postgres (dsn_env: DATABASE_URL), risk pass_through, execution default_notional: 500, observers (structured_log, signal_comparison, heartbeat), websocket key_file
- `docker-compose.aws.yml`: Added v2-paper service (512MB limit, heartbeat healthcheck, depends on db)
- `requirements.in`: Added pyyaml==6.0.2

## Lessons Learned

1. **Candle aggregation is essential**: WebSocket ticker_batch provides raw prices, but strategies need OHLCV candles. This gap wasn't in the original plan and required a post-deployment fix.
2. **Strategies don't carry trade size**: The composite scoring strategy only outputs direction signals. Execution managers need a `default_notional` fallback for live/paper mode.
3. **IP allowlists break on instance upgrade**: EC2 instance type changes can reassign Elastic IPs, breaking API key IP allowlists.
4. **CDP key files**: Coinbase uses JSON files with `name` (key ID) and `signing_key` (PEM) fields. Multiline PEM keys don't survive bash env var export — read them directly in Python.
5. **Docker disk space**: Frequent rebuilds consume disk fast on small instances. Regular `docker system prune` is needed.

## What Wasn't Completed

- Signal comparison analysis (v1 vs v2 side-by-side) — needs 24+ hours of parallel data
- Merging fixes back to `refactor/plugin-architecture` branch (all work done on `main`)
- Reducing WebSocket debug logging verbosity (very chatty with websockets.client messages)

## Tips for Future Developers

- v2 paper mode needs ~40 minutes after startup before generating signals (min_bars=40 warmup)
- During quiet markets, the strategy correctly returns HOLD (no signals) — this is expected
- The `default_notional: 500` means each trade targets $500 USD worth of the asset
- Signal comparison: `python scripts/compare_signals.py --v1 logs/score_log.jsonl --v2 logs/v2_score_log.jsonl`
- To check v2 health: `docker compose logs v2-paper --tail 20`, `ls -la logs/v2/heartbeat`
- v2 uses `v2_*` prefixed PostgreSQL tables — doesn't conflict with v1
