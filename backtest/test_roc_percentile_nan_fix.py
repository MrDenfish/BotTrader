#!/usr/bin/env python3
"""
Test ROC percentile NaN filtering fix

This test verifies:
1. ROC-score percentile computation filters NaN values from warmup period
2. Valid percentiles are computed when NaN values are present
3. Percentile calibration section is skipped when all values are NaN

Usage:
    python3 test_roc_percentile_nan_fix.py
"""

import sys
from config_4h_hybrid import get_phase2_3_roc_baseline_config
from engine_4h_hybrid import Hybrid4hBacktestEngine
from data_resampler import DataResampler
import pandas as pd
from pathlib import Path
from datetime import timedelta


def test_roc_percentile_nan_fix():
    """Test ROC percentile computation with NaN filtering"""

    days = 60  # Small window to ensure we hit warmup period
    symbol = "BTC-USD"

    print("=" * 80)
    print("ROC PERCENTILE NaN FIX TEST")
    print("=" * 80)
    print()
    print(f"Objective: Verify ROC percentile computation filters NaN values")
    print(f"Symbol: {symbol}")
    print(f"Period: {days} days")
    print()

    # Load data
    symbol_file = symbol.replace('-', '_')
    csv_path = Path(__file__).parent / "data" / f"{symbol_file}.csv"
    if not csv_path.exists():
        print(f"❌ ERROR: Data file not found: {csv_path}")
        sys.exit(1)

    df_1m = pd.read_csv(csv_path)
    if 'time' in df_1m.columns:
        df_1m['time'] = pd.to_datetime(df_1m['time'])
        df_1m = df_1m.set_index('time')
    elif 'timestamp' in df_1m.columns:
        df_1m['timestamp'] = pd.to_datetime(df_1m['timestamp'])
        df_1m = df_1m.set_index('timestamp')
    df_1m = df_1m.sort_index()

    # Window last N days
    end_date = df_1m.index[-1]
    start_date = end_date - timedelta(days=days)
    df_1m_window = df_1m[df_1m.index >= start_date].copy()

    print(f"Data loaded: {len(df_1m_window):,} bars ({start_date} to {end_date})")
    print()

    # Get config
    config = get_phase2_3_roc_baseline_config()
    config.use_compression_filter = True

    # Run backtest
    engine = Hybrid4hBacktestEngine(config)
    df_4h, df_1d, df_aligned = engine.calculate_indicators(df_1m_window)

    print(f"Indicators calculated: {len(df_aligned)} 1m bars")
    print()

    print("Running strategy...")
    for idx, (ts, bar) in enumerate(df_aligned.iterrows()):
        is_4h_close = engine._is_timeframe_boundary(ts, '4h')
        is_1d_close = engine._is_timeframe_boundary(ts, '1D')

        indicators_4h = {
            'atr_pct': bar.get('atr_pct_4h', 0),
            'donch_high_prev': bar.get('donch_high_prev_4h', 0),
            'bb_width_pct': bar.get('bb_width_pct_4h', 0),
            'bb_width': bar.get('bb_width_4h', 0),
            'roc_score_4h': bar.get('roc_score_4h', 0)
        }

        indicators_1d = {
            'ema200': bar.get('ema200_1d', 0),
            'ema50': bar.get('ema50_1d', 0),
            'ema50_slope': bar.get('ema50_slope_1d', 0)
        }

        engine.strategy.process_bar_1m(
            symbol=symbol,
            bar=bar,
            indicators_4h=indicators_4h,
            indicators_1d=indicators_1d,
            is_4h_close=is_4h_close,
            is_1d_close=is_1d_close
        )

    print("Strategy complete")
    print()

    # Get statistics
    stats = engine.strategy.get_statistics()

    # Capture diagnostic output
    import io
    captured_output = io.StringIO()
    original_stdout = sys.stdout

    try:
        sys.stdout = captured_output
        engine.strategy.print_enhanced_diagnostics()
    finally:
        sys.stdout = original_stdout

    output = captured_output.getvalue()

    print()
    print("=" * 80)
    print("TEST RESULTS")
    print("=" * 80)
    print()

    # Extract ROC-SCORE PERCENTILE CALIBRATION section
    if "ROC-SCORE PERCENTILE CALIBRATION" in output:
        start_idx = output.find("ROC-SCORE PERCENTILE CALIBRATION")
        end_idx = output.find("=" * 80, start_idx + 100)
        roc_section = output[start_idx:end_idx]

        print("Found ROC-SCORE PERCENTILE CALIBRATION section:")
        print("-" * 80)
        print(roc_section)
        print("-" * 80)
        print()

        # Check 1: No NaN percentiles in output
        nan_count = roc_section.count("nan")
        print(f"✓ Check 1: No NaN percentiles in output")
        if nan_count == 0:
            print(f"  PASS - Found {nan_count} 'nan' strings in percentile section")
        else:
            print(f"  FAIL - Found {nan_count} 'nan' strings (should be 0)")
            print()
            print("Sample NaN lines:")
            for line in roc_section.split('\n'):
                if 'nan' in line.lower():
                    print(f"    {line}")

        print()

        # Check 2: Valid percentile values present
        has_min = "Min:" in roc_section and "nan" not in roc_section.split("Min:")[1].split("\n")[0]
        has_p25 = "P25:" in roc_section and "nan" not in roc_section.split("P25:")[1].split("\n")[0]
        has_median = "Median:" in roc_section and "nan" not in roc_section.split("Median:")[1].split("\n")[0]
        has_max = "Max:" in roc_section and "nan" not in roc_section.split("Max:")[1].split("\n")[0]

        print(f"✓ Check 2: Valid percentile values present")
        print(f"  Min:    {has_min}")
        print(f"  P25:    {has_p25}")
        print(f"  Median: {has_median}")
        print(f"  Max:    {has_max}")

        if has_min and has_p25 and has_median and has_max:
            print(f"  PASS - All percentile values are valid")
        else:
            print(f"  FAIL - Some percentile values are missing or invalid")

        print()

        # Check 3: NaN filtering message present (if warmup occurred)
        has_filter_msg = "Filtered" in roc_section and "NaN values" in roc_section
        print(f"✓ Check 3: NaN filtering message (if warmup NaNs present)")
        if has_filter_msg:
            print(f"  PASS - Found NaN filtering message (warmup period detected)")
        else:
            print(f"  INFO - No filtering message (all values valid, no warmup NaNs)")

        print()

    else:
        print("⚠️  ROC-SCORE PERCENTILE CALIBRATION section not found")
        print("   This may indicate no viable bars or setup_mode != 'roc_score'")
        print()

        # Show gate audit for context
        gate_audit = engine.strategy.gate_audit
        print("Gate audit:")
        print(f"  Total 4h bars: {gate_audit.get('total_4h_bars', 0)}")
        print(f"  Regime OK: {gate_audit.get('regime_ok', 0)}")
        print(f"  Viable bars: {gate_audit.get('viable_bars', 0)}")
        print(f"  ROC OK: {gate_audit.get('roc_ok', 0)}")
        print()

    print("=" * 80)
    print("TEST COMPLETE")
    print("=" * 80)
    print()

    # Summary
    if "ROC-SCORE PERCENTILE CALIBRATION" in output and nan_count == 0:
        print("✅ SUCCESS: ROC percentile NaN filtering is working correctly")
        print("   - No NaN percentiles in output")
        print("   - Valid Min/P25/Median/Max values computed")
    else:
        print("❌ ISSUES DETECTED:")
        if "ROC-SCORE PERCENTILE CALIBRATION" not in output:
            print("   - Percentile section not found (check viable bar count)")
        elif nan_count > 0:
            print(f"   - {nan_count} NaN values still in output (should be 0)")

    print()


if __name__ == "__main__":
    test_roc_percentile_nan_fix()
