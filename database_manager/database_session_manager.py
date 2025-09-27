import os
import ssl
import json
import asyncio
import asyncpg
import TableModels

from sqlalchemy import text
from sqlalchemy import select
from TableModels.base import Base
from urllib.parse import urlparse
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import SQLAlchemyError
from contextlib import asynccontextmanager
from TableModels.shared_data import SharedData
from TableModels.passive_orders import PassiveOrder
from sqlalchemy.exc import OperationalError, DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession

class DatabaseSessionManager:
    """Handles the creation and management of database sessions."""
    """Handles the creation and management of database sessions."""

    _instance = None

    @classmethod
    def get_instance(cls,config, profit_extras, logger_manager, shared_data_manager, custom_json_decoder):
        if cls._instance is None:
            cls._instance = cls(config, profit_extras, logger_manager, shared_data_manager,
                                custom_json_decoder)
        return cls._instance

    def __init__(self, config, profit_extras, logger_manager, shared_data_manager, custom_json_decoder):

        if logger_manager.name == 'shared_logger':
            self.logger = logger_manager  # 🙂
        else:
            pass
        self.config = config  # Load the configuration once
        self.profit_extras = profit_extras
        self.shared_data_manager = shared_data_manager
        self.custom_json_decoder = custom_json_decoder

        # Ensure that database_url is correctly set
        # print(f'❌ DatabaseSessionManager: database_url {self.config.database_url}')
        if not self.config.database_url:
            self.logger.error("Database URL is not configured properly.")
            raise ValueError("Database URL is not configured. Please check your configuration.")


        # Initialize the SQLAlchemy async engine
        def _should_use_ssl(db_url: str) -> bool:
            """Use SSL for RDS by default; allow env overrides."""
            if os.getenv("DB_REQUIRE_SSL", "").lower() in ("1", "true", "yes", "on"):
                return True
            if os.getenv("DB_DISABLE_SSL", "").lower() in ("1", "true", "yes", "on"):
                return False
            host = urlparse(db_url.replace("+asyncpg", "")).hostname or ""
            return host.endswith(".rds.amazonaws.com") or "amazonaws.com" in host

        # --- Build connect_args (ssl + timeouts + app_name) ---
        app_name = os.getenv("DB_APP_NAME", "bottrader")
        statement_timeout_ms = os.getenv("DB_STATEMENT_TIMEOUT_MS", "60000")  # 60s default
        connect_timeout = float(os.getenv("DB_CONNECT_TIMEOUT", "5"))  # seconds

        connect_args: dict = {
            "timeout": connect_timeout,
            "server_settings": {
                "application_name": app_name,
                # per-connection GUCs (optional but handy)
                "statement_timeout": statement_timeout_ms,  # ms
                # keepalives help server notice dead clients sooner
                "tcp_keepalives_idle": os.getenv("DB_TCP_KEEPALIVES_IDLE", "60"),
                "tcp_keepalives_interval": os.getenv("DB_TCP_KEEPALIVES_INTERVAL", "20"),
                "tcp_keepalives_count": os.getenv("DB_TCP_KEEPALIVES_COUNT", "3"),
            },
        }

        if _should_use_ssl(self.config.database_url):
            try:
                cafile = "/etc/ssl/certs/rds-global-bundle.pem"
                if os.path.exists(cafile):
                    ssl_ctx = ssl.create_default_context(cafile=cafile)
                    connect_args["ssl"] = ssl_ctx
                else:
                    connect_args["ssl"] = True
            except Exception:
                connect_args["ssl"] = True

        # --- Engine with modest pool + quick recycle ---
        self.engine = create_async_engine(
            self.config.database_url,
            echo=False,
            pool_size=int(os.getenv("DB_POOL_SIZE", "5")),
            max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "5")),
            pool_timeout=int(os.getenv("DB_POOL_TIMEOUT", "10")),
            pool_recycle=int(os.getenv("DB_POOL_RECYCLE", "300")),  # 5 min
            pool_pre_ping=True,
            future=True,
            connect_args=connect_args,
        )

        self._async_session_factory = sessionmaker(
            bind=self.engine, expire_on_commit=False, class_=AsyncSession
        )

        # --- Optional: a tiny, safe initialize() with one retry ---
        async def initialize(self) -> None:
            """Warm up the pool and verify connectivity, with a single fast retry."""
            last_exc = None
            for attempt in (1, 2):
                try:
                    async with self._async_session_factory() as s:
                        await s.execute(text("SELECT 1"))
                    return
                except (OSError, ConnectionError, OperationalError, DBAPIError, asyncpg.PostgresError) as e:
                    last_exc = e
                    # If this was a transient tunnel flap, dispose and try once more.
                    await self.engine.dispose()
                    if attempt == 1:
                        await asyncio.sleep(0.75)
                        continue
                    break
            # give original error after retries
            raise last_exc

    def _connect_args_for_ssl(self, database_url: str) -> dict:
        """
        Decide if we should enable TLS to Postgres and build asyncpg connect_args.

        Defaults:
          - If host ends with 'rds.amazonaws.com' -> enable TLS (verify with RDS CA if present)
          - Otherwise -> no TLS unless env says so

        Env overrides:
          DB_SSL / DB_SSLMODE:
            'require', 'verify-ca', 'verify-full', 'true', '1' -> enable TLS
            'disable', 'false', '0' -> disable TLS
          DB_CA_FILE:
            path to CA bundle (default: /etc/ssl/certs/rds-global-bundle.pem)
          DB_SSL_VERIFY:
            'true' (default) -> verify cert
            'false'          -> TLS but skip verification
        """
        # Strip '+asyncpg' so urlparse sees a normal scheme
        parsed = urlparse(database_url.replace('+asyncpg', ''))
        host = (parsed.hostname or '').lower()

        # Decide if we want TLS
        want_ssl = False
        mode = (os.getenv('DB_SSL') or os.getenv('DB_SSLMODE') or '').strip().lower()

        if mode in ('require', 'verify-ca', 'verify-full', 'true', '1'):
            want_ssl = True
        elif mode in ('disable', 'false', '0'):
            want_ssl = False
        elif host.endswith('rds.amazonaws.com'): # not localhost
            # Sensible default on AWS RDS
            want_ssl = True

        if not want_ssl:
            return {}

        cafile = os.getenv('DB_CA_FILE', '/etc/ssl/certs/rds-global-bundle.pem')
        verify = (os.getenv('DB_SSL_VERIFY', 'true').lower() in ('1', 'true', 'yes'))

        # Build SSL context
        if verify:
            if os.path.exists(cafile):
                ctx = ssl.create_default_context(cafile=cafile)
            else:
                # Fall back to system CAs if the RDS bundle isn't present
                ctx = ssl.create_default_context()
        else:
            # Require TLS, but skip verification (not recommended for prod)
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

        return {'ssl': ctx}

    @asynccontextmanager
    async def async_session(self) -> AsyncSession:
        async with self.shared_data_manager.db_semaphore:  # throttle concurrency
            async with self._async_session_factory() as session:
                yield session

    @property
    def async_session_factory(self):
        raise RuntimeError("Do not access session factory directly. Use `async_session()` instead.")

    async def get_active_connection_count(self) -> int:
        """Returns number of active DB connections to the current database."""
        try:
            async with self.async_session() as session:
                result = await session.execute(
                    text("SELECT COUNT(*) FROM pg_stat_activity WHERE datname = current_database();")
                )
                return int(result.scalar_one())  # 👈 ensure int
        except Exception as e:
            self.logger.warning(f"⚠️ Failed to get active DB connections: {e}", exc_info=True)
            return -1

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
            async with self.async_session() as session:
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
            async with self.async_session() as session:
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
        except asyncio.CancelledError:
            self.logger.warning("🛑 populate_initial_data was cancelled.")
            raise

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

    async def check_ohlcv_initialized(self):
        """PART III: Check if OHLCV data is initialized in the database."""
        try:
            async with self.async_session() as session:
                result = await session.execute(
                    select(TableModels.OHLCVData).limit(1)
                )
                row = result.scalar_one_or_none()
                print(f'{row}')
                return row is not None
        except asyncio.CancelledError:
            self.logger.warning("🛑 check_ohlcv_initialized was cancelled.")
            raise
        except Exception as e:
            self.logger.error(f"❌ Error checking OHLCV initialization: {e}", exc_info=True)
            return False

    async def fetch_market_data(self) -> dict:
        """Fetch market_data from the database using SQLAlchemy. Called from main.py.refresh_loop"""
        try:
            async with self.async_session() as session:
                result = await session.execute(
                    select(SharedData).where(SharedData.data_type == "market_data")
                )
                row = result.scalar_one_or_none()
                if not row:
                    self.logger.warning("No market_data found.")
                    return {}

                return json.loads(row.data, cls=self.custom_json_decoder)

        except asyncio.CancelledError:
            self.logger.warning("🛑 save_data was cancelled.")
            raise
        except Exception as e:
            self.logger.error(f"❌ Error fetching market data: {e}", exc_info=True)
            return {}

    async def fetch_order_management(self):
        """Fetch market_data from the database using SQLAlchemy. Called from main.py.refresh_loop"""
        try:
            async with self.async_session() as session:
                result = await session.execute(
                    select(SharedData).where(SharedData.data_type == "order_management")
                )
                row = result.scalar_one_or_none()  # Will return the actual JSON string

                if not row:
                    self.logger.warning("No data found for order_management.")
                    return {}

                return json.loads(row.data, cls=self.custom_json_decoder)

        except asyncio.CancelledError:
            self.logger.warning("🛑 save_data was cancelled.")
            raise
        except Exception as e:
            self.logger.error(f"❌ Error fetching order_management: {e}", exc_info=True)
            return {}

    async def fetch_passive_orders(self) -> dict:
        """Fetch passive_orders and return them keyed by symbol using SQLAlchemy."""
        try:
            async with self.async_session() as session:
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
        except asyncio.CancelledError:
            self.logger.warning("🛑 save_data was cancelled.")
            raise
        except Exception as e:
            self.logger.error(f"❌ Error fetching passive_orders: {e}", exc_info=True)
            return {}


