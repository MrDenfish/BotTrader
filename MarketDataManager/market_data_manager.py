import asyncio
import  time


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
            if not new_market_data or not new_order_management:
                self.logger.error("⚠️ One-time refresh failed — no market or order data.")
                return
            _, _, updated_order_tracker = await self.websocket_helper.refresh_open_orders()
            if updated_order_tracker:
                new_order_management['order_tracker'] = updated_order_tracker
            await self.shared_data_manager.update_shared_data(new_market_data, new_order_management)
            self.logger.info("✅ One-time market data refresh complete.")
        except Exception as e:
            self.logger.error(f"❌ Error in one-time refresh: {e}", exc_info=True)


