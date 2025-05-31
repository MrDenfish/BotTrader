class SnapshotsManager:
    _instance = None  # Singleton instance

    def __init__(self, shared_data_manager, shared_utils_precision, logger_manager):
        self.logger = logger_manager  # 🙂
        self.shared_data_manager = shared_data_manager
        self.shared_utils_precision = shared_utils_precision
        print(f"✅ SnapshotsManager initialized successfully.")

    @classmethod
    def get_instance(cls, shared_data_manager, shared_utils_precision, logger=None):
        if cls._instance is None:
            cls._instance = cls(shared_data_manager, shared_utils_precision, logger)
        return cls._instance

    async def get_market_data_snapshot(self):
        snapshot = await self.shared_data_manager.get_snapshots()
        return {
            "market_data": snapshot[0],
            "order_management": snapshot[1]
        }

    async def get_snapshots(self):
        if not self.shared_data_manager.market_data or not self.shared_data_manager.order_management:
            if self.logger:
                self.logger.warning("⚠️ Market or order data not initialized.")
            return {}, {}

        return self.shared_data_manager.market_data, self.shared_data_manager.order_management

