#!/usr/bin/env python3
"""
Comprehensive position diagnostic - checks multiple sources.
"""
import json
import sys
import os
from datetime import datetime, timedelta
from pathlib import Path
import pg8000.native

# Load .env file if it exists
env_path = Path(__file__).parent.parent / '.env'
if env_path.exists():
    from dotenv import load_dotenv
    load_dotenv(env_path)
    print(f"[INFO] Loaded environment from {env_path}\n")

print("=" * 80)
print("COMPREHENSIVE POSITION DIAGNOSTIC")
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
print("1. CHECK ORDER_MANAGEMENT IN SHARED_DATA")
print("=" * 80)

result = conn.run("SELECT data FROM shared_data WHERE data_type = 'order_management'")
if result:
    data = json.loads(result[0][0])
    positions = data.get('positions', {})

    if positions:
        print(f"✓ Found {len(positions)} position(s) in order_management")
        for symbol, pos in positions.items():
            print(f"  - {symbol}: ${pos.get('entry_price')} SL=${pos.get('sl_price')}")
    else:
        print("✗ No positions in order_management.positions")
        print(f"  Keys in order_management: {list(data.keys())}")
else:
    print("✗ No order_management data found")

print("\n" + "=" * 80)
print("2. CHECK WEBHOOK_LIMIT_ONLY_POSITIONS TABLE")
print("=" * 80)

try:
    result = conn.run("SELECT COUNT(*) FROM webhook_limit_only_positions")
    count = result[0][0]
    print(f"Rows in webhook_limit_only_positions: {count}")

    if count > 0:
        result = conn.run("""
            SELECT order_id, symbol, entry_price, size, sl_price, tp_price, timestamp, source
            FROM webhook_limit_only_positions
            LIMIT 10
        """)
        print("\nRecent limit-only positions:")
        for row in result:
            print(f"  {row[1]}: Entry=${row[2]:.2f} SL=${row[4]:.2f} TP=${row[5]:.2f} ({row[6]})")
except Exception as e:
    print(f"✗ Table does not exist or error: {e}")

print("\n" + "=" * 80)
print("3. CHECK RECENT TRADE_RECORDS (LAST 7 DAYS)")
print("=" * 80)

seven_days_ago = datetime.now() - timedelta(days=7)
result = conn.run("""
    SELECT
        symbol,
        side,
        price,
        size,
        order_time,
        pnl_usd
    FROM trade_records
    WHERE order_time >= %s
    ORDER BY order_time DESC
    LIMIT 20
""", seven_days_ago)

if result:
    print(f"\nFound {len(result)} recent trades:")
    print("\n{:<15} {:<6} {:>12} {:>12} {:<20} {:>12}".format(
        "Symbol", "Side", "Price", "Size", "Time", "PnL"))
    print("-" * 80)
    for row in result:
        print("{:<15} {:<6} ${:>11.2f} {:>12.6f} {:<20} ${:>11.2f}".format(
            row[0], row[1], float(row[2]), float(row[3]),
            str(row[4])[:19], float(row[5]) if row[5] else 0.0
        ))
else:
    print("✗ No recent trades found")

print("\n" + "=" * 80)
print("4. CALCULATE NET POSITIONS FROM ALL TRADES")
print("=" * 80)

result = conn.run("""
    SELECT
        symbol,
        SUM(CASE WHEN LOWER(side) = 'buy' THEN size ELSE -size END) as net_qty,
        COUNT(*) as trade_count,
        MAX(order_time) as last_trade
    FROM trade_records
    GROUP BY symbol
    HAVING ABS(SUM(CASE WHEN LOWER(side) = 'buy' THEN size ELSE -size END)) > 0.0001
    ORDER BY last_trade DESC
""")

if result:
    print(f"\nFound {len(result)} symbols with non-zero net position:")
    print("\n{:<15} {:>15} {:>12} {:<20}".format(
        "Symbol", "Net Position", "Trades", "Last Trade"))
    print("-" * 80)
    for row in result:
        print("{:<15} {:>15.6f} {:>12} {:<20}".format(
            row[0], float(row[1]), row[2], str(row[3])[:19]
        ))
else:
    print("✓ All positions are flat (no net exposure)")

print("\n" + "=" * 80)
print("5. CHECK BOT STATUS (DOCKER)")
print("=" * 80)

try:
    import subprocess
    result = subprocess.run(['docker', 'ps', '--filter', 'name=bot'],
                          capture_output=True, text=True)
    if result.returncode == 0:
        print(result.stdout)
    else:
        print("Could not check Docker status (not running in Docker environment?)")
except:
    print("Docker not available or not running")

print("\n" + "=" * 80)
print("SUMMARY")
print("=" * 80)

print("""
Findings:
- order_management.positions is empty
- This could mean:
  1. All positions were recently closed (check recent trades above)
  2. Bot restarted and hasn't reloaded positions
  3. Stop losses triggered and closed positions
  4. Positions are tracked differently than expected

Next steps:
1. Check bot logs: docker logs bot | tail -100
2. Check if bot is running: docker ps
3. Look for recent sell orders in trade_records above
4. Verify Coinbase account directly
""")

conn.close()
