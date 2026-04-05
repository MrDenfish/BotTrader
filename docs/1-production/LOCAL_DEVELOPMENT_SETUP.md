# Local Development Setup

How to set up BotTrader v2 on your local machine for development, testing, and backtesting.

---

## Prerequisites

- **Python 3.10 or 3.11** (3.11 recommended — matches Docker image)
- **Docker Desktop** (for PostgreSQL, or use a native install)
- **Git** (the project is deployed via git)
- **Kraken API keys** (optional — only needed for live/paper trading against Kraken)

---

## 1. Clone and Create Virtual Environment

```bash
git clone <repo-url> BotTrader
cd BotTrader

# Create virtual environment
python3.11 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

The `requirements.txt` is pinned via `pip-compile` from `requirements.in`. Key dependencies:
- `asyncpg` — PostgreSQL async driver
- `aiohttp` / `websockets` — WebSocket connections
- `pandas` / `numpy` — Indicator computation
- `coinbase-advanced-py` — Coinbase exchange (dormant)
- `ccxt` — Multi-exchange library (Kraken)
- `pyyaml` — Configuration loading
- `jinja2` — HTML report templates
- `boto3` — AWS SES for email reports

---

## 2. Start PostgreSQL

### Option A: Docker (recommended)

```bash
# Start a local PostgreSQL container
docker run -d \
  --name bottrader-db \
  -e POSTGRES_DB=bot_trader_db \
  -e POSTGRES_USER=bot_user \
  -e POSTGRES_PASSWORD=localdev \
  -p 5432:5432 \
  postgres:16

# Verify it's running
docker exec bottrader-db pg_isready -U bot_user
```

### Option B: Use an existing PostgreSQL instance

Just ensure you have a database and user ready. The v2 storage plugin auto-creates
its tables (`v2_fills`, `v2_orders`, `v2_positions`, `v2_state`) on first connect.

---

## 3. Configure Environment

Create a `.env` file in the project root (gitignored):

```bash
# ── Core ──
DATABASE_URL=postgresql://bot_user:localdev@localhost:5432/bot_trader_db
LOG_LEVEL=INFO
TZ=America/Los_Angeles

# ── Kraken API (optional — only needed for live/paper trading) ──
# Keys go in Config/kraken_api_info.json (see below)

# ── Email Reports (optional — only needed if testing report delivery) ──
# SMTP_USERNAME=...
# SMTP_PASSWORD=...
```

### Kraken API Keys (optional)

If you want to run paper trading with live Kraken WebSocket data, create
`Config/kraken_api_info.json`:

```json
{
    "api_key": "your-kraken-api-key",
    "api_secret": "your-kraken-api-secret"
}
```

This file is gitignored. You can generate keys at https://www.kraken.com/u/security/api
with "Query Funds" and "Query Open Orders & Trades" permissions (no trading permissions
needed for paper mode).

---

## 4. Verify Installation

```bash
# Activate venv if not already
source .venv/bin/activate

# List all registered plugins (no DB or API keys needed)
python -m v2 --list-plugins
```

Expected output (30 plugins across 8 categories):
```
exchange: backtest, paper, kraken, coinbase, backtest_sim
data: websocket, kraken_websocket, csv_replay
strategy: composite_scoring, hybrid_4h_maker, random_entry
risk: basic, exit_manager, performance_filter, circuit_breaker
execution: maker_only, bracket
storage: postgres, sqlite
observer: structured_log, signal_comparison, heartbeat, alerting, daily_report, daily_report_v2, backtest_diagnostics, backtest_results
pair_discovery: kraken, coinbase, csv
```

---

## 5. Run Tests

```bash
# Run the full test suite
python -m pytest v2/tests/ -v

# Run a specific test file
python -m pytest v2/tests/test_exit_manager.py -v

# Run tests matching a keyword
python -m pytest v2/tests/ -k "composite" -v
```

Tests use SQLite storage and mock exchanges — no PostgreSQL or API keys required.

**Important test convention:** When writing exit_manager tests, default fees to 0 in
the test fixture unless specifically testing fee behavior.

---

## 6. Run a Backtest

Backtests replay historical CSV data through the full v2 pipeline. No API keys or
PostgreSQL needed (uses SQLite storage).

### Historical Data Location

```
backtest/data/
├── BTC/BTC-USD_1min.csv
├── ETH/ETH-USD_1min.csv
├── SOL/SOL-USD_1min.csv
└── ... (9 symbols total)
```

CSV format: `timestamp,open,high,low,close,volume` (1-minute bars).

### Run a Standard Backtest

```bash
# Composite scoring strategy (same as production)
python -m v2 --config v2/backtest_composite.yaml

# With diagnostics (MFE/MAE, regime snapshots, indicator correlations)
python -m v2 --config v2/backtest_diagnostic.yaml

# Random entry baseline (control group for comparison)
python -m v2 --config v2/backtest_random_baseline.yaml
```

Results print to stdout. Diagnostic output goes to `backtest/diagnostic_output/diagnostic_trades.jsonl`.

### Analyze Diagnostic Results

```bash
python scripts/analyze_diagnostics.py
```

---

## 7. Run Paper Trading Locally

Paper trading uses live WebSocket market data but simulates all order fills locally.
Requires Kraken API keys for WebSocket authentication and PostgreSQL for state persistence.

```bash
# Make sure PostgreSQL is running and .env is configured
# Make sure Config/kraken_api_info.json has your API keys

python -m v2 --config v2/kraken_paper_trading.yaml --log-level DEBUG
```

This will:
1. Connect to Kraken WebSocket for live ticker data
2. Discover trading pairs (filtered by volume and spread)
3. Aggregate tickers into 5-minute candles
4. Run composite scoring on each candle
5. Simulate order fills via the paper exchange
6. Persist fills and positions to PostgreSQL
7. Log signals to `logs/v2_kraken_score_log.jsonl`

Press `Ctrl+C` to stop gracefully.

---

## What Works Without API Keys

| Feature | API Keys Needed | Database Needed |
|---------|----------------|-----------------|
| `--list-plugins` | No | No |
| Run tests (`pytest`) | No | No |
| Run backtests | No | No (uses SQLite) |
| Analyze diagnostics | No | No |
| Paper trading (Kraken) | Yes (Kraken) | Yes (PostgreSQL) |
| Generate reports manually | No keys, but needs fills in DB | Yes |

---

## Project Structure Quick Reference

```
BotTrader/
├── v2/                    # All v2 code — start here
│   ├── core/              #   Framework (types, event bus, interfaces, registry, config, app)
│   ├── plugins/           #   30 plugins across 8 categories
│   ├── tests/             #   672+ tests
│   └── *.yaml             #   Configuration files
├── backtest/              #   Backtest engine + historical data
├── Config/                #   API keys (gitignored)
├── scripts/               #   Analysis and diagnostic scripts
├── docs/                  #   Documentation (start with SYSTEM_CONTEXT.md)
└── docker-compose.aws.yml #   Production Docker Compose
```

For the full system overview, read [SYSTEM_CONTEXT.md](../SYSTEM_CONTEXT.md).

---

## Common Issues

### `ModuleNotFoundError: No module named 'v2'`

Make sure you're running from the project root:
```bash
cd /path/to/BotTrader
python -m v2 --list-plugins
```

### `asyncpg` connection refused

PostgreSQL isn't running or `DATABASE_URL` is wrong. Check:
```bash
docker ps | grep postgres
echo $DATABASE_URL
```

### Tests fail with import errors

Make sure you installed all dependencies:
```bash
pip install -r requirements.txt
```

### Backtest says "No data files found"

Check that CSV data exists in `backtest/data/<SYMBOL>/<SYMBOL>-USD_1min.csv`.
The symbol directory name must match (e.g., `BTC/BTC-USD_1min.csv`).

---

**Last Updated:** 2026-04-03
