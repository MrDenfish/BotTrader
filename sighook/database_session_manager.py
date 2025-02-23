
from sqlalchemy.ext.asyncio import create_async_engine
from Shared_Utils.config_manager import CentralConfig
from sighook.database_table_models import OHLCVData
from sighook.database_ops import DatabaseOpsManager
from databases import Database
from sqlalchemy import select
import asyncio





class DatabaseSessionManager:
    """Handles the creation and management of database sessions."""
    """Handles the creation and management of database sessions."""

    _instance = None

    @classmethod
    def get_instance(cls, profit_extras, log_manager):
        if cls._instance is None:
            cls._instance = cls(profit_extras, log_manager)
        return cls._instance

    def __init__(self, profit_extras, log_manager):
        self.log_manager = log_manager
        self.config = CentralConfig()
        self.profit_extras = profit_extras
        self.start_time = self.ticker_cache = self.market_cache_vol = self.holdings_list = self.current_prices = None
        self.filtered_pairs = None


        # Ensure that database_url is correctly set
        if not self.config.database_url:
            self.log_manager.error("Database URL is not configured properly.")
            raise ValueError("Database URL is not configured. Please check your configuration.")

        # Initialize the databases.Database instance
        self.database = Database(self.config.database_url, min_size=5, max_size=15)

        # Initialize the SQLAlchemy async engine
        self.engine = create_async_engine(self.config.database_url, echo=False)


        # Use the new database_url method
        #self.database = Database(self.config.database_url)
        self.database_ops = None  # Will be set later after components are initialized

    def set_trade_parameters(self, start_time, market_data, order_management):
        self.start_time = start_time
        self.ticker_cache = market_data['ticker_cache']
        self.market_cache_vol = market_data['filtered_vol']
        self.holdings_list = market_data['spot_positions']
        self.current_prices = market_data['current_prices']
        self.filtered_pairs = order_management['non_zero_balances']

    async def connect(self, retries=3):
        """Establish the database connection."""
        for attempt in range(1, retries + 1):
            try:
                if not self.database.is_connected:
                    await self.database.connect()
                    self.log_manager.info("Database connected successfully.")
                    return
            except Exception as e:
                self.log_manager.warning(f"Database connection attempt {attempt} failed: {e}")
                await asyncio.sleep(2)  # Wait before retrying
        raise ConnectionError("Failed to establish a database connection after retries.")

    async def initialize(self):
        """Initialize the database connection."""
        try:
            await self.connect()  # Establish database connection
            self.log_manager.info("DatabaseSessionManager initialized and connected to the database.")
        except Exception as e:
            self.log_manager.error(f"❌ Failed to initialize DatabaseSessionManager: {e}", exc_info=True)
            raise

    async def disconnect(self):
        """Close the database connection."""
        try:
            if self.database.is_connected:
                await self.database.disconnect()
                self.log_manager.info("Database disconnected successfully.")
        except Exception as e:
            self.log_manager.error(f"Error while disconnecting from the database: {e}", exc_info=True)

    def get_database_ops(self, *args, **kwargs):
        if self.database_ops is None:
            self.database_ops = DatabaseOpsManager.get_instance(
                self.log_manager, self.profit_extras, self.config, self.database, *args, **kwargs
            )
        return self.database_ops

    async def process_data(self, start_time):
        """Delegates processing to DatabaseOpsManager within a transaction."""
        try:
            # Ensure database is connected
            if not self.database.is_connected:
                await self.connect()
                self.log_manager.info("Database reconnected within process_data.")

            # Execute within an explicit transaction
            async with self.database.transaction():
                await self.database_ops.process_data(start_time)
        except Exception as e:
            self.log_manager.error(f"Failed to process data in session manager: {e}")
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
            print(f"DEBUG: Retrieved Market Data: {result}")
            if not result or "data" not in result:
                self.log_manager.warning("No data found for market_data.")
                return {}

            return result
        except Exception as e:
            self.log_manager.error(f"Error fetching market data: {e}", exc_info=True)
            return {}

    async def fetch_order_management(self):
        """Fetch market_data from the database."""
        try:
            if not self.database.is_connected:
                await self.connect()
            query = "SELECT data FROM shared_data WHERE data_type = 'order_management'"
            result = await self.database.fetch_one(query)
            if not result or "data" not in result:
                self.log_manager.warning("No data found for market_data.")
                return {}

            return result
        except Exception as e:
            self.log_manager.error(f"Error fetching order_management: {e}", exc_info=True)
            return {}
