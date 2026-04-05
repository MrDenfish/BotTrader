# AWS Deployment Guide

Complete guide for deploying and managing BotTrader v2 on AWS.

---

## Infrastructure Overview

| Component | Detail |
|-----------|--------|
| **Instance** | EC2 t3.medium (2 vCPU, 4GB RAM, 20GB root EBS) |
| **SSH alias** | `bottrader-aws` (configured in `~/.ssh/config`) |
| **Code location** | `/opt/bot` (git repository — `main` branch) |
| **Docker Compose** | `docker-compose.aws.yml` |
| **Containers** | `db` (PostgreSQL 16), `v2-kraken` (paper trading bot) |
| **Secrets** | `/opt/bot/.env` (not in git) |
| **API keys** | `/opt/bot/Config/kraken_api_info.json` (not in git) |
| **Logs** | `/opt/bot/logs/` (mounted from host) |
| **DB volume** | Docker named volume `bottrader-aws_pg_data` |

---

## Routine Code Deployment

This is the standard workflow for pushing code changes to production.

### Step 1: Push to GitHub

```bash
git push origin main
```

### Step 2: Pull on AWS

```bash
ssh bottrader-aws "cd /opt/bot && git pull origin main"
```

### Step 3: Rebuild and Restart

```bash
ssh bottrader-aws "cd /opt/bot && docker compose -f docker-compose.aws.yml up -d --build v2-kraken"
```

**CRITICAL:** Always use `--build`. The Dockerfile uses `COPY . /app` to bake code
into the image. `docker compose restart` does NOT pick up code changes — it reuses the
old image. This was a real bug (2026-02-15) where 4 days of changes were not running.

### Step 4: Verify

```bash
# Confirm correct commit is deployed
ssh bottrader-aws "cd /opt/bot && git log --oneline -3"

# Confirm containers are running
ssh bottrader-aws "cd /opt/bot && docker compose -f docker-compose.aws.yml ps"

# Watch startup logs
ssh bottrader-aws "docker logs v2-kraken --tail 30 -f"
```

### One-Liner (After Pushing)

```bash
ssh bottrader-aws "cd /opt/bot && git pull origin main && docker compose -f docker-compose.aws.yml up -d --build v2-kraken"
```

---

## Health Checks

### Quick Status Check

```bash
# Container status + uptime
ssh bottrader-aws "docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'"

# Heartbeat file (updated every 10 seconds by v2-kraken)
ssh bottrader-aws "ls -la /opt/bot/logs/v2_kraken/heartbeat"

# Most recent log lines
ssh bottrader-aws "docker logs v2-kraken --tail 20"
```

### Docker Healthcheck

The `v2-kraken` container has a built-in healthcheck that verifies the heartbeat
file is less than 5 minutes old. Check its status:

```bash
ssh bottrader-aws "docker inspect --format='{{.State.Health.Status}}' v2-kraken"
# Expected: healthy
```

### Verify Trading Activity

```bash
# Recent fills from database
ssh bottrader-aws 'docker exec db psql -U bot_user -d bot_trader_db -c "
SELECT timestamp, symbol, side, ROUND(price::numeric, 2) as price
FROM v2_fills
ORDER BY timestamp DESC LIMIT 5;
"'

# Current open positions
ssh bottrader-aws 'docker exec db psql -U bot_user -d bot_trader_db -c "
SELECT symbol, ROUND(qty::numeric, 6) as qty, ROUND(avg_entry_price::numeric, 4) as entry
FROM v2_positions WHERE qty > 0.000001;
"'

# Signal log line count (growing = strategy is running)
ssh bottrader-aws "wc -l /opt/bot/logs/v2_kraken_score_log.jsonl"
```

---

## Log Inspection

### View Live Logs

```bash
# Follow v2-kraken logs
ssh bottrader-aws "docker logs v2-kraken -f --tail 50"

# Filter for specific patterns
ssh bottrader-aws "docker logs v2-kraken 2>&1 | grep -i 'signal\|fill\|error' | tail -30"
```

### Log Configuration

- **Log driver:** `json-file` (Docker default)
- **Max size:** 50MB per file, 3 files retained (configured in `docker-compose.aws.yml`)
- **App log level:** Controlled by `LOG_LEVEL` env var (default: `INFO`)
- **Signal log:** `/opt/bot/logs/v2_kraken_score_log.jsonl` (JSONL format, all signals)
- **Heartbeat:** `/opt/bot/logs/v2_kraken/heartbeat` (updated every 10s)

### Download Logs Locally

```bash
# Copy signal log for local analysis
scp bottrader-aws:/opt/bot/logs/v2_kraken_score_log.jsonl ./

# Copy Docker logs
ssh bottrader-aws "docker logs v2-kraken > /tmp/v2-kraken.log 2>&1"
scp bottrader-aws:/tmp/v2-kraken.log ./
```

---

## Container Management

### Restart a Container

```bash
# Restart without rebuild (only for config/env changes, NOT code changes)
ssh bottrader-aws "docker restart v2-kraken"

# Restart with rebuild (for code changes — the standard deploy)
ssh bottrader-aws "cd /opt/bot && docker compose -f docker-compose.aws.yml up -d --build v2-kraken"
```

### Stop / Start

```bash
# Stop trading bot (keeps database running)
ssh bottrader-aws "docker stop v2-kraken"

# Start it again
ssh bottrader-aws "cd /opt/bot && docker compose -f docker-compose.aws.yml up -d v2-kraken"

# Stop everything
ssh bottrader-aws "cd /opt/bot && docker compose -f docker-compose.aws.yml down"
# WARNING: This stops the database too. Positions and state are safe (persistent volume).
```

### View Container Resource Usage

```bash
ssh bottrader-aws "docker stats --no-stream"
```

The `v2-kraken` container has a 512MB memory limit (set in `docker-compose.aws.yml`).

---

## Database Management

### Interactive SQL

```bash
ssh bottrader-aws "docker exec -it db psql -U bot_user -d bot_trader_db"
```

See [DATABASE_ACCESS_GUIDE.md](DATABASE_ACCESS_GUIDE.md) for query recipes and
remote access via pgAdmin/SSH tunnel.

### Database Backup

```bash
# Dump to file on AWS
ssh bottrader-aws "docker exec db pg_dump -U bot_user bot_trader_db > /tmp/bottrader_backup.sql"

# Copy to local machine
scp bottrader-aws:/tmp/bottrader_backup.sql ./
```

### Database Volume

The PostgreSQL data lives in a Docker named volume (`bottrader-aws_pg_data`), marked
`external: true` in docker-compose. This means:
- `docker compose down` does NOT delete the volume
- `docker compose down -v` would normally delete volumes, but `external: true` protects it
- To truly delete the data, you'd have to `docker volume rm bottrader-aws_pg_data` explicitly

---

## Disk Space Management

The t3.medium has a 20GB root volume. Docker images and build cache can fill it.

### Check Disk Usage

```bash
ssh bottrader-aws "df -h / && echo '---' && docker system df"
```

### Reclaim Space

```bash
# Remove unused Docker images, build cache, and stopped containers
ssh bottrader-aws "docker system prune -af"
```

This is safe to run anytime — it only removes unused resources. On 2026-02-21 this
freed 7.3GB.

### Prevent Buildup

The `.dockerignore` should exclude large directories from the build context:
- `.git/`, `.venv/`, `__pycache__/`
- `backtest/data/` (historical CSVs — large)
- `archive/`, `docs/`
- Log files

---

## Environment Configuration

### .env File Location

`/opt/bot/.env` — mounted read-only into the v2-kraken container.

### Key Variables

```bash
# ── Database ──
DB_NAME=bot_trader_db
DB_USER=bot_user
DB_PASSWORD=<secret>
DB_PORT=5432

# ── Application ──
TZ=America/Los_Angeles
LOG_LEVEL=INFO
V2_CONFIG=/app/v2/kraken_paper_trading.yaml

# ── Email Reports ──
SMTP_USERNAME=<SES SMTP credentials>
SMTP_PASSWORD=<SES SMTP credentials>
```

The entrypoint script (`docker/entrypoint/entrypoint.v2.sh`) automatically:
1. Loads `.env`
2. Constructs `DATABASE_URL` from `DB_*` variables
3. Waits for PostgreSQL to be reachable
4. Creates log directories
5. Launches `python -m v2 --config $V2_CONFIG`

### Changing Configuration

To change trading parameters (strategy, risk, execution):
1. Edit `v2/kraken_paper_trading.yaml` locally
2. Commit and deploy (standard workflow above)

To change environment variables:
1. SSH to AWS and edit `/opt/bot/.env`
2. Restart the container: `docker restart v2-kraken`
   (No `--build` needed for env-only changes)

---

## Emergency Procedures

### Rollback to Previous Commit

```bash
# View recent commits
ssh bottrader-aws "cd /opt/bot && git log --oneline -10"

# Reset to a specific commit
ssh bottrader-aws "cd /opt/bot && git checkout <commit-hash> -- ."

# Rebuild
ssh bottrader-aws "cd /opt/bot && docker compose -f docker-compose.aws.yml up -d --build v2-kraken"
```

To undo the rollback later:
```bash
ssh bottrader-aws "cd /opt/bot && git checkout main -- . && git pull origin main"
```

### Container Won't Start

```bash
# Check what went wrong
ssh bottrader-aws "docker logs v2-kraken --tail 50"

# Common causes:
# - PostgreSQL not ready → entrypoint waits 60s, then exits
# - Bad YAML config → Python traceback in logs
# - Missing API keys → connection error at startup
# - Out of memory → check `docker stats` or `dmesg | tail`
```

### Database Connection Issues

```bash
# Is PostgreSQL running?
ssh bottrader-aws "docker ps | grep db"

# Can we connect?
ssh bottrader-aws "docker exec db pg_isready -U bot_user"

# Check PostgreSQL logs
ssh bottrader-aws "docker logs db --tail 30"
```

See [AWS_POSTGRES_TROUBLESHOOTING.md](AWS_POSTGRES_TROUBLESHOOTING.md) for
Unix socket vs. TCP/IP issues.

### Kill a Stuck Container

```bash
# Graceful stop (sends SIGTERM, waits 10s, then SIGKILL)
ssh bottrader-aws "docker stop v2-kraken"

# Force kill immediately
ssh bottrader-aws "docker kill v2-kraken"

# Remove and recreate
ssh bottrader-aws "cd /opt/bot && docker compose -f docker-compose.aws.yml up -d --build v2-kraken"
```

---

## What NOT to Do

| Don't | Why |
|-------|-----|
| `rsync` code to AWS | Code must come from git. rsync creates drift between git and what's running. |
| Deploy to `~/BotTrader` | Production path is `/opt/bot`. Other locations are ignored by Docker. |
| `docker compose restart` for code changes | Restart reuses the old image. `--build` is required. |
| Edit code directly on AWS | Changes will be overwritten by the next `git pull`. |
| `docker compose down -v` | The `-v` flag deletes volumes. The `external: true` flag protects the DB volume, but don't rely on it. |
| Run `docker system prune` during active builds | Wait for builds to finish first. |

---

## First-Time Server Setup

If setting up a brand new EC2 instance from scratch:

### 1. Install Docker

```bash
sudo apt update && sudo apt install -y docker.io docker-compose-plugin
sudo usermod -aG docker ubuntu
# Log out and back in for group change to take effect
```

### 2. Clone Repository

```bash
sudo mkdir -p /opt/bot
sudo chown ubuntu:ubuntu /opt/bot
git clone <repo-url> /opt/bot
cd /opt/bot
```

### 3. Create Environment File

```bash
cp /dev/null /opt/bot/.env
# Edit .env with the required variables (see Environment Configuration above)
```

### 4. Create API Key File

```bash
# Create Config/kraken_api_info.json with your Kraken API keys
```

### 5. Create Database Volume

```bash
docker volume create bottrader-aws_pg_data
```

### 6. Start Services

```bash
cd /opt/bot
docker compose -f docker-compose.aws.yml up -d
```

### 7. Verify

```bash
docker ps
docker logs v2-kraken --tail 20
```

---

**Last Updated:** 2026-04-03
