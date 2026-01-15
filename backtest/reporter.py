"""
Backtest Results Reporter

Formats and displays backtest results.
"""

from backtest.models import BacktestResults, ExitReason
from decimal import Decimal


class BacktestReporter:
    """Generate formatted backtest reports"""

    @staticmethod
    def print_summary(results: BacktestResults):
        """Print comprehensive backtest summary"""
        summary = results.get_summary()

        print("\n" + "=" * 70)
        print("  BACKTEST RESULTS SUMMARY")
        print("=" * 70)
        print()

        # Overview
        print("📊 Overview:")
        print("-" * 70)
        print(f"  Strategy: {summary['strategy']}")
        print(f"  Period: {summary['period']}")
        print(f"  Initial Capital: ${summary['initial_capital']:,.2f}")
        print(f"  Final Capital: ${summary['final_capital']:,.2f}")
        print()

        # Performance
        print("💰 Performance:")
        print("-" * 70)
        pnl = summary['total_pnl']
        pnl_color = "+" if pnl >= 0 else ""
        print(f"  Total P&L: {pnl_color}${pnl:,.2f}")
        print(f"  Total Return: {pnl_color}{summary['total_return_pct']:.2f}%")
        print(f"  Total Fees: ${summary['total_fees']:,.2f}")
        print()

        # Trade Statistics
        print("📈 Trade Statistics:")
        print("-" * 70)
        print(f"  Total Trades: {summary['total_trades']}")
        print(f"  Win Rate: {summary['win_rate_pct']:.1f}%")
        print(f"  Profit Factor: {summary['profit_factor']:.2f}")
        print(f"  Average Win: ${summary['avg_win']:,.2f}")
        print(f"  Average Loss: ${summary['avg_loss']:,.2f}")
        print()

        # Risk Metrics
        print("⚠️  Risk Metrics:")
        print("-" * 70)
        print(f"  Max Drawdown: ${summary['max_drawdown']:,.2f}")
        print(f"  Max Drawdown %: {summary['max_drawdown_pct']:.2f}%")
        print()

        # Trade Breakdown
        print("🔍 Trade Breakdown:")
        print("-" * 70)
        print(f"  ROC Momentum Trades: {summary['roc_trades']}")
        print(f"  Standard Signal Trades: {summary['signal_trades']}")
        print()
        print(f"  Take Profit Exits: {summary['tp_exits']}")
        print(f"  Stop Loss Exits: {summary['sl_exits']}")
        print(f"  ROC Peak/Reversal Exits: {summary['roc_exits']}")
        print()

        # Verdict
        print("=" * 70)
        if summary['total_return_pct'] > 0:
            print("  ✅ PROFITABLE STRATEGY")
        else:
            print("  ❌ UNPROFITABLE STRATEGY")
        print("=" * 70)
        print()

    @staticmethod
    def print_trade_list(results: BacktestResults, limit: int = 20):
        """Print list of individual trades"""
        print("\n" + "=" * 70)
        print(f"  TRADE HISTORY (Showing {min(limit, len(results.trades))} of {len(results.trades)} trades)")
        print("=" * 70)
        print()

        for i, trade in enumerate(results.trades[:limit]):
            win_loss = "WIN" if trade.is_winner else "LOSS"
            symbol = f"{win_loss:4} {trade.symbol:12}"
            pnl = f"${trade.net_pnl:+,.2f}"
            reason = trade.exit_reason.value
            duration = f"{trade.hold_time_hours:.1f}h"

            print(f"  {i+1:3}. {symbol} | {pnl:12} | {reason:20} | {duration}")

        if len(results.trades) > limit:
            print(f"\n  ... and {len(results.trades) - limit} more trades")

        print()

    @staticmethod
    def export_csv(results: BacktestResults, filename: str):
        """Export trades to CSV file"""
        import csv

        with open(filename, 'w', newline='') as f:
            writer = csv.writer(f)

            # Header
            writer.writerow([
                'Trade#', 'Symbol', 'Type', 'Side',
                'Entry Time', 'Entry Price', 'Entry Fee',
                'Exit Time', 'Exit Price', 'Exit Fee',
                'Size', 'Gross P&L', 'Net P&L', 'Return %',
                'Hold Hours', 'Exit Reason',
                'Peak Price', 'Peak ROC'
            ])

            # Trades
            for i, trade in enumerate(results.trades, 1):
                writer.writerow([
                    i,
                    trade.symbol,
                    trade.trade_type.value,
                    trade.side,
                    trade.entry_time,
                    float(trade.entry_price),
                    float(trade.entry_fee),
                    trade.exit_time,
                    float(trade.exit_price),
                    float(trade.exit_fee),
                    float(trade.size),
                    float(trade.gross_pnl),
                    float(trade.net_pnl),
                    float(trade.return_pct),
                    trade.hold_time_hours,
                    trade.exit_reason.value,
                    float(trade.peak_price) if trade.peak_price else None,
                    float(trade.peak_roc) if trade.peak_roc else None
                ])

        print(f"✅ Trades exported to: {filename}")

    @staticmethod
    def compare_strategies(results_list: list[BacktestResults]):
        """Compare multiple strategy configurations"""
        print("\n" + "=" * 100)
        print("  STRATEGY COMPARISON")
        print("=" * 100)
        print()

        print(f"{'Strategy':<30} {'Trades':>8} {'Win%':>8} {'P&L':>12} {'Return%':>10} {'Profit Factor':>15}")
        print("-" * 100)

        for results in results_list:
            summary = results.get_summary()
            name = summary['strategy'][:28]
            trades = summary['total_trades']
            win_rate = summary['win_rate_pct']
            pnl = summary['total_pnl']
            ret = summary['total_return_pct']
            pf = summary['profit_factor']

            print(f"{name:<30} {trades:>8} {win_rate:>7.1f}% ${pnl:>10,.2f} {ret:>9.2f}% {pf:>15.2f}")

        print()
