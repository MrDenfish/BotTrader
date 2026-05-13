
import time
import copy
import json
import asyncio
import datetime
import pandas as pd

from inspect import stack  # debugging
from asyncio import Event
from decimal import Decimal
from sqlalchemy.sql import text
from TableModels.base import Base
from sqlalchemy import select, delete
from contextlib import asynccontextmanager
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from TableModels.ohlcv_data import OHLCVData
from TableModels.shared_data import SharedData
from TableModels.passive_orders import PassiveOrder
from TableModels.active_symbols import ActiveSymbol
from datetime import datetime, date, timezone, timedelta
from SharedDataManager.trade_recorder import TradeRecorder
from SharedDataManager.leader_board import recompute_and_upsert_active_symbols, LeaderboardConfig
from Shared_Utils.logger import get_logger

# Module-level logger for static methods
_logger = get_logger('shared_data_manager', context={'component': 'shared_data_manager'})



class CustomJSONDecoder(json.JSONDecoder):
    def __init__(self, *args, **kwargs):
        super().__init__(object_hook=self.object_hook, *args, **kwargs)

    @staticmethod
    def object_hook(obj):
        # Restore Decimal
        if "__type__" in obj and obj["__type__"] == "Decimal":
            return Decimal(obj["value"])

        # Restore DataFrame
        if "__type__" in obj and obj["__type__"] == "DataFrame":
            return pd.DataFrame.from_dict(obj["data"])

        # Restore specific keys to DataFrames (e.g., ticker_cache and usd_pairs_cache)
        if "ticker_cache" in obj:
            obj["ticker_cache"] = pd.DataFrame(obj["ticker_cache"])
        if "usd_pairs_cache" in obj:
            obj["usd_pairs_cache"] = pd.DataFrame(obj["usd_pairs_cache"])

        return obj


class DecimalEncoderIn(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)  # or str(obj) if you prefer precision
        elif isinstance(obj, (datetime, date)):
            return obj.isoformat()  # Convert to ISO 8601 string
        elif isinstance(obj, ThePortfolioPosition):
            return {
                "attribute1": obj.attribute1,
                "attribute2": obj.attribute2,
                # Add other attributes as needed
            }
        return super().default(obj)


class DecimalDecoderOut(json.JSONDecoder):
    """Custom JSON decoder to restore Decimals."""
    def __init__(self, *args, **kwargs):
        super().__init__(object_hook=self.object_hook, *args, **kwargs)

    @staticmethod
    def object_hook(obj):
        # Convert any string fields that look like decimals back to Decimal
        for key, value in obj.items():
            if isinstance(value, str) and value.replace('.', '', 1).isdigit():
                try:
                    obj[key] = Decimal(value)
                except Exception as e:
                    _logger.error("Error converting value to Decimal",
                                extra={'value': str(value), 'error_type': type(e).__name__, 'error_msg': str(e)})
                    pass  # Ignore if conversion fails
        return obj


def preprocess_market_data(data):
    """Recursively process market data for JSON serialization."""
    processed_data = {}

    for key, value in data.items():
        if isinstance(value, pd.DataFrame):
            processed_data[key] = {"__type__": "DataFrame", "data": value.to_dict(orient="records")}
        elif isinstance(value, Decimal):
            processed_data[key] = {"__type__": "Decimal", "value": str(value)}
        else:
            processed_data[key] = value

    return processed_data


class ThePortfolioPosition:
    def __init__(self, asset, account_uuid, total_balance_fiat, total_balance_crypto):
        self.asset = asset
        self.account_uuid = account_uuid
        self.total_balance_fiat = total_balance_fiat
        self.total_balance_crypto = total_balance_crypto


class SharedDataManager:
    _instance = None  # Singleton instance

    @classmethod
    def get_instance(cls, logger_manager, database_session_manager, shared_utils_utility, shared_utils_precision, coinbase_api=None,):
        """Ensures only one instance of SharedDataManager is created."""
        if cls._instance is None:
            cls._instance = cls(logger_manager, database_session_manager,
                                shared_utils_utility,shared_utils_precision, coinbase_api=coinbase_api,)
        return cls._instance

    def __init__(self, logger_manager, database_session_manager, shared_utils_utility, shared_utils_precision, coinbase_api=None,):
        if SharedDataManager._instance is not None:
            raise Exception("This class is a singleton! Use get_instance() instead.")

        self.db_semaphore = asyncio.Semaphore(10)
        self.logger = get_logger('shared_data_manager', context={'component': 'shared_data_manager'})
        self.logger_manager = logger_manager

        self.shared_utils_utility = shared_utils_utility
        self.database_session_manager = database_session_manager
        self.shared_utils_precision = shared_utils_precision
        self.coinbase_api = coinbase_api
        self.trade_recorder = TradeRecorder(self.database_session_manager, logger_manager,
                                            shared_utils_precision, coinbase_api, shared_data_manager=self)
        self.market_data = {}
        self.order_management = {}

        self._last_save_ts = 0
        self._save_throttle_seconds = 2  # configurable


        self.lock = asyncio.Lock()
        self._initialized_event = Event()

        self._profitable_symbols_cache = {
            "last_update": 0.0,
            "symbols": set()
        }

    @asynccontextmanager
    async def db_session(self):
        """App-level wrapper around DatabaseSessionManager.async_session()."""
        async with self.database_session_manager.async_session() as sess:
            yield sess

    async def initialize_schema(self) -> None:
        """Create tables if they don't exist."""
        try:
            # use the engine that lives in the DB session manager
            async with self.database_session_manager.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            self.logger.info("✅ Database schema initialized.")
        except Exception as e:
            self.logger.error("❌ Failed to initialize schema: %s", e, exc_info=True)
            raise

    async def populate_initial_data(self) -> None:
        """
        Seed default rows if needed.
        Currently ensures a 'market_data' row exists in shared_data.
        """
        try:
            async with self.db_session() as session:
                async with session.begin():
                    # market_data (existing)
                    md = await session.execute(
                        select(SharedData).where(SharedData.data_type == "market_data")
                    )
                    if md.scalar_one_or_none() is None:
                        session.add(SharedData(data_type="market_data", data="{}"))
                        self.logger.info("✅ Inserted initial market_data row.")

                    # order_management (new)
                    om = await session.execute(
                        select(SharedData).where(SharedData.data_type == "order_management")
                    )
                    if om.scalar_one_or_none() is None:
                        session.add(SharedData(data_type="order_management", data="{}"))
                        self.logger.info("✅ Inserted initial order_management row.")
        except asyncio.CancelledError:
            self.logger.warning("🛑 populate_initial_data was cancelled.")
            raise
        except SQLAlchemyError as e:
            self.logger.error(f"❌ Failed to populate initial shared_data: {e}", exc_info=True)

    def inject_maintenance_callback(self):
        from TestDebugMaintenance.trade_record_maintenance import run_maintenance_if_needed
        self.trade_recorder.run_maintenance_if_needed = lambda: run_maintenance_if_needed(self, self.trade_recorder)

    async def validate_startup_state(self, market_data_manager,ticker_manager):
        """Ensure required shared data exists, or initialize it if missing."""
        raw_market_data = await self.fetch_market_data()
        raw_order_mgmt = await self.fetch_order_management()
        raw_order_mgmt["passive_orders"] = await self.fetch_passive_orders()

        market_data = None
        order_mgmt = None

        # Attempt to decode and validate JSON fields
        try:
            raw_md = raw_market_data.get("data")
            raw_om = raw_order_mgmt.get("data")

            # Check for invalid strings like 'null' or empty
            if raw_md and raw_md.strip().lower() != "null":
                market_data = json.loads(raw_md, cls=CustomJSONDecoder)

            if raw_om and raw_om.strip().lower() != "null":
                order_mgmt = json.loads(raw_om, cls=CustomJSONDecoder)
                order_mgmt["passive_orders"] = await self.fetch_passive_orders()

        except Exception as e:
            self.logger.error(f"❌ Error decoding startup data: {e}", exc_info=True)
        try:
            # ✅ Logging the state
            if isinstance(market_data, dict):
                tc = market_data.get("ticker_cache")
                if isinstance(tc, pd.DataFrame):
                    self.logger.info(f"📊 ticker_cache rows: {len(tc)}")

            # ✅ Check if startup snapshot is usable
            if not market_data or not order_mgmt or self.is_market_data_invalid(market_data):
                self.logger.warning("⚠️ No startup snapshot or incomplete market data. Attempting fresh data fetch...")

                start_time = time.time()
                result = await ticker_manager.update_ticker_cache(start_time=start_time)
                new_market_data, new_order_mgmt = result or ({}, {})
                await self.update_shared_data(
                    new_market_data=new_market_data,
                    new_order_management=new_order_mgmt
                )
                await self.save_data()
                self.logger.info("✅ Startup data initialized and saved.")
                return new_market_data, new_order_mgmt
            else:
                self.logger.info("✅ Startup snapshot loaded from database.")
                return market_data, order_mgmt

        except asyncio.CancelledError:
            self.logger.warning("🔁 Market data update was cancelled.")
            raise
        except Exception as e:
            self.logger.error(f"❌ Error updating MarketDataManager: {e}", exc_info=True)
            return {}, {}



    # ✅ Check if startup snapshot is usable or stale
    def is_market_data_invalid(self, market_data: dict) -> bool:
        if not market_data:
            return True
        if market_data.get("ticker_cache") is None or market_data["ticker_cache"].empty:
            return True
        if market_data.get("bid_ask_spread") is None or len(market_data["bid_ask_spread"]) == 0:
            return True
        if market_data.get("usd_pairs_cache") is None or market_data["usd_pairs_cache"].empty:
            return True
        if not isinstance(market_data.get("avg_quote_volume"), Decimal) or market_data["avg_quote_volume"] <= 0:
            return True
        return False

    async def initialize(self):
        """Initialize SharedDataManager."""
        try:
            # Ensure DatabaseSessionManager is connected
            await self.database_session_manager.initialize()

        except Exception as e:
            self.logger.error(f"Failed to initialize SharedDataManager: {e}", exc_info=True)
            raise

    async def wait_until_initialized(self):
        await self._initialized_event.wait()

    async def update_shared_data(self, new_market_data, new_order_management):
        try:
            async with self.lock:
                if new_market_data:
                    self.logger.debug(f"🧠 Updating market_data: Keys = {list(new_market_data.keys())}")
                    # Merge instead of replace to preserve keys like buy_sell_matrix from database
                    self.market_data.update(new_market_data)
                if new_order_management:
                    self.logger.debug(f"📦 Updating order_management: Keys = {list(new_order_management.keys())}")
                    self.order_management = new_order_management

                if not self._initialized_event.is_set():
                    self._initialized_event.set()
                    self.logger.info("✅ SharedDataManager market data initialized.")

        except Exception as e:
            self.logger.error(f"❌ Error in update_market_data : {e}", exc_info=True)

    async def initialize_shared_data(self):
        """Initialize market_data and order_management from the database."""
        async with self.lock:
            try:
                func_name = stack()[1].function
                self.logger.info("Fetching market data from database",
                               extra={'initiated_by': func_name})
                if not self.market_data:
                    self.market_data = await self.fetch_market_data()
                else:
                    self.logger.debug("Skipping fetch_market_data - already populated")

                self.logger.info("Fetching order management data from database")
                if not self.order_management:
                    self.market_data = await self.fetch_order_management()
                else:
                    self.logger.debug("Skipping fetch_order_management - already populated")
                self.order_management["passive_orders"] = await self.fetch_passive_orders()
                self.logger.info("SharedDataManager initialized successfully")

                if not self._initialized_event.is_set():
                    self._initialized_event.set()

                return self.market_data, self.order_management

            except Exception as e:
                if self.logger:
                    self.logger.error(f"❌ Failed to initialize shared data: {e}", exc_info=True)
                else:
                    print(f"❌ Failed to initialize shared data: {e}")
                self.market_data = {}
                self.order_management = {}
                return {}, {}

    async def get_order_tracker(self) -> dict:
        """Safely retrieve the current order_tracker dict from shared order_management."""
        async with self.lock:
            if not isinstance(self.order_management, dict):
                self.order_management = {}
            if "order_tracker" not in self.order_management:
                self.order_management["order_tracker"] = {}
            return self.order_management["order_tracker"]


    async def refresh_shared_data(self):
        """Refresh shared data periodically."""
        async with (self.lock):
            try:
                market_result = await self.fetch_market_data()
                self.market_data = market_result if market_result else {}
                self.market_data = self.validate_market_data(self.market_data)
                order_management_result = await self.fetch_order_management()
                self.order_management = order_management_result if order_management_result else {}
                self.order_management = self.validate_order_management_data(self.order_management)
                self.order_management["passive_orders"] = await self.fetch_passive_orders()

                self.logger.info("Shared data refreshed successfully")
                return self.market_data, self.order_management
            except Exception as e:
                self.logger.error(f"❌ Error refreshing shared data: {e}", exc_info=True)

    @staticmethod
    def validate_market_data(market_data: dict) -> dict:
        if not isinstance(market_data, dict):
            raise TypeError("market_data must be a dictionary.")

        validated = dict(market_data)  # Start with a shallow copy of all keys

        # Validate and coerce known expected keys
        if not isinstance(validated.get("ticker_cache"), pd.DataFrame):
            validated["ticker_cache"] = pd.DataFrame()

        if not isinstance(validated.get("usd_pairs_cache"), pd.DataFrame):
            validated["usd_pairs_cache"] = pd.DataFrame()

        if not isinstance(validated.get("avg_quote_volume"), Decimal):
            validated["avg_quote_volume"] = Decimal("0")

        return validated

    @staticmethod
    def validate_order_management_data(order_management_data):
        if not isinstance(order_management_data.get("non_zero_balances"), dict):
            raise TypeError("non_zero_balances is not a Dictionary.")
        if not isinstance(order_management_data.get("order_tracker"), dict):
            raise TypeError("order_tracker is not a Dictionary.")
        return order_management_data

    async def set_order_management(self, updated_order_management: dict):
        """Updates Shared State"""
        async with self.lock:
            # Merge instead of replacing the full dict
            if not isinstance(self.order_management, dict):
                self.order_management = {}
            for key, value in updated_order_management.items():
                self.order_management[key] = value
            self._order_management = self.order_management
            missing_keys = {"order_tracker", "non_zero_balances"} - set(self.order_management.keys())
            if missing_keys:
                self.logger.warning(f"⚠️ order_management missing keys: {missing_keys}")
            self.logger.debug(f"✅ set_order_management updated with {len(self.order_management.get('order_tracker', {}))} open orders")

    async def fetch_market_data(self) -> dict:
        """
        Load the 'market_data' blob from shared_data.
        Uses self.custom_json_decoder if provided; falls back to json.JSONDecoder.
        """
        try:
            async with self.db_session() as session:
                result = await session.execute(
                    select(SharedData).where(SharedData.data_type == "market_data")
                )
                row = result.scalar_one_or_none()
                if not row:
                    self.logger.warning("No market_data found.")
                    return {}

                decoder_cls = getattr(self, "custom_json_decoder", json.JSONDecoder)
                try:
                    return json.loads(row.data, cls=decoder_cls)
                except Exception as parse_err:
                    self.logger.error(
                        f"❌ Failed to decode market_data JSON: {parse_err}", exc_info=True
                    )
                    return {}
        except asyncio.CancelledError:
            self.logger.warning("🛑 fetch_market_data was cancelled.")
            raise
        except SQLAlchemyError as e:
            self.logger.error(f"❌ DB error fetching market_data: {e}", exc_info=True)
            return {}
        except Exception as e:
            self.logger.error(f"❌ Unexpected error fetching market_data: {e}", exc_info=True)
            return {}

    async def fetch_order_management(self) -> dict:
        """
        Load the 'order_management' blob from shared_data.
        Uses self.custom_json_decoder if provided; falls back to json.JSONDecoder.
        """
        try:
            async with self.db_session() as session:
                result = await session.execute(
                    select(SharedData).where(SharedData.data_type == "order_management")
                )
                row = result.scalar_one_or_none()
                if not row:
                    self.logger.warning("No data found for order_management.")
                    return {}

                decoder_cls = getattr(self, "custom_json_decoder", json.JSONDecoder)
                try:
                    return json.loads(row.data, cls=decoder_cls)
                except Exception as parse_err:
                    self.logger.error(
                        f"❌ Failed to decode order_management JSON: {parse_err}",
                        exc_info=True,
                    )
                    return {}
        except asyncio.CancelledError:
            self.logger.warning("🛑 fetch_order_management was cancelled.")
            raise
        except SQLAlchemyError as e:
            self.logger.error(f"❌ DB error fetching order_management: {e}", exc_info=True)
            return {}
        except Exception as e:
            self.logger.error(f"❌ Unexpected error fetching order_management: {e}", exc_info=True)
            return {}

    async def fetch_passive_orders(self) -> dict:
        """
        Fetch PassiveOrder rows and return them keyed by symbol.

        NOTE: if multiple passive orders exist for the same symbol, later rows will
        overwrite earlier ones. If you need ALL per-symbol, see the alternative below.
        """
        try:
            async with self.db_session() as session:
                result = await session.execute(select(PassiveOrder))
                rows = result.scalars().all()

                passive_orders: dict[str, list[dict]] = {}
                for row in rows:
                    d = {
                        "order_id": row.order_id,
                        "symbol": row.symbol,
                        "side": row.side,
                        "timestamp": row.timestamp,
                        "order_data": row.order_data,
                    }
                    if row.symbol:
                        passive_orders.setdefault(row.symbol, []).append(d)
                return passive_orders

        except asyncio.CancelledError:
            self.logger.warning("🛑 fetch_passive_orders was cancelled.")
            raise
        except SQLAlchemyError as e:
            self.logger.error("❌ DB error fetching passive_orders: %s", e, exc_info=True)
            return {}
        except Exception as e:
            self.logger.error("❌ Unexpected error fetching passive_orders: %s", e, exc_info=True)
            return {}

    async def check_ohlcv_initialized(self) -> bool:
        """
        Return True if any OHLCVData row exists.
        """
        try:
            async with self.db_session() as session:
                result = await session.execute(select(OHLCVData).limit(1))
                row = result.scalar_one_or_none()
                return row is not None

        except asyncio.CancelledError:
            self.logger.warning("🛑 check_ohlcv_initialized was cancelled.")
            raise
        except SQLAlchemyError as e:
            self.logger.error(f"❌ DB error checking OHLCV initialization: {e}", exc_info=True)
            return False
        except Exception as e:
            self.logger.error(f"❌ Unexpected error checking OHLCV initialization: {e}", exc_info=True)
            return False

    async def get_snapshots(self):
        async with self.lock:
            try:
                market_data = copy.deepcopy(self.market_data)
                order_management = copy.deepcopy(self.order_management)
                return market_data, order_management
            except Exception as e:
                self.logger.error(f"❌ Error fetching snapshots: {e}", exc_info=True)
                return {}, {}

    async def _fetch_data_in_transaction(self, data_type: str, session: AsyncSession) -> dict:
        """Fetch data from database within an existing transaction."""
        try:
            result = await session.execute(
                select(SharedData).where(SharedData.data_type == data_type)
            )
            row = result.scalar_one_or_none()
            if not row:
                return {}

            decoder_cls = getattr(self, "custom_json_decoder", json.JSONDecoder)
            return json.loads(row.data, cls=decoder_cls)
        except Exception as e:
            self.logger.error(f"❌ Error fetching {data_type} in transaction: {e}", exc_info=True)
            return {}

    async def update_data(self, data_type: str, data: dict, session: AsyncSession):
        """Update shared data in the database using a pooled session."""
        try:
            encoded_data = json.dumps(data, cls=DecimalEncoderIn)
            await session.execute(
                text("""
                    INSERT INTO shared_data (data_type, data, last_updated)
                    VALUES (:data_type, :data, NOW())
                    ON CONFLICT (data_type) DO UPDATE SET
                        data = :data,
                        last_updated = NOW()
                """),
                {"data_type": data_type, "data": encoded_data},
            )
            self.logger.debug(f"✅ Updated shared_data row for: {data_type}")
        except Exception as e:
            self.logger.error(f"❌ Error updating {data_type}: {e}", exc_info=True)

    async def save_data(self):
        """Efficiently save shared data to the database using pooled session."""
        try:
            now = time.time()
            if now - self._last_save_ts < self._save_throttle_seconds:
                self.logger.debug("⏳ Skipping save_data — throttled.")
                return
            self._last_save_ts = now

            self.logger.debug("💾 Starting save_data...")
            start_time = time.time()

            async with self.database_session_manager.async_session() as session:
                async with session.begin():  # start transaction
                    # Clear old snapshots
                    await self.clear_old_data(session, "market_data_snapshots")
                    await self.clear_old_data(session, "order_management_snapshots")

                    # Save new snapshots
                    saved_market_data = await self.save_market_data_snapshot(session, self.market_data)
                    saved_order_management = await self.save_order_management_snapshot(session, self.order_management)

                    # Remove runtime-only keys
                    saved_order_management_clean = {
                        k: v for k, v in saved_order_management.items() if k != "passive_orders"
                    }

                    # Merge with existing database data to preserve keys from other containers
                    if self.market_data:
                        db_market_data = await self._fetch_data_in_transaction("market_data", session)
                        if db_market_data:
                            # Preprocess DB data to handle DataFrames before merging
                            db_market_data_preprocessed = preprocess_market_data(db_market_data)
                            # Merge: DB data first, then overlay with our changes
                            merged_market_data = {**db_market_data_preprocessed, **saved_market_data}
                            await self.update_data("market_data", merged_market_data, session)
                        else:
                            await self.update_data("market_data", saved_market_data, session)

                    if self.order_management:
                        await self.update_data("order_management", saved_order_management_clean, session)

            # Restore passive_orders in memory (runtime only)
            if "passive_orders" in self.order_management:
                saved_order_management_clean["passive_orders"] = self.order_management["passive_orders"]

            self.order_management = saved_order_management_clean
            duration = round(time.time() - start_time, 2)
            self.logger.debug(f"✅ save_data completed in {duration}s")

        except asyncio.CancelledError:
            self.logger.warning("🛑 save_data was cancelled.")
            raise

        except Exception as e:
            self.logger.error(f"❌ Error saving shared data: {e}", exc_info=True)

    async def save_market_data_snapshot(self, session: AsyncSession, market_data: dict) -> dict:
        """Save a snapshot of market data using pooled session."""
        try:
            processed_data = preprocess_market_data(market_data)
            encoded_data = json.dumps(processed_data, cls=DecimalEncoderIn)

            await session.execute(
                text("""
                    INSERT INTO market_data_snapshots (data, snapshot_time)
                    VALUES (:data, NOW())
                """),
                {"data": encoded_data},
            )

            self.logger.debug("📊 Market data snapshot saved.")
            return processed_data
        except Exception as e:
            self.logger.error(
                f"❌ Error saving market data snapshot: {e}",
                exc_info=True
            )
            return {}

    async def save_order_management_snapshot(self, session: AsyncSession, order_management: dict) -> dict:
        """Save a snapshot of dismantled order management data using pooled session."""
        try:
            dismantled = self.dismantle_order_management(order_management)
            encoded_data = json.dumps(dismantled, cls=DecimalEncoderIn)

            await session.execute(
                text("""
                    INSERT INTO order_management_snapshots (data, snapshot_time)
                    VALUES (:data, NOW())
                """),
                {"data": encoded_data},
            )

            self.logger.debug("📝 Order management snapshot saved.")
            return dismantled
        except Exception as e:
            self.logger.error(
                f"❌ Error saving order management snapshot: {e}",
                exc_info=True
            )
            return {}

    @staticmethod
    def dismantle_order_management(order_management: dict) -> dict:
        """
        Dismantle the order_management structure into simpler dictionaries,
        excluding any runtime-only keys that should not be persisted.

        Returns:
            dict: A simplified snapshot-ready version of order_management.
        """
        # 🔒 Runtime-only keys to exclude from persistence
        runtime_keys_to_exclude = {"passive_orders", "cache_meta"}

        # Remove runtime-only keys (if they exist)
        filtered_om = {k: v for k, v in order_management.items() if k not in runtime_keys_to_exclude}

        # Initialize dismantled structure
        dismantled = {
            "non_zero_balances": {},
            "order_tracker": filtered_om.get("order_tracker", {}),
        }

        # Convert non_zero_balances to flat dicts
        for asset, position in filtered_om.get("non_zero_balances", {}).items():
            if hasattr(position, "to_dict") and callable(position.to_dict):
                dismantled["non_zero_balances"][asset] = position.to_dict()
            elif isinstance(position, dict):
                dismantled["non_zero_balances"][asset] = position
            else:
                raise TypeError(
                    f"Unsupported type for position: {type(position).__name__}. "
                    f"Expected object with to_dict() or a plain dict."
                )

        return dismantled

    @staticmethod
    def reassemble_order_management(dismantled):
        """Reassemble order_management from dismantled components."""
        reassembled = {
            "non_zero_balances": {},
            "order_tracker": dismantled.get("order_tracker", {}),
        }

        # Convert each dict in non_zero_balances back into a PortfolioPosition
        for asset, position_dict in dismantled.get("non_zero_balances", {}).items():
            if isinstance(position_dict, dict):  # Convert back to PortfolioPosition
                reassembled["non_zero_balances"][asset] = ThePortfolioPosition(
                    asset=position_dict["asset"],
                    account_uuid=position_dict["account_uuid"],
                    total_balance_fiat=position_dict["total_balance_fiat"],
                    total_balance_crypto=position_dict["total_balance_crypto"],
                )
            else:
                reassembled["non_zero_balances"][asset] = position_dict  # Already in the desired form

        return reassembled

    async def clear_old_data(self, session: AsyncSession, table_name: str):
        """Clear all old data from the specified snapshot table using a pooled session."""
        try:
            await session.execute(text(f"DELETE FROM {table_name}"))
            self.logger.debug(f"🧹 Cleared old data from {table_name}")
        except Exception as e:
            self.logger.error(f"❌ Error clearing old data from {table_name}: {e}", exc_info=True)

    def normalize_raw_order(self, order: dict) -> dict:
        """
        Normalize a raw order dict (possibly from WebSocket, REST, or snapshot) into a consistent structure.
        This allows the order tracker to use a uniform schema.

        Args:
            order (dict): Raw order object from exchange

        Returns:
            dict: Normalized order
        """
        try:
            info = order.get("info", {})
            order_config = info.get("order_configuration", {})

            # Prefer high-level fields first, fall back to nested structure
            normalized = {
                "order_id": order.get("id") or order.get("order_id"),
                "symbol": order.get("symbol") or info.get("product_id", "").replace("/", "-") or
                order.get("product_id", "").replace("/", "-"),
                "side": order.get("side") or info.get("order_side") or order.get("order_side"),
                "type": order.get("type") or info.get("order_type") or order.get("order_type"),
                "status": order.get("status") or info.get("status"),
                "filled": self.shared_utils_precision.safe_decimal(order.get("filled"), default="0")
                          or self.shared_utils_precision.safe_decimal(info.get("filled_size"), default="0")
                          or self.shared_utils_precision.safe_decimal(order.get("filled_value"), default="0"),

                "remaining": self.shared_utils_precision.safe_decimal(order.get("remaining"), default="0")
                            or self.shared_utils_precision.safe_decimal(info.get("leaves_quantity"), default="0"),


                "stopPrice": self.shared_utils_precision.safe_decimal(order.get("stopPrice"), default="0")
                          or self.shared_utils_precision.safe_decimal(info.get("stop_price"), default="0"),

                "price": self.shared_utils_precision.safe_decimal(order.get("price"), default="0")
                          or self.shared_utils_precision.safe_decimal(info.get("limit_price"), default="0"),

                "datetime": order.get("datetime") or info.get("created_time") or order.get("creation_time"),
                "trigger_status": info.get("trigger_status", "Not Active"),
                "clientOrderId": order.get("clientOrderId") or info.get("client_order_id"),
            }

            # Handle TAKE_PROFIT_STOP_LOSS bracket orders
            trigger_bracket = order_config.get("trigger_bracket_gtc")
            if trigger_bracket:
                normalized["amount"] = Decimal(trigger_bracket.get("base_size", 0))
                normalized["limit_price"] = Decimal(trigger_bracket.get("limit_price", 0))
                normalized["stop_trigger_price"] = Decimal(trigger_bracket.get("stop_trigger_price", 0))
            else:
                # fallback to top-level values if available
                normalized["amount"] = (
                        self.shared_utils_precision.safe_decimal(order.get("amount"))
                        or self.shared_utils_precision.safe_decimal(info.get("leaves_quantity"), default="0")
                )

                normalized["limit_price"] = (
                        self.shared_utils_precision.safe_decimal(order.get("limit_price"), default="0")
                        or Decimal("0")  # Optional fallback
                )


            return normalized

        except Exception as e:
            self.logger.error(f"Error normalizing raw order: {e}", exc_info=True)
            return {}
    # PASSIVE ORDER METHODS
    import json


    async def add_passive_order(self, order_id: str, symbol: str, side: str, order_data: dict):
        # Back-compat alias
        return await self.save_passive_order(order_id, symbol, side, order_data)


    async def save_passive_order(self, order_id: str, symbol: str, side: str, order_data: dict):
        try:
            # Step 1: Convert to JSON-safe types
            json_safe = self.shared_utils_utility.convert_json_safe(order_data)

            # Step 2: Serialize to string

            async with self.database_session_manager.async_session() as session:
                async with session.begin():
                    po = PassiveOrder(
                        order_id=order_id,
                        symbol=symbol,
                        side=side,
                        timestamp=datetime.now(timezone.utc),
                        order_data=json_safe    # ✅ JSON object (so ->> works in SQL)
                    )
                    session.add(po)

            self.logger.info(f"✅ Saved passive order: {symbol} {side} {order_id}")
        except asyncio.CancelledError:
            self.logger.warning("🛑 save_passive_order was cancelled.", exc_info=True)
            raise

        except Exception as e:
            self.logger.error(f"❌ Failed to save passive order: {e}", exc_info=True)

    async def remove_passive_order(self, order_id: str):
        try:
            async with self.database_session_manager.async_session() as session:
                async with session.begin():
                    await session.execute(delete(PassiveOrder).where(PassiveOrder.order_id == order_id))
        except asyncio.CancelledError:
            self.logger.warning("🛑 remove_passive_order was cancelled.", exc_info=True)
            raise

        except Exception as e:
            self.logger.error(f"❌ Failed to remove passive order: {e}", exc_info=True)

    async def load_all_passive_orders(self) -> list[tuple[str, str, dict]]:
        async with self.database_session_manager.async_session() as session:
            async with session.begin():
                result = await session.execute(select(PassiveOrder))
                rows = result.scalars().all()
                return [(r.symbol, r.side, r.order_data) for r in rows]


    async def reconcile_passive_orders(self):
        """
        Delete passive_orders from the DB that are no longer in the active order_tracker.
        """
        try:
            # Step 1: Load current active orders
            order_tracker = await self.get_order_tracker()
            if isinstance(order_tracker, list):
                passive_tracker_snapshot = {
                    o["order_id"]: o for o in order_tracker if isinstance(o, dict) and "order_id" in o
                }
            else:
                passive_tracker_snapshot = dict(order_tracker)


            active_order_ids = {order_id for order_id in passive_tracker_snapshot.keys()}

            # Step 2: Fetch all passive orders from DB
            async with self.database_session_manager.async_session() as session:
                async with session.begin():
                    result = await session.execute(select(PassiveOrder.order_id))
                    db_order_ids = {row[0] for row in result.all()}

            # Step 3: Determine stale passive orders
            stale_ids = db_order_ids - active_order_ids
            if not stale_ids:
                self.logger.info("✅ No stale passive orders found.")
                return

            # Step 4: Delete stale entries
            self.logger.info(f"🧹 Cleaning {len(stale_ids)} stale passive orders: {stale_ids}")
            async with self.database_session_manager.async_session() as session:
                async with session.begin():
                    await session.execute(
                        delete(PassiveOrder).where(PassiveOrder.order_id.in_(stale_ids))
                    )
        except asyncio.CancelledError:
            self.logger.warning("🛑 reconcile_passive_orders was cancelled.", exc_info=True)
            raise

        except Exception as e:
            self.logger.error(f"❌ Failed to reconcile passive orders: {e}", exc_info=True)

    async def fetch_profitable_symbols(
            self,
            min_trades: int = 5,
            min_pnl_usd: Decimal = Decimal("0.0"),
            lookback_days: int = 7,
            source_filter: str | None = None,
            min_quote_volume: Decimal = Decimal("750000"),
            refresh_interval: int = 60
    ) -> set[str]:
        """
        Returns symbols that are (a) recently profitable by realized SELL PnL and
        (b) meet a simple 24h volume filter.

        - Uses cache to avoid DB churn (refresh every `refresh_interval` seconds).
        - Fetches only trades in last `lookback_days`.
        - If `source_filter` is set, restricts to that strategy source.

        Dependencies:
          • self.trade_recorder.fetch_recent_trades(days=lookback_days)  -> list of trade objs
          • self.market_data["usd_pairs_cache"] with column 'volume_24h' (optional)
        """
        try:
            now = time.time()
            if now - self._profitable_symbols_cache["last_update"] < refresh_interval:
                return self._profitable_symbols_cache["symbols"]

            cutoff_time = datetime.now(timezone.utc) - timedelta(days=lookback_days)

            # Pull recent trades via your existing helper
            trades = await self.trade_recorder.fetch_recent_trades(days=lookback_days)
            if not trades:
                self._profitable_symbols_cache.update({"last_update": now, "symbols": set()})
                return set()

            # Normalize rows -> DataFrame
            df = pd.DataFrame([t.__dict__ for t in trades])

            # Robust column names (your snippet used realized_profit; DB often has pnl_usd)
            # Create a single 'realized_pnl_usd' column from what's available.
            if "realized_profit" in df.columns:
                df["realized_pnl_usd"] = df["realized_profit"]
            elif "pnl_usd" in df.columns:
                df["realized_pnl_usd"] = df["pnl_usd"]
            else:
                # If neither column present, nothing to compute
                self.logger.warning("⚠️ No realized PnL column found (expected realized_profit or pnl_usd).")
                self._profitable_symbols_cache.update({"last_update": now, "symbols": set()})
                return set()

            # Time filter (order_time assumed ISO/UTC)
            df = df[pd.to_datetime(df["order_time"], utc=True, errors="coerce") >= cutoff_time]

            if source_filter:
                if "source" in df.columns:
                    df = df[df["source"] == source_filter]
                else:
                    self.logger.warning("⚠️ source_filter provided but 'source' column missing; ignoring source filter.")

            if df.empty:
                self._profitable_symbols_cache.update({"last_update": now, "symbols": set()})
                return set()

            grouped = df.groupby("symbol").agg(
                trade_count=("order_id", "count"),
                total_profit=("realized_pnl_usd", "sum"),
            )

            filtered = grouped[
                (grouped["trade_count"] >= int(min_trades)) &
                (grouped["total_profit"] >= float(min_pnl_usd))
                ]

            profitable_symbols: set[str] = set(filtered.index)

            # Optional liquidity filter via market_data cache
            try:
                usd_pairs = self.market_data.get("usd_pairs_cache", pd.DataFrame())
                if not usd_pairs.empty and "volume_24h" in usd_pairs.columns:
                    liquid_symbols = set(
                        usd_pairs[usd_pairs["volume_24h"] >= float(min_quote_volume)]["symbol"]
                    )
                    profitable_symbols = profitable_symbols.intersection(liquid_symbols)
                else:
                    # Not a hard error—just warn once in logs if you like
                    pass
            except Exception as e:
                self.logger.warning(f"⚠️ 24h volume filter failed: {e}")

            self._profitable_symbols_cache.update({
                "last_update": now,
                "symbols": profitable_symbols
            })

            self.logger.info(
                f"✅ Profitable & Liquid symbols (cached {refresh_interval}s): {sorted(profitable_symbols)}"
            )
            return profitable_symbols

        except Exception as e:
            self.logger.warning(f"⚠️ fetch_profitable_symbols failed: {e}", exc_info=True)
            return set()


    async def recompute_leaderboard(self, lookback_hours: int = 24, min_n_24h: int = 3,
                                    win_rate_min: float = 0.35, pf_min: float = 1.30) -> None:
        """
        Recompute rolling leaderboard and upsert into active_symbols.
        Runs inside a pooled AsyncSession managed by DatabaseSessionManager.
        """
        cfg = LeaderboardConfig(
            lookback_hours=lookback_hours,
            min_n_24h=min_n_24h,
            win_rate_min=win_rate_min,
            pf_min=pf_min
        )
        async with self.database_session_manager.async_session() as session:
            async with session.begin():
                # Note: function itself commits; we start a tx for safety in your manager style
                await recompute_and_upsert_active_symbols(
                    session,
                    cfg,
                    self.shared_utils_precision.fetch_precision,
                    self.shared_utils_precision.adjust_precision
                )

    async def fetch_active_symbols(self, as_of_max_age_sec: int = 6*3600) -> set[str]:
        """
        Return symbols where eligible=true and as_of is recent.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=as_of_max_age_sec)
        async with self.database_session_manager.async_session() as session:
            async with session.begin():
                rows = await session.execute(
                    select(ActiveSymbol.symbol)
                    .where(ActiveSymbol.eligible == True)
                    .where(ActiveSymbol.as_of >= cutoff)
                )
                return {r[0] for r in rows.fetchall()}
