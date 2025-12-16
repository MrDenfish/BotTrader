#!/usr/bin/env python3
"""
BotTrader Performance Diagnostic Tool

Analyzes trading performance to identify profitability issues.
Run this on your production server to diagnose strategy problems.

Usage:
    python diagnostic_performance_analysis.py [--days 30] [--detailed]
"""

import asyncio
import argparse
import json
import os
import sys
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Dict, List, Tuple

# Add project to path
sys.path.insert(0, str(Path(__file__).parent))

from database_manager.database_session_manager import DatabaseSessionManager
from sqlalchemy import text


class _SimpleLogger:
    """Simple logger for diagnostic output."""
    def debug(self, msg, **kwargs): pass
    def info(self, msg, **kwargs): print(f"ℹ️  {msg}")
    def warning(self, msg, **kwargs): print(f"⚠️  {msg}")
    def error(self, msg, **kwargs): print(f"❌ {msg}")
    def exception(self, msg, **kwargs): print(f"❌ {msg}")


class PerformanceDiagnostic:
    """Comprehensive performance diagnostic analyzer."""

    def __init__(self, days: int = 30, detailed: bool = False):
        self.days = days
        self.detailed = detailed
        self.db = None

    async def initialize(self):
        """Initialize database connection."""
        # Get DSN from environment (same as main.py)
        dsn = os.getenv("DATABASE_URL") or os.getenv("TRADEBOT_DATABASE_URL")
        if not dsn:
            raise RuntimeError("DATABASE_URL or TRADEBOT_DATABASE_URL must be set")

        # Normalize to async driver
        if dsn.startswith("postgres://"):
            dsn = dsn.replace("postgres://", "postgresql+asyncpg://", 1)
        elif dsn.startswith("postgresql://") and "+asyncpg" not in dsn:
            dsn = dsn.replace("postgresql://", "postgresql+asyncpg://", 1)

        # Create DatabaseSessionManager with proper initialization
        logger = _SimpleLogger()
        self.db = DatabaseSessionManager(
            dsn,
            logger=logger,
            echo=False,
            pool_size=int(os.getenv("DB_POOL_SIZE", "5")),
            max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "5")),
            pool_timeout=int(os.getenv("DB_POOL_TIMEOUT", "10")),
            pool_recycle=int(os.getenv("DB_POOL_RECYCLE", "300")),
            pool_pre_ping=True,
            future=True,
        )

        await self.db.initialize()

    async def close(self):
        """Close database connection."""
        if self.db is not None:
            await self.db.close()

    async def analyze_all(self):
        """Run all diagnostic analyses."""
        print("=" * 80)
        print(f"🔍 BOTTRADER PERFORMANCE DIAGNOSTIC (Last {self.days} Days)")
        print("=" * 80)
        print()

        await self.overall_performance()
        await self.win_rate_analysis()
        await self.profit_factor_analysis()
        await self.symbol_performance()
        await self.timing_analysis()
        await self.fee_impact_analysis()
        await self.tp_sl_effectiveness()
        await self.leaderboard_status()

        print("\n" + "=" * 80)
        print("📋 DIAGNOSTIC COMPLETE")
        print("=" * 80)

    async def overall_performance(self):
        """Analyze overall performance metrics."""
        print("📊 OVERALL PERFORMANCE SUMMARY")
        print("-" * 80)

        async with self.db.session() as session:
            query = text("""
                SELECT
                    COUNT(*) as total_trades,
                    COUNT(CASE WHEN pnl_usd > 0.01 THEN 1 END) as wins,
                    COUNT(CASE WHEN pnl_usd < -0.01 THEN 1 END) as losses,
                    COUNT(CASE WHEN ABS(pnl_usd) <= 0.01 THEN 1 END) as breakeven,
                    ROUND(SUM(pnl_usd)::numeric, 2) as total_pnl,
                    ROUND(AVG(pnl_usd)::numeric, 4) as avg_pnl,
                    ROUND(AVG(CASE WHEN pnl_usd > 0.01 THEN pnl_usd END)::numeric, 2) as avg_win,
                    ROUND(AVG(CASE WHEN pnl_usd < -0.01 THEN pnl_usd END)::numeric, 2) as avg_loss,
                    ROUND(SUM(total_fees_usd)::numeric, 2) as total_fees,
                    ROUND(AVG(total_fees_usd)::numeric, 4) as avg_fee
                FROM trade_records
                WHERE status = 'closed'
                AND order_time > NOW() - INTERVAL ':days days'
            """.replace(':days', str(self.days)))

            result = await session.execute(query)
            row = result.fetchone()

            if not row or not row.total_trades:
                print("❌ No closed trades found in the specified period.")
                print()
                return

            total = row.total_trades
            wins = row.wins or 0
            losses = row.losses or 0

            win_rate = (wins / total * 100) if total > 0 else 0

            print(f"Total Closed Trades: {total}")
            print(f"  Wins: {wins} ({win_rate:.1f}%)")
            print(f"  Losses: {losses} ({losses/total*100:.1f}%)")
            print(f"  Breakeven: {row.breakeven or 0}")
            print()
            print(f"Total PnL: ${row.total_pnl:.2f}")
            print(f"Average PnL per Trade: ${row.avg_pnl:.4f}")
            print(f"  Average Win: ${row.avg_win or 0:.2f}")
            print(f"  Average Loss: ${row.avg_loss or 0:.2f}")
            print()
            print(f"Total Fees Paid: ${row.total_fees:.2f}")
            print(f"Average Fee per Trade: ${row.avg_fee:.4f}")
            print(f"Net PnL (after fees): ${(row.total_pnl or 0) - (row.total_fees or 0):.2f}")

            # Calculate profit factor
            if wins > 0 and losses > 0 and row.avg_win and row.avg_loss:
                profit_factor = abs(float(row.avg_win) * wins / (float(row.avg_loss) * losses))
                print(f"\nProfit Factor: {profit_factor:.2f}", end="")
                if profit_factor < 1.0:
                    print(" ⚠️  (< 1.0 = losing strategy)")
                elif profit_factor < 1.3:
                    print(" ⚠️  (< 1.3 = marginal)")
                elif profit_factor < 2.0:
                    print(" ✓ (healthy)")
                else:
                    print(" ✓✓ (excellent)")

            # Risk/Reward ratio
            if row.avg_win and row.avg_loss:
                rr_ratio = abs(float(row.avg_win) / float(row.avg_loss))
                print(f"Risk:Reward Ratio: 1:{rr_ratio:.2f}", end="")
                if rr_ratio < 1.0:
                    print(" ⚠️  (losses > wins)")
                elif rr_ratio < 1.5:
                    print(" ⚠️  (marginal)")
                else:
                    print(" ✓")

            print()

    async def win_rate_analysis(self):
        """Analyze win rate patterns."""
        print("🎯 WIN RATE ANALYSIS")
        print("-" * 80)

        async with self.db.session() as session:
            # Win rate by day of week
            query = text("""
                SELECT
                    TO_CHAR(order_time, 'Day') as day_name,
                    EXTRACT(DOW FROM order_time) as day_num,
                    COUNT(*) as trades,
                    COUNT(CASE WHEN pnl_usd > 0.01 THEN 1 END) as wins,
                    ROUND(AVG(pnl_usd)::numeric, 3) as avg_pnl
                FROM trade_records
                WHERE status = 'closed'
                AND order_time > NOW() - INTERVAL ':days days'
                GROUP BY day_name, day_num
                ORDER BY day_num
            """.replace(':days', str(self.days)))

            result = await session.execute(query)
            rows = result.fetchall()

            if rows:
                print("Win Rate by Day of Week:")
                print(f"{'Day':<12} {'Trades':>7} {'Wins':>5} {'Win%':>6} {'Avg PnL':>9}")
                print("-" * 45)
                for row in rows:
                    wr = (row.wins / row.trades * 100) if row.trades > 0 else 0
                    print(f"{row.day_name.strip():<12} {row.trades:>7} {row.wins:>5} {wr:>5.1f}% ${row.avg_pnl:>7}")
                print()

    async def profit_factor_analysis(self):
        """Analyze profit factor trends."""
        print("💰 PROFIT FACTOR TRENDS")
        print("-" * 80)

        async with self.db.session() as session:
            # Weekly profit factor
            query = text("""
                SELECT
                    DATE_TRUNC('week', order_time) as week,
                    COUNT(*) as trades,
                    COUNT(CASE WHEN pnl_usd > 0.01 THEN 1 END) as wins,
                    ROUND(SUM(CASE WHEN pnl_usd > 0.01 THEN pnl_usd ELSE 0 END)::numeric, 2) as gross_profit,
                    ROUND(ABS(SUM(CASE WHEN pnl_usd < -0.01 THEN pnl_usd ELSE 0 END))::numeric, 2) as gross_loss,
                    ROUND(SUM(pnl_usd)::numeric, 2) as net_pnl
                FROM trade_records
                WHERE status = 'closed'
                AND order_time > NOW() - INTERVAL ':days days'
                GROUP BY week
                ORDER BY week DESC
            """.replace(':days', str(self.days)))

            result = await session.execute(query)
            rows = result.fetchall()

            if rows:
                print("Profit Factor by Week:")
                print(f"{'Week Starting':<12} {'Trades':>7} {'Win%':>6} {'Gross':>10} {'Loss':>10} {'PF':>6} {'Net PnL':>9}")
                print("-" * 70)
                for row in rows:
                    wr = (row.wins / row.trades * 100) if row.trades > 0 else 0
                    pf = (row.gross_profit / row.gross_loss) if row.gross_loss > 0 else 0
                    week_str = row.week.strftime('%Y-%m-%d')
                    pf_indicator = "⚠️" if pf < 1.3 else "✓"
                    print(f"{week_str:<12} {row.trades:>7} {wr:>5.1f}% ${row.gross_profit:>8} ${row.gross_loss:>8} {pf:>5.2f} ${row.net_pnl:>7} {pf_indicator}")
                print()

    async def symbol_performance(self):
        """Analyze per-symbol performance."""
        print("📈 SYMBOL PERFORMANCE (Top 10 Best & Worst)")
        print("-" * 80)

        async with self.db.session() as session:
            query = text("""
                SELECT
                    symbol,
                    COUNT(*) as trades,
                    COUNT(CASE WHEN pnl_usd > 0.01 THEN 1 END) as wins,
                    ROUND(SUM(pnl_usd)::numeric, 2) as total_pnl,
                    ROUND(AVG(pnl_usd)::numeric, 3) as avg_pnl,
                    ROUND(SUM(total_fees_usd)::numeric, 2) as fees
                FROM trade_records
                WHERE status = 'closed'
                AND order_time > NOW() - INTERVAL '7 days'
                GROUP BY symbol
                HAVING COUNT(*) >= 3
                ORDER BY total_pnl DESC
            """)

            result = await session.execute(query)
            rows = result.fetchall()

            if rows:
                print(f"{'Symbol':<15} {'Trades':>7} {'Win%':>6} {'Avg PnL':>9} {'Total PnL':>10} {'Fees':>8} {'Net':>9}")
                print("-" * 75)

                # Top 10
                for row in rows[:10]:
                    wr = (row.wins / row.trades * 100) if row.trades > 0 else 0
                    net = row.total_pnl - row.fees
                    indicator = "✓" if net > 0 else "⚠️"
                    print(f"{row.symbol:<15} {row.trades:>7} {wr:>5.1f}% ${row.avg_pnl:>8} ${row.total_pnl:>9} ${row.fees:>7} ${net:>8} {indicator}")

                # Bottom 10
                if len(rows) > 10:
                    print("\n" + "-" * 75)
                    print("WORST PERFORMERS:")
                    print("-" * 75)
                    for row in rows[-10:]:
                        wr = (row.wins / row.trades * 100) if row.trades > 0 else 0
                        net = row.total_pnl - row.fees
                        print(f"{row.symbol:<15} {row.trades:>7} {wr:>5.1f}% ${row.avg_pnl:>8} ${row.total_pnl:>9} ${row.fees:>7} ${net:>8} ⚠️")
                print()

    async def timing_analysis(self):
        """Analyze trade timing and duration."""
        print("⏱️  TIMING ANALYSIS")
        print("-" * 80)

        async with self.db.session() as session:
            # This would require matching buy/sell pairs - simplified version
            query = text("""
                SELECT
                    COUNT(*) as total,
                    COUNT(CASE WHEN trigger->>'type' = 'profit' THEN 1 END) as tp_exits,
                    COUNT(CASE WHEN trigger->>'type' = 'loss' THEN 1 END) as sl_exits,
                    COUNT(CASE WHEN trigger->>'type' = 'signal' THEN 1 END) as signal_exits
                FROM trade_records
                WHERE status = 'closed'
                AND side = 'SELL'
                AND order_time > NOW() - INTERVAL '7 days'
            """)

            result = await session.execute(query)
            row = result.fetchone()

            if row and row.total:
                print("Exit Reasons (Last 7 Days):")
                print(f"  Take Profit Exits: {row.tp_exits or 0} ({(row.tp_exits or 0)/row.total*100:.1f}%)")
                print(f"  Stop Loss Exits: {row.sl_exits or 0} ({(row.sl_exits or 0)/row.total*100:.1f}%)")
                print(f"  Signal Exits: {row.signal_exits or 0} ({(row.signal_exits or 0)/row.total*100:.1f}%)")

                # Analysis
                if (row.sl_exits or 0) > (row.tp_exits or 0):
                    print("\n⚠️  WARNING: More stop-loss exits than take-profit!")
                    print("   Recommendation: Review TP/SL levels or signal quality")
                print()

    async def fee_impact_analysis(self):
        """Analyze fee impact on profitability."""
        print("💸 FEE IMPACT ANALYSIS")
        print("-" * 80)

        async with self.db.session() as session:
            query = text("""
                SELECT
                    ROUND(SUM(pnl_usd)::numeric, 2) as gross_pnl,
                    ROUND(SUM(total_fees_usd)::numeric, 2) as total_fees,
                    ROUND((SUM(pnl_usd) - SUM(total_fees_usd))::numeric, 2) as net_pnl,
                    ROUND((SUM(total_fees_usd) / NULLIF(SUM(ABS(pnl_usd)), 0) * 100)::numeric, 1) as fee_pct_of_pnl,
                    ROUND(AVG(total_fees_usd / NULLIF(ABS(pnl_usd), 0) * 100)::numeric, 1) as avg_fee_pct
                FROM trade_records
                WHERE status = 'closed'
                AND order_time > NOW() - INTERVAL ':days days'
            """.replace(':days', str(self.days)))

            result = await session.execute(query)
            row = result.fetchone()

            if row:
                print(f"Gross PnL (before fees): ${row.gross_pnl:.2f}")
                print(f"Total Fees Paid: ${row.total_fees:.2f}")
                print(f"Net PnL (after fees): ${row.net_pnl:.2f}")
                print(f"Fees as % of PnL: {row.fee_pct_of_pnl:.1f}%")

                if row.fee_pct_of_pnl and row.fee_pct_of_pnl > 50:
                    print("\n⚠️  CRITICAL: Fees are consuming >50% of profits!")
                    print("   Recommendation: Increase position size or reduce trade frequency")
                elif row.fee_pct_of_pnl and row.fee_pct_of_pnl > 30:
                    print("\n⚠️  WARNING: High fee impact (>30%)")
                    print("   Recommendation: Consider larger positions or better signals")
                print()

    async def tp_sl_effectiveness(self):
        """Analyze TP/SL effectiveness."""
        print("🎯 TP/SL EFFECTIVENESS")
        print("-" * 80)

        # Check if JSONL file exists
        tpsl_path = Path("/app/logs/tpsl.jsonl")
        if not tpsl_path.exists():
            tpsl_path = Path("logs/tpsl.jsonl")

        if tpsl_path.exists():
            print(f"Analyzing {tpsl_path}...")
            rr_ratios = []
            tp_pcts = []
            sl_pcts = []

            with open(tpsl_path, 'r') as f:
                for line in f:
                    try:
                        data = json.loads(line.strip())
                        if 'rr' in data:
                            rr_ratios.append(float(data['rr']))
                        if 'tp_pct' in data:
                            tp_pcts.append(float(data['tp_pct']))
                        if 'stop_pct' in data:
                            sl_pcts.append(abs(float(data['stop_pct'])))
                    except:
                        continue

            if rr_ratios:
                avg_rr = sum(rr_ratios) / len(rr_ratios)
                avg_tp = sum(tp_pcts) / len(tp_pcts) if tp_pcts else 0
                avg_sl = sum(sl_pcts) / len(sl_pcts) if sl_pcts else 0

                print(f"Average Risk:Reward Ratio: 1:{avg_rr:.2f}", end="")
                if avg_rr < 1.0:
                    print(" ⚠️  (< 1.0 = poor)")
                elif avg_rr < 1.5:
                    print(" ⚠️  (marginal)")
                else:
                    print(" ✓")

                print(f"Average TP: {avg_tp:.2f}%")
                print(f"Average SL: {avg_sl:.2f}%")

                if avg_rr < 1.5:
                    print("\n⚠️  Recommendation: Increase TP target or tighten SL")
            else:
                print("No TP/SL data found in logs")
        else:
            print("TP/SL log file not found")

        print()

    async def leaderboard_status(self):
        """Check leaderboard and eligibility."""
        print("🏆 LEADERBOARD STATUS")
        print("-" * 80)

        async with self.db.session() as session:
            query = text("""
                SELECT
                    COUNT(*) as total_symbols,
                    COUNT(CASE WHEN eligible THEN 1 END) as eligible_symbols,
                    ROUND(AVG(CASE WHEN eligible THEN win_rate END)::numeric, 3) as avg_eligible_wr,
                    ROUND(AVG(CASE WHEN eligible THEN profit_factor END)::numeric, 2) as avg_eligible_pf,
                    ROUND(AVG(CASE WHEN NOT eligible THEN win_rate END)::numeric, 3) as avg_ineligible_wr
                FROM active_symbols
            """)

            result = await session.execute(query)
            row = result.fetchone()

            if row and row.total_symbols:
                print(f"Total Symbols Tracked: {row.total_symbols}")
                print(f"Eligible Symbols: {row.eligible_symbols} ({row.eligible_symbols/row.total_symbols*100:.1f}%)")
                print(f"Average Win Rate (Eligible): {(row.avg_eligible_wr or 0)*100:.1f}%")
                print(f"Average Profit Factor (Eligible): {row.avg_eligible_pf or 0:.2f}")
                print(f"Average Win Rate (Ineligible): {(row.avg_ineligible_wr or 0)*100:.1f}%")

                if row.eligible_symbols == 0:
                    print("\n⚠️  CRITICAL: No eligible symbols!")
                    print("   Bot may not be trading. Check leaderboard criteria.")
                elif row.eligible_symbols < 5:
                    print("\n⚠️  WARNING: Very few eligible symbols")
                    print("   Consider relaxing eligibility criteria")
            else:
                print("⚠️  No leaderboard data available")
                print("   Run: python -m SharedDataManager.leaderboard_runner")

            print()


async def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description='BotTrader Performance Diagnostic')
    parser.add_argument('--days', type=int, default=30, help='Days to analyze (default: 30)')
    parser.add_argument('--detailed', action='store_true', help='Show detailed analysis')
    args = parser.parse_args()

    diagnostic = PerformanceDiagnostic(days=args.days, detailed=args.detailed)

    try:
        await diagnostic.initialize()
        await diagnostic.analyze_all()
    except Exception as e:
        print(f"\n❌ Error during analysis: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await diagnostic.close()


if __name__ == "__main__":
    asyncio.run(main())
