#!/usr/bin/env python3
"""
Backfill trigger metadata for historical trades.

This script:
1. Queries trades with generic trigger values ('LIMIT', 'limit', 'MARKET', 'market')
2. Fetches the corresponding orders from Coinbase API
3. Parses the client_order_id to extract the real trigger
4. Updates the trade_records.trigger field

Usage:
    python scripts/backfill_trigger_metadata.py --dry-run  # Preview changes
    python scripts/backfill_trigger_metadata.py            # Apply changes
"""

import asyncio
import argparse
from datetime import datetime, timezone
from typing import Optional

# Add project root to path
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from Api_manager.coinbase_api import CoinbaseAPI
from Config.config_manager import CentralConfig
from database_manager.database_session_manager import DatabaseSessionManager
from TableModels.trade_record import TradeRecord


def parse_trigger_from_client_order_id(client_order_id: str) -> Optional[dict]:
    """
    Extract trigger from client_order_id format: {TRIGGER}-{SYMBOL}-{UUID}

    Examples:
      - "ROC_MOMO_24H-BTC-abc123" → {"trigger": "roc_momo_24h"}
      - "SCORE-ETH-def456" → {"trigger": "score"}

    Returns None if format doesn't match or is legacy format.
    """
    if not client_order_id:
        return None

    try:
        parts = str(client_order_id).split("-", 2)
        if len(parts) < 3:
            return None

        trigger_type = parts[0].lower()

        # Skip legacy source names
        old_sources = {"webhook", "passivemm", "sighook", "websocket", "position_monitor"}
        if trigger_type in old_sources:
            return None

        return {
            "trigger": trigger_type,
            "trigger_note": f"backfilled from client_order_id: {client_order_id}"
        }

    except Exception as e:
        print(f"Error parsing client_order_id '{client_order_id}': {e}")
        return None


async def main(dry_run: bool = True):
    """Backfill trigger metadata for historical trades."""

    config = CentralConfig()
    api = CoinbaseAPI(config)
    db_manager = DatabaseSessionManager(config)

    print(f"🔍 Starting trigger metadata backfill (dry_run={dry_run})")
    print("-" * 60)

    # Get trades with broken trigger metadata
    async with db_manager.async_session() as session:
        from sqlalchemy import select, or_

        query = select(TradeRecord).where(
            or_(
                TradeRecord.trigger['trigger'].astext == 'LIMIT',
                TradeRecord.trigger['trigger'].astext == 'limit',
                TradeRecord.trigger['trigger'].astext == 'MARKET',
                TradeRecord.trigger['trigger'].astext == 'market'
            )
        ).order_by(TradeRecord.order_time.desc()).limit(100)

        result = await session.execute(query)
        broken_trades = result.scalars().all()

    print(f"Found {len(broken_trades)} trades with generic trigger values\n")

    if not broken_trades:
        print("✅ No trades need backfilling!")
        return

    # Fetch corresponding orders from Coinbase
    print("📥 Fetching order details from Coinbase API...")
    params = {'limit': 500, 'order_status': ['FILLED']}
    response = await api.get_historical_orders_batch(params=params)
    orders = response.get('orders', [])

    # Create order lookup by order_id
    order_lookup = {o['order_id']: o for o in orders}
    print(f"Retrieved {len(orders)} orders from Coinbase\n")

    # Process each broken trade
    fixed_count = 0
    skipped_count = 0
    missing_count = 0

    for trade in broken_trades:
        order_id = trade.order_id
        symbol = trade.symbol
        old_trigger = trade.trigger

        # Look up order in Coinbase data
        order = order_lookup.get(order_id)

        if not order:
            print(f"⚠️  {order_id[:20]}... {symbol:10s} - Order not in Coinbase response")
            missing_count += 1
            continue

        # Parse trigger from client_order_id
        client_order_id = order.get('client_order_id')
        new_trigger = parse_trigger_from_client_order_id(client_order_id)

        if not new_trigger:
            print(f"⏭️  {order_id[:20]}... {symbol:10s} - No parseable client_order_id: {client_order_id}")
            skipped_count += 1
            continue

        # Show what would be updated
        print(f"✅ {order_id[:20]}... {symbol:10s} - {old_trigger['trigger']:10s} → {new_trigger['trigger']}")

        # Update the trade record
        if not dry_run:
            async with db_manager.async_session() as session:
                trade_to_update = await session.get(TradeRecord, order_id)
                if trade_to_update:
                    trade_to_update.trigger = new_trigger
                    await session.commit()

        fixed_count += 1

    # Summary
    print()
    print("=" * 60)
    print(f"📊 Backfill Summary:")
    print(f"   ✅ Fixed:   {fixed_count}")
    print(f"   ⏭️  Skipped: {skipped_count} (no parseable client_order_id)")
    print(f"   ⚠️  Missing: {missing_count} (order not in Coinbase response)")
    print(f"   📝 Total:   {len(broken_trades)}")
    print("=" * 60)

    if dry_run:
        print("\n⚠️  This was a DRY RUN - no changes were made")
        print("   Run without --dry-run to apply changes")
    else:
        print(f"\n✅ Backfill complete! Updated {fixed_count} trades")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill trigger metadata for historical trades")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without applying them")
    args = parser.parse_args()

    asyncio.run(main(dry_run=args.dry_run))
