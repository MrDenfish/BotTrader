#!/usr/bin/env python3
"""
Import USD cash transactions from Coinbase CSV export.
Filters for USD-only deposits and withdrawals since inception date.
"""

import os
import sys
import csv
import re
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from sqlalchemy import create_engine, text
from dotenv import load_dotenv

# Load environment
load_dotenv(project_root / ".env")

# Database connection
DB_USER = os.getenv("DB_USER", "bot_user")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "bot_trader_db")

# Inception date (when GDAX transferred to Coinbase Advanced)
INCEPTION_DATE = datetime(2023, 11, 23, tzinfo=timezone.utc)

# CSV file path
CSV_PATH = project_root / "data" / "coinbase_usd_transactions.csv"

# Transaction type mapping
TYPE_MAPPING = {
    # Deposits (money coming IN)
    "Deposit": "deposit",
    "Pro Deposit": "deposit",  # Note: In CSV, negative qty means outflow FROM Coinbase TO Pro
    "Pro Withdrawal": "deposit",  # Positive qty means inflow FROM Pro TO Coinbase
    "Exchange Deposit": "deposit",  # Same logic as Pro Deposit

    # Withdrawals (money going OUT)
    "Withdrawal": "withdrawal",
    "Send": "withdrawal",
}

def clean_amount(value_str):
    """Clean dollar amounts from CSV format like '$1,000.00' or '($500.00)'"""
    if not value_str:
        return Decimal("0")

    # Remove currency symbols, commas, spaces
    cleaned = value_str.strip().replace("$", "").replace(",", "").replace(" ", "")

    # Handle parentheses (negative values)
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = "-" + cleaned[1:-1]

    try:
        return Decimal(cleaned)
    except Exception:
        return Decimal("0")


def normalize_transaction_type(tx_type, quantity):
    """
    Determine if transaction is deposit or withdrawal.

    Coinbase CSV logic:
    - "Deposit" with positive qty = deposit from bank
    - "Pro Deposit" with NEGATIVE qty = transfer FROM Coinbase TO Pro (outflow)
    - "Pro Withdrawal" with positive qty = transfer FROM Pro TO Coinbase (inflow)
    - "Exchange Deposit" with NEGATIVE qty = transfer FROM Coinbase TO GDAX (outflow)

    We want NET cash flow into/out of ALL Coinbase accounts combined.
    For our purposes:
    - Bank deposits = actual deposits
    - Internal transfers (Coinbase <-> Pro <-> Advanced) = ignore or track separately
    """
    tx_type_clean = tx_type.strip()

    # For inception transfer (Pro Withdrawal on 2023-11-23), treat as initial capital
    # These are transfers FROM Pro TO Advanced, so they're inflows to our trading account
    if tx_type_clean in ["Pro Withdrawal", "Pro Deposit", "Exchange Deposit"]:
        # Negative quantity in CSV means outflow, positive means inflow
        # But we want to track NET effect on available capital
        # For now, we'll import all and let the report logic handle it
        if quantity >= 0:
            return "deposit"  # Money came into Coinbase/Advanced
        else:
            return "withdrawal"  # Money left Coinbase (went to Pro/GDAX)

    # Bank deposits/withdrawals
    if tx_type_clean == "Deposit":
        return "deposit"
    elif tx_type_clean in ["Withdrawal", "Send"]:
        return "withdrawal"

    # Default mapping
    return TYPE_MAPPING.get(tx_type_clean, "unknown")


def parse_csv():
    """Parse Coinbase CSV and extract USD cash transactions since inception."""

    transactions = []
    skipped_count = 0
    error_count = 0

    print(f"Reading CSV from: {CSV_PATH}")

    with open(CSV_PATH, 'r', encoding='utf-8') as f:
        # Skip first 2 header lines (Transactions, User)
        next(f)
        next(f)

        reader = csv.DictReader(f)

        for row_num, row in enumerate(reader, start=4):  # CSV starts with headers at line 4
            try:
                # Skip header rows and empty rows
                if not row.get('ID') or row.get('ID') == 'ID':
                    continue

                # Parse timestamp
                timestamp_str = row.get('Timestamp', '').strip()
                if not timestamp_str:
                    skipped_count += 1
                    continue

                # Parse date (format: "2023-11-23 00:41:37 UTC")
                tx_date = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S %Z")
                tx_date = tx_date.replace(tzinfo=timezone.utc)

                # Filter: Only transactions on or after inception date
                if tx_date < INCEPTION_DATE:
                    skipped_count += 1
                    continue

                # Get asset - skip if not USD
                asset = row.get('Asset', '').strip()
                if asset != 'USD':
                    skipped_count += 1
                    continue

                # Get transaction type
                tx_type = row.get('Transaction Type', '').strip()

                # Get quantity (can be negative)
                quantity_str = row.get('Quantity Transacted', '0')
                quantity = clean_amount(quantity_str)

                # Normalize transaction type
                normalized_type = normalize_transaction_type(tx_type, quantity)

                # Skip unknown types
                if normalized_type == "unknown":
                    print(f"  ⚠️  Unknown type: {tx_type} (row {row_num})")
                    skipped_count += 1
                    continue

                # Get amounts
                subtotal = clean_amount(row.get('Subtotal', '0'))
                total = clean_amount(row.get('Total (inclusive of fees and/or spread)', '0'))
                fees = clean_amount(row.get('Fees and/or Spread', '0'))

                # Amount USD is absolute value of total
                amount_usd = abs(total)

                # Detect source
                source = "coinbase"
                if "Pro" in tx_type:
                    source = "coinbase_pro"
                elif "Exchange" in tx_type:
                    source = "gdax"
                elif tx_date >= datetime(2023, 11, 23, tzinfo=timezone.utc):
                    source = "coinbase_advanced"

                transactions.append({
                    'transaction_id': row.get('ID', '').strip(),
                    'transaction_date': tx_date,
                    'transaction_type': tx_type,
                    'normalized_type': normalized_type,
                    'asset': asset,
                    'quantity': quantity,
                    'amount_usd': amount_usd,
                    'subtotal': abs(subtotal),
                    'total': abs(total),
                    'fees': abs(fees),
                    'notes': row.get('Notes', '').strip(),
                    'source': source,
                })

            except Exception as e:
                print(f"  ❌ Error parsing row {row_num}: {e}")
                error_count += 1
                continue

    print(f"\n✅ Parsed {len(transactions)} transactions")
    print(f"   Skipped: {skipped_count}")
    print(f"   Errors: {error_count}")

    return transactions


def insert_transactions(transactions):
    """Insert transactions into database."""

    conn_str = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    engine = create_engine(conn_str)

    print(f"\n📊 Inserting {len(transactions)} transactions into database...")

    inserted = 0
    duplicates = 0

    with engine.connect() as conn:
        for tx in transactions:
            try:
                # Check if already exists
                check = conn.execute(text("""
                    SELECT id FROM cash_transactions
                    WHERE transaction_id = :tx_id
                """), {"tx_id": tx['transaction_id']}).fetchone()

                if check:
                    duplicates += 1
                    continue

                # Insert
                conn.execute(text("""
                    INSERT INTO cash_transactions (
                        transaction_id, transaction_date, transaction_type,
                        normalized_type, asset, quantity, amount_usd,
                        subtotal, total, fees, notes, source
                    ) VALUES (
                        :transaction_id, :transaction_date, :transaction_type,
                        :normalized_type, :asset, :quantity, :amount_usd,
                        :subtotal, :total, :fees, :notes, :source
                    )
                """), tx)

                inserted += 1

            except Exception as e:
                print(f"  ❌ Error inserting {tx['transaction_id']}: {e}")
                continue

        conn.commit()

    print(f"✅ Inserted: {inserted}")
    print(f"   Duplicates skipped: {duplicates}")

    return inserted


def verify_data():
    """Verify imported data and show summary."""

    conn_str = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    engine = create_engine(conn_str)

    print("\n📋 Verification Summary:")

    with engine.connect() as conn:
        # Total cash flow
        result = conn.execute(text("""
            SELECT
                COUNT(*) as total_transactions,
                SUM(CASE WHEN normalized_type = 'deposit' THEN amount_usd ELSE 0 END) as total_deposits,
                SUM(CASE WHEN normalized_type = 'withdrawal' THEN amount_usd ELSE 0 END) as total_withdrawals,
                SUM(CASE WHEN normalized_type = 'deposit' THEN amount_usd ELSE -amount_usd END) as net_cash_flow
            FROM cash_transactions
        """)).fetchone()

        print(f"   Total Transactions: {result[0]}")
        print(f"   Total Deposits: ${result[1]:,.2f}")
        print(f"   Total Withdrawals: ${result[2]:,.2f}")
        print(f"   Net Cash Flow: ${result[3]:,.2f}")

        # Inception transfer
        inception = conn.execute(text("""
            SELECT transaction_id, transaction_date, transaction_type, amount_usd
            FROM cash_transactions
            WHERE transaction_date >= :inception
            ORDER BY transaction_date
            LIMIT 5
        """), {"inception": INCEPTION_DATE}).fetchall()

        print(f"\n   First 5 transactions after inception ({INCEPTION_DATE.date()}):")
        for tx in inception:
            print(f"      {tx[1].date()} | {tx[2]:20s} | ${tx[3]:>10.2f} | {tx[0]}")


def main():
    """Main import process."""

    print("=" * 60)
    print("Cash Transactions Import")
    print("=" * 60)
    print(f"Inception Date: {INCEPTION_DATE.date()}")
    print(f"CSV File: {CSV_PATH}")
    print("=" * 60)

    # Parse CSV
    transactions = parse_csv()

    if not transactions:
        print("\n⚠️  No transactions found to import")
        return

    # Show sample
    print(f"\nSample transaction:")
    print(f"  Date: {transactions[0]['transaction_date']}")
    print(f"  Type: {transactions[0]['transaction_type']} → {transactions[0]['normalized_type']}")
    print(f"  Amount: ${transactions[0]['amount_usd']}")
    print(f"  Notes: {transactions[0]['notes'][:50]}")

    # Confirm
    response = input(f"\n❓ Import {len(transactions)} transactions? (yes/no): ")
    if response.lower() not in ['yes', 'y']:
        print("❌ Import cancelled")
        return

    # Insert
    insert_transactions(transactions)

    # Verify
    verify_data()

    print("\n✅ Import complete!")


if __name__ == "__main__":
    main()
