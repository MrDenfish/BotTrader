#!/usr/bin/env python3
"""Download Binance 1-minute OHLCV data and convert to backtest CSV format.

Downloads monthly ZIP archives from data.binance.vision, extracts, and converts
to the format expected by the v2 backtest framework:
    time,open,high,low,close,volume

Usage:
    python scripts/download_binance_data.py --set b
    python scripts/download_binance_data.py --set c
    python scripts/download_binance_data.py --set all
    python scripts/download_binance_data.py --symbols BNB,ATOM --months 2025-09,2025-10 --output backtest/data/custom/
"""

import argparse
import csv
import io
import os
import sys
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Dataset Definitions
# ---------------------------------------------------------------------------

# Set A (existing): BTC, ETH, SOL, XRP, ADA, DOGE, LINK, DOT, AVAX
#   Period: Sep 2025 – Mar 2026 (already in backtest/data/)

# Set B: 9 different symbols, SAME time period as Set A (symbol robustness test)
SET_B_SYMBOLS = ["BNB", "ATOM", "NEAR", "UNI", "LTC", "AAVE", "ICP", "FIL", "APT"]
SET_B_MONTHS = ["2025-09", "2025-10", "2025-11", "2025-12", "2026-01", "2026-02"]
SET_B_OUTPUT = "backtest/data/set_b"

# Set C: 9 more different symbols, SAME time period (more symbol robustness)
SET_C_SYMBOLS = ["ARB", "OP", "SUI", "INJ", "TAO", "HBAR", "ALGO", "FET", "LDO"]
SET_C_MONTHS = ["2025-09", "2025-10", "2025-11", "2025-12", "2026-01", "2026-02"]
SET_C_OUTPUT = "backtest/data/set_c"

# Binance data URL template
URL_TEMPLATE = (
    "https://data.binance.vision/data/spot/monthly/klines/"
    "{symbol}USDT/1m/{symbol}USDT-1m-{month}.zip"
)

# Binance CSV columns (no header in raw data)
# open_time, open, high, low, close, volume, close_time,
# quote_volume, count, taker_buy_volume, taker_buy_quote_volume, ignore
BINANCE_COLS = [
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_volume", "count",
    "taker_buy_volume", "taker_buy_quote_volume", "ignore",
]


def download_and_extract(symbol: str, month: str) -> list[list[str]]:
    """Download a monthly ZIP and return rows as lists of strings."""
    url = URL_TEMPLATE.format(symbol=symbol, month=month)
    print(f"  Downloading {symbol} {month} ... ", end="", flush=True)

    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
    except Exception as e:
        print(f"FAILED ({e})")
        return []

    size_mb = len(data) / 1024 / 1024
    print(f"{size_mb:.1f} MB ... ", end="", flush=True)

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        csv_name = zf.namelist()[0]
        with zf.open(csv_name) as f:
            text = f.read().decode("utf-8")
            reader = csv.reader(io.StringIO(text))
            rows = list(reader)

    print(f"{len(rows):,} rows")
    return rows


def convert_rows(rows: list[list[str]]) -> list[dict]:
    """Convert Binance raw rows to backtest format."""
    converted = []
    for row in rows:
        if len(row) < 6:
            continue
        # open_time is microsecond epoch (newer Binance format) or millisecond (older)
        ts = int(row[0])
        if ts > 1e15:  # microseconds
            dt = datetime.utcfromtimestamp(ts / 1_000_000)
        else:  # milliseconds
            dt = datetime.utcfromtimestamp(ts / 1_000)
        time_str = dt.strftime("%Y-%m-%d %H:%M:%S")
        converted.append({
            "time": time_str,
            "open": row[1],
            "high": row[2],
            "low": row[3],
            "close": row[4],
            "volume": row[5],
        })
    return converted


def write_csv(rows: list[dict], output_path: Path) -> None:
    """Write rows to CSV in backtest format."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["time", "open", "high", "low", "close", "volume"])
        writer.writeheader()
        writer.writerows(rows)


def download_symbol(symbol: str, months: list[str], output_dir: str) -> None:
    """Download all months for a symbol and write a single CSV."""
    print(f"\n{'='*60}")
    print(f"Symbol: {symbol}-USD  ({len(months)} months)")
    print(f"{'='*60}")

    all_rows = []
    for month in months:
        raw = download_and_extract(symbol, month)
        converted = convert_rows(raw)
        all_rows.extend(converted)

    if not all_rows:
        print(f"  WARNING: No data for {symbol}, skipping.")
        return

    # Sort by time (should already be sorted, but be safe)
    all_rows.sort(key=lambda r: r["time"])

    # Remove duplicates (overlapping month boundaries)
    seen = set()
    deduped = []
    for row in all_rows:
        if row["time"] not in seen:
            seen.add(row["time"])
            deduped.append(row)

    output_path = Path(output_dir) / f"{symbol}_USD.csv"
    write_csv(deduped, output_path)
    print(f"  Wrote {len(deduped):,} rows to {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Download Binance 1-min OHLCV data for backtesting")
    parser.add_argument(
        "--set", choices=["b", "c", "all"],
        help="Download predefined dataset (b, c, or all)",
    )
    parser.add_argument("--symbols", help="Comma-separated symbols (e.g., BNB,ATOM)")
    parser.add_argument("--months", help="Comma-separated months (e.g., 2025-09,2025-10)")
    parser.add_argument("--output", help="Output directory", default="backtest/data/custom")
    args = parser.parse_args()

    if args.set:
        sets_to_download = []
        if args.set in ("b", "all"):
            sets_to_download.append(("Set B", SET_B_SYMBOLS, SET_B_MONTHS, SET_B_OUTPUT))
        if args.set in ("c", "all"):
            sets_to_download.append(("Set C", SET_C_SYMBOLS, SET_C_MONTHS, SET_C_OUTPUT))

        for name, symbols, months, output in sets_to_download:
            print(f"\n{'#'*60}")
            print(f"# {name}: {len(symbols)} symbols x {len(months)} months")
            print(f"# Output: {output}/")
            print(f"{'#'*60}")
            for sym in symbols:
                download_symbol(sym, months, output)

        print("\n" + "=" * 60)
        print("DOWNLOAD COMPLETE")
        print("=" * 60)

    elif args.symbols and args.months:
        symbols = [s.strip().upper() for s in args.symbols.split(",")]
        months = [m.strip() for m in args.months.split(",")]
        for sym in symbols:
            download_symbol(sym, months, args.output)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
