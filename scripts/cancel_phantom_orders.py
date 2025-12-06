#!/usr/bin/env python3
"""
Cancel phantom orders that are blocking position_monitor exits.
"""
import asyncio
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from Config.config_manager import CentralConfig
from ExchangeManager.coinbase_api import CoinBaseAPI

# Phantom order IDs found in order_tracker
PHANTOM_ORDER_IDS = [
    "2ba528e5-ab81-4d07-958a-b76e48b99945",  # BCH-USD
    "5a18d962-f819-4446-a828-214ce2cd32ff",  # UNI-USD
    "60103fed-6d9f-4eba-b827-015dacf185f7",  # SOL-USD
    "f8d4db65-fdab-442c-9a76-032db070ad69",  # TAO-USD
    "fc58cc8b-481b-42e8-9d6e-06dc46006fbc",  # AVAX-USD
]

async def main():
    print("🔍 Checking for phantom orders on Coinbase...")

    # Initialize config and API
    config = CentralConfig()
    api = CoinBaseAPI(config)

    for order_id in PHANTOM_ORDER_IDS:
        print(f"\n📋 Checking order {order_id}...")

        try:
            # Try to get order details
            order = await api.get_order(order_id)

            if order:
                status = order.get('status', 'UNKNOWN')
                symbol = order.get('product_id', 'UNKNOWN')
                side = order.get('side', 'UNKNOWN')

                print(f"   ✓ Order exists: {symbol} {side} - Status: {status}")

                if status in ['OPEN', 'PENDING']:
                    print(f"   🚨 Order is {status} - attempting to cancel...")

                    cancel_result = await api.cancel_orders([order_id])

                    if cancel_result and cancel_result[0].get('success'):
                        print(f"   ✅ Successfully cancelled order {order_id}")
                    else:
                        reason = cancel_result[0].get('failure_reason', 'Unknown') if cancel_result else 'No response'
                        print(f"   ❌ Failed to cancel: {reason}")
                else:
                    print(f"   ℹ️  Order status is {status} (not OPEN), no cancel needed")
            else:
                print(f"   ℹ️  Order not found on exchange (likely already filled/cancelled)")

        except Exception as e:
            print(f"   ⚠️  Error checking order: {e}")

    print("\n✅ Phantom order check complete!")

if __name__ == "__main__":
    asyncio.run(main())
