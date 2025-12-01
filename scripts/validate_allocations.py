#!/usr/bin/env python3
"""
Validate FIFO Allocations

This script validates FIFO allocations to ensure correctness.

Usage:
    # Validate a version
    python -m scripts.validate_allocations --version 1

    # Strict validation (warnings = errors)
    python -m scripts.validate_allocations --version 1 --strict

    # Generate detailed report
    python -m scripts.validate_allocations --version 1 --report
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
        raise RuntimeError("No database URL found.")

    # Normalize DSN
    if dsn.startswith("postgres://"):
        dsn = dsn.replace("postgres://", "postgresql+asyncpg://", 1)
    elif dsn.startswith("postgresql://"):
        dsn = dsn.replace("postgresql://", "postgresql+asyncpg://", 1)

    # Initialize logger
    log_config = {"log_level": os.getenv("LOG_LEVEL", "INFO")}
    logger_manager = LoggerManager(log_config)
    shared_logger = logger_manager.get_logger("shared_logger")

    # Initialize database
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

    # Initialize SharedDataManager & PrecisionUtils
    shared_data_manager = SharedDataManager.__new__(SharedDataManager)
    shared_utils_utility = SharedUtility.get_instance(logger_manager)
    precision_utils = PrecisionUtils.get_instance(logger_manager, shared_data_manager)

    return database_session_manager, logger_manager, precision_utils


async def validate_allocations(args):
    """Main validation logic."""
    from fifo_engine import AllocationValidator
    from sqlalchemy import text

    print("=" * 80)
    print("FIFO ALLOCATION VALIDATION")
    print("=" * 80)
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Version: {args.version}")
    print(f"Strict mode: {'ON' if args.strict else 'OFF'}")

    # Initialize dependencies
    print("\n🔧 Initializing dependencies...")
    db, logger_manager, precision_utils = await init_dependencies()

    # Initialize validator
    validator = AllocationValidator(
        database_session_manager=db,
        logger_manager=logger_manager,
        precision_utils=precision_utils
    )

    # Check if version exists
    async with db.async_session() as session:
        result = await session.execute(text("""
            SELECT COUNT(*) FROM fifo_allocations WHERE allocation_version = :version
        """), {'version': args.version})
        allocation_count = result.fetchone()[0]

    if allocation_count == 0:
        print(f"\n⚠️  Version {args.version} has no allocations!")
        print("    Run compute_allocations.py first to create allocations.")
        return

    print(f"📊 Found {allocation_count:,} allocations for Version {args.version}")

    # Run validation
    print(f"\n🔍 Validating allocations...")
    result = await validator.validate_version(
        version=args.version,
        strict=args.strict
    )

    # Display results
    print("\n" + "=" * 80)
    print("VALIDATION RESULTS")
    print("=" * 80)

    status_emoji = "✅" if result.is_valid else "❌"
    status_text = "PASSED" if result.is_valid else "FAILED"
    print(f"{status_emoji} Validation {status_text}")

    print(f"\nStatistics:")
    print(f"  - Total allocations: {result.total_allocations:,}")
    print(f"  - Total sells in DB: {result.total_sells:,}")
    print(f"  - Total buys in DB: {result.total_buys:,}")

    print(f"\nDiscrepancies:")
    print(f"  - Unmatched sells: {result.unmatched_sells:,}")
    print(f"  - Under-allocated sells: {result.under_allocated_sells:,}")
    print(f"  - Over-allocated sells: {result.over_allocated_sells:,}")
    print(f"  - Duplicate allocations: {result.duplicate_allocations:,}")

    if result.total_pnl_computed is not None:
        print(f"\nPnL:")
        print(f"  - Total PnL: ${result.total_pnl_computed:,.2f}")

    # Show errors
    if result.error_messages:
        print(f"\n❌ Errors ({len(result.error_messages)}):")
        for i, msg in enumerate(result.error_messages[:10], 1):
            print(f"  {i}. {msg}")
        if len(result.error_messages) > 10:
            print(f"  ... and {len(result.error_messages) - 10} more")

    # Show warnings
    if result.warnings:
        print(f"\n⚠️  Warnings ({len(result.warnings)}):")
        for i, msg in enumerate(result.warnings[:10], 1):
            print(f"  {i}. {msg}")
        if len(result.warnings) > 10:
            print(f"  ... and {len(result.warnings) - 10} more")

    # Generate detailed report if requested
    if args.report:
        print("\n" + "=" * 80)
        print("DETAILED HEALTH REPORT")
        print("=" * 80)

        health_report = await validator.generate_health_report(args.version)

        print(f"\nAllocation Health:")
        if health_report['health']:
            for key, value in health_report['health'].items():
                print(f"  - {key}: {value}")

        if health_report['unmatched_sells']:
            print(f"\nUnmatched Sells ({len(health_report['unmatched_sells'])}):")
            for i, sell in enumerate(health_report['unmatched_sells'][:5], 1):
                print(f"  {i}. {sell.get('sell_order_id')} - {sell.get('symbol')} - {sell.get('unmatched_size')}")
            if len(health_report['unmatched_sells']) > 5:
                print(f"  ... and {len(health_report['unmatched_sells']) - 5} more")

        if health_report['discrepancies']:
            print(f"\nDiscrepancies ({len(health_report['discrepancies'])}):")
            for i, disc in enumerate(health_report['discrepancies'][:5], 1):
                print(f"  {i}. {disc.get('sell_order_id')} - {disc.get('symbol')} - {disc.get('status')}")
            if len(health_report['discrepancies']) > 5:
                print(f"  ... and {len(health_report['discrepancies']) - 5} more")

    # Recommendations
    print(f"\n📋 Recommendations:")
    if result.is_valid:
        print(f"  ✅ Allocations are valid! Safe to use for reporting.")
        print(f"  - Generate reports: python -m scripts.allocation_reports --version {args.version}")
    else:
        print(f"  ⚠️  Allocations have errors. Investigate issues:")
        if result.unmatched_sells > 0:
            print(f"     - Check manual_review_queue table for unmatched sells")
        if result.under_allocated_sells > 0 or result.over_allocated_sells > 0:
            print(f"     - Run: SELECT * FROM v_allocation_discrepancies")
        print(f"     - Consider recomputing: python -m scripts.compute_allocations --version {args.version} --all-symbols --force")

    print(f"\nFinished: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


def main():
    """Parse arguments and run validation."""
    parser = argparse.ArgumentParser(
        description="Validate FIFO allocations for correctness",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic validation
  python -m scripts.validate_allocations --version 1

  # Strict validation (warnings = errors)
  python -m scripts.validate_allocations --version 1 --strict

  # Generate detailed health report
  python -m scripts.validate_allocations --version 1 --report

Validation Checks:
  - All sells are fully allocated
  - No duplicate allocations
  - No over-allocation
  - Temporal consistency (buy before sell)
  - Unmatched sell detection
        """
    )

    parser.add_argument(
        '--version',
        type=int,
        required=True,
        help='Allocation version number to validate'
    )

    parser.add_argument(
        '--strict',
        action='store_true',
        help='Strict mode: treat warnings as errors'
    )

    parser.add_argument(
        '--report',
        action='store_true',
        help='Generate detailed health report'
    )

    args = parser.parse_args()

    # Run async validation
    asyncio.run(validate_allocations(args))


if __name__ == "__main__":
    main()
