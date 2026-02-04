#!/usr/bin/env python3
"""
Compression Threshold Sweep - Phase 2.3.2

Runs backtest multiple times varying bb_width_pct_threshold while holding
everything else constant and using the same 180d window.

Usage:
    python3 sweep_compression_threshold.py [--thresholds 30,40,50,60]

Outputs:
    - Runs 4 backtests with different compression thresholds
    - Prints comparison table at the end
    - Saves detailed logs to sweep_compression_TIMESTAMP.log
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path
import pandas as pd

from config_4h_hybrid import get_phase2_3_roc_baseline_config
from engine_4h_hybrid import Hybrid4hBacktestEngine
from data_resampler import DataResampler


def run_sweep(thresholds: list[int] = None, days: int = 180):
    """
    Run compression threshold sweep

    Args:
        thresholds: List of bb_width_pct_threshold values to test
        days: Number of days for backtest window
    """

    if thresholds is None:
        thresholds = [30, 40, 50, 60]

    symbol = "BTC-USD"

    print("=" * 80)
    print("COMPRESSION THRESHOLD SWEEP (Phase 2.3.2)")
    print("=" * 80)
    print()
    print(f"Symbol: {symbol}")
    print(f"Period: {days} days")
    print(f"Thresholds to test: {thresholds}")
    print()

    # Load data ONCE
    print("=" * 80)
    print("LOADING DATA (CANONICAL WINDOW)")
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

    # Define canonical window
    end_date = df_1m.index[-1]
    start_date = end_date - timedelta(days=days)
    df_1m_window = df_1m[df_1m.index >= start_date].copy()

    print(f"Canonical window defined:")
    print(f"  Start: {start_date}")
    print(f"  End:   {end_date}")
    print(f"  1m bars: {len(df_1m_window):,}")
    print()

    # Resample ONCE
    df_4h = DataResampler.resample_ohlcv(df_1m_window, '4h')
    print(f"  4h bars: {len(df_4h):,}")
    print()

    # Run sweeps
    results_summary = []

    for i, threshold in enumerate(thresholds, 1):
        print("=" * 80)
        print(f"RUN {i}/{len(thresholds)}: bb_width_pct_threshold={threshold}")
        print("=" * 80)
        print()

        # Get fresh config for each run
        config = get_phase2_3_roc_baseline_config()
        config.use_compression_filter = True
        config.bb_width_pct_threshold = threshold

        print(f"Config: bb_width_pct_threshold={config.bb_width_pct_threshold}, use_compression_filter={config.use_compression_filter}")
        print()

        # Run backtest
        engine = Hybrid4hBacktestEngine(config)

        # Calculate indicators
        df_4h_run, df_1d_run, df_aligned_run = engine.calculate_indicators(df_1m_window)

        # Run strategy
        for idx, (ts, bar) in enumerate(df_aligned_run.iterrows()):
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

        # Extract key metrics
        stats = engine.strategy.get_statistics()
        strategy = engine.strategy

        signals = stats.get('signals', 0)
        entries = stats.get('entries', 0)
        trades = stats.get('trades', 0)
        net_pnl = stats.get('net_pnl', 0)
        win_rate = stats.get('win_rate', 0) * 100 if 'win_rate' in stats else 0

        # Extract gate counts
        gate_audit = strategy.gate_audit
        regime_ok = gate_audit.get('regime_ok', 0)
        viability_ok = gate_audit.get('viability_ok', 0)

        # Extract decomposition
        setup_only = strategy.setup_only_count
        compression_only = strategy.compression_only_count
        intersection = strategy.intersection_count
        neither = strategy.neither_count

        results_summary.append({
            'threshold': threshold,
            'signals': signals,
            'entries': entries,
            'trades': trades,
            'net_pnl': net_pnl,
            'win_rate': win_rate,
            'regime_ok': regime_ok,
            'viability_ok': viability_ok,
            'setup_only': setup_only,
            'compression_only': compression_only,
            'intersection': intersection,
            'neither': neither
        })

        # Print brief results
        print()
        print(f"Results for threshold={threshold}:")
        print(f"  Signals: {signals}")
        print(f"  Trades: {trades}")
        print(f"  Net P&L: ${net_pnl:.2f}")
        print(f"  Win Rate: {win_rate:.1f}%")
        print(f"  Viable bars: {viability_ok}")
        print(f"  Intersection (setup+compression): {intersection}")
        print()

    # Print comparison table
    print()
    print("=" * 80)
    print("SWEEP COMPARISON TABLE")
    print("=" * 80)
    print()

    print(f"{'Threshold':<12} {'Signals':<10} {'Trades':<10} {'Net P&L':<12} {'Win%':<10} {'Viable':<10} {'Intersect':<12}")
    print("-" * 80)

    for r in results_summary:
        print(
            f"{r['threshold']:<12} "
            f"{r['signals']:<10} "
            f"{r['trades']:<10} "
            f"${r['net_pnl']:<11.2f} "
            f"{r['win_rate']:<9.1f}% "
            f"{r['viability_ok']:<10} "
            f"{r['intersection']:<12}"
        )

    print()
    print("=" * 80)
    print("INTERPRETATION")
    print("=" * 80)
    print()

    # Find threshold with most intersections
    best_intersection = max(results_summary, key=lambda x: x['intersection'])
    worst_intersection = min(results_summary, key=lambda x: x['intersection'])

    print(f"Highest intersection: threshold={best_intersection['threshold']} with {best_intersection['intersection']} bars")
    print(f"Lowest intersection: threshold={worst_intersection['threshold']} with {worst_intersection['intersection']} bars")
    print()

    if best_intersection['intersection'] <= 5:
        print("⚠️  All thresholds show very low intersection (<= 5 bars)")
        print("   → Compression filter is too strict across all tested values")
        print("   → Consider testing higher thresholds (70, 80) or disabling compression")
    elif best_intersection['intersection'] > 50:
        print("⚠️  Highest threshold shows very high intersection (> 50 bars)")
        print("   → Compression filter may be too loose")
        print("   → Consider using lower threshold for better selectivity")
    else:
        print(f"✅ Threshold {best_intersection['threshold']} provides reasonable balance")

    print()
    print("=" * 80)


if __name__ == "__main__":
    # Parse command line args
    thresholds = [30, 40, 50, 60]

    if len(sys.argv) > 1:
        if sys.argv[1] in ["--help", "-h"]:
            print(__doc__)
            sys.exit(0)
        elif sys.argv[1] == "--thresholds":
            if len(sys.argv) < 3:
                print("ERROR: --thresholds requires values (e.g., --thresholds 30,40,50,60)")
                sys.exit(1)
            thresholds = [int(x.strip()) for x in sys.argv[2].split(',')]
        else:
            print(f"Unknown argument: {sys.argv[1]}")
            print("Use --thresholds 30,40,50,60 or --help")
            sys.exit(1)

    run_sweep(thresholds=thresholds)
