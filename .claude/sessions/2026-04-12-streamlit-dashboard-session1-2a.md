# Session: Streamlit Dashboard — Session 1 + 2a
**Date:** 2026-04-12
**Duration:** ~4 hours
**Participants:** User (hobbyist programmer), Senior Project Director (via relay), QSE (Claude Code)

---

## Session Summary

Built and deployed a Streamlit dashboard as a third Docker container on the EC2 instance, replacing 4-hour email reports with an on-demand web UI. Completed Session 1 (Performance Report page, 7 panels) and Session 2a (Edge Analysis page, 6 panels). Both pages are live in production.

---

## Git Summary

**Starting commit:** `efb6803` (main)
**Ending commit:** `6f94808` (main)
**Total commits:** 11
**Files changed:** 16 (14 added, 1 modified existing code, 1 modified docs)
**Lines:** +1,446 / -23

### All Commits (chronological)
1. `66f6267` — feat: Add Streamlit dashboard — Page 1 (Performance Report)
2. `3589b1b` — feat(dashboard): complete Session 1 — simplify entrypoint, clean dead code
3. `a0f2d2f` — fix: use relative paths for st.Page() resolution
4. `32046cd` — fix: add /app to PYTHONPATH for v2.* imports
5. `a8f5ba8` — fix: set event loop for asyncpg compatibility in Streamlit
6. `17c4a95` — fix: guard observer import for dashboard context (aiohttp/jinja2)
7. `9b9b487` — fix: cached values stuck + Arrow serialization error
8. `ce75f8c` — fix: portfolio value uses all-time P&L, not period P&L
9. `262c727` — feat: Add Edge Analysis page — Session 2a
10. `0750c69` — fix: trigger fallback + histogram bin width
11. `6f94808` — docs: Update SYSTEM_CONTEXT with dashboard deployment

### Files Changed
| File | Type | Purpose |
|------|------|---------|
| `docker-compose.aws.yml` | Modified | Added dashboard service (256MB, 127.0.0.1:8501) |
| `docker/Dockerfile.dashboard` | Added | python:3.11-slim image for Streamlit |
| `docker/entrypoint/entrypoint.dashboard.sh` | Added | Minimal entrypoint (set -e + exec streamlit run) |
| `v2/dashboard/__init__.py` | Added | Package marker |
| `v2/dashboard/app.py` | Added | Streamlit entry point, 4-page navigation |
| `v2/dashboard/db.py` | Added | asyncpg pool + async-to-sync bridge for Streamlit |
| `v2/dashboard/prices.py` | Added | Kraken public REST ticker for live unrealized P&L |
| `v2/dashboard/trades.py` | Added | FIFO round-trip matcher linking buy/sell metadata |
| `v2/dashboard/requirements.txt` | Added | streamlit, plotly, asyncpg, pyyaml, pandas, requests |
| `v2/dashboard/pages/__init__.py` | Added | Package marker |
| `v2/dashboard/pages/report.py` | Added | Page 1: Performance Report (7 panels) |
| `v2/dashboard/pages/edge_analysis.py` | Added | Page 2: Edge Analysis (6 panels) |
| `v2/dashboard/pages/health.py` | Added | Stub: "Coming soon" |
| `v2/dashboard/pages/config_editor.py` | Added | Stub: "Coming soon" |
| `v2/plugins/observability/daily_report_v2/__init__.py` | Modified | try/except guard for dashboard import compatibility |
| `docs/SYSTEM_CONTEXT.md` | Modified | Dashboard architecture, deployment, observability sections |

### Branch Status
- `main` — all dashboard code deployed from here
- `feature/streamlit-dashboard` — has edge analysis spec for Director review (behind main)
- Spec file: `docs/5-planning/dashboard-edge-analysis-spec.md` (on feature branch + locally)

### Final Git Status
- Working tree clean (only pre-existing untracked files: backtest logs, email files, etc.)

---

## Task Summary

**Completed: 11 tasks**
1. Create Dockerfile.dashboard and entrypoint script
2. Create v2/dashboard core modules (__init__, requirements, db, prices)
3. Create Streamlit app.py and page stubs
4. Build Page 1: Performance Report (report.py — all 7 panels)
5. Modify docker-compose.aws.yml (add dashboard service)
6. Replace entrypoint.dashboard.sh with minimal version (Director review)
7. Remove dead _get_exit_stats code from report.py (Director review)
8. Verify EXCHANGE string against live database (confirmed "paper-kraken")
9. Build v2/dashboard/trades.py — RoundTrip matcher
10. Build edge_analysis.py — all 6 panels
11. Update app.py navigation for Edge Analysis

**Remaining: 0 tasks**

---

## Key Accomplishments

### Session 1 — Performance Report (Page 1)
- 7 panels: date range selector, hero P&L metrics, portfolio summary (all-time), P&L by symbol, exit reason breakdown (dynamic from trade log), open positions with live Kraken prices, scrollable trade log
- Portfolio value always uses all-time realized P&L regardless of date range selector
- Live unrealized P&L from Kraken public Ticker API with graceful degradation
- 60-second cache on all queries via @st.cache_data

### Session 2a — Edge Analysis (Page 2)
- 6 panels: weekly gross/net P&L trend (fee drag visualization), exit reason P&L stacked bar, hard stop rate trend vs 22% backtest baseline, avg trade metrics table, P&L distribution histogram ($0.50 bins), peak capture scatter for trailing stops
- FIFO round-trip matcher (`trades.py`) links buy metadata (score, indicators, ADX, RVOL, ATR percentile) to sell outcomes (exit reason, P&L, peak price)
- Trigger filter defaults to score/score_high — roc_momo excluded by default with caption when opted in

### Infrastructure
- Dashboard runs as 3rd Docker container (256MB limit, ~124MB actual)
- SSH tunnel only (127.0.0.1:8501)
- EC2 disk cleaned from 90% to 76% (pruned Docker cache + removed backtest CSVs from server)

### Documentation
- Edge Analysis spec written for Director review (`docs/5-planning/dashboard-edge-analysis-spec.md`)
- SYSTEM_CONTEXT.md fully updated (architecture, deployment, observability, changelog)
- Memory files updated for next session continuity

---

## Problems Encountered and Solutions

### Runtime Errors (5 fixes after initial deploy)

| # | Error | Root Cause | Fix |
|---|-------|-----------|-----|
| 1 | `Unable to create Page. The file report.py could not be found` | `st.Page()` resolves relative to script file, not CWD | Changed to `pages/report.py` (relative paths) |
| 2 | `No module named 'v2'` | `streamlit run` doesn't set PYTHONPATH like `python -m` | Added `export PYTHONPATH="/app"` in entrypoint |
| 3 | `There is no current event loop in thread 'ScriptRunner.scriptThread'` | asyncpg.create_pool() calls get_event_loop() internally; Streamlit's thread has none | `asyncio.set_event_loop(_loop)` at module import + in run_async() |
| 4 | `No module named 'aiohttp'` | Package __init__.py imports observer which imports Slack delivery (aiohttp) and HTML renderer (jinja2) | try/except guard in daily_report_v2/__init__.py |
| 5 | Values stuck across time range changes + Arrow serialization error | Streamlit ignores `_`-prefixed params in cache keys; em-dash strings in numeric columns | Renamed `_start`/`_end` to `start`/`end`; replaced "—" with None |

### Other Issues
- **Portfolio value reset**: Showed $10K + period P&L instead of $10K + all-time P&L. Fixed by fetching separate all-time P&L for portfolio panel.
- **EC2 disk full**: Docker build cache filled 20GB disk. Pruned ~3.6GB + removed 505MB backtest CSVs.
- **Soft stop investigation**: Director flagged 30 soft_stop exits in live data. Confirmed all from Feb 16-28 only (before config fix). No active config problem.
- **Buy/sell imbalance**: 203 sells vs 140 buys explained — cross-week FIFO matching from Feb 23 roc_momo cascade.

---

## Dependencies Added
- `v2/dashboard/requirements.txt`: streamlit>=1.30,<2.0, plotly>=5.18,<6.0, asyncpg>=0.29,<1.0, pyyaml>=6.0, pandas>=2.0,<3.0, requests>=2.31,<3.0

---

## Configuration Changes
- `docker-compose.aws.yml`: Added `dashboard` service with DATABASE_URL, STARTING_CAPITAL=10000, STREAMLIT_SERVER_* env vars, 256MB memory limit, 127.0.0.1:8501 port binding

---

## Deployment Steps Taken
1. Created feature/streamlit-dashboard branch, committed initial build
2. Merged to main (fast-forward), pushed
3. Deployed: `ssh bottrader-aws "cd /opt/bot && git pull origin main && docker compose -f docker-compose.aws.yml up -d --build dashboard"`
4. 5 runtime fix cycles (commit → push → pull → rebuild → verify logs)
5. EC2 cleanup: `docker builder prune --all -f`, `docker image prune -f`, `rm -rf backtest/data/`
6. Verified: `docker ps`, `docker logs dashboard --tail`, `docker stats`

---

## What Wasn't Completed
- **Entry Quality page (Session 2b)**: Spec written, not implemented. 6 panels: win rate by symbol, score vs outcome, indicator hit rate, indicator combos, entry condition scatters, time-of-day heatmap.
- **README.md rewrite**: Current README describes v1 (Coinbase, webhook/sighook). Deferred to next session.
- **`.idea/` cleanup**: `git rm -r --cached .idea/` — deferred.
- **EC2 log rotation**: `/opt/bot/logs/` at 2.1 GB, needs inspection and rotation.
- **`.dockerignore`**: Would reduce build context from ~500MB to ~50MB. Not yet created.
- **Feature branch sync**: Session 2a code was committed directly to main, not the feature branch.

---

## Lessons Learned / Tips for Future Developers

1. **Streamlit `@st.cache_data` ignores `_`-prefixed parameters** — they're excluded from cache keys by convention (for unhashable types). Never use `_start`, `_end` etc. as cached function params.

2. **asyncpg + Streamlit threading**: asyncpg.create_pool() calls asyncio.get_event_loop() in __init__, before your coroutine runs. You must `asyncio.set_event_loop()` BEFORE creating the pool, and again in run_async() since Streamlit may switch threads between reruns.

3. **st.Page() paths are relative to the script**, not the working directory. When running `streamlit run v2/dashboard/app.py` from `/app`, pages are at `pages/report.py` not `v2/dashboard/pages/report.py`.

4. **Package __init__.py imports cascade**: Importing a submodule (e.g., `daily_report_v2.collectors.pnl`) executes the package's `__init__.py`, which may import heavy dependencies. Use try/except guards when the dashboard needs collectors but not the full observer.

5. **Docker build context on small EC2**: `COPY . /app` copies everything including .git, backtest data, logs. Builds can fail with "no space left" even with free disk due to build cache accumulation. Always prune before builds on constrained instances. A `.dockerignore` is the proper long-term fix.

6. **Mixed types in DataFrame columns break pyarrow**: Using string placeholders (like "—") in columns that should be numeric causes Arrow serialization errors in Streamlit. Use `None` instead — pandas/Streamlit handles NaN display gracefully.

7. **Portfolio value is always all-time**: Don't compute portfolio value from period-filtered P&L. It's a "what is my account worth now?" metric that must use all-time realized P&L regardless of the selected date range.
