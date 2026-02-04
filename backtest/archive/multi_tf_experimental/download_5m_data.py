#!/usr/bin/env python3
"""
Download 5-minute historical OHLCV data from Coinbase.

This script downloads 5m candles for faster backtesting during optimization.
For Phase 2.3, we use 5m data for parameter optimization and 1m data for final validation.
"""

import time
import argparse
import requests
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path

# Coinbase API endpoint
COINBASE_API_URL = "https://api.exchange.coinbase.com"

# Data directory
DATA_DIR = Path("data")


def download_5m_candles(
    product_id: str,
    start_time: datetime,
    end_time: datetime
) -> pd.DataFrame:
    """
    Download 5-minute candles from Coinbase.

    Args:
        product_id: Trading pair (e.g., 'BTC-USD')
        start_time: Start datetime
        end_time: End datetime

    Returns:
        DataFrame with OHLCV data at 5m resolution
    """
    url = f"{COINBASE_API_URL}/products/{product_id}/candles"

    # Coinbase API returns max 300 candles per request
    # 5m granularity = 300 seconds
    # For 60 days at 5m: 60 * 288 = 17,280 candles → need ~58 requests
    # For 365 days at 5m: 365 * 288 = 105,120 candles → need ~351 requests

    all_candles = []
    current_start = start_time
    request_count = 0

    total_minutes = int((end_time - start_time).total_seconds() / 60)
    expected_candles = total_minutes // 5
    print(f"  Expected ~{expected_candles:,} 5m candles")

    while current_start < end_time:
        # Request 300 candles at a time (= 1500 minutes = 25 hours)
        current_end = min(current_start + timedelta(minutes=1500), end_time)

        params = {
            'start': current_start.isoformat(),
            'end': current_end.isoformat(),
            'granularity': 300  # 5 minutes in seconds
        }

        try:
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()

            candles = response.json()

            if candles:
                all_candles.extend(candles)
                request_count += 1

                if request_count % 10 == 0:
                    print(f"  Progress: {len(all_candles):,} candles downloaded ({request_count} requests)")

            # Move to next batch
            current_start = current_end

            # Rate limiting: Coinbase allows ~3 requests/second
            time.sleep(0.4)

        except requests.exceptions.RequestException as e:
            print(f"  ⚠️  API error: {e}")
            print(f"  Retrying in 5 seconds...")
            time.sleep(5)
            continue

    if not all_candles:
        print(f"  ❌ No data returned for {product_id}")
        return pd.DataFrame()

    # Convert to DataFrame
    # Coinbase format: [time, low, high, open, close, volume]
    df = pd.DataFrame(all_candles, columns=['time', 'low', 'high', 'open', 'close', 'volume'])

    # Convert timestamp to datetime
    df['time'] = pd.to_datetime(df['time'], unit='s')

    # Sort by time (Coinbase returns newest first)
    df = df.sort_values('time')

    # Reset index
    df = df.reset_index(drop=True)

    print(f"  ✅ Downloaded {len(df):,} 5m candles for {product_id}")

    return df


def download_symbol_5m(symbol: str, days: int) -> bool:
    """
    Download 5m data for a single symbol and save to CSV.

    Args:
        symbol: Trading pair (e.g., 'BTC-USD')
        days: Number of days of history to download

    Returns:
        True if successful, False otherwise
    """
    print(f"\n📥 Downloading {symbol} (5m, last {days} days)...")

    # Calculate time range
    end_time = datetime.utcnow()
    start_time = end_time - timedelta(days=days)

    print(f"  Period: {start_time.strftime('%Y-%m-%d')} to {end_time.strftime('%Y-%m-%d')}")

    # Download candles
    df = download_5m_candles(symbol, start_time, end_time)

    if df.empty:
        return False

    # Ensure data directory exists
    DATA_DIR.mkdir(exist_ok=True)

    # Save to CSV with resolution in filename
    symbol_underscore = symbol.replace('-', '_')
    output_file = DATA_DIR / f"{symbol_underscore}_5m.csv"

    df.to_csv(output_file, index=False)
    print(f"  💾 Saved to: {output_file}")
    print(f"  📊 Size: {output_file.stat().st_size / 1024 / 1024:.2f} MB")

    return True


def main():
    parser = argparse.ArgumentParser(
        description='Download 5-minute historical data from Coinbase'
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
        default='BTC-USD',
        help='Symbol to download (default: BTC-USD)'
    )

    args = parser.parse_args()

    print("=" * 80)
    print("5-MINUTE DATA DOWNLOAD")
    print("=" * 80)
    print()
    print(f"Symbol: {args.symbol}")
    print(f"Days: {args.days}")
    print(f"Resolution: 5 minutes")
    print(f"Output directory: {DATA_DIR}")
    print()
    print("=" * 80)

    success = download_symbol_5m(args.symbol, args.days)

    print()
    print("=" * 80)

    if success:
        print("✅ DOWNLOAD COMPLETE")
    else:
        print("❌ DOWNLOAD FAILED")

    print("=" * 80)
    print()

    return 0 if success else 1


if __name__ == "__main__":
    exit(main())
