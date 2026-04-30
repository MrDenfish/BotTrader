# BotTrader Streamlit Dashboard — Executive Overview

## What

A Streamlit web dashboard that replaces the email reports and adds live bot monitoring and configuration editing. Three pages: **Report**, **Bot Health**, and **Config Editor**.

## Why

- Email reports are view-only snapshots on a fixed 4-hour schedule
- No way to adjust strategy parameters without editing Python/YAML and redeploying
- No on-demand visibility into bot health or trade history
- The dashboard gives you a single place to monitor, review, and tune the bot

## What It Replaces

The `daily_report_v2` email observer gets retired. Everything it reports — P&L, trade log, open positions, exit stats, portfolio tracking — moves to the dashboard with the added benefit of flexible time ranges and on-demand access.

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  AWS t3.medium (EC2)                                         │
│                                                              │
│  ┌──────────┐    ┌──────────────┐    ┌───────────────────┐   │
│  │   db      │◄───│  v2-kraken   │    │    dashboard      │   │
│  │ Postgres  │◄───│  (trading)   │    │   (Streamlit)     │   │
│  │   :5432   │    │              │    │    :8501           │   │
│  └──────────┘    └──────┬───────┘    └──────┬────────────┘   │
│       ▲                  │                   │                │
│       │                  │ YAML config       │ Docker socket  │
│       └──────────────────┼───────────────────┘                │
│                          │                                    │
│  Access: ssh -L 8501:localhost:8501 bottrader-aws             │
└──────────────────────────────────────────────────────────────┘
```

- Runs as a **third Docker container** alongside `db` and `v2-kraken`
- Reads trade data directly from **PostgreSQL** (same DB the bot writes to)
- Reads/writes the **YAML config file** via a shared volume mount
- Can **restart `v2-kraken`** via Docker socket (config changes take effect)
- **Not exposed to the internet** — accessed via SSH tunnel only

## The Three Pages

### Page 1: Report (replaces email)

Everything in today's email, plus:
- **Flexible time ranges**: 4h, 24h, 7d, 30d, all-time, custom
- Hero P&L with win/loss, fees, best/worst trade
- Portfolio value (starting → ending, drawdown, cash/positions)
- P&L by symbol breakdown
- Full trade log with exit reasons
- Open positions with unrealized P&L
- Exit manager stats (hard/soft/trailing stops)

### Page 2: Bot Health

- Container up/down status (heartbeat check)
- Active symbols and signal generation rate
- Risk events (vetoes, circuit breaker trips)
- Equity curve chart
- Recent trades timeline

### Page 3: Config Editor

- Edit strategy, exit, execution, and risk parameters via form controls
- See a diff of changes before saving
- Save → writes YAML config (with automatic backup)
- Restart button → applies changes by restarting `v2-kraken`

## Resource Impact

| Resource | Current | With Dashboard | Headroom |
|----------|---------|----------------|----------|
| RAM      | ~768 MB | ~896 MB (+128M)| ~3.1 GB  |
| Disk     | ~12 GB  | ~12.3 GB       | ~7.7 GB  |
| CPU      | Light   | Negligible add | Fine     |
| DB conns | 8       | 11 (+3)        | Max 100  |

The t3.medium handles this comfortably. Streamlit is lightweight and the dashboard is accessed on-demand, not continuously.

## Security

- Dashboard binds to `127.0.0.1:8501` (localhost only, not reachable from internet)
- Access via SSH tunnel: `ssh -L 8501:localhost:8501 bottrader-aws`
- Docker socket is mounted for container restart only

## Migration Path

1. **Phase 1**: Deploy dashboard alongside email reports. Validate data matches.
2. **Phase 2**: Disable email delivery. Dashboard becomes primary.
3. **Phase 3**: Remove email observer and SMTP credentials.

## Effort Estimate

- **9 new files** (dashboard code + Dockerfile + entrypoint)
- **1 modified file** (docker-compose.aws.yml)
- Reuses existing report collectors (P&L, trade stats, positions, exit stats)
- No changes to the trading bot itself

## Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| Docker socket = host access | Container runs non-root; only used for restart; SSH-only access |
| Config write race | Atomic write (temp file + rename); bot only reads config at startup |
| Signal JSONL grows large | Acceptable for now; rotate or move to DB later |
| Portfolio equity curve approximation | Based on fills only (no intra-fill ticks) — good enough for dashboard |
