#!/usr/bin/env python3
"""Fetch recent 1-minute OHLCV from Kraken and update backtest CSV files.

Uses Kraken's public OHLC endpoint (no auth needed). Fetches the most
recent N days of data for each symbol and merges with existing CSVs,
deduplicating by timestamp.

Usage:
    python scripts/backfill_kraken_ohlcv.py                # all symbols, 30 days
    python scripts/backfill_kraken_ohlcv.py --days 60       # all symbols, 60 days
    python scripts/backfill_kraken_ohlcv.py --symbols BTC   # BTC only
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

# Kraken symbol mappings: our CSV name → Kraken pair name.
# Add new tickers here to include them in future backfills.
SYMBOL_MAP = {
    "BTC_USD": "XBTUSD",
    "ETH_USD": "ETHUSD",
    "SOL_USD": "SOLUSD",
    "XRP_USD": "XRPUSD",
    "DOGE_USD": "XDGUSD",
    "ADA_USD": "ADAUSD",
    "LINK_USD": "LINKUSD",
    "DOT_USD": "DOTUSD",
    "AVAX_USD": "AVAXUSD",
    "LTC_USD": "LTCUSD",
    "SUI_USD": "SUIUSD",
    "NEAR_USD": "NEARUSD",
    "ATOM_USD": "ATOMUSD",
}

DATA_DIR = Path(__file__).parent.parent / "backtest" / "data"
KRAKEN_OHLC_URL = "https://api.kraken.com/0/public/OHLC"
MAX_RESULTS = 720  # Kraken returns max 720 candles per request


def fetch_ohlcv(pair: str, since: int, interval: int = 1) -> list[dict]:
    """Fetch OHLCV data from Kraken public API.

    Args:
        pair: Kraken pair name (e.g., "XBTUSD")
        since: Unix timestamp to start from
        interval: Candle interval in minutes (default: 1)

    Returns:
        List of dicts with keys: time, open, high, low, close, volume
    """
    params = {
        "pair": pair,
        "interval": interval,
        "since": since,
    }
    resp = requests.get(KRAKEN_OHLC_URL, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    if data.get("error"):
        raise RuntimeError(f"Kraken API error: {data['error']}")

    # Response format: {"result": {"XBTUSD": [[time, open, high, low, close, vwap, volume, count], ...], "last": ...}}
    result = data["result"]
    # Find the candle data (key is the pair name, but could vary)
    candle_key = [k for k in result if k != "last"][0]
    raw = result[candle_key]

    candles = []
    for row in raw:
        ts = int(row[0])
        candles.append({
            "time": datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[6]),  # index 6 is volume, 5 is vwap
        })

    return candles


def fetch_all_since(pair: str, since_ts: int) -> list[dict]:
    """Fetch all 1-minute candles from since_ts to now, paginating as needed."""
    all_candles = []
    current_since = since_ts

    while True:
        candles = fetch_ohlcv(pair, current_since)
        if not candles:
            break

        all_candles.extend(candles)
        print(f"    Fetched {len(candles)} candles (total: {len(all_candles)})", end="\r")

        # If we got fewer than max, we're done
        if len(candles) < MAX_RESULTS:
            break

        # Next page: start from the last candle's timestamp
        last_time = datetime.strptime(candles[-1]["time"], "%Y-%m-%d %H:%M:%S")
        last_time = last_time.replace(tzinfo=timezone.utc)
        current_since = int(last_time.timestamp())

        # Rate limit: ~1 req/sec
        time.sleep(1.5)

    print()  # Clear \r
    return all_candles


def load_existing_csv(path: Path) -> dict[str, dict]:
    """Load existing CSV into dict keyed by timestamp string."""
    rows = {}
    if not path.exists():
        return rows
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows[row["time"]] = row
    return rows


def save_csv(path: Path, rows: dict[str, dict]) -> None:
    """Save merged data back to CSV, sorted by timestamp."""
    sorted_rows = sorted(rows.values(), key=lambda r: r["time"])
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["time", "open", "high", "low", "close", "volume"])
        writer.writeheader()
        writer.writerows(sorted_rows)


def backfill_symbol(symbol: str, pair: str, days: int) -> None:
    """Fetch and merge OHLCV data for one symbol."""
    csv_path = DATA_DIR / f"{symbol}.csv"
    print(f"\n{symbol} ({pair}):")

    # Determine start time
    since = datetime.now(timezone.utc) - timedelta(days=days)
    since_ts = int(since.timestamp())

    # Check existing data
    existing = load_existing_csv(csv_path)
    if existing:
        dates = sorted(existing.keys())
        print(f"  Existing: {len(existing)} rows ({dates[0]} to {dates[-1]})")
    else:
        print(f"  No existing data at {csv_path}")

    # Fetch from Kraken
    print(f"  Fetching last {days} days from Kraken...")
    new_candles = fetch_all_since(pair, since_ts)
    print(f"  Fetched {len(new_candles)} candles from API")

    if not new_candles:
        print("  No data returned — skipping")
        return

    # Merge: new data overwrites existing for same timestamp
    for candle in new_candles:
        existing[candle["time"]] = candle

    # Save
    save_csv(csv_path, existing)
    dates = sorted(existing.keys())
    print(f"  Saved: {len(existing)} rows ({dates[0]} to {dates[-1]})")


def main():
    parser = argparse.ArgumentParser(
        description="Backfill Kraken OHLCV data for backtesting",
    )
    parser.add_argument(
        "--days", "-d",
        type=int,
        default=30,
        help="Number of days to fetch (default: 30)",
    )
    parser.add_argument(
        "--symbols", "-s",
        nargs="+",
        default=None,
        help="Symbols to fetch (e.g., BTC ETH SOL). Default: all",
    )
    args = parser.parse_args()

    # Filter symbols
    if args.symbols:
        symbols = {
            s: SYMBOL_MAP[f"{s}_USD"]
            for s in args.symbols
            if f"{s}_USD" in SYMBOL_MAP
        }
        missing = [s for s in args.symbols if f"{s}_USD" not in SYMBOL_MAP]
        if missing:
            print(f"Warning: unknown symbols: {missing}")
            print(f"Available: {[k.replace('_USD','') for k in SYMBOL_MAP]}")
    else:
        symbols = {k.replace("_USD", ""): v for k, v in SYMBOL_MAP.items()}

    print("=" * 60)
    print("KRAKEN OHLCV BACKFILL")
    print("=" * 60)
    print(f"  Days: {args.days}")
    print(f"  Symbols: {list(symbols.keys())}")
    print(f"  Data dir: {DATA_DIR}")

    for short_name, pair in symbols.items():
        csv_name = f"{short_name}_USD"
        try:
            backfill_symbol(csv_name, pair, args.days)
        except Exception as e:
            print(f"  ERROR: {e}")
        # Rate limit between symbols
        time.sleep(2)

    print("\nDone!")


if __name__ == "__main__":
    main()
