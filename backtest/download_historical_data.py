#!/usr/bin/env python3
"""
Historical Data Downloader for Coinbase
========================================

Downloads 60 days of 1-minute OHLCV data from Coinbase Advanced Trade API
and stores it in the database for backtesting.

Usage:
    python3 backtest/download_historical_data.py --days 60

Date: January 28, 2026
"""

import os
import sys
sys.path.insert(0, '/Users/Manny/Python_Projects/BotTrader')

import argparse
import time
import requests
import pandas as pd
from datetime import datetime, timedelta
from typing import List, Dict
import sqlalchemy as sa
from sqlalchemy import text


# Coinbase API (public, no auth needed for historical data)
COINBASE_API_URL = "https://api.exchange.coinbase.com"

# Database connection (via SSH tunnel). Requires DB_PASSWORD in env.
DB_URL = f"postgresql://bot_user:{os.environ['DB_PASSWORD']}@localhost:5433/bot_trader_db"


def get_symbols_from_database() -> List[str]:
    """Get list of active symbols from database."""
    engine = sa.create_engine(DB_URL)

    query = text("""
        SELECT DISTINCT symbol
        FROM ohlcv_data
        WHERE time >= NOW() - INTERVAL '2 days'
        ORDER BY symbol
    """)

    with engine.connect() as conn:
        result = conn.execute(query)
        symbols = [row[0] for row in result]

    engine.dispose()

    return symbols


def get_product_id(symbol: str) -> str:
    """Convert symbol format (e.g., BTC-USD) to Coinbase product ID."""
    # Already in correct format for Coinbase
    return symbol


def download_candles(
    product_id: str,
    start_time: datetime,
    end_time: datetime,
    granularity: int = 60
) -> pd.DataFrame:
    """
    Download historical candles from Coinbase.

    Args:
        product_id: Trading pair (e.g., 'BTC-USD')
        start_time: Start datetime
        end_time: End datetime
        granularity: Candle size in seconds (60 = 1 minute)

    Returns:
        DataFrame with OHLCV data
    """
    url = f"{COINBASE_API_URL}/products/{product_id}/candles"

    # Coinbase API returns max 300 candles per request
    # For 60 days at 1m = 86,400 candles, need ~288 requests

    all_candles = []
    current_start = start_time

    while current_start < end_time:
        # Request 300 candles at a time
        current_end = min(current_start + timedelta(minutes=300), end_time)

        params = {
            'start': current_start.isoformat(),
            'end': current_end.isoformat(),
            'granularity': granularity
        }

        try:
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()

            candles = response.json()

            if candles:
                all_candles.extend(candles)
                print(f"  {product_id}: Downloaded {len(candles)} candles "
                      f"({current_start.date()} to {current_end.date()})", end='\r')

            # Move to next batch
            current_start = current_end

            # Rate limiting (Coinbase: 10 requests/second public)
            time.sleep(0.11)

        except requests.exceptions.RequestException as e:
            print(f"\n  ⚠️  Error downloading {product_id}: {e}")
            # Continue with next batch
            current_start = current_end
            time.sleep(1)

    if not all_candles:
        return pd.DataFrame()

    # Convert to DataFrame
    # Coinbase format: [time, low, high, open, close, volume]
    df = pd.DataFrame(all_candles, columns=['time', 'low', 'high', 'open', 'close', 'volume'])

    # Convert timestamp to datetime
    df['time'] = pd.to_datetime(df['time'], unit='s')

    # Sort by time
    df = df.sort_values('time').reset_index(drop=True)

    # Add symbol column
    df['symbol'] = product_id

    print(f"\n  ✅ {product_id}: {len(df)} candles")

    return df


def store_candles_to_database(df: pd.DataFrame, replace: bool = False):
    """
    Store candles in database.

    Args:
        df: DataFrame with OHLCV data
        replace: If True, delete existing data for this symbol first
    """
    if df.empty:
        return

    engine = sa.create_engine(DB_URL)

    symbol = df['symbol'].iloc[0]

    with engine.connect() as conn:
        if replace:
            # Delete existing data for this symbol in date range
            min_time = df['time'].min()
            max_time = df['time'].max()

            delete_query = text("""
                DELETE FROM ohlcv_data
                WHERE symbol = :symbol
                  AND time >= :min_time
                  AND time <= :max_time
            """)

            conn.execute(delete_query, {
                'symbol': symbol,
                'min_time': min_time,
                'max_time': max_time
            })
            conn.commit()

        # Insert new data
        # Use to_sql with if_exists='append'
        df[['time', 'symbol', 'open', 'high', 'low', 'close', 'volume']].to_sql(
            'ohlcv_data',
            conn,
            if_exists='append',
            index=False,
            method='multi'
        )
        conn.commit()

    engine.dispose()


def download_all_symbols(days: int = 60, symbols: List[str] = None):
    """
    Download historical data for all symbols.

    Args:
        days: Number of days to download
        symbols: List of symbols (if None, fetches from database)
    """
    print("=" * 80)
    print("HISTORICAL DATA DOWNLOADER")
    print("=" * 80)
    print(f"Period: {days} days")
    print()

    # Get symbols
    if symbols is None:
        print("Fetching active symbols from database...")
        symbols = get_symbols_from_database()
        print(f"✅ Found {len(symbols)} active symbols\n")

    # Date range
    end_time = datetime.now()
    start_time = end_time - timedelta(days=days)

    print(f"Date range: {start_time.date()} to {end_time.date()}")
    print()

    # Download each symbol
    success_count = 0
    error_count = 0

    for i, symbol in enumerate(symbols, 1):
        print(f"[{i}/{len(symbols)}] {symbol}")

        try:
            # Download
            df = download_candles(symbol, start_time, end_time)

            if not df.empty:
                # Store
                store_candles_to_database(df, replace=True)
                success_count += 1
            else:
                print(f"  ⚠️  No data available for {symbol}")
                error_count += 1

        except Exception as e:
            print(f"  ❌ Error: {e}")
            error_count += 1

        print()

    print("=" * 80)
    print(f"✅ Download complete!")
    print(f"   Success: {success_count} symbols")
    print(f"   Errors: {error_count} symbols")
    print("=" * 80)


def main():
    parser = argparse.ArgumentParser(description="Download historical OHLCV data from Coinbase")

    parser.add_argument(
        '--days',
        type=int,
        default=60,
        help='Number of days to download (default: 60)'
    )

    parser.add_argument(
        '--symbols',
        type=str,
        nargs='+',
        help='Specific symbols to download (e.g., BTC-USD ETH-USD)'
    )

    parser.add_argument(
        '--test',
        action='store_true',
        help='Test mode: download only BTC-USD for 7 days'
    )

    args = parser.parse_args()

    if args.test:
        print("TEST MODE: Downloading BTC-USD for 7 days only\n")
        download_all_symbols(days=7, symbols=['BTC-USD'])
    else:
        download_all_symbols(days=args.days, symbols=args.symbols)


if __name__ == '__main__':
    main()
