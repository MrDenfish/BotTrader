# BotTrader Documentation

Documentation for the BotTrader cryptocurrency trading bot.

For a high-level project overview, see [BOTTRADER_OVERVIEW.md](BOTTRADER_OVERVIEW.md).

## Directory Structure

### [1-production/](1-production/) - Live Trading Bot
Production system documentation: architecture, deployment, operations, and strategy docs.
- **architecture/** - System design, FIFO accounting, dual-container model
- **strategies/** - Production signal generation and filtering
- **deployment/** - AWS deployment, database access, reconciliation
- **operations/** - Logging, testing, order flow, monitoring

### [2-backtesting/](2-backtesting/) - Backtesting Framework
Strategy backtesting documentation, specs, and results.
- **strategies/4h-hybrid-maker/** - Primary backtest strategy (Phase 2.x)
- **strategies/archived-strategies/** - Superseded strategies (ROC, multi-TF)
- **architecture/** - Backtest engine design
- **guides/** - Writing and running backtests
- **test-results/** - Backtest outputs

### [3-plugin-architecture/](3-plugin-architecture/) - Plugin System Design
Refactoring plan for plugin-based strategy and risk management architecture.
- **design/** - Plugin interface specifications
- **migration-plan/** - Phased migration from monolithic to plugin system
- **examples/** - Example plugin implementations

### [4-analysis/](4-analysis/) - Analysis & Performance
Performance tracking, feasibility studies, and issue investigations.
- **performance/** - Bot performance metrics and reports
- **feasibility/** - Feature feasibility assessments
- **issues/** - Documented issues and investigations

### [5-planning/](5-planning/) - Planning
Active and completed planning documents.
- **active/** - Current planning work
- **completed/** - Finished plans (TPSL, schema cleanup, optimization)

### [6-archive/](6-archive/) - Archive
Historical documentation, resolved bugs, and superseded plans.
- **bugs-resolved/** - Fixed bug analyses
- **sessions/** - Historical session summaries
- **analysis/** - Old analysis docs
- **planning/** - Superseded plans
- **deprecated/** - Deprecated features

## Quick Links

### Development
- [Architecture Deep Dive](1-production/architecture/ARCHITECTURE_DEEP_DIVE.md)
- [FIFO Allocations Design](1-production/architecture/FIFO_ALLOCATIONS_DESIGN.md)
- [Order Flow Documentation](1-production/operations/ORDER_FLOW_DOCUMENTATION.md)

### Deployment
- [AWS Deployment Checklist](1-production/deployment/AWS_DEPLOYMENT_CHECKLIST.md)
- [Database Access Guide](1-production/deployment/DATABASE_ACCESS_GUIDE.md)

### Backtesting
- [4h Hybrid Maker Strategy](2-backtesting/strategies/4h-hybrid-maker/)
- [Plugin Architecture Plan](3-plugin-architecture/README.md)

---

**Last Updated:** 2026-02-06
