#!/usr/bin/env python3
"""
Diagnose why OCO orders aren't being placed - check for blocking conditions.
"""
import json
import sys
import os
from datetime import datetime
from pathlib import Path
import pg8000.native

# Load .env file
env_path = Path(__file__).parent.parent / '.env'
if env_path.exists():
    from dotenv import load_dotenv
    load_dotenv(env_path)
    print(f"[INFO] Loaded environment from {env_path}\n")

print("=" * 80)
print("OCO ORDER PLACEMENT DIAGNOSTIC")
print("=" * 80)
print(f"Timestamp: {datetime.now()}\n")

# Get database connection info from environment
db_host = os.getenv('DB_HOST', '127.0.0.1')
db_port = int(os.getenv('DB_PORT', '5432'))
db_name = os.getenv('DB_NAME', 'bot_trader_db')
db_user = os.getenv('DB_USER', 'bot_user')
db_pass = os.getenv('DB_PASSWORD', '')

if not db_pass:
    print("❌ Error: DB_PASSWORD not found in environment")
    sys.exit(1)

print(f"Connecting to {db_host}:{db_port}/{db_name} as {db_user}...\n")

conn = pg8000.native.Connection(
    host=db_host,
    port=db_port,
    database=db_name,
    user=db_user,
    password=db_pass
)

print("=" * 80)
print("1. CHECK ORDER_TRACKER IN ORDER_MANAGEMENT")
print("=" * 80)

result = conn.run("SELECT data FROM shared_data WHERE data_type = 'order_management'")
if result:
    data = json.loads(result[0][0])
    order_tracker = data.get('order_tracker', {})

    if order_tracker:
        print(f"✓ Found {len(order_tracker)} order(s) in order_tracker\n")
        for order_id, order_data in order_tracker.items():
            symbol = order_data.get('symbol', 'N/A')
            side = order_data.get('side', 'N/A')
            order_type = order_data.get('order_type', 'N/A')
            status = order_data.get('status', 'N/A')
            print(f"  Order ID: {order_id}")
            print(f"    Symbol: {symbol}")
            print(f"    Side: {side}")
            print(f"    Type: {order_type}")
            print(f"    Status: {status}")
            print()
    else:
        print("✓ No orders in order_tracker (empty)\n")
else:
    print("✗ No order_management data found\n")

print("=" * 80)
print("2. CHECK POSITIONS IN ORDER_MANAGEMENT")
print("=" * 80)

if result:
    data = json.loads(result[0][0])
    positions = data.get('positions', {})

    if positions:
        print(f"✓ Found {len(positions)} position(s) in order_management\n")
        for symbol, pos_data in positions.items():
            print(f"  Symbol: {symbol}")
            print(f"    Order ID: {pos_data.get('order_id', 'N/A')}")
            print(f"    Entry: ${pos_data.get('entry_price', 0)}")
            print(f"    SL: ${pos_data.get('sl_price', 0)}")
            print(f"    TP: ${pos_data.get('tp_price', 0)}")
            print()
    else:
        print("✓ No positions in order_management.positions\n")

print("=" * 80)
print("3. CHECK SPOT POSITIONS (HOLDINGS)")
print("=" * 80)

result = conn.run("SELECT data FROM shared_data WHERE data_type = 'market_data'")
if result:
    data = json.loads(result[0][0])
    spot_positions = data.get('spot_positions', {})

    holdings = []
    for asset, asset_data in spot_positions.items():
        if asset == 'USD':
            continue
        total_balance = float(asset_data.get('total_balance_crypto', 0))
        available_balance = float(asset_data.get('available_to_trade_crypto', 0))
        if total_balance > 0 or available_balance > 0:
            holdings.append({
                'asset': asset,
                'total': total_balance,
                'available': available_balance
            })

    if holdings:
        print(f"✓ Found {len(holdings)} non-zero holdings:\n")
        for h in holdings:
            print(f"  {h['asset']}: total={h['total']:.6f}, available={h['available']:.6f}")
        print()
    else:
        print("✓ No non-zero holdings found\n")

print("=" * 80)
print("4. CHECK FOR ORPHANED/STALE ORDERS")
print("=" * 80)

print("Checking if order_tracker contains orders for symbols with positions...\n")

if result:
    # Get order_management again
    result = conn.run("SELECT data FROM shared_data WHERE data_type = 'order_management'")
    order_mgmt_data = json.loads(result[0][0])
    order_tracker = order_mgmt_data.get('order_tracker', {})
    positions = order_mgmt_data.get('positions', {})

    # Get market data for holdings
    result = conn.run("SELECT data FROM shared_data WHERE data_type = 'market_data'")
    market_data = json.loads(result[0][0])
    spot_positions = market_data.get('spot_positions', {})

    # Find symbols with holdings but no positions
    untracked_symbols = []
    for asset, asset_data in spot_positions.items():
        if asset == 'USD':
            continue
        total_balance = float(asset_data.get('total_balance_crypto', 0))
        if total_balance > 0:
            symbol = f"{asset}-USD"
            if symbol not in positions:
                untracked_symbols.append(symbol)

    if untracked_symbols:
        print(f"⚠️  Found {len(untracked_symbols)} untracked symbols (holdings without positions):")
        for symbol in untracked_symbols:
            print(f"  - {symbol}")

            # Check if there's an open order for this symbol
            has_open_order = False
            for order_id, order_data in order_tracker.items():
                if order_data.get('symbol') == symbol:
                    has_open_order = True
                    print(f"    🔴 HAS OPEN ORDER: {order_id} (side={order_data.get('side')}, status={order_data.get('status')})")
                    print(f"       THIS IS BLOCKING OCO PLACEMENT!")

            if not has_open_order:
                print(f"    ✓ No open order blocking placement")
        print()
    else:
        print("✓ All holdings are tracked in positions\n")

print("=" * 80)
print("5. DIAGNOSIS SUMMARY")
print("=" * 80)

print("""
Root Cause Analysis:
====================

The asset_monitor detects untracked positions (holdings without protection) and
attempts to place OCO orders. However, the order placement is blocked if:

1. There's already an open order for that symbol in order_tracker
2. This causes place_order() to return (False, validation_result)
3. The validation succeeds ('is_valid': True) but the order isn't placed
4. The position remains unprotected
5. The cycle repeats every 60 seconds

Solution:
=========

If orphaned/stale orders are found above, they need to be cleaned up. Options:

A. Manual cleanup:
   - Cancel the stale orders on Coinbase
   - Remove them from order_tracker in shared_data

B. Code fix:
   - Modify asset_monitor to check if existing order is actually a protective OCO
   - If not, cancel the old order and place the new OCO
   - Or skip the has_open_order check for rearm_oco_missing triggers

C. Investigate why orders are orphaned:
   - Orders may have been placed but not tracked properly
   - Or tracked orders weren't cleaned up after fills/cancels
""")

conn.close()
