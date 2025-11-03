#!/usr/bin/env python3
"""Test script for Config package with detailed diagnostics."""

import sys
import os

print("=" * 80)
print("Config Package Diagnostics")
print("=" * 80)
print()

# Python environment
print("Python Environment:")
print("-" * 80)
print(f"   Python executable: {sys.executable}")
print(f"   Python version: {sys.version}")
print(f"   Working directory: {os.getcwd()}")
print()

# Check dotenv
print("Checking python-dotenv:")
print("-" * 80)
try:
    import dotenv
    print(f"   ✅ dotenv found at: {dotenv.__file__}")
    print(f"   Version: {dotenv.__version__ if hasattr(dotenv, '__version__') else 'unknown'}")
except ImportError as e:
    print(f"   ❌ dotenv NOT found: {e}")
print()

# Test environment detection
print("Environment Detection:")
print("-" * 80)
from Config.environment import env
print(f"   Environment object: {env}")
print(f"   Is Docker: {env.is_docker}")
print(f"   Env Name: {env.env_name}")
print(f"   Env File: {env.env_file}")
print(f"   File exists: {env.env_file.exists() if env.env_file else False}")
print(f"   Loaded: {env._loaded}")
print()

# Check a few env vars that should be in .env_tradebot
print("Environment Variables (from .env):")
print("-" * 80)
test_vars = ['TAKER_FEE', 'MAKER_FEE', 'DB_HOST', 'QUOTE_CURRENCY', 'ATR_WINDOW']
for var in test_vars:
    val = os.getenv(var, 'NOT SET')
    print(f"   {var}: {val}")
print()

# Test constants
print("Core Constants:")
print("-" * 80)
from Config import constants_core
print(f"   POSITION_DUST_THRESHOLD: {constants_core.POSITION_DUST_THRESHOLD}")
print(f"   FAST_ROUNDTRIP_MAX_SECONDS: {constants_core.FAST_ROUNDTRIP_MAX_SECONDS}")
print()

print("Trading Constants:")
print("-" * 80)
from Config import constants_trading
print(f"   ATR_WINDOW: {constants_trading.ATR_WINDOW}")
print(f"   RSI_WINDOW: {constants_trading.RSI_WINDOW}")
print(f"   TAKER_FEE: {constants_trading.TAKER_FEE}")
print(f"   MAKER_FEE: {constants_trading.MAKER_FEE}")
print()

print("Report Constants:")
print("-" * 80)
from Config import constants_report
print(f"   DEFAULT_TOP_POSITIONS: {constants_report.DEFAULT_TOP_POSITIONS}")
print(f"   DEFAULT_LOOKBACK_HOURS: {constants_report.DEFAULT_LOOKBACK_HOURS}")
print()

# Final verdict
print("=" * 80)
if env._loaded and constants_trading.TAKER_FEE > 0:
    print("✅ Config system is working correctly!")
    print()
    print("Summary:")
    print(f"  - Environment: {env.env_name} ({'Docker' if env.is_docker else 'Desktop'})")
    print(f"  - Config file: {env.env_file}")
    print(f"  - Constants loaded: Yes")
    print(f"  - Ready to use: Yes")
else:
    print("⚠️  Config system loaded but may have issues")
print("=" * 80)