# v1 Libraries Archive

Frozen v1-era top-level libraries and their consumer scripts, moved here during the **Pass 3** project audit (2026-05-12).

These directories had no live consumers in `v2/`, `strategies/` (Phase 1, imported by v2), `backtest/`, or anywhere else outside this archive. They were the supporting libraries for the v1 webhook/sighook/report services, which were retired and archived to `archive/v1/` in February 2026.

## Directories

| Directory | Size | Purpose |
|---|---|---|
| `Shared_Utils/` | 372K | v1 utilities (dates, logging, paths, exchange manager, scheduler, precision) |
| `SharedDataManager/` | 428K | v1 shared data layer (leader board, trade recorder, shared_data view) |
| `TableModels/` | 172K | v1 SQLAlchemy ORM models (`trade_records`, `cash_transactions`, `active_symbols`, etc.) |
| `database/` | 52K | v1 strategy snapshot manager + `migrations/` (SQL for FIFO allocations + strategy snapshots) |
| `database_manager/` | 72K | v1 DB session manager |
| `fifo_engine/` | 136K | v1 FIFO P&L allocation engine |
| `utils/` | 8K | `version.py` — git-info helper for v1 services |
| `data/` | 424K | Historical CSVs (`coinbase_usd_transactions.csv`, recent orders snapshot, env backup) |

## Scripts (moved alongside)

`scripts/` here contains scripts that imported the archived dirs above and would break otherwise:

- `backfill_trigger_metadata.py` — backfills trigger field in v1 `trade_records` using Coinbase REST
- `compute_allocations.py` — runs the FIFO engine to materialize allocations for v1 `trade_records`
- `validate_allocations.py` — validates FIFO allocation consistency in v1 schema
- `import_cash_transactions_orm.py` — imports Coinbase CSV cash transactions via `TableModels.CashTransaction`
- `diagnostics/diagnostic_performance_analysis.py` — performance diagnostic over v1 `trade_records` + `active_symbols`

## Internal coupling (at time of archive)

- `Shared_Utils/` → `Config.logging_config`
- `SharedDataManager/` → `Shared_Utils/`, `TableModels/`
- `fifo_engine/` → `Shared_Utils/`, `database_manager/`
- `database_manager/` → `Config.config_manager`
- `TableModels/`, `database/`, `utils/`, `data/` → standalone

The leftover v1-era Python files in `Config/` (`config_manager.py`, `health_check.py`, `validators.py`, `tpsl_validator.py`, `constants_*.py`, `environment.py`, `exceptions.py`, `logging_config.py`) were NOT moved in Pass 3 because the `Config/` directory also holds gitignored production API key JSON files (`kraken_api_info.json`) that v2 still reads at runtime. Those `.py` files are now orphans (no callers outside this archive) and are candidates for a future cleanup pass.

## Pass 4 candidates (NOT moved in Pass 3)

The following scripts also target v1 schema (`trade_records`, `cash_transactions`, FIFO allocations) but do not directly import the archived dirs, so they continue to live in `scripts/`. They are stale and may be archived in a future pass:

- `scripts/backfill_realized_profit_from_fifo.py` — backfills `realized_profit` in v1 `trade_records`
- `scripts/import_cash_sql.py` — quick-and-dirty Coinbase CSV → SQL
- `scripts/import_cash_transactions.py` — Coinbase CSV import (non-ORM variant)
- `scripts/migrations/001_remove_deprecated_columns.py` — drops deprecated columns from v1 `trade_records`
- `scripts/diagnostics/diagnostic_signal_quality.py` — reads v1 `scores.jsonl` format
- `scripts/diagnostics/verify_email_report.py` — verifies email vs v1 FIFO allocations
- `scripts/diagnostics/verify_report_accuracy.py` — same domain as above

## How to reach this code if ever needed

These directories are preserved in full with `git mv` history. To inspect or temporarily revive:

```bash
git log --follow archive/v1-libs/<dir>/<file>.py
```
