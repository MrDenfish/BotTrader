# BotTrader — System Context (Living Document)

> **Audience:** The project owner, outside developers, and AI assistants.
> **How to use:** Read top-to-bottom for full context, or jump to a section. If you're an AI assistant starting a new conversation, this is your single-source briefing.
> **Keeping it current:** Update the [Changelog](#changelog) at the bottom whenever significant changes ship. Stale docs are worse than no docs.

---

## 1. Why This Exists

**The problem:** Cryptocurrency markets run 24/7 and move fast. Manually screening dozens of trading pairs, computing technical indicators, managing entries with favorable fees, and enforcing disciplined exits across volatile assets is unsustainable for a solo trader.

**What BotTrader does:** It automates the full lifecycle of crypto trading — from dynamic pair selection, through multi-indicator signal generation, to fee-aware order execution and multi-layer exit management. The system runs continuously on AWS inside Docker containers, trading on Kraken via WebSocket data and REST order submission.

**Core strategy:** A **composite scoring** engine evaluates 8 technical indicator families (Bollinger Bands, MACD, RSI, ROC, W-Bottom/M-Top, Swing, Volume Divergence) on 5-minute aggregated candles. When enough indicators align with trend confirmation, a buy signal fires. Positions are then managed by a sophisticated **exit manager** that layers hard stops, trailing stops, and time-based exits — all fee-aware.

**Target user:** The project owner — an active crypto trader who wants systematic, disciplined execution with full observability.

**What it is NOT:** Not a high-frequency trading system. Not a market maker. The strategy is swing/momentum-oriented, holding positions for minutes to hours (up to 48h max). All orders are post-only LIMIT orders to capture maker fees.

---

## 2. Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                   EC2 (t3.small, Ubuntu)                      │
│                                                               │
│  ┌───────────────┐     ┌──────────────────────────────────┐  │
│  │  PostgreSQL 16 │     │          v2-kraken               │  │
│  │  (db)          │◄────│  ┌────────┐  ┌───────────────┐  │  │
│  │  port 5432     │     │  │Kraken  │  │  Composite    │  │  │
│  │                │     │  │WS v2   │──│  Scoring +    │  │  │
│  │  v2_fills      │     │  │(ticker)│  │  Exit Manager │  │  │
│  │  v2_orders     │     │  └────────┘  └───────┬───────┘  │  │
│  │  v2_positions  │     │                      │          │  │
│  │  v2_state      │     │  ┌───────────────────▼───────┐  │  │
│  └───────┬───────┘     │  │  Maker-Only Execution     │  │  │
│          │              │  │  (post-only LIMIT orders) │  │  │
│          │              │  └──────────┬────────────────┘  │  │
│          │              │             │ REST API           │  │
│          │              │  ┌──────────▼────────────────┐  │  │
│          │              │  │  Paper Exchange (sim)     │  │  │
│          │              │  │  or Kraken REST (live)    │  │  │
│          │              │  └──────────────────────────┘  │  │
│          │              └──────────────────────────────────┘  │
│          │                                                    │
│          │              ┌──────────────────────────────────┐  │
│          └──────────────│        dashboard                 │  │
│                         │  Streamlit (port 8501)           │  │
│                         │  Report · Edge Analysis          │  │
│                         │  Reads DB + Kraken public REST   │  │
│                         └──────────────┬───────────────────┘  │
│                                        │                      │
│                         ┌──────────────▼───────────────────┐  │
│                         │        caddy                     │  │
│                         │  Reverse proxy :443 (HTTPS)      │  │
│                         │  Let's Encrypt + Basic Auth      │  │
│                         └──────────────────────────────────┘  │
│                                                               │
│  Public:  https://bottrader.trade (Basic Auth)                │
│  Fallback: ssh -L 8501:localhost:8501 bottrader-aws           │
└──────────────────────────────────────────────────────────────┘
```

**Stack:** Python 3.11 | PostgreSQL 16 | asyncio | WebSocket (Kraken v2) | Docker Compose | AWS EC2 | Streamlit 1.56 | Caddy 2

**Four Docker services** defined in `docker-compose.aws.yml`:
- **db** — PostgreSQL 16, persistent named volume (`bottrader-aws_pg_data`)
- **v2-kraken** — Paper trading bot (code baked into image via `COPY . /app`, 512MB limit)
- **dashboard** — Streamlit web UI (256MB limit, `127.0.0.1:8501`, SSH tunnel fallback)
- **caddy** — Reverse proxy (64MB limit, ports 80+443, auto Let's Encrypt TLS, Basic Auth)

**Current mode:** Paper trading on Kraken (simulated fills, live market data). The system is exchange-agnostic — Coinbase plugins also exist but are dormant (fees too high).

---

## 3. Plugin Architecture

BotTrader v2 is built on an **event-driven plugin system**. Every component is a plugin that implements one of 8 abstract base classes (ABCs). Plugins never hold direct references to each other — all communication flows through a typed **EventBus**.

### The 8 Plugin Categories

| Category | ABC | Job | Active Plugin(s) |
|----------|-----|-----|-------------------|
| **Exchange** | `ExchangeAdapter` | Order management, balances, fees | `paper` (sim), `kraken` (live), `coinbase`, `backtest`, `backtest_sim` |
| **Data** | `DataProvider` | Market data streaming | `kraken_websocket`, `websocket` (Coinbase), `csv_replay` |
| **Strategy** | `Strategy` | Signal generation from candles | `composite_scoring`, `hybrid_4h_maker`, `random_entry` |
| **Risk** | `RiskManager` | Signal validation and exit management | `basic`, `exit_manager`, `circuit_breaker`, `performance_filter` |
| **Execution** | `ExecutionManager` | Signal → order translation | `maker_only`, `bracket` |
| **Storage** | `StorageAdapter` | Persistence (fills, orders, state) | `postgres`, `sqlite` |
| **Observer** | `Observer` | Logging, metrics, reports, alerts | `structured_log`, `signal_comparison`, `heartbeat`, `daily_report_v2`, `backtest_results`, `backtest_diagnostics`, `daily_report`, `alerting` |
| **Pair Discovery** | `PairDiscovery` | Dynamic symbol selection | `kraken`, `coinbase`, `csv` |

**31 plugins total** across 8 categories (includes backtest and development plugins). `mean_reversion_v3` was added April 2026 as a clean-redesign alternative; backtest validation showed it inferior to `composite_scoring` and it remains available but not deployed.

### How Plugins Are Registered

Plugins self-register via decorator:
```python
@registry.plugin("strategy", "composite_scoring")
class CompositeScoringStrategy(Strategy):
    ...
```

On startup, `registry.discover_plugins()` walks all modules under `v2.plugins.*` (skipping `__init__.py` and `base.py`), importing them so decorators execute. The app then instantiates plugins by name from the YAML config.

### Event Types

All inter-plugin communication uses frozen dataclasses:

| Event | Published By | Consumed By |
|-------|-------------|-------------|
| `CandleEvent` | Data providers | Strategies, risk managers (ATR) |
| `TickerEvent` | Data providers | Strategies, exit manager, execution (stale orders) |
| `SignalEvent` | Strategies | Risk chain → execution pipeline |
| `FillEvent` | Exchange adapters | Risk managers, execution, storage, portfolio, observers |
| `OrderEvent` | Execution | Risk managers (rejection tracking), execution (stale tracking) |
| `RiskEvent` | Risk managers | Strategies (loss lockout), observers |
| `SymbolsUpdatedEvent` | Pair discovery | Data providers (subscribe/unsubscribe) |

---

## 4. Codebase Map

```
BotTrader/
├── v2/                              # ── All v2 code ──
│   ├── core/
│   │   ├── app.py                   #   Application lifecycle (setup → run → teardown)
│   │   ├── types.py                 #   Shared domain types (Candle, Signal, Fill, Order, etc.)
│   │   ├── event_bus.py             #   Typed pub/sub event system
│   │   ├── interfaces.py            #   8 plugin ABCs
│   │   ├── registry.py              #   Plugin discovery and factory
│   │   └── config.py                #   Layered config loader (YAML → env → overrides)
│   ├── plugins/
│   │   ├── exchanges/               #   paper, coinbase, kraken, backtest, backtest_sim
│   │   ├── data/                    #   kraken_websocket, websocket (Coinbase), csv_replay
│   │   ├── strategies/
│   │   │   └── composite_scoring/   #   PRIMARY: config, indicators, scoring, guardrails, strategy
│   │   ├── risk/                    #   basic, exit_manager, circuit_breaker, performance_filter
│   │   ├── execution/               #   maker_only (post-only LIMIT orders)
│   │   ├── persistence/             #   postgres, sqlite
│   │   ├── observability/           #   structured_log, signal_comparison, heartbeat, daily_report_v2
│   │   └── pair_discovery/          #   kraken, coinbase, csv
│   ├── utils/
│   │   ├── credentials.py           #   Shared credential loader (explicit > env > key_file)
│   │   ├── kraken_auth.py           #   HMAC-SHA512 signature generation
│   │   └── symbol_mapper.py         #   Kraken symbol normalization (BTC ↔ XBT)
│   ├── dashboard/
│   │   ├── app.py                   #   Streamlit entry point + page navigation (6 pages)
│   │   ├── db.py                    #   asyncpg pool + async-to-sync bridge
│   │   ├── prices.py                #   Kraken public REST ticker (live unrealized P&L)
│   │   ├── trades.py                #   FIFO round-trip matcher (links buy metadata → sell outcomes)
│   │   ├── ai_summary.py            #   Claude API executive summary generation
│   │   ├── strategy_probe.py        #   Runs composite_scoring against fresh REST data (Bot Health idle-vs-broken)
│   │   └── pages/                   #   report, edge_analysis, entry_quality, executive_summary, health, config_editor (stub)
│   ├── tests/                       #   685+ passing tests
│   ├── kraken_paper_trading.yaml    #   PRODUCTION config (paper trading on Kraken)
│   ├── config.yaml                  #   Coinbase live config (dormant)
│   └── backtest_*.yaml              #   Backtest configurations
├── backtest/                        # ── Backtest framework ──
│   ├── engine.py                    #   Event-driven backtest simulator
│   ├── data/                        #   Historical 1-min OHLCV CSVs per symbol
│   └── diagnostic_output/           #   Trade-level analysis (MFE/MAE, regime, indicators)
├── strategies/                      #   Phase 1 plugin code (v2 imports — do NOT archive)
├── Config/                          #   Exchange API keys only (gitignored): kraken_api_info.json, websocket_api_info.json. All v1-era .py and JSON files archived in Pass 4 (2026-05-12).
├── scripts/                         #   Diagnostics, analysis, deployment helpers
├── docs/                            #   Organized documentation (see Section 14)
├── archive/v1/                      #   All v1 code (archived Feb 2026, history preserved)
├── archive/v1-libs/                 #   v1-era root libraries + their consumer scripts (archived May 2026, Pass 3)
├── archive/experiments/             #   Completed experiment artifacts — each subdir has README, configs, outputs
├── docker/
│   ├── Dockerfile.v2                #   v2 image (requirements cached, code COPYed)
│   ├── Dockerfile.dashboard         #   Dashboard image (Streamlit + asyncpg + plotly)
│   ├── Dockerfile.caddy             #   Caddy reverse proxy (2 lines — copies Caddyfile)
│   ├── Caddyfile                    #   Caddy config (HTTPS + Basic Auth + reverse proxy)
│   └── entrypoint/                  #   Container startup scripts
├── docker-compose.aws.yml           #   Production Docker Compose (4 services: db, v2-kraken, dashboard, caddy)
└── CLAUDE.md                        #   AI assistant instructions (deployment, project structure)
```

**Critical conventions:**
- **`strategies/`** is imported by v2 — do NOT archive it.
- **`backtest/`** config is imported by v2 — the directories are coupled.
- **`archive/v1/`** is frozen — all v1 code was moved here Feb 2026 via `git mv`.
- **`archive/v1-libs/`** is frozen — v1-era root libraries (`Shared_Utils/`, `SharedDataManager/`, `TableModels/`, `database/`, `database_manager/`, `fifo_engine/`, `utils/`, `data/`) and their consumer scripts were moved here May 2026 via `git mv` (Pass 3 audit). See `archive/v1-libs/README.md`.

---

## 5. How a Trade Happens (Signal Pipeline)

This is the core flow — from raw market data to an executed order:

```
Kraken WebSocket          Data Provider              Strategy
    ticker ──────────► aggregate to ──────────► compute indicators
    (real-time)         5-min candles              on rolling buffer
                         (CandleEvent)                    │
                                                          ▼
                                                   weighted scoring
                                                   (8 indicator families)
                                                          │
                                                          ▼
                                                   guardrails check
                                                   (hysteresis, cooldown,
                                                    loss lockout, trend gate,
                                                    ADX gate, volume gate)
                                                          │
                                              ┌───────────▼───────────┐
                                              │     SignalEvent       │
                                              │  (BUY/SELL + metadata)│
                                              └───────────┬───────────┘
                                                          │
                               ┌──────────────────────────▼──────────────────────────┐
                               │              Risk Manager Chain                      │
                               │  1. BasicRiskManager  — exposure, daily loss, HODL   │
                               │  2. PerformanceFilter — symbol exclusion             │
                               │  3. ExitManager       — trailing/hard stop override  │
                               │  4. CircuitBreaker    — drawdown protection          │
                               │  Each can VETO (return None) or APPROVE              │
                               └──────────────────────────┬──────────────────────────┘
                                                          │ approved
                                                          ▼
                                                  ExecutionManager
                                                  (maker_only)
                                                  ├─ fetch bid/ask
                                                  ├─ compute size from trigger
                                                  ├─ price = ask × (1 - buffer)
                                                  └─ submit post-only LIMIT
                                                          │
                                                          ▼
                                                    FillEvent
                                                  ├─ update portfolio
                                                  ├─ persist to DB
                                                  ├─ notify exit manager
                                                  └─ notify observers
```

### Exit Manager (Parallel Path)

The exit manager subscribes to `TickerEvent` independently and monitors all open positions on every price tick:

| Exit Layer | Condition | Order Type | Priority |
|-----------|-----------|------------|----------|
| **Hard stop** | Loss exceeds 5.5% (ATR-based, floor 5.5%, ceiling 8%) | MARKET | 1 (highest) |
| **Breakeven stop** *(opt-in)* | After unrealized P&L hits `breakeven_trigger_pct`, exit when P&L returns to ≤ 0 | MARKET | 1.5 |
| **Trailing stop** | Activates at +2% profit, trails by ATR distance [1%-2%] | MARKET | 2 |
| **Time limit** | Score-triggered positions held > 48 hours; if `stale_exit_regardless_of_pnl=true`, exits regardless of P&L | LIMIT/MARKET | 3 |
| **Fixed take-profit** *(opt-in)* | Exit at MARKET when unrealized P&L ≥ `fixed_take_profit_pct` | MARKET | 3.5 |
| **Signal exit** | Sell signal from strategy + P&L ≥ 0% | LIMIT | 4 |
| **Peak tracking** | For momentum trades: +6% activation, -5% drawdown exit, 24h max | MARKET | 2 |

The four "opt-in" rows above (breakeven_trigger_pct, fixed_take_profit_pct, trailing_enabled, stale_exit_regardless_of_pnl) were added April 2026 during the v3 experiment. All default to off — no behavior change in production. Available to any future strategy via YAML config.

**Note:** All stop-type exits (hard, trailing, peak) use MARKET orders to ensure immediate fill. Only signal-based and time-limit exits use LIMIT.

All P&L calculations are **fee-aware**: entry cost = `avg_entry × (1 + maker_fee)`, exit revenue = `price × (1 - taker_fee)`.

---

## 6. The Composite Scoring Strategy

### Indicator Set (8 families, 16 signals)

| Indicator | Buy Signal | Sell Signal | Weight |
|-----------|-----------|-------------|--------|
| **Bollinger Bands (Ratio)** | Band compression (ratio < buy_ratio) | Band expansion (ratio > sell_ratio) | 1.2 |
| **Bollinger Bands (Touch)** | Price touches lower band | Price touches upper band | 1.5 |
| **W-Bottom / M-Top** | Double bottom pattern detected | Double top pattern detected | 2.0 |
| **RSI** | RSI < 25 (oversold) | RSI > 75 (overbought) | 1.5 |
| **ROC** | Rate of change > +2.0 | Rate of change < -1.0 | 2.0 |
| **MACD** | Histogram crossover (bearish → bullish) | Histogram crossover (bullish → bearish) | 1.8 |
| **Swing** | Swing low detected (shift 1 bar) | Swing high detected (shift 1 bar) | 2.2 |
| **Volume Divergence** | Price falling + volume declining (exhaustion) | Price rising + volume declining (fading) | 1.5 |

### Scoring Mechanics

Each indicator returns `(decision, value, threshold)`. When `decision == 1`, its weight is added to the buy or sell score. A **BUY** signal fires when `buy_score >= 2.0` (the target), and similarly for SELL.

### Guardrails (Applied After Scoring)

| Guardrail | What It Does |
|-----------|-------------|
| **Min indicators** | Require ≥3 buy indicators firing (prevents thin signals) |
| **Trend confirmation** | Require ≥1 trend indicator (MACD, ROC, or Swing) — prevents "falling knife" buys from counter-trend signals (Touch + RSI + Volume Div) |
| **ADX gate** | Suppress buys when ADX < 20 (no meaningful trend) |
| **Volume confirmation** | Suppress buys when RVOL < 0.7 (below-average volume) |
| **Regime filter** | Skip buys during downtrends (ATR percentile > 60th) |
| **Hysteresis** | After a buy, require +10% extra score to flip to sell (prevents chatter) |
| **Cooldown** | 2-bar delay between opposite-side signals |
| **Loss lockout** | Block re-entry for 12-24 bars after a stop-loss exit (prevents death spiral) |
| **Red-day gate** | Optionally block buys when 24h price change < 0 |

### Momentum Paths (Currently Disabled)

Two bypass paths (`roc_momo_20m` and `roc_momo_24h`) can trigger buys based on short-term ROC momentum independent of composite scoring. Both are **disabled** in production due to excessive hard stop rates (28-35%).

---

## 7. Risk Management

### Risk Manager Chain

Signals pass through up to 4 risk managers in sequence. Any one can veto:

**1. BasicRiskManager** — Pre-execution limits
- Max exposure per symbol, max open positions, max daily loss
- Fee hurdle gate: veto buys if round-trip fees > 1.0%
- HODL gate: block all execution for listed symbols
- FIFO protection: no buying if already holding the symbol

**2. PerformanceFilter** — Symbol exclusion
- Tracks per-symbol performance over a rolling 30-day window
- Excludes symbols with: win rate < 30%, avg P&L < -$5, total P&L < -$50

**3. ExitManager** — Dynamic exits (see Section 5)
- Also acts as a risk manager in the chain: approves sell signals it generates, vetos conflicting signals

**4. CircuitBreaker** — Drawdown protection
- Trips on: 5 losses in 30 minutes, or a single loss > $50
- 30-minute cooldown after tripping (blocks all new signals)

---

## 8. Execution

The `maker_only` execution manager handles signal-to-order translation:

| Feature | Detail |
|---------|--------|
| **Order type** | Post-only LIMIT (captures maker fees) |
| **Pricing** | BUY: `ask × (1 - buffer)`, SELL: `bid × (1 + buffer)` |
| **Buffer** | Starts at 0.1%, +0.05% per retry (max 3 retries) |
| **Sizing** | Trigger-based: `score: $75`, `score_high: $150` (4+ indicators), `roc_momo_20m: $30`, `roc_momo_24h: $30` (both disabled) |
| **Min order** | Skip dust orders below $10 notional |
| **Stale cancellation** | Cancel if price drifts > 0.5% from order price |
| **Buy TTL** | Hard cancel unfilled buys after 10 minutes |
| **Emergency orders** | Hard stops always use MARKET orders |

---

## 9. Configuration

All configuration lives in YAML files. The production config is `v2/kraken_paper_trading.yaml`.

### Config Loading Priority
`CLI overrides → environment variables → YAML file → dataclass defaults`

Keys ending in `_env` are expanded from environment variables (e.g., `dsn_env: "DATABASE_URL"` becomes the value of `$DATABASE_URL`).

### YAML Structure (Abridged)

```yaml
app:
  mode: "paper"                     # paper | live | backtest
  symbols: ["BTC-USD", ...]         # fallback if pair discovery fails

pair_discovery:
  type: "kraken"
  config:
    min_quote_volume: 2000000       # $2M minimum 24h volume
    max_spread_bps: 100             # reject pairs with spread > 1.0%
    max_pairs: 30

exchange:
  type: "paper"
  maker_fee: 0.0025                 # Kraken: 0.25% maker
  taker_fee: 0.004                  # Kraken: 0.40% taker

strategies:
  - type: "composite_scoring"
    config:
      candle_interval_minutes: 5    # aggregate 1-min → 5-min candles
      score_buy_target: 2.0         # weighted score threshold
      min_indicators_required: 3    # confirmation gate
      # ... 40+ tunable parameters (see config.py for full list)

risk:
  - type: "basic"                   # exposure limits, fee hurdle
  - type: "performance_filter"      # symbol exclusion
  - type: "exit_manager"            # hard/trailing stops, time limit
  - type: "circuit_breaker"         # drawdown protection

execution:
  type: "maker_only"
  default_notional: 75              # USD per trade
  buy_order_ttl_seconds: 600        # 10-min TTL for unfilled buys

storage:
  type: "postgres"

observers:
  - type: "structured_log"
  - type: "signal_comparison"       # JSONL signal log
  - type: "heartbeat"               # healthcheck file
  - type: "daily_report_v2"         # email + Slack reports
```

### Environment Variables (Production)

```bash
DATABASE_URL=postgresql://bot_user:...@db:5432/bot_trader_db
SMTP_USERNAME=...            # SES SMTP credentials
SMTP_PASSWORD=...            # SES SMTP credentials
LOG_LEVEL=INFO
V2_CONFIG=/app/v2/kraken_paper_trading.yaml
```

API keys are stored in `Config/kraken_api_info.json` (gitignored). The `.gitignore` also globs `Config/*_api_info.json`, `Config/*.key`, and `Config/*.pem` to catch future credential drops in that directory (added 2026-06-03 after a public-repo audit). DB credentials flow only through environment: Docker Compose interpolates `${DB_PASSWORD}` into the consumer containers' `DATABASE_URL`, and any laptop-side script reads it via `os.environ['DB_PASSWORD']` — never hardcode credentials in tracked scripts.

---

## 10. Database Schema

### Key Tables

| Table | Purpose | Key Columns |
|-------|---------|-------------|
| `v2_fills` | Every executed trade | symbol, side, price, qty, fee, timestamp, metadata (JSON — includes `signal_reason` for exit tracking) |
| `v2_orders` | Submitted orders | symbol, side, order_type, price, qty, status |
| `v2_positions` | Current position state | symbol, qty, avg_entry_price, cost_basis |
| `v2_state` | Key-value state persistence | key, state (JSON) — guardrail state, strategy state survive restarts |

**Caveat:** `v2_positions.avg_entry_price` and `cost_basis` may be stale after partial sells — the exit manager uses in-memory portfolio (correct), not DB values.

**Caveat:** `v2_orders.status` only ever takes values `open` or `cancelled`. The Kraken exchange (`v2/plugins/exchanges/kraken.py:587`) emits a `FillEvent` when an order fills but never re-emits an `OrderEvent(status=FILLED)`, so fully-filled orders stay at `open` in the DB. `v2_fills` is the source of truth for executed trades. Dashboards and queries should derive true status by left-joining `v2_fills` on `order_id` and comparing `SUM(fills.qty)` against `orders.qty`. Persistence fix is on the backlog, deferred until after the July 2026 live-data review (P3 in `open_work_items.md`).

---

## 11. Deployment

### Production Location
- **Server:** AWS EC2 t3.small (20GB disk, 2GB RAM; downsized from t3.medium 2026-08-12)
- **Path:** `/opt/bot` (git repository)
- **SSH alias:** `bottrader-aws`

### Docker Services

| Service | Container | Memory Limit | Port | Purpose |
|---------|-----------|-------------|------|---------|
| db | `db` | unlimited | 127.0.0.1:5432 | PostgreSQL 16, persistent volume |
| v2-kraken | `v2-kraken` | 512MB | none | Paper trading bot |
| dashboard | `dashboard` | 256MB | 127.0.0.1:8501 | Streamlit web UI (SSH tunnel) |

### Deployment Workflow

```bash
# 1. Push to GitHub
git push origin main

# 2. Pull on AWS
ssh bottrader-aws "cd /opt/bot && git pull origin main"

# 3. Rebuild and restart (--build is REQUIRED — code is baked into image)
ssh bottrader-aws "cd /opt/bot && docker compose -f docker-compose.aws.yml up -d --build v2-kraken"

# 4. Rebuild dashboard (only if dashboard code changed)
ssh bottrader-aws "cd /opt/bot && docker compose -f docker-compose.aws.yml up -d --build dashboard"

# 5. Verify
ssh bottrader-aws "cd /opt/bot && git log --oneline -3"
```

**Critical:** `docker compose restart` does NOT pick up code changes. The Dockerfile uses `COPY . /app`, so `--build` is always required. This was a real bug (2026-02-15) where 4 days of changes were not deployed.

**Disk space:** `.dockerignore` excludes `.git/`, `logs/`, `backtest/data/`, `archive/`, `docs/` from build context (~2.4 GB → ~50 MB). Backtest CSVs and v1 logs have been removed from the server. If builds still fail with "no space left on device", run `docker builder prune --all -f && docker image prune -f`.

### Health Monitoring
- **Heartbeat file:** `/app/logs/v2_kraken/heartbeat` (updated every 10s)
- **Docker healthcheck:** Verifies heartbeat file is < 5 minutes old
- **Email reports:** Every 4 hours via SES SMTP to `dennfish@gmail.com`
- **Dashboard:** `ssh -L 8501:localhost:8501 bottrader-aws -N` then open `http://localhost:8501`

---

## 12. Pair Discovery

The Kraken pair discovery plugin dynamically selects which trading pairs to trade:

1. **Fetch** all USD-quoted pairs from Kraken public API
2. **Filter by volume:** 24h quote volume ≥ $2M
3. **Filter by spread:** Bid-ask spread ≤ 100 bps (1.0%)
4. **Filter by listing age** (added 2026-07-31): pairs listed on Kraken < `min_listing_age_days` ago are dropped (paper config: 365). Listing date is approximated by the pair's first weekly OHLC bar and cached for the process lifetime. Newly listed pairs trade on thin, market-maker-driven books that gap through stop levels — this is a universe-hygiene screen, not an edge claim. Pairs whose listing date can't be determined are kept (graceful degradation).
5. **Exclude shill coins:** Configurable blocklist (e.g., TRUMP)
6. **Cap at 30 pairs**, sorted by volume
7. **Seed symbols guaranteed:** BTC-USD and ETH-USD always included (seeds also bypass the listing-age gate)
8. **Refresh every 30 minutes** — publishes `SymbolsUpdatedEvent` on changes

Position symbols are always preserved — if pair discovery drops a symbol you're holding, it stays in the active set until the position is closed.

---

## 13. Observability

### Streamlit Dashboard (deployed 2026-04-12)

Web-based monitoring and diagnostics, replacing the 4-hour email reports for on-demand access. Runs as the `dashboard` Docker container, accessed via SSH tunnel on port 8501.

**Page 1 — Performance Report:**
- Hero P&L metrics (net, gross, fees, win rate, best/worst trade)
- Portfolio value (all-time realized + live unrealized from Kraken public REST)
- P&L by symbol, exit reason breakdown, open positions with live prices, trade log
- Date range selector (Today / 7d / 30d / All time / Custom)

**Page 2 — Edge Analysis:**
- Weekly gross vs net P&L trend (fee drag visualization)
- Exit reason P&L trend (stacked bar by week)
- Hard stop rate trend vs 22% backtest baseline
- Avg trade metrics table by exit reason
- P&L distribution histogram ($0.50 bins)
- Peak capture scatter for trailing stops (MFE vs realized P&L)
- Trigger filter: defaults to `score`/`score_high` only (roc_momo excluded, available via opt-in)

**Page 3 — Entry Quality:**
- Win rate by symbol (sortable table)
- Score vs outcome scatter (buy_score vs net P&L, colored by exit reason, sized by indicator count)
- Indicator hit rate: winners vs losers (paired horizontal bars + delta table)
- Indicator combination performance (combo string, WR, avg P&L, min 3 trades)
- Entry condition scatters (ADX, RVOL, ATR percentile vs net P&L)
- Time-of-day heatmap (hour x day of week, with low sample size warning)
- Same trigger filter as Edge Analysis

**Page 4 — Executive Summary (AI-generated):**
- Feeds structured metrics + backtest baselines to Claude Sonnet 4.6 via Anthropic API
- Generates 6-10 sentence interpretive summary comparing live performance to backtest expectations
- Highlights trends (improving/stable/degrading), anomalies, and actionable recommendations
- Cached 5 minutes, manual regenerate button available
- Requires `ANTHROPIC_API_KEY` in `.env`
- Model upgraded from `claude-sonnet-4-20250514` → `claude-sonnet-4-6` on 2026-05-16 (Sonnet 4 retired by Anthropic on 2026-06-15)

**Page 5 — Bot Health (deployed 2026-05-16, idle-vs-broken upgrade 2026-05-29):**
- Status: 3-state pill (🟢 Active / 🟡 Idle / 🔴 Stale). Active = DB write within 60 min. Beyond that, a live strategy probe disambiguates: all symbols blocked by guardrails → Idle (bot is doing its job); ≥1 symbol would pass every gate → Stale (bot should have fired, investigate). Graceful fallback to the old freshness bands if the probe is unreachable.
- "Why is the bot idle?" panel: runs `compute_indicators` + `compute_scores` from the live strategy code against fresh Kraken public OHLC (cached 5 min) for the recently-active symbol set. Renders 5 per-gate metric cards (Indicator strength, Indicator count, Trend strength, Volume, Volatility regime) showing how many of the probed symbols pass each gate, plus a per-symbol table with friendly column headers and hover tooltips containing the technical names and thresholds. Sorts would-fire symbols to the top.
- Recent Activity: last order / fill / cancel timestamps with ages, plus 24h / 7d order counts and avg orders/day
- Daily Order Submissions (30d): stacked bar of buy / sell / cancelled submissions per day — measures signal-generation rate
- Active Symbols (7d): per-symbol fill counts and last-fill timestamps, sourced from `v2_fills` (source of truth)
- Equity Curve: cumulative realized net P&L over time from FIFO round-trips, plus realized P&L all-time, round-trip count, and drawdown-from-peak
- Recent Orders: last 20 orders with derived status (`filled` / `open` / `cancelled` / `partial` / `partial+cancelled`) via LEFT JOIN to `v2_fills`
- **Caveat on the probe**: reads REST OHLC, not the bot's live in-memory candle buffer — these can diverge intra-bar or right after symbol churn. Sufficient for "is the regime buy-able?" question; an in-process snapshot observer to a `v2_strategy_snapshots` table is held as a post-July follow-up.
- **Not yet surfaced**: container up/down (dashboard has no Docker socket), risk events (vetoes / circuit-breaker trips — `RiskEventAccumulator` is in-process only, needs a `v2_risk_events` table)

**Technical details:**
- Reuses existing async collectors from `daily_report_v2/collectors/` (pnl, trade_log, positions, trade_stats)
- `trades.py` provides FIFO round-trip matching with full buy/sell metadata extraction
- `prices.py` fetches live prices from Kraken public Ticker API (AssetPairs mapping cached 1h)
- `ai_summary.py` computes metrics, compares to backtest baselines, calls Claude API
- `db.py` bridges async asyncpg to synchronous Streamlit via module-level event loop
- `@st.cache_data(ttl=60)` on all queries — at most one DB round trip per minute
- `daily_report_v2/__init__.py` has try/except guard so dashboard imports collectors without needing aiohttp/jinja2

### Daily Report (every 4 hours)

The `daily_report_v2` observer generates HTML reports with 5 sections:
1. **Hero P&L** — Portfolio value, cash, unrealized, period return
2. **P&L by Symbol** — Per-symbol realized P&L (FIFO accounting)
3. **Open Positions** — Current holdings with unrealized P&L
4. **System Health** — Exit stats (hard/soft/trailing counts), signal rates
5. **v1/v2 Comparison** — Side-by-side (disabled since v1 shutdown)

Delivered via SMTP (SES) and optionally Slack webhook. Running in parallel with the dashboard during validation. Will be retired once the dashboard is confirmed to match email report data.

### Alerting Observer

Real-time email/SMS alerts for critical events: circuit breaker trips, signal vetoes, order rejections, large fills. Rate-limited (5-minute minimum between alerts). Independent from the daily report — not affected by dashboard migration.

### Signal Logging

All signals are logged to JSONL at `/app/logs/v2_kraken_score_log.jsonl` with full indicator snapshots, score components, and metadata. This is the primary data source for post-hoc analysis.

### Structured Logging

JSON-formatted logs with event type, symbol, and metadata. Docker log driver caps at 50MB × 3 files.

---

## 14. Backtesting & Diagnostics (Summary)

### Backtest Framework

The backtest engine (`backtest/engine.py`) replays historical 1-minute OHLCV data through the full v2 pipeline — same strategy, risk chain, and exit manager as live trading. This ensures **backtest-live parity** (validated via trade-for-trade matching: 33 trades, 100% match).

**Data:** 180-day, 1-minute CSVs for 9 symbols (BTC, ETH, SOL, XRP, DOGE, ADA, LINK, DOT, LTC).

### Diagnostic Tooling

The `backtest_diagnostics` observer captures per-trade:
- **MFE/MAE** — Maximum favorable/adverse excursion
- **Post-exit tracking** — What happened 60 bars after the trade closed
- **Market regime** — SMA slope + ATR percentile at entry
- **Indicator snapshot** — Which indicators fired and their values

A `random_entry` strategy (Poisson-distributed baseline) provides a control group for comparing real strategy edge vs. exit manager luck.

### Key Findings

| Metric | Pre-Trend-Gate (139 trades) | Post-Trend-Gate (79 trades) |
|--------|----------------------------|----------------------------|
| Hard stops | 18 (12.9%) | 9 (11.4%) |
| Trailing stops | 40 (28.8%) | 36 (45.6%) |
| Win rate | ~20% | ~30% |

The trend confirmation gate (Feb 27) eliminated the "falling knife" pattern where Touch + RSI + Volume Div fired on oversold signals without momentum confirmation — responsible for 12/18 hard stops.

### Overfitting Awareness

Five strategy changes were derived from the same 180-day, 9-symbol dataset: trend gate, ATR hard stops, peak tracking tuning, volume features, and regime filter.

**Out-of-sample validation completed (2026-04-09):** Three independent 9-symbol sets (27 symbols total, zero overlap) tested over the same 180-day period:

| Set | Symbols | Trades | Win Rate | Gross P&L | Fees | Net P&L | Profit Factor |
|-----|---------|--------|----------|-----------|------|---------|---------------|
| **A (training)** | BTC, ETH, SOL, XRP, DOGE, ADA, LINK, DOT, AVAX | 103 | 54.4% | -$12 | $51 | -$62 | 0.57 |
| **B (OOS)** | AAVE, APT, ATOM, BNB, FIL, ICP, LTC, NEAR, UNI | 113 | 64.6% | +$35 | $56 | -$22 | 0.85 |
| **C (OOS)** | ALGO, ARB, FET, HBAR, INJ, LDO, OP, SUI, TAO | 99 | 61.6% | +$16 | $49 | -$33 | 0.77 |

**Verdict: NOT overfit.** OOS sets outperformed training data on all metrics. Exit distribution is consistent across all sets (~60% trailing, ~25% hard stop, ~15% stale). The strategy logic is robust. See `CLAUDE.md` memory for the full overfitting policy.

### Fee/Sizing & Hard Stop Analysis (2026-04-11)

**Fee analysis** (branch `fee-sizing-analysis`, script `analysis/fee_sizing/fee_sizing_analysis.py`): PF is constant at 0.73 across all notional levels — percentage-based fees scale proportionally. Avg gross return +0.187%/trade cannot cover 0.650% RT fee. Kelly fraction negative (-0.21). No Kraken fee tier reaches breakeven (need RT < 0.19%, lowest available is 0.30%).

**Exit reason is the real story:**

| Exit Reason | Trades | Net P&L at $75 | Avg Net | Win Rate |
|-------------|--------|-----------------|---------|----------|
| trailing_stop | 172 (53.8%) | +$290.56 | +$1.69 | 100% |
| hard_stop | 72 (22.5%) | -$312.73 | -$4.34 | 0% |
| stale_exit | 53 (16.6%) | -$49.42 | -$0.93 | 24.5% |
| roc_momo_20m | 22 (6.9%) | -$38.61 | -$1.76 | 0% |

**Hard stop reduction backtests:** Regime filtering (ATR pctile 60→40) and ADX sweep (20/25/30) both improve P&L by reducing total trade count, but hard stop % stays locked at ~21% — neither filter discriminates winners from losers at entry time.

**Hard stop price path analysis** (`analysis/fee_sizing/hard_stop_path_check.py`): 83% of hard stops exit within 0.2% of the trade's absolute worst point (MAE). Post-exit recovery averages just 1.03% over 60 minutes. The stop is catching genuine bottoms. A trailing hard stop (wait for 1% bounce) has +$0.15/trade EV — marginal.

**Conclusion:** The strategy is gross profitable. Hard stops and fees together create the net drag. Entry-time indicators cannot predict which trades will hit hard stops — the adverse move happens after entry and is not distinguishable from normal volatility that winners also experience. Collecting 60-90 days of live paper trading data (target: July 2026) before next analysis session.

---

## 15. Working With This Codebase

### Safe to Change
- **Observer plugins** (`v2/plugins/observability/`) — isolated, no effect on trading logic
- **Report renderers/templates** — UI only
- **Backtest configs** — no production impact
- **Pair discovery filters** (volume thresholds, spread limits) — low risk, easy to revert
- **Scoring weights** in `composite_scoring/config.py` — additive, easy to tune

### Change With Care
- **`composite_scoring/indicators.py`** — Indicator computation. Changes affect all scoring. Must validate against backtest.
- **`composite_scoring/scoring.py`** — Scoring logic. The trend gate and min-indicator checks are here. Changes can enable/suppress entire classes of trades.
- **`exit_manager.py`** — 700+ lines of exit logic with subtle state tracking. The `_pending_exits` set, `_trailing_active` dict, and peak tracking state must stay consistent. A 2026-02-21 bug where `check_signal()` blocked the exit manager's own signals took days to find.
- **`app.py`** — Event wiring order matters. Portfolio update must happen synchronously before async storage. Position save must happen before memory delete (phantom position bug, 2026-02-27).
- **`maker_only.py`** — Stale order tracking and TTL logic. Changes can leave orders orphaned or cancel too aggressively.

### Do Not Change Without Discussion
- **Risk manager chain order** in YAML config — the sequence matters (basic → performance_filter → exit_manager → circuit_breaker). Reordering can cause signals to bypass critical checks.
- **Exit manager fee calculations** — These are carefully validated. Entry cost includes maker fee, exit revenue deducts taker fee. Changing the formula silently shifts all stop levels.
- **Sell de-duplication window** (30s) in `app.py` — Prevents duplicate sell orders from overlapping ticker events. Too short = double sells. Too long = missed exits.

### Known Gotchas
- **`@registry.plugin` in `__init__.py`**: Don't do it — `discover_plugins()` skips `__init__` modules. Use a separate file.
- **`docker compose restart` vs `--build`**: Restart does NOT rebuild. Code is baked into the image. Always use `docker compose up -d --build v2-kraken`.
- **Backtest vs. live de-dup**: Sell de-duplication uses wall-clock time. It's skipped in backtest mode (`self._backtest_mode`). Without this, simulated exits silently fail.
- **`v2_positions` staleness**: The DB stores position state, but `avg_entry_price` and `cost_basis` aren't recomputed after partial sells. The exit manager uses in-memory portfolio (correct). Don't query DB for live P&L decisions.
- **Kraken symbol mapping**: BTC ↔ XBT, DOGE ↔ XDG. Balance keys use legacy prefixes (XXBT → BTC, ZUSD → USD). The `KrakenSymbolMapper` handles this, but raw API responses need mapping.
- **Fee cache**: Exchange fees are fetched once per hour and cached. If Kraken changes fee tiers, it takes up to 1 hour to reflect.
- **Coinbase JWT**: Always use `jwt_generator.format_jwt_uri("GET", path)` for REST calls, never manual string formatting — causes 401s.

---

## 16. CLI Quick Reference

```bash
# ── Live / Paper Trading ──
python -m v2 --config v2/kraken_paper_trading.yaml       # Start paper trading
python -m v2 --config v2/config.yaml                      # Start with Coinbase config
python -m v2 --list-plugins                                # Show all registered plugins

# ── Reports ──
python -m v2 report                                        # Generate report manually

# ── Backtesting ──
python -m v2 --config v2/backtest_composite.yaml           # Run event-driven backtest
python -m v2 --config v2/backtest_diagnostic.yaml          # Run with diagnostics
python -m v2 --config v2/backtest_random_baseline.yaml     # Random entry baseline

# ── Analysis ──
python analysis/fee_sizing/fee_sizing_analysis.py          # Fee/sizing 5-section report
python analysis/fee_sizing/hard_stop_analysis.py           # Hard stop reduction run comparison
python analysis/fee_sizing/hard_stop_path_check.py         # Hard stop price path analysis
python scripts/analyze_diagnostics.py                      # Compare real vs random trades
python scripts/compare_signals.py                          # Compare v1/v2 signals
python scripts/diagnose_portfolio.py                       # Portfolio reconciliation

# ── Database (via Docker) ──
docker exec db psql -U bot_user -d bot_trader_db           # Interactive SQL
docker exec -i v2-kraken python < scripts/diagnose_portfolio.py   # Run script in container
```

---

## 17. What's Next

### Currently Live (as of 2026-07-31)
- Composite scoring paper bot on Kraken — serving as a regime instrument rather than a deployment candidate (see 2026-07-27/28 changelog: three strategy families evaluated against pre-registered validation; none passed for the current era). One universe-hygiene change since the July evaluation: a listing-age gate in pair discovery (2026-07-31, see changelog) — strategy parameters remain frozen.
- Streamlit dashboard (5 functional pages) + Caddy public access
- Walk-forward validation harness (`backtest/rotation/`) — reusable gauntlet for any strategy: 13 years of Kraken daily data cached, closed parameter menus, locked holdouts, exposure calibration; a full run takes minutes

### Active Work
- **Stable cash sleeve build (next session)**: wire the §12a "paid to wait" component (USDC flexible rewards under a close-based depeg gate, cap 0.75) into the paper bot as its baseline state, with simulated-accrual reporting as a separate P&L line.
- **Quarterly carry re-gauntlet** (from ~Oct 2026): the regime-gated carry strategy passed its fit era decisively and failed only the most recent era; re-run the 5-minute gauntlet as new data arrives.
- **Infra right-sizing**: BotTrader's workload is a fraction of its instance; downsize, then evaluate migration off EC2.
- **Equities research track**: evaluating a pivot of active-strategy effort to stocks (fee structure removes the dominant constraint identified in the July evaluation); first step is an economics audit of an existing external prediction dataset.

### Completed
- **Bot Health idle-vs-broken distinction** (2026-05-29): Three dashboard-only commits (`d8ba659` → `51ee7b8` → `9f7297c`) added a live strategy probe (`v2/dashboard/strategy_probe.py`) that runs the actual composite_scoring code against fresh Kraken REST OHLC. Status pill now has 3 states (🟢 Active / 🟡 Idle / 🔴 Stale) where the probe disambiguates "no recent trades" into "guardrails correctly blocking" vs "bot should have fired but didn't." New "Why is the bot idle?" panel renders per-gate pass counts and a per-symbol indicator/guardrail snapshot with outside-reader-friendly labels and tooltips. No v2-kraken changes — preserves the no-changes-until-July policy.
- **Silent-strategy event diagnosis** (2026-05-29): v2-kraken had no signals from 2026-05-23 14:48 UTC through 2026-05-29 04:30 UTC. Initial hypothesis (stale in-memory state after 7-week uptime) ruled out by restart (silence resumed). Root cause: regime + volume guardrails correctly blocking the entire active symbol universe in a low-volume / elevated-rolling-volatility regime. Confirmed by running the live strategy code locally against fresh Kraken OHLC — 12/12 sampled symbols had ATR percentile 84–97 (filter cap: 60) and 11/12 had RVOL below 0.7. The bot was working as designed. Diagnostic lesson: silence ≠ broken; always check what the strategy *would* output on current data before assuming a bug. This investigation directly drove the Bot Health upgrade above.
- **Bot Health dashboard page** (2026-05-16): Page 5 deployed — runtime status, recent activity, daily order submissions (30d), active symbols (7d, from `v2_fills`), equity curve from FIFO round-trips, recent orders with derived status. While building, discovered that `v2_orders.status` never persists `FILLED` (Kraken emits FillEvent but no OrderEvent on fill) — worked around via LEFT JOIN to `v2_fills`. Container state and risk-event panels deferred until they have DB persistence. Commits `de6ab9f` + `0fc778d`.
- **Executive Summary model upgrade** (2026-05-16): Swapped `claude-sonnet-4-20250514` → `claude-sonnet-4-6` ahead of Anthropic's 2026-06-15 retirement of Sonnet 4. Page footer label also updated. Commits `13d64bf` + `e339ae5`.
- **mean_reversion_v3 experiment** (2026-04-28 → 2026-04-30): Two-day exercise to redesign the strategy as a clean mean-reversion plugin. **Negative result** — v3 underperformed composite_scoring on all 3 OOS sets (WR 22-26% vs 55-65%). Plugin retained at `v2/plugins/strategies/mean_reversion_v3/` as alternative; not deployed. Net gains: 4 new exit_manager features (config-flagged, default-off), `min_trend_indicators` config on composite_scoring, Supertrend indicator implementation, 19 new tests, and a documented negative result. Key lesson: theoretical alignment doesn't translate to empirical edge — composite_scoring's edge isn't decomposable into clean theoretical components. See AI memory `v3_experiment_2026-04-29.md`.
- **Dashboard Session 2b** (2026-04-13): Entry Quality page (6 panels: win rate by symbol, score vs outcome, indicator hit rate, indicator combos, entry conditions, time-of-day heatmap). AI Executive Summary page (Claude Sonnet API). `.dockerignore` reduces build context from ~2.4 GB to ~50 MB. EC2 log rotation freed 2.1 GB (v1 logs removed). README.md rewritten for v2.
- **Streamlit dashboard Session 1 + 2a** (2026-04-12): Performance Report page (7 panels) and Edge Analysis page (6 panels) deployed. Reuses existing async collectors. FIFO round-trip matcher links buy metadata (score, indicators, ADX, RVOL) to sell outcomes (exit reason, P&L). Live prices from Kraken public REST API.
- **Fee/sizing analysis** (2026-04-11): Definitive finding — notional scaling is irrelevant with percentage-based fees (PF locked at 0.73 at all notionals). Avg gross return (+0.187%/trade) is 3.5x below RT fee (0.650%). No Kraken fee tier reaches breakeven. The strategy is not a fee problem — it is a hard stop problem. See `analysis/fee_sizing/` on branch `fee-sizing-analysis`.
- **Hard stop reduction backtests** (2026-04-11): Two runs tested regime filtering (ATR pctile 60→40) and ADX sweep (20/25/30). Neither filter discriminates between winners and losers — hard stop % stays ~21% regardless of filter strength. Improvements come from reducing total trade count (fewer fees), not selective filtering. Hard stop price path analysis confirmed stops are catching genuine bottoms (83% exit within 0.2% of MAE). Trailing hard stop idea has marginal +$0.15/trade EV ($10.56 total across 72 HS).
- **Overfitting validation** (2026-04-09): 3-set out-of-sample testing confirmed strategy is robust (see Section 14).
- **Backtest config alignment** (2026-04-09): 30 parameters aligned, 6 infra params intentionally different.

### Future Roadmap
- **Live deployment only via the pre-registered validation path** (gauntlet pass → paper quarter → small real capital). No strategy ships on backtest enthusiasm — the 2026-07 cycle demonstrated why, twice.
- `v2_orders.status` persistence fix: emit `OrderEvent(status=FILLED)` from `kraken.py` on fill so `v2_orders.status` becomes accurate without dashboard-side LEFT JOIN workarounds. One-line behavior addition, no strategy impact.
- **Strategy snapshot observer**: write per-symbol indicator + guardrail state to a new `v2_strategy_snapshots` table every N minutes from inside v2-kraken. Bot Health "Why is the bot idle?" panel then reads from that table instead of re-simulating from REST. Closes the REST-vs-live-buffer divergence. Requires a v2-kraken deploy.
- **Strategy cold-start warmup backfill**: extend the REST OHLC backfill in `kraken_websocket.py` to pre-seed `min_bars` of historical candles per symbol on startup, emitting them as CandleEvents before opening the WS. Eliminates the current ~6.7h post-restart blind window (`Strategy.warmup_bars` is declared in the interface but unconsumed).
- Dashboard Config Editor page
- Retire email reports once dashboard is validated

---

## 18. Further Reading

| Document | Location | What It Covers |
|----------|----------|---------------|
| Local Development Setup | `docs/1-production/LOCAL_DEVELOPMENT_SETUP.md` | Python setup, running tests, backtests, paper trading |
| AWS Deployment Guide | `docs/1-production/deployment/AWS_DEPLOYMENT_GUIDE.md` | Deploy workflow, health checks, container management, emergencies |
| Operational Runbook | `docs/1-production/OPERATIONAL_RUNBOOK.md` | Real incidents, troubleshooting, maintenance procedures (living document) |
| Database Access Guide | `docs/1-production/deployment/DATABASE_ACCESS_GUIDE.md` | SQL queries, pgAdmin SSH tunnel, v2 schema reference |
| Plugin Architecture | `docs/3-plugin-architecture/` | 8 ABCs, 30 plugins, EventBus, Registry, migration history |
| Methodology & Validation | `docs/4-analysis/METHODOLOGY_AND_VALIDATION.md` | Backtest datasets, overfitting policy, change tracking |
| Backtest Docs | `docs/2-backtesting/` | 4h-hybrid strategy specs, archived strategies |
| Dashboard Overview | `docs/5-planning/streamlit-dashboard-overview.md` | Architecture, resource impact, migration plan |
| Dashboard Dev Spec | `docs/5-planning/streamlit-dashboard-devspec.md` | File structure, Docker integration, page implementations |
| Edge Analysis Spec | `docs/5-planning/dashboard-edge-analysis-spec.md` | Session 2 spec: Edge Analysis + Entry Quality pages |
| Caddy Public Access Spec | `docs/5-planning/caddy-public-dashboard-spec.md` | HTTPS reverse proxy + basic auth for public domain access |
| Planning | `docs/5-planning/` | Active plans and specs |
| Archive | `docs/6-archive/` | v1-era docs, resolved bugs, historical sessions |
| AI Memory | `.claude/projects/.../memory/MEMORY.md` | Detailed project state for AI assistants |

---

## Active Issues

| Issue | Severity | Found | Details |
|-------|----------|-------|---------|
| **Backtest config drift** | ~~Medium~~ Resolved | 2026-04-04 | Fixed 2026-04-09 (commit `db46af3`). All 30 strategy/risk/sizing parameters aligned to production. Only infra params differ (retries, post_only, stale tracking). Out-of-sample validation confirmed strategy is not overfit. |
| **Fee drag on profitability** | ~~Medium~~ Analyzed | 2026-04-09 | **Closed 2026-04-11.** Notional scaling irrelevant (PF constant at 0.73 across all notionals). Avg gross return +0.187% vs 0.650% RT fee. No fee tier fixes it. Real lever is hard stop reduction, not sizing. Entry filters (regime, ADX) don't discriminate winners from losers — hard stop % stays ~21% regardless. Hard stops catch genuine bottoms (83% within 0.2% of MAE). Collecting 60-90 days live data for July 2026 re-analysis. |
| **roc_momo_20m sell path** | ~~High~~ Resolved | 2026-04-03 | Fixed 2026-04-04 (commit `193183a`). Moved `enable_roc_20m_momentum` to outer gate. Backtest validated, deployed. Monitoring for clean data. |

---

## Changelog

All significant changes to the system should be logged here. Format: `YYYY-MM-DD: Description`.

| Date | Change | Details |
|------|--------|---------|
| 2026-08-24 | BTC 200d-SMA regime turn — watcher fired; first event-driven carry re-gauntlet | BTC's daily close crossed above its 200-day SMA on the 2026-08-19 bar — the first above-SMA regime in the entire live collection record. The host-cron watcher detected the cross on its next scheduled run (2026-08-20 00:15 UTC) and emailed the alert exactly as designed. Operator note: the alert shares its sender address with the routine 4-hour reports and was swept out of the inbox by a mail filter, so it went unseen for four days — a filter exception is the immediate fix; a distinct alert sender is a hardening option. Per the standing protocol, the carry gauntlet was re-run on REST-topped-up daily bars (~1 min runtime): verdict unchanged from the 2026-07-28 run, which was mechanically expected — the strategy enters only on Mondays with the gate open, the cross confirmed on a Wednesday, and no gate-open Monday had yet completed inside the data window (the data store deliberately trails by a bar to guard against in-progress candles). The re-run re-verified the harness end-to-end on fresh data and pinned a clean pre-position baseline. Next re-gauntlet ≈ late Sep 2026 after ~4 gate-open weeks, immediately on a down-cross alert, or the Oct 2026 backstop — whichever comes first. Also verified the carry harness commits are contained in `main` (`feature/regime-carry` is fully merged; branch is a deletion candidate). Composite paper bot unchanged — now collecting its first above-SMA-regime forward data. |
| 2026-08-12 | Infra right-sizing + DNS/proxy consolidation | EC2 downsized t3.medium → t3.small (workload used <25% of the smaller size's RAM; Elastic IP retained so DNS was untouched); the restart doubled as the deploy for the score-target alignment below, paying the warmup blackout once. bottrader.trade moved behind the Cloudflare proxy (origin IP no longer in public DNS; SSL Full-strict; Caddy's Let's Encrypt cert validates at the edge) — watch item: first cert renewal through the proxy. Account-wide: the platform's four production domains now share one Cloudflare account; an idle Elastic IP was put to use instead of billed for nothing; a sibling project's box reclaimed 21GB of dead Docker build cache. Fleet cost ~$99 → ~$80/mo. |
| 2026-08-12 | Market-regime watcher + score-target config alignment | Added `scripts/market_regime_watch.py` (stdlib-only, host cron on the EC2 at 00:15 UTC): compares BTC's latest completed daily close to its 200-day SMA and emails on a cross in either direction, with a state file for once-per-cross alerting. Motivation: a retroactive analysis of the live record found BTC closed below its 200-day SMA on every day of the collection window — meaning the regime condition the shelved carry strategy waits for has never yet occurred during live collection; the watcher makes that regime turn an event rather than a quarterly calendar check. Also aligned `v2/kraken_paper_trading.yaml` score targets to the floors implied by the indicator-count gates (buy 2.0→4.5, sell 2.0→7.5, hysteresis pinned non-binding) — the old targets were never binding, so this is documentation-as-config with no live behavior change; deploy rides with the next container rebuild. Commits `67b453c` + `1af84d8`. |
| 2026-07-31 | Listing-age gate added to Kraken pair discovery | A replay of the live round-trip record against Kraken listing dates (first daily bar in the bulk dataset, verified against listing announcements) showed that the excess hard-stop incidence versus backtest calibration was concentrated entirely in recently listed pairs — the backtest datasets never contained fresh listings, while live pair discovery admits them as soon as their volume clears the threshold. Several such pairs exhibited stop-level overshoots consistent with thin, market-maker-driven books (one was delisted by Kraken within six months of listing). Added `min_listing_age_days` config to `KrakenPairDiscovery` (0 = disabled; paper config: 365): listing date approximated by the pair's first weekly OHLC bar via public REST, cached per process; seed symbols bypass; unknown listing dates are kept. Framed strictly as a universe-hygiene / tail-risk screen, not an edge claim — the pre-registered forward-paper prediction is that hard-stop incidence reverts toward backtest calibration. TDD (8 new tests, suite at 799), verified live on first production discovery pass (2 pairs rejected). Commit `a29d561`. |
| 2026-07-29 | Stable cash sleeve amendment ("paid to wait") | After the carry verdict, analyzed stablecoin depeg history from the bulk daily dataset (USDT's two-era record: chronic pre-2019 instability, then three recent years without a single daily close outside ±0.25%; USDC the cleanest on venue — the March 2023 SVB weekend is its only sub-0.995 close episode). Amended the carry spec (§12a/§12b) with an independent component deployable without the trading legs: USDC-only yield-bearing cash sleeve under a close-based depeg gate (exit below 0.9975 with the standard 1-day lag; re-entry after 5 consecutive closes ≥ 0.9990 — the gate fires exactly once in the 2020-2026 record), operator-set cap (set: 0.75), yield treated as live-contractual only and never backtested. Kraken Earn terms verified against the operator's account: flexible Opt-In Rewards selected; the DeFi Earn vault evaluated and declined (self-described leverage, 87% single-protocol concentration, ~13% liquidity buffer — a thin premium for a tall risk stack). Sleeve build scheduled as the next session. Commit `f9ff579`. |
| 2026-07-28 | Regime-gated carry strategy designed, built, validated — NOT deployed | Full cycle in one day: spec (dual-layer 200d gates, BTC/ETH/SOL, no-redistribution sleeve weights, 1-day exit-lag honesty handicap, fixed 2017+ eras, closed two-config menu) → subagent-driven TDD build (`regime_carry` core with reduced-book convention, lagged-exit backtest engine with hand-computed numeric fixture, exposure bisection calibration, single-phase gauntlet runner; multiple review-loop catches including a plan fixture defect and a scheme-asymmetric universe-semantics bug that would have contaminated config selection) → gauntlet run. Verdict: **failed criterion 1 on the validate era (Jan 2025 → Jul 2026)** despite a strong 2017-2025 fit and demonstrated bear-leg avoidance (fully in cash through the 2022 winter). Per spec §7: not deployed, no post-hoc rescue. Third independent strategy family to fail the same recent era — working conclusion: the current regime offers no defensible long-only spot edge at retail fees. Carry retained as shelf candidate; quarterly re-gauntlets (~5 min) from Oct 2026. Also: AWS bill attributed across the 3 projects sharing the account (no zombies); BotTrader infra right-sizing logged as a work item. Merged to main. |
| 2026-07-27 | July evaluation complete + momentum rotation harness — strategy NOT deployed | The planned July review ran on the full clean-collection window (234 FIFO round trips): the deep-indicator confirmation hypothesis **reversed out of sample** and was rejected before deployment; regime-conditional decomposition showed every entry-condition/outcome correlation flips between sub-periods (regime dominates parameters; symbol-cohort effects dominate signal effects); PerformanceFilter verified working live (the suspected AND-logic bug does not exist — real gap is in-memory state loss on restart, P3). A momentum rotation strategy (weekly cross-sectional, regime-gated, daily bars) was then specced and built as a complete walk-forward validation harness (`backtest/rotation/`: bulk OHLCVT + REST daily data store with in-progress-candle guard, point-in-time universe screens with stablecoin exclusion and staleness aging, no-lookahead portfolio engine, 547-bar eras, closed 24-config parameter menu, phase-locked one-shot holdout) and run against 13 years of Kraken daily history (626 USD pairs imported). Fit and validate eras passed; the locked holdout era failed — **not deployed** per the pre-registered bar. The harness itself (reusable for any strategy) merged to main. Composite scoring paper bot continues unchanged as a regime instrument. |
| 2026-06-03 | Security cleanup: rotated leaked credentials | Audit during May-review prep found a Coinbase Advanced Trade API EC private key committed in `Config/websocket_api_info.json` and the production Postgres password in 10 tracked files (2 live scripts, 7 archive files, 1 session note). Coinbase key revoked by user. Postgres password rotated via `ALTER USER bot_user PASSWORD ...` on the live db container, `/opt/bot/.env` updated atomically, v2-kraken + dashboard force-recreated cleanly (`PostgreSQL connected (pool_size=5)` confirmed in logs). Both leaked files untracked via `git rm --cached`; `.gitignore` hardened with `Config/*_api_info.json`, `Config/*.key`, `Config/*.pem` globs. 10 HEAD references to the literal old password scrubbed — live scripts switched to `os.environ['DB_PASSWORD']`, archives + session note redacted. Three stale `.env.backup-*` files removed from `/opt/bot`. Commits `f16e5c3` (untrack + gitignore) and `9ad211a` (HEAD scrub). Old values remain in git history; treated as fully public, no history rewrite. The BotTrader repo is public on GitHub — going-forward rule: no credentials, P&L analysis, or trading-edge hypotheses in tracked files. |
| 2026-05-29 | Bot Health idle-vs-broken distinction shipped | Three dashboard commits (`d8ba659`, `51ee7b8`, `9f7297c`) added `v2/dashboard/strategy_probe.py` — runs `compute_indicators` + `compute_scores` against fresh Kraken REST OHLC, cached 5 min. Status pill became 3-state (🟢 Active / 🟡 Idle / 🔴 Stale) with the probe disambiguating "stuck idle" from "correctly idle." New "Why is the bot idle?" panel renders 5 per-gate metric cards (Indicator strength / Indicator count / Trend strength / Volume / Volatility regime) and a per-symbol table with friendly column names + hover tooltips containing the technical names and thresholds. Dashboard-only — preserves the no-changes-until-July policy. |
| 2026-05-29 | Silent-strategy event investigated and closed (not a bug) | v2-kraken produced zero signals from 2026-05-23 14:48 UTC through 2026-05-29 04:30 UTC. Container restart did not resume signals — ruled out stale state. Root cause: regime + volume guardrails correctly vetoing the entire active symbol universe in a low-volume / elevated-rolling-volatility regime. Confirmed by running the live strategy code locally against fresh Kraken OHLC (12/12 sampled symbols had ATR percentile 84–97 vs `regime_max_atr_percentile: 60` cap, and 11/12 had RVOL well below the `volume_confirm_threshold: 0.7`). Bot was working as designed; the Bot Health page just couldn't surface that. Lesson: silence ≠ broken — always check what the strategy *would* output on current data before assuming a bug. Drove the Bot Health upgrade above. Full diagnostic writeup in AI memory `silent_strategy_event_2026-05-29.md`. |
| 2026-05-16 | Bot Health dashboard page deployed (Page 5) | New page surfaces runtime status (DB-activity freshness proxy), recent order/fill/cancel timestamps, daily order submissions (30d), active symbols (7d, from `v2_fills`), equity curve from FIFO round-trips, and recent orders with derived status (LEFT JOIN to `v2_fills`). Documented an existing persistence gap: `v2_orders.status` never transitions to `FILLED` because `kraken.py:587` emits `FillEvent` but no `OrderEvent(FILLED)`. Container state and risk-event panels deferred (no DB persistence yet). Persistence fix is P3 on the backlog, held back until post-July to preserve the live-data collection window. Commits `de6ab9f` + `0fc778d`. |
| 2026-05-16 | Executive Summary model upgrade (Sonnet 4 retirement) | Anthropic announced Sonnet 4 (`claude-sonnet-4-20250514`) retirement on 2026-06-15. Updated `v2/dashboard/ai_summary.py:229` to `claude-sonnet-4-6` and synced the page footer caption. Commits `13d64bf` + `e339ae5`. |
| 2026-05-12 | Pass 4 project audit — Config/*.py + stale scripts archived | 13 `Config/*.py` files (incl. `__init__.py`), 5 v1-era JSON configs (`config.json`, `sighook_config.json`, `webhook_*.json`), and 7 v1-schema scripts (3 in `scripts/`, 3 in `scripts/diagnostics/`, all 3 files of now-empty `scripts/migrations/`) moved to `archive/v1-libs/` via `git mv` (27 renames). Empty `scripts/migrations/` removed. `Config/` now holds only gitignored production API key JSONs. All 704 v2 tests still pass. |
| 2026-05-12 | Pass 3 project audit — v1-era libs archived | 8 root dirs (`Shared_Utils/`, `SharedDataManager/`, `TableModels/`, `database/`, `database_manager/`, `fifo_engine/`, `utils/`, `data/`) and 5 v1-schema scripts moved to `archive/v1-libs/` via `git mv` (60 renames). All 704 v2 tests still pass. See `archive/v1-libs/README.md`. |
| 2026-04-30 | mean_reversion_v3 experiment closed (negative result) | New `mean_reversion_v3` strategy plugin (~530 lines) built and validated against 3-set OOS framework. Inferior to composite_scoring (22-26% WR vs 55-65%; statistically indistinguishable winners/losers at entry). Plugin retained at `v2/plugins/strategies/mean_reversion_v3/` but not deployed. Permanent gains: 4 new exit_manager config flags (`breakeven_trigger_pct`, `fixed_take_profit_pct`, `trailing_enabled`, `stale_exit_regardless_of_pnl`, all default-off), `min_trend_indicators` on composite_scoring, Supertrend indicator implementation, 19 new tests (suite at 704). Key lesson: composite_scoring's edge doesn't decompose by indicator philosophy. |
| 2026-04-13 | Caddy public dashboard deployed | `bottrader.trade` — Caddy reverse proxy with auto Let's Encrypt TLS and HTTP Basic Auth. 4th Docker container (64MB, Alpine). Domain via Cloudflare Registrar, Elastic IP `44.238.14.228`. SSH tunnel preserved as fallback. New files: `docker/Caddyfile`, `docker/Dockerfile.caddy`. `.env` additions: `DASHBOARD_DOMAIN`, `DASHBOARD_USER`, `DASHBOARD_PASSWORD_HASH`. |
| 2026-04-13 | Dashboard Session 2b + maintenance | Entry Quality page (6 panels: win rate by symbol, score vs outcome, indicator hit rate, indicator combos, entry condition scatters, time-of-day heatmap). AI Executive Summary page (Claude Sonnet API generates interpretive performance summary with backtest comparison). `.dockerignore` added (build context 2.4 GB → 50 MB). EC2 log rotation: 2.1 GB v1 logs removed. README.md rewritten for v2. Caddy public access spec written. |
| 2026-04-12 | Streamlit dashboard deployed (Session 1 + 2a) | 3rd Docker container (`dashboard`, 256MB, Streamlit 1.56). Page 1: Performance Report (hero P&L, portfolio, P&L by symbol, exit breakdown, open positions with live Kraken prices, trade log). Page 2: Edge Analysis (weekly P&L trend, exit reason P&L trend, hard stop rate vs baseline, avg metrics, P&L distribution, peak capture scatter). FIFO round-trip matcher (`trades.py`) links buy metadata to sell outcomes. Trigger filter defaults to score-only (roc_momo excluded). `daily_report_v2/__init__.py` guarded for dashboard import compatibility. EC2 disk cleaned (Docker prune + backtest data removed from server). |
| 2026-04-11 | Fee/sizing & hard stop analysis complete | Fee analysis: PF constant at 0.73 across all notionals (%-based fees). Hard stop analysis: entry filters (regime, ADX) don't discriminate — HS% stays ~21%. Price path: 83% of hard stops catch genuine bottoms. Trailing HS idea: +$0.15/trade EV (marginal). Decision: collect 60-90d live data, re-analyze July 2026. Branch `fee-sizing-analysis`. |
| 2026-04-09 | Backtest config aligned + OOS validation | 30 parameters aligned to production. 3-set out-of-sample validation (27 symbols) confirmed strategy is not overfit — OOS sets outperformed training data. Fee drag identified as #1 P&L lever. Commit `db46af3`. |
| 2026-04-09 | Order persistence, symbol logging, warmup | Orders now persisted to `v2_orders`. Pair discovery logs full symbol list at startup. `min_bars` increased 40→80 for reliable indicator warmup. Commit `b2e9e24`. |
| 2026-04-09 | Exit manager MARKET order + pending exit fix | Trailing stop and peak tracking exits were using LIMIT orders instead of MARKET — a stale LIMIT sell could drift and cancel, leaving the position stuck. `_pending_exits` was never cleared on order cancellation, permanently blocking exit re-evaluation. Both fixed. Commit `a3f6198`. |
| 2026-04-04 | roc_momo_20m exit bug fixed | `enable_roc_20m_momentum: false` now gates both buy AND sell paths. Previously only gated buys — 22 unwanted sell signals across all config eras. Commit `193183a`. |
| 2026-04-04 | Documentation overhaul | SYSTEM_CONTEXT.md (living document), ONBOARDING.md, LOCAL_DEVELOPMENT_SETUP.md, AWS_DEPLOYMENT_GUIDE.md, OPERATIONAL_RUNBOOK.md (14 real incidents). 22 stale v1 docs archived, plugin architecture docs updated to 30 plugins / 8 categories. |
| 2026-03-15 | Buy order TTL + stale exit conditioning | Unfilled buys auto-cancel after 10 min. `stale_exit` only fires when P&L < 0 (no longer ejects profitable positions). |
| 2026-03-15 | Zombie position prevention | Positions from DB restore are merged into pair discovery symbol list. Data providers notified of position symbols to prevent unsubscribe. |
| 2026-03-15 | Report fix | Reports now sent even during quiet trading periods (previously skipped when no events in window). |
| 2026-03-10 | ATR hard stops | Hard stops now use 3× hourly ATR (14-period), clamped to [5.5%, 8%]. Replaced fixed 5.5% stop. Separate `hard_stop_atr_period` config. |
| 2026-03-10 | roc_momo_24h disabled | 28% hard stop rate in live trading. Both momentum paths now off. |
| 2026-02-27 | Trend confirmation gate | Requires ≥1 trend indicator (MACD, ROC, Swing) for composite buys. Eliminated 12/18 hard stops from falling-knife pattern. |
| 2026-02-27 | Phantom position restore fix | `save_position(qty=0)` called before deleting from memory. Prevents stale DB entries from restoring phantom positions on restart. |
| 2026-02-27 | Config sync | Live config aligned to backtest-validated params (hard_stop 4.5%→5.5%, trailing activation 3%→2%). |
| 2026-02-23 | Diagnostic backtest framework | MFE/MAE tracking, post-exit analysis, market regime snapshots, random entry baseline. |
| 2026-02-21 | Exit manager signal fix | `check_signal()` was blocking exit manager's own trailing/hard stop signals. Fixed via `_pending_exits` set. |
| 2026-02-21 | Pair discovery hardened | Bid-ask spread filter (max 100 bps). Eliminated junk tokens (MYX, USELESS, AZTEC). |
| 2026-02-21 | Market orders for all stops | Soft stops changed from LIMIT to MARKET. LIMIT sells on illiquid pairs were getting cancelled as stale. |
| 2026-02-18 | v2 live trading parity | 8 phases: fee-aware P&L, market orders, ATR trailing, peak tracking, per-channel WS monitoring, trigger sizing, min order validation, position restore. |
| 2026-02-18 | Kraken integration | 3 new plugins (exchange, data, pair discovery) + symbol mapper + auth utilities. |
| 2026-02-18 | v1 archived | All v1 code moved to `archive/v1/`. Coinbase paper trader stopped (fees too high). |
| 2026-02-10 | Daily report v2 | Modular observer: FIFO P&L, trade log, exit stats, HTML + Slack renderers, SES delivery. |
| 2026-02-08 | v2 signal pipeline validated | 24 signals logged end-to-end. Trade-for-trade match (33 trades, 100%) with Phase 1 backtest. |
| 2026-02 | v2 plugin architecture | 8 ABCs, EventBus pub/sub, Registry auto-discovery. Full rewrite from v1 dual-container model. |
