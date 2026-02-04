# Stage C A/B Test Plan: Compression-Based Runner Policy

**Date**: 2026-02-03
**Status**: Ready for execution
**Objective**: Validate whether compression-based runner policy improves risk-adjusted returns

---

## Background

**Stage C** implements adaptive runner/trailing behavior based on entry compression context:
- **Compressed entries** (compression_recent_12=True): Expect tighter ranges, faster mean reversion
- **Normal entries** (compression_recent_12=False): Expect wider ranges, stronger trends

**Hypothesis**: Compressed entries benefit from tighter trail stops and smaller runner allocations to avoid giving back profits during quick reversals.

---

## Test Configuration

### Baseline (A): Stage C OFF
```python
use_compression_runner_policy = False
# Falls back to config defaults:
runner_qty_frac = 0.20  # Via runner_qty_frac_normal
trail_mult = 2.0        # Via trail_mult_normal (or config.trail_mult if not overridden)
```

### Treatment (B): Stage C ON - Conservative Parameters
```python
use_compression_runner_policy = True

# Compressed entries: Tighter stops, smaller runner
runner_qty_frac_compressed = 0.15  # 15% runner (25% reduction from 20%)
trail_mult_compressed = 1.8        # 1.8x ATR trail (10% tighter than 2.0x)

# Normal entries: Wider stops, standard runner
runner_qty_frac_normal = 0.20      # 20% runner (baseline)
trail_mult_normal = 2.2            # 2.2x ATR trail (10% wider than 2.0x)
```

**Rationale**: Conservative 10-15% parameter differences to minimize risk during initial validation.

---

## Test Windows

| Window | Period | Bars (1m) | Purpose |
|--------|--------|-----------|---------|
| Recent | 180d   | ~258k     | Recent market regime validation |
| Long   | 365d   | ~525k     | Full cycle robustness check |

---

## Success Metrics

### Primary (Risk-Adjusted Returns)
1. **Net P&L**: Stage C should improve or maintain net P&L
2. **Sharpe Ratio**: Risk-adjusted return (if multiple trades)
3. **Max Drawdown**: Stage C should not increase drawdown

### Secondary (Trade Quality)
4. **Win Rate**: Compare compressed vs normal entry win rates
5. **TP2 Hit Rate**: Higher TP2 rate suggests better tail capture
6. **Avg Hold Time**: Compressed entries should exit faster (if hypothesis correct)
7. **MAE/MFE**: Max adverse/favorable excursion analysis

### Tertiary (Entry Context Validation)
8. **Compressed Entry Count**: Should see ~40-50% compressed entries (based on 180d test: 5/11 = 45%)
9. **Normal Entry Count**: Balance of ~50-60% normal entries

---

## Decision Criteria

### ✅ PASS (Deploy Stage C)
- Net P&L improvement ≥ 5% OR maintained within -2%
- Sharpe ratio improvement ≥ 10% OR maintained
- Max drawdown not increased by > 10%
- Compressed entries show meaningfully different behavior (win rate, hold time, or TP rates differ by ≥ 10%)

### ❌ FAIL (Keep Stage C OFF)
- Net P&L degradation > 5%
- Sharpe ratio degradation > 10%
- Max drawdown increase > 10%
- No meaningful difference between compressed/normal entries (all metrics within ± 5%)

### 🔄 ITERATE (Adjust Parameters)
- Mixed results: some metrics improve, others degrade
- Compressed vs normal difference is directionally correct but small
- Action: Widen parameter spread (e.g., runner 25%/15%, trail 2.5x/1.5x)

---

## Execution Steps

### 1. Run Baseline (Stage C OFF)
```bash
python3 run_single_180d.py  # Default config has use_compression_runner_policy=False
python3 run_single_365d.py  # (Create if needed, similar to 180d)
```

Save results:
- `results/baseline_180d_stage_c_off.txt`
- `results/baseline_365d_stage_c_off.txt`

### 2. Run Treatment (Stage C ON - Conservative)
Create test config:
```python
def get_phase2_4_stage_c_conservative_config() -> Hybrid4hConfig:
    config = get_phase2_3_roc_baseline_config()
    config.use_compression_runner_policy = True
    config.runner_qty_frac_compressed = 0.15
    config.runner_qty_frac_normal = 0.20
    config.trail_mult_compressed = 1.8
    config.trail_mult_normal = 2.2
    return config
```

Run:
```bash
python3 run_single_180d.py --config phase2_4_stage_c_conservative
python3 run_single_365d.py --config phase2_4_stage_c_conservative
```

Save results:
- `results/treatment_180d_stage_c_conservative.txt`
- `results/treatment_365d_stage_c_conservative.txt`

### 3. Compare Results
Focus on:
- **Option 1 Observability** section (state occupancy, fill rates, opportunity loss)
- **Stage C Trade Breakdown** section (compressed vs normal performance)
- **Backtest Results** section (net P&L, win rate, TP rates, hold time)

### 4. Extract Trade CSVs (Optional)
If deeper analysis needed:
```python
# Add to engine_4h_hybrid.py or create export script
df_trades = pd.DataFrame([
    {
        'entry_time': t.entry_time,
        'exit_time': t.exit_time,
        'entry_price': t.entry_price,
        'net_pnl': t.net_pnl,
        'entry_type': t.entry_type,
        'entry_compressed_recent_12': t.entry_compressed_recent_12,
        'tp1_filled': t.tp1_filled,
        'tp2_filled': t.tp2_filled,
        'bars_held': t.bars_held,
        'mfe_pct': t.mfe_pct,
        'mae_pct': t.mae_pct,
    }
    for t in strategy.trades
])
df_trades.to_csv('trades_baseline_180d.csv', index=False)
```

Compare:
```python
df_baseline = pd.read_csv('trades_baseline_180d.csv')
df_treatment = pd.read_csv('trades_treatment_180d.csv')

# Stratify by compression context
print(df_baseline.groupby('entry_compressed_recent_12')['net_pnl'].describe())
print(df_treatment.groupby('entry_compressed_recent_12')['net_pnl'].describe())
```

---

## Next Steps After A/B Test

### If PASS → Deploy Stage C
1. Update `get_phase2_3_roc_baseline_config()` with conservative Stage C parameters
2. Commit and tag: `git tag v2.4-stage-c-enabled`
3. Deploy to production (following CLAUDE.md git-based workflow)
4. Monitor live performance vs backtest expectations

### If FAIL → Keep Stage C OFF
1. Document findings in backtest results archive
2. Keep code in place (disabled by default) for future revisit
3. Focus optimization efforts elsewhere (roc_score_thresh tuning, TP target optimization)

### If ITERATE → Parameter Sweep
1. Run grid search on:
   - `runner_qty_frac_compressed`: [0.10, 0.15, 0.20, 0.25, 0.30]
   - `trail_mult_compressed`: [1.5, 1.8, 2.0, 2.2, 2.5]
2. Fix `runner_qty_frac_normal=0.20`, `trail_mult_normal=2.2` as control
3. Rank by Sharpe ratio or net P&L
4. Re-run A/B test with best parameters

---

## Validation Checklist

Before running A/B test:
- [ ] Verify baseline config has `use_compression_runner_policy=False`
- [ ] Verify treatment config has `use_compression_runner_policy=True`
- [ ] Verify differential parameters are set (compressed != normal)
- [ ] Check data availability: `data/BTC_USD.csv` has sufficient history
- [ ] Confirm 180d window: ~258k bars, processable 4h boundaries ~1078
- [ ] Confirm 365d window: ~525k bars, processable 4h boundaries ~2190

After running A/B test:
- [ ] Compare total trades (should be same count if setup logic unchanged)
- [ ] Verify compressed/normal split is ~40-60% (based on 180d: 5/11 = 45%)
- [ ] Check Stage C verification logs (🔍 markers) in treatment output
- [ ] Validate trail_mult and runner_qty_frac values in logs match config
- [ ] Review trade breakdown section for meaningful differences

---

## Notes

- **Current 180d Test Data** (Stage C OFF baseline):
  - Signals: 12, Entries: 11 (91.7% fill rate)
  - Net P&L: -$22.09
  - Win Rate: 36.4%
  - Compressed entries: 5/11 (45.5%)
  - Normal entries: 6/11 (54.5%)

- **Compressed Entry Performance** (180d baseline):
  - Win Rate: 60.0% (vs 16.7% normal)
  - Avg Net: -$0.74 (vs -$3.07 normal)
  - Hold Time: 77.5h (vs 40.3h normal)
  - **Key Insight**: Compressed entries already outperform on win rate but hold longer

- **Stage C Hypothesis Refinement**:
  - Original: Compressed = tighter trail (faster exit)
  - Data: Compressed = higher win rate + longer hold
  - **Revised**: Compressed entries may benefit from WIDER trail (let trends run) or LARGER runner (better tail capture)
  - **Alternative Test**: Invert parameters (compressed gets 25% runner / 2.5x trail, normal gets 15% / 1.8x)

---

## File References

- Config: `backtest/config_4h_hybrid.py`
- Strategy: `backtest/strategy_4h_hybrid.py`
- Engine: `backtest/engine_4h_hybrid.py`
- Test Script: `backtest/test_stage_c_runner_policy.py`
- Run Script: `backtest/run_single_180d.py`

**Contact**: See `.claude/sessions/2026-02-03-option1-observability-stage-c.md` for implementation details.
