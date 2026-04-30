# Composite Scoring Baselines — Snapshot (2026-04 era)

## What this is

A frozen snapshot of `composite_scoring` backtest outputs against the 3-set OOS framework, generated during the 2026-04 work session. These are the reference baselines against which the v3 experiment, min_trend_indicators sweep, and Buy Swing weight=0 sandbox were compared.

Preserving this snapshot allows future sessions (especially the July 2026 review) to compare new experiments against a fixed historical reference, without re-running.

## What's in here

```
diagnostic_output/        Set A (training)  — diagnostic_trades.jsonl, ~9 symbols, 180 days
diagnostic_output_set_b/  Set B (OOS)
diagnostic_output_set_c/  Set C (OOS)
```

These are produced by running the canonical configs:
- `v2/backtest_diagnostic.yaml` → `diagnostic_output/`
- `v2/backtest_diagnostic_set_b.yaml` → `diagnostic_output_set_b/`
- `v2/backtest_diagnostic_set_c.yaml` → `diagnostic_output_set_c/`

Re-running any of those configs will write a fresh output to `backtest/diagnostic_output*` (the original location) — this archive copy is a frozen snapshot for cross-reference.

## Headline numbers (composite_scoring baseline)

| Set | Trades | WR | Gross | Net | PF |
|-----|---|---|---|---|---|
| A (training) | 103 | 54.4% | -$11.79 | -$62.40 | 0.57 |
| B (OOS)      | 113 | 64.6% | +$34.54 | -$21.61 | 0.85 |
| C (OOS)      | 99  | 61.6% | +$16.06 | -$32.60 | 0.77 |

OOS sets produce positive *gross* P&L (+$35, +$16) — composite_scoring has real entry edge that fees consume. This is the gross edge that v3 failed to replicate.

## See also

- AI memory: `memory/overfitting_policy.md` — original 3-set OOS validation methodology
- AI memory: `memory/datasets.md` — symbol composition of each set
- AI memory: `memory/july_2026_evaluation_prep.md` — how to use these baselines in July review
