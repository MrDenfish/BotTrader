# BotTrader Documentation

Documentation for the BotTrader v2 cryptocurrency trading bot.

## Start Here

**[ONBOARDING.md](ONBOARDING.md)** — New to the project? Start here. A phased reading
list that tells you what to read and in what order.

**[SYSTEM_CONTEXT.md](SYSTEM_CONTEXT.md)** — The single-source briefing for the entire project.
Covers architecture, plugin system, signal pipeline, risk management, configuration,
deployment, backtesting, and known gotchas.

## Directory Structure

### [1-production/](1-production/) - Live Trading Operations
Local development setup, AWS deployment, database access, logging, and day-to-day operations.
- `LOCAL_DEVELOPMENT_SETUP.md` - Python setup, running tests, backtests, and paper trading
- **deployment/** - AWS deployment guide, database access, PostgreSQL troubleshooting
- **operations/** - Structured logging, log analysis, quick checks, session management

### [2-backtesting/](2-backtesting/) - Backtesting Framework
Strategy specifications and backtest documentation.
- **strategies/4h-hybrid-maker/** - 4h hybrid maker strategy specs (Phase 2.x)
- **strategies/archived-strategies/** - Superseded strategies (ROC, multi-TF)

### [3-plugin-architecture/](3-plugin-architecture/) - v2 Plugin Architecture
Plugin system design, interface contracts, and migration history.
- **design/** - Plugin ABCs, shared types, EventBus, Registry
- **migration-plan/** - v1→v2 migration history

### [4-analysis/](4-analysis/) - Analysis & Methodology
- `METHODOLOGY_AND_VALIDATION.md` - Backtest methodology, datasets, overfitting policy

### [5-planning/](5-planning/) - Planning
Active and completed planning documents.
- Streamlit dashboard specs (overview + devspec)
- **completed/** - Finished plans (TPSL, schema cleanup, optimization)

### [6-archive/](6-archive/) - Archive
Historical documentation, resolved bugs, and superseded plans.
- **v1-production/** - All v1-era production docs (archived Apr 2026)
- **v1-analysis/** - v1-era performance analyses and issue investigations
- **backtesting-superseded/** - Older versions of backtesting spec docs
- **bugs-resolved/** - Fixed bug analyses
- **sessions/** - Historical session summaries
- **deprecated/** - Deprecated features

## Quick Links

### Essential
- [System Context (start here)](SYSTEM_CONTEXT.md)
- [Local Development Setup](1-production/LOCAL_DEVELOPMENT_SETUP.md)
- [AWS Deployment Guide](1-production/deployment/AWS_DEPLOYMENT_GUIDE.md)
- [Operational Runbook](1-production/OPERATIONAL_RUNBOOK.md)
- [Database Access Guide](1-production/deployment/DATABASE_ACCESS_GUIDE.md)
- [Methodology & Validation](4-analysis/METHODOLOGY_AND_VALIDATION.md)

### Plugin System
- [Plugin Architecture Overview](3-plugin-architecture/README.md)
- [Plugin Interface Contracts](3-plugin-architecture/design/plugin-interfaces.md)

### Backtesting
- [4h Hybrid Maker Strategy](2-backtesting/strategies/4h-hybrid-maker/)
- [Backtest Framework Overview](2-backtesting/README.md)

### Planning
- [Streamlit Dashboard Spec](5-planning/streamlit-dashboard-overview.md)

---

**Last Updated:** 2026-04-03
