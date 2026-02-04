# Phase 2.3.2 Backtest CLI Commands

Complete guide for running 4h Hybrid strategy backtests with Phase 2.3.2 diagnostics.

---

## Prerequisites

### 1. Download Historical Data

Before running any backtest, ensure you have 1-minute historical data:

```bash
cd /Users/Manny/Python_Projects/BotTrader/backtest

# Download 180 days (minimum)
python3 download_historical_data_csv.py --days 210 --symbol BTC-USD
```

**What it does**: Downloads 1-minute OHLCV data from Coinbase Pro API
**Output location**: `data/BTC-USD_1m.csv`
**Note**: Download extra days (210) to account for warmup periods

---

## A) Single 180-Day Run with Phase 2.3.2 Diagnostics

### **Compression ON** (Default)

```bash
cd /Users/Manny/Python_Projects/BotTrader/backtest
python3 run_single_180d.py --compression-on 2>&1 | tee single_run_compression_on.log
```

**What it does**:
- Runs single 180-day backtest with compression filter enabled
- Prints Phase 2.3.2 fee floor truth line
- Prints universe-level diagnostic counts (ALL/regime/base)
- Prints compression variants (now/recent_6/recent_12)
- Prints annualized projections
- Full gate audit and backtest results

**Config used**: `get_phase2_3_roc_baseline_config()` from `config_4h_hybrid.py`
- Symbol: BTC-USD (hardcoded in script)
- Timeframe: Last 180 days from data file
- Setup mode: roc_score
- Compression: bb_width_pct_threshold=30 (default)

**Output**:
- Console: Full diagnostic output
- Log file: `single_run_compression_on.log`

---

### **Compression OFF**

```bash
cd /Users/Manny/Python_Projects/BotTrader/backtest
python3 run_single_180d.py --compression-off 2>&1 | tee single_run_compression_off.log
```

**What it does**: Same as above but with `use_compression_filter=False`

---

## B) A/B Test: Compression ON vs OFF

### **Using Canonical Window**

```bash
cd /Users/Manny/Python_Projects/BotTrader/backtest
python3 test_compression_on_off_v2.py 2>&1 | tee ab_test_compression_180d.log
```

**What it does**:
- Defines single canonical 180-day window
- Runs TWO backtests using IDENTICAL timestamps:
  1. Compression OFF
  2. Compression ON
- Prints comprehensive comparison:
  - Truth lines (data validation)
  - Gate audits for both runs
  - Phase 2.3.2 diagnostics for both runs
  - Comparative analysis table
  - Conclusions and recommendations

**Config used**: `get_phase2_3_roc_baseline_config()`
- Symbol: BTC-USD (hardcoded)
- Timeframe: Last 180 days
- Setup mode: roc_score

**Output**:
- Console: Full A/B comparison output
- Log file: `ab_test_compression_180d.log`

**Key sections to review**:
1. `FEE FLOOR VIABILITY PARAMETERS (TRUTH LINE)` - Verify fee calculations
2. `UNIVERSE-LEVEL DIAGNOSTIC COUNTS` - See breakdown by universe
3. `COMPARATIVE ANALYSIS: ON vs OFF` - Direct comparison table
4. `CONCLUSIONS` - Automated interpretation of bottlenecks

---

## C) Diagnostic-Only Run (Proposed)

**STATUS**: Not currently supported natively.

### **Proposal**: Add `--diagnostics-only` flag to skip order simulation

This would be useful for fast diagnostic iteration without running full order matching logic.

#### **Minimal Implementation** (Proposed patch):

**File**: `run_single_180d.py` (add parameter)

```python
def run_single_180d(compression_enabled: bool = True, diagnostics_only: bool = False):
    """
    Args:
        diagnostics_only: If True, compute masks/gates but skip order execution
    """
    # ... existing code ...

    if diagnostics_only:
        # Compute indicators and gates only
        engine._compute_indicators(df_1m_window, df_4h, symbol)
        # Skip: engine.run()
        # Just collect masks and print diagnostics
    else:
        # Normal full backtest
        results = engine.run(df_1m_window, df_4h, symbol)
```

#### **After adding flag**:

```bash
# Fast diagnostic-only run (proposed)
python3 run_single_180d.py --diagnostics-only --compression-on
```

**Note**: This feature is NOT currently implemented. To add it:
1. Modify `run_single_180d.py` as shown above
2. Extract indicator/gate computation logic from `engine.run()` into separate method
3. Call diagnostic methods directly without order processing

---

## D) Parameter Sweep: bb_width_pct_threshold

### **Test 4 Compression Thresholds**

```bash
cd /Users/Manny/Python_Projects/BotTrader/backtest
python3 sweep_compression_threshold.py --thresholds 30,40,50,60 2>&1 | tee sweep_compression_results.log
```

**What it does**:
- Defines single canonical 180-day window (ONCE)
- Runs 4 backtests with different `bb_width_pct_threshold` values: {30, 40, 50, 60}
- Holds ALL other parameters constant
- Uses same data window for all runs
- Prints comparison table at the end

**Config used**: `get_phase2_3_roc_baseline_config()` with modified `bb_width_pct_threshold`

**Output**:
- Console: 4 separate backtest runs + comparison table
- Log file: `sweep_compression_results.log`

**Comparison table includes**:
- Signals, Trades, Net P&L, Win Rate
- Viable bar counts
- Intersection counts (setup + compression)
- Automated interpretation of best threshold

---

### **Custom Threshold Values**

```bash
# Test different thresholds
python3 sweep_compression_threshold.py --thresholds 20,35,55,70

# Test wider range
python3 sweep_compression_threshold.py --thresholds 10,30,50,70,90
```

---

## Advanced: Multi-Parameter Sweeps

For multi-dimensional parameter sweeps (e.g., varying both compression threshold AND vol_min_mult), you'll need to create a custom script.

### **Example**: 2D Sweep Script (Template)

```bash
# Create new sweep script
cat > sweep_2d_compression_viability.py << 'EOF'
#!/usr/bin/env python3
"""2D sweep: bb_width_pct_threshold × vol_min_mult"""

from sweep_compression_threshold import run_sweep  # Import base sweep
from config_4h_hybrid import get_phase2_3_roc_baseline_config

# Extend sweep logic for 2D grid...
# (Implementation left as exercise)
EOF

chmod +x sweep_2d_compression_viability.py
python3 sweep_2d_compression_viability.py
```

---

## Configuration Details

### **Config Class**: `Hybrid4hConfig` in `config_4h_hybrid.py`

All backtests use `get_phase2_3_roc_baseline_config()` which returns:

```python
Hybrid4hConfig(
    # Setup mode
    setup_mode="roc_score",
    roc_len_4h=6,          # 24h rate of change
    roc_ema_len=3,         # 12h smoothing
    roc_score_thresh=1.2,  # Momentum threshold

    # Fees
    maker_fee=0.004,       # 0.4%
    taker_fee=0.008,       # 0.8%
    round_trip_maker_fee=0.008,  # 2 × maker

    # Viability
    vol_min_mult=1.0,      # Standard threshold
    vol_min_mult_compression=0.5,  # Phase 2.3.2: Conditional viability

    # Compression
    use_compression_filter=True,
    bb_width_pct_threshold=30,     # Percentile threshold
    compression_lookback_4h=6,     # 24h lookback
    compression_recent_lookback=12, # Phase 2.3.2: 48h lookback

    # Regime (Phase 2.3.2 enhancements)
    use_ema200_slope_filter=False,  # Optional slope filter
    use_ema50_slope_filter=False,
    regime_band_pct=0.02,           # 2% softening band

    # Entry/Exit mechanics (unchanged from Phase 2.2)
    # ... (see config_4h_hybrid.py for full details)
)
```

### **Modifying Config Programmatically**

```python
from config_4h_hybrid import get_phase2_3_roc_baseline_config

# Get baseline config
config = get_phase2_3_roc_baseline_config()

# Modify parameters
config.bb_width_pct_threshold = 40
config.vol_min_mult = 1.5
config.use_conditional_viability = True

# Use modified config in backtest
engine = Hybrid4hBacktestEngine(config)
results = engine.run(df_1m, df_4h, symbol)
```

---

## Data Requirements

| Test Type | Minimum Days | Recommended Days | Reason |
|-----------|--------------|------------------|--------|
| Single run | 180 | 210 | 30-day warmup buffer |
| A/B test | 180 | 210 | Same window for ON/OFF |
| Sweep | 180 | 210 | Consistent window across runs |
| 365-day | 365 | 395 | Full year + warmup |

---

## Output Locations

All outputs print to console. Redirect with `tee` for logging:

```bash
# Save console output to file while displaying
python3 run_single_180d.py 2>&1 | tee my_test_results.log

# Run in background
python3 run_single_180d.py > my_test.log 2>&1 &

# Follow progress in real-time
tail -f my_test.log
```

**Log file naming conventions**:
- Single runs: `single_run_*.log`
- A/B tests: `ab_test_*.log`
- Sweeps: `sweep_*.log`

---

## Phase 2.3.2 Diagnostic Outputs

All scripts now include Phase 2.3.2 enhanced diagnostics:

### **1. Fee Floor Truth Line**

```
================================================================================
FEE FLOOR VIABILITY PARAMETERS (TRUTH LINE)
================================================================================
  maker_fee:              0.0040 (0.40%)
  taker_fee:              0.0080 (0.80%)
  round_trip_maker_fee:   0.0080 (0.80%)
  vol_min_mult:           1.00
  viability_floor_atr_pct: 0.0080 (0.80%)
================================================================================
```

**Purpose**: Verify fee calculations are correct (was showing 0.0060 before bug fix)

---

### **2. Universe-Level Diagnostic Counts**

```
================================================================================
UNIVERSE-LEVEL DIAGNOSTIC COUNTS (Phase 2.3.2)
================================================================================

ALL 4h bars (N=143):
  setup_ok:               28 (19.6%)
  compression_now:        4 (2.8%)
  compression_recent_6:   4 (2.8%)
  compression_recent_12:  4 (2.8%)
  setup & compression_now: 1 (0.7%)

REGIME_OK bars (N=115):
  setup_ok:               27 (23.5%)
  compression_now:        2 (1.7%)
  compression_recent_6:   2 (1.7%)
  setup & compression_now: 1 (0.9%)

BASE_OK bars (regime + viability) (N=84):
  setup_ok:               24 (28.6%)
  compression_now:        2 (2.4%)
  compression_recent_6:   2 (2.4%)
  setup & compression_now: 1 (1.2%)
  setup & compression_recent_6: 1 (1.2%)
  setup & compression_recent_12: 1 (1.2%)

ANNUALIZED PROJECTIONS (180d → 365d):
  setup_only:                     46/year
  setup & compression_now:        2/year
  setup & compression_recent_6:   2/year
  setup & compression_recent_12:  2/year
================================================================================
```

**Purpose**:
- Show gate pass rates across different universes (ALL → REGIME_OK → BASE_OK)
- Compare compression variants (same-bar vs recent lookback)
- Project annual setup frequency

---

## Troubleshooting

### **Error: Data file not found**

```
❌ ERROR: Data file not found: data/BTC-USD_1m.csv
   Please run: python3 download_historical_data_csv.py --days 210 --symbol BTC-USD
```

**Solution**: Download data first (see Prerequisites section)

---

### **Insufficient data warning**

If you see fewer bars than expected, check:
1. Data file has enough rows
2. Warmup period isn't dropping too many bars
3. No gaps in data

```bash
# Check data file
wc -l data/BTC-USD_1m.csv
head data/BTC-USD_1m.csv
tail data/BTC-USD_1m.csv
```

---

### **Very low setup frequency**

If diagnostics show < 10 setups in 180 days:

**Causes**:
- Regime filter too strict (check REGIME_OK counts)
- Viability filter too strict (check BASE_OK counts)
- Setup trigger threshold too high (check setup_ok percentages)
- Compression filter too strict (check intersection counts)

**Solutions**:
1. Review diagnostic output for bottleneck identification
2. Use sweep script to test different compression thresholds
3. Adjust `vol_min_mult` or `roc_score_thresh` in config
4. Consider using `use_conditional_viability=True`

---

## Summary of Available Scripts

| Script | Purpose | Key Flags |
|--------|---------|-----------|
| `run_single_180d.py` | Single backtest with diagnostics | `--compression-on`, `--compression-off` |
| `test_compression_on_off_v2.py` | A/B test (ON vs OFF) | None (hardcoded 180d) |
| `sweep_compression_threshold.py` | Parameter sweep | `--thresholds 30,40,50,60` |
| `download_historical_data_csv.py` | Download 1m data | `--days 210 --symbol BTC-USD` |

All scripts use config from `config_4h_hybrid.py` via `get_phase2_3_roc_baseline_config()`.

---

## Quick Start Workflow

```bash
# 1. Download data
cd /Users/Manny/Python_Projects/BotTrader/backtest
python3 download_historical_data_csv.py --days 210 --symbol BTC-USD

# 2. Run A/B test to see compression impact
python3 test_compression_on_off_v2.py 2>&1 | tee ab_test_results.log

# 3. If compression is bottleneck, sweep thresholds
python3 sweep_compression_threshold.py --thresholds 30,40,50,60 2>&1 | tee sweep_results.log

# 4. Review diagnostics and iterate
less sweep_results.log
```

---

## Next Steps

After reviewing Phase 2.3.2 diagnostics:

1. **If regime filter is bottleneck**: Consider enabling slope filters or adjusting band
2. **If viability is bottleneck**: Lower `vol_min_mult` or enable `use_conditional_viability`
3. **If setup is bottleneck**: Lower `roc_score_thresh` (see ROC-score percentile calibration)
4. **If compression is bottleneck**: Raise `bb_width_pct_threshold` or disable compression

All changes are made in `config_4h_hybrid.py` or via config object modifications in scripts.
