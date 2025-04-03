class SnapshotsManager:
    _instance = None # Singleton instance

    @classmethod
    def get_instance(cls, shared_data_manager, logger_manager):
        """
        Singleton method to ensure only one instance of SnapshotsManager exists.
        """
        if cls._instance is None:
            cls._instance = cls(shared_data_manager, logger_manager)
        return cls._instance # Always return the existing instance

    def __init__(self, shared_data_manager, logger_manager):
        self.logger = logger_manager
        self.shared_data_manager = shared_data_manager
        print(f"✅ SnapshotsManager initialized successfully.")

    #
    async def get_market_data_snapshot(self):
        snapshot = await self.shared_data_manager.get_snapshots()
        return {
            "market_data": snapshot[0],
            "order_management": snapshot[1]
        }

    async def get_snapshots(self):
        snapshot = await self.get_market_data_snapshot()
        return snapshot["market_data"], snapshot["order_management"]

    # async def get_market_data_snapshot(self) -> dict:
#     """Fetch a combined snapshot of market data and order management from SharedDataManager."""
#     try:
#
#         # Delegate snapshot retrieval to SharedDataManager
#         snapshot = await self.shared_data_manager.get_snapshots()
#         return {
#             "market_data": snapshot[0],  # Extract market_data
#             "order_management": snapshot[1]  # Extract order_management
#         }
#     except Exception as e:
#         self.logger.error(f"❌ Error fetching market data snapshot: {e}", exc_info=True)
#         return {
#             "market_data": {},
#             "order_management": {}
#         }
# async def get_snapshots(self):
#     """Take a snapshot of market data and order management."""
#     try:
#         snapshot = await self.get_market_data_snapshot()
#         market_data = snapshot["market_data"]
#         order_management = snapshot["order_management"]
#
#         return market_data, order_management
#     except asyncio.TimeoutError:
#         self.logger.error("❌ Timeout while waiting for market_data_lock in get_snapshots", exc_info=True)
#         return {}, {}
#     except Exception as e:
#         self.logger.error(f"❌ Error fetching snapshots: {e}", exc_info=True)
#         return {}, {}
