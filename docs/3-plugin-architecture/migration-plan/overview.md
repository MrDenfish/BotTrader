# Plugin Architecture Migration

**Status**: Phases 1-4 complete. Paper trading deployed on AWS.

## Original Plan (2026-02-06)

Phased migration from monolithic architecture to plugin-based system with 4 motivations:

1. Decompose monolithic `strategy_4h_hybrid.py` (2,813 lines)
2. Independent testing of production and backtest strategies
3. A/B strategy comparison with consistent infrastructure
4. Extensibility — new strategies without modifying core engine

## What Was Actually Built

The implementation expanded well beyond the original 4-phase plan. Rather than wrapping v1 code in plugin interfaces, a full v2 system was built from scratch in `v2/` with 7 plugin categories, an event bus, auto-discovery registry, and multi-mode support.

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
| Unified backtest runner | `v2/core/app.py` backtest mode — synchronous bar-by-bar via `on_backtest_bar()` |
| Side-by-side comparison | `signal_comparison` observer + `scripts/compare_signals.py` CLI |
| Consistent fee modeling | Paper exchange with configurable maker_fee, taker_fee, slippage_bps |
| Test suite | 246 tests across 11 files |
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
| — | HODL gate + backlog fixes | 2026-02-08 | HODL gate, exit_reason persistence, security fixes |

## Remaining

- **Production cutover**: Run 24h+ parallel comparison of v1 vs v2 signals, then switch v2 to live
- **Runtime hot-swap**: Currently config-driven at startup only; runtime strategy switching not implemented
- **Circuit breaker**: `circuit_breaker.py` exists as a stub, not fully implemented

## Last Updated

2026-02-08
