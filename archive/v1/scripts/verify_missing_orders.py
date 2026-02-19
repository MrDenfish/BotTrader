#!/usr/bin/env python3
"""
Verify Missing Orders on Coinbase Exchange

This script helps manually verify that the missing order_ids actually exist
on the Coinbase exchange before backfilling.

Usage:
    python -m scripts.verify_missing_orders
"""

import asyncio
import os
import sys
from pathlib import Path

# Ensure project root is in path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


# Missing order_ids identified by reconciliation
MISSING_ORDERS = [
    # AVT-USD
    {
        'order_id': '538ab11c-9d7d-48c9-9950-831fd3ae7f16',
        'symbol': 'AVT-USD',
        'size': 1.6,
        'price': 1.60,
        'time': '2025-10-13T04:26:12.091628Z'
    },
    {
        'order_id': 'c412c727-65b9-48be-a763-28abecc29f5e',
        'symbol': 'AVT-USD',
        'size': 13.4,
        'price': 1.60,
        'time': '2025-10-13T05:05:28.516775Z'
    },
    # DASH-USD
    {
        'order_id': 'a5203d1f-806c-4aaa-8455-bab6b1d1e8f3',
        'symbol': 'DASH-USD',
        'size': 0.7371,
        'price': 81.74,
        'time': '2025-11-15T02:34:02.990499Z'
    },
    # ELA-USD
    {
        'order_id': 'ead3cbf8-6605-464d-a934-260051e3f8a9',
        'symbol': 'ELA-USD',
        'size': 1.73,
        'price': 2.557,
        'time': '2025-09-12T10:32:27.004451Z'
    },
    {
        'order_id': '1446597b-8088-44d5-9e9f-6c3d29e95fc8',
        'symbol': 'ELA-USD',
        'size': 1.0,
        'price': 2.652,
        'time': '2025-09-11T18:29:40.033960Z'
    },
    {
        'order_id': 'bde39d34-457b-4b4b-9408-ec8684f0e927',
        'symbol': 'ELA-USD',
        'size': 6.0,
        'price': 2.662,
        'time': '2025-09-11T20:37:28.223988Z'
    },
    # EUL-USD
    {
        'order_id': 'ce7cdb99-6efa-4b61-9313-4ac64ae0f72c',
        'symbol': 'EUL-USD',
        'size': 6.0,
        'price': 9.729,
        'time': '2025-10-13T15:00:16.538203Z'
    },
    # METIS-USD
    {
        'order_id': 'b830f951-6173-4877-b6f8-cdcbe2b15f9a',
        'symbol': 'METIS-USD',
        'size': 2.407,
        'price': 9.42,
        'time': '2025-11-06T16:06:19.558272Z'
    },
    # QI-USD (LARGEST)
    {
        'order_id': 'c5e02fc4-a6af-4e0f-8d37-2e1e18d9c8f5',
        'symbol': 'QI-USD',
        'size': 5494.0,
        'price': 0.010163,
        'time': '2025-09-21T04:17:29.833828Z'
    },
    # SNX-USD
    {
        'order_id': '3151d12b-5c15-4f68-ad71-645315e8a4c7',
        'symbol': 'SNX-USD',
        'size': 1.533,
        'price': 1.044,
        'time': '2025-09-26T15:39:59.977541Z'
    },
    # ZORA-USD
    {
        'order_id': 'b1e60081-41d4-4865-b710-d61b2b8f5a9e',
        'symbol': 'ZORA-USD',
        'size': 103.0,
        'price': 0.10164,
        'time': '2025-10-13T05:33:48.547496Z'
    },
]


async def init_config():
    """Initialize configuration."""
    from Config.config_manager import CentralConfig

    config = CentralConfig(is_docker=False)
    rest_client = config.rest_client

    return rest_client


async def verify_order(rest_client, order_info):
    """
    Verify a single order exists on the exchange.

    Note: Coinbase API may not have a direct "get order by ID" endpoint
    for historical fills. This function attempts to fetch fills for the
    symbol/time range and check if the order_id exists.
    """
    from datetime import datetime, timedelta

    order_id = order_info['order_id']
    symbol = order_info['symbol']
    expected_size = order_info['size']
    expected_price = order_info['price']
    order_time_str = order_info['time']

    print(f"\n{'=' * 80}")
    print(f"Verifying: {order_id}")
    print(f"Symbol: {symbol}")
    print(f"Expected: {expected_size} @ ${expected_price}")
    print(f"Time: {order_time_str}")

    try:
        # Parse time and create range
        order_time = datetime.fromisoformat(order_time_str.replace('Z', '+00:00'))
        start_time = order_time - timedelta(hours=1)
        end_time = order_time + timedelta(hours=1)

        print(f"\n🔍 Fetching fills from exchange...")
        print(f"   Range: {start_time} to {end_time}")

        # Fetch fills from exchange
        response = await asyncio.to_thread(
            rest_client.get_fills,
            product_id=symbol,
            start_sequence_timestamp=start_time.isoformat(),
            end_sequence_timestamp=end_time.isoformat()
        )

        if hasattr(response, 'fills'):
            found = False
            for fill in response.fills:
                if fill.order_id == order_id:
                    found = True
                    print(f"\n✅ ORDER FOUND ON EXCHANGE!")
                    print(f"   Order ID: {fill.order_id}")
                    print(f"   Side: {fill.side}")
                    print(f"   Size: {fill.size}")
                    print(f"   Price: ${fill.price}")
                    print(f"   Time: {fill.trade_time}")
                    print(f"   Trade ID: {fill.trade_id}")

                    # Verify details match
                    if abs(float(fill.size) - expected_size) > 0.0001:
                        print(f"   ⚠️  SIZE MISMATCH: Expected {expected_size}, got {fill.size}")
                    if abs(float(fill.price) - expected_price) > 0.01:
                        print(f"   ⚠️  PRICE MISMATCH: Expected ${expected_price}, got ${fill.price}")
                    if fill.side.upper() != 'BUY':
                        print(f"   ⚠️  SIDE MISMATCH: Expected BUY, got {fill.side}")

                    return True

            if not found:
                print(f"\n❌ ORDER NOT FOUND IN EXCHANGE RESPONSE")
                print(f"   Searched {len(response.fills)} fills in time range")
                print(f"   Order may be outside the search window or doesn't exist")
                return False
        else:
            print(f"\n⚠️  No fills returned from exchange")
            return None

    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return None


async def main():
    """Main verification loop."""
    print("=" * 80)
    print("MISSING ORDER VERIFICATION")
    print("=" * 80)
    print(f"Total orders to verify: {len(MISSING_ORDERS)}")
    print()

    # Initialize
    rest_client = await init_config()

    # Verify each order
    results = {
        'verified': [],
        'not_found': [],
        'errors': []
    }

    for i, order_info in enumerate(MISSING_ORDERS, 1):
        print(f"\n[{i}/{len(MISSING_ORDERS)}]")

        result = await verify_order(rest_client, order_info)

        if result is True:
            results['verified'].append(order_info['order_id'])
        elif result is False:
            results['not_found'].append(order_info['order_id'])
        else:
            results['errors'].append(order_info['order_id'])

        # Rate limiting - wait between API calls
        if i < len(MISSING_ORDERS):
            await asyncio.sleep(0.5)

    # Summary
    print("\n" + "=" * 80)
    print("VERIFICATION SUMMARY")
    print("=" * 80)
    print(f"✅ Verified on exchange: {len(results['verified'])}")
    print(f"❌ Not found: {len(results['not_found'])}")
    print(f"⚠️  Errors: {len(results['errors'])}")
    print()

    if results['verified']:
        print(f"Verified order_ids ({len(results['verified'])}):")
        for oid in results['verified']:
            print(f"  ✅ {oid}")

    if results['not_found']:
        print(f"\nNot found order_ids ({len(results['not_found'])}):")
        for oid in results['not_found']:
            print(f"  ❌ {oid}")

    if results['errors']:
        print(f"\nError order_ids ({len(results['errors'])}):")
        for oid in results['errors']:
            print(f"  ⚠️  {oid}")

    print("\n" + "=" * 80)
    print("NEXT STEPS")
    print("=" * 80)

    if len(results['verified']) == len(MISSING_ORDERS):
        print("✅ All orders verified! Safe to proceed with auto-backfill:")
        print("   python -m scripts.reconcile_with_exchange --version 1 --tier 1 --auto-backfill")
    elif len(results['verified']) > 0:
        print(f"⚠️  {len(results['verified'])} orders verified, {len(results['not_found']) + len(results['errors'])} need investigation")
        print("   Review the not found / error orders before backfilling")
        print("   Consider manual backfill for verified orders only")
    else:
        print("❌ No orders verified - investigate API issues or order_id correctness")


if __name__ == "__main__":
    asyncio.run(main())
