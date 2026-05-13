# v1 Libraries Archive

Frozen v1-era top-level libraries and their consumer scripts, moved here during the **Pass 3 + Pass 4** project audits (both 2026-05-12).

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

## Pass 4 additions (2026-05-12)

### `Config/` Python files + v1-era JSON configs

The `Config/` directory at repo root is mixed-content: it holds gitignored production API key JSON files (`kraken_api_info.json`) that v2 reads at runtime, alongside v1-era Python modules with no live callers. Pass 3 left the Python files in place because the dir couldn't move wholesale. Pass 4 archived them file-by-file, leaving only the active JSON keys in `Config/`.

- `Config/__init__.py` — package marker; eagerly imported `environment` + 5 `constants_*` modules
- `Config/config_manager.py` (34 KB) — v1 CentralConfig (Coinbase REST client, env loader, path resolution)
- `Config/config.json` — v1 multi-environment paths config (Manny / jack / do_server / docker)
- `Config/constants_core.py`, `constants_report.py`, `constants_sighook.py`, `constants_trading.py`, `constants_webhook.py` — v1 constant modules
- `Config/environment.py` — v1 env loader (referenced from `__init__.py`)
- `Config/exceptions.py` — v1 ConfigError hierarchy
- `Config/health_check.py` (12 KB) — v1 startup health check
- `Config/logging_config.py` (12 KB) — v1 structured logger setup
- `Config/tpsl_validator.py` — v1 TP/SL config validator
- `Config/validators.py` (29 KB) — v1 config schema validator
- `Config/sighook_config.json` — v1 sighook service config
- `Config/webhook_api_key.json`, `webhook_config.json`, `webhook_tb_api_key.json` — v1 webhook service configs

What remains in `Config/` after Pass 4:
- `kraken_api_info.json` (gitignored) — LIVE, read by v2 kraken_paper_trading.yaml
- `websocket_api_info.json` (gitignored) — referenced by dormant `v2/paper_trading.yaml` (Coinbase entrypoint fallback)

### v1-schema scripts (stale-but-inert)

These were left out of Pass 3 because their imports wouldn't break on archive (they only touch v1 schema at runtime, not v1 module paths). Pass 4 archives them for tidiness:

- `scripts/backfill_realized_profit_from_fifo.py` — backfills `realized_profit` in v1 `trade_records`
- `scripts/import_cash_sql.py` — quick Coinbase CSV → SQL
- `scripts/import_cash_transactions.py` — Coinbase CSV import (non-ORM variant of the Pass 3 ORM script)
- `scripts/migrations/` (whole subdir: `001_remove_deprecated_columns.py`, `__init__.py`, `README.md`) — drops deprecated columns from v1 `trade_records`; scheduled date was 2025-12-29, never run, irrelevant in v2
- `scripts/diagnostics/diagnostic_signal_quality.py` — reads v1 `scores.jsonl` format
- `scripts/diagnostics/verify_email_report.py` — verifies email vs v1 FIFO allocations
- `scripts/diagnostics/verify_report_accuracy.py` — same domain

`scripts/migrations/` is empty post-archive and was removed.

### Pass 4 verification

- Full v2 test suite: 704/704 pass (same count as Pass 3 baseline).
- `Config/` now contains only the two gitignored production JSON keys.
- No code outside this archive imports any `Config.*` Python module.

## How to reach this code if ever needed

These directories are preserved in full with `git mv` history. To inspect or temporarily revive:

```bash
git log --follow archive/v1-libs/<dir>/<file>.py
```
