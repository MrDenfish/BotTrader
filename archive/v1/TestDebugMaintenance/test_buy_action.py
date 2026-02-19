import asyncio
import logging
from sighook.order_manager import OrderManager
from sighook.sender import SenderWebhook

# ✅ Simple dummy logger for testing
class DummyLogger:
    def info(self, msg): print(f"INFO: {msg}")
    def warning(self, msg): print(f"WARNING: {msg}")
    def error(self, msg, exc_info=False): print(f"ERROR: {msg}")
    def buy(self, msg): print(f"BUY: {msg}")
class DummyWebhook:
    async def send_webhook(self, session, webhook_payload):
        print(f"📤 [TEST] Webhook sent → {webhook_payload}")
        return type("DummyResponse", (), {"status": 200})()  # mimic aiohttp response
async def main():
    dummy_logger = DummyLogger()

    order_manager = OrderManager.get_instance(
        trading_strategy=None,
        ticker_manager=None,
        exchange=None,
        webhook=DummyWebhook(),
        alerts=None,
        logger_manager=dummy_logger,
        coinbase_api=None,
        ccxt_api=None,
        shared_utils_precision=None,
        shared_utils_color=None,
        shared_data_manager=None,
        web_url="http://localhost",
        signal_manager=None
    )

    await order_manager.test_handle_buy_action_sanity(order_manager)

if __name__ == "__main__":
    asyncio.run(main())

