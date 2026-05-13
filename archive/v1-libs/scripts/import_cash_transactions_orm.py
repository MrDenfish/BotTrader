#!/usr/bin/env python3
"""
Import cash transactions from Coinbase CSV using SQLAlchemy ORM.

This script imports USD deposits and withdrawals from Coinbase transaction
history CSV into the cash_transactions table using the CashTransaction model.

Usage:
    python -m scripts.import_cash_transactions_orm --csv data/coinbase_usd_transactions.csv --dry-run
    python -m scripts.import_cash_transactions_orm --csv data/coinbase_usd_transactions.csv

Notes:
    - Inception date is 2023-11-22 (first GDAX → Coinbase Advanced transfer)
    - Only USD transactions are imported
    - Duplicates are handled via ON CONFLICT (transaction_id) DO NOTHING
"""

import argparse
import asyncio
import csv
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from TableModels.cash_transaction import CashTransaction


async def import_transactions(csv_path: str, dry_run: bool = False):
    """Import cash transactions from CSV file."""
    # Initialize database connection
    from scripts.compute_allocations import init_dependencies
    db, logger_manager, precision_utils, logger = await init_dependencies()

    print(f"Importing cash transactions from: {csv_path}")
    print(f"Mode: {'DRY RUN' if dry_run else 'LIVE IMPORT'}")
    print("")

    # Inception date - first real trading deposits
    INCEPTION = datetime(2023, 11, 22, tzinfo=timezone.utc)

    # Parse CSV
    transactions = []
    csv_file = Path(csv_path)

    if not csv_file.exists():
        print(f"ERROR: CSV file not found: {csv_path}")
        return

    with open(csv_file, 'r') as f:
        # Skip header lines (Transactions, User)
        next(f)
        next(f)
        reader = csv.DictReader(f)

        for row in reader:
            if not row.get('ID'):
                continue

            # Parse date
            ts = datetime.strptime(row['Timestamp'], "%Y-%m-%d %H:%M:%S %Z")
            ts = ts.replace(tzinfo=timezone.utc)

            # Filter: only USD, only after inception
            if row['Asset'] != 'USD' or ts < INCEPTION:
                continue

            # Parse amounts
            qty = Decimal(row['Quantity Transacted'].replace(',', ''))
            total_str = row['Total (inclusive of fees and/or spread)'].strip()
            total_str = total_str.replace('$', '').replace(',', '').replace(' ', '')
            if total_str.startswith('('):
                total_str = '-' + total_str[1:-1]
            total = Decimal(total_str)

            # Normalize type
            tx_type = row['Transaction Type']
            if qty >= 0:
                norm_type = 'deposit'
            else:
                norm_type = 'withdrawal'

            transactions.append({
                'transaction_id': row['ID'],
                'transaction_date': ts,
                'transaction_type': tx_type,
                'normalized_type': norm_type,
                'asset': 'USD',
                'quantity': float(qty),
                'amount_usd': float(abs(total)),
                'notes': row['Notes'],
                'source': 'coinbase_advanced'
            })

    print(f"Parsed {len(transactions)} transactions from CSV")
    print("")

    if len(transactions) == 0:
        print("No transactions to import!")
        return

    # Show summary
    total_deposits = sum(tx['amount_usd'] for tx in transactions if tx['normalized_type'] == 'deposit')
    total_withdrawals = sum(tx['amount_usd'] for tx in transactions if tx['normalized_type'] == 'withdrawal')

    print(f"Summary:")
    print(f"  Total deposits:    ${total_deposits:,.2f}")
    print(f"  Total withdrawals: ${total_withdrawals:,.2f}")
    print(f"  Net cash flow:     ${total_deposits - total_withdrawals:,.2f}")
    print("")

    if dry_run:
        print("DRY RUN - no changes made")
        print("")
        print("Sample transactions:")
        for tx in transactions[:5]:
            print(f"  {tx['transaction_date'].date()} | {tx['transaction_type']:20s} | ${tx['amount_usd']:>10.2f}")
        if len(transactions) > 5:
            print(f"  ... and {len(transactions) - 5} more")
        return

    # Import using SQLAlchemy ORM
    async with db.async_session() as session:
        # Use INSERT ... ON CONFLICT DO NOTHING for idempotency
        stmt = insert(CashTransaction).values(transactions)
        stmt = stmt.on_conflict_do_nothing(index_elements=['transaction_id'])

        result = await session.execute(stmt)
        await session.commit()

        print(f"Import complete!")
        print(f"Rows inserted: {result.rowcount if hasattr(result, 'rowcount') else 'N/A'}")

    # Verify import
    async with db.async_session() as session:
        count_query = select(CashTransaction).where(CashTransaction.source == 'coinbase_advanced')
        result = await session.execute(count_query)
        records = result.scalars().all()

        print(f"Total records in database: {len(records)}")

        # Calculate totals
        db_deposits = sum(float(r.amount_usd) for r in records if r.normalized_type == 'deposit')
        db_withdrawals = sum(float(r.amount_usd) for r in records if r.normalized_type == 'withdrawal')

        print(f"Database verification:")
        print(f"  Total deposits:    ${db_deposits:,.2f}")
        print(f"  Total withdrawals: ${db_withdrawals:,.2f}")
        print(f"  Net cash flow:     ${db_deposits - db_withdrawals:,.2f}")


def main():
    parser = argparse.ArgumentParser(description='Import cash transactions from CSV')
    parser.add_argument('--csv', required=True, help='Path to Coinbase transactions CSV')
    parser.add_argument('--dry-run', action='store_true', help='Preview without importing')

    args = parser.parse_args()

    asyncio.run(import_transactions(args.csv, args.dry_run))


if __name__ == '__main__':
    main()
