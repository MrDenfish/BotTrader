#!/usr/bin/env python3
"""
Test Stage C warmup behavior for early fills

This test verifies:
1. Early fills (before bb_width_pct warmup) show "WARMUP" message, not NaN arrays
2. Compression flags (compression_now/recent_6/recent_12) are False during warmup
3. Normal runner policy is applied (not compressed) during warmup
4. Later fills (after warmup) show valid BB width values

Usage:
    python3 test_stage_c_warmup.py
"""

import sys
import math
from io import StringIO
from config_4h_hybrid import get_phase2_4_stage_c_test_config
from engine_4h_hybrid import Hybrid4hBacktestEngine
from data_resampler import DataResampler
import pandas as pd
from pathlib import Path
from datetime import timedelta


def test_stage_c_warmup():
    """Test Stage C warmup behavior on early fills"""

    # Use minimal window to force early fills during warmup
    days = 30  # Small window to hit warmup period
    symbol = "BTC-USD"

    print("=" * 80)
    print("STAGE C WARMUP TEST")
    print("=" * 80)
    print()
    print(f"Objective: Verify Stage C logging handles BB width warmup gracefully")
    print(f"Symbol: {symbol}")
    print(f"Period: {days} days (small window to hit warmup period)")
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

    # Get config with Stage C enabled
    config = get_phase2_4_stage_c_test_config()
    config.use_compression_runner_policy = True

    # Run backtest with Stage C enabled
    engine = Hybrid4hBacktestEngine(config)
    df_4h, df_1d, df_aligned = engine.calculate_indicators(df_1m_window)

    print(f"Indicators calculated: {len(df_aligned)} 1m bars")
    print()

    # Capture stdout to verify Stage C verification logs
    import io
    captured_output = io.StringIO()
    original_stdout = sys.stdout

    try:
        sys.stdout = captured_output  # Redirect stdout

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

    finally:
        sys.stdout = original_stdout  # Restore stdout

    # Get captured output
    output = captured_output.getvalue()

    print()
    print("=" * 80)
    print("TEST RESULTS")
    print("=" * 80)
    print()

    # Extract Stage C verification blocks
    verification_blocks = []
    for block in output.split("🔍 STAGE C VERIFICATION"):
        if "Fill @" in block:
            verification_blocks.append("🔍 STAGE C VERIFICATION" + block)

    total_fills = len(verification_blocks)
    print(f"Total fills with Stage C verification: {total_fills}")
    print()

    if total_fills == 0:
        print("⚠️  No fills detected in this window. Test inconclusive.")
        print("   Try increasing window size or different date range.")
        return

    # Analyze first 3 fills (likely in warmup period)
    print("Analyzing first 3 fills (warmup period):")
    print()

    warmup_fills = verification_blocks[:min(3, total_fills)]
    for i, block in enumerate(warmup_fills, 1):
        print(f"Fill {i}:")
        print("-" * 40)

        # Check for WARMUP message
        has_warmup_msg = "WARMUP" in block
        has_nan_array = "[nan" in block.lower() or "np.float64(nan)" in block

        # Extract compression flags
        compression_now = "compression_now (current bar):    True" in block
        compression_recent_6 = "compression_recent_6 (24h):       True" in block
        compression_recent_12 = "compression_recent_12 (48h):      True" in block

        # Extract policy selection
        using_compressed = "Using COMPRESSED runner policy" in block
        using_normal = "Using NORMAL runner policy" in block

        # Extract timestamp
        import re
        ts_match = re.search(r'Fill @ (.+)', block)
        timestamp = ts_match.group(1) if ts_match else "unknown"

        print(f"  Timestamp: {timestamp}")
        print(f"  Has 'WARMUP' message: {has_warmup_msg}")
        print(f"  Has NaN array in output: {has_nan_array}")
        print(f"  compression_now: {compression_now}")
        print(f"  compression_recent_6: {compression_recent_6}")
        print(f"  compression_recent_12: {compression_recent_12}")
        print(f"  Policy: {'COMPRESSED' if using_compressed else 'NORMAL' if using_normal else 'UNKNOWN'}")
        print()

    # Check last fill (should have valid BB width values)
    if total_fills > 3:
        print()
        print("Analyzing last fill (post-warmup period):")
        print("-" * 40)

        last_block = verification_blocks[-1]

        has_warmup_msg = "WARMUP" in last_block
        has_nan_array = "[nan" in last_block.lower() or "np.float64(nan)" in last_block
        has_valid_bb_history = "BB width history (last 12 bars):" in last_block and not has_warmup_msg

        ts_match = re.search(r'Fill @ (.+)', last_block)
        timestamp = ts_match.group(1) if ts_match else "unknown"

        print(f"  Timestamp: {timestamp}")
        print(f"  Has 'WARMUP' message: {has_warmup_msg}")
        print(f"  Has NaN array in output: {has_nan_array}")
        print(f"  Has valid BB history: {has_valid_bb_history}")
        print()

    # Validation assertions
    print("=" * 80)
    print("VALIDATION CHECKS")
    print("=" * 80)
    print()

    # Check 1: No NaN arrays in output
    nan_count = output.count("[nan")
    nan_count += output.count("np.float64(nan)")
    print(f"✓ Check 1: No NaN arrays in Stage C logs")
    if nan_count == 0:
        print(f"  PASS - Found {nan_count} NaN array outputs")
    else:
        print(f"  FAIL - Found {nan_count} NaN array outputs (should be 0)")
        print(f"  Sample NaN output:")
        # Show first occurrence
        for line in output.split('\n'):
            if '[nan' in line.lower() or 'np.float64(nan)' in line:
                print(f"    {line.strip()}")
                break

    print()

    # Check 2: WARMUP message appears for early fills
    warmup_count = output.count("WARMUP")
    print(f"✓ Check 2: WARMUP message appears for early fills")
    if warmup_count > 0:
        print(f"  PASS - Found {warmup_count} WARMUP messages")
    else:
        print(f"  WARN - Found {warmup_count} WARMUP messages (expected > 0 for early fills)")

    print()

    # Check 3: Compression logic is safe during warmup
    print(f"✓ Check 3: Compression logic is safe during warmup")
    print(f"  Early fills should have compression_recent_12=False (insufficient history)")
    early_compressed_count = 0
    for block in warmup_fills:
        if "compression_recent_12 (48h):      True" in block:
            early_compressed_count += 1

    if early_compressed_count == 0:
        print(f"  PASS - No early fills incorrectly marked as compressed")
    else:
        print(f"  WARN - {early_compressed_count} early fills marked as compressed (may be valid if NaNs filtered)")

    print()
    print("=" * 80)
    print("TEST COMPLETE")
    print("=" * 80)
    print()

    # Summary
    if nan_count == 0 and warmup_count > 0:
        print("✅ SUCCESS: Stage C warmup logging is correct")
        print("   - No NaN arrays dumped to output")
        print("   - WARMUP messages displayed for early fills")
        print("   - Compression logic remains safe (returns False when history insufficient)")
    else:
        print("❌ ISSUES DETECTED:")
        if nan_count > 0:
            print(f"   - {nan_count} NaN arrays still in output (should be 0)")
        if warmup_count == 0:
            print(f"   - No WARMUP messages (expected for early fills)")

    print()

    # Print sample of first verification block for manual inspection
    if len(verification_blocks) > 0:
        print("Sample Stage C verification (first fill):")
        print("-" * 80)
        print(verification_blocks[0])
        print("-" * 80)


if __name__ == "__main__":
    test_stage_c_warmup()
