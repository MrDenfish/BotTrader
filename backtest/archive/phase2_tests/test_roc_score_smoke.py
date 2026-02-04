#!/usr/bin/env python3
"""
ROC-Score Setup Mode Smoke Test

Validates that ROC-score implementation works correctly:
1. ROC indicator is calculated properly
2. ROC-score setup detection triggers
3. Entry/exit mechanics work with ROC setups
4. Results are reasonable (no crashes, some signals)

This is NOT a performance test - just a sanity check.
"""

from config_4h_hybrid import get_phase2_3_roc_baseline_config
from engine_4h_hybrid import Hybrid4hBacktestEngine


def run_smoke_test():
    """Run ROC-score mode smoke test"""

    print("=" * 80)
    print("ROC-SCORE SETUP MODE SMOKE TEST")
    print("=" * 80)
    print()
    print("Testing Phase 2.3 ROC-score implementation...")
    print()
    print("=" * 80)
    print()

    symbol = "BTC-USD"
    days = 60

    # Get Phase 2.3 ROC baseline config
    config = get_phase2_3_roc_baseline_config()

    print(f"Config: {config.setup_mode} mode")
    print(f"  ROC length: {config.roc_len_4h} bars (4h)")
    print(f"  ROC EMA smoothing: {config.roc_ema_len} bars (4h)")
    print(f"  ROC-score threshold: {config.roc_score_thresh}")
    print(f"  Compression filter: {config.use_compression_filter}")
    print()

    # Run backtest on 1m data
    print("📊 RUNNING ROC-SCORE BACKTEST...")
    print()
    engine = Hybrid4hBacktestEngine(config, data_dir="data", resolution='1m')
    stats = engine.run_backtest(symbol, days)

    print()
    print("=" * 80)
    print("SMOKE TEST RESULTS")
    print("=" * 80)
    print()

    if "error" in stats:
        print(f"❌ ERROR: {stats['error']}")
        print()
        print("=" * 80)
        return False

    # Print key metrics
    print(f"Signals: {stats['signals']}")
    print(f"Entries: {stats['entries']}")
    print(f"Trades: {stats['trades']}")
    print(f"Expired: {stats['expired']}")
    print()

    if stats['trades'] > 0:
        print(f"Net P&L: ${stats.get('net_pnl', 0):.2f}")
        print(f"Win Rate: {stats.get('win_rate', 0):.1%}")
        print(f"TP1 Hit: {stats.get('tp1_hit_pct', 0):.1f}%")
        print()

    # Validation checks
    print("=" * 80)
    print("VALIDATION CHECKS")
    print("=" * 80)
    print()

    all_pass = True

    # Check 1: No errors
    if "error" in stats:
        print("❌ FAIL: Backtest encountered an error")
        all_pass = False
    else:
        print("✅ PASS: Backtest completed without errors")

    # Check 2: Some signals (even if zero is technically valid)
    if stats['signals'] > 0:
        print(f"✅ PASS: Generated signals ({stats['signals']})")
    else:
        print("⚠️  WARNING: No signals generated (ROC threshold may be too high)")

    # Check 3: Signal→Entry rate is reasonable (if signals exist)
    if stats['signals'] > 0:
        signal_to_entry_pct = stats.get('signal_to_entry_pct', 0)
        if signal_to_entry_pct > 0:
            print(f"✅ PASS: Signal→Entry rate: {signal_to_entry_pct:.1f}%")
        else:
            print("⚠️  WARNING: Signals generated but no entries (all setups expired)")

    # Check 4: Trades completed (if entries exist)
    if stats['entries'] > 0 and stats['trades'] > 0:
        print(f"✅ PASS: Trades completed ({stats['trades']})")
    elif stats['entries'] > 0:
        print("⚠️  WARNING: Entries exist but no trades completed")

    # Check 5: P&L is calculated (if trades exist)
    if stats['trades'] > 0:
        net_pnl = stats.get('net_pnl', 0)
        if net_pnl != 0:
            print(f"✅ PASS: P&L calculated (${net_pnl:.2f})")
        else:
            print("⚠️  WARNING: P&L is exactly zero (unusual)")

    print()
    print("=" * 80)
    print("SMOKE TEST SUMMARY")
    print("=" * 80)
    print()

    if all_pass and stats['signals'] > 0:
        print("✅ SMOKE TEST PASSED")
        print()
        print("ROC-score implementation is working correctly:")
        print("  • ROC indicators calculated")
        print("  • Setup detection functional")
        print("  • Entry/exit mechanics work")
        print()
        print("Next steps:")
        print("  • Run baseline validation on 365 days")
        print("  • Compare signal characteristics vs Donchian")
        print("  • Begin Stage A optimization (gates/entry)")
    else:
        print("⚠️  SMOKE TEST COMPLETED WITH WARNINGS")
        print()
        print("Implementation appears functional but may need tuning:")
        print("  • Review ROC threshold (may be too conservative)")
        print("  • Check compression filter settings")
        print("  • Inspect market conditions for test period")

    print()
    print("=" * 80)
    print()

    return all_pass


if __name__ == "__main__":
    success = run_smoke_test()
    exit(0 if success else 1)
