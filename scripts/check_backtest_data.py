#!/usr/bin/env python3
"""
Check available data for backtesting

Shows date ranges and data availability for backtesting the trading strategy.
"""

import sys
import os
sys.path.insert(0, '/Users/Manny/Python_Projects/BotTrader')

from sqlalchemy import create_engine, text
from dotenv import load_dotenv

def main():
    # Load environment and get database connection
    load_dotenv('/Users/Manny/Python_Projects/BotTrader/.env')

    # Build database URL from env vars (using SSH tunnel on port 5433)
    db_user = 'bot_user'
    db_pass = os.environ['DB_PASSWORD']
    db_host = 'localhost'  # SSH tunnel
    db_port = '5433'  # SSH tunnel port
    db_name = 'bot_trader_db'

    db_url = f"postgresql://{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}"

    print(f"Connecting to: {db_host}:{db_port}/{db_name} (via SSH tunnel)")
    print()

    print("=" * 70)
    print("  BotTrader Backtest Data Analysis")
    print("=" * 70)
    print()

    engine = create_engine(db_url)

    with engine.connect() as conn:
        # Check OHLCV data
        print("📊 OHLCV Market Data:")
        print("-" * 70)
        result = conn.execute(text("""
            SELECT
                MIN(time) as earliest_data,
                MAX(time) as latest_data,
                COUNT(*) as total_rows,
                COUNT(DISTINCT symbol) as unique_symbols
            FROM ohlcv_data
        """)).fetchone()

        print(f"  Date Range: {result[0]} to {result[1]}")
        print(f"  Total Rows: {result[2]:,}")
        print(f"  Unique Symbols: {result[3]}")
        print()

        # Check data by symbol
        print("📈 Data by Symbol:")
        print("-" * 70)
        symbols = conn.execute(text("""
            SELECT
                symbol,
                MIN(time) as earliest,
                MAX(time) as latest,
                COUNT(*) as rows
            FROM ohlcv_data
            GROUP BY symbol
            ORDER BY rows DESC
            LIMIT 10
        """)).fetchall()

        for sym in symbols:
            print(f"  {sym[0]:12} {sym[1]} to {sym[2]} ({sym[3]:,} rows)")
        print()

        # Check trade records
        print("💰 Trade Records:")
        print("-" * 70)
        trades = conn.execute(text("""
            SELECT
                MIN(order_time) as first_trade,
                MAX(order_time) as last_trade,
                COUNT(*) as total_trades,
                COUNT(CASE WHEN side = 'buy' THEN 1 END) as buys,
                COUNT(CASE WHEN side = 'sell' THEN 1 END) as sells
            FROM trade_records
            WHERE status = 'filled'
        """)).fetchone()

        print(f"  Date Range: {trades[0]} to {trades[1]}")
        print(f"  Total Trades: {trades[2]:,}")
        print(f"  Buys: {trades[3]:,}")
        print(f"  Sells: {trades[4]:,}")
        print()

        # Check data completeness by month
        print("📅 Data Completeness (OHLCV):")
        print("-" * 70)
        monthly = conn.execute(text("""
            SELECT
                DATE_TRUNC('month', time) as month,
                COUNT(*) as rows,
                COUNT(DISTINCT symbol) as symbols,
                COUNT(DISTINCT DATE_TRUNC('day', time)) as days
            FROM ohlcv_data
            GROUP BY DATE_TRUNC('month', time)
            ORDER BY month DESC
            LIMIT 6
        """)).fetchall()

        for month in monthly:
            print(f"  {month[0].strftime('%Y-%m'):10} {month[1]:6,} rows, {month[2]:3} symbols, {month[3]:2} days")
        print()

        # Backtest recommendations
        print("🎯 Backtest Recommendations:")
        print("-" * 70)
        print(f"  ✅ Full Historical: {result[0].date()} to {result[1].date()}")
        print(f"  ✅ Recent 30 days:  {(result[1] - __import__('datetime').timedelta(days=30)).date()} to {result[1].date()}")
        print(f"  ✅ Recent 60 days:  {(result[1] - __import__('datetime').timedelta(days=60)).date()} to {result[1].date()}")
        print()
        print("  Recommended: Start with 30-day backtest for quick feedback")
        print("              Then run full historical for comprehensive analysis")
        print()

if __name__ == "__main__":
    main()
