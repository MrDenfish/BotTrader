#!/usr/bin/env python3
"""
Compute FIFO Allocations

This script computes FIFO allocations for trade records.

Usage:
    # Bootstrap Version 1 (compute all symbols)
    python -m scripts.compute_allocations --version 1 --all-symbols

    # Compute single symbol
    python -m scripts.compute_allocations --version 1 --symbol BTC-USD

    # Recompute (clears existing allocations for version)
    python -m scripts.compute_allocations --version 1 --all-symbols --force

Examples:
    python -m scripts.compute_allocations --version 1 --all-symbols
    python -m scripts.compute_allocations --version 2 --symbol ETH-USD
"""

import argparse
import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path

# Ensure project root is in path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


async def init_dependencies():
    """Initialize all required dependencies."""
    from Config.config_manager import CentralConfig
    from Shared_Utils.logging_manager import LoggerManager
    from Shared_Utils.precision import PrecisionUtils
    from SharedDataManager.shared_data_manager import SharedDataManager
    from database_manager.database_session_manager import DatabaseSessionManager
    from Shared_Utils.utility import SharedUtility

    # Load config
    config = CentralConfig(is_docker=False)

    # Get database URL
    dsn = getattr(config, "database_url", None) or os.getenv("DATABASE_URL")
    if not dsn:
        raise RuntimeError("No database URL found. Set DATABASE_URL or configure database_url in config.")

    # Normalize DSN for asyncpg
    if dsn.startswith("postgres://"):
        dsn = dsn.replace("postgres://", "postgresql+asyncpg://", 1)
    elif dsn.startswith("postgresql://"):
        dsn = dsn.replace("postgresql://", "postgresql+asyncpg://", 1)

    # Initialize logger
    log_config = {"log_level": os.getenv("LOG_LEVEL", "INFO")}
    logger_manager = LoggerManager(log_config)
    shared_logger = logger_manager.get_logger("shared_logger")

    # Initialize database session manager
    database_session_manager = DatabaseSessionManager(
        dsn,
        logger=shared_logger,
        echo=False,
        pool_size=int(os.getenv("DB_POOL_SIZE", "5")),
        max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "5")),
        pool_timeout=int(os.getenv("DB_POOL_TIMEOUT", "10")),
        pool_recycle=int(os.getenv("DB_POOL_RECYCLE", "300")),
        pool_pre_ping=True,
        future=True,
    )
    await database_session_manager.initialize()

    # Initialize SharedDataManager (needed for PrecisionUtils)
    try:
        shared_data_manager = SharedDataManager.__new__(SharedDataManager)
        shared_utils_utility = SharedUtility.get_instance(logger_manager)

        # Initialize PrecisionUtils
        precision_utils = PrecisionUtils.get_instance(logger_manager, shared_data_manager)
    except Exception as e:
        # If PrecisionUtils fails to initialize, use None (FIFO engine has fallback)
        shared_logger.warning(f"PrecisionUtils initialization failed: {e}. Using fallback precision.")
        precision_utils = None

    return database_session_manager, logger_manager, precision_utils, shared_logger


async def compute_allocations(args):
    """Main computation logic."""
    from fifo_engine import FifoAllocationEngine
    from sqlalchemy import text

    print("=" * 80)
    print("FIFO ALLOCATION COMPUTATION")
    print("=" * 80)
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Version: {args.version}")

    # Initialize dependencies
    print("\n🔧 Initializing dependencies...")
    db, logger_manager, precision_utils, logger = await init_dependencies()

    # Initialize FIFO engine
    engine = FifoAllocationEngine(
        database_session_manager=db,
        logger_manager=logger_manager,
        precision_utils=precision_utils
    )

    # Check if version already has allocations
    async with db.async_session() as session:
        result = await session.execute(text("""
            SELECT COUNT(*) FROM fifo_allocations WHERE allocation_version = :version
        """), {'version': args.version})
        existing_count = result.fetchone()[0]

    if existing_count > 0 and not args.force:
        print(f"\n⚠️  Version {args.version} already has {existing_count:,} allocations!")
        print("    Use --force to recompute (will delete existing allocations)")
        return

    if existing_count > 0 and args.force:
        print(f"\n🗑️  Clearing {existing_count:,} existing allocations for Version {args.version}...")

    # Determine what to compute
    if args.all_symbols:
        print(f"\n🚀 Computing allocations for ALL symbols...")
        result = await engine.compute_all_symbols(
            version=args.version,
            triggered_by='manual'
        )
    elif args.symbol:
        print(f"\n🚀 Computing allocations for {args.symbol}...")
        result = await engine.compute_symbol(
            symbol=args.symbol,
            version=args.version
        )
    else:
        print("\n❌ Error: Must specify either --all-symbols or --symbol")
        return

    # Display results
    print("\n" + "=" * 80)
    print("COMPUTATION RESULTS")
    print("=" * 80)

    if result.success:
        print(f"✅ Computation SUCCESSFUL")
        print(f"\nStatistics:")
        print(f"  - Version: {result.version}")
        print(f"  - Batch ID: {result.batch_id}")
        print(f"  - Symbols processed: {len(result.symbols_processed)}")
        if result.symbols_processed:
            print(f"    ({', '.join(result.symbols_processed[:5])}" +
                  (f", ... and {len(result.symbols_processed) - 5} more" if len(result.symbols_processed) > 5 else "") + ")")
        print(f"  - Buys processed: {result.buys_processed:,}")
        print(f"  - Sells processed: {result.sells_processed:,}")
        print(f"  - Allocations created: {result.allocations_created:,}")
        if result.total_pnl is not None:
            print(f"  - Total PnL: ${result.total_pnl:,.2f}")
        if result.duration_ms:
            duration_sec = result.duration_ms / 1000
            print(f"  - Duration: {duration_sec:.2f}s ({result.duration_ms:,}ms)")

        # Recommend next steps
        print(f"\n📋 Next Steps:")
        print(f"  1. Validate allocations:")
        print(f"     python -m scripts.validate_allocations --version {args.version}")
        print(f"  2. Generate reports:")
        print(f"     python -m scripts.allocation_reports --version {args.version}")

    else:
        print(f"❌ Computation FAILED")
        print(f"\nError:")
        print(f"  {result.error_message}")

    print(f"\nFinished: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


def main():
    """Parse arguments and run computation."""
    parser = argparse.ArgumentParser(
        description="Compute FIFO allocations for trade records",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Bootstrap Version 1 (all symbols)
  python -m scripts.compute_allocations --version 1 --all-symbols

  # Compute single symbol
  python -m scripts.compute_allocations --version 1 --symbol BTC-USD

  # Recompute (force)
  python -m scripts.compute_allocations --version 1 --all-symbols --force

Version Guidelines:
  - Version 1: Initial bootstrap (full computation)
  - Version 2+: After algorithm changes, bug fixes, or data amendments
  - Use same version for incremental updates
        """
    )

    parser.add_argument(
        '--version',
        type=int,
        required=True,
        help='Allocation version number (e.g., 1 for initial bootstrap)'
    )

    # Mutually exclusive: either all symbols or specific symbol
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        '--all-symbols',
        action='store_true',
        help='Compute allocations for all symbols (full recomputation)'
    )
    group.add_argument(
        '--symbol',
        type=str,
        help='Compute allocations for a specific symbol (e.g., BTC-USD)'
    )

    parser.add_argument(
        '--force',
        action='store_true',
        help='Force recomputation (delete existing allocations for this version)'
    )

    args = parser.parse_args()

    # Run async computation
    asyncio.run(compute_allocations(args))


if __name__ == "__main__":
    main()
