"""
60-Day Backtest: 4h Hybrid Maker Strategy

Phase 1 minimal implementation test on BTC-USD.

Test both fee scenarios:
1. Worst-case: maker=0.6%, taker=1.2%
2. Closer-to-actual: maker=0.4%, taker=0.8%

Success criteria:
- Net P&L positive under closer-to-actual fees
- Maker fill rate >70% (entries + TPs)
- >50% of trades reach TP1 (2× fees)
- Trade frequency: 2-4 trades/month
- Win rate: >50%
"""

import sys
from datetime import datetime

from config_4h_hybrid import Hybrid4hConfig, get_default_config, get_closer_to_actual_fees_config
from engine_4h_hybrid import Hybrid4hBacktestEngine


def run_test():
    """Run 60-day BTC backtest with both fee scenarios"""

    print("=" * 80)
    print("60-DAY 4H HYBRID MAKER STRATEGY TEST")
    print("=" * 80)
    print()
    print("Testing BTC-USD with two fee scenarios:")
    print("  1. Worst-case: maker=0.6%, taker=1.2%")
    print("  2. Closer-to-actual: maker=0.4%, taker=0.8%")
    print()
    print("=" * 80)
    print()

    symbol = "BTC-USD"
    days = 60

    # ========== Test 1: Worst-Case Fees ==========

    print("\n" + "=" * 80)
    print("TEST 1: WORST-CASE FEES (maker=0.6%, taker=1.2%)")
    print("=" * 80)

    config_worst = get_default_config()
    engine_worst = Hybrid4hBacktestEngine(config_worst)

    stats_worst = engine_worst.run_backtest(symbol, days)
    engine_worst.print_results(stats_worst, symbol)
    engine_worst.export_trades("trades_4h_hybrid_btc_worst_case.csv")

    # ========== Test 2: Closer-to-Actual Fees ==========

    print("\n" + "=" * 80)
    print("TEST 2: CLOSER-TO-ACTUAL FEES (maker=0.4%, taker=0.8%)")
    print("=" * 80)

    config_actual = get_closer_to_actual_fees_config()
    engine_actual = Hybrid4hBacktestEngine(config_actual)

    stats_actual = engine_actual.run_backtest(symbol, days)
    engine_actual.print_results(stats_actual, symbol)
    engine_actual.export_trades("trades_4h_hybrid_btc_closer_to_actual.csv")

    # ========== Success Criteria Analysis ==========

    print("\n" + "=" * 80)
    print("SUCCESS CRITERIA ANALYSIS")
    print("=" * 80)
    print()

    # Use closer-to-actual stats for criteria
    if stats_actual['trades'] == 0:
        print("❌ No trades generated - strategy did not trigger")
        return

    trades_per_month = (stats_actual['trades'] / days) * 30
    tp1_rate = stats_actual.get('tp1_hit_pct', 0)
    win_rate = stats_actual.get('win_rate', 0)
    net_pnl = stats_actual.get('net_pnl', 0)

    print("Criteria                          | Target      | Actual       | Status")
    print("-" * 80)

    # 1. Net P&L positive
    status_pnl = "✅ PASS" if net_pnl > 0 else "❌ FAIL"
    print(f"Net P&L positive                  | > $0        | ${net_pnl:>8.2f}  | {status_pnl}")

    # 2. Trade frequency
    status_freq = "✅ PASS" if 2 <= trades_per_month <= 4 else "⚠️  OUT OF RANGE"
    print(f"Trade frequency                   | 2-4/month   | {trades_per_month:>8.1f}/mo | {status_freq}")

    # 3. TP1 hit rate
    status_tp1 = "✅ PASS" if tp1_rate >= 50 else "❌ FAIL"
    print(f">50% reach TP1 (2× fees)          | >50%        | {tp1_rate:>8.1f}%  | {status_tp1}")

    # 4. Win rate
    status_win = "✅ PASS" if win_rate >= 0.50 else "❌ FAIL"
    print(f"Win rate                          | >50%        | {win_rate:>8.1%}  | {status_win}")

    print("-" * 80)

    # Overall verdict
    all_pass = (net_pnl > 0 and tp1_rate >= 50 and win_rate >= 0.50)

    print()
    if all_pass:
        print("🎉 STRATEGY VIABLE - All core criteria passed!")
        print()
        print("Next steps:")
        print("  - Review trade log for quality")
        print("  - Test on other symbols (ETH, SOL)")
        print("  - Consider Phase 2 features (pullback-reclaim, ROC-score)")
    else:
        print("⚠️  STRATEGY NOT YET VIABLE")
        print()
        print("Possible improvements:")
        if net_pnl <= 0:
            print("  - Adjust fee multiples (try 1.5× and 3× instead of 2× and 4×)")
            print("  - Widen Donchian period (try 30 instead of 20)")
            print("  - Tighten viability filter (require 2.5× fees min vol)")
        if tp1_rate < 50:
            print("  - Lower TP1 target (try 1.5× fees)")
            print("  - Review if stops are too tight")
        if win_rate < 0.50:
            print("  - Review entry quality (maybe add EMA50 slope filter)")
            print("  - Consider tighter stops (1.5× ATR%)")

    print()
    print("=" * 80)
    print()

    # ========== Comparison Table ==========

    print("=" * 80)
    print("FEE SCENARIO COMPARISON")
    print("=" * 80)
    print()

    print(f"{'Metric':<30} | {'Worst-Case':<15} | {'Closer-Actual':<15}")
    print("-" * 80)
    print(f"{'Maker Fee':<30} | {config_worst.maker_fee:>14.1%} | {config_actual.maker_fee:>14.1%}")
    print(f"{'Taker Fee':<30} | {config_worst.taker_fee:>14.1%} | {config_actual.taker_fee:>14.1%}")
    print(f"{'Trades':<30} | {stats_worst['trades']:>15} | {stats_actual['trades']:>15}")
    print(f"{'Gross P&L':<30} | ${stats_worst.get('gross_pnl', 0):>13.2f} | ${stats_actual.get('gross_pnl', 0):>13.2f}")
    print(f"{'Fees':<30} | ${stats_worst.get('fees', 0):>13.2f} | ${stats_actual.get('fees', 0):>13.2f}")
    print(f"{'Net P&L':<30} | ${stats_worst.get('net_pnl', 0):>13.2f} | ${stats_actual.get('net_pnl', 0):>13.2f}")

    if stats_actual['trades'] > 0:
        print(f"{'Win Rate':<30} | {stats_worst.get('win_rate', 0):>14.1%} | {stats_actual.get('win_rate', 0):>14.1%}")
        print(f"{'TP1 Hit Rate':<30} | {stats_worst.get('tp1_hit_pct', 0):>13.1f}% | {stats_actual.get('tp1_hit_pct', 0):>13.1f}%")
        print(f"{'TP2 Hit Rate':<30} | {stats_worst.get('tp2_hit_pct', 0):>13.1f}% | {stats_actual.get('tp2_hit_pct', 0):>13.1f}%")

    print("=" * 80)


if __name__ == "__main__":
    run_test()
