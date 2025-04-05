import asyncio

from databases import Database
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from Config.config_manager import CentralConfig
from sighook.database_ops import DatabaseOpsManager
from sighook.database_table_models import OHLCVData


class DatabaseSessionManager:
    """Handles the creation and management of database sessions."""
    """Handles the creation and management of database sessions."""

    _instance = None

    @classmethod
    def get_instance(cls, profit_extras, logger_manager, shared_data_manager):
        if cls._instance is None:
            cls._instance = cls(profit_extras, logger_manager, shared_data_manager)
        return cls._instance

    def __init__(self, profit_extras, logger_manager, shared_data_manager):

        self.logger = logger_manager.get_logger('sighook_logger')
        self.config = CentralConfig()
        self.profit_extras = profit_extras
        self.shared_data_manager = shared_data_manager

        # Ensure that database_url is correctly set
        if not self.config.database_url:
            self.logger.error("Database URL is not configured properly.")
            raise ValueError("Database URL is not configured. Please check your configuration.")

        # Initialize the databases.Database instance
        self.database = Database(self.config.database_url, min_size=5, max_size=15)

        # Initialize the SQLAlchemy async engine
        self.engine = create_async_engine(self.config.database_url, echo=False)


        # Use the new database_url method
        #self.database = Database(self.config.database_url)
        self.database_ops = None  # Will be set later after components are initialized

    @property
    def market_data(self):
        return self.shared_data_manager.market_data

    @property
    def order_management(self):
        return self.shared_data_manager.order_management

    @property
    def ticker_cache(self):
        return self.market_data.get('ticker_cache')

    @property
    def filtered_pairs(self):
        return self.order_management.get('non_zero_balances')

    @property
    def market_cache_vol(self):
        return self.market_data.get('filtered_vol')

    @property
    def market_cache_usd(self):
        return self.market_data.get('usd_pairs_cache')

    @property
    def holdings_list(self):
        return self.market_data.get('spot_positions')

    @property
    def current_prices(self):
        return self.market_data.get('current_prices')

    async def connect(self, retries=3):
        """Establish the database connection."""
        for attempt in range(1, retries + 1):
            try:
                if not self.database.is_connected:
                    await self.database.connect()
                    self.logger.info("Database connected successfully.")
                    return
            except Exception as e:
                self.logger.warning(f"❌ Database connection attempt {attempt} failed: {e}")
                await asyncio.sleep(2)  # Wait before retrying
        raise ConnectionError("Failed to establish a database connection after retries.")

    async def initialize(self):
        """Initialize the database connection."""
        try:
            await self.connect()  # Establish database connection
            self.logger.info("DatabaseSessionManager initialized and connected to the database.")
        except Exception as e:
            self.logger.error(f"❌ Failed to initialize DatabaseSessionManager: {e}", exc_info=True)
            raise

    async def disconnect(self):
        """Close the database connection."""
        try:
            if self.database.is_connected:
                await self.database.disconnect()
                self.logger.info("Database disconnected successfully.")
        except Exception as e:
            self.logger.error(f"❌ Error while disconnecting from the database: {e}", exc_info=True)

    def get_database_ops(self, *args, **kwargs):
        if self.database_ops is None:
            self.database_ops = DatabaseOpsManager.get_instance(
                self.logger, self.profit_extras, self.config, self.database, *args, **kwargs
            )
        return self.database_ops

    async def process_data(self):
        """Delegates processing to DatabaseOpsManager within a transaction."""
        try:
            # Ensure database is connected
            if not self.database.is_connected:
                await self.connect()
                self.logger.info("Database reconnected within process_data.")

            # Execute within an explicit transaction
            async with self.database.transaction():
                await self.database_ops.process_data()
        except Exception as e:
            self.logger.error(f"❌ Failed to process data in session manager: {e}")
            raise

    async def check_ohlcv_initialized(self):
        """PART III:
        Check if OHLCV data is initialized in the database."""
        query = select(OHLCVData).limit(1)
        result = await self.database.fetch_one(query)
        return result is not None

    async def fetch_market_data(self):
        """Fetch market_data from the database."""
        try:
            if not self.database.is_connected:
                await self.connect()
            query = "SELECT data FROM shared_data WHERE data_type = 'market_data'"
            result = await self.database.fetch_one(query)
            if not result or "data" not in result:
                self.logger.warning("No data found for market_data.")
                return {}

            return result
        except Exception as e:
            self.logger.error(f"❌ Error fetching market data: {e}", exc_info=True)
            return {}

    async def fetch_order_management(self):
        """Fetch market_data from the database."""
        try:
            if not self.database.is_connected:
                await self.connect()
            query = "SELECT data FROM shared_data WHERE data_type = 'order_management'"
            result = await self.database.fetch_one(query)
            if not result or "data" not in result:
                self.logger.warning("No data found for market_data.")
                return {}

            return result
        except Exception as e:
            self.logger.error(f"❌ Error fetching order_management: {e}", exc_info=True)
            return {}
