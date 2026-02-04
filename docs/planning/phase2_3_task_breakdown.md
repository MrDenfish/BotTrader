# Phase 2.3 Task Breakdown — Detailed Implementation Guide

**Date:** 2026-01-31
**Context:** Detailed implementation tasks for Phase 2.3 optimization plan
**Reference:** `phase2_3_optimization_plan_v2.md`

---

## Overview

Phase 2.3 implements a two-tier backtest resolution strategy (5m for optimization, 1m for validation) and adds ROC-score setup mode to increase signal diversity and reduce market regime dependency.

**Key Principles:**
1. Test ROC-score independently before combining with Donchian
2. Use 5m data for optimization speed, 1m for final validation
3. Require minimum sample size (>= 30 trades) for statistical validity
4. Stage optimization: Gates/Entry first (Stage A), Exits second (Stage B)

---

## Step 1: Data Infrastructure (5m Support)

**Goal:** Enable 5m base data for faster optimization while maintaining fill accuracy

### Task 1.1: Add 5m Data Loader
**File:** `backtest/engine_4h_hybrid.py`

**Changes:**
```python
def load_data(self, symbol: str, days: int, resolution: str = '1m') -> Optional[pd.DataFrame]:
    """
    Load OHLCV data at specified resolution (1m or 5m)

    Args:
        symbol: Trading pair (e.g., 'BTC-USD')
        days: Number of days to load
        resolution: '1m' or '5m' (default: '1m')

    Returns:
        DataFrame with OHLCV data at specified resolution
    """

    # Try multiple naming patterns
    symbol_underscore = symbol.replace('-', '_')
    csv_paths = [
        self.data_dir / f"{symbol_underscore}_{resolution}.csv",  # BTC_USD_5m.csv
        self.data_dir / f"{symbol_underscore}.csv",               # BTC_USD.csv (fallback for 1m)
    ]

    # [Implementation details...]
```

**Acceptance Criteria:**
- [ ] Can load both 1m and 5m CSV files
- [ ] Correctly filters to last N days
- [ ] Returns DataFrame with proper timestamp index
- [ ] Handles missing files gracefully with clear error messages

---

### Task 1.2: Update Engine Constructor
**File:** `backtest/engine_4h_hybrid.py`

**Changes:**
```python
def __init__(self, config: Hybrid4hConfig, data_dir: str = "backtest/data", resolution: str = '1m'):
    """
    Initialize backtest engine

    Args:
        config: Strategy configuration
        data_dir: Directory containing CSV data files
        resolution: Base data resolution ('1m' or '5m')
    """
    self.config = config
    self.data_dir = Path(data_dir)
    self.resolution = resolution  # NEW
    self.strategy = Hybrid4hStrategy(config)
```

**Acceptance Criteria:**
- [ ] Engine can be initialized with resolution parameter
- [ ] Default remains '1m' for backward compatibility
- [ ] Resolution is stored and used in data loading

---

### Task 1.3: Update run_backtest() Method
**File:** `backtest/engine_4h_hybrid.py`

**Changes:**
```python
def run_backtest(self, symbol: str, days: int = 60) -> dict:
    """Run backtest with configured resolution"""

    # Load data at configured resolution
    df_base = self.load_data(symbol, days, resolution=self.resolution)

    if df_base is None:
        return self._empty_stats()

    # Resample to 4h and 1D (works from both 1m and 5m base)
    df_4h, df_1d, df_aligned = self.calculate_indicators(df_base)

    # [Rest of backtest logic unchanged...]
```

**Acceptance Criteria:**
- [ ] Uses configured resolution when loading data
- [ ] Resampling works correctly from both 1m and 5m base data
- [ ] Indicator calculations produce same values on 4h bars

---

### Task 1.4: Create 5m vs 1m Comparison Test
**File:** `backtest/test_5m_vs_1m_comparison.py`

**Purpose:** Validate that 5m optimization approximates 1m results

**Test Structure:**
```python
"""
5m vs 1m Resolution Comparison Test

Validates that 5m base data produces similar backtest results to 1m data
for the purpose of optimization ranking.

Expected differences:
- Fill timing: 5m may be slightly less precise
- Fill rate: 5m may overestimate slightly (5m bars span 5× the range)
- Trade count: Should be identical or very close
- Net P&L: Should be within 5-10% for ranking purposes
"""

def run_comparison():
    symbol = "BTC-USD"
    days = 60
    config = get_phase2_2_baseline_config()

    # Run on 1m data
    engine_1m = Hybrid4hBacktestEngine(config, data_dir="data", resolution='1m')
    stats_1m = engine_1m.run_backtest(symbol, days)

    # Run on 5m data
    engine_5m = Hybrid4hBacktestEngine(config, data_dir="data", resolution='5m')
    stats_5m = engine_5m.run_backtest(symbol, days)

    # Compare key metrics
    compare_metrics(stats_1m, stats_5m)
```

**Acceptance Criteria:**
- [ ] Both resolutions produce results without errors
- [ ] Trade counts within ±1 trade
- [ ] Fill rates within ±5%
- [ ] Net P&L within ±10% (acceptable for ranking)
- [ ] 5m run completes in ~20% of 1m runtime

---

### Task 1.5: Download 5m Historical Data
**File:** `backtest/download_historical_data_5m.py` (new)

**Purpose:** Download 5m candles for BTC-USD

**Implementation:**
```python
"""
Download 5m historical data from Coinbase

Creates BTC_USD_5m.csv with 365+ days of 5m OHLCV candles
"""

import ccxt
import pandas as pd
from datetime import datetime, timedelta

def download_5m_data(symbol: str, days: int = 365):
    """Download 5m candles for symbol"""

    exchange = ccxt.coinbase({'enableRateLimit': True})

    # 5m candles: ~105,120 candles per year (288 per day × 365)
    # Fetch in chunks of 300 candles (25 hours per request)

    # [Implementation similar to existing 1m downloader...]
```

**Acceptance Criteria:**
- [ ] Downloads 365 days of 5m data successfully
- [ ] CSV format matches 1m data structure
- [ ] Handles API rate limits properly
- [ ] Verifies data completeness (no large gaps)

---

## Step 2: ROC-Score Setup Implementation

**Goal:** Add ROC-score as a new setup mode to the 4h Hybrid strategy

### Task 2.1: Add Setup Mode to Config
**File:** `backtest/config_4h_hybrid.py`

**Changes:**
```python
from enum import Enum

class SetupMode(Enum):
    """Setup trigger modes for 4h Hybrid strategy"""
    DONCHIAN = "donchian"
    ROC_SCORE = "roc_score"

@dataclass
class Hybrid4hConfig:
    """4h Hybrid Strategy Configuration"""

    # Setup mode
    setup_mode: SetupMode = SetupMode.DONCHIAN

    # Donchian parameters (used when setup_mode = DONCHIAN)
    donch_len: int = 10

    # ROC-score parameters (used when setup_mode = ROC_SCORE)
    roc_len_4h: int = 6              # ROC lookback period (4h bars)
    roc_ema_len: int = 3             # EMA smoothing of ROC (1 = no smoothing)
    roc_score_thresh: float = 1.2   # Minimum ROC/ATR ratio for setup

    # [Rest of existing parameters...]
```

**Acceptance Criteria:**
- [ ] SetupMode enum added with DONCHIAN and ROC_SCORE
- [ ] Config includes all ROC parameters
- [ ] Backward compatibility maintained (default = DONCHIAN)

---

### Task 2.2: Implement ROC Indicator Calculation
**File:** `backtest/engine_4h_hybrid.py`

**Changes:**
```python
def _calculate_4h_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
    """Calculate 4h indicators including ROC-score"""

    # [Existing ATR, Donchian, Bollinger calculations...]

    # ROC-score calculation (if using ROC_SCORE mode)
    if self.config.setup_mode == SetupMode.ROC_SCORE:
        # Raw ROC: (close - close[L]) / close[L]
        roc_raw = df['close'].pct_change(self.config.roc_len_4h)

        # Smooth with EMA if configured
        if self.config.roc_ema_len > 1:
            df['roc_4h'] = roc_raw.ewm(span=self.config.roc_ema_len, adjust=False).mean()
        else:
            df['roc_4h'] = roc_raw

        # ROC-score: ROC / ATR%
        df['roc_score_4h'] = df['roc_4h'] / df['atr_pct_4h'].replace(0, np.nan)
        df['roc_score_4h'] = df['roc_score_4h'].fillna(0)

    return df
```

**Acceptance Criteria:**
- [ ] ROC calculated correctly using configured lookback
- [ ] EMA smoothing applied when roc_ema_len > 1
- [ ] ROC-score = ROC / ATR% (normalized by volatility)
- [ ] Handles division by zero gracefully

---

### Task 2.3: Implement ROC-Score Setup Check
**File:** `backtest/strategy_4h_hybrid.py`

**Changes:**
```python
def _check_roc_score_setup(self, bar, indicators_4h) -> tuple[bool, float]:
    """
    Check for ROC-score setup trigger

    ROC-score setup triggers when:
    - roc_score >= roc_score_thresh
    - Recent compression (same as Donchian)
    - Volatility viable (same as Donchian)
    - Regime filter (same as Donchian)

    Args:
        bar: Current 1m bar
        indicators_4h: 4h indicators at this timestamp

    Returns:
        (is_setup, signal_level) where signal_level = current close price
    """

    if indicators_4h is None:
        return (False, 0.0)

    roc_score = indicators_4h.get('roc_score_4h', 0.0)

    # Check ROC-score threshold
    if roc_score < self.config.roc_score_thresh:
        return (False, 0.0)

    # Signal level for ROC-score is current close (not a breakout level)
    signal_level = bar['close']

    return (True, signal_level)
```

**Acceptance Criteria:**
- [ ] ROC-score threshold check implemented
- [ ] Uses current close as signal level (different from Donchian breakout level)
- [ ] Returns proper tuple format for consistency

---

### Task 2.4: Update Setup Detection Dispatcher
**File:** `backtest/strategy_4h_hybrid.py`

**Changes:**
```python
def _handle_flat_state(self, symbol, bar, indicators_4h):
    """Check for setup triggers based on configured mode"""

    # Dispatch to appropriate setup checker
    if self.config.setup_mode == SetupMode.DONCHIAN:
        is_setup, breakout_level = self._check_donchian_setup(bar, indicators_4h)
        setup_type = "donchian"
    elif self.config.setup_mode == SetupMode.ROC_SCORE:
        is_setup, breakout_level = self._check_roc_score_setup(bar, indicators_4h)
        setup_type = "roc_score"
    else:
        return  # Unknown mode

    if not is_setup:
        return

    # [Rest of setup handling is identical - retest bid placement, etc.]
```

**Acceptance Criteria:**
- [ ] Correctly dispatches based on setup_mode
- [ ] Both modes use same entry logic (retest/chase)
- [ ] Both modes use same gates (compression, viability, regime)
- [ ] setup_type recorded in trade logs for analysis

---

### Task 2.5: Create ROC-Score Baseline Config
**File:** `backtest/config_4h_hybrid.py`

**New Function:**
```python
def get_phase2_3_roc_baseline_config() -> Hybrid4hConfig:
    """
    Phase 2.3 ROC-Score Baseline Configuration

    Starting point for ROC-score mode optimization.
    Uses same gates/entry/exit as Phase 2.2, but with ROC-score trigger.
    """

    return Hybrid4hConfig(
        # Setup mode
        setup_mode=SetupMode.ROC_SCORE,

        # ROC-score parameters (initial guesses)
        roc_len_4h=6,              # 24h momentum
        roc_ema_len=3,             # Light smoothing
        roc_score_thresh=1.2,      # Require 1.2× ATR% momentum

        # Gates (same as Phase 2.2)
        bb_width_window=120,
        bb_width_pct_threshold=30,
        compression_lookback_4h=6,
        use_compression_filter=True,

        vol_min_mult=1.0,
        vol_lookback=30,
        vol_pct_threshold=50,

        # Entry (same as Phase 2.2)
        retest_offset=0.0015,
        retest_ttl_minutes=45,

        chase_offset_min=0.0005,
        chase_atr_mult=0.25,
        chase_max_extension=0.050,
        chase_max_reprices=3,
        chase_reprice_interval_minutes=30,

        # Exit (same as Phase 2.2)
        tp1_pct=0.015,
        tp2_pct=0.025,
        stop_loss_pct=0.010,
        runner_pct=0.50,

        # [Rest of parameters...]
    )
```

**Acceptance Criteria:**
- [ ] Baseline config created with ROC_SCORE mode
- [ ] Parameters match Phase 2.2 except for setup trigger
- [ ] Well-documented initial parameter choices

---

## Step 3: ROC-Score Baseline Validation (365 days)

**Goal:** Validate ROC-score generates adequate signals before optimization

### Task 3.1: Create 365-Day Baseline Test Script
**File:** `backtest/test_4h_roc_baseline_365d.py`

**Implementation:**
```python
"""
365-Day ROC-Score Baseline Validation

Tests ROC-score setup mode on 1 year of data to validate:
1. Signal generation frequency (target: >= 30 trades)
2. Fill rate (target: >= 30%)
3. Maker-first behavior (target: >= 80% maker fills)
4. Net P&L sign (positive preferred, but not critical for baseline)

Uses 5m base data for speed.
"""

from config_4h_hybrid import get_phase2_3_roc_baseline_config
from engine_4h_hybrid import Hybrid4hBacktestEngine

def run_baseline_test():
    print("=" * 80)
    print("PHASE 2.3 ROC-SCORE BASELINE VALIDATION (365 DAYS)")
    print("=" * 80)
    print()
    print("Testing ROC-score setup mode with baseline parameters")
    print()

    symbol = "BTC-USD"
    days = 365

    config = get_phase2_3_roc_baseline_config()

    print(f"ROC-Score Config:")
    print(f"  - roc_len_4h: {config.roc_len_4h}")
    print(f"  - roc_ema_len: {config.roc_ema_len}")
    print(f"  - roc_score_thresh: {config.roc_score_thresh}")
    print(f"  - Compression: ≤{config.bb_width_pct_threshold}th percentile")
    print(f"  - Volatility: {config.vol_min_mult}× median ATR%")
    print()

    # Use 5m data for speed
    engine = Hybrid4hBacktestEngine(config, data_dir="data", resolution='5m')

    print(f"Running 365-day backtest on 5m data...\\n")

    stats = engine.run_backtest(symbol, days)

    print("\\n" + "=" * 80)
    print("BASELINE VALIDATION RESULTS")
    print("=" * 80)
    print()

    engine.print_results(stats, symbol)

    # Evaluate success criteria
    print("\\n" + "=" * 80)
    print("SUCCESS CRITERIA")
    print("=" * 80)
    print()

    trades = stats.get('trades', 0)
    trades_per_month = (trades / days) * 30
    fill_rate = stats.get('signal_to_entry_pct', 0)
    net_pnl = stats.get('net_pnl', 0)

    criteria = {
        "Minimum trades (>= 30)": (trades >= 30, f"{trades} trades"),
        "Trade frequency (2-6/mo)": (2 <= trades_per_month <= 6, f"{trades_per_month:.1f}/month"),
        "Fill rate (>= 30%)": (fill_rate >= 30, f"{fill_rate:.1f}%"),
        "Net P&L positive": (net_pnl > 0, f"${net_pnl:.2f}"),
    }

    passed_count = 0
    for criterion, (passed, value) in criteria.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{criterion:<35} {value:<20} {status}")
        if passed:
            passed_count += 1

    print()

    if trades < 30:
        print("❌ BASELINE FAILED: Insufficient sample size")
        print()
        print("Recommendations:")
        print("  1. Loosen compression filter (bb_width_pct_threshold → 40-50)")
        print("  2. Lower ROC-score threshold (roc_score_thresh → 0.8-1.0)")
        print("  3. Reduce volatility filter (vol_min_mult → 0.75)")
        print()
        return False

    if passed_count >= 3:
        print("✅ BASELINE PASSED: Ready for Stage A optimization")
        print()
        return True
    else:
        print("⚠️  BASELINE PARTIAL: Consider parameter adjustments before optimization")
        print()
        return False

if __name__ == "__main__":
    run_baseline_test()
```

**Acceptance Criteria:**
- [ ] Runs 365-day test on 5m data successfully
- [ ] Reports all key metrics (trades, fill rate, P&L)
- [ ] Clear pass/fail criteria evaluation
- [ ] Actionable recommendations if baseline fails

---

### Task 3.2: Run Baseline and Document Results
**Action Items:**
1. Download 365 days of 5m data: `python3 backtest/download_historical_data_5m.py --days 365`
2. Run baseline test: `python3 backtest/test_4h_roc_baseline_365d.py`
3. Document results in: `backtest/PHASE2_3_ROC_BASELINE_RESULTS.md`

**Decision Tree:**
- **If trades >= 30 and >= 3 criteria pass:** → Proceed to Step 4 (Stage A Optimization)
- **If trades < 30:** → Loosen gates, re-run baseline
- **If trades >= 30 but poor P&L:** → Proceed anyway (Stage B will optimize exits)

---

## Step 4: ROC-Score Stage A Optimization

**Goal:** Optimize gates and entry parameters for ROC-score mode

### Task 4.1: Create Stage A Optimizer
**File:** `backtest/optimize_phase2_3_roc_stage_a.py`

**Implementation:**
```python
"""
Phase 2.3 ROC-Score Stage A Optimization

Optimize gates and entry parameters for ROC-score setup mode.

Parameters to optimize:
- ROC-score: roc_len_4h, roc_ema_len, roc_score_thresh
- Compression: bb_width_pct_threshold, bb_width_window
- Volatility: vol_min_mult
- Entry: retest_offset, chase parameters

Fixed parameters (Stage B):
- Exit: tp1_pct, tp2_pct, stop_loss_pct, runner_pct

Constraints:
- Minimum 30 trades for ranking
- Test on 365 days, 5m base data
- Rank by net P&L with soft penalties
"""

import itertools
from config_4h_hybrid import Hybrid4hConfig, SetupMode
from engine_4h_hybrid import Hybrid4hBacktestEngine

def run_stage_a_optimization():
    print("=" * 80)
    print("PHASE 2.3 ROC-SCORE STAGE A OPTIMIZATION")
    print("=" * 80)
    print()

    symbol = "BTC-USD"
    days = 365
    min_trades_for_rank = 30

    # Parameter grid (intentionally small for first pass)
    roc_lens = [4, 6, 8]
    roc_ema_lens = [1, 3]
    roc_score_thresholds = [0.8, 1.0, 1.2, 1.4]

    bb_thresholds = [25, 30, 35, 40]
    bb_windows = [120, 180]

    vol_mults = [0.75, 1.0, 1.25]

    retest_offsets = [0.0010, 0.0015, 0.0020]

    # Total: 3 × 2 × 4 × 4 × 2 × 3 × 3 = 1,728 combinations
    # At ~10 sec/test on 5m data: ~5 hours total

    # [Grid search implementation...]
    # [Ranking by net P&L with penalties for low freq, high expired rate...]
    # [Output top 10 configs...]
```

**Acceptance Criteria:**
- [ ] Tests all parameter combinations systematically
- [ ] Enforces min_trades >= 30 constraint
- [ ] Ranks by net P&L with frequency/quality penalties
- [ ] Exports top 10 configs to CSV
- [ ] Completes in reasonable time (<6 hours)

---

### Task 4.2: Run Stage A Optimization
**Action Items:**
1. Run optimizer: `python3 backtest/optimize_phase2_3_roc_stage_a.py > stage_a_results.log 2>&1 &`
2. Monitor progress: `tail -f stage_a_results.log`
3. Analyze results: Review top 10 configurations
4. Select best config for Stage B

---

## Step 5: ROC-Score Stage B Optimization

**Goal:** Optimize exit parameters using best Stage A config

### Task 5.1: Create Stage B Optimizer
**File:** `backtest/optimize_phase2_3_roc_stage_b.py`

**Implementation:**
```python
"""
Phase 2.3 ROC-Score Stage B Optimization

Optimize exit parameters using best Stage A configuration.

Parameters to optimize:
- TP levels: tp1_pct, tp2_pct (as fee multiples)
- TP quantities: tp1_qty_frac, tp2_qty_frac
- Stop/Trail: stop_mult, trail_mult

Fixed parameters (from Stage A):
- All gates and entry parameters locked to best Stage A config
"""

# [Similar structure to Stage A but smaller grid...]
```

**Acceptance Criteria:**
- [ ] Uses best Stage A config as baseline
- [ ] Only varies exit parameters
- [ ] Optimizes for profit factor and net P&L
- [ ] Selects final production config

---

## Step 6: Validation

**Goal:** Validate top configs with walk-forward and 1m precision

### Task 6.1: Walk-Forward Validation
**File:** `backtest/validate_phase2_3_walkforward.py`

**Test Structure:**
```python
"""
Walk-Forward Validation for Phase 2.3 Top Configs

Tests robustness across different time periods:
- Train: First 250 days (70%)
- Test: Last 115 days (30%)

Run on 1m data for maximum realism.
"""
```

---

### Task 6.2: 5m vs 1m Validation
**File:** `backtest/validate_phase2_3_precision.py`

**Test Structure:**
```python
"""
5m vs 1m Precision Validation

Compares top 3 configs on both resolutions:
- Trade count: Should be very close
- Fill rate: 1m may be slightly lower (more realistic)
- Net P&L: Should be within 10-15%
"""
```

---

## Step 7: Production Deployment

### Task 7.1: Update Production Config
**File:** `backtest/config_4h_hybrid.py`

**Action:**
```python
def get_phase2_3_production_config() -> Hybrid4hConfig:
    """
    Phase 2.3 Production Configuration

    Optimized ROC-score setup mode with validated parameters.
    """

    return Hybrid4hConfig(
        setup_mode=SetupMode.ROC_SCORE,

        # [Parameters from optimization results...]
    )
```

---

### Task 7.2: Create Production Test Suite
**File:** `backtest/test_phase2_3_production_suite.py`

**Tests:**
- [ ] 365-day validation on 1m data
- [ ] Multi-symbol test (BTC-USD, ETH-USD)
- [ ] Regime analysis (trending vs choppy periods)
- [ ] Drawdown analysis
- [ ] Fee breakdown validation

---

## Implementation Timeline

**Estimated Duration:** 7-10 days

| Step | Tasks | Est. Time | Dependencies |
|------|-------|-----------|--------------|
| 1 | Data Infrastructure (5m) | 1-2 days | None |
| 2 | ROC-Score Implementation | 1-2 days | Step 1 |
| 3 | Baseline Validation | 0.5 day | Step 2 |
| 4 | Stage A Optimization | 1 day + 5h runtime | Step 3 pass |
| 5 | Stage B Optimization | 0.5 day + 2h runtime | Step 4 |
| 6 | Validation | 1 day | Step 5 |
| 7 | Production Deployment | 0.5 day | Step 6 |

---

## Success Criteria Summary

**Phase 2.3 Complete When:**
- [ ] 5m data infrastructure working and validated
- [ ] ROC-score mode implemented and tested
- [ ] Baseline achieves >= 30 trades in 365 days
- [ ] Stage A optimization completes successfully
- [ ] Stage B optimization produces positive expectancy
- [ ] Walk-forward validation confirms robustness
- [ ] 1m validation confirms fill rate assumptions
- [ ] Production config documented and ready to deploy

---

## Risk Mitigation

**Risk 1: ROC-score doesn't generate enough signals**
- Mitigation: Loosen gates before optimization
- Fallback: Combine with Donchian mode sooner than planned

**Risk 2: 5m overestimates fill rates significantly**
- Mitigation: 1m validation will catch this
- Fallback: Use 1m for final optimization if 5m correlation poor

**Risk 3: Optimization takes too long**
- Mitigation: Use smaller parameter grids initially
- Fallback: Reduce days to 180 for initial screening, 365 for final

---

## Next Immediate Action

**START HERE:** Task 1.1 - Add 5m Data Loader

```bash
# 1. Review current engine_4h_hybrid.py structure
# 2. Implement load_data() with resolution parameter
# 3. Test with existing 1m data (backward compatibility)
# 4. Download 5m data
# 5. Test with 5m data
```

---

**END OF TASK BREAKDOWN**
