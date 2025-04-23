import asyncio

class MarketDataUpdater:
    _instance = None

    @classmethod
    async def get_instance(cls, ticker_manager, logger_manager):
        loop = asyncio.get_running_loop()

        if cls._instance is None or cls._instance_loop != loop:
            cls._instance = cls(ticker_manager, logger_manager)
            cls._instance_loop = loop  # Store the event loop where it was created

        return cls._instance

    def __init__(self, ticker_manager, logger_manager):
        """
        Initializes the MarketDataUpdater with its dependencies.

        Args:
            ticker_manager (object): Instance of the TickerManager.
            logger_manager (object): Logger instance for logging operations.
        """
        self.ticker_manager = ticker_manager
        self.logger_manager = logger_manager  # 🙂
        if logger_manager.loggers['shared_logger'].name == 'shared_logger':  # 🙂
            self.logger = logger_manager.loggers['shared_logger']
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



