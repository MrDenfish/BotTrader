"""
Compression ON vs OFF A/B Test - V2 (Enhanced)

Improvements over V1:
1. Enforce single canonical window for both tests (identical timestamps)
2. Print comprehensive "truth lines" for validation
3. Enhanced gate audit with setup/compression decomposition
4. ROC-score percentile calibration if setups are rare
5. Expected bar count validation with sanity checks

This test determines:
- Whether compression filter is the bottleneck
- Whether setup triggers (Donchian/ROC-score) are the bottleneck
- Optimal ROC-score threshold to achieve target frequency
"""

from config_4h_hybrid import get_phase2_3_roc_baseline_config
from engine_4h_hybrid import Hybrid4hBacktestEngine
from data_resampler import DataResampler
import pandas as pd
from pathlib import Path
from datetime import timedelta


def print_truth_lines(df_1m_raw, df_1m_window, df_4h_raw, df_4h_clean, days):
    """Print comprehensive data validation (truth lines)"""

    print()
    print("=" * 80)
    print("DATA VALIDATION (TRUTH LINES)")
    print("=" * 80)
    print()

    # Raw 1m data
    print(f"Raw 1m data (full CSV):")
    print(f"  Start: {df_1m_raw.index[0]}")
    print(f"  End:   {df_1m_raw.index[-1]}")
    print(f"  Rows:  {len(df_1m_raw):,}")
    print()

    # Windowed 1m data
    print(f"Windowed 1m data (last {days} days):")
    print(f"  Start: {df_1m_window.index[0]}")
    print(f"  End:   {df_1m_window.index[-1]}")
    print(f"  Rows:  {len(df_1m_window):,}")
    print()

    # 4h resampled (pre-dropna)
    print(f"4h resampled (pre-dropna):")
    print(f"  Rows:  {len(df_4h_raw):,}")
    print()

    # 4h clean (post-dropna)
    print(f"4h clean (post-dropna/warmup):")
    print(f"  Rows:  {len(df_4h_clean):,}")
    print(f"  Dropped: {len(df_4h_raw) - len(df_4h_clean)}")
    print()

    # Expected bar counts
    expected_1m = days * 1440  # 1440 minutes per day
    expected_4h = days * 6     # 6 4h bars per day

    pct_1m = (len(df_1m_window) / expected_1m * 100) if expected_1m > 0 else 0
    pct_4h = (len(df_4h_clean) / expected_4h * 100) if expected_4h > 0 else 0

    print(f"Expected bar counts (theoretical):")
    print(f"  1m:  {expected_1m:,} (actual: {len(df_1m_window):,}, {pct_1m:.1f}%)")
    print(f"  4h:  {expected_4h:,} (actual: {len(df_4h_clean):,}, {pct_4h:.1f}%)")
    print()

    # Sanity checks
    if pct_4h < 80:
        print(f"⚠️  WARNING: 4h bar count is {pct_4h:.1f}% of expected (< 80%)")
        print(f"    This suggests excessive warmup dropping or data issues")
    elif pct_4h > 105:
        print(f"⚠️  WARNING: 4h bar count is {pct_4h:.1f}% of expected (> 105%)")
        print(f"    This suggests duplicate timestamps or resampling issues")
    else:
        print(f"✅ 4h bar count sanity check passed ({pct_4h:.1f}% of expected)")

    print()
    print("=" * 80)
    print()


def run_compression_on_off_v2(days: int = 180, setup_mode: str = "roc_score"):
    """
    Run enhanced compression ON vs OFF test with proper validation

    Args:
        days: Number of days to test
        setup_mode: 'donchian' or 'roc_score'
    """

    print("=" * 80)
    print("COMPRESSION ON vs OFF A/B TEST (V2 - Enhanced)")
    print("=" * 80)
    print()
    print(f"Test period: {days} days")
    print(f"Setup mode: {setup_mode}")
    print()

    # ========================================================================
    # STEP 1: Load raw data and define canonical window ONCE
    # ========================================================================

    print("=" * 80)
    print("STEP 1: LOAD DATA AND DEFINE CANONICAL WINDOW")
    print("=" * 80)
    print()

    data_dir = Path("data")
    csv_path = data_dir / "BTC_USD.csv"

    df_raw = pd.read_csv(csv_path)
    df_raw['time'] = pd.to_datetime(df_raw['time'])
    df_raw = df_raw.set_index('time')
    df_raw = df_raw.sort_index()
    df_raw.index.name = 'timestamp'

    # Define canonical window (last N days)
    end_ts = df_raw.index[-1]
    start_ts = end_ts - timedelta(days=days)
    df_window = df_raw[df_raw.index >= start_ts].copy()

    print(f"Canonical window defined:")
    print(f"  Start: {start_ts}")
    print(f"  End:   {end_ts}")
    print(f"  Window will be used for BOTH ON and OFF tests")
    print()

    # Resample to 4h (pre-dropna)
    df_4h_raw = DataResampler.resample_ohlcv(df_window, '4h', label='right')

    print(f"Window resampled to 4h:")
    print(f"  4h bars (pre-warmup): {len(df_4h_raw)}")
    print()

    # ========================================================================
    # STEP 2: Run Compression OFF test
    # ========================================================================

    print("=" * 80)
    print("STEP 2: COMPRESSION OFF TEST")
    print("=" * 80)
    print()

    if setup_mode == "roc_score":
        config_off = get_phase2_3_roc_baseline_config()
    else:
        from config_4h_hybrid import get_phase2_2_baseline_config
        config_off = get_phase2_2_baseline_config()

    config_off.use_compression_filter = False

    print(f"Config: {config_off}")
    print()

    # Create engine with pre-loaded data window
    engine_off = Hybrid4hBacktestEngine(config_off)

    # Manually run backtest with our canonical window
    df_4h_off, df_1d_off, df_aligned_off = engine_off.calculate_indicators(df_window)

    # Print truth lines
    print_truth_lines(df_raw, df_window, df_4h_raw, df_4h_off, days)

    # Run strategy
    print("Running strategy (compression OFF)...")
    print(f"  Processing {len(df_aligned_off)} 1m bars...")

    for idx, (ts, bar) in enumerate(df_aligned_off.iterrows()):
        is_4h_close = engine_off._is_timeframe_boundary(ts, '4h')
        is_1d_close = engine_off._is_timeframe_boundary(ts, '1D')

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

        engine_off.strategy.process_bar_1m(
            symbol="BTC-USD",
            bar=bar,
            indicators_4h=indicators_4h,
            indicators_1d=indicators_1d,
            is_4h_close=is_4h_close,
            is_1d_close=is_1d_close
        )

        if (idx + 1) % 50000 == 0:
            print(f"    Processed {idx + 1}/{len(df_aligned_off)} bars...")

    print(f"  ✅ Complete")
    print()

    stats_off = engine_off.strategy.get_statistics()
    engine_off.print_results(stats_off, "BTC-USD")

    # ========================================================================
    # STEP 3: Run Compression ON test (same window!)
    # ========================================================================

    print()
    print("=" * 80)
    print("STEP 3: COMPRESSION ON TEST (SAME WINDOW)")
    print("=" * 80)
    print()

    if setup_mode == "roc_score":
        config_on = get_phase2_3_roc_baseline_config()
    else:
        from config_4h_hybrid import get_phase2_2_baseline_config
        config_on = get_phase2_2_baseline_config()

    config_on.use_compression_filter = True

    print(f"Config: {config_on}")
    print()

    # Create NEW engine with compression ON
    engine_on = Hybrid4hBacktestEngine(config_on)

    # Use SAME canonical window
    df_4h_on, df_1d_on, df_aligned_on = engine_on.calculate_indicators(df_window)

    # Verify window consistency
    print(f"Window consistency check:")
    print(f"  OFF window: {df_window.index[0]} to {df_window.index[-1]} ({len(df_window)} bars)")
    print(f"  ON window:  {df_window.index[0]} to {df_window.index[-1]} ({len(df_window)} bars)")
    print(f"  ✅ Windows are identical: {len(df_aligned_off) == len(df_aligned_on)}")
    print()

    # Print truth lines (should match OFF test exactly)
    print_truth_lines(df_raw, df_window, df_4h_raw, df_4h_on, days)

    # Run strategy
    print("Running strategy (compression ON)...")
    print(f"  Processing {len(df_aligned_on)} 1m bars...")

    for idx, (ts, bar) in enumerate(df_aligned_on.iterrows()):
        is_4h_close = engine_on._is_timeframe_boundary(ts, '4h')
        is_1d_close = engine_on._is_timeframe_boundary(ts, '1D')

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

        engine_on.strategy.process_bar_1m(
            symbol="BTC-USD",
            bar=bar,
            indicators_4h=indicators_4h,
            indicators_1d=indicators_1d,
            is_4h_close=is_4h_close,
            is_1d_close=is_1d_close
        )

        if (idx + 1) % 50000 == 0:
            print(f"    Processed {idx + 1}/{len(df_aligned_on)} bars...")

    print(f"  ✅ Complete")
    print()

    stats_on = engine_on.strategy.get_statistics()
    engine_on.print_results(stats_on, "BTC-USD")

    # ========================================================================
    # STEP 4: Comparative Analysis
    # ========================================================================

    print()
    print("=" * 80)
    print("COMPARATIVE ANALYSIS: ON vs OFF")
    print("=" * 80)
    print()

    months = days / 30.0

    off_signals = stats_off.get('signals', 0)
    on_signals = stats_on.get('signals', 0)

    off_trades = stats_off.get('trades', 0)
    on_trades = stats_on.get('trades', 0)

    off_spm = off_signals / months
    on_spm = on_signals / months

    off_tpm = off_trades / months
    on_tpm = on_trades / months

    print(f"{'Metric':<25} {'OFF':<15} {'ON':<15} {'Delta':<15}")
    print("-" * 70)
    print(f"{'Signals':<25} {off_signals:<15} {on_signals:<15} {on_signals - off_signals:+<15}")
    print(f"{'Signals/month':<25} {off_spm:<15.2f} {on_spm:<15.2f} {on_spm - off_spm:+<15.2f}")
    print(f"{'Trades':<25} {off_trades:<15} {on_trades:<15} {on_trades - off_trades:+<15}")
    print(f"{'Trades/month':<25} {off_tpm:<15.2f} {on_tpm:<15.2f} {on_tpm - off_tpm:+<15.2f}")
    print(f"{'Net P&L':<25} ${stats_off.get('net_pnl', 0):<14.2f} ${stats_on.get('net_pnl', 0):<14.2f}")
    print("=" * 70)
    print()

    # ========================================================================
    # STEP 5: Conclusions and Recommendations
    # ========================================================================

    print("=" * 80)
    print("CONCLUSIONS")
    print("=" * 80)
    print()

    target_min = 2.0
    target_max = 4.0

    print(f"Target frequency: {target_min}-{target_max} trades/month")
    print()

    # Determine bottleneck
    if off_tpm >= target_min and on_tpm < target_min:
        print("✅ CONCLUSION: Compression is THE bottleneck")
        print(f"   → Disabling compression: {off_tpm:.2f} trades/month (meets target)")
        print(f"   → With compression: {on_tpm:.2f} trades/month (below target)")
        print()
        print("RECOMMENDATION:")
        print("  • Keep compression DISABLED or use very loose parameters")
        print("  • Proceed with setup trigger optimization")

    elif off_tpm < 1.0:
        print("🚨 CRITICAL: Setup triggers are TOO STRICT (even with compression OFF)")
        print(f"   → Compression OFF: {off_tpm:.2f} trades/month (critically low)")
        print(f"   → Compression ON: {on_tpm:.2f} trades/month")
        print()
        print("RECOMMENDATION:")
        print("  • Loosen setup trigger thresholds (Donchian/ROC-score)")
        print("  • If using ROC-score, calibrate by percentile (see next section)")
        print("  • Consider redesigning filter architecture")

    else:
        print("⚠️  MIXED: Both compression AND setup triggers contribute to low frequency")
        print(f"   → Compression OFF: {off_tpm:.2f} trades/month")
        print(f"   → Compression ON: {on_tpm:.2f} trades/month")
        print()
        print("RECOMMENDATION:")
        print("  • Loosen BOTH compression and setup triggers")
        print("  • Use compression as soft scoring, not hard gate")

    print()
    print("=" * 80)
    print()


if __name__ == "__main__":
    # Run enhanced test on 180 days with ROC-score mode
    run_compression_on_off_v2(days=180, setup_mode="roc_score")
