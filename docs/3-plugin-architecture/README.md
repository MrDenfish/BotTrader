# Plugin Architecture

v2 plugin-based architecture for BotTrader. All code lives in `v2/` at the project root.

For the full system overview including signal pipeline, risk chain, and exit management,
see [SYSTEM_CONTEXT.md](../SYSTEM_CONTEXT.md).

## Architecture

v2 is an event-driven plugin system with **8 plugin categories**, a typed **EventBus** for pub/sub communication, and a **Registry** for auto-discovery. Plugins communicate exclusively through shared domain types — no plugin holds a direct reference to another.

```
v2/
├── core/                              # Framework
│   ├── types.py                       # Shared domain types (Candle, Signal, Order, Fill, etc.)
│   ├── event_bus.py                   # Typed pub/sub (publish, subscribe, subscribe_all)
│   ├── interfaces.py                  # 8 plugin ABCs
│   ├── registry.py                    # @plugin() decorator + auto-discovery
│   ├── config.py                      # YAML loading with env var interpolation
│   └── app.py                         # Lifecycle orchestration (startup → wiring → run → teardown)
│
├── plugins/                           # 30 concrete plugins
│   ├── exchanges/                     # ExchangeAdapter implementations
│   │   ├── coinbase.py                #   Coinbase Advanced Trade (JWT auth, REST + WS)
│   │   ├── kraken.py                  #   Kraken REST + WS v2 executions channel
│   │   ├── paper.py                   #   Simulated exchange with live ticker prices
│   │   ├── backtest.py                #   No-op exchange for backtest mode
│   │   └── backtest_fill_sim.py       #   Event-driven backtest fill simulator
│   ├── data/                          # DataProvider implementations
│   │   ├── websocket.py               #   Coinbase WS ticker → 1-min candle aggregation
│   │   ├── kraken_websocket.py        #   Kraken WS v2 ticker → 1-min candle aggregation
│   │   └── csv_replay.py              #   Historical CSV replay for backtesting
│   ├── strategies/                    # Strategy implementations
│   │   ├── composite_scoring/         #   PRODUCTION: multi-indicator scoring (8 families, 16 signals)
│   │   ├── hybrid_4h_maker/           #   Backtest: Donchian breakout + compression
│   │   └── random_entry/              #   Diagnostic: Poisson-distributed baseline
│   ├── risk/                          # RiskManager implementations
│   │   ├── basic.py                   #   Exposure limits, daily loss, HODL gate, fee hurdle
│   │   ├── exit_manager.py            #   Dynamic exits: hard/trailing stops, time limit, peak tracking
│   │   ├── performance_filter.py      #   Symbol exclusion based on rolling win rate / P&L
│   │   └── circuit_breaker.py         #   Drawdown protection (max losses in window)
│   ├── execution/                     # ExecutionManager implementations
│   │   ├── maker_only.py              #   Post-only limit orders with retries + buffer escalation
│   │   └── bracket.py                 #   Take-profit / stop-loss brackets
│   ├── persistence/                   # StorageAdapter implementations
│   │   ├── postgres.py                #   PostgreSQL (v2_fills, v2_orders, v2_positions, v2_state)
│   │   └── sqlite.py                  #   SQLite for backtest / local dev
│   ├── observability/                 # Observer implementations
│   │   ├── structured_log.py          #   JSON event logging
│   │   ├── signal_comparison.py       #   JSONL signal log for analysis
│   │   ├── heartbeat.py               #   Periodic health check file
│   │   ├── alerting.py                #   Alert generation on risk events
│   │   ├── daily_report.py            #   Legacy email report (simple)
│   │   ├── daily_report_v2/           #   PRODUCTION: modular report (7 collectors, HTML + Slack)
│   │   ├── backtest_diagnostics.py    #   MFE/MAE, regime, indicator snapshots per trade
│   │   └── backtest_results.py        #   Backtest summary statistics collector
│   └── pair_discovery/                # PairDiscovery implementations
│       ├── kraken.py                  #   PRODUCTION: Kraken API volume + spread filtering
│       ├── coinbase.py                #   Coinbase REST pair discovery
│       └── csv.py                     #   Static CSV-based pair list
│
├── utils/                             # Shared utilities
│   ├── credentials.py                 #   Credential loader (explicit > env > key_file)
│   ├── kraken_auth.py                 #   HMAC-SHA512 signature generation
│   └── symbol_mapper.py              #   Kraken symbol normalization (BTC ↔ XBT)
│
├── tests/                             # 672+ passing tests
├── kraken_paper_trading.yaml          # PRODUCTION config (Kraken paper trading)
├── config.yaml                        # Coinbase live config (dormant)
└── backtest_*.yaml                    # Backtest configurations
```

## Event Flow

```
                                                   SymbolsUpdatedEvent
PairDiscovery ─────────────────────────────────────────────┐
                                                           ▼
DataProvider ─── CandleEvent ──→ Strategy ─── SignalEvent ──→ RiskManager Chain
      │                                                         │
      └─── TickerEvent ──→ ExitManager (exit monitoring)   (approved or vetoed)
                                                                │
                                              ExecutionManager ←─┘
                                                    │
                                              ExchangeAdapter
                                                    │
                                               FillEvent ──→ StorageAdapter
                                                    │         Portfolio
                                     All Events ──→ Observer (subscribe_all)
```

## Quick Start

```bash
# List all discovered plugins
python -m v2 --list-plugins

# Paper trading on Kraken (production config)
python -m v2 --config v2/kraken_paper_trading.yaml

# Backtest with composite scoring
python -m v2 --config v2/backtest_composite.yaml

# Backtest with diagnostics (MFE/MAE, regime snapshots)
python -m v2 --config v2/backtest_diagnostic.yaml

# Run tests
python -m pytest v2/tests/ -v
```

## Contents

- **[design/](design/)** - Plugin interface contracts and type reference
  - [`plugin-interfaces.md`](design/plugin-interfaces.md) - All 8 ABCs + shared types + EventBus + Registry
- **[migration-plan/](migration-plan/)** - Migration history
  - [`overview.md`](migration-plan/overview.md) - Phase-by-phase record of what was built

## Key Design Decisions

1. **One job per plugin** — each ABC defines exactly one responsibility
2. **Event-driven decoupling** — plugins never reference each other; all communication goes through the EventBus
3. **Config-driven composition** — YAML selects which plugin to use per category; no code changes needed to swap implementations
4. **Multi-mode from one codebase** — live, paper, and backtest modes differ only in which plugins are configured
5. **Exchange-agnostic** — Coinbase and Kraken share the same strategy, risk, and execution plugins; only exchange, data, and pair discovery differ
6. **Risk chain** — multiple risk managers run in sequence; any one can veto a signal

## Status

**30 plugins across 8 categories.** v2 is deployed on AWS paper trading on Kraken (v2-kraken container). v1 was archived Feb 2026. 672+ tests passing. Backtest-live parity validated (33-trade exact match).

## Last Updated

2026-04-03
