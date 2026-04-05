# Production

Live trading bot documentation for the BotTrader v2 production system.

For the comprehensive system overview, see [SYSTEM_CONTEXT.md](../SYSTEM_CONTEXT.md).

## Contents

- `LOCAL_DEVELOPMENT_SETUP.md` - **Start here for local development** — Python setup, running tests, backtests, and paper trading
- `OPERATIONAL_RUNBOOK.md` - **Troubleshooting and maintenance** — Real incidents, fixes, and procedures (living document)
- **deployment/** - AWS deployment and database access
  - `AWS_DEPLOYMENT_GUIDE.md` - Full deployment workflow, health checks, container management, emergency procedures
  - `DATABASE_ACCESS_GUIDE.md` - Database connection, SSH tunnels, query recipes
  - `AWS_POSTGRES_TROUBLESHOOTING.md` - Database troubleshooting (Unix socket vs TCP/IP)
- **operations/** - Day-to-day operations, logging, monitoring
  - `LOGGING_PHASE1_GUIDE.md` - Structured logging foundation (JSON, custom levels)
  - `LOG_EVALUATION_GUIDE.md` - Log analysis with jq, grep, and analysis tools
  - `QUICK_LOG_CHECK.md` - Quick log inspection commands
  - `claude-sessions_read_me.md` - Claude Code session management

## Note

v1-era documentation (architecture deep dive, order flow, FIFO design, etc.) has been
moved to `docs/6-archive/v1-production/` as of 2026-04-03. The v1 dual-container
webhook/sighook architecture was replaced by v2's plugin system in February 2026.

## Last Updated

2026-04-03
