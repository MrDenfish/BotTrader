#!/usr/bin/env python3
"""
Investigate why stop losses aren't triggering (AWS version).
Run this on the AWS server to check actual open positions.
"""
import json
import sys
import os
from datetime import datetime
from pathlib import Path
import pg8000.native

# Load .env file if it exists
env_path = Path(__file__).parent.parent / '.env'
if env_path.exists():
    from dotenv import load_dotenv
    load_dotenv(env_path)
    print(f"[INFO] Loaded environment from {env_path}\n")

print("=" * 80)
print("STOP LOSS INVESTIGATION (AWS)")
print("=" * 80)
print(f"Timestamp: {datetime.now()}\n")

# Get database connection info from environment
db_host = os.getenv('DB_HOST', '127.0.0.1')
db_port = int(os.getenv('DB_PORT', '5432'))
db_name = os.getenv('DB_NAME', 'bot_trader_db')
db_user = os.getenv('DB_USER', 'bot_user')
db_pass = os.getenv('DB_PASSWORD', '')  # Note: DB_PASSWORD not DB_PASS

if not db_pass:
    print("❌ Error: DB_PASSWORD not found in environment")
    print("   Set DB_PASSWORD environment variable or add to .env file")
    sys.exit(1)

print(f"Connecting to {db_host}:{db_port}/{db_name} as {db_user}...")

# Connect to database
conn = pg8000.native.Connection(
    host=db_host,
    port=db_port,
    database=db_name,
    user=db_user,
    password=db_pass
)

# Get order_management data
print("\n📊 Fetching current positions from shared_data...")
result = conn.run("SELECT data FROM shared_data WHERE data_type = 'order_management'")

if not result:
    print("❌ No order_management data found")
    sys.exit(1)

data = json.loads(result[0][0])

# Extract positions
positions = data.get('positions', {})

if not positions:
    print("✓ No open positions found in order_management")
    print("\nℹ️  This could mean:")
    print("  1. All positions were closed")
    print("  2. Positions are tracked elsewhere")
    print("  3. Bot is not running or hasn't loaded positions yet")
    sys.exit(0)

print(f"\n📍 Found {len(positions)} open position(s):\n")

# Display each position
position_list = []
for symbol, pos_data in positions.items():
    print("─" * 80)
    print(f"Symbol: {symbol}")
    print(f"  Order ID: {pos_data.get('order_id', 'N/A')}")

    entry_price = pos_data.get('entry_price', 0)
    size = pos_data.get('size', 0)
    sl_price = pos_data.get('sl_price', 0)
    tp_price = pos_data.get('tp_price', 0)

    print(f"  Entry Price: ${entry_price}")
    print(f"  Size: {size}")
    print(f"  Stop Loss: ${sl_price}")
    print(f"  Take Profit: ${tp_price}")
    print(f"  Opened: {pos_data.get('timestamp', 'N/A')}")

    # Calculate how long it's been open
    if 'timestamp' in pos_data:
        try:
            ts = datetime.fromisoformat(pos_data['timestamp'].replace('Z', '+00:00'))
            age = datetime.now(ts.tzinfo) - ts
            print(f"  Age: {age.days} days, {age.seconds // 3600} hours")
        except Exception as e:
            print(f"  Age: (parse error: {e})")

    position_list.append({
        'symbol': symbol,
        'entry_price': float(entry_price) if entry_price else 0,
        'sl_price': float(sl_price) if sl_price else 0,
        'tp_price': float(tp_price) if tp_price else 0,
        'size': float(size) if size else 0
    })

print("\n" + "=" * 80)
print("MARKET PRICE ANALYSIS")
print("=" * 80)

# Get current market prices from most recent trade_records
print("\n📈 Fetching current market prices from trade_records...")
for pos in position_list:
    symbol = pos['symbol']
    query = f"""
        SELECT price, order_time
        FROM trade_records
        WHERE symbol = '{symbol}'
        ORDER BY order_time DESC
        LIMIT 1
    """
    result = conn.run(query)

    print(f"\n{symbol}:")
    if result:
        current_price = float(result[0][0])
        last_update = result[0][1]
        entry_price = pos['entry_price']
        sl_price = pos['sl_price']

        print(f"  Current Price: ${current_price:.4f} (as of {last_update})")
        print(f"  Entry Price: ${entry_price:.4f}")
        print(f"  Stop Loss: ${sl_price:.4f}")

        if current_price > 0 and sl_price > 0 and entry_price > 0:
            # For a SELL order (short position), SL should trigger when price goes UP
            pnl_pct = ((entry_price - current_price) / entry_price) * 100
            distance_from_sl = ((current_price - sl_price) / sl_price) * 100
            sl_distance_from_entry = ((sl_price - entry_price) / entry_price) * 100

            print(f"  Current P&L: {pnl_pct:+.2f}%")
            print(f"  Distance from SL: {distance_from_sl:+.2f}%")
            print(f"  SL offset from entry: {sl_distance_from_entry:+.2f}%")

            # Check if SL should have triggered (for short positions, SL is above entry)
            if current_price >= sl_price:
                diff = current_price - sl_price
                diff_pct = (diff / sl_price) * 100
                print(f"  🚨 STOP LOSS SHOULD HAVE TRIGGERED!")
                print(f"     Price ${current_price:.4f} is ${diff:.4f} ({diff_pct:.2f}%) ABOVE SL ${sl_price:.4f}")
            else:
                remaining = sl_price - current_price
                remaining_pct = (remaining / current_price) * 100
                print(f"  ✓ Stop loss not yet hit")
                print(f"     ${remaining:.4f} ({remaining_pct:.2f}%) to go before SL triggers")
    else:
        print(f"  ⚠️  No recent trade data found for {symbol}")

print("\n" + "=" * 80)
print("DIAGNOSTIC RECOMMENDATIONS")
print("=" * 80)
print("""
Next steps to diagnose stop loss issues:

1. CHECK BOT LOGS:
   docker logs bot | tail -100
   # Look for stop loss trigger attempts

2. VERIFY BOT IS RUNNING:
   docker ps | grep bot
   # Ensure bot container is active

3. CHECK TICKER UPDATES:
   docker logs bot | grep -i "ticker\|price update"
   # Verify price updates are being received

4. REVIEW STOP LOSS CODE:
   # Check SharedDataManager/order_management logic
   # Verify stop loss comparison logic

5. MANUAL COINBASE CHECK:
   # Log into Coinbase and verify order status
   # Check if orders still exist on exchange

If stop losses should have triggered but didn't:
- Bot may not be monitoring prices
- Stop loss logic may have a bug
- Orders may have been canceled on exchange
- Price data may not be updating
""")

conn.close()
