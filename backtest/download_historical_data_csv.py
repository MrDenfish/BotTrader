#!/usr/bin/env python3
"""
Download historical OHLCV data from Coinbase and save to CSV files.

This version saves data as CSV files (one per symbol) instead of database
to avoid SQL bulk insert limits and enable parallel processing.
"""

import os
import sys
import time
import argparse
import requests
import pandas as pd
import sqlalchemy as sa
from datetime import datetime, timedelta
from pathlib import Path
from typing import List

# Coinbase API endpoint
COINBASE_API_URL = "https://api.exchange.coinbase.com"

# Database URL for getting active symbols
DB_URL = "postgresql://localhost:5432/trading_development"

# Data directory
DATA_DIR = Path("backtest/data")


def get_active_symbols() -> List[str]:
    """Get list of active symbols."""
    # Default list of active symbols (avoids database dependency).
    # These match the symbols available on Kraken and in backtest/data/.
    # Add new tickers here to include them in future backfills.
    DEFAULT_SYMBOLS = [
        'BTC-USD', 'ETH-USD', 'SOL-USD', 'XRP-USD', 'ADA-USD',
        'DOGE-USD', 'DOT-USD', 'AVAX-USD', 'LINK-USD',
        # Uncomment to add more symbols for backtesting:
        # 'LTC-USD', 'SUI-USD', 'NEAR-USD', 'ATOM-USD',
    ]

    try:
        print("Fetching active symbols from database...")
        engine = sa.create_engine(DB_URL)

        query = sa.text("""
            SELECT DISTINCT symbol
            FROM symbols
            WHERE active = true
            ORDER BY symbol
        """)

        with engine.connect() as conn:
            result = conn.execute(query)
            symbols = [row[0] for row in result]

        print(f"✅ Found {len(symbols)} active symbols from database")
        return symbols

    except Exception as e:
        print(f"⚠️  Could not connect to database: {e}")
        print(f"Using default symbol list: {len(DEFAULT_SYMBOLS)} symbols")
        return DEFAULT_SYMBOLS


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
                print(f"  {product_id}: Downloaded {len(all_candles):,} candles", end='\r')

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

    # Reorder columns to match expected format
    df = df[['time', 'open', 'high', 'low', 'close', 'volume']]

    print(f"\n  ✅ {product_id}: {len(df):,} candles")

    return df


def save_to_csv(df: pd.DataFrame, symbol: str, data_dir: Path):
    """
    Save DataFrame to CSV file, merging with existing data if present.

    New data overwrites existing rows for the same timestamp (dedup).
    This allows incremental backfills without losing older data.
    """
    if df.empty:
        return

    # Create data directory if it doesn't exist
    data_dir.mkdir(parents=True, exist_ok=True)

    filename = data_dir / f"{symbol.replace('-', '_')}.csv"

    # Merge with existing CSV if present
    if filename.exists():
        existing = pd.read_csv(filename)
        time_col = "time" if "time" in existing.columns else "timestamp"
        existing[time_col] = pd.to_datetime(existing[time_col]).dt.strftime("%Y-%m-%d %H:%M:%S")
        df["time"] = pd.to_datetime(df["time"]).dt.strftime("%Y-%m-%d %H:%M:%S")
        # Concat and dedup: new data takes priority
        merged = pd.concat([existing, df]).drop_duplicates(subset=[time_col], keep="last")
        merged = merged.sort_values(time_col).reset_index(drop=True)
        merged.to_csv(filename, index=False)
        print(f"  Merged: {len(merged):,} rows ({len(existing):,} existing + {len(df):,} new, {len(merged) - len(existing):,} net new)")
    else:
        df.to_csv(filename, index=False)
        print(f"  Saved {len(df):,} rows to {filename}")


def download_all_symbols(days: int = 60, symbols: List[str] = None):
    """
    Download historical data for all symbols and save to CSV.

    Args:
        days: Number of days of history to download
        symbols: List of symbols (if None, fetch from database)
    """
    if symbols is None:
        symbols = get_active_symbols()

    end_time = datetime.now()
    start_time = end_time - timedelta(days=days)

    print(f"\n{'='*80}")
    print(f"DOWNLOADING HISTORICAL DATA")
    print(f"{'='*80}")
    print(f"Period: {start_time.date()} to {end_time.date()} ({days} days)")
    print(f"Symbols: {len(symbols)}")
    print(f"Output: {DATA_DIR}/")
    print(f"{'='*80}\n")

    for i, symbol in enumerate(symbols, 1):
        print(f"[{i}/{len(symbols)}] {symbol}")

        try:
            # Download data
            df = download_candles(symbol, start_time, end_time)

            # Save to CSV
            if not df.empty:
                save_to_csv(df, symbol, DATA_DIR)
            else:
                print(f"  ⚠️  No data for {symbol}")

        except Exception as e:
            print(f"  ❌ Error: {e}")
            continue

        print()  # Blank line between symbols

    print(f"\n{'='*80}")
    print(f"✅ DOWNLOAD COMPLETE")
    print(f"{'='*80}")
    print(f"Data saved to: {DATA_DIR.absolute()}/")
    print(f"Files: {len(list(DATA_DIR.glob('*.csv')))}")
    print(f"{'='*80}\n")


def main():
    parser = argparse.ArgumentParser(
        description="Download historical OHLCV data from Coinbase to CSV files"
    )
    parser.add_argument(
        '--days',
        type=int,
        default=60,
        help='Number of days of history to download (default: 60)'
    )
    parser.add_argument(
        '--symbol',
        type=str,
        help='Single symbol to download (e.g., BTC-USD). If not specified, downloads all active symbols.'
    )
    parser.add_argument(
        '--test',
        action='store_true',
        help='Test mode: download just BTC-USD for 7 days'
    )

    args = parser.parse_args()

    if args.test:
        print("🧪 TEST MODE: Downloading BTC-USD for 7 days\n")
        download_all_symbols(days=7, symbols=['BTC-USD'])
    elif args.symbol:
        download_all_symbols(days=args.days, symbols=[args.symbol])
    else:
        download_all_symbols(days=args.days)


if __name__ == "__main__":
    main()
