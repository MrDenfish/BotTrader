#!/usr/bin/env python3
"""
Migration: Remove deprecated P&L columns from trade_records

Removes columns that are no longer used after FIFO engine implementation:
- pnl_usd (replaced by fifo_allocations.pnl_usd)
- realized_profit (replaced by fifo_allocations.pnl_usd)
- parent_id (replaced by parent_ids array)
- cost_basis (replaced by fifo_allocations.cost_basis_usd)

PREREQUISITES:
- All code must use fifo_allocations table
- Soft deprecation period completed (21+ days)
- No DEPRECATED warnings in logs
- Backup of production database taken

Usage:
    python -m scripts.migrations.001_remove_deprecated_columns --dry-run
    python -m scripts.migrations.001_remove_deprecated_columns --execute
"""

import argparse
import asyncio
from sqlalchemy import text


async def migrate(dry_run: bool = True):
    """Execute the migration to remove deprecated columns."""
    from scripts.compute_allocations import init_dependencies
    db, logger_manager, precision_utils, logger = await init_dependencies()

    print("=" * 70)
    print("Migration 001: Remove Deprecated Columns from trade_records")
    print("=" * 70)
    print(f"Mode: {'DRY RUN (preview only)' if dry_run else 'EXECUTE (will modify database!)'}")
    print("")

    # Step 1: Verify all columns are NULL
    print("Step 1: Verifying columns are empty...")
    async with db.async_session() as session:
        verify_query = text("""
            SELECT
                COUNT(*) as total_trades,
                COUNT(pnl_usd) as has_pnl,
                COUNT(realized_profit) as has_realized,
                COUNT(parent_id) as has_parent,
                COUNT(cost_basis) as has_cost_basis
            FROM trade_records
        """)

        result = await session.execute(verify_query)
        row = result.fetchone()

        print(f"  Total trades in database: {row[0]:,}")
        print(f"  Trades with pnl_usd: {row[1]:,}")
        print(f"  Trades with realized_profit: {row[2]:,}")
        print(f"  Trades with parent_id: {row[3]:,}")
        print(f"  Trades with cost_basis: {row[4]:,}")
        print("")

        if row[1] > 0 or row[2] > 0 or row[3] > 0 or row[4] > 0:
            print("❌ ERROR: Some columns still have data!")
            print("")
            print("   These columns must be NULL before removal.")
            print("   Run soft deprecation first:")
            print("")
            print("   UPDATE trade_records SET")
            print("       pnl_usd = NULL,")
            print("       realized_profit = NULL,")
            print("       parent_id = NULL,")
            print("       cost_basis = NULL")
            print("   WHERE pnl_usd IS NOT NULL")
            print("      OR realized_profit IS NOT NULL")
            print("      OR parent_id IS NOT NULL")
            print("      OR cost_basis IS NOT NULL;")
            print("")
            return False

        print("✅ All deprecated columns are empty (NULL)")
        print("")

    # Step 2: Show current schema
    print("Step 2: Current schema...")
    async with db.async_session() as session:
        schema_query = text("""
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_name = 'trade_records'
                AND column_name IN ('pnl_usd', 'realized_profit', 'parent_id', 'cost_basis')
            ORDER BY ordinal_position
        """)

        result = await session.execute(schema_query)
        columns = result.fetchall()

        if columns:
            print("  Columns to be removed:")
            for col in columns:
                print(f"    - {col[0]:20s} ({col[1]:15s}, nullable: {col[2]})")
        else:
            print("  ⚠️  Columns already removed!")
        print("")

    if dry_run:
        print("=" * 70)
        print("DRY RUN - Would execute the following SQL:")
        print("=" * 70)
        print("""
ALTER TABLE trade_records
DROP COLUMN IF EXISTS pnl_usd,
DROP COLUMN IF EXISTS realized_profit,
DROP COLUMN IF EXISTS parent_id,
DROP COLUMN IF EXISTS cost_basis;
        """)
        print("=" * 70)
        print("")
        print("To execute this migration, run with --execute flag")
        print("")
        print("⚠️  IMPORTANT: Backup database before executing!")
        print("   docker exec db pg_dump -U bot_user bot_trader_db > backup.sql")
        print("")
        return True

    # Step 3: Execute migration
    print("Step 3: Executing migration...")
    print("  ⚠️  This will permanently remove 4 columns from trade_records")
    print("")

    # Give user a chance to cancel
    import sys
    try:
        response = input("  Type 'YES' to confirm: ")
        if response != 'YES':
            print("")
            print("Migration cancelled.")
            return False
    except (KeyboardInterrupt, EOFError):
        print("")
        print("Migration cancelled.")
        return False

    print("")
    print("  Dropping columns...")

    async with db.async_session() as session:
        drop_query = text("""
            ALTER TABLE trade_records
            DROP COLUMN IF EXISTS pnl_usd,
            DROP COLUMN IF EXISTS realized_profit,
            DROP COLUMN IF EXISTS parent_id,
            DROP COLUMN IF EXISTS cost_basis
        """)

        await session.execute(drop_query)
        await session.commit()

        print("  ✅ SQL executed successfully!")
        print("")

    # Step 4: Verify schema changes
    print("Step 4: Verifying schema changes...")
    async with db.async_session() as session:
        # Check removed columns
        removed_check = text("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'trade_records'
                AND column_name IN ('pnl_usd', 'realized_profit', 'parent_id', 'cost_basis')
        """)

        result = await session.execute(removed_check)
        remaining = result.fetchall()

        if remaining:
            print(f"  ⚠️  WARNING: {len(remaining)} columns still exist:")
            for col in remaining:
                print(f"    - {col[0]}")
            print("")
        else:
            print("  ✅ All 4 columns successfully removed!")
            print("")

        # Show updated schema
        all_columns_query = text("""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name = 'trade_records'
            ORDER BY ordinal_position
        """)

        result = await session.execute(all_columns_query)
        columns = result.fetchall()

        print(f"  Remaining columns in trade_records ({len(columns)} total):")
        for col in columns:
            print(f"    - {col[0]:30s} ({col[1]})")
        print("")

    # Step 5: Final checks
    print("Step 5: Final verification...")

    # Test that FIFO queries still work
    async with db.async_session() as session:
        test_query = text("""
            SELECT
                COUNT(DISTINCT tr.order_id) as sell_trades,
                COUNT(fa.id) as fifo_allocations,
                SUM(fa.pnl_usd) as total_pnl
            FROM trade_records tr
            LEFT JOIN fifo_allocations fa
                ON fa.sell_order_id = tr.order_id
                AND fa.allocation_version = 2
            WHERE tr.side = 'sell'
                AND tr.order_time >= NOW() - INTERVAL '7 days'
        """)

        result = await session.execute(test_query)
        row = result.fetchone()

        print(f"  Recent sell trades (7 days): {row[0]:,}")
        print(f"  FIFO allocations: {row[1]:,}")
        print(f"  Total P&L from FIFO: ${row[2]:.2f}" if row[2] else "  Total P&L from FIFO: $0.00")
        print("")

    print("=" * 70)
    print("✅ Migration completed successfully!")
    print("=" * 70)
    print("")
    print("Next steps:")
    print("1. Update TableModels/trade_record.py to remove column definitions")
    print("2. Restart containers: docker compose restart webhook sighook")
    print("3. Generate email report to verify everything works")
    print("4. Monitor logs for any errors")
    print("")

    return True


def main():
    parser = argparse.ArgumentParser(
        description='Remove deprecated columns from trade_records table',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Preview what will happen (safe, read-only)
  python -m scripts.migrations.001_remove_deprecated_columns --dry-run

  # Execute the migration (requires confirmation)
  python -m scripts.migrations.001_remove_deprecated_columns --execute

Prerequisites:
  1. Backup database first!
  2. Verify all deprecated columns are NULL
  3. Verify FIFO allocations contain all P&L data
  4. Verify reports use fifo_allocations (not trade_records columns)
        """
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Preview changes without modifying database (safe)'
    )
    parser.add_argument(
        '--execute',
        action='store_true',
        help='Execute the migration (requires confirmation)'
    )

    args = parser.parse_args()

    if not args.dry_run and not args.execute:
        parser.print_help()
        print("")
        print("ERROR: Must specify either --dry-run or --execute")
        return 1

    try:
        success = asyncio.run(migrate(dry_run=args.dry_run))
        return 0 if success else 1
    except KeyboardInterrupt:
        print("")
        print("Migration cancelled by user.")
        return 130
    except Exception as e:
        print(f"")
        print(f"❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    exit(main())
