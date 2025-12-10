#!/usr/bin/env python3
"""
Verify email report accuracy by comparing reported values against actual database queries.
Run this after receiving a daily report email to validate the PnL calculations.

Usage:
    python3 verify_report_accuracy.py

This script will:
1. Query the database for actual FIFO allocation PnL in the last 24 hours
2. Compare win rate calculations
3. Display any discrepancies
"""

import os
import sys
from datetime import datetime, timezone, timedelta
from sqlalchemy import create_engine, text

# Database connection from environment
DB_USER = os.getenv("DB_USER", "bot_user")
DB_PASSWORD = os.getenv("DB_PASSWORD", "7317botTrade4ssm")
DB_NAME = os.getenv("DB_NAME", "bot_trader_db")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")

DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

def verify_report(hours_back=24):
    """
    Verify report accuracy by comparing database values.
    """
    print(f"\n{'='*80}")
    print(f"Report Verification - Last {hours_back} Hours")
    print(f"Timestamp: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"{'='*80}\n")

    try:
        engine = create_engine(DATABASE_URL)

        with engine.connect() as conn:
            # 1. Direct FIFO PnL query (what report should show)
            print("1. FIFO Allocation PnL (Ground Truth)")
            print("-" * 80)

            fifo_query = text("""
                SELECT
                    COALESCE(SUM(pnl_usd), 0) as total_pnl,
                    COUNT(*) as total_allocations,
                    COUNT(CASE WHEN pnl_usd > 0 THEN 1 END) as winners,
                    COUNT(CASE WHEN pnl_usd < 0 THEN 1 END) as losers,
                    COUNT(CASE WHEN pnl_usd = 0 THEN 1 END) as breakeven
                FROM fifo_allocations
                WHERE allocation_version = 2
                  AND sell_time >= (NOW() AT TIME ZONE 'UTC' - INTERVAL :hours_back)
                  AND sell_time < (NOW() AT TIME ZONE 'UTC')
            """)

            result = conn.execute(fifo_query, {"hours_back": f"{hours_back} hours"}).fetchone()
            total_pnl = float(result[0] or 0)
            total_allocations = int(result[1] or 0)
            winners = int(result[2] or 0)
            losers = int(result[3] or 0)
            breakeven = int(result[4] or 0)

            fifo_win_rate = (winners / total_allocations * 100) if total_allocations > 0 else 0

            print(f"Total PnL:        ${total_pnl:,.4f}")
            print(f"Allocations:      {total_allocations} (W:{winners} L:{losers} BE:{breakeven})")
            print(f"Win Rate:         {fifo_win_rate:.1f}%")

            # 2. Win rate from trade_records (what report calculates)
            print(f"\n2. Win Rate Calculation (Trade Records + FIFO)")
            print("-" * 80)

            winrate_query = text("""
                WITH trade_pnl AS (
                    SELECT
                        tr.order_id,
                        COALESCE(SUM(fa.pnl_usd), 0) AS pnl
                    FROM trade_records tr
                    LEFT JOIN fifo_allocations fa
                        ON fa.sell_order_id = tr.order_id
                        AND fa.allocation_version = 2
                    WHERE tr.order_time >= (NOW() AT TIME ZONE 'UTC' - INTERVAL :hours_back)
                      AND tr.order_time < (NOW() AT TIME ZONE 'UTC')
                      AND tr.side = 'sell'
                      AND tr.status IN ('filled', 'done')
                    GROUP BY tr.order_id
                )
                SELECT
                    COUNT(*) as total_trades,
                    COUNT(*) FILTER (WHERE pnl > 0) AS wins,
                    COUNT(*) FILTER (WHERE pnl < 0) AS losses,
                    COUNT(*) FILTER (WHERE pnl = 0) AS breakeven,
                    SUM(pnl) as total_pnl
                FROM trade_pnl
            """)

            result = conn.execute(winrate_query, {"hours_back": f"{hours_back} hours"}).fetchone()
            total_trades = int(result[0] or 0)
            wins = int(result[1] or 0)
            losses = int(result[2] or 0)
            be = int(result[3] or 0)
            trade_pnl = float(result[4] or 0)

            trade_win_rate = (wins / total_trades * 100) if total_trades > 0 else 0

            print(f"Total Sell Orders: {total_trades} (W:{wins} L:{losses} BE:{be})")
            print(f"Win Rate:          {trade_win_rate:.1f}% ({wins}/{total_trades})")
            print(f"Total PnL:         ${trade_pnl:,.4f}")

            # 3. Comparison
            print(f"\n3. Comparison & Validation")
            print("-" * 80)

            pnl_diff = abs(total_pnl - trade_pnl)
            if pnl_diff < 0.01:
                print(f"✅ PnL Match: Both methods agree (${total_pnl:,.4f})")
            else:
                print(f"❌ PnL Mismatch: Difference of ${pnl_diff:,.4f}")
                print(f"   FIFO Direct:     ${total_pnl:,.4f}")
                print(f"   Trade Records:   ${trade_pnl:,.4f}")

            print(f"\nExpected Report Values:")
            print(f"  Realized PnL:    ${total_pnl:,.2f}")
            print(f"  Win Rate:        {trade_win_rate:.1f}% ({wins}/{total_trades})")

            # 4. Symbol breakdown (top performers)
            print(f"\n4. Top Symbol Performance")
            print("-" * 80)

            symbol_query = text("""
                SELECT
                    symbol,
                    COUNT(*) as trades,
                    ROUND(SUM(pnl_usd)::numeric, 4) as total_pnl,
                    ROUND(AVG(pnl_usd)::numeric, 4) as avg_pnl,
                    COUNT(CASE WHEN pnl_usd > 0 THEN 1 END) as wins
                FROM fifo_allocations
                WHERE allocation_version = 2
                  AND sell_time >= (NOW() AT TIME ZONE 'UTC' - INTERVAL :hours_back)
                  AND sell_time < (NOW() AT TIME ZONE 'UTC')
                GROUP BY symbol
                ORDER BY total_pnl DESC
                LIMIT 5
            """)

            results = conn.execute(symbol_query, {"hours_back": f"{hours_back} hours"}).fetchall()

            print(f"{'Symbol':<15} {'Trades':<8} {'Total PnL':<12} {'Avg PnL':<12} {'Wins':<6}")
            print("-" * 80)
            for row in results:
                symbol, trades, total_pnl_sym, avg_pnl, wins_sym = row
                print(f"{symbol:<15} {trades:<8} ${float(total_pnl_sym):>10.4f} ${float(avg_pnl):>10.4f} {wins_sym:<6}")

        print(f"\n{'='*80}")
        print("✅ Verification Complete")
        print(f"{'='*80}\n")

    except Exception as e:
        print(f"\n❌ Error during verification: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    verify_report()
