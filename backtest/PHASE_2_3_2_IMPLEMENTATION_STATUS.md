# Phase 2.3.2 MVP Implementation Status

## Goal
Add diagnostics and gating variants without touching execution/order mechanics.

## COMPLETED ✅

### A) Config Changes (config_4h_hybrid.py)
- Added `use_ema200_slope_filter: bool = False`
- Added `use_ema50_slope_filter: bool = False`
- Added `regime_band_pct: float = 0.02`
- Added `use_conditional_viability: bool = False`
- Added `vol_min_mult_compression: float = 0.5`
- Added `compression_recent_lookback: int = 12`

### B) Engine Changes (engine_4h_hybrid.py)
- Added ema200_slope calculation (always computed)
- Added ema200_slope to 1D indicators dataframe
- Added ema200_slope to indicators_1d dict passed to strategy

### C) Strategy Initialization (strategy_4h_hybrid.py ~lines 311-318)
- Added `compression_now_mask = []`
- Added `compression_recent_6_mask = []`
- Added `compression_recent_12_mask = []`
- Added `viability_rescued_count = 0`

### D) Regime Filter Fix (strategy_4h_hybrid.py ~lines 1030-1082)
- **FIXED BUG**: Removed `ema200 > 0` no-op check
- Added price criterion with softening band logic
- Added optional ema200_slope filter
- Added optional ema50_slope filter
- Maintains backward compatibility

## REMAINING WORK 🔧

### 1) Update _check_viability_filter() ~line 1076
Need to replace current simple implementation with:

```python
def _check_viability_filter(self, indicators_4h: dict, bb_width_pct_history: list = None) -> tuple[bool, bool]:
    """
    Phase 2.3.2: Enhanced viability with conditional threshold.

    Returns:
        (viability_ok, was_rescued): viability_ok is final result, was_rescued indicates
                                     if conditional viability saved this bar
    """
    atr_pct = indicators_4h.get('atr_pct', 0)
    fee_rt = self.config.round_trip_maker_fee

    # Standard viability
    threshold_normal = self.config.vol_min_mult * fee_rt
    viability_normal = atr_pct >= threshold_normal

    if viability_normal:
        return (True, False)  # Passed without rescue

    # Phase 2.3.2: Conditional viability (compression-aware)
    if hasattr(self.config, 'use_conditional_viability') and self.config.use_conditional_viability:
        if bb_width_pct_history is not None and len(bb_width_pct_history) >= 6:
            # Check if compression_recent is True
            compression_recent = self._check_recent_compression(
                bb_width_pct_history=bb_width_pct_history,
                threshold=self.config.bb_width_pct_threshold,
                lookback=6
            )

            if compression_recent:
                threshold_compression = self.config.vol_min_mult_compression * fee_rt
                viability_compression = atr_pct >= threshold_compression

                if viability_compression:
                    return (True, True)  # Rescued by conditional viability

    return (False, False)  # Failed both
```

**ALSO UPDATE ALL CALLS to _check_viability_filter():**
- Currently: `viability_ok = self._check_viability_filter(indicators_4h)`
- New: `viability_ok, was_rescued = self._check_viability_filter(indicators_4h, bb_width_pct_history)`
- Track rescued count: `if was_rescued: self.viability_rescued_count += 1`

### 2) Add compression_now tracking in _handle_flat_state() ~line 420
After line where `compression_ok` is computed, add:

```python
# Phase 2.3.2: Track compression_now (same bar only)
bb_width_pct = indicators_4h.get('bb_width_pct', 0)
compression_now = (bb_width_pct <= self.config.bb_width_pct_threshold) if self.config.use_compression_filter else False
self.compression_now_mask.append(compression_now)

# Phase 2.3.2: Track compression_recent variants
if len(self.bb_width_history.get(symbol, [])) >= 6:
    compression_recent_6 = self._check_recent_compression(
        bb_width_pct_history=self.bb_width_history[symbol],
        threshold=self.config.bb_width_pct_threshold,
        lookback=6
    )
else:
    compression_recent_6 = False

if len(self.bb_width_history.get(symbol, [])) >= 12:
    compression_recent_12 = self._check_recent_compression(
        bb_width_pct_history=self.bb_width_history[symbol],
        threshold=self.config.bb_width_pct_threshold,
        lookback=12
    )
else:
    compression_recent_12 = False

self.compression_recent_6_mask.append(compression_recent_6)
self.compression_recent_12_mask.append(compression_recent_12)
```

### 3) Add Fee Floor Truth Print to get_statistics() ~line 1686
At START of get_statistics(), add one-time print:

```python
def get_statistics(self) -> dict:
    """Return strategy statistics"""

    # Phase 2.3.2: Fee floor truth print (ONCE per run)
    print()
    print("=" * 80)
    print("FEE FLOOR VIABILITY PARAMETERS (TRUTH LINE)")
    print("=" * 80)
    print(f"  maker_fee:              {self.config.maker_fee:.4f} ({self.config.maker_fee*100:.2f}%)")
    print(f"  taker_fee:              {self.config.taker_fee:.4f} ({self.config.taker_fee*100:.2f}%)")
    print(f"  round_trip_maker_fee:   {self.config.round_trip_maker_fee:.4f} ({self.config.round_trip_maker_fee*100:.2f}%)")
    print(f"  vol_min_mult:           {self.config.vol_min_mult:.2f}")
    print(f"  viability_floor_atr_pct: {self.config.vol_min_mult * self.config.round_trip_maker_fee:.4f} ({(self.config.vol_min_mult * self.config.round_trip_maker_fee)*100:.2f}%)")

    if hasattr(self.config, 'use_conditional_viability') and self.config.use_conditional_viability:
        print(f"  vol_min_mult_compression: {self.config.vol_min_mult_compression:.2f}")
        print(f"  conditional_floor_atr_pct: {self.config.vol_min_mult_compression * self.config.round_trip_maker_fee:.4f} ({(self.config.vol_min_mult_compression * self.config.round_trip_maker_fee)*100:.2f}%)")
        print(f"  bars_rescued:           {self.viability_rescued_count}")

    print("=" * 80)
    print()

    # Phase 2.3.1: Compute decomposition from collected masks
    self._compute_decomposition()

    # ... rest of existing method
```

### 4) Add Universe-Level Diagnostics to print_enhanced_diagnostics() ~line 1800
After existing gate audit section, add new section:

```python
# Phase 2.3.2: UNIVERSE-LEVEL DIAGNOSTIC COUNTS
lines.append("=" * 80)
lines.append("UNIVERSE-LEVEL DIAGNOSTIC COUNTS (Phase 2.3.2)")
lines.append("=" * 80)
lines.append("")

import numpy as np

total_bars = len(self.regime_ok_mask)
regime_ok_arr = np.array(self.regime_ok_mask)
viability_ok_arr = np.array(self.viability_ok_mask)
base_ok_arr = regime_ok_arr & viability_ok_arr
setup_ok_arr = np.array(self.setup_ok_mask)
compression_now_arr = np.array(self.compression_now_mask)
compression_recent_6_arr = np.array(self.compression_recent_6_mask)
compression_recent_12_arr = np.array(self.compression_recent_12_mask)

# ALL bars universe
lines.append(f"ALL 4h bars (N={total_bars}):")
lines.append(f"  setup_ok:               {int(np.sum(setup_ok_arr))} ({100*np.sum(setup_ok_arr)/total_bars:.1f}%)")
lines.append(f"  compression_now:        {int(np.sum(compression_now_arr))} ({100*np.sum(compression_now_arr)/total_bars:.1f}%)")
lines.append(f"  compression_recent_6:   {int(np.sum(compression_recent_6_arr))} ({100*np.sum(compression_recent_6_arr)/total_bars:.1f}%)")
lines.append(f"  compression_recent_12:  {int(np.sum(compression_recent_12_arr))} ({100*np.sum(compression_recent_12_arr)/total_bars:.1f}%)")
lines.append(f"  setup & compression_now: {int(np.sum(setup_ok_arr & compression_now_arr))} ({100*np.sum(setup_ok_arr & compression_now_arr)/total_bars:.1f}%)")
lines.append("")

# REGIME_OK universe
regime_ok_count = int(np.sum(regime_ok_arr))
if regime_ok_count > 0:
    lines.append(f"REGIME_OK bars (N={regime_ok_count}):")
    lines.append(f"  setup_ok:               {int(np.sum(setup_ok_arr & regime_ok_arr))} ({100*np.sum(setup_ok_arr & regime_ok_arr)/regime_ok_count:.1f}%)")
    lines.append(f"  compression_now:        {int(np.sum(compression_now_arr & regime_ok_arr))} ({100*np.sum(compression_now_arr & regime_ok_arr)/regime_ok_count:.1f}%)")
    lines.append(f"  compression_recent_6:   {int(np.sum(compression_recent_6_arr & regime_ok_arr))} ({100*np.sum(compression_recent_6_arr & regime_ok_arr)/regime_ok_count:.1f}%)")
    lines.append(f"  setup & compression_now: {int(np.sum(setup_ok_arr & compression_now_arr & regime_ok_arr))} ({100*np.sum(setup_ok_arr & compression_now_arr & regime_ok_arr)/regime_ok_count:.1f}%)")
    lines.append("")

# BASE_OK universe (regime + viability)
base_ok_count = int(np.sum(base_ok_arr))
if base_ok_count > 0:
    lines.append(f"BASE_OK bars (regime + viability) (N={base_ok_count}):")
    lines.append(f"  setup_ok:               {int(np.sum(setup_ok_arr & base_ok_arr))} ({100*np.sum(setup_ok_arr & base_ok_arr)/base_ok_count:.1f}%)")
    lines.append(f"  compression_now:        {int(np.sum(compression_now_arr & base_ok_arr))} ({100*np.sum(compression_now_arr & base_ok_arr)/base_ok_count:.1f}%)")
    lines.append(f"  compression_recent_6:   {int(np.sum(compression_recent_6_arr & base_ok_arr))} ({100*np.sum(compression_recent_6_arr & base_ok_arr)/base_ok_count:.1f}%)")
    lines.append(f"  setup & compression_now: {int(np.sum(setup_ok_arr & compression_now_arr & base_ok_arr))} ({100*np.sum(setup_ok_arr & compression_now_arr & base_ok_arr)/base_ok_count:.1f}%)")
    lines.append(f"  setup & compression_recent_6: {int(np.sum(setup_ok_arr & compression_recent_6_arr & base_ok_arr))} ({100*np.sum(setup_ok_arr & compression_recent_6_arr & base_ok_arr)/base_ok_count:.1f}%)")
    lines.append(f"  setup & compression_recent_12: {int(np.sum(setup_ok_arr & compression_recent_12_arr & base_ok_arr))} ({100*np.sum(setup_ok_arr & compression_recent_12_arr & base_ok_arr)/base_ok_count:.1f}%)")
    lines.append("")

# Annualized projections (assuming 180d window)
days = 180  # Adjust if different
scale_factor = 365.0 / days

lines.append("ANNUALIZED PROJECTIONS (180d → 365d):")
lines.append(f"  setup_only:                     {int(np.sum(setup_ok_arr & base_ok_arr & ~compression_now_arr) * scale_factor)}/year")
lines.append(f"  setup & compression_now:        {int(np.sum(setup_ok_arr & base_ok_arr & compression_now_arr) * scale_factor)}/year")
lines.append(f"  setup & compression_recent_6:   {int(np.sum(setup_ok_arr & base_ok_arr & compression_recent_6_arr) * scale_factor)}/year")
lines.append(f"  setup & compression_recent_12:  {int(np.sum(setup_ok_arr & base_ok_arr & compression_recent_12_arr) * scale_factor)}/year")

lines.append("")
lines.append("=" * 80)
lines.append("")
```

### 5) Update test_compression_on_off_v2.py calls
Update where `get_phase2_3_roc_baseline_config()` is used:
- No changes needed! Config already has new params with safe defaults

## Testing Checklist

After completing remaining work:

1. ✅ Run `python3 test_compression_on_off_v2.py`
2. ✅ Verify fee floor truth line prints ONCE with correct values
3. ✅ Verify universe-level counts sum correctly
4. ✅ Verify compression variants (now/recent_6/recent_12) differ
5. ✅ Test with `use_conditional_viability=True` and verify rescued count > 0
6. ✅ Ensure execution logic unchanged (same trades as before on baseline config)
7. ✅ Verify A/B invariants (OFF and ON have same total/regime/setup counts)

## File Locations

- `config_4h_hybrid.py`: lines ~45-51, ~76-79, ~112-114
- `engine_4h_hybrid.py`: lines ~184-189, ~224, ~307
- `strategy_4h_hybrid.py`:
  - Initialization: ~lines 311-318
  - Regime filter: ~lines 1030-1082
  - Viability filter: ~line 1076 (NEEDS UPDATE)
  - Bar processing: ~line 420 (NEEDS compression tracking)
  - Statistics: ~line 1686 (NEEDS fee floor print)
  - Diagnostics: ~line 1800 (NEEDS universe counts)

## Summary

**Completed:** Config params, engine slope calculation, regime filter fix, tracking lists initialization

**Remaining:** Viability filter enhancement (5 call sites), compression tracking in bar loop, fee floor print, universe diagnostics print

**Estimated remaining work:** ~100 lines of code across 3 locations in strategy_4h_hybrid.py
