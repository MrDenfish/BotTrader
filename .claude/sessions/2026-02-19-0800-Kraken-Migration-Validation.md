# Kraken Migration Validation — 2026-02-19

## Session Overview
- **Start time:** ~08:00 UTC
- **Status:** In Progress

## Goals
- Continue validating v2-kraken paper trading
- Check logs and daily reports from overnight run
- Tune Kraken config if needed
- Verify daily_report_v2 observer is sending emails for Kraken

## Progress

### Overnight Run Check (09:17 UTC)
- All 3 containers healthy (db 2 days, v2-paper/v2-kraken 6h, started ~03:33 UTC)
- **v2-kraken data flow**: Ticker + volume caching working, 36 pairs, 626 USD pairs discovered
- **v2-kraken trade**: BUY SOL-USD @ 81.51 at 09:06 UTC (fill), phantom sell vetoes working (OP-USD, HOUSE-USD)
- **v2-kraken WS reconnect**: Successful at 08:03 UTC (per-channel monitoring detected ticker silence)
- **v2-paper**: Sent report at 08:00 UTC (04:00-08:00 period), multiple trades (SOL, HBAR, DOGE, VVV)

### Report Investigation
- **v2-kraken report NOT yet sent** — investigated and found it's working correctly:
  - No signal/fill/risk activity during 00:00-04:00 or 04:00-08:00 UTC periods (strategy warmup)
  - First activity at 08:07 UTC (sell vetoes) in the 08:00-12:00 period
  - Report will fire at 12:00 UTC boundary
- **Waiting for 12:00 UTC** to confirm report fires

### Config Issues Found & Fixed (in `.env` on AWS)
1. **`TZ=America/Los_Angeles` → `TZ=UTC`**: All log timestamps were PST, confusing for UTC-based system
2. **`LOG_LEVEL=DEBUG` → `LOG_LEVEL=INFO`**: Debug websocket messages flooding 50MB log buffer
3. Changes need container restart to take effect — waiting until after 12:00 UTC report

### New Issue Identified: Mixed v2_fills Table
- Both v2-paper (Coinbase) and v2-kraken share same `v2_fills` table with no exchange column
- Report observer's `_hydrate_portfolio()` replays ALL fills (cross-exchange contamination)
- Needs `exchange` column + filtered queries — follow-up task
