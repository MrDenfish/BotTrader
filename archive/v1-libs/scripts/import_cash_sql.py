#!/usr/bin/env python3
import csv
from datetime import datetime, timezone
from decimal import Decimal

# Skip first 2 header lines, read CSV
CSV_FILE = "/tmp/coinbase_usd_transactions.csv"
INCEPTION = datetime(2023, 11, 23, tzinfo=timezone.utc)

transactions = []

with open(CSV_FILE, 'r') as f:
    next(f)  # Skip "Transactions" line
    next(f)  # Skip "User" line
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
        total = row['Total (inclusive of fees and/or spread)'].strip()
        total = total.replace('$', '').replace(',', '').replace(' ', '')
        if total.startswith('('):
            total = '-' + total[1:-1]
        total = Decimal(total)

        # Normalize type
        tx_type = row['Transaction Type']
        if qty >= 0:
            norm_type = 'deposit'
        else:
            norm_type = 'withdrawal'

        transactions.append({
            'id': row['ID'],
            'date': ts.isoformat(),
            'type': tx_type,
            'norm': norm_type,
            'qty': float(qty),
            'amt': float(abs(total)),
            'notes': row['Notes'].replace("'", "''")  # Escape quotes
        })

# Generate SQL
print("BEGIN;")
for tx in transactions:
    print(f"""INSERT INTO cash_transactions (transaction_id, transaction_date, transaction_type, normalized_type, asset, quantity, amount_usd, notes, source) VALUES ('{tx['id']}', '{tx['date']}', '{tx['type']}', '{tx['norm']}', 'USD', {tx['qty']}, {tx['amt']}, '{tx['notes']}', 'coinbase_advanced') ON CONFLICT (transaction_id) DO NOTHING;""")

print("COMMIT;")
print(f"-- Total: {len(transactions)} transactions", end='')
