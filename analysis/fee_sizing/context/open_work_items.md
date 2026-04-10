---
name: Open Work Items
description: Prioritized backlog of unstarted work, grouped by session — updated 2026-04-09
type: project
---

## ~~Priority 1 — Backtest Alignment & Validation~~ DONE (2026-04-09)

- Config aligned (commit `db46af3`), 30 parameters matched
- Overfitting validation complete: 3 independent 9-symbol sets confirm strategy is robust
- Training set: 54.4% WR, -$62 net, PF 0.57
- OOS Set B: 64.6% WR, -$22 net, PF 0.85
- OOS Set C: 61.6% WR, -$33 net, PF 0.77
- **Finding**: All sets gross profitable or near-breakeven; fees ($49-56/set) are the drag

## Priority 1 — Fee/Sizing Analysis (one session)

6. **Fee drag on profitability**
   - At $75 notional with 0.65% round-trip fees (~$0.49/trade), avg win ($1.47-$1.80) barely covers costs.
   - All 3 backtest sets are gross profitable but net negative after fees.
   - Options: increase notional sizing, target higher-conviction trades only, negotiate lower fee tier on Kraken, or reduce trade frequency.
   - This is now the #1 P&L lever — strategy logic is validated.

## Priority 2 — Exit & Risk Tuning (one session)

3. **Hard stop drag on volatile small-caps**
   - RLS-USD hit hard stop twice in one day; RIVER-USD also a repeat offender.
   - Options: tighten pair discovery filters (higher volume floor, lower spread ceiling), widen ATR hard stop ceiling for micro-caps, or add per-symbol volatility-adjusted stops.
   - Post-Apr-09 data will include order flow in `v2_orders` for better analysis.

4. **Phase 2 adaptive TTL**
   - Scale buy order TTL by ATR (high volatility = shorter TTL).
   - Phase 1 (fixed 10-min TTL) deployed 2026-03-15. Orders now persisted to DB (as of 2026-04-09) so TTL cancellation history will be available for analysis.
   - See `memory/exit_strategy_redesign.md` for plan.

## Priority 3 — Dashboard (multi-session project)

5. **Streamlit dashboard**
   - Replaces 4-hour email reports with on-demand web UI.
   - 3 pages: Report (flexible time ranges), Bot Health (live monitoring), Config Editor (YAML tuning + container restart).
   - Specs in `docs/5-planning/streamlit-dashboard-overview.md` and `streamlit-dashboard-devspec.md`.
   - Runs as third Docker container, accessed via SSH tunnel.
   - Largest effort item — likely 2-3 sessions.
