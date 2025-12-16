# BotTrader Documentation Index

This directory contains all documentation for the BotTrader cryptocurrency trading bot project.

## Directory Structure

### 📂 active/
**Currently relevant operational documentation**
- `architecture/` - System architecture and design specifications
- `deployment/` - Deployment guides and troubleshooting
- `guides/` - User guides for logging, monitoring, and operations

### 📂 archive/
**Historical documentation (completed work)**
- `sessions/` - Completed session summaries
- `bugs-resolved/` - Resolved bug analyses and fixes
- `deprecated/` - Obsolete implementation guides
- `planning/` - Completed planning documents

### 📂 planning/
**Future work and active roadmaps**
- Next session preparation documents
- Refactoring plans
- Strategy optimization plans
- TPSL enhancement plans

### 📂 analysis/
**Performance and system analysis reports**
- Performance analysis reports
- Database maintenance analysis
- Risk & capital metrics issues
- Strategy performance tracking
- Debug logs

### 📂 reminders/
**Scheduled maintenance and future tasks**
- Time-based reminders for maintenance tasks

---

## Quick Links

### For Development
- [Architecture Deep Dive](active/architecture/ARCHITECTURE_DEEP_DIVE.md)
- [FIFO Allocations Design](active/architecture/FIFO_ALLOCATIONS_DESIGN.md)
- [Database Access Guide](active/deployment/DATABASE_ACCESS_GUIDE.md)

### For Deployment
- [AWS Deployment Checklist](active/deployment/AWS_DEPLOYMENT_CHECKLIST.md)
- [AWS PostgreSQL Troubleshooting](active/deployment/AWS_POSTGRES_TROUBLESHOOTING.md)
- [Reconciliation Setup](active/deployment/RECONCILIATION_SETUP.md)

### For Operations
- [Logging Guide](active/guides/LOGGING_PHASE1_GUIDE.md)
- [Log Evaluation Guide](active/guides/LOG_EVALUATION_GUIDE.md)
- [Quick Log Check](active/guides/QUICK_LOG_CHECK.md)

### For Next Session
- [Cash Transactions Integration](planning/NEXT_SESSION_CASH_TRANSACTIONS.md) - ⚠️ **PENDING IMPLEMENTATION**
- [Optimization Prep Tasks](planning/NEXT_SESSION_PREP_TASKS.md) - ⚠️ **ACTIVE (Eval: Jan 7, 2025)**
- [Schema Cleanup](planning/NEXT_SESSION_SCHEMA_CLEANUP.md) - ⚠️ **PENDING**
- [Reminder: Schema Cleanup](reminders/REMINDER_2025-12-29_schema_cleanup.md) - ⏰ **Due: Dec 29, 2025**

---

## Current Session Documentation

**Latest session documentation is kept in the project root for easy access:**
- `/SESSION_SUMMARY_DEC15_2025.md` - Current session summary (Dec 15, 2025)
- `/PASSIVE_MM_FIXES_SESSION.md` - PassiveOrderManager fixes documentation
- `/DYNAMIC_FILTER_DOCUMENTATION.md` - Dynamic symbol filtering guide

Once the session is complete and a new session begins, these files should be moved to `docs/archive/sessions/`.

---

## Document Lifecycle

1. **Active Documents** - Live in `active/` directory, regularly referenced and updated
2. **Planning Documents** - Live in `planning/` until work is completed
3. **Current Session** - Lives in project root during active development
4. **Archived Documents** - Moved to `archive/` when work is completed or superseded

---

**Last Updated:** December 15, 2025
**Maintained By:** BotTrader Team
