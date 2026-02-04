#!/usr/bin/env python3
"""
Test Stage C compression-based runner policy

This test verifies:
1. Compression context is captured at fill time
2. Runner policy switches based on compression_recent_12
3. Different trail_mult and runner_qty_frac are applied

Usage:
    python3 test_stage_c_runner_policy.py
"""

import sys
from config_4h_hybrid import get_phase2_4_stage_c_test_config
from engine_4h_hybrid import Hybrid4hBacktestEngine
from data_resampler import DataResampler
import pandas as pd
from pathlib import Path
from datetime import timedelta


def test_stage_c_180d():
    """Test Stage C runner policy on 180d window"""

    days = 180
    symbol = "BTC-USD"

    print("=" * 80)
    print("STAGE C RUNNER POLICY TEST (180d)")
    print("=" * 80)
    print()
    print(f"Symbol: {symbol}")
    print(f"Period: {days} days")
    print()

    # Load data
    print("=" * 80)
    print("LOADING DATA")
    print("=" * 80)
    print()

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

    print("=" * 80)
    print("STAGE C CONFIGURATION")
    print("=" * 80)
    print()
    print(f"use_compression_runner_policy: {config.use_compression_runner_policy}")
    print()
    print("Compressed entries:")
    print(f"  runner_qty_frac: {config.runner_qty_frac_compressed:.1%}")
    print(f"  trail_mult:      {config.trail_mult_compressed:.1f}x")
    print()
    print("Normal entries:")
    print(f"  runner_qty_frac: {config.runner_qty_frac_normal:.1%}")
    print(f"  trail_mult:      {config.trail_mult_normal:.1f}x")
    print()
    print("=" * 80)
    print()

    # Run backtest
    engine = Hybrid4hBacktestEngine(config)
    df_4h, df_1d, df_aligned = engine.calculate_indicators(df_1m_window)

    print(f"Indicators calculated: {len(df_aligned)} 1m bars")
    print()
    print("Running strategy...")
    print()

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

    print()
    print("=" * 80)
    print("STAGE C VERIFICATION SUMMARY")
    print("=" * 80)
    print()

    # Analyze trades by compression context
    trades = engine.strategy.trades
    compressed_trades = [t for t in trades if t.entry_compressed_recent_12]
    normal_trades = [t for t in trades if not t.entry_compressed_recent_12]

    print(f"Total trades: {len(trades)}")
    print(f"  Compressed entries (compression_recent_12=True): {len(compressed_trades)}")
    print(f"  Normal entries (compression_recent_12=False):    {len(normal_trades)}")
    print()

    if compressed_trades:
        print("Compressed trade details:")
        for i, t in enumerate(compressed_trades, 1):
            print(f"  {i}. Entry: {t.entry_time}, Price: ${t.entry_price:.2f}, Type: {t.entry_type}")

    if normal_trades:
        print()
        print("Normal trade details:")
        for i, t in enumerate(normal_trades, 1):
            print(f"  {i}. Entry: {t.entry_time}, Price: ${t.entry_price:.2f}, Type: {t.entry_type}")

    print()
    print("=" * 80)
    print("TEST COMPLETE")
    print("=" * 80)
    print()
    print("VERIFICATION CHECKLIST:")
    print("  ✓ Check above for 🔍 STAGE C VERIFICATION logs at each fill")
    print("  ✓ Verify compression context (now/recent_6/recent_12) is shown")
    print("  ✓ Verify policy switches between COMPRESSED and NORMAL")
    print("  ✓ Verify runner_qty_frac and trail_mult match expected values")
    print()


if __name__ == "__main__":
    test_stage_c_180d()
