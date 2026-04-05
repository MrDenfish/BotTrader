# Backtesting

Backtesting framework documentation, strategy specifications, and test results.

The v2 backtest engine (`backtest/engine.py`) replays historical 1-minute OHLCV data through
the full v2 plugin pipeline. See [SYSTEM_CONTEXT.md](../SYSTEM_CONTEXT.md) Section 14 for an overview.

## Contents

### strategies/4h-hybrid-maker/

The 4-hour hybrid maker strategy — a Donchian breakout + compression-based entry system
with fee-multiple profit targets. Available as a v2 plugin (`hybrid_4h_maker`) but **not
the active live strategy** (composite scoring is).

| File | What It Covers |
|------|---------------|
| `strategy_hybrid_4h_fee_aware_post_only.md` | Foundational design spec — fees, timeframes, indicators, state machine, entry/exit logic |
| `phase2_spec_4h_hybrid_compression_chase.md` | Phase 2 spec — compression filter, chase entry, expanded TP targets |
| `phase2_implementation_plan_phase2_1.md` | Implementation plan with Phase 2.1 validation + hardening addendum |
| `phase2_2_patch_spec_4h_hybrid_fillrate_v5.md` | Phase 2.2 fill rate spec (v5) — retest/chase logic, repricing, state enums |
| `phase2_2_state_machine_refactor_guide.md` | Phase 2.2 state machine refactor — 6-stage implementation guide |
| `phase2_2_implementation_progress.md` | Phase 2.2 progress tracking |
| `phase2_3_optimization_plan_v4.md` | Phase 2.3 optimization plan (v4, latest) — ROC-score mode, frequency rescue, walk-forward |
| `phase2_3_task_breakdown.md` | Phase 2.3 task breakdown — 7 implementation steps with acceptance criteria |

Earlier versions of the optimization plans and implementation plans have been archived
to `docs/6-archive/backtesting-superseded/`.

### strategies/archived-strategies/

- `roc-multi-strategy-system.md` — ROC multi-strategy system (superseded by composite scoring)
- `roc_dual_atrpct_strategy_spec.md` — ROC dual ATR-PCT strategy spec

## Last Updated

2026-04-03
