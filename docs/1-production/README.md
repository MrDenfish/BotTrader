# Production

Live trading bot documentation for the BotTrader production system.

## Contents

- **architecture/** - System architecture, FIFO design, dual-container architecture
  - `ARCHITECTURE_DEEP_DIVE.md` - Comprehensive system architecture overview
  - `FIFO_ALLOCATIONS_DESIGN.md` - FIFO-based cost allocation system
- **strategies/** - Production strategy documentation
  - `DYNAMIC_FILTER_DOCUMENTATION.md` - Dynamic filter and signal generation
- **deployment/** - AWS deployment, database access, reconciliation
  - `AWS_DEPLOYMENT_CHECKLIST.md` - Production deployment steps
  - `AWS_POSTGRES_TROUBLESHOOTING.md` - Database troubleshooting
  - `DATABASE_ACCESS_GUIDE.md` - Database connection and queries
  - `RECONCILIATION_SETUP.md` - Position reconciliation
- **operations/** - Day-to-day operations, logging, testing, order flow
  - `COMPREHENSIVE_TESTING_GUIDE.md` - End-to-end testing guide
  - `ORDER_FLOW_DOCUMENTATION.md` - Order lifecycle documentation
  - `ORDER_SIZING_DOCUMENTATION.md` - Position sizing logic
  - `QUICK_LOG_CHECK.md` - Quick log inspection guide
  - `DATA_COLLECTION_CONTAINER_ARCHITECTURE.md` - Data pipeline

## Quick Links

- [Architecture Deep Dive](architecture/ARCHITECTURE_DEEP_DIVE.md)
- [AWS Deployment Checklist](deployment/AWS_DEPLOYMENT_CHECKLIST.md)
- [Order Flow Documentation](operations/ORDER_FLOW_DOCUMENTATION.md)

## Last Updated

2026-02-06
