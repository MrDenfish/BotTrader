
import copy
import json
import time
from decimal import Decimal
import datetime
from datetime import datetime, date
from inspect import stack  # debugging

import pandas as pd
from asyncio import Event
import asyncio

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import text
from SharedDataManager.trade_recorder import TradeRecorder
from webhook.webhook_validate_orders import OrderData
from TableModels.passive_orders import PassiveOrder
from sqlalchemy import select, delete

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
                    print(f"Error converting {value} to Decimal: {e}")
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
    def get_instance(cls, logger_manager, database_session_manager, shared_utils_utility, shared_utils_precision, coinbase_api=None):
        """Ensures only one instance of SharedDataManager is created."""
        if cls._instance is None:
            cls._instance = cls(logger_manager, database_session_manager,
                                shared_utils_utility,shared_utils_precision, coinbase_api=None)
        return cls._instance

    def __init__(self, logger_manager, database_session_manager, shared_utils_utility, shared_utils_precision, coinbase_api=None):
        if SharedDataManager._instance is not None:
            raise Exception("This class is a singleton! Use get_instance() instead.")

        self.logger = logger_manager  # 🙂

        self.shared_utils_utility = shared_utils_utility
        self.database_session_manager = database_session_manager
        self.shared_utils_precision = shared_utils_precision
        self.coinbase_api = coinbase_api
        self.trade_recorder = TradeRecorder(self.database_session_manager, logger_manager,
                                            shared_utils_precision, coinbase_api)
        self.market_data = {}
        self.order_management = {}

        self._last_save_ts = 0
        self._save_throttle_seconds = 2  # configurable

        self.lock = asyncio.Lock()
        self._initialized_event = Event()

    async def validate_startup_state(self, market_data_manager,ticker_manager):
        """Ensure required shared data exists, or initialize it if missing."""
        raw_market_data = await self.database_session_manager.fetch_market_data()
        raw_order_mgmt = await self.database_session_manager.fetch_order_management()
        raw_order_mgmt["passive_orders"] = await self.database_session_manager.fetch_passive_orders()

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
                order_mgmt["passive_orders"] = await self.database_session_manager.fetch_passive_orders()

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
                    self.market_data = new_market_data
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
                print(f"Fetching market data from the database...Initiated by {func_name}")
                self.market_data = await self.fetch_market_data()

                print("Fetching order management data from the database...")
                self.order_management = await self.fetch_order_management()
                self.order_management["passive_orders"] = await self.database_session_manager.fetch_passive_orders()
                print("✅ SharedDataManager:initialized successfully.")
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
                market_result = await self.database_session_manager.fetch_market_data()
                self.market_data = json.loads(market_result["data"], cls=CustomJSONDecoder) if market_result else {}
                self.market_data = self.validate_market_data(self.market_data)
                order_management_result = await self.database_session_manager.fetch_order_management()
                self.order_management = json.loads(order_management_result["data"], cls=CustomJSONDecoder) if order_management_result else {}
                self.order_management = self.validate_order_management_data(self.order_management)
                self.order_management["passive_orders"] = await self.database_session_manager.fetch_passive_orders()

                print("Shared data refreshed successfully.")
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
            self.logger.info(f"✅ set_order_management updated with {len(self.order_management.get('order_tracker', {}))} open orders")

    async def fetch_market_data(self):
        """Fetch market_data from the database via DatabaseSessionManager."""
        try:
            result = await self.database_session_manager.fetch_market_data()

            if result is None:
                return {}

            # Convert Record to native dict
            result_dict = dict(result)

            # Parse the JSON stored in the 'data' field
            raw_data = result_dict.get("data")
            market_data = json.loads(raw_data, cls=CustomJSONDecoder) if raw_data else {}

            return self.validate_market_data(market_data)

        except Exception as e:
            if self.logger:
                self.logger.error(f"❌ Error fetching market data: {e}", exc_info=True)
            else:
                print(f"❌ Error fetching market data: {e}")
            return {}

    async def fetch_order_management(self):
        """Fetch order_management and merge passive_orders."""
        try:
            result = await self.database_session_manager.fetch_order_management()
            order_management = json.loads(result["data"], cls=CustomJSONDecoder) if result else {}

            # ✅ Fetch passive orders separately
            passive_orders = await self.database_session_manager.fetch_passive_orders()
            order_management["passive_orders"] = passive_orders  # ⬅️ Merge

            return order_management
        except Exception as e:
            self.logger.error(f"❌ Error fetching order management data: {e}", exc_info=True)
            return {}

    async def get_snapshots(self):
        async with self.lock:
            try:
                market_data = copy.deepcopy(self.market_data)
                order_management = copy.deepcopy(self.order_management)
                return market_data, order_management
            except Exception as e:
                self.logger.error(f"❌ Error fetching snapshots: {e}", exc_info=True)
                return {}, {}

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

                    # Write cleaned data
                    if self.market_data:
                        await self.update_data("market_data", saved_market_data, session)
                    if self.order_management:
                        await self.update_data("order_management", saved_order_management_clean, session)

            # Restore passive_orders in memory (runtime only)
            if "passive_orders" in self.order_management:
                saved_order_management_clean["passive_orders"] = self.order_management["passive_orders"]

            self.order_management = saved_order_management_clean
            duration = round(time.time() - start_time, 2)
            self.logger.debug(f"✅ save_data completed in {duration}s")

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
                "filled": order.get("filled") or Decimal(info.get("filled_size", 0)) or Decimal(order.get('filled_value')),
                "remaining": order.get("remaining") or Decimal(info.get("leaves_quantity", 0)) or
                Decimal(order.get("leaves_quantity", 0)),
                "stopPrice": order.get("stopPrice") or Decimal(info.get("stop_price") or 0),
                "price": order.get("price") or Decimal(info.get("limit_price") or 0) or
                Decimal(order.get("limit_price") or 0),
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
                normalized["amount"] = order.get("amount") or Decimal(info.get("leaves_quantity", 0))
                normalized["limit_price"] = order.get("limit_price") or Decimal(info.get("limit_price", 0))

            return normalized

        except Exception as e:
            self.logger.error(f"Error normalizing raw order: {e}", exc_info=True)
            return {}
    # PASSIVE ORDER METHODS
    import json

    async def save_passive_order(self, order_id: str, symbol: str, side: str, order_data: dict):
        try:
            # Step 1: Convert to JSON-safe types
            json_safe = self.shared_utils_utility.convert_json_safe(order_data)

            # Step 2: Serialize to string
            json_string = json.dumps(json_safe)

            async with self.database_session_manager.async_session() as session:
                async with session.begin():
                    po = PassiveOrder(
                        order_id=order_id,
                        symbol=symbol,
                        side=side,
                        timestamp=datetime.utcnow(),
                        order_data=json_string  # ✅ Explicit string
                    )
                    session.add(po)

            self.logger.info(f"✅ Saved passive order: {symbol} {side} {order_id}")

        except Exception as e:
            self.logger.error(f"❌ Failed to save passive order: {e}", exc_info=True)

    async def remove_passive_order(self, order_id: str):
        try:
            async with self.database_session_manager.async_session() as session:
                async with session.begin():
                    await session.execute(delete(PassiveOrder).where(PassiveOrder.order_id == order_id))
        except Exception as e:
            self.logger.error(f"❌ Failed to remove passive order: {e}", exc_info=True)

    async def load_all_passive_orders(self) -> list[tuple[str, str, dict]]:
        async with self.database_session_manager.async_session() as session:
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

        except Exception as e:
            self.logger.error(f"❌ Failed to reconcile passive orders: {e}", exc_info=True)