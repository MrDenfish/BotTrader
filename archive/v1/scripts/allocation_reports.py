#!/usr/bin/env python3
"""
FIFO Allocation Reports

Generate reports from FIFO allocations.

Usage:
    # PnL by symbol
    python -m scripts.allocation_reports --version 1 --pnl-by-symbol

    # Allocation summary
    python -m scripts.allocation_reports --version 1 --summary

    # Unmatched sells
    python -m scripts.allocation_reports --version 1 --unmatched

    # All reports
    python -m scripts.allocation_reports --version 1 --all
"""

import argparse
import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path
from decimal import Decimal

# Ensure project root is in path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


async def init_database():
    """Initialize database connection."""
    from Config.config_manager import CentralConfig
    from Shared_Utils.logging_manager import LoggerManager
    from database_manager.database_session_manager import DatabaseSessionManager

    config = CentralConfig(is_docker=False)
    dsn = getattr(config, "database_url", None) or os.getenv("DATABASE_URL")

    if not dsn:
        raise RuntimeError("No database URL found.")

    if dsn.startswith("postgres://"):
        dsn = dsn.replace("postgres://", "postgresql+asyncpg://", 1)
    elif dsn.startswith("postgresql://"):
        dsn = dsn.replace("postgresql://", "postgresql+asyncpg://", 1)

    log_config = {"log_level": "WARNING"}  # Quiet for reports
    logger_manager = LoggerManager(log_config)
    shared_logger = logger_manager.get_logger("shared_logger")

    db = DatabaseSessionManager(
        dsn,
        logger=shared_logger,
        echo=False,
        pool_size=2,
        max_overflow=2,
        pool_timeout=10,
        pool_recycle=300,
        pool_pre_ping=True,
        future=True,
    )
    await db.initialize()

    return db


async def report_summary(db, version):
    """Generate allocation summary report."""
    from sqlalchemy import text

    print("=" * 80)
    print(f"ALLOCATION SUMMARY - Version {version}")
    print("=" * 80)

    async with db.async_session() as session:
        # Get health stats
        result = await session.execute(text("""
            SELECT * FROM v_allocation_health WHERE allocation_version = :version
        """), {'version': version})

        row = result.fetchone()
        if not row:
            print(f"⚠️  No allocations found for Version {version}")
            return

        data = dict(row._mapping)

        print(f"\nOverall Statistics:")
        print(f"  - Total allocations: {data.get('total_allocations', 0):,}")
        print(f"  - Sells matched: {data.get('sells_matched', 0):,}")
        print(f"  - Buys used: {data.get('buys_used', 0):,}")
        print(f"  - Unmatched sells: {data.get('unmatched_sells', 0):,}")
        print(f"  - Total PnL: ${float(data.get('total_pnl', 0)):,.2f}")

        if data.get('first_allocation'):
            print(f"  - First allocation: {data['first_allocation']}")
        if data.get('last_allocation'):
            print(f"  - Last allocation: {data['last_allocation']}")


async def report_pnl_by_symbol(db, version):
    """Generate PnL by symbol report."""
    from sqlalchemy import text

    print("=" * 80)
    print(f"PnL BY SYMBOL - Version {version}")
    print("=" * 80)

    async with db.async_session() as session:
        result = await session.execute(text("""
            SELECT
                symbol,
                COUNT(DISTINCT sell_order_id) as num_sells,
                COUNT(DISTINCT buy_order_id) FILTER (WHERE buy_order_id IS NOT NULL) as num_buys,
                SUM(allocated_size) as total_size,
                SUM(pnl_usd) as total_pnl,
                AVG(pnl_usd) as avg_pnl,
                MIN(sell_time) as first_sell,
                MAX(sell_time) as last_sell
            FROM fifo_allocations
            WHERE allocation_version = :version
            GROUP BY symbol
            ORDER BY total_pnl DESC
        """), {'version': version})

        rows = result.fetchall()

        if not rows:
            print(f"⚠️  No allocations found for Version {version}")
            return

        # Print table header
        print(f"\n{'Symbol':<15} {'Sells':<8} {'Buys':<8} {'Size':<15} {'Total PnL':<15} {'Avg PnL':<15}")
        print("-" * 80)

        total_pnl = Decimal('0')
        for row in rows:
            data = dict(row._mapping)
            symbol = data['symbol']
            num_sells = data['num_sells']
            num_buys = data['num_buys']
            total_size = float(data['total_size']) if data['total_size'] else 0
            pnl = float(data['total_pnl']) if data['total_pnl'] else 0
            avg_pnl = float(data['avg_pnl']) if data['avg_pnl'] else 0

            total_pnl += Decimal(str(pnl))

            pnl_color = "+" if pnl >= 0 else ""
            print(f"{symbol:<15} {num_sells:<8} {num_buys:<8} {total_size:<15.4f} {pnl_color}${pnl:<14,.2f} ${avg_pnl:<14,.2f}")

        print("-" * 80)
        print(f"{'TOTAL':<15} {'':<8} {'':<8} {'':<15} ${float(total_pnl):<14,.2f}")
        print()


async def report_unmatched(db, version):
    """Generate unmatched sells report."""
    from sqlalchemy import text

    print("=" * 80)
    print(f"UNMATCHED SELLS - Version {version}")
    print("=" * 80)

    async with db.async_session() as session:
        result = await session.execute(text("""
            SELECT * FROM v_unmatched_sells WHERE allocation_version = :version
            ORDER BY sell_time DESC
        """), {'version': version})

        rows = result.fetchall()

        if not rows:
            print(f"\n✅ No unmatched sells found for Version {version}")
            return

        print(f"\n⚠️  Found {len(rows)} unmatched sell(s):\n")

        # Print table header
        print(f"{'Order ID':<40} {'Symbol':<15} {'Size':<15} {'Price':<12} {'Time':<20}")
        print("-" * 110)

        for row in rows:
            data = dict(row._mapping)
            order_id = data['sell_order_id'][:38] + "..."
            symbol = data['symbol']
            size = float(data['unmatched_size']) if data['unmatched_size'] else 0
            price = float(data['sell_price']) if data['sell_price'] else 0
            sell_time = str(data['sell_time'])[:19] if data['sell_time'] else ''

            print(f"{order_id:<40} {symbol:<15} {size:<15.4f} ${price:<11,.2f} {sell_time:<20}")

        print()
        print(f"These sells require manual investigation. Check manual_review_queue table.")


async def report_discrepancies(db, version):
    """Generate allocation discrepancies report."""
    from sqlalchemy import text

    print("=" * 80)
    print(f"ALLOCATION DISCREPANCIES - Version {version}")
    print("=" * 80)

    async with db.async_session() as session:
        result = await session.execute(text("""
            SELECT * FROM v_allocation_discrepancies
            ORDER BY ABS(discrepancy) DESC
            LIMIT 50
        """))

        rows = result.fetchall()

        if not rows:
            print(f"\n✅ No allocation discrepancies found")
            return

        print(f"\n⚠️  Found {len(rows)} discrepancy(ies):\n")

        # Print table header
        print(f"{'Order ID':<40} {'Symbol':<15} {'Sell Size':<12} {'Allocated':<12} {'Discrepancy':<12} {'Status':<15}")
        print("-" * 120)

        for row in rows:
            data = dict(row._mapping)
            order_id = str(data['sell_order_id'])[:38] + "..."
            symbol = data['symbol']
            sell_size = float(data['sell_size']) if data['sell_size'] else 0
            allocated = float(data['allocated_total']) if data['allocated_total'] else 0
            discrepancy = float(data['discrepancy']) if data['discrepancy'] else 0
            status = data['status']

            print(f"{order_id:<40} {symbol:<15} {sell_size:<12.4f} {allocated:<12.4f} {discrepancy:<12.4f} {status:<15}")

        print()


async def report_computation_log(db, version):
    """Show computation log for a version."""
    from sqlalchemy import text

    print("=" * 80)
    print(f"COMPUTATION LOG - Version {version}")
    print("=" * 80)

    async with db.async_session() as session:
        result = await session.execute(text("""
            SELECT * FROM fifo_computation_log
            WHERE allocation_version = :version
            ORDER BY computation_start DESC
            LIMIT 10
        """), {'version': version})

        rows = result.fetchall()

        if not rows:
            print(f"\n⚠️  No computation log found for Version {version}")
            return

        print(f"\nRecent computations ({len(rows)}):\n")

        for i, row in enumerate(rows, 1):
            data = dict(row._mapping)

            print(f"{i}. Computation #{data.get('id')}")
            print(f"   Status: {data.get('status')}")
            print(f"   Mode: {data.get('computation_mode')}")
            print(f"   Started: {data.get('computation_start')}")
            if data.get('computation_end'):
                print(f"   Ended: {data.get('computation_end')}")
            if data.get('computation_duration_ms'):
                duration_sec = data['computation_duration_ms'] / 1000
                print(f"   Duration: {duration_sec:.2f}s")
            if data.get('allocations_created'):
                print(f"   Allocations: {data['allocations_created']:,}")
            if data.get('symbols_processed'):
                symbols = data['symbols_processed']
                print(f"   Symbols: {', '.join(symbols[:5])}" +
                      (f" ... and {len(symbols) - 5} more" if len(symbols) > 5 else ""))
            if data.get('error_message'):
                print(f"   Error: {data['error_message']}")
            print()


async def generate_reports(args):
    """Generate requested reports."""
    print("=" * 80)
    print("FIFO ALLOCATION REPORTS")
    print("=" * 80)
    print(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Version: {args.version}\n")

    # Initialize database
    db = await init_database()

    # Generate requested reports
    if args.all or args.summary:
        await report_summary(db, args.version)
        print()

    if args.all or args.pnl_by_symbol:
        await report_pnl_by_symbol(db, args.version)
        print()

    if args.all or args.unmatched:
        await report_unmatched(db, args.version)
        print()

    if args.all or args.discrepancies:
        await report_discrepancies(db, args.version)
        print()

    if args.all or args.computation_log:
        await report_computation_log(db, args.version)
        print()

    print("=" * 80)


def main():
    """Parse arguments and generate reports."""
    parser = argparse.ArgumentParser(
        description="Generate FIFO allocation reports",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # All reports
  python -m scripts.allocation_reports --version 1 --all

  # PnL by symbol only
  python -m scripts.allocation_reports --version 1 --pnl-by-symbol

  # Unmatched sells only
  python -m scripts.allocation_reports --version 1 --unmatched

  # Multiple reports
  python -m scripts.allocation_reports --version 1 --summary --pnl-by-symbol
        """
    )

    parser.add_argument(
        '--version',
        type=int,
        required=True,
        help='Allocation version number'
    )

    parser.add_argument(
        '--all',
        action='store_true',
        help='Generate all reports'
    )

    parser.add_argument(
        '--summary',
        action='store_true',
        help='Generate allocation summary'
    )

    parser.add_argument(
        '--pnl-by-symbol',
        action='store_true',
        help='Generate PnL by symbol report'
    )

    parser.add_argument(
        '--unmatched',
        action='store_true',
        help='Show unmatched sells'
    )

    parser.add_argument(
        '--discrepancies',
        action='store_true',
        help='Show allocation discrepancies'
    )

    parser.add_argument(
        '--computation-log',
        action='store_true',
        help='Show computation log'
    )

    args = parser.parse_args()

    # If no specific report requested, show summary
    if not any([args.all, args.summary, args.pnl_by_symbol, args.unmatched,
                args.discrepancies, args.computation_log]):
        args.summary = True

    # Run async reports
    asyncio.run(generate_reports(args))


if __name__ == "__main__":
    main()
