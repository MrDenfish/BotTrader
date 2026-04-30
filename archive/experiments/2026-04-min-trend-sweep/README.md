# min_trend_indicators Sweep (2026-04-28)

## What this is

A 6-run backtest comparing `composite_scoring` baseline (`min_trend_indicators=1`, the historical default) against a tightened variant (`min_trend_indicators=2`, requires 2 of MACD/ROC/Swing to fire instead of 1). This was the precursor to the v3 redesign and showed that mild regularization helps on OOS.

The new `min_trend_indicators` config field was added to `composite_scoring/config.py` during this sweep and **remains in the codebase at default 1** (no behavior change in production).

## What's in here

```
configs/
  backtest_diagnostic_tight.yaml          Set A, min_trend_indicators=2
  backtest_diagnostic_set_b_tight.yaml    Set B, min_trend_indicators=2
  backtest_diagnostic_set_c_tight.yaml    Set C, min_trend_indicators=2

outputs/
  diag_tight_a/, diag_tight_b/, diag_tight_c/    Diagnostic JSONLs
```

For the baseline (min_trend_indicators=1) outputs, see `../2026-04-baselines-snapshot/`.

## Headline results

Tight (min_trend=2) vs baseline (min_trend=1):

| Set | Δ Trades | Δ WR | Δ Net | Δ HS rate |
|-----|---|---|---|---|
| A (training) | -16 | -2.7% | **-$4.15** | +2.5 pts ⚠ |
| B (OOS)      | -8  | +1.1% | **+$8.74** | -2.1 pts |
| C (OOS)      | -7  | +0.4% | **+$3.94** | -1.1 pts |

Pattern: training degrades slightly, OOS improves — classic regularization signature, opposite of overfitting. **Mild OOS improvement, not deployed** — the absolute gain was small (~$10 / $4 net per OOS set) and the v3 work was prioritized instead.

## Why archived (not deployed)

After this sweep, attention shifted to the v3 redesign experiment which targeted a deeper architectural problem. The `min_trend_indicators=2` setting was deemed an incremental tweak unlikely to move the needle as much as a coherent redesign — though the redesign ultimately failed.

The setting could be revisited as a small standalone change after the July 2026 review, especially if the live data confirms Buy Swing's anti-predictiveness.

## Reproduce

```bash
cp archive/experiments/2026-04-min-trend-sweep/configs/backtest_diagnostic_tight.yaml v2/
python -m v2 -c v2/backtest_diagnostic_tight.yaml
```

## See also

- `v2/plugins/strategies/composite_scoring/config.py` — `min_trend_indicators: int = 1` field
- `v2/plugins/strategies/composite_scoring/scoring.py` — count-based gate logic at lines ~95-110
