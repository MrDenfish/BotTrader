#!/usr/bin/env python3
"""
Investigate Missing Buy Records for Unmatched Sells

This script analyzes the 19 unmatched sells to determine if they have
corresponding buy orders that are missing from the database.
"""

import asyncio
import os
import sys
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


async def investigate_missing_buys():
    """Main investigation logic."""
    from sqlalchemy import text

    print("=" * 80)
    print("INVESTIGATING MISSING BUY RECORDS FOR UNMATCHED SELLS")
    print("=" * 80)

    db = await init_database()

    async with db.async_session() as session:
        print("\n" + "=" * 80)
        print("INVENTORY ANALYSIS FOR UNMATCHED SELLS")
        print("=" * 80)

        # Main inventory check query
        result = await session.execute(text("""
            WITH unmatched_sells AS (
                SELECT
                    fa.sell_order_id,
                    tr.symbol,
                    tr.size,
                    tr.price,
                    tr.order_time,
                    fa.notes
                FROM fifo_allocations fa
                JOIN trade_records tr ON tr.order_id = fa.sell_order_id
                WHERE fa.allocation_version = 1
                  AND fa.buy_order_id IS NULL
                ORDER BY tr.symbol, tr.order_time
            ),

            symbol_trade_analysis AS (
                SELECT
                    tr.symbol,
                    COUNT(*) FILTER (WHERE tr.side = 'buy') as buy_count,
                    COUNT(*) FILTER (WHERE tr.side = 'sell') as sell_count,
                    SUM(tr.size) FILTER (WHERE tr.side = 'buy') as total_buy_size,
                    SUM(tr.size) FILTER (WHERE tr.side = 'sell') as total_sell_size
                FROM trade_records tr
                WHERE tr.symbol IN (SELECT DISTINCT symbol FROM unmatched_sells)
                GROUP BY tr.symbol
            ),

            inventory_check AS (
                SELECT
                    us.symbol,
                    us.sell_order_id,
                    us.size as sell_size,
                    us.order_time as sell_time,
                    sta.total_buy_size,
                    sta.total_sell_size,
                    (sta.total_buy_size - sta.total_sell_size) as net_inventory,
                    sta.buy_count,
                    sta.sell_count,
                    us.notes,
                    (
                        SELECT
                            COALESCE(SUM(t2.size) FILTER (WHERE t2.side = 'buy'), 0) -
                            COALESCE(SUM(t2.size) FILTER (WHERE t2.side = 'sell'), 0)
                        FROM trade_records t2
                        WHERE t2.symbol = us.symbol
                          AND t2.order_time <= us.order_time
                    ) as inventory_at_sell_time
                FROM unmatched_sells us
                JOIN symbol_trade_analysis sta ON sta.symbol = us.symbol
            )

            SELECT
                symbol,
                sell_order_id,
                sell_size,
                TO_CHAR(sell_time, 'YYYY-MM-DD HH24:MI:SS') as sell_time,
                buy_count,
                sell_count,
                ROUND(total_buy_size::numeric, 4) as total_buy_size,
                ROUND(total_sell_size::numeric, 4) as total_sell_size,
                ROUND(net_inventory::numeric, 4) as net_inventory,
                ROUND(inventory_at_sell_time::numeric, 4) as inventory_at_sell_time,
                CASE
                    WHEN buy_count = 0 THEN 'NO BUYS IN DATABASE'
                    WHEN inventory_at_sell_time < 0 THEN '⚠️  NEGATIVE INVENTORY - MISSING BUYS'
                    WHEN inventory_at_sell_time < sell_size THEN '⚠️  INSUFFICIENT INVENTORY - MISSING BUYS'
                    ELSE 'Legitimate exhaustion'
                END as diagnosis,
                notes
            FROM inventory_check
            ORDER BY
                CASE
                    WHEN buy_count = 0 THEN 1
                    WHEN inventory_at_sell_time < 0 THEN 2
                    WHEN inventory_at_sell_time < sell_size THEN 3
                    ELSE 4
                END,
                symbol,
                sell_time
        """))

        rows = result.fetchall()

        if not rows:
            print("\n✅ No unmatched sells found!")
            return

        # Print results
        print(f"\n{'Symbol':<15} {'Sell Order ID':<40} {'Size':<12} {'Sell Time':<20} {'Buys':<6} {'Inventory@Time':<15} {'Diagnosis':<30}")
        print("-" * 160)

        diagnosis_summary = {
            'NO BUYS IN DATABASE': 0,
            '⚠️  NEGATIVE INVENTORY - MISSING BUYS': 0,
            '⚠️  INSUFFICIENT INVENTORY - MISSING BUYS': 0,
            'Legitimate exhaustion': 0
        }

        for row in rows:
            data = dict(row._mapping)
            symbol = data['symbol']
            order_id = data['sell_order_id'][:38] + "..."
            size = float(data['sell_size'])
            sell_time = data['sell_time']
            buy_count = data['buy_count']
            inventory_at_time = float(data['inventory_at_sell_time'])
            diagnosis = data['diagnosis']

            diagnosis_summary[diagnosis] += 1

            print(f"{symbol:<15} {order_id:<40} {size:<12.4f} {sell_time:<20} {buy_count:<6} {inventory_at_time:<15.4f} {diagnosis:<30}")

        # Summary
        print("\n" + "=" * 80)
        print("DIAGNOSIS SUMMARY")
        print("=" * 80)

        for diagnosis, count in diagnosis_summary.items():
            if count > 0:
                print(f"  {diagnosis}: {count}")

        # Time gap analysis
        print("\n" + "=" * 80)
        print("TIME GAP ANALYSIS: Suspicious gaps in trade times")
        print("=" * 80)

        result = await session.execute(text("""
            WITH trade_gaps AS (
                SELECT
                    symbol,
                    side,
                    order_id,
                    order_time,
                    LAG(order_time) OVER (PARTITION BY symbol, side ORDER BY order_time) as prev_time,
                    order_time - LAG(order_time) OVER (PARTITION BY symbol, side ORDER BY order_time) as time_gap
                FROM trade_records
                WHERE symbol IN (
                    SELECT DISTINCT tr.symbol
                    FROM fifo_allocations fa
                    JOIN trade_records tr ON tr.order_id = fa.sell_order_id
                    WHERE fa.allocation_version = 1 AND fa.buy_order_id IS NULL
                )
            )
            SELECT
                symbol,
                side,
                COUNT(*) as gap_count,
                MAX(time_gap) as max_gap,
                AVG(time_gap) as avg_gap
            FROM trade_gaps
            WHERE time_gap > INTERVAL '1 hour'
            GROUP BY symbol, side
            ORDER BY symbol, side
        """))

        gap_rows = result.fetchall()

        if gap_rows:
            print(f"\n{'Symbol':<15} {'Side':<6} {'Gaps > 1hr':<12} {'Max Gap':<20} {'Avg Gap':<20}")
            print("-" * 80)
            for row in gap_rows:
                data = dict(row._mapping)
                print(f"{data['symbol']:<15} {data['side']:<6} {data['gap_count']:<12} {str(data['max_gap']):<20} {str(data['avg_gap']):<20}")
        else:
            print("\n✅ No suspicious time gaps found")

        # Round-trip analysis
        print("\n" + "=" * 80)
        print("ROUND-TRIP TRADES: Buy→sell pairs with same size")
        print("=" * 80)

        result = await session.execute(text("""
            WITH buys AS (
                SELECT
                    order_id,
                    symbol,
                    size,
                    order_time
                FROM trade_records
                WHERE side = 'buy'
                  AND symbol IN (
                      SELECT DISTINCT tr.symbol
                      FROM fifo_allocations fa
                      JOIN trade_records tr ON tr.order_id = fa.sell_order_id
                      WHERE fa.allocation_version = 1 AND fa.buy_order_id IS NULL
                  )
            ),
            sells AS (
                SELECT
                    order_id,
                    symbol,
                    size,
                    order_time
                FROM trade_records
                WHERE side = 'sell'
                  AND symbol IN (
                      SELECT DISTINCT tr.symbol
                      FROM fifo_allocations fa
                      JOIN trade_records tr ON tr.order_id = fa.sell_order_id
                      WHERE fa.allocation_version = 1 AND fa.buy_order_id IS NULL
                  )
            )
            SELECT
                b.symbol,
                COUNT(*) as matching_round_trips
            FROM buys b
            JOIN sells s ON s.symbol = b.symbol
                AND ABS(s.size - b.size) < 0.0001
                AND s.order_time > b.order_time
                AND s.order_time - b.order_time < INTERVAL '1 day'
            GROUP BY b.symbol
            ORDER BY matching_round_trips DESC
        """))

        roundtrip_rows = result.fetchall()

        if roundtrip_rows:
            print(f"\n{'Symbol':<15} {'Round-trips':<15}")
            print("-" * 30)
            for row in roundtrip_rows:
                data = dict(row._mapping)
                print(f"{data['symbol']:<15} {data['matching_round_trips']:<15}")
        else:
            print("\n✅ No round-trip patterns found")

        print("\n" + "=" * 80)
        print("RECOMMENDATIONS")
        print("=" * 80)

        if diagnosis_summary['NO BUYS IN DATABASE'] > 0:
            print(f"\n🔍 {diagnosis_summary['NO BUYS IN DATABASE']} symbols have NO buy records at all")
            print("   Action: Check if these symbols were traded on exchange")

        if diagnosis_summary['⚠️  NEGATIVE INVENTORY - MISSING BUYS'] > 0:
            print(f"\n⚠️  {diagnosis_summary['⚠️  NEGATIVE INVENTORY - MISSING BUYS']} sells with NEGATIVE inventory")
            print("   Action: Buys are definitely missing from database - reconcile with exchange")

        if diagnosis_summary['⚠️  INSUFFICIENT INVENTORY - MISSING BUYS'] > 0:
            print(f"\n⚠️  {diagnosis_summary['⚠️  INSUFFICIENT INVENTORY - MISSING BUYS']} sells with INSUFFICIENT inventory")
            print("   Action: Some buys are missing - reconcile with exchange")

        print(f"\n📋 Next Step: Implement exchange reconciliation to find missing order_ids")


if __name__ == "__main__":
    asyncio.run(investigate_missing_buys())
