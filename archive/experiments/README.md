# Experiment Archive

Frozen artifacts (configs + outputs) from completed experiments. Each subdirectory has its own README explaining what was tested, what was found, and how to reproduce.

| Directory | Date | Result |
|---|---|---|
| `2026-04-baselines-snapshot/` | 2026-04 | Frozen snapshot of `composite_scoring` 3-set OOS outputs. Reference baseline. |
| `2026-04-min-trend-sweep/` | 2026-04-28 | Tested `min_trend_indicators=2` on composite. Mild OOS improvement (~$4-9 net per set). Not deployed. |
| `2026-04-v3-redesign/` | 2026-04-28 → 30 | Mean-reversion v3 redesign experiment. Negative result — v3 underperformed composite on all OOS sets. Plugin retained at `v2/plugins/strategies/mean_reversion_v3/` but not deployed. |
| `2026-04-buy-swing-weight-zero/` | 2026-04-30 | Sandbox testing Buy Swing weight=0. Documented as no-op due to scoring/gate interaction (gotcha). |

## How to use this archive

- **Reproduce an experiment:** copy the relevant config from `<experiment>/configs/` back to `v2/`, then run `python -m v2 -c v2/<config>.yaml`.
- **Compare against baseline:** `2026-04-baselines-snapshot/diagnostic_output*/diagnostic_trades.jsonl` are the frozen reference JSONLs.
- **Avoid repeating dead ends:** read each experiment's README for what was tried and what was learned.
