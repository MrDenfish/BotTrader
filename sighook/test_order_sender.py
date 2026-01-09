"""
Test Order Sender for Strategy Snapshot Linkage Verification

This script sends test orders through the normal sighook->webhook flow
to verify that strategy snapshot metadata is properly preserved.

Usage:
    python sighook/test_order_sender.py --symbol BTC-USD --side buy --size 10.00 --trigger test_signal
"""

import asyncio
import argparse
import sys
import os
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Config.config_manager import Config
from sighook.alerts_msgs_webhooks import SenderWebhook
from logger_module.logger_manager import LoggerManager
import aiohttp


async def send_test_order(
    symbol: str,
    side: str,
    size: float,
    trigger: str = "test_signal",
    order_type: str = "limit"
):
    """
    Send a test order through the webhook flow.

    Args:
        symbol: Trading pair (e.g., "BTC-USD", "ETH-USD")
        side: "buy" or "sell"
        size: Order size in USD (e.g., 10.00 for $10 test orders)
        trigger: Trigger type for identification (default: "test_signal")
        order_type: Order type (default: "limit")
    """
    config = Config()
    logger_manager = LoggerManager()
    logger = logger_manager.get_logger("test_order_sender")

    webhook = SenderWebhook(config, logger)

    # Build test webhook payload
    webhook_payload = {
        "source": "TEST",
        "pair": symbol,
        "side": side.lower(),
        "action": side.lower(),
        "order_type": order_type,
        "trigger": {
            "trigger": trigger,
            "timestamp": datetime.now().isoformat()
        },
        "score": {
            "Buy Score": 3.5 if side.lower() == "buy" else 0.0,
            "Sell Score": 0.0 if side.lower() == "buy" else 3.5
        },
        "order_amount_fiat": size if side.lower() == "buy" else None,
        "base_avail_to_trade": 0.001 if side.lower() == "sell" else 0.0,
        "price": None,  # Let webhook container determine market price
        "verified": "valid",
        "quote_avail_balance": 100.0 if side.lower() == "buy" else 0.0,
        "snapshot_id": None,  # Will be set by StrategySnapshotManager in webhook container
    }

    logger.info(f"📤 Sending TEST order: {side.upper()} {symbol} @ ${size:.2f}")
    logger.info(f"   Trigger: {trigger}")
    logger.info(f"   Payload: {webhook_payload}")

    async with aiohttp.ClientSession() as session:
        try:
            response = await webhook.send_webhook(session, webhook_payload)
            if response:
                logger.info(f"✅ Webhook sent successfully: HTTP {response.status}")
                response_text = await response.text()
                logger.info(f"   Response: {response_text}")
                return response
            else:
                logger.error("❌ Webhook send failed: No response")
                return None
        except Exception as e:
            logger.error(f"❌ Error sending test order: {e}", exc_info=True)
            return None


def main():
    parser = argparse.ArgumentParser(description="Send test orders for strategy snapshot testing")
    parser.add_argument("--symbol", required=True, help="Trading pair (e.g., BTC-USD)")
    parser.add_argument("--side", required=True, choices=["buy", "sell"], help="Order side")
    parser.add_argument("--size", type=float, default=10.00, help="Order size in USD (default: 10.00)")
    parser.add_argument("--trigger", default="test_signal", help="Trigger type (default: test_signal)")
    parser.add_argument("--order-type", default="limit", help="Order type (default: limit)")

    args = parser.parse_args()

    print(f"\n{'='*60}")
    print(f"TEST ORDER SENDER - Strategy Snapshot Linkage Verification")
    print(f"{'='*60}")
    print(f"Symbol:       {args.symbol}")
    print(f"Side:         {args.side.upper()}")
    print(f"Size:         ${args.size:.2f}")
    print(f"Trigger:      {args.trigger}")
    print(f"Order Type:   {args.order_type}")
    print(f"{'='*60}\n")

    asyncio.run(send_test_order(
        symbol=args.symbol,
        side=args.side,
        size=args.size,
        trigger=args.trigger,
        order_type=args.order_type
    ))


if __name__ == "__main__":
    main()
