# Plugin Architecture Migration

**Status**: Complete. v1 archived. v2 is the sole production system (paper trading on Kraken).

## Original Plan (2026-02-06)

Phased migration from monolithic architecture to plugin-based system with 4 motivations:

1. Decompose monolithic `strategy_4h_hybrid.py` (2,813 lines)
2. Independent testing of production and backtest strategies
3. A/B strategy comparison with consistent infrastructure
4. Extensibility — new strategies without modifying core engine

## What Was Actually Built

The implementation expanded well beyond the original 4-phase plan. Rather than wrapping v1 code in plugin interfaces, a full v2 system was built from scratch in `v2/` with 8 plugin categories, an event bus, auto-discovery registry, and multi-mode support. v1 was archived and v2 became the sole production system.

---

## Phase 1: Extract Interfaces

**Original scope**: Define ABCs for strategy, risk, and data feed.

**Delivered**: 7 plugin ABCs (added exchange, execution, storage, observer beyond original scope), plus a full shared type system (Candle, Signal, Order, Fill, Position, Portfolio) and 7 event types for pub/sub communication.

| Milestone | What |
|-----------|------|
| Milestone 1 | `v2/core/` — types.py, event_bus.py, interfaces.py, registry.py, config.py, app.py |

## Phase 2: Refactor Strategies as Plugins

**Original scope**: Wrap `signal_manager.py` and `strategy_4h_hybrid.py` as plugins.

**Delivered**: Both strategies ported as plugins, plus 13 additional plugins across 6 other categories.

| Milestone | What |
|-----------|------|
| Phase 1 (pre-v2) | `strategies/` package — decomposed 4H hybrid into 7 modules, backtest engine, validated 8/8 trades |
| Milestone 2 | Backtest mode with 7 plugins, trade-for-trade validation (33 trades exact match) |
| Milestone 3 | Composite scoring strategy extracted from `sighook/signal_manager.py` |
| Milestone 4 | Live exchange plugins — Coinbase, paper exchange, WebSocket data, maker-only and bracket execution |
| Milestone 5 | Risk management (basic + circuit breaker), persistence (postgres + sqlite), observability (5 observers) |

## Phase 3: Build Testing Framework

**Original scope**: Unified backtest runner, side-by-side comparison, consistent fee modeling.

**Delivered**:

| Item | Status |
|------|--------|
| Unified backtest runner | `v2/core/app.py` backtest mode — event-driven CandleEvent + TickerEvent per bar |
| Side-by-side comparison | `signal_comparison` observer + `scripts/compare_signals.py` CLI |
| Consistent fee modeling | Paper exchange with configurable maker_fee, taker_fee, slippage_bps |
| Test suite | 246 tests across 11 files (later grew to 672+) |
| Trade validation | 33-trade exact match (v2 backtest vs Phase 1 backtest) |

## Phase 4: Production Deployment

**Original scope**: Deploy, validate against existing behavior, enable hot-swapping.

**Delivered**:

| Item | Status |
|------|--------|
| Deployment | v2-paper running on AWS alongside v1 (Milestone 6) |
| Behavior validation | Signal pipeline validated end-to-end (24 signals logged) |
| Hot-swapping | Config-driven plugin selection via YAML (no runtime swap) |
| Docker | Dockerfile with pip cache optimization, `docker-compose.aws.yml` integration |
| Signal comparison | v1 `scores.jsonl` vs v2 `v2_score_log.jsonl`, CLI tool for analysis |
| HODL gate | Risk manager blocks BUY+SELL for configured HODL symbols |
| Candle aggregation | WebSocket ticker_batch → 1-minute OHLCV at minute boundaries |

## Phase 5: Live Trading Parity + Kraken Integration (2026-02-18)

Features needed to match v1 behavior and support a second exchange.

| Item | What |
|------|------|
| Fee-aware P&L | All exit decisions use entry*(1+maker_fee) vs price*(1-taker_fee) |
| Market orders for exits | Hard stops always MARKET. All stops changed from LIMIT to MARKET after stale order bug. |
| ATR trailing stops | `trailing_mode: "atr"`, constrained to [1%, 2%] distance |
| Peak tracking | SMA-smoothed peak for momentum trades. Activation/breakeven/drawdown/time-limit logic. |
| Per-channel WS monitoring | Ticker silent >120s + heartbeats alive → force reconnect |
| Trigger-based sizing | `notional_by_trigger: {score: 75, roc_momo_20m: 30}` |
| Position restore | Load positions from PostgreSQL on startup, upsert on every fill |
| **Kraken exchange** | REST orders + WS v2 executions channel (`v2/plugins/exchanges/kraken.py`) |
| **Kraken WebSocket** | WS v2 ticker → 1-min candle aggregation, REST volume backfill (`v2/plugins/data/kraken_websocket.py`) |
| **Kraken pair discovery** | Public API volume + spread filtering (`v2/plugins/pair_discovery/kraken.py`) |
| **Symbol mapper** | Bidirectional BTC-USD ↔ XBT/USD ↔ XBTUSD (`v2/utils/symbol_mapper.py`) |
| **8th plugin category** | `PairDiscovery` ABC + `SymbolsUpdatedEvent` added to core |
| **v1 stopped and archived** | All v1 code moved to `archive/v1/` via `git mv` (109 files) |
| Daily report v2 | Modular observer: 7 collectors, HTML (Jinja2) + Slack renderers, SMTP + Slack delivery |
| 20 plugins, 662 tests | Up from 15 plugins and 246 tests |

## Phase 6: Diagnostic Framework + Strategy Optimization (2026-02-23 to 2026-03-10)

Tooling for understanding trade quality and data-driven parameter tuning.

| Item | What |
|------|------|
| Backtest diagnostics observer | MFE/MAE per trade, post-exit 60-bar tracking, market regime snapshots |
| Random entry strategy | Poisson-distributed baseline for control group comparison |
| Signal metadata enrichment | All 3 signal paths carry indicator_snapshot, raw_values, score_components |
| Trend confirmation gate | Requires ≥1 trend indicator for buys. Eliminated falling-knife pattern (hard stops 18→9). |
| ATR hard stops | Dynamic per-symbol: 3× hourly ATR, clamped to [5.5%, 8%] |
| ADX gate | Suppress buys when ADX < 20 (no meaningful trend) |
| roc_momo_24h disabled | 28% hard stop rate in live trading — both momentum paths turned off |
| Performance filter | New risk manager: exclude symbols with poor rolling win rate / P&L |
| Pair discovery hardened | Bid-ask spread filter (max 100 bps). Eliminated junk tokens. |
| Phantom position fix | `save_position(qty=0)` before memory delete. Prevents stale DB restores. |
| Config sync | Live config aligned to backtest-validated params |
| 672 tests, 22 plugins | Test suite grew substantially |

## Phase 7: Exit Strategy Redesign (2026-03-15)

Targeted improvements to position management.

| Item | What |
|------|------|
| Buy order TTL | Hard cancel unfilled buys after 10 minutes (`buy_order_ttl_seconds: 600`) |
| Conditioned stale exit | `stale_exit` only fires when P&L < 0% (no longer ejects profitable positions) |
| Zombie position prevention | Position symbols merged into pair discovery list. Data providers notified of held symbols. |
| Report quiet-period fix | Reports sent even during quiet trading periods (no longer skipped on zero events) |

---

## Milestone Timeline

| # | Name | Date | Key Deliverable |
|---|------|------|----------------|
| Phase 1 | Strategy decomposition | 2026-02-06 | `strategies/` package, 8/8 trade validation |
| 1 | Core framework | 2026-02-07 | types, event_bus, interfaces, registry, config, app |
| 2 | Backtest mode | 2026-02-07 | 7 plugins, 33-trade exact match |
| 3 | Composite scoring | 2026-02-07 | Production strategy as v2 plugin |
| 4 | Live exchange | 2026-02-07 | Coinbase, paper, WebSocket, execution plugins |
| 5 | Risk + persistence + observability | 2026-02-07 | Risk gates, storage, logging, heartbeat |
| 6 | AWS deployment | 2026-02-08 | v2-paper container, signal pipeline validated |
| 7 | Live trading parity | 2026-02-18 | 8 features for v1 parity, 662 tests |
| 8 | Kraken integration | 2026-02-18 | 3 new plugins, symbol mapper, 8th ABC (PairDiscovery) |
| 9 | v1 archived | 2026-02-18 | 109 files moved to archive/v1/, Coinbase paper stopped |
| 10 | Diagnostic framework | 2026-02-23 | MFE/MAE, random baseline, signal enrichment |
| 11 | Strategy optimization | 2026-02-27 | Trend gate, config sync, phantom fix |
| 12 | ATR hard stops + performance filter | 2026-03-10 | Dynamic stops, symbol exclusion, 672 tests |
| 13 | Exit strategy redesign Phase 1 | 2026-03-15 | Buy TTL, conditioned stale exit, zombie prevention |

## Current State (April 2026)

- **30 plugins** across 8 categories
- **672+ tests** passing
- **Production**: Kraken paper trading (v2-kraken container on AWS)
- **v1**: Fully archived, no containers running
- **Active monitoring**: Accumulating live trade data for out-of-sample validation

## Last Updated

2026-04-03
