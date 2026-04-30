# mean_reversion_v3 Redesign Experiment (2026-04-28 → 2026-04-30)

## What this is

A two-day attempt to redesign the trading strategy as a clean mean-reversion plugin (`mean_reversion_v3`) that would replace the muddled `composite_scoring`. **The result was a negative finding** — v3 underperformed composite_scoring on all three OOS sets.

The plugin code itself remains in the working tree at `v2/plugins/strategies/mean_reversion_v3/` because the plugin architecture isolates it cleanly and it is "available alternative" if a future hypothesis wants its scaffolding. **This archive contains the experiment artifacts** (configs and outputs), not the plugin code.

## What's in here

```
configs/
  backtest_v3_set_a.yaml         Round 1 v3 baseline, training set
  backtest_v3_set_b.yaml         Round 1 v3 baseline, OOS set B
  backtest_v3_set_c.yaml         Round 1 v3 baseline, OOS set C
  backtest_v3_pathA_set_a.yaml   Path A: same as Round 1 but with adx_max_threshold=999 (effectively disabled)
  backtest_v3_pathA_set_b.yaml
  backtest_v3_pathA_set_c.yaml

outputs/
  diag_v3_a/, diag_v3_b/, diag_v3_c/                       Round 1 baseline JSONLs
  diag_v3_pathA_a/, diag_v3_pathA_b/, diag_v3_pathA_c/     Path A JSONLs
```

Each `diag_*` directory contains a `diagnostic_trades.jsonl` with full per-trade entry metadata, MFE/MAE, and exit reason.

## Headline results

| Set | composite Net / WR | v3 Round 1 Net / WR | v3 Path A Net / WR |
|-----|---|---|---|
| A (training) | -$62 / 54.4% | -$18 / 22.7% | -$18 / 24.2% |
| B (OOS)      | -$22 / 64.6% | -$19 / 26.2% | -$18 / 30.5% |
| C (OOS)      | -$33 / 61.6% | -$23 / 21.6% | -$44 / 21.4% |

v3 had no entry edge — winners and losers were statistically identical at entry. Path A (dropping the ADX gate) confirmed the regime gate wasn't the issue.

## Reproduce

To re-run any variant:
```bash
cp archive/experiments/2026-04-v3-redesign/configs/backtest_v3_set_a.yaml v2/
python -m v2 -c v2/backtest_v3_set_a.yaml
# Output goes to backtest/diag_v3_a/ as configured
```

The `mean_reversion_v3` plugin must be present in `v2/plugins/strategies/mean_reversion_v3/` for these configs to work (it is, by default).

## See also

- AI memory: `memory/v3_experiment_2026-04-29.md` — distilled lessons
- AI memory: `memory/july_2026_evaluation_prep.md` — sharpened questions for July
- `docs/SYSTEM_CONTEXT.md` Changelog 2026-04-30 entry
