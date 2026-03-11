# Plugin Architecture

v2 plugin-based architecture for BotTrader. All code lives in `v2/` at the project root.

## Architecture

v2 is an event-driven plugin system with 7 plugin categories, a typed EventBus for pub/sub communication, and a Registry for auto-discovery. Plugins communicate exclusively through shared domain types — no plugin holds a direct reference to another.

```
v2/
├── core/                          # Framework
│   ├── types.py                   # Shared domain types (Candle, Signal, Order, Fill, etc.)
│   ├── event_bus.py               # Typed pub/sub (publish, subscribe, subscribe_all)
│   ├── interfaces.py              # 7 plugin ABCs
│   ├── registry.py                # @plugin() decorator + auto-discovery
│   ├── config.py                  # YAML loading with env var interpolation
│   └── app.py                     # Lifecycle orchestration (startup → wiring → run → teardown)
│
├── plugins/                       # 15 concrete plugins
│   ├── exchanges/                 # ExchangeAdapter implementations
│   │   ├── coinbase.py            #   Live Coinbase Advanced Trade (JWT auth, REST + WebSocket)
│   │   ├── paper.py               #   Simulated exchange with live ticker prices
│   │   └── backtest.py            #   No-op exchange for backtest mode
│   ├── data/                      # DataProvider implementations
│   │   ├── websocket.py           #   Live WebSocket ticker → 1-minute candle aggregation
│   │   └── csv_replay.py          #   Historical CSV replay with indicator pre-computation
│   ├── strategies/                # Strategy implementations
│   │   ├── composite_scoring/     #   Production: multi-indicator scoring (from signal_manager.py)
│   │   └── hybrid_4h_maker/      #   Backtest: ROC momentum with 4H candles
│   ├── risk/                      # RiskManager implementations
│   │   ├── basic.py               #   Exposure limits, daily loss, HODL gate, position count
│   │   └── circuit_breaker.py     #   Drawdown monitoring (stub)
│   ├── execution/                 # ExecutionManager implementations
│   │   ├── maker_only.py          #   Post-only limit orders with retries + buffer escalation
│   │   └── bracket.py             #   Take-profit / stop-loss brackets
│   ├── persistence/               # StorageAdapter implementations
│   │   ├── postgres.py            #   PostgreSQL (v2_* tables, no collision with v1)
│   │   └── sqlite.py              #   SQLite for backtest / local dev
│   └── observability/             # Observer implementations
│       ├── structured_log.py      #   JSON event logging
│       ├── signal_comparison.py   #   JSONL signal log (for v1 vs v2 comparison)
│       ├── heartbeat.py           #   Periodic health check
│       ├── alerting.py            #   Alert generation on risk events
│       └── daily_report.py        #   Email P&L reports
│
├── tests/                         # 246 tests across 11 files
├── config.yaml                    # Live trading config
├── paper_trading.yaml             # Paper trading config (deployed on AWS)
├── backtest_composite.yaml        # Backtest with composite scoring strategy
└── backtest_4h.yaml               # Backtest with 4H hybrid strategy
```

## Event Flow

```
DataProvider ─── CandleEvent ──→ Strategy ─── SignalEvent ──→ RiskManager
                                                                  │
                                                          (approved or vetoed)
                                                                  │
                                              ExecutionManager ←──┘
                                                    │
                                              ExchangeAdapter
                                                    │
                                               FillEvent ──→ StorageAdapter
                                                    │
                                     All Events ──→ Observer (subscribe_all)
```

## Quick Start

```bash
# List all discovered plugins
python -m v2 --list-plugins

# Paper trading (live WebSocket data, simulated orders)
python -m v2 --config v2/paper_trading.yaml

# Backtest with composite scoring
python -m v2 --config v2/backtest_composite.yaml

# Backtest with 4H hybrid
python -m v2 --config v2/backtest_4h.yaml

# Run tests
python -m pytest v2/tests/ -v
```

## Contents

- **[design/](design/)** - Plugin interface contracts and type reference
  - [`plugin-interfaces.md`](design/plugin-interfaces.md) - All 7 ABCs + shared types + EventBus + Registry
- **[migration-plan/](migration-plan/)** - Migration history (all 4 phases complete)
  - [`overview.md`](migration-plan/overview.md) - Phase-by-phase record of what was built

## Key Design Decisions

1. **One job per plugin** — each ABC defines exactly one responsibility
2. **Event-driven decoupling** — plugins never reference each other; all communication goes through the EventBus
3. **Config-driven composition** — YAML selects which plugin to use per category; no code changes needed to swap implementations
4. **Multi-mode from one codebase** — live, paper, and backtest modes differ only in which plugins are configured
5. **v2/ is isolated** — v1 production code is untouched; v2 runs alongside it

## Status

**Milestone 6 complete.** v2 is deployed to AWS in paper trading mode alongside v1. 246 tests passing. Signal pipeline validated end-to-end (24 signals logged). Trade-for-trade backtest match confirmed (33 trades).

Remaining: 24h+ parallel signal comparison, then production cutover.

## Last Updated

2026-02-08
