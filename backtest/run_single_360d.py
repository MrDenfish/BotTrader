#!/usr/bin/env python3
"""
Single 360-day backtest runner with Phase 2.3.2 diagnostics

Usage:
    python3 run_single_360d.py [--compression-on|--compression-off] [--stagec-on] [--runner-compressed X] [--runner-normal X] [--trail-compressed X] [--trail-normal X]

Outputs:
    - Fee floor truth line
    - Universe-level diagnostic counts
    - Gate audit
    - Full backtest results
"""

import sys
from config_4h_hybrid import get_phase2_3_roc_baseline_config
from engine_4h_hybrid import Hybrid4hBacktestEngine
from data_resampler import DataResampler
import pandas as pd
from pathlib import Path
from datetime import timedelta


def run_single_360d(
    compression_enabled: bool = True,
    stagec_enabled: bool = False,
    runner_compressed: float = None,
    runner_normal: float = None,
    trail_compressed: float = None,
    trail_normal: float = None
):
    """
    Run single 360-day backtest with Phase 2.3.2 diagnostics

    Args:
        compression_enabled: If True, use compression filter; if False, disable it
        stagec_enabled: If True, enable Stage C compression-based runner policy
        runner_compressed: Runner qty fraction for compressed entries (Stage C)
        runner_normal: Runner qty fraction for normal entries (Stage C)
        trail_compressed: Trail multiplier for compressed entries (Stage C)
        trail_normal: Trail multiplier for normal entries (Stage C)
    """

    days = 360
    symbol = "BTC-USD"

    print("=" * 80)
    print(f"SINGLE 360-DAY BACKTEST RUN (Phase 2.3.2)")
    print("=" * 80)
    print()
    print(f"Symbol: {symbol}")
    print(f"Period: {days} days")
    print(f"Compression filter: {'ON' if compression_enabled else 'OFF'}")
    print(f"Stage C runner policy: {'ON' if stagec_enabled else 'OFF'}")
    print()

    # Load data
    print("=" * 80)
    print("LOADING DATA")
    print("=" * 80)
    print()

    # Convert BTC-USD to BTC_USD for filename
    symbol_file = symbol.replace('-', '_')
    csv_path = Path(__file__).parent / "data" / f"{symbol_file}.csv"
    if not csv_path.exists():
        print(f"❌ ERROR: Data file not found: {csv_path}")
        print(f"   Please run: python3 download_historical_data_csv.py --days {days+30} --symbol {symbol}")
        sys.exit(1)

    # Try both 'time' and 'timestamp' column names for compatibility
    df_1m = pd.read_csv(csv_path)
    if 'time' in df_1m.columns:
        df_1m['time'] = pd.to_datetime(df_1m['time'])
        df_1m = df_1m.set_index('time')
    elif 'timestamp' in df_1m.columns:
        df_1m['timestamp'] = pd.to_datetime(df_1m['timestamp'])
        df_1m = df_1m.set_index('timestamp')
    else:
        print(f"❌ ERROR: CSV must have 'time' or 'timestamp' column")
        sys.exit(1)
    df_1m = df_1m.sort_index()

    # Window last N days
    end_date = df_1m.index[-1]
    start_date = end_date - timedelta(days=days)
    df_1m_window = df_1m[df_1m.index >= start_date].copy()

    print(f"Data loaded:")
    print(f"  CSV file: {csv_path}")
    print(f"  Full data: {len(df_1m):,} bars ({df_1m.index[0]} to {df_1m.index[-1]})")
    print(f"  Window: {len(df_1m_window):,} bars ({start_date} to {end_date})")
    print()

    # Resample to 4h
    df_4h = DataResampler.resample_ohlcv(df_1m_window, '4h')

    # Get config
    config = get_phase2_3_roc_baseline_config()
    config.use_compression_filter = compression_enabled

    # Apply Stage C parameters if enabled
    if stagec_enabled:
        config.use_compression_runner_policy = True
        if runner_compressed is not None:
            config.runner_qty_frac_compressed = runner_compressed
        if runner_normal is not None:
            config.runner_qty_frac_normal = runner_normal
        if trail_compressed is not None:
            config.trail_mult_compressed = trail_compressed
        if trail_normal is not None:
            config.trail_mult_normal = trail_normal

    print("=" * 80)
    print("RUNNING BACKTEST")
    print("=" * 80)
    print()
    print(f"Config: {config}")
    print()

    # TRUTH LINE: Stage C Configuration Verification
    print("=" * 80)
    print("TRUTH LINE: STAGE C RUNNER POLICY CONFIGURATION")
    print("=" * 80)
    print(f"  use_compression_runner_policy:  {config.use_compression_runner_policy}")
    print(f"  runner_qty_frac_compressed:     {config.runner_qty_frac_compressed:.2f}")
    print(f"  runner_qty_frac_normal:         {config.runner_qty_frac_normal:.2f}")
    print(f"  trail_mult_compressed:          {config.trail_mult_compressed:.1f}")
    print(f"  trail_mult_normal:              {config.trail_mult_normal:.1f}")
    print("=" * 80)
    print()

    # Run backtest
    engine = Hybrid4hBacktestEngine(config)

    # Calculate indicators
    df_4h, df_1d, df_aligned = engine.calculate_indicators(df_1m_window)

    print(f"Indicators calculated:")
    print(f"  4h bars: {len(df_4h)}")
    print(f"  1d bars: {len(df_1d)}")
    print(f"  Aligned 1m bars: {len(df_aligned)}")
    print()

    # Run strategy
    print("Running strategy...")
    print(f"  Processing {len(df_aligned)} 1m bars...")
    print()

    # Phase 2.3.2: Debug counter for 4h boundary detection
    count_4h_detected = 0
    first_20_boundaries = []

    for idx, (ts, bar) in enumerate(df_aligned.iterrows()):
        is_4h_close = engine._is_timeframe_boundary(ts, '4h')
        is_1d_close = engine._is_timeframe_boundary(ts, '1D')

        # Phase 2.3.2: Track 4h boundary detections
        if is_4h_close:
            count_4h_detected += 1
            if count_4h_detected <= 20:
                first_20_boundaries.append((count_4h_detected, ts, idx))

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

        if (idx + 1) % 100000 == 0:
            print(f"  Processed {idx + 1}/{len(df_aligned)} bars... ({count_4h_detected} 4h boundaries detected so far)")

    print(f"  ✅ Complete")
    print()

    # Phase 2.3.2: Print debug info
    print("=" * 80)
    print("DEBUG: 4H BOUNDARY DETECTION IN PROCESSING LOOP")
    print("=" * 80)
    print(f"  Total 4h boundaries detected during loop: {count_4h_detected}")
    print(f"  Expected processable boundaries from df_aligned: {count_4h_detected}")
    print(f"  (Note: df_4h from resampling has {len(df_4h)} bars total)")
    print()
    print("  First 20 4h boundaries detected:")
    for num, ts, idx in first_20_boundaries:
        print(f"    {num:2d}. {ts} (1m bar index {idx})")
    print("=" * 80)
    print()

    # Get statistics
    stats = engine.strategy.get_statistics()

    # Print results
    print()
    engine.print_results(stats, symbol)

    # Print diagnostics
    print()
    engine.strategy.print_enhanced_diagnostics()

    print()
    print("=" * 80)
    print("BACKTEST COMPLETE")
    print("=" * 80)
    print()


if __name__ == "__main__":
    # Parse command line args
    compression_on = True
    stagec_on = False
    runner_compressed = None
    runner_normal = None
    trail_compressed = None
    trail_normal = None

    i = 1
    while i < len(sys.argv):
        arg = sys.argv[i].lower()

        if arg in ["--compression-off", "--off", "-off"]:
            compression_on = False
            i += 1
        elif arg in ["--compression-on", "--on", "-on"]:
            compression_on = True
            i += 1
        elif arg in ["--stagec-on", "--stagec", "--stage-c-on"]:
            stagec_on = True
            i += 1
        elif arg in ["--stagec-off", "--stage-c-off"]:
            stagec_on = False
            i += 1
        elif arg == "--runner-compressed":
            if i + 1 >= len(sys.argv):
                print("ERROR: --runner-compressed requires a value")
                sys.exit(1)
            runner_compressed = float(sys.argv[i + 1])
            i += 2
        elif arg == "--runner-normal":
            if i + 1 >= len(sys.argv):
                print("ERROR: --runner-normal requires a value")
                sys.exit(1)
            runner_normal = float(sys.argv[i + 1])
            i += 2
        elif arg == "--trail-compressed":
            if i + 1 >= len(sys.argv):
                print("ERROR: --trail-compressed requires a value")
                sys.exit(1)
            trail_compressed = float(sys.argv[i + 1])
            i += 2
        elif arg == "--trail-normal":
            if i + 1 >= len(sys.argv):
                print("ERROR: --trail-normal requires a value")
                sys.exit(1)
            trail_normal = float(sys.argv[i + 1])
            i += 2
        elif arg in ["--help", "-h"]:
            print(__doc__)
            sys.exit(0)
        else:
            print(f"Unknown argument: {arg}")
            print("Use --compression-on/--compression-off and optionally --stagec-on with Stage C parameters")
            print("Example: python3 run_single_360d.py --compression-on --stagec-on --runner-compressed 0.20 --runner-normal 0.15 --trail-compressed 2.2 --trail-normal 1.8")
            sys.exit(1)

    run_single_360d(
        compression_enabled=compression_on,
        stagec_enabled=stagec_on,
        runner_compressed=runner_compressed,
        runner_normal=runner_normal,
        trail_compressed=trail_compressed,
        trail_normal=trail_normal
    )
