# Buy Swing Weight=0 Sandbox (2026-04-30)

## What this is

A 3-run sandbox testing whether setting `Buy Swing` weight to 0 in `composite_scoring`'s YAML produces different trade behavior than the baseline.

**Outcome: byte-identical results to baseline.** This documented a non-obvious gotcha rather than an actual strategy change.

## What's in here

```
configs/
  backtest_swing0_set_a.yaml    Composite with weights override: Buy Swing = 0.0, all else default
  backtest_swing0_set_b.yaml
  backtest_swing0_set_c.yaml

outputs/
  diag_swing0_a/, diag_swing0_b/, diag_swing0_c/    Diagnostic JSONLs (identical to baseline outputs)
```

## The gotcha (important for future work)

In `composite_scoring/scoring.py`, Buy Swing has **three roles** and weight only controls one:

| Role | Affected by weight=0? |
|------|----|
| Adds to `buy_score` (weight × decision) | **Yes** — contributes 0 |
| Counts toward `min_indicators_required` | **No** — still increments `buy_fired` |
| Counts toward trend gate (`_TREND_INDICATORS`) | **No** — Swing firing still satisfies the gate |

So weight=0 only suppresses score contribution. Most fired signals already clear the score target without Swing's contribution, so **no signals were suppressed** by zeroing the weight. Trade behavior was unchanged across all 3 sets.

To **truly disable** an indicator: drop it from `cfg.weights` dict entirely AND remove from `_TREND_INDICATORS` set in scoring.py. This is a code change, not just a config change.

## Why preserved

The configs exist as documentation of "we tested this and it was a no-op." Future sessions tempted to "disable Buy Swing" via weight=0 will find this and avoid the dead end.

## See also

- AI memory: `memory/v3_experiment_2026-04-29.md` (the gotcha is documented there too)
- `v2/plugins/strategies/composite_scoring/scoring.py:75-110` — the relevant scoring + gate logic
