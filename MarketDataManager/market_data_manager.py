import asyncio

class MarketDataUpdater:
    _instance = None

    @classmethod
    def get_instance(cls, ticker_manager, log_manager):
        loop = asyncio.get_running_loop()

        if cls._instance is None or cls._instance_loop != loop:
            cls._instance = cls(ticker_manager, log_manager)
            cls._instance_loop = loop  # Store the event loop where it was created

        return cls._instance

    def __init__(self, ticker_manager, log_manager):
        """
        Initializes the MarketDataUpdater with its dependencies.

        Args:
            ticker_manager (object): Instance of the TickerManager.
            log_manager (object): Logger instance for logging operations.
        """
        self.ticker_manager = ticker_manager
        self.log_manager = log_manager
        self.start_time = None

    async def update_market_data(self, start_time, open_orders=None):
        """Fetch and prepare updated market data."""
        try:
            # Fetch new data
            new_market_data, new_order_management = await self.ticker_manager.update_ticker_cache(open_orders)

            # Return the new data
            return new_market_data or {}, new_order_management or {}
        except Exception as e:
            self.log_manager.error(f"Error updating MarketDataManager: {e}", exc_info=True)
            return {}, {}



