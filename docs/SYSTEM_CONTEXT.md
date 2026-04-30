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
│                   EC2 (t3.medium, Ubuntu)                     │
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
│   │   └── pages/                   #   report, edge_analysis, entry_quality, executive_summary, health (stub), config_editor (stub)
│   ├── tests/                       #   685+ passing tests
│   ├── kraken_paper_trading.yaml    #   PRODUCTION config (paper trading on Kraken)
│   ├── config.yaml                  #   Coinbase live config (dormant)
│   └── backtest_*.yaml              #   Backtest configurations
├── backtest/                        # ── Backtest framework ──
│   ├── engine.py                    #   Event-driven backtest simulator
│   ├── data/                        #   Historical 1-min OHLCV CSVs per symbol
│   └── diagnostic_output/           #   Trade-level analysis (MFE/MAE, regime, indicators)
├── strategies/                      #   Phase 1 plugin code (v2 imports — do NOT archive)
├── Config/                          #   Exchange API keys (gitignored), pair filtering
├── database_manager/                #   SQLAlchemy ORM utilities
├── fifo_engine/                     #   Tax-compliant FIFO P&L accounting
├── scripts/                         #   Diagnostics, analysis, deployment helpers
├── docs/                            #   Organized documentation (see Section 14)
├── archive/v1/                      #   All v1 code (archived Feb 2026, history preserved)
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

API keys are stored in `Config/kraken_api_info.json` (gitignored).

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

---

## 11. Deployment

### Production Location
- **Server:** AWS EC2 t3.medium (20GB disk, 4GB RAM)
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
4. **Exclude shill coins:** Configurable blocklist (e.g., TRUMP)
5. **Cap at 30 pairs**, sorted by volume
6. **Seed symbols guaranteed:** BTC-USD and ETH-USD always included
7. **Refresh every 30 minutes** — publishes `SymbolsUpdatedEvent` on changes

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
- Feeds structured metrics + backtest baselines to Claude Sonnet via Anthropic API
- Generates 6-10 sentence interpretive summary comparing live performance to backtest expectations
- Highlights trends (improving/stable/degrading), anomalies, and actionable recommendations
- Cached 5 minutes, manual regenerate button available
- Requires `ANTHROPIC_API_KEY` in `.env`

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

### Currently Live (as of 2026-04-13)
- Paper trading on Kraken with dynamic pair discovery (16-30 pairs)
- Composite scoring with trend confirmation gate, ADX gate, volume confirmation
- ATR-based hard stops and trailing stops
- 4-hour email reports via SES (running in parallel with dashboard)
- Streamlit dashboard with 4 functional pages: Report, Edge Analysis, Entry Quality, Executive Summary
- AI-generated executive summaries via Claude Sonnet (Anthropic API)
- Momentum paths (roc_momo_20m, roc_momo_24h) disabled

### Active Work
- **Paper trading data collection** (2026-04-12 → July 2026): Collecting 75-90 days of live Kraken paper trading data. **No parameter changes during this period** — the analytical clean-regime is the experiment. Dashboard Edge Analysis and Entry Quality pages serve as the primary analysis tool for the July review. The April 2026 v3 experiment sharpened the questions for July (see "AI Memory" file `july_2026_evaluation_prep.md`).

### Completed
- **mean_reversion_v3 experiment** (2026-04-28 → 2026-04-30): Two-day exercise to redesign the strategy as a clean mean-reversion plugin. **Negative result** — v3 underperformed composite_scoring on all 3 OOS sets (WR 22-26% vs 55-65%). Plugin retained at `v2/plugins/strategies/mean_reversion_v3/` as alternative; not deployed. Net gains: 4 new exit_manager features (config-flagged, default-off), `min_trend_indicators` config on composite_scoring, Supertrend indicator implementation, 19 new tests, and a documented negative result. Key lesson: theoretical alignment doesn't translate to empirical edge — composite_scoring's edge isn't decomposable into clean theoretical components. See AI memory `v3_experiment_2026-04-29.md`.
- **Dashboard Session 2b** (2026-04-13): Entry Quality page (6 panels: win rate by symbol, score vs outcome, indicator hit rate, indicator combos, entry conditions, time-of-day heatmap). AI Executive Summary page (Claude Sonnet API). `.dockerignore` reduces build context from ~2.4 GB to ~50 MB. EC2 log rotation freed 2.1 GB (v1 logs removed). README.md rewritten for v2.
- **Streamlit dashboard Session 1 + 2a** (2026-04-12): Performance Report page (7 panels) and Edge Analysis page (6 panels) deployed. Reuses existing async collectors. FIFO round-trip matcher links buy metadata (score, indicators, ADX, RVOL) to sell outcomes (exit reason, P&L). Live prices from Kraken public REST API.
- **Fee/sizing analysis** (2026-04-11): Definitive finding — notional scaling is irrelevant with percentage-based fees (PF locked at 0.73 at all notionals). Avg gross return (+0.187%/trade) is 3.5x below RT fee (0.650%). No Kraken fee tier reaches breakeven. The strategy is not a fee problem — it is a hard stop problem. See `analysis/fee_sizing/` on branch `fee-sizing-analysis`.
- **Hard stop reduction backtests** (2026-04-11): Two runs tested regime filtering (ATR pctile 60→40) and ADX sweep (20/25/30). Neither filter discriminates between winners and losers — hard stop % stays ~21% regardless of filter strength. Improvements come from reducing total trade count (fewer fees), not selective filtering. Hard stop price path analysis confirmed stops are catching genuine bottoms (83% exit within 0.2% of MAE). Trailing hard stop idea has marginal +$0.15/trade EV ($10.56 total across 72 HS).
- **Overfitting validation** (2026-04-09): 3-set out-of-sample testing confirmed strategy is robust (see Section 14).
- **Backtest config alignment** (2026-04-09): 30 parameters aligned, 6 infra params intentionally different.

### Future Roadmap (July 2026+)
- **Live data analysis**: Analyze 60-90 days of paper trading data using dashboard Edge Analysis and Entry Quality pages. Compare live metrics to backtest baselines. Executive Summary page provides AI interpretation.
- Public dashboard via Caddy + domain (spec ready, awaiting prerequisites)
- Dashboard Bot Health page and Config Editor page
- Retire email reports once dashboard is validated
- Transition from paper to live trading on Kraken

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
