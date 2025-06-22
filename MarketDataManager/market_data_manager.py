import asyncio
from datetime import datetime
import pandas as pd
import  time

async def market_data_watchdog(shared_data_manager, listener, logger, check_interval=60, max_age_sec=180):
    """
    Monitors shared_data_manager.market_data["bid_ask_spread"] for staleness.
    If data hasn't updated in `max_age_sec`, automatically calls listener.refresh_market_data().
    """
    last_prices = None
    last_update_time = datetime.utcnow()

    while True:
        await asyncio.sleep(check_interval)

        try:
            bid_ask_spread = shared_data_manager.market_data.get("bid_ask_spread", {})
            if not bid_ask_spread:
                logger.warning("⚠️ [Watchdog] bid_ask_spread is empty. Attempting manual refresh...")
                await listener.refresh_market_data()
                continue

            if bid_ask_spread != last_prices:
                last_prices = bid_ask_spread.copy()
                last_update_time = datetime.utcnow()
            else:
                age = (datetime.utcnow() - last_update_time).total_seconds()
                if age > max_age_sec:
                    logger.warning(
                        f"⚠️ [Watchdog] Market prices haven't updated for {int(age)} seconds. Triggering refresh..."
                    )
                    await listener.refresh_market_data()
                    last_update_time = datetime.utcnow()  # Reset after recovery attempt
        except Exception as e:
            logger.error(f"❌ [Watchdog] Unexpected error: {e}", exc_info=True)

class MarketDataUpdater:
    _instance = None
    _instance_loop = None

    @classmethod
    async def get_instance(cls, ticker_manager, logger_manager, websocket_helper=None, shared_data_manager=None):
        loop = asyncio.get_running_loop()

        if cls._instance is None or cls._instance_loop != loop:
            cls._instance = cls(ticker_manager, logger_manager, websocket_helper, shared_data_manager)
            cls._instance_loop = loop

        return cls._instance

    def __init__(self, ticker_manager, logger_manager, websocket_helper=None, shared_data_manager=None):

        """
        Initializes the MarketDataUpdater with its dependencies.

        Args:
            ticker_manager (object): Instance of the TickerManager.
            logger_manager (object): Logger instance for logging operations.
        """
        self.ticker_manager = ticker_manager
        self.logger_manager = logger_manager
        self.websocket_helper = websocket_helper
        self.shared_data_manager = shared_data_manager

        self.logger = logger_manager.loggers.get('shared_logger', None)
        self.start_time = None

    @property
    def open_orders(self):
        return self.shared_data_manager.order_management.get('order_tracker', {})

    async def update_market_data(self, start_time, open_orders=None):
        """Fetch and prepare updated market data."""
        try:
            # Fetch new data
            new_market_data, new_order_management = await self.ticker_manager.update_ticker_cache(open_orders)

            # Return the new data
            return new_market_data or {}, new_order_management or {}
        except Exception as e:
            self.logger.error(f"❌ Error updating MarketDataManager: {e}", exc_info=True)
            return {}, {}

    async def run_single_refresh_market_data(self):
        """One-time version of refresh_market_data() for manual use."""
        try:
            new_market_data, new_order_management = await self.update_market_data(time.time())
            new_order_management["passive_orders"] = await self.shared_data_manager.database_session_manager.fetch_passive_orders()
            if not new_market_data or not new_order_management:
                self.logger.error("⚠️ One-time refresh failed — no market or order data.")
                return
            # _, _, updated_order_tracker = await self.websocket_helper.refresh_open_orders()
            # if updated_order_tracker:
            #     new_order_management['order_tracker'] = updated_order_tracker
            await self.shared_data_manager.update_shared_data(new_market_data, new_order_management)
            self.logger.info("✅ One-time market data refresh complete.")
        except Exception as e:
            self.logger.error(f"❌ Error in one-time refresh: {e}", exc_info=True)

    def get_empty_keys(self,data: dict) -> list:
        empty_keys = []

        for key, value in data.items():
            if value is None:
                empty_keys.append(key)
            elif isinstance(value, (dict, list, set, tuple)) and len(value) == 0:
                empty_keys.append(key)
            elif isinstance(value, pd.DataFrame) and value.empty:
                empty_keys.append(key)
        return empty_keys
