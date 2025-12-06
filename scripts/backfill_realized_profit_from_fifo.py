#!/usr/bin/env python3
"""
Backfill realized_profit from FIFO allocations.

This script updates the realized_profit column in trade_records to match
the values from the FIFO allocations table (allocation_version=2).

Background:
    The realized_profit column was previously populated by inline FIFO
    computation which caused data corruption (100x-1000x P&L errors).
    The FIFO allocations table is now the sole source of truth.

Usage:
    python -m scripts.backfill_realized_profit_from_fifo --version 2 --dry-run
    python -m scripts.backfill_realized_profit_from_fifo --version 2  # Actually update
"""

import argparse
import asyncio
from sqlalchemy import text


async def backfill(version: int, dry_run: bool = False):
    """Backfill realized_profit from FIFO allocations."""
    # Initialize database connection
    from scripts.compute_allocations import init_dependencies
    db, logger_manager, precision_utils, logger = await init_dependencies()

    print(f"Backfilling realized_profit from FIFO allocations (version {version})")
    print(f"Mode: {'DRY RUN' if dry_run else 'LIVE UPDATE'}")
    print("")

    # Count affected rows
    async with db.async_session() as session:
        count_query = text("""
            SELECT COUNT(*)
            FROM trade_records tr
            WHERE tr.side = 'sell'
              AND (tr.realized_profit IS NULL
                   OR ABS(tr.realized_profit - COALESCE((
                       SELECT SUM(fa.pnl_usd)
                       FROM fifo_allocations fa
                       WHERE fa.sell_order_id = tr.order_id
                         AND fa.allocation_version = :version
                   ), 0)) > 0.01)
        """)
        result = await session.execute(count_query, {'version': version})
        count = result.scalar()
        print(f"Rows to update: {count:,}")

    if count == 0:
        print("No rows need updating!")
        return

    if dry_run:
        print("")
        print("DRY RUN - no changes made")
        print("")
        print("Sample of rows that would be updated:")
        async with db.async_session() as session:
            sample_query = text("""
                SELECT
                    tr.order_id,
                    tr.symbol,
                    tr.order_time,
                    tr.realized_profit AS current_value,
                    COALESCE((
                        SELECT SUM(fa.pnl_usd)
                        FROM fifo_allocations fa
                        WHERE fa.sell_order_id = tr.order_id
                          AND fa.allocation_version = :version
                    ), 0) AS new_value
                FROM trade_records tr
                WHERE tr.side = 'sell'
                  AND (tr.realized_profit IS NULL
                       OR ABS(tr.realized_profit - COALESCE((
                           SELECT SUM(fa.pnl_usd)
                           FROM fifo_allocations fa
                           WHERE fa.sell_order_id = tr.order_id
                             AND fa.allocation_version = :version
                       ), 0)) > 0.01)
                ORDER BY tr.order_time DESC
                LIMIT 10
            """)
            result = await session.execute(sample_query, {'version': version})
            rows = result.fetchall()

            print(f"{'Order ID':<20} {'Symbol':<12} {'Current':<12} {'New':<12} {'Difference':<12}")
            print("-" * 80)
            for row in rows:
                order_id, symbol, order_time, current, new = row
                diff = (new or 0) - (current or 0)
                print(f"{order_id:<20} {symbol:<12} ${current or 0:>10.2f} ${new or 0:>10.2f} ${diff:>10.2f}")

        return

    # Perform update
    async with db.async_session() as session:
        update_query = text("""
            UPDATE trade_records tr
            SET realized_profit = (
                SELECT COALESCE(SUM(fa.pnl_usd), 0)
                FROM fifo_allocations fa
                WHERE fa.sell_order_id = tr.order_id
                  AND fa.allocation_version = :version
            )
            WHERE tr.side = 'sell'
              AND (tr.realized_profit IS NULL
                   OR ABS(tr.realized_profit - COALESCE((
                       SELECT SUM(fa.pnl_usd)
                       FROM fifo_allocations fa
                       WHERE fa.sell_order_id = tr.order_id
                         AND fa.allocation_version = :version
                   ), 0)) > 0.01)
        """)
        result = await session.execute(update_query, {'version': version})
        await session.commit()
        print(f"✅ Updated {result.rowcount:,} rows")
        print("")
        print("Backfill complete!")


def main():
    parser = argparse.ArgumentParser(description='Backfill realized_profit from FIFO allocations')
    parser.add_argument('--version', type=int, default=2, help='FIFO allocation version (default: 2)')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be updated without making changes')
    args = parser.parse_args()

    asyncio.run(backfill(args.version, args.dry_run))


if __name__ == "__main__":
    main()
