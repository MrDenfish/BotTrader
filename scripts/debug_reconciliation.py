#!/usr/bin/env python3
"""
Debug Reconciliation Issue

Check what order_ids the exchange is actually returning vs what's in our database.
"""

import asyncio
import os
import sys
from pathlib import Path
from datetime import datetime, timedelta

# Ensure project root is in path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


async def init_dependencies():
    """Initialize dependencies."""
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

    log_config = {"log_level": "WARNING"}
    logger_manager = LoggerManager(log_config)
    shared_logger = logger_manager.get_logger("shared_logger")

    database_session_manager = DatabaseSessionManager(
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
    await database_session_manager.initialize()

    rest_client = config.rest_client

    return database_session_manager, rest_client


async def debug_symbol(db, rest_client, symbol: str, start_date: datetime, end_date: datetime):
    """Debug a single symbol's reconciliation."""
    from sqlalchemy import text
    from decimal import Decimal

    print(f"\n{'=' * 80}")
    print(f"DEBUG: {symbol}")
    print(f"{'=' * 80}")
    print(f"Date range: {start_date} to {end_date}")

    # Fetch from exchange
    print(f"\n📡 Fetching from exchange...")
    try:
        response = await asyncio.to_thread(
            rest_client.get_fills,
            product_id=symbol,
            start_sequence_timestamp=start_date.isoformat(),
            end_sequence_timestamp=end_date.isoformat()
        )

        exchange_fills = {}
        if hasattr(response, 'fills'):
            for fill in response.fills:
                exchange_fills[fill.order_id] = {
                    'order_id': fill.order_id,
                    'side': fill.side,
                    'size': Decimal(fill.size),
                    'price': Decimal(fill.price),
                    'time': fill.trade_time
                }

        print(f"   Found {len(exchange_fills)} fills on exchange")

        # Show first 5 from exchange
        print(f"\n   Sample exchange order_ids:")
        for i, (oid, data) in enumerate(list(exchange_fills.items())[:5], 1):
            print(f"      {i}. {oid} | {data['side']} | {data['size']} @ ${data['price']}")

    except Exception as e:
        print(f"   ❌ Error: {e}")
        return

    # Fetch from database
    print(f"\n💾 Fetching from database...")
    async with db.async_session() as session:
        result = await session.execute(text("""
            SELECT order_id, side, size, price, order_time
            FROM trade_records
            WHERE symbol = :symbol
              AND order_time >= :start_date
              AND order_time <= :end_date
            ORDER BY order_time
        """), {
            'symbol': symbol,
            'start_date': start_date,
            'end_date': end_date
        })

        db_rows = result.fetchall()
        db_order_ids = set(row[0] for row in db_rows)

        print(f"   Found {len(db_order_ids)} fills in database")

        # Show first 5 from database
        print(f"\n   Sample database order_ids:")
        for i, row in enumerate(db_rows[:5], 1):
            print(f"      {i}. {row[0]} | {row[1]} | {row[2]} @ ${row[3]}")

    # Compare
    print(f"\n🔍 Comparison:")
    exchange_order_ids = set(exchange_fills.keys())
    missing_from_db = exchange_order_ids - db_order_ids
    missing_from_exchange = db_order_ids - exchange_order_ids

    print(f"   Exchange has: {len(exchange_order_ids)} order_ids")
    print(f"   Database has: {len(db_order_ids)} order_ids")
    print(f"   Missing from DB: {len(missing_from_db)}")
    print(f"   Missing from exchange: {len(missing_from_exchange)}")

    if missing_from_db:
        print(f"\n   ⚠️  Orders on exchange but NOT in database:")
        for oid in list(missing_from_db)[:10]:
            data = exchange_fills[oid]
            print(f"      - {oid}")
            print(f"        {data['side']} | {data['size']} @ ${data['price']} | {data['time']}")

    if missing_from_exchange:
        print(f"\n   ⚠️  Orders in database but NOT on exchange (exchange may have pruned old data):")
        for oid in list(missing_from_exchange)[:5]:
            print(f"      - {oid}")


async def main():
    """Debug main symbols."""
    print("=" * 80)
    print("RECONCILIATION DEBUG")
    print("=" * 80)

    db, rest_client = await init_dependencies()

    from datetime import timezone as tz

    # Debug a few key symbols
    symbols_to_debug = [
        {
            'symbol': 'AVT-USD',
            'start': datetime(2025, 9, 13, tzinfo=tz.utc),
            'end': datetime(2025, 11, 12, tzinfo=tz.utc)
        },
        {
            'symbol': 'QI-USD',
            'start': datetime(2025, 8, 22, tzinfo=tz.utc),
            'end': datetime(2025, 10, 21, tzinfo=tz.utc)
        },
        {
            'symbol': 'DASH-USD',
            'start': datetime(2025, 9, 12, tzinfo=tz.utc),
            'end': datetime(2025, 12, 15, tzinfo=tz.utc)
        }
    ]

    for info in symbols_to_debug:
        await debug_symbol(
            db,
            rest_client,
            symbol=info['symbol'],
            start_date=info['start'],
            end_date=info['end']
        )

    print(f"\n{'=' * 80}")
    print("DEBUG COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
