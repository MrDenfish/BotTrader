import asyncio
import json
import TableModels


from sqlalchemy import text
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession

from TableModels.base import Base
from TableModels.shared_data import SharedData
from Config.config_manager import CentralConfig
from TableModels.passive_orders import PassiveOrder


class DatabaseSessionManager:
    """Handles the creation and management of database sessions."""
    """Handles the creation and management of database sessions."""

    _instance = None

    @classmethod
    def get_instance(cls, profit_extras, logger_manager, shared_data_manager, custom_json_decoder):
        if cls._instance is None:
            cls._instance = cls(profit_extras, logger_manager, shared_data_manager,
                                custom_json_decoder)
        return cls._instance

    def __init__(self, profit_extras, logger_manager, shared_data_manager, custom_json_decoder):

        if logger_manager.name == 'shared_logger':
            self.logger = logger_manager  # 🙂
        else:
            pass
        self.config = CentralConfig()
        self.profit_extras = profit_extras
        self.shared_data_manager = shared_data_manager
        self.custom_json_decoder = custom_json_decoder

        # Ensure that database_url is correctly set
        # print(f'❌ DatabaseSessionManager: database_url {self.config.database_url}')
        if not self.config.database_url:
            self.logger.error("Database URL is not configured properly.")
            raise ValueError("Database URL is not configured. Please check your configuration.")


        # Initialize the SQLAlchemy async engine
        self.engine = create_async_engine(
            self.config.database_url,
            echo=False,
            pool_size=10,  # same as databases.Database min_size
            max_overflow=20,  # allow extra temporary connections
            pool_timeout=60,  # wait before raising TimeoutError
            pool_recycle=1800,  # recycle idle connections every 30 min
            future=True
        )

        self.async_session_factory = sessionmaker(bind=self.engine, expire_on_commit=False, class_=AsyncSession)


    async def initialize_schema(self):
        try:
            async with self.engine.begin() as conn:

                await conn.run_sync(Base.metadata.create_all)
            self.logger.info("✅ Database schema initialized (tables created if they didn't exist).")
        except Exception as e:
            self.logger.error(f"❌ Failed to initialize database schema: {e}", exc_info=True)

    @property
    def market_data(self):
        return self.shared_data_manager.market_data

    @property
    def order_management(self):
        return self.shared_data_manager.order_management

    @property
    def ticker_cache(self):
        return self.shared_data_manager.market_data.get('ticker_cache')

    @property
    def filtered_pairs(self):
        return self.shared_data_manager.market_data.get('non_zero_balances')

    @property
    def market_cache_vol(self):
        return self.shared_data_manager.market_data.get('filtered_vol')

    @property
    def market_cache_usd(self):
        return self.shared_data_manager.market_data.get('usd_pairs_cache')

    @property
    def holdings_list(self):
        return self.shared_data_manager.market_data.get('spot_positions')

    @property
    def bid_ask_spread(self):
        return self.shared_data_manager.market_data.get('bid_ask_spread')


    async def initialize(self):
        try:
            # 🟡 Optional: Run a lightweight SQLAlchemy connectivity check
            async with self.async_session_factory() as session:
                await session.execute(text("SELECT 1"))
            self.logger.info("✅ SQLAlchemy database connection verified.")

            # Continue with schema/data setup
            await self.initialize_schema()
            await self.populate_initial_data()
        except Exception as e:
            self.logger.error(f"❌ Failed to initialize DatabaseSessionManager: {e}", exc_info=True)
            raise

    async def populate_initial_data(self):
        """Seed default rows if needed using SQLAlchemy ORM."""
        try:
            async with self.async_session_factory() as session:
                async with session.begin():
                    # Check if a 'market_data' row already exists
                    result = await session.execute(
                        select(SharedData).where(SharedData.data_type == "market_data")
                    )
                    row = result.scalar_one_or_none()

                    if row is None:
                        # Insert empty JSON string for market_data
                        new_entry = SharedData(
                            data_type="market_data",
                            data="{}"
                        )
                        session.add(new_entry)
                        self.logger.info("✅ Inserted initial market_data row.")

        except SQLAlchemyError as e:
            self.logger.error(f"❌ Failed to populate initial shared_data: {e}", exc_info=True)

    async def disconnect(self):
        """Close the SQLAlchemy database engine (optional for graceful shutdown)."""
        try:
            if self.engine:
                await self.engine.dispose()
                self.logger.info("✅ SQLAlchemy engine disposed successfully.")
        except Exception as e:
            self.logger.error(f"❌ Error while disposing SQLAlchemy engine: {e}", exc_info=True)

    # def get_database_ops(self, *args, **kwargs):
    #     if self.database_ops is None:
    #         self.database_ops = DatabaseOpsManager.get_instance(
    #             self.logger, self.profit_extras, self.config, self.database, *args, **kwargs
    #         )
    #     return self.database_ops

    # async def process_data(self):
    #     """Delegates processing to DatabaseOpsManager within a transaction."""
    #     try:
    #         # Ensure database is connected
    #         if not self.database.is_connected:
    #             await self.connect()
    #             self.logger.info("Database reconnected within process_data.")
    #
    #         # Execute within an explicit transaction
    #         # async with self.database.transaction():
    #         #     await self.database_ops.process_data()
    #     except Exception as e:
    #         self.logger.error(f"❌ Failed to process data in session manager: {e}")
    #         raise

    async def check_ohlcv_initialized(self):
        """PART III: Check if OHLCV data is initialized in the database."""
        try:
            async with self.async_session_factory() as session:
                result = await session.execute(
                    select(TableModels.OHLCVData).limit(1)
                )
                row = result.scalar_one_or_none()
                print(f'{row}')
                return row is not None
        except Exception as e:
            self.logger.error(f"❌ Error checking OHLCV initialization: {e}", exc_info=True)
            return False

    async def fetch_market_data(self) -> dict:
        try:
            async with self.async_session_factory() as session:
                result = await session.execute(
                    select(SharedData).where(SharedData.data_type == "market_data")
                )
                row = result.scalar_one_or_none()
                if not row:
                    self.logger.warning("No market_data found.")
                    return {}

                return json.loads(row.data, cls=self.custom_json_decoder)

        except Exception as e:
            self.logger.error(f"❌ Error fetching market data: {e}", exc_info=True)
            return {}

    async def fetch_order_management(self):
        """Fetch order_management from the database using SQLAlchemy."""
        try:
            async with self.async_session_factory() as session:
                stmt = select(SharedData.data).where(SharedData.data_type == 'order_management')
                result = await session.execute(stmt)
                row = result.scalar_one_or_none()  # Will return the actual JSON string

                if not row:
                    self.logger.warning("No data found for order_management.")
                    return {}

                return {"data": row}  # Preserve structure expected by downstream code

        except Exception as e:
            self.logger.error(f"❌ Error fetching order_management: {e}", exc_info=True)
            return {}

    async def fetch_passive_orders(self) -> dict:
        """Fetch passive_orders and return them keyed by symbol using SQLAlchemy."""
        try:
            async with self.async_session_factory() as session:
                result = await session.execute(select(PassiveOrder))
                rows = result.scalars().all()  # get the list of PassiveOrder objects

                passive_orders = {}
                for row in rows:
                    row_dict = {
                        "order_id": row.order_id,
                        "symbol": row.symbol,
                        "side": row.side,
                        "timestamp": row.timestamp,
                        "order_data": row.order_data,
                    }
                    if row.symbol:
                        passive_orders[row.symbol] = row_dict

                return passive_orders

        except Exception as e:
            self.logger.error(f"❌ Error fetching passive_orders: {e}", exc_info=True)
            return {}

    def async_session(self) -> AsyncSession:
        """Returns a new SQLAlchemy async session."""
        return self.async_session_factory()
