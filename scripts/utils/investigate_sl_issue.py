#!/usr/bin/env python3
"""
Investigate why stop losses aren't triggering.
"""
import json
import sys
import os
from datetime import datetime
import pg8000.native

print("=" * 80)
print("STOP LOSS INVESTIGATION")
print("=" * 80)
print(f"Timestamp: {datetime.now()}\n")

# Connect to database
conn = pg8000.native.Connection(
    host="127.0.0.1",
    port=5432,
    database="bot_trader_db",
    user="bot_user",
    password="your_secure_password_here"
)

# Get order_management data
print("📊 Fetching current positions...")
result = conn.run("SELECT data FROM shared_data WHERE data_type = 'order_management'")

if not result:
    print("❌ No order_management data found")
    sys.exit(1)

data = json.loads(result[0][0])

# Extract positions
positions = data.get('positions', {})

if not positions:
    print("✓ No open positions found in order_management")
    sys.exit(0)

print(f"\n📍 Found {len(positions)} open position(s):\n")

# Display each position
for symbol, pos_data in positions.items():
    print("─" * 80)
    print(f"Symbol: {symbol}")
    print(f"  Order ID: {pos_data.get('order_id', 'N/A')}")
    print(f"  Entry Price: ${pos_data.get('entry_price', 'N/A')}")
    print(f"  Size: {pos_data.get('size', 'N/A')}")
    print(f"  Stop Loss: ${pos_data.get('sl_price', 'N/A')}")
    print(f"  Take Profit: ${pos_data.get('tp_price', 'N/A')}")
    print(f"  Opened: {pos_data.get('timestamp', 'N/A')}")

    # Calculate how long it's been open
    if 'timestamp' in pos_data:
        try:
            ts = datetime.fromisoformat(pos_data['timestamp'].replace('Z', '+00:00'))
            age = datetime.now(ts.tzinfo) - ts
            print(f"  Age: {age.days} days, {age.seconds // 3600} hours")
        except:
            pass

print("\n" + "=" * 80)
print("ANALYSIS")
print("=" * 80)

# Get current market prices from most recent trade_records
print("\n📈 Fetching current market prices...")
for symbol in positions.keys():
    query = f"""
        SELECT price, order_time
        FROM trade_records
        WHERE symbol = '{symbol}'
        ORDER BY order_time DESC
        LIMIT 1
    """
    result = conn.run(query)

    if result:
        current_price = float(result[0][0])
        last_update = result[0][1]
        entry_price = float(positions[symbol].get('entry_price', 0))
        sl_price = float(positions[symbol].get('sl_price', 0))

        print(f"\n{symbol}:")
        print(f"  Current Price: ${current_price:.2f} (as of {last_update})")
        print(f"  Entry Price: ${entry_price:.2f}")
        print(f"  Stop Loss: ${sl_price:.2f}")

        if current_price > 0 and sl_price > 0:
            # For a SELL order (short position), SL should trigger when price goes UP
            distance_from_entry = ((current_price - entry_price) / entry_price) * 100
            distance_from_sl = ((current_price - sl_price) / sl_price) * 100

            print(f"  Distance from entry: {distance_from_entry:+.2f}%")
            print(f"  Distance from SL: {distance_from_sl:+.2f}%")

            # Check if SL should have triggered (for short positions)
            if current_price >= sl_price:
                print(f"  ⚠️  STOP LOSS SHOULD HAVE TRIGGERED! Price ${current_price:.2f} >= SL ${sl_price:.2f}")
            else:
                print(f"  ✓ Stop loss not yet hit (price ${current_price:.2f} < SL ${sl_price:.2f})")

print("\n" + "=" * 80)
print("RECOMMENDATIONS")
print("=" * 80)
print("""
1. Check bot logs for stop loss trigger attempts
2. Verify stop loss logic is running (ticker updates)
3. Check Coinbase API order status
4. Review stop loss trigger conditions in code
""")
