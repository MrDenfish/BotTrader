#!/usr/bin/env python3
"""
Maintenance script to fix historical PnL calculations.

This script recomputes FIFO parent linkages and PnL for all SELL orders
in the database, fixing the issue where Coinbase's stale parent_ids
were causing incorrect cost basis calculations.

Usage:
    python3 fix_pnl_historical.py

Note: Run this from the project root directory
"""

import asyncio
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv

# Load environment
load_dotenv()

# Import required components
from database_manager.database_session_manager import DatabaseSessionManager
from SharedDataManager.shared_data_manager import SharedDataManager


async def main():
    """Run PnL fix maintenance."""
    print("="*80)
    print("STARTING PNL FIX MAINTENANCE")
    print("="*80)

    # Database setup
    db_host = os.getenv("DB_HOST", "127.0.0.1")
    db_port = os.getenv("DB_PORT", "5432")
    db_name = os.getenv("DB_NAME", "bot_trader_db")
    db_user = os.getenv("DB_USER", "bot_user")
    db_password = os.getenv("DB_PASSWORD")

    database_url = f"postgresql+asyncpg://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
    db_manager = DatabaseSessionManager(database_url)

    try:
        # Initialize SharedDataManager
        print("\nInitializing SharedDataManager...")
        shared_data_manager = SharedDataManager(db_manager)
        await shared_data_manager.initialize()

        print("\nRunning fix_unlinked_sells() to recompute all FIFO linkages...")
        print("This will:")
        print("  - Find all SELL orders with incorrect parent linkages")
        print("  - Recompute cost basis using proper FIFO matching")
        print("  - Update parent_ids, cost_basis_usd, pnl_usd, remaining_size")
        print("")
        print("This may take several minutes depending on trade history size...")
        print("")

        # Run the fix
        await shared_data_manager.trade_recorder.fix_unlinked_sells()

        print("")
        print("="*80)
        print("PNL FIX MAINTENANCE COMPLETE")
        print("="*80)
        print("")
        print("Next steps:")
        print("1. Run SQL queries to verify DASH-USD and ZEC-USD PnL is now correct")
        print("2. Check that recent sells are matched to recent buys (same day)")
        print("3. Verify remaining_size is properly decremented on parent buys")

    except Exception as e:
        print(f"❌ Maintenance failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        await db_manager.close()
        print("\nDatabase connection closed")


if __name__ == "__main__":
    asyncio.run(main())
