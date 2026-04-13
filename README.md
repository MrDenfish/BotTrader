# BotTrader v2

Automated cryptocurrency trading bot for Kraken with composite scoring strategy, multi-layer exit management, and a Streamlit diagnostic dashboard.

## What It Does

BotTrader continuously monitors 16-30 Kraken USD trading pairs via WebSocket, evaluates 8 technical indicator families on 5-minute candles, and executes trades when enough indicators align. Positions are managed by a layered exit system (hard stops, trailing stops, time-based exits) — all fee-aware.

**Current mode:** Paper trading on Kraken (simulated fills, live market data).

## Architecture

```
EC2 (t3.medium)
  db          PostgreSQL 16         (port 5432, localhost only)
  v2-kraken   Paper trading bot     (512MB limit)
  dashboard   Streamlit web UI      (port 8501, SSH tunnel)
```

- **Plugin architecture:** 8 categories, 30 plugins, event-driven via typed pub/sub EventBus
- **Strategy:** Composite scoring across Bollinger Bands, MACD, RSI, ROC, W-Bottom/M-Top, Swing, Volume Divergence
- **Exit management:** ATR-based hard stops (floor 5.5%), ATR trailing stops (activates at +2%), 48h time limit, fee-conditioned stale exits
- **Execution:** Post-only LIMIT orders (maker fees), $75 default notional

## Dashboard

Streamlit web UI replacing email reports. Access via SSH tunnel:

```bash
ssh -L 8501:localhost:8501 bottrader-aws -N
# Open http://localhost:8501
```

**Pages:**
1. **Report** — P&L, portfolio value, trade log, open positions with live Kraken prices
2. **Edge Analysis** — Weekly P&L trends, exit reason breakdown, hard stop rate vs backtest baseline, fee drag visualization
3. **Entry Quality** — Win rate by symbol, indicator hit rate (winners vs losers), indicator combination performance, score vs outcome
4. **Executive Summary** — AI-generated performance interpretation via Claude API

## Quick Start

### Prerequisites

- Python 3.11+
- Docker & Docker Compose
- Kraken API credentials (for live trading; paper mode uses simulated fills)

### Local Development

```bash
# Clone and install
git clone <repo-url>
cd BotTrader
pip install -r requirements.txt

# Run tests
pytest v2/tests/ -q

# Start paper trading locally
python -m v2 --config v2/kraken_paper_trading.yaml

# List all plugins
python -m v2 --list-plugins
```

### Production Deployment (AWS)

```bash
# Push code
git push origin main

# Deploy to EC2
ssh bottrader-aws "cd /opt/bot && git pull origin main"

# Rebuild bot (--build is REQUIRED — code is baked into image)
ssh bottrader-aws "cd /opt/bot && docker compose -f docker-compose.aws.yml up -d --build v2-kraken"

# Rebuild dashboard
ssh bottrader-aws "cd /opt/bot && docker compose -f docker-compose.aws.yml up -d --build dashboard"

# Verify
ssh bottrader-aws "docker ps"
```

**Important:** `docker compose restart` does NOT pick up code changes. Always use `up -d --build`.

## Project Structure

```
v2/                           All v2 code
  core/                       App lifecycle, types, EventBus, Registry, config
  plugins/
    exchanges/                paper, kraken, coinbase, backtest
    data/                     kraken_websocket, websocket (Coinbase), csv_replay
    strategies/               composite_scoring (primary)
    risk/                     basic, exit_manager, circuit_breaker, performance_filter
    execution/                maker_only (post-only LIMIT orders)
    persistence/              postgres, sqlite
    observability/            structured_log, heartbeat, daily_report_v2, alerting
    pair_discovery/           kraken, coinbase, csv
  dashboard/                  Streamlit dashboard
    app.py                    Entry point + page navigation
    db.py                     asyncpg pool + async-to-sync bridge
    trades.py                 FIFO round-trip matcher (buy metadata -> sell outcomes)
    prices.py                 Kraken public REST ticker (live unrealized P&L)
    ai_summary.py             Claude API executive summary generation
    pages/                    report, edge_analysis, entry_quality, executive_summary
  kraken_paper_trading.yaml   Production config
backtest/                     Backtest framework + engine
strategies/                   Phase 1 plugin code (v2 imports — do NOT archive)
docker/
  Dockerfile.v2               Bot image
  Dockerfile.dashboard        Dashboard image
  entrypoint/                 Container startup scripts
docker-compose.aws.yml        Production Docker Compose (3 services)
docs/                         Documentation
  SYSTEM_CONTEXT.md           Full system context (living document)
```

## Database

Four tables in PostgreSQL 16:

| Table | Purpose |
|-------|---------|
| `v2_fills` | Every executed trade (price, qty, fee, metadata JSONB) |
| `v2_orders` | Submitted orders and status |
| `v2_positions` | Current position state |
| `v2_state` | Key-value state persistence (survives restarts) |

```bash
# Connect to database
ssh bottrader-aws "docker exec -it db psql -U bot_user -d bot_trader_db"
```

## Configuration

All configuration in YAML. Production config: `v2/kraken_paper_trading.yaml`.

Key parameters:
- **Pair discovery:** Volume floor $2M, spread max 100 bps, max 30 pairs
- **Scoring:** 8 indicator families, buy threshold 2.0, min 3 indicators, trend confirmation required
- **Exit manager:** ATR hard stops (3x hourly ATR, floor 5.5%, ceiling 8%), trailing activation at +2%
- **Execution:** Post-only LIMIT, $75 notional, 10-min buy TTL
- **Fees:** 0.25% maker / 0.40% taker (0.65% round-trip)

## Testing

```bash
pytest v2/tests/ -q              # All tests (685+)
pytest v2/tests/ -k "exit"       # Exit manager tests
pytest v2/tests/ --tb=short      # Short tracebacks
```

## Documentation

| Document | Location |
|----------|----------|
| System Context (start here) | `docs/SYSTEM_CONTEXT.md` |
| Dashboard Specs | `docs/5-planning/` |
| Deployment Guide | `docs/1-production/deployment/` |
| Operational Runbook | `docs/1-production/OPERATIONAL_RUNBOOK.md` |
| Backtesting | `docs/2-backtesting/` |
| Plugin Architecture | `docs/3-plugin-architecture/` |

## Current Status

**Paper trading on Kraken** — collecting live data for July 2026 performance analysis. No parameter changes during collection period.

**Key metrics (as of April 2026):**
- Profit Factor: 0.73 (not yet profitable — fees exceed gross edge)
- Hard stop rate: ~22% (confirmed stable across backtest and live)
- Avg gross return: +0.187% per trade vs 0.65% round-trip fee
- Strategy validated as not overfit (3-set out-of-sample testing, 27 symbols)

## License

Private project — All rights reserved.
