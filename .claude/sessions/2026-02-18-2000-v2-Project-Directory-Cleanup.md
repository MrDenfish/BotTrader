# v2 Project Directory Cleanup — 2026-02-18

## Session Overview
- **Start time:** ~20:00 UTC
- **End time:** ~20:30 UTC
- **Status:** Complete
- **Commit:** `160d355`

## Goals
- Audit the project directory for v1 code, Phase 1 bridge code, and obsolete artifacts
- Remove directories and files that are no longer relevant to v2
- Ensure backtest framework and other active tooling aren't broken by removals
- Leave a clean project structure focused on v2

## Progress

### Full Audit
Used an Explore agent to classify every top-level directory and file as v1-only, v2-only, shared, or obsolete. Checked all cross-dependencies (v2 imports, backtest imports, scripts imports) before moving anything.

### Key Finding
v2 has NO actual imports from any v1 directory — only "Ported from" comments. Safe to archive all v1 runtime code.

### Shared Infrastructure Dependencies
`Config/`, `Shared_Utils/`, `SharedDataManager/` are used by `database_manager/`, `fifo_engine/`, and shared scripts (compute_allocations, validate_allocations, backfill_*, diagnostics). These were kept to avoid breaking DB tools.

---

## Session End Summary

### Duration
~20:00 – ~20:30 UTC (~30 minutes)

### Git Summary
- **1 commit**: `160d355`
- **110 files changed** (+20 / -168 lines)
- 109 files renamed (git mv to archive/v1/)
- docker/README.md rewritten for v2

### Files Archived to `archive/v1/`

| Category | Directories/Files |
|----------|-------------------|
| v1 runtime | `webhook/`, `sighook/`, `main.py` |
| v1 features | `AccumulationManager/`, `Api_manager/`, `ProfitDataManager/`, `MarketDataManager/` |
| v1 debug | `TestDebugMaintenance/` |
| v1 reporting | `botreport/` |
| v1 SQL reports | `queries/` |
| v1 tests | `tests/` |
| v1 Docker | `Dockerfile.bot`, `entrypoint.bot.sh` |
| v1 scripts (16) | allocation_reports, cancel_phantom_orders, check_order_size_config, debug_reconciliation, deploy.sh, deploy-local.sh, diagnose_oco_blocking, diagnose_positions, investigate_missing_buys, investigate_sl_aws, reconcile_with_exchange, verify_missing_orders, verify_order_size_load, weekly_reconciliation.sh, weekly_reconciliation_aws.sh, weekly_strategy_review.sh |

### Files Removed (temp/junk)
- `single_run_compression_off.log`, `single_run_compression_on.log`
- `BotTrader v2 4h Report — *.eml` (2 files)
- `backtest_results/` (grid search CSVs)
- `Trade records for review/` (old trade CSVs)
- `environment.yml` (old conda spec)
- `requirements-report.in`, `requirements-report.txt`

### Final Project Root (20 items, down from 35)
```
CLAUDE.md            Config/              LICENSE              README.md
SharedDataManager/   Shared_Utils/        TableModels/         archive/
backtest/            data/                database/            database_manager/
docker/              docker-compose.aws.yml  docs/             fifo_engine/
logs/                pytest.ini           requirements.*       scripts/
strategies/          utils/               v2/
```

### Kept (shared infrastructure)
- `Config/` — used by database_manager, fifo_engine, scripts
- `Shared_Utils/` — used by fifo_engine, scripts
- `SharedDataManager/` — used by scripts
- `database_manager/` — used by scripts
- `fifo_engine/` — used by scripts
- `TableModels/` — used by scripts
- `strategies/` — v2 imports Phase 1 code from here
- `backtest/` — v2 imports config from here

### Task Summary
- **5/5 tasks completed, 0 remaining**
  1. Remove temp/junk files
  2. Check v1 runtime dependencies before archiving
  3. Archive v1-only directories
  4. Archive v1 runtime code
  5. Clean up any broken references (611 v2 tests passing)

### Deployment
1. Pushed `160d355` to main
2. Pulled on AWS
3. Rebuilt both v2-paper and v2-kraken containers
4. All 3 containers healthy (db, v2-paper, v2-kraken)

### What Wasn't Completed
- `Config/`, `Shared_Utils/`, `SharedDataManager/` remain at root — shared by scripts and DB tools. Could be consolidated into a `shared/` directory in a future session, but would require updating imports in ~20 files.

### Tips for Future Developers
- All v1 code is in `archive/v1/` — git history preserved via `git mv`
- `strategies/` and `backtest/` look like v1 but are v2 dependencies — don't archive
- If archiving Config/Shared_Utils/SharedDataManager later, update imports in: database_manager/database_ops.py, fifo_engine/engine.py, fifo_engine/validator.py, and ~10 scripts
- v2 tests (`v2/tests/`) are the only active test suite — `pytest.ini` still points to old `tests/` dir (now archived)
