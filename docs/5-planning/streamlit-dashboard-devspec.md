# BotTrader Streamlit Dashboard — Developer Spec

## 1. File Structure

```
v2/
  dashboard/
    __init__.py
    app.py                        # Streamlit entry point + page router
    db.py                         # asyncpg pool, query helpers, portfolio replay
    config_io.py                  # YAML load/save/diff/validate
    docker_control.py             # Container restart via Docker SDK
    pages/
      __init__.py
      report.py                   # Page 1: Report (replaces email)
      health.py                   # Page 2: Bot Health
      config_editor.py            # Page 3: Config Editor

docker/
  Dockerfile.dashboard            # Slim Python image for Streamlit
  entrypoint/
    entrypoint.dashboard.sh       # Postgres wait + streamlit launch

docker-compose.aws.yml            # Modified: add dashboard service + config volume
```

**Total: 9 new files, 1 modified file.**

---

## 2. Docker Integration

### 2.1 Dockerfile.dashboard

```dockerfile
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    tini curl && rm -rf /var/lib/apt/lists/*

RUN groupadd -r appuser && useradd -r -g appuser appuser

WORKDIR /app
COPY v2/dashboard/requirements.txt /app/v2/dashboard/requirements.txt
RUN pip install --no-cache-dir -r /app/v2/dashboard/requirements.txt

COPY . /app
RUN chown -R appuser:appuser /app

USER appuser
EXPOSE 8501
ENTRYPOINT ["tini", "--", "/app/docker/entrypoint/entrypoint.dashboard.sh"]
```

### 2.2 Dashboard requirements.txt

```
streamlit>=1.30,<2.0
plotly>=5.18,<6.0
asyncpg>=0.29,<1.0
pyyaml>=6.0
pandas>=2.0,<3.0
docker>=7.0,<8.0
```

### 2.3 docker-compose.aws.yml Changes

Add the `dashboard` service and mount the config file as a volume on `v2-kraken`:

```yaml
  # ADD to v2-kraken volumes (config no longer baked-in, allows live edit):
  v2-kraken:
    volumes:
      - /opt/bot/.env:/app/.env:ro
      - /opt/bot/logs:/app/logs
      - /opt/bot/v2/kraken_paper_trading.yaml:/app/v2/kraken_paper_trading.yaml:ro

  # NEW service:
  dashboard:
    build:
      context: .
      dockerfile: ./docker/Dockerfile.dashboard
    container_name: dashboard
    logging:
      driver: json-file
      options:
        max-size: "20m"
        max-file: "2"
    environment:
      TZ: ${TZ}
      DATABASE_URL: "postgresql://${DB_USER:-bot_user}:${DB_PASSWORD}@db:${DB_PORT:-5432}/${DB_NAME}"
      V2_CONFIG_PATH: "/config/kraken_paper_trading.yaml"
      STREAMLIT_SERVER_ADDRESS: "0.0.0.0"
      STREAMLIT_SERVER_PORT: "8501"
      STREAMLIT_SERVER_HEADLESS: "true"
      STREAMLIT_BROWSER_GATHER_USAGE_STATS: "false"
    depends_on:
      db:
        condition: service_healthy
    ports:
      - "127.0.0.1:8501:8501"
    volumes:
      - /opt/bot/.env:/app/.env:ro
      - /opt/bot/logs:/app/logs:ro
      - /opt/bot/v2/kraken_paper_trading.yaml:/config/kraken_paper_trading.yaml
      - /var/run/docker.sock:/var/run/docker.sock
    deploy:
      resources:
        limits:
          memory: 128M
    restart: unless-stopped
```

Key details:
- Config mounted at `/config/` (read-write for dashboard, read-only for v2-kraken)
- Logs mounted read-only (for signal JSONL access)
- Docker socket mounted for container restart
- Port bound to localhost only (SSH tunnel access)

### 2.4 entrypoint.dashboard.sh

```bash
#!/usr/bin/env bash
set -euo pipefail

# Source .env for DB credentials
if [ -f /app/.env ]; then
  set -a; source /app/.env; set +a
fi

# Wait for postgres
for i in $(seq 1 30); do
  pg_isready -h db -p "${DB_PORT:-5432}" -U "${DB_USER:-bot_user}" -q && break
  echo "Waiting for postgres... ($i/30)"
  sleep 2
done

exec streamlit run /app/v2/dashboard/app.py
```

---

## 3. Core Modules

### 3.1 db.py — Database Layer

```python
"""Database connection and query helpers for the dashboard."""

import asyncio
import os
from datetime import datetime
from functools import lru_cache

import asyncpg
import streamlit as st


@st.cache_resource
def _get_pool() -> asyncpg.Pool:
    """Create a shared asyncpg connection pool (cached per Streamlit session)."""
    return asyncio.get_event_loop().run_until_complete(
        asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=3)
    )


def run_async(coro):
    """Run an async coroutine from synchronous Streamlit code."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def get_pool() -> asyncpg.Pool:
    return _get_pool()
```

**Portfolio replay from DB** — standalone function that mirrors `PortfolioTracker.replay_fills()`:

```python
async def compute_equity_curve(
    pool: asyncpg.Pool,
    exchange: str = "paper-kraken",
    initial_balance: float = 10000.0,
) -> list[dict]:
    """Replay all fills to build an equity curve.

    Returns list of {timestamp, cash, positions_value, total_equity} dicts.
    """
    rows = await pool.fetch(
        "SELECT timestamp, symbol, side, price, qty, fee "
        "FROM v2_fills WHERE exchange = $1 ORDER BY timestamp ASC",
        exchange,
    )
    # FIFO replay: track cash and positions, emit a data point per fill
    ...
```

### 3.2 config_io.py — Config Read/Write

```python
"""YAML config read/write with backup, diff, and validation."""

import os
import shutil
from datetime import datetime
from pathlib import Path

import yaml


def load_config(path: str | None = None) -> dict:
    """Load the YAML config file. Uses V2_CONFIG_PATH env if path not given."""
    path = path or os.environ["V2_CONFIG_PATH"]
    with open(path) as f:
        return yaml.safe_load(f)


def save_config(config: dict, path: str | None = None) -> str:
    """Save config to YAML with atomic write and timestamped backup.

    Returns the backup file path.
    """
    path = path or os.environ["V2_CONFIG_PATH"]
    # 1. Backup current file
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    backup = f"{path}.bak.{ts}"
    shutil.copy2(path, backup)
    # 2. Write to temp, then atomic rename
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        yaml.safe_dump(config, f, default_flow_style=False, sort_keys=False)
    os.replace(tmp, path)
    return backup


def diff_configs(old: dict, new: dict, prefix: str = "") -> list[str]:
    """Return human-readable list of changed keys."""
    changes = []
    all_keys = set(old.keys()) | set(new.keys())
    for key in sorted(all_keys):
        full_key = f"{prefix}.{key}" if prefix else key
        old_val = old.get(key)
        new_val = new.get(key)
        if isinstance(old_val, dict) and isinstance(new_val, dict):
            changes.extend(diff_configs(old_val, new_val, full_key))
        elif old_val != new_val:
            changes.append(f"{full_key}: {old_val!r} → {new_val!r}")
    return changes


def validate_config(config: dict) -> list[str]:
    """Return list of validation errors (empty = valid)."""
    errors = []
    required_sections = ["app", "exchange", "strategies", "risk", "execution", "storage"]
    for section in required_sections:
        if section not in config:
            errors.append(f"Missing required section: {section}")

    # Range checks for critical numeric params
    strategies = config.get("strategies", [])
    for s in strategies:
        cfg = s.get("config", {})
        if "score_buy_target" in cfg:
            v = cfg["score_buy_target"]
            if not (1.0 <= v <= 20.0):
                errors.append(f"score_buy_target={v} out of range [1.0, 20.0]")

    for r in config.get("risk", []):
        if r.get("type") == "exit_manager":
            hsp = r.get("hard_stop_pct", 0.055)
            if not (0.01 <= hsp <= 0.20):
                errors.append(f"hard_stop_pct={hsp} out of range [0.01, 0.20]")
    return errors
```

### 3.3 docker_control.py — Container Management

```python
"""Docker container control for config-triggered restarts."""

import docker


def get_container_status(name: str = "v2-kraken") -> dict:
    """Return container state, uptime, health status."""
    client = docker.from_env()
    try:
        c = client.containers.get(name)
        return {
            "status": c.status,           # "running", "exited", etc.
            "health": c.attrs["State"].get("Health", {}).get("Status", "unknown"),
            "started_at": c.attrs["State"]["StartedAt"],
        }
    except docker.errors.NotFound:
        return {"status": "not_found", "health": "unknown", "started_at": None}


def restart_container(name: str = "v2-kraken", timeout: int = 30) -> bool:
    """Restart a container. Returns True on success."""
    client = docker.from_env()
    try:
        c = client.containers.get(name)
        c.restart(timeout=timeout)
        return True
    except Exception:
        return False
```

---

## 4. Page Implementations

### 4.1 app.py — Entry Point

```python
"""BotTrader Dashboard — Streamlit entry point."""

import streamlit as st

st.set_page_config(
    page_title="BotTrader Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

report_page = st.Page("v2/dashboard/pages/report.py", title="Report", icon="📈", default=True)
health_page = st.Page("v2/dashboard/pages/health.py", title="Bot Health", icon="🩺")
config_page = st.Page("v2/dashboard/pages/config_editor.py", title="Config", icon="⚙️")

pg = st.navigation([report_page, health_page, config_page])
pg.run()
```

### 4.2 Page 1: Report (`pages/report.py`)

**Data sources**: Reuses existing collectors from `v2/plugins/observability/daily_report_v2/collectors/`.

```
┌─────────────────────────────────────────────────────────────┐
│  Time Range: [4h ▾] [24h] [7d] [30d] [All] [Custom...]     │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌─── Hero P&L ──────────────────────────────────────────┐  │
│  │           -$13.45                                     │  │
│  │   14W / 24L (36.8%)  ·  76 fills  ·  $2.14 fees      │  │
│  │   Best: SOL-USD +$3.21  ·  Worst: RIVER-USD -$4.12   │  │
│  └───────────────────────────────────────────────────────┘  │
│                                                             │
│  ┌─── Portfolio ─────────────────────────────────────────┐  │
│  │  Starting  $9,940.00    High / Low  $9,952 / $9,921   │  │
│  │  Ending    $9,926.55    Max DD      0.3%              │  │
│  │  Cash      $9,702.10    Positions   $224.45           │  │
│  └───────────────────────────────────────────────────────┘  │
│                                                             │
│  ┌─── P&L by Symbol ────────────────────────────────────┐  │
│  │  Symbol     Trades    P&L                             │  │
│  │  SOL-USD    3B / 3S   +$5.20                          │  │
│  │  BTC-USD    2B / 2S   +$1.10                          │  │
│  │  RIVER-USD  4B / 4S   -$8.30                          │  │
│  └───────────────────────────────────────────────────────┘  │
│                                                             │
│  ┌─── Trade Log ────────────────────────────────────────┐  │
│  │  Time      Symbol    Side  Price    Notional  P&L     │  │
│  │  14:23:01  SOL-USD   BUY   $142.30  $75.00   —       │  │
│  │  14:55:12  SOL-USD   SELL  $144.50  $75.80   +$1.80  │  │
│  │  ...                                                  │  │
│  └───────────────────────────────────────────────────────┘  │
│                                                             │
│  ┌─── Open Positions ───────────────────────────────────┐  │
│  │  Symbol    Qty        Entry     Cost     Unrealized   │  │
│  │  ETH-USD   0.02100    $3,240    $68.04   -$1.22      │  │
│  └───────────────────────────────────────────────────────┘  │
│                                                             │
│  ┌─── Exit Manager ─────────────────────────────────────┐  │
│  │  Hard stops: 10   Trailing: 11   Stale: 3            │  │
│  │  Signal exits: 5  Activations: 15                     │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

**Collector reuse map:**

| Section | Collector Function | Import Path |
|---------|-------------------|-------------|
| Hero P&L | `collect_pnl(pool, start, end, exchange)` | `v2.plugins.observability.daily_report_v2.collectors.pnl` |
| Portfolio | `compute_equity_curve()` (new, in `db.py`) | `v2.dashboard.db` |
| P&L by Symbol | `PnLSummary.by_symbol` (from collect_pnl) | same as Hero |
| Trade Log | `collect_trade_log(pool, start, end, exchange)` | `...collectors.trade_log` |
| Open Positions | `collect_positions(pool, exchange, last_prices)` | `...collectors.positions` |
| Exit Manager | `collect_exit_stats_from_db(pool, start, end, exchange)` | `...collectors.exit_events` |
| Trade Stats | `collect_trade_stats(pool, start, end, exchange)` | `...collectors.trade_stats` |

**Caching**: Use `@st.cache_data(ttl=60)` on each collector call so the dashboard doesn't hammer the DB on every rerun. The 60-second TTL means data refreshes at most once per minute.

**Async bridge**: Each collector is async. Wrap in `run_async()` from `db.py`:
```python
pnl = run_async(collect_pnl(pool, start, end, "paper-kraken"))
```

### 4.3 Page 2: Bot Health (`pages/health.py`)

```
┌─────────────────────────────────────────────────────────────┐
│  Bot Health                                                 │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌─── Status ───────────────────────────────────────────┐  │
│  │  v2-kraken: 🟢 Running (healthy)  Uptime: 4d 12h     │  │
│  │  Heartbeat: 8s ago                                    │  │
│  │  Last trade: 23 min ago                               │  │
│  └───────────────────────────────────────────────────────┘  │
│                                                             │
│  ┌─── Activity (24h) ───────────────────────────────────┐  │
│  │  Active symbols: 16   Signals: 342 (112B / 230S)     │  │
│  │  Fills: 18 (8B / 10S)   Fees: $0.54                  │  │
│  └───────────────────────────────────────────────────────┘  │
│                                                             │
│  ┌─── Risk Events (24h) ────────────────────────────────┐  │
│  │  Vetoes: 24   Circuit breaker: 0   Stale cancels: 3  │  │
│  └───────────────────────────────────────────────────────┘  │
│                                                             │
│  ┌─── Equity Curve ─────────────────────────────────────┐  │
│  │  [Plotly line chart: equity over time from fills]     │  │
│  │  $10,000 ─────────╲──────╱──────╲─── $9,926          │  │
│  └───────────────────────────────────────────────────────┘  │
│                                                             │
│  ┌─── Recent Trades ────────────────────────────────────┐  │
│  │  (last 20 fills, compact table)                       │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

**Data sources:**
- Container status → `docker_control.get_container_status()`
- Heartbeat → `os.path.getmtime("/app/logs/v2_kraken/heartbeat")`
- Activity/Risk → `collect_trade_stats()` + `collect_exit_stats_from_db()` for 24h window
- Signals → `collect_signals()` from JSONL file
- Equity curve → `compute_equity_curve()` from `db.py`, rendered with Plotly
- Recent trades → `SELECT * FROM v2_fills ORDER BY timestamp DESC LIMIT 20`

### 4.4 Page 3: Config Editor (`pages/config_editor.py`)

```
┌─────────────────────────────────────────────────────────────┐
│  Config Editor                                              │
│  ⚠ Changes require a bot restart to take effect             │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ▸ Strategy (composite_scoring)                             │
│  ┌───────────────────────────────────────────────────────┐  │
│  │  Score buy target     [5.5    ]                       │  │
│  │  Score sell target    [5.5    ]                       │  │
│  │  Min indicators       [3      ]                       │  │
│  │  Cooldown bars        [2      ]                       │  │
│  │  ADX gate             [✓]  ADX threshold  [20.0  ]    │  │
│  │  Volume confirm buy   [✓]  Threshold      [0.7   ]    │  │
│  │  Require trend        [✓]                             │  │
│  │  ROC 20m momentum     [ ]                             │  │
│  │  ROC 24h momentum     [ ]                             │  │
│  └───────────────────────────────────────────────────────┘  │
│                                                             │
│  ▸ Exit Manager                                             │
│  ┌───────────────────────────────────────────────────────┐  │
│  │  Hard stop mode       [atr ▾]                         │  │
│  │  Hard stop fallback % [5.5   ]                        │  │
│  │  Hard stop ATR mult   [3.0   ]                        │  │
│  │  Hard stop min %      [5.5   ]  max %  [8.0   ]      │  │
│  │  Trailing activation  [2.0   ] %                      │  │
│  │  Trailing mode        [atr ▾]                         │  │
│  │  Trailing ATR mult    [2.0   ]                        │  │
│  │  Max hold hours       [48    ]                        │  │
│  │  Stale exit only if   [✓] negative P&L                │  │
│  └───────────────────────────────────────────────────────┘  │
│                                                             │
│  ▸ Execution                                                │
│  ┌───────────────────────────────────────────────────────┐  │
│  │  Default notional $   [75    ]                        │  │
│  │  Score notional $     [75    ]                        │  │
│  │  Score high notional  [150   ]                        │  │
│  │  Buy order TTL sec    [600   ]                        │  │
│  │  Min order $          [10    ]                        │  │
│  └───────────────────────────────────────────────────────┘  │
│                                                             │
│  ▸ Risk                                                     │
│  ┌───────────────────────────────────────────────────────┐  │
│  │  Circuit breaker: max losses [5] in [30] min          │  │
│  │  Large loss threshold $  [50]                         │  │
│  │  Perf filter win rate    [0.30]                       │  │
│  │  Perf filter min trades  [3   ]                       │  │
│  └───────────────────────────────────────────────────────┘  │
│                                                             │
│  ┌───────────────────────────────────────────────────────┐  │
│  │  Changes:                                              │  │
│  │    hard_stop_pct: 0.055 → 0.06                        │  │
│  │    trailing_activation_pct: 0.02 → 0.025              │  │
│  │                                                       │  │
│  │  [Save Config]  [Restart v2-kraken]                   │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

**Implementation details:**

Config params are organized into 4 expander sections. Each param renders the appropriate widget based on its type:
- `bool` → `st.checkbox()`
- `int` → `st.number_input(step=1)`
- `float` → `st.number_input(step=0.001)` or `st.slider()` for bounded ranges
- `str` enum → `st.selectbox()` (e.g., `hard_stop_mode: ["fixed", "atr"]`)

**Save flow:**
1. User edits values → Streamlit session state tracks changes
2. Diff shown live at the bottom (old vs new for each changed field)
3. "Save Config" → `config_io.save_config()` (creates `.bak` file, atomic write)
4. "Restart v2-kraken" → `docker_control.restart_container()` with confirmation dialog
5. After restart, poll heartbeat file to confirm bot came back up

**Important constraint**: The YAML must be mounted as a volume (not baked into the image) for this to work. The v2-kraken service needs the config volume mount added to `docker-compose.aws.yml`.

---

## 5. Collector Reuse Strategy

The daily_report_v2 collectors are **pure async functions** that take an `asyncpg.Pool` and time range. They can be imported and called directly from the dashboard without the event bus or bot running.

| Collector | Reusable? | Notes |
|-----------|-----------|-------|
| `pnl.py` → `collect_pnl()` | Yes, direct | Returns PnLSummary with by_symbol breakdown |
| `trade_stats.py` → `collect_trade_stats()` | Yes, direct | Buy/sell counts, volume, fees |
| `trade_log.py` → `collect_trade_log()` | Yes, direct | Individual fills with FIFO P&L |
| `positions.py` → `collect_positions()` | Yes, partial | Pass `last_prices={}` (no live tickers) |
| `exit_events.py` → `collect_exit_stats_from_db()` | Yes, direct | Hard/soft/trailing from fill metadata |
| `signals.py` → `collect_signals()` | Yes, direct | Reads JSONL file |
| `portfolio_tracker.py` | No — in-memory | Rebuild as `compute_equity_curve()` in `db.py` |
| `risk_events.py` | No — in-memory | Approximate from fill metadata + exit stats |

The only new data logic needed is the equity curve computation in `db.py`, which replays fills to build a time-series of portfolio value.

---

## 6. Config Volume Mount Strategy

Currently, the YAML config is baked into the v2-kraken image via `COPY . /app`. For config editing to work without a full `--build`:

**Change:** Mount the config file as a Docker volume on v2-kraken:
```yaml
v2-kraken:
  volumes:
    - /opt/bot/v2/kraken_paper_trading.yaml:/app/v2/kraken_paper_trading.yaml:ro
```

**Effect:** `docker restart v2-kraken` now picks up the edited YAML (no rebuild needed). The baked-in copy is shadowed by the volume mount.

**Risk:** If the host file is deleted or corrupted, the container fails to start. Mitigation: `config_io.save_config()` always creates a timestamped backup before writing.

---

## 7. Security

- **Network**: Port 8501 bound to `127.0.0.1` only. Not reachable from the internet.
- **Access**: SSH tunnel required: `ssh -L 8501:localhost:8501 bottrader-aws`
- **Docker socket**: Grants host-level container control. Acceptable because:
  - Dashboard container runs as non-root (`appuser`)
  - Only used for `container.restart()`, not arbitrary commands
  - Dashboard is only accessible via SSH (requires your SSH key)
- **Future option**: Add Streamlit's built-in password auth if needed (`st.secrets` or custom login page)

---

## 8. Testing Plan

### Unit Tests (`v2/tests/test_dashboard/`)

| Test File | What It Covers |
|-----------|---------------|
| `test_config_io.py` | load/save round-trip, diff detection, validation errors, backup creation |
| `test_db.py` | `compute_equity_curve()` against known fill sequences |
| `test_docker_control.py` | Mock Docker SDK, verify restart logic |

### Integration Tests

1. **DB + Dashboard only**: `docker compose up db dashboard` — verify pages load with empty/real data
2. **Config round-trip**: Edit config via dashboard → verify YAML changed → restart v2-kraken → verify new params in bot logs
3. **Report accuracy**: Compare dashboard P&L to `python -m v2 report` for same time range

### Pre-deploy Local Test

```bash
docker compose -f docker-compose.aws.yml build dashboard
docker compose -f docker-compose.aws.yml up -d db dashboard
open http://localhost:8501
```

---

## 9. Migration Plan

### Phase 1: Dashboard + Email (parallel)
- Deploy dashboard service
- Email reports continue as-is
- Validate dashboard data matches emails for the same periods
- Test config editing with a minor, safe parameter change

### Phase 2: Disable Email
- Remove `email` section from daily_report_v2 config in YAML
- Keep the observer running (it still drives the signal_comparison log)
- OR remove the observer entirely if signal_comparison is a separate observer

### Phase 3: Cleanup
- Remove SMTP env vars from `.env` and docker-compose
- Remove email delivery code (optional — no harm leaving it)
- Remove daily_report_v2 observer from config

---

## 10. Deployment Steps

```bash
# 1. Push code
git push origin main

# 2. Pull on AWS
ssh bottrader-aws "cd /opt/bot && git pull origin main"

# 3. Build and start dashboard
ssh bottrader-aws "cd /opt/bot && docker compose -f docker-compose.aws.yml up -d --build dashboard"

# 4. Rebuild v2-kraken (for config volume mount change)
ssh bottrader-aws "cd /opt/bot && docker compose -f docker-compose.aws.yml up -d --build v2-kraken"

# 5. Verify
ssh bottrader-aws "docker ps"

# 6. Access via SSH tunnel
ssh -L 8501:localhost:8501 bottrader-aws
# Open http://localhost:8501
```

---

## 11. Open Questions for Review

1. **Auto-refresh**: Should the Report and Health pages auto-refresh on a timer (e.g., every 60s)? Streamlit supports `st.rerun()` with a timer.

2. **Pair discovery config**: Should the Config Editor also expose pair discovery settings (volume thresholds, shill coins, max pairs)?

3. **Signal log in DB**: The signal JSONL file grows unbounded. Should we plan to migrate signal logging to the DB (new table) for better queryability?

4. **Historical report snapshots**: Should the dashboard store periodic snapshots (like the email did) so you can compare "this week vs last week"?

5. **Alerting**: With email gone, should the dashboard have any alerting mechanism (e.g., Slack webhook for hard stops or circuit breaker trips)?
