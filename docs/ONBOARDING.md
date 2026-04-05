# BotTrader — Onboarding Guide

Welcome to the BotTrader project. This guide tells you what to read and in what order
to get fully up to speed.

**What this project is:** BotTrader is a containerized cryptocurrency trading bot running
on AWS. It uses a plugin-based architecture (v2) with 30 plugins across 8 categories,
trading on Kraken via WebSocket data and REST order submission. The system evaluates
8 technical indicator families, applies multi-layer risk management, and manages positions
with fee-aware exit logic — all running 24/7 in Docker containers.

The system is currently in **paper trading mode** (live market data, simulated fills)
collecting data for validation before transitioning to live trading.

---

## Phase 1: Must Read (Day One)

Read these in order. Each builds on the previous. ~1 hour total.

1. **[SYSTEM_CONTEXT.md](SYSTEM_CONTEXT.md)** (~25 min)
   The single-source briefing for the entire project. Covers why it exists, how trades
   flow through the system, the plugin architecture, risk management, configuration,
   deployment, and known gotchas.

2. **[Local Development Setup](1-production/LOCAL_DEVELOPMENT_SETUP.md)** (~10 min)
   How to clone the repo, install dependencies, run the test suite, run backtests,
   and optionally run paper trading on your local machine.

3. **[AWS Deployment Guide](1-production/deployment/AWS_DEPLOYMENT_GUIDE.md)** (~15 min)
   How the production environment works, the standard deploy workflow, health checks,
   container management, and emergency procedures.

4. **[Methodology & Validation](4-analysis/METHODOLOGY_AND_VALIDATION.md)** (~10 min)
   Backtest methodology, the 3 independent datasets, overfitting policy, and which
   strategy changes are data-derived vs. structural. Important context before
   proposing any parameter changes.

---

## Phase 2: Should Read (First Week)

These deepen understanding of the subsystems you'll be making decisions about.

5. **[Plugin Architecture Overview](3-plugin-architecture/README.md)**
   The 30-plugin directory map, how plugins are registered and discovered, and how to
   add new ones.

6. **[Plugin Interface Contracts](3-plugin-architecture/design/plugin-interfaces.md)**
   All 8 plugin ABCs with method signatures, the shared type system, event bus API,
   and a tutorial for writing new plugins.

7. **[Migration History](3-plugin-architecture/migration-plan/overview.md)**
   How v2 was built phase by phase from February through March 2026. Useful for
   understanding design decisions and what was delivered at each milestone.

8. **[Production Config](../v2/kraken_paper_trading.yaml)** (read directly)
   The actual YAML configuration the system runs with today. Every strategy parameter,
   risk threshold, and execution setting.

9. **[Strategy Parameters](../v2/plugins/strategies/composite_scoring/config.py)** (read directly)
   All 40+ tunable parameters for the composite scoring strategy, with defaults and
   inline comments explaining each one.

10. **[Database Access Guide](1-production/deployment/DATABASE_ACCESS_GUIDE.md)**
    How to connect to the production database, the v2 schema, and SQL query recipes
    for monitoring trades and positions.

---

## Phase 3: Read When Needed

Keep these bookmarked. Reach for them when a specific situation comes up.

- **[Operational Runbook](1-production/OPERATIONAL_RUNBOOK.md)** — Something is broken
  or behaving unexpectedly. 14 real incidents with symptoms, root causes, and fixes.
  Also contains maintenance procedures (reset paper trading, investigate a trade,
  export data, rotate API keys).

- **[PostgreSQL Troubleshooting](1-production/deployment/AWS_POSTGRES_TROUBLESHOOTING.md)** —
  Database connection issues.

- **[Log Evaluation Guide](1-production/operations/LOG_EVALUATION_GUIDE.md)** —
  Digging into logs with jq and grep to understand what happened.

- **[Quick Log Check](1-production/operations/QUICK_LOG_CHECK.md)** —
  One-liner log inspection commands.

- **[Backtesting Docs](2-backtesting/README.md)** — Planning changes to the 4h-hybrid strategy
  or understanding its design history. Strategy specs are in `2-backtesting/strategies/4h-hybrid-maker/`.

- **[Streamlit Dashboard Spec](5-planning/streamlit-dashboard-overview.md)** —
  When ready to build the real-time monitoring dashboard.

---

## What to Skip

Everything in `docs/6-archive/` describes the v1 system that was fully replaced in
February 2026. It's preserved for historical context but will actively mislead if
read as current documentation.

---

## Key Contacts

| Role | Who | Scope |
|------|-----|-------|
| Project Owner | Manny | Trading thesis, risk tolerance, parameter decisions, go/no-go for live trading |
| AI Development Partner | Claude Code | Architecture, implementation, analysis, documentation |

---

**Last Updated:** 2026-04-03
