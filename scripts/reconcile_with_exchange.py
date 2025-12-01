#!/usr/bin/env python3
"""
Exchange Reconciliation Engine

Reconciles trade_records with Coinbase exchange to detect missing trades.

Tier 1: Lightweight check for unmatched sells only (fast)
Tier 2: Weekly full reconciliation (medium)
Tier 3: Deep audit (slow, on-demand)

Usage:
    # Tier 1: Quick check for Version 1 unmatched sells
    python -m scripts.reconcile_with_exchange --version 1 --tier 1

    # Tier 1 with auto-backfill (caution!)
    python -m scripts.reconcile_with_exchange --version 1 --tier 1 --auto-backfill

    # Tier 2: Weekly reconciliation
    python -m scripts.reconcile_with_exchange --tier 2

    # Tier 3: Deep audit for specific symbol
    python -m scripts.reconcile_with_exchange --tier 3 --symbol BTC-USD
"""

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Dict, Optional, Set
from decimal import Decimal

# Ensure project root is in path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


async def init_dependencies():
    """Initialize all required dependencies."""
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

    log_config = {"log_level": os.getenv("LOG_LEVEL", "INFO")}
    logger_manager = LoggerManager(log_config)
    shared_logger = logger_manager.get_logger("shared_logger")

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

    # Get REST client for exchange API
    rest_client = config.rest_client

    return database_session_manager, logger_manager, shared_logger, rest_client, config


class ExchangeReconciliationEngine:
    """Reconcile trade_records with Coinbase exchange."""

    def __init__(self, db, logger, rest_client, config):
        self.db = db
        self.logger = logger
        self.rest_client = rest_client
        self.config = config

    async def tier1_lightweight_check(self, version: int, auto_backfill: bool = False):
        """
        Tier 1: Quick check for unmatched sells only.

        Only checks symbols with unmatched sells, only for date ranges around those sells.
        Fast and targeted.
        """
        from sqlalchemy import text

        print("=" * 80)
        print("TIER 1: LIGHTWEIGHT RECONCILIATION CHECK")
        print("=" * 80)
        print(f"Version: {version}")
        print(f"Auto-backfill: {'ENABLED' if auto_backfill else 'DISABLED'}")
        print()

        async with self.db.async_session() as session:
            # Get unmatched sells with negative inventory
            result = await session.execute(text("""
                WITH unmatched_sells AS (
                    SELECT
                        fa.sell_order_id,
                        tr.symbol,
                        tr.size as sell_size,
                        tr.order_time as sell_time,
                        fa.notes
                    FROM fifo_allocations fa
                    JOIN trade_records tr ON tr.order_id = fa.sell_order_id
                    WHERE fa.allocation_version = :version
                      AND fa.buy_order_id IS NULL
                )
                SELECT
                    us.symbol,
                    us.sell_order_id,
                    us.sell_size,
                    us.sell_time,
                    (
                        SELECT
                            COALESCE(SUM(t2.size) FILTER (WHERE t2.side = 'buy'), 0) -
                            COALESCE(SUM(t2.size) FILTER (WHERE t2.side = 'sell'), 0)
                        FROM trade_records t2
                        WHERE t2.symbol = us.symbol
                          AND t2.order_time <= us.sell_time
                    ) as inventory_at_sell_time
                FROM unmatched_sells us
                WHERE (
                    SELECT
                        COALESCE(SUM(t2.size) FILTER (WHERE t2.side = 'buy'), 0) -
                        COALESCE(SUM(t2.size) FILTER (WHERE t2.side = 'sell'), 0)
                    FROM trade_records t2
                    WHERE t2.symbol = us.symbol
                      AND t2.order_time <= us.sell_time
                ) < us.sell_size
                ORDER BY us.symbol, us.sell_time
            """), {'version': version})

            problem_sells = result.fetchall()

            if not problem_sells:
                print("✅ No unmatched sells with inventory issues found!")
                return

            print(f"🔍 Found {len(problem_sells)} unmatched sells with potential missing buys")
            print()

            # Group by symbol for efficient API calls
            symbols_to_check = {}
            for row in problem_sells:
                data = dict(row._mapping)
                symbol = data['symbol']
                if symbol not in symbols_to_check:
                    symbols_to_check[symbol] = {
                        'sells': [],
                        'min_time': data['sell_time'],
                        'max_time': data['sell_time']
                    }
                symbols_to_check[symbol]['sells'].append(data)

                # Track date range
                if data['sell_time'] < symbols_to_check[symbol]['min_time']:
                    symbols_to_check[symbol]['min_time'] = data['sell_time']
                if data['sell_time'] > symbols_to_check[symbol]['max_time']:
                    symbols_to_check[symbol]['max_time'] = data['sell_time']

            # Process each symbol
            total_missing_buys = 0
            missing_buy_records = []

            for symbol, info in symbols_to_check.items():
                print("-" * 80)
                print(f"Symbol: {symbol}")
                print(f"Date range: {info['min_time']} to {info['max_time']}")
                print(f"Unmatched sells: {len(info['sells'])}")
                print()

                # Expand date range by 30 days before/after for safety
                start_date = info['min_time'] - timedelta(days=30)
                end_date = info['max_time'] + timedelta(days=30)

                print(f"📡 Fetching fills from exchange for {symbol}...")
                print(f"   Extended range: {start_date} to {end_date}")

                try:
                    # Fetch fills from exchange
                    exchange_fills = await self._fetch_exchange_fills(
                        symbol=symbol,
                        start_date=start_date,
                        end_date=end_date
                    )

                    print(f"   Found {len(exchange_fills)} fills on exchange")

                    # Get database order_ids for this symbol/date range
                    db_order_ids = await self._get_db_order_ids(
                        session=session,
                        symbol=symbol,
                        start_date=start_date,
                        end_date=end_date
                    )

                    print(f"   Found {len(db_order_ids)} fills in database")

                    # Find missing order_ids (on exchange but not in DB)
                    exchange_order_ids = set(exchange_fills.keys())
                    missing_order_ids = exchange_order_ids - db_order_ids

                    # Filter to only BUY orders
                    missing_buys = [
                        oid for oid in missing_order_ids
                        if exchange_fills[oid].get('side') == 'BUY'
                    ]

                    if missing_buys:
                        print(f"   ⚠️  Found {len(missing_buys)} missing BUY orders!")
                        total_missing_buys += len(missing_buys)

                        # Add ALL missing buys to records list (not just first 5)
                        for oid in missing_buys:
                            fill = exchange_fills[oid]
                            missing_buy_records.append({
                                'symbol': symbol,
                                'order_id': oid,
                                'fill': fill
                            })

                        # Display first 5 for readability
                        for oid in missing_buys[:5]:
                            fill = exchange_fills[oid]
                            print(f"      - {oid[:30]}... | Size: {fill.get('size')} | Price: ${fill.get('price')} | Time: {fill.get('time')}")

                        if len(missing_buys) > 5:
                            print(f"      ... and {len(missing_buys) - 5} more")
                    else:
                        print(f"   ✅ No missing buys found for {symbol}")

                except Exception as e:
                    print(f"   ❌ Error fetching exchange data: {e}")
                    self.logger.error(f"Exchange reconciliation error for {symbol}", exc_info=True)

                print()

            # Summary
            print("=" * 80)
            print("RECONCILIATION SUMMARY")
            print("=" * 80)
            print(f"Symbols checked: {len(symbols_to_check)}")
            print(f"Missing BUY orders found: {total_missing_buys}")
            print()

            if total_missing_buys > 0:
                print("⚠️  ACTION REQUIRED:")
                print(f"   {total_missing_buys} buy orders are on the exchange but missing from database")
                print()

                if auto_backfill:
                    print("🔄 AUTO-BACKFILL ENABLED - Inserting missing records...")
                    await self._backfill_missing_trades(session, missing_buy_records)
                else:
                    print("💡 RECOMMENDATIONS:")
                    print("   1. Review missing order_ids above")
                    print("   2. Investigate why these buys weren't imported")
                    print("   3. Manually backfill or run with --auto-backfill")
                    print(f"   4. Re-run FIFO: python -m scripts.compute_allocations --version {version + 1} --all-symbols")
            else:
                print("✅ No missing buys found - all exchange fills are in database")

    async def _fetch_exchange_fills(
        self,
        symbol: str,
        start_date: datetime,
        end_date: datetime
    ) -> Dict[str, Dict]:
        """
        Fetch fills from Coinbase exchange API.

        Returns: Dict[order_id, fill_data]
        """
        fills = {}

        try:
            # Coinbase API: list fills
            # https://docs.cdp.coinbase.com/advanced-trade/reference/retailbrokerageapi_getfills

            # Note: Coinbase API might require pagination for large result sets
            # This is a simplified implementation - production should handle pagination

            response = await asyncio.to_thread(
                self.rest_client.get_fills,
                product_id=symbol,
                start_sequence_timestamp=start_date.isoformat(),
                end_sequence_timestamp=end_date.isoformat()
            )

            if hasattr(response, 'fills'):
                for fill in response.fills:
                    fills[fill.order_id] = {
                        'order_id': fill.order_id,
                        'symbol': fill.product_id,
                        'side': fill.side,  # 'BUY' or 'SELL'
                        'size': Decimal(fill.size),
                        'price': Decimal(fill.price),
                        'fee': Decimal(getattr(fill, 'commission', '0')),
                        'time': fill.trade_time,
                        'trade_id': fill.trade_id
                    }

        except Exception as e:
            self.logger.error(f"Error fetching fills from exchange: {e}", exc_info=True)
            raise

        return fills

    async def _get_db_order_ids(
        self,
        session,
        symbol: str,
        start_date: datetime,
        end_date: datetime
    ) -> Set[str]:
        """Get all order_ids from database for given symbol/date range."""
        from sqlalchemy import text

        result = await session.execute(text("""
            SELECT order_id
            FROM trade_records
            WHERE symbol = :symbol
              AND order_time >= :start_date
              AND order_time <= :end_date
        """), {
            'symbol': symbol,
            'start_date': start_date,
            'end_date': end_date
        })

        rows = result.fetchall()
        return set(row[0] for row in rows)

    async def _backfill_missing_trades(self, session, missing_records: List[Dict]):
        """Insert missing trade records into database."""
        from sqlalchemy import text
        from datetime import datetime

        print(f"\n🔄 Backfilling {len(missing_records)} missing trades...")

        if not missing_records:
            print(f"⚠️  No records to backfill (list is empty)")
            return

        inserted = 0
        for record in missing_records:
            fill = record['fill']

            print(f"   Inserting: {fill['order_id'][:30]}... | {fill['symbol']} | {fill['side']} | {fill['size']}")

            try:
                # Convert ISO time string to datetime object
                time_str = fill['time']
                if isinstance(time_str, str):
                    order_time = datetime.fromisoformat(time_str.replace('Z', '+00:00'))
                else:
                    order_time = time_str

                result = await session.execute(text("""
                    INSERT INTO trade_records (
                        order_id, symbol, side, size, price, total_fees_usd, order_time, trigger
                    ) VALUES (
                        :order_id, :symbol, :side, :size, :price, :fee, :time, CAST(:trigger AS json)
                    )
                    ON CONFLICT (order_id) DO NOTHING
                    RETURNING order_id
                """), {
                    'order_id': fill['order_id'],
                    'symbol': fill['symbol'],
                    'side': fill['side'].lower(),  # Convert to lowercase
                    'size': fill['size'],
                    'price': fill['price'],
                    'fee': fill['fee'],
                    'time': order_time,  # Now a datetime object
                    'trigger': '{"trigger": "manual_backfill"}'  # JSON format
                })

                row = result.fetchone()
                if row:
                    inserted += 1
                    print(f"      ✅ Inserted")
                else:
                    print(f"      ⚠️  Skipped (already exists)")

            except Exception as e:
                print(f"      ❌ Error: {e}")
                self.logger.error(f"Error backfilling order_id {fill['order_id']}: {e}", exc_info=True)

        await session.commit()
        print(f"\n✅ Backfilled {inserted} trades successfully")

    async def tier2_weekly_reconciliation(self):
        """Tier 2: Compare counts by symbol."""
        print("Tier 2 reconciliation not yet implemented")
        # TODO: Implement count-based reconciliation

    async def tier3_deep_audit(self, symbol: Optional[str] = None):
        """Tier 3: Full order-by-order comparison."""
        print("Tier 3 deep audit not yet implemented")
        # TODO: Implement comprehensive audit


async def main():
    """Parse arguments and run reconciliation."""
    parser = argparse.ArgumentParser(
        description="Reconcile trade_records with Coinbase exchange",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Tier 1: Quick check for Version 1
  python -m scripts.reconcile_with_exchange --version 1 --tier 1

  # Tier 1 with auto-backfill (USE WITH CAUTION!)
  python -m scripts.reconcile_with_exchange --version 1 --tier 1 --auto-backfill

  # Tier 2: Weekly reconciliation
  python -m scripts.reconcile_with_exchange --tier 2

  # Tier 3: Deep audit for specific symbol
  python -m scripts.reconcile_with_exchange --tier 3 --symbol BTC-USD

Tiers:
  Tier 1: Fast, targeted check for unmatched sells (~1-2 min)
  Tier 2: Medium, count-based reconciliation (~5-10 min)
  Tier 3: Slow, comprehensive audit (~20-30 min)
        """
    )

    parser.add_argument(
        '--tier',
        type=int,
        choices=[1, 2, 3],
        required=True,
        help='Reconciliation tier (1=lightweight, 2=weekly, 3=deep)'
    )

    parser.add_argument(
        '--version',
        type=int,
        help='FIFO allocation version (required for Tier 1)'
    )

    parser.add_argument(
        '--symbol',
        type=str,
        help='Specific symbol to audit (Tier 3 only)'
    )

    parser.add_argument(
        '--auto-backfill',
        action='store_true',
        help='Automatically insert missing trades (USE WITH CAUTION!)'
    )

    args = parser.parse_args()

    # Validation
    if args.tier == 1 and not args.version:
        parser.error("--version is required for Tier 1 reconciliation")

    if args.tier == 3 and not args.symbol:
        parser.error("--symbol is required for Tier 3 reconciliation")

    # Initialize
    print("🔧 Initializing dependencies...")
    db, logger_manager, logger, rest_client, config = await init_dependencies()

    # Create engine
    engine = ExchangeReconciliationEngine(db, logger, rest_client, config)

    # Run reconciliation
    if args.tier == 1:
        await engine.tier1_lightweight_check(args.version, args.auto_backfill)
    elif args.tier == 2:
        await engine.tier2_weekly_reconciliation()
    elif args.tier == 3:
        await engine.tier3_deep_audit(args.symbol)


if __name__ == "__main__":
    asyncio.run(main())
