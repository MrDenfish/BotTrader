"""
Comprehensive Multi-Timeframe Backtest Comparison
==================================================

Tests all 4 configurations:
1. 5m + continuation entry
2. 5m + pullback entry
3. 15m + continuation entry
4. 15m + pullback entry

Compares results to find the best configuration for further optimization.

Date: January 30, 2026
"""

from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
import pandas as pd

from backtest.engine_multi_tf import BacktestEngineMultiTF
from backtest.strategy_multi_tf import MultiTFConfig


def run_comprehensive_test():
    """
    Run all 4 configuration combinations and compare results.
    """
    print("="*80)
    print("COMPREHENSIVE MULTI-TIMEFRAME BACKTEST")
    print("="*80)
    print("\nTesting 4 configurations:")
    print("  1. 5m + continuation")
    print("  2. 5m + pullback")
    print("  3. 15m + continuation")
    print("  4. 15m + pullback")
    print("\n" + "="*80)

    # Test period: 7 days
    end_date = datetime(2026, 1, 29)
    start_date = end_date - timedelta(days=7)

    # Test symbols
    symbols = ['BTC-USD', 'ETH-USD', 'SOL-USD']

    results_summary = []

    # =========================================================================
    # Configuration 1: 5m + continuation
    # =========================================================================
    print("\n\n" + "="*80)
    print("TEST 1: 5m + CONTINUATION")
    print("="*80)

    config_5m_cont = MultiTFConfig(
        variant_name="5M_CONT",
        signal_timeframe='5m',
        entry_mode='continuation',

        # ROC parameters (scaled for 5m)
        roc_len=9,
        roc_ema_len=5,
        roc_entry_threshold=0.008,  # 0.8%
        roc_arm_threshold=0.004,  # 0.4%

        # Volume filter
        vol_sma_len=20,
        vol_ratio_min=2.0,

        # ATR_fast (on 5m)
        atr_fast_len=14,
        M_fast=2.0,
        fast_trail_activation_k=1.0,

        # ATR_slow (on 1h for 5m signals)
        tf_slow='1h',
        atr_slow_len=14,
        M_slow=3.0,

        # ROC exit
        X_dd=0.40,

        # Entry execution
        entry_max_bars_to_wait=10,
        min_bars_after_entry_for_exit=2,

        # Position sizing
        order_size=Decimal("100.00"),
        fee_rate=Decimal("0.006")
    )

    engine = BacktestEngineMultiTF(config_5m_cont, symbols, start_date, end_date)
    engine.load_data()
    engine.calculate_indicators()
    engine.run_backtest()
    results_1 = engine.print_results()
    results_summary.append(('5m + continuation', results_1))

    # =========================================================================
    # Configuration 2: 5m + pullback
    # =========================================================================
    print("\n\n" + "="*80)
    print("TEST 2: 5m + PULLBACK")
    print("="*80)

    config_5m_pull = MultiTFConfig(
        variant_name="5M_PULLBACK",
        signal_timeframe='5m',
        entry_mode='pullback',  # CHANGED

        # Same parameters as continuation
        roc_len=9,
        roc_ema_len=5,
        roc_entry_threshold=0.008,
        roc_arm_threshold=0.004,
        vol_sma_len=20,
        vol_ratio_min=2.0,
        atr_fast_len=14,
        M_fast=2.0,
        fast_trail_activation_k=1.0,
        tf_slow='1h',
        atr_slow_len=14,
        M_slow=3.0,
        X_dd=0.40,
        entry_max_bars_to_wait=10,
        pullback_retrace_pct=0.5,
        min_bars_after_entry_for_exit=2,
        order_size=Decimal("100.00"),
        fee_rate=Decimal("0.006")
    )

    engine = BacktestEngineMultiTF(config_5m_pull, symbols, start_date, end_date)
    engine.load_data()
    engine.calculate_indicators()
    engine.run_backtest()
    results_2 = engine.print_results()
    results_summary.append(('5m + pullback', results_2))

    # =========================================================================
    # Configuration 3: 15m + continuation
    # =========================================================================
    print("\n\n" + "="*80)
    print("TEST 3: 15m + CONTINUATION")
    print("="*80)

    config_15m_cont = MultiTFConfig(
        variant_name="15M_CONT",
        signal_timeframe='15m',  # CHANGED
        entry_mode='continuation',

        # ROC parameters (scaled for 15m - even higher threshold)
        roc_len=9,
        roc_ema_len=5,
        roc_entry_threshold=0.012,  # 1.2% (higher for 15m)
        roc_arm_threshold=0.006,  # 0.6%

        # Volume filter
        vol_sma_len=20,
        vol_ratio_min=2.0,

        # ATR_fast (on 15m)
        atr_fast_len=14,
        M_fast=2.0,
        fast_trail_activation_k=1.0,

        # ATR_slow (on 4h for 15m signals)
        tf_slow='4h',  # CHANGED
        atr_slow_len=14,
        M_slow=3.0,

        # ROC exit
        X_dd=0.40,

        # Entry execution
        entry_max_bars_to_wait=10,
        min_bars_after_entry_for_exit=2,

        # Position sizing
        order_size=Decimal("100.00"),
        fee_rate=Decimal("0.006")
    )

    engine = BacktestEngineMultiTF(config_15m_cont, symbols, start_date, end_date)
    engine.load_data()
    engine.calculate_indicators()
    engine.run_backtest()
    results_3 = engine.print_results()
    results_summary.append(('15m + continuation', results_3))

    # =========================================================================
    # Configuration 4: 15m + pullback
    # =========================================================================
    print("\n\n" + "="*80)
    print("TEST 4: 15m + PULLBACK")
    print("="*80)

    config_15m_pull = MultiTFConfig(
        variant_name="15M_PULLBACK",
        signal_timeframe='15m',  # CHANGED
        entry_mode='pullback',  # CHANGED

        # Same parameters as 15m continuation
        roc_len=9,
        roc_ema_len=5,
        roc_entry_threshold=0.012,
        roc_arm_threshold=0.006,
        vol_sma_len=20,
        vol_ratio_min=2.0,
        atr_fast_len=14,
        M_fast=2.0,
        fast_trail_activation_k=1.0,
        tf_slow='4h',
        atr_slow_len=14,
        M_slow=3.0,
        X_dd=0.40,
        entry_max_bars_to_wait=10,
        pullback_retrace_pct=0.5,
        min_bars_after_entry_for_exit=2,
        order_size=Decimal("100.00"),
        fee_rate=Decimal("0.006")
    )

    engine = BacktestEngineMultiTF(config_15m_pull, symbols, start_date, end_date)
    engine.load_data()
    engine.calculate_indicators()
    engine.run_backtest()
    results_4 = engine.print_results()
    results_summary.append(('15m + pullback', results_4))

    # =========================================================================
    # Print comparison table
    # =========================================================================
    print("\n\n" + "="*80)
    print("RESULTS COMPARISON")
    print("="*80)
    print("\nKey Metrics:")
    print(f"{'Configuration':<20} {'Signals':<8} {'Entries':<8} {'Conv%':<7} {'Trades':<7} "
          f"{'P&L':<10} {'Win%':<7} {'PF':<6} {'Avg Hold':<9} {'Gross PnL':<10} {'Fees':<10}")
    print("-" * 120)

    for config_name, results in results_summary:
        print(f"{config_name:<20} "
              f"{results['total_signals']:<8} "
              f"{results['total_entries']:<8} "
              f"{results['signal_to_entry_pct']:<7.1f} "
              f"{results['total_trades']:<7} "
              f"${results['total_pnl']:<9.2f} "
              f"{results['win_rate']*100:<7.1f} "
              f"{results['profit_factor']:<6.2f} "
              f"{results['avg_bars_held']:<9.1f} "
              f"${results['gross_pnl']:<9.2f} "
              f"${results['total_fees']:<9.2f}")

    print("\n" + "="*80)
    print("ANALYSIS")
    print("="*80)

    # Find best by net P&L
    best = max(results_summary, key=lambda x: x[1]['total_pnl'])
    print(f"\n✅ Best Net P&L: {best[0]} (${best[1]['total_pnl']:.2f})")

    # Find best by gross P&L (shows edge before fees)
    best_gross = max(results_summary, key=lambda x: x[1]['gross_pnl'])
    print(f"✅ Best Gross P&L: {best_gross[0]} (${best_gross[1]['gross_pnl']:.2f})")

    # Find best by profit factor
    best_pf = max(results_summary, key=lambda x: x[1]['profit_factor'])
    print(f"✅ Best Profit Factor: {best_pf[0]} ({best_pf[1]['profit_factor']:.2f})")

    # Find best by win rate
    best_wr = max(results_summary, key=lambda x: x[1]['win_rate'])
    print(f"✅ Best Win Rate: {best_wr[0]} ({best_wr[1]['win_rate']*100:.1f}%)")

    # Check if any have positive gross P&L
    positive_gross = [x for x in results_summary if x[1]['gross_pnl'] > 0]
    if positive_gross:
        print(f"\n🎯 {len(positive_gross)} configuration(s) show positive gross edge:")
        for config_name, results in positive_gross:
            print(f"   {config_name}: Gross ${results['gross_pnl']:.2f}, "
                  f"Fees ${results['total_fees']:.2f}, Net ${results['total_pnl']:.2f}")
    else:
        print("\n⚠️  NO configurations show positive gross edge before fees")
        print("   Strategy may need different parameters or this period is not favorable")

    print("\n" + "="*80)

    return results_summary


if __name__ == "__main__":
    run_comprehensive_test()
