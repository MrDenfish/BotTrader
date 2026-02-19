#!/usr/bin/env python3
"""
Verify ORDER_SIZE_FIAT loading from environment through CentralConfig.
This script tests the Singleton loading behavior.
"""
import os
import sys
from pathlib import Path

# Load .env file
env_path = Path(__file__).parent.parent / '.env'
if env_path.exists():
    from dotenv import load_dotenv
    load_dotenv(env_path)
    print(f"[INFO] Loaded .env from {env_path}\n")

print("=" * 80)
print("ORDER_SIZE_FIAT LOADING TEST")
print("=" * 80)
print()

# Check raw environment variable
raw_env = os.getenv('ORDER_SIZE_FIAT')
print(f"1. Raw os.getenv('ORDER_SIZE_FIAT'): {raw_env}")
print()

# Load CentralConfig (Singleton)
sys.path.insert(0, str(Path(__file__).parent.parent))
from Config.config_manager import CentralConfig

print("2. Loading CentralConfig (Singleton)...")
config = CentralConfig()
print(f"   config.order_size_fiat: {config.order_size_fiat}")
print(f"   config._is_loaded: {config._is_loaded}")
print()

# Try loading again (should return same instance)
print("3. Loading CentralConfig again (should be same instance)...")
config2 = CentralConfig()
print(f"   config2.order_size_fiat: {config2.order_size_fiat}")
print(f"   Same instance? {config is config2}")
print()

print("=" * 80)
print("CONCLUSION")
print("=" * 80)
print()
print("CentralConfig is a Singleton - it loads ONCE per Python process.")
print("When you 'docker restart', the Python process may not fully exit,")
print("leaving the old cached configuration in memory.")
print()
print("To fix:")
print("  1. SSH to AWS server")
print("  2. Run: docker-compose down")
print("  3. Verify ORDER_SIZE_FIAT=35 in /opt/bot/.env")
print("  4. Run: docker-compose up -d")
print()
print("This ensures Python process fully exits and reloads config on startup.")
