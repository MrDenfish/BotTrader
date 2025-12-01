#!/usr/bin/env python3
"""
Check ORDER_SIZE_FIAT configuration on the server.
"""
import os
import sys
from pathlib import Path
from decimal import Decimal

# Load .env file if it exists
env_path = Path(__file__).parent.parent / '.env'
if env_path.exists():
    from dotenv import load_dotenv
    load_dotenv(env_path)
    print(f"[INFO] Loaded environment from {env_path}\n")

print("=" * 80)
print("ORDER SIZE CONFIGURATION CHECK")
print("=" * 80)
print()

# Check environment variable
env_value = os.getenv('ORDER_SIZE_FIAT')
print(f"ORDER_SIZE_FIAT from environment: {env_value}")

# Check default from constants
default_value = float(os.getenv('ORDER_SIZE_FIAT', '60'))
print(f"ORDER_SIZE_FIAT with default fallback: {default_value}")

# Try to load Config
try:
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from Config.config_manager import CentralConfig as Config

    config = Config()
    config_value = config.order_size_fiat
    print(f"ORDER_SIZE_FIAT from CentralConfig: {config_value}")
    print()

    # Check USD balance
    print("=" * 80)
    print("USD BALANCE CHECK")
    print("=" * 80)

    import pg8000.native

    db_host = os.getenv('DB_HOST', '127.0.0.1')
    db_port = int(os.getenv('DB_PORT', '5432'))
    db_name = os.getenv('DB_NAME', 'bot_trader_db')
    db_user = os.getenv('DB_USER', 'bot_user')
    db_pass = os.getenv('DB_PASSWORD', '')

    if db_pass:
        conn = pg8000.native.Connection(
            host=db_host,
            port=db_port,
            database=db_name,
            user=db_user,
            password=db_pass
        )

        # Get USD balance from market_data
        result = conn.run("SELECT data FROM shared_data WHERE data_type = 'market_data'")
        if result:
            import json
            data = json.loads(result[0][0])
            spot_positions = data.get('spot_positions', {})
            usd_data = spot_positions.get('USD', {})

            total_usd = float(usd_data.get('total_balance_fiat', 0))
            available_usd = float(usd_data.get('available_to_trade_fiat', 0))

            print(f"\nUSD Balance:")
            print(f"  Total: ${total_usd:.2f}")
            print(f"  Available: ${available_usd:.2f}")
            print()
            print(f"ORDER_SIZE_FIAT setting: ${config_value}")
            print()

            if available_usd < float(config_value):
                print(f"⚠️  WARNING: Available USD (${available_usd:.2f}) < ORDER_SIZE_FIAT (${config_value})")
                print(f"   This will prevent new orders from being placed!")
                print(f"   Recommended: Either add more USD or reduce ORDER_SIZE_FIAT to ${int(available_usd - 5)}")
            else:
                print(f"✓ Available USD (${available_usd:.2f}) >= ORDER_SIZE_FIAT (${config_value})")
                print(f"  New orders should be able to be placed")

        conn.close()

except Exception as e:
    print(f"Error checking config: {e}")
    import traceback
    traceback.print_exc()

print()
print("=" * 80)
print("RECOMMENDATION")
print("=" * 80)
print()

if env_value and float(env_value) != default_value:
    print(f"⚠️  Mismatch detected!")
    print(f"   .env file has: {env_value}")
    print(f"   Container is using: {default_value}")
    print()
    print("Solution:")
    print("  1. Check /opt/bot/.env on AWS server")
    print("  2. Ensure it has ORDER_SIZE_FIAT=30")
    print("  3. Restart webhook container: docker restart webhook")
elif env_value == '30' and default_value == 60:
    print(f"⚠️  .env has ORDER_SIZE_FIAT=30 but container is using default 60")
    print()
    print("This means .env is not being loaded properly. Check:")
    print("  1. Docker volume mount: /opt/bot/.env:/app/.env:ro")
    print("  2. .env file exists and is readable on server")
    print("  3. Restart container after fixing")
else:
    print(f"✓ Configuration looks correct: ORDER_SIZE_FIAT={env_value or default_value}")
