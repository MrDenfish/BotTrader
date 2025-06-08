
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
    def get_instance(cls, logger_manager, database_session_manager, shared_utils_utility, shared_utils_precision):
        """Ensures only one instance of SharedDataManager is created."""
        if cls._instance is None:
            cls._instance = cls(logger_manager, database_session_manager,
                                shared_utils_utility,shared_utils_precision)
        return cls._instance

    def __init__(self, logger_manager, database_session_manager, shared_utils_utility, shared_utils_precision):
        if SharedDataManager._instance is not None:
            raise Exception("This class is a singleton! Use get_instance() instead.")

        self.logger = logger_manager  # 🙂

        self.shared_utils_utility = shared_utils_utility
        self.database_session_manager = database_session_manager
        self.shared_utils_precision = shared_utils_precision
        self.trade_recorder = TradeRecorder(self.database_session_manager, logger_manager,
                                            shared_utils_precision)
        self.market_data = {}
        self.order_management = {}
        self.lock = asyncio.Lock()
        self._initialized_event = Event()

    async def validate_startup_state(self, ticker_manager):
        """Ensure required shared data exists, or initialize it if missing."""
        raw_market_data = await self.database_session_manager.fetch_market_data()
        raw_order_mgmt = await self.database_session_manager.fetch_order_management()

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

        except Exception as e:
            self.logger.error(f"❌ Error decoding startup data: {e}", exc_info=True)

        # ✅ Logging the state
        if isinstance(market_data, dict):
            tc = market_data.get("ticker_cache")
            if isinstance(tc, pd.DataFrame):
                self.logger.info(f"📊 ticker_cache rows: {len(tc)}")

        # ✅ Check if startup snapshot is usable
        if not market_data or not order_mgmt:
            self.logger.warning("⚠️ No startup snapshot found. Attempting fresh data fetch...")

            start_time = time.time()
            new_market_data, new_order_mgmt = await ticker_manager.update_ticker_cache(start_time=start_time)

            await self.update_shared_data(
                new_market_data=new_market_data,
                new_order_management=new_order_mgmt
            )
            await self.save_data()

            self.logger.info("✅ Startup data initialized and saved.")
        else:
            self.logger.info("✅ Startup snapshot loaded from database.")

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
        async with self.lock:
            self.order_management = updated_order_management

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
        """Fetch order_management from the database via DatabaseSessionManager."""
        try:
            result = await self.database_session_manager.fetch_order_management()
            # Delegate the call to DatabaseSessionManager
            return json.loads(result["data"], cls=CustomJSONDecoder) if result else {}
        except Exception as e:
            if self.logger:
                self.logger.error(f"❌ Error fetching order management data: {e}", exc_info=True)
            else:
                print(f"❌ Error fetching order management data: {e}")
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

    async def update_data(self, data_type, data, conn):
        """Update shared data in the database."""
        try:
            encoded_data = json.dumps(data, cls=DecimalEncoderIn)  # Convert to JSON
            await conn.execute(
                text(f"""
                INSERT INTO shared_data (data_type, data, last_updated)
                VALUES (:data_type, :data, NOW())
                ON CONFLICT (data_type) DO UPDATE SET
                    data = :data,
                    last_updated = NOW()
                """),
                {"data_type": data_type, "data": encoded_data},
            )
        except Exception as e:
            self.logger.error(f"❌ Error updating {data_type}: {e}", exc_info=True)

    async def save_data(self):
        """Save shared data to the database using an active connection."""
        try:
            self.logger.info("Starting to save shared data...")
            async with self.database_session_manager.engine.begin() as conn:
                # Clear old data from snapshot tables
                await self.clear_old_data(conn, "market_data_snapshots")
                await self.clear_old_data(conn, "order_management_snapshots")

                # Save new snapshots
                saved_market_data = await self.save_market_data_snapshot(conn, self.market_data)
                saved_order_management = await self.save_order_management_snapshot(conn, self.order_management)

                # Update shared data
                if self.market_data:
                    await self.update_data("market_data", saved_market_data, conn)
                if self.order_management:
                    await self.update_data("order_management", saved_order_management, conn)
        except Exception as e:
            self.logger.error(f"❌ Error saving shared data: {e}", exc_info=True)

    async def save_market_data_snapshot(self, conn, market_data):
        """Save a snapshot of market data."""
        try:
            # Preprocess the market_data to ensure it's JSON-serializable
            processed_data = preprocess_market_data(market_data)
            encoded_data = json.dumps(processed_data, cls=DecimalEncoderIn)

            await conn.execute(
                text("""
                    INSERT INTO market_data_snapshots (data, snapshot_time)
                    VALUES (:data, NOW())
                """),
                {"data": encoded_data},
            )
            print("Market data snapshot saved.")
            return processed_data
        except Exception as e:
            self.logger.error(f"❌ Error saving market data snapshot: {e}", exc_info=True)

    async def save_order_management_snapshot(self, conn, order_management):
        """Save a snapshot of dismantled order management data."""
        try:
            # Dismantle the order_management structure
            dismantled = self.dismantle_order_management(order_management)

            # Serialize dismantled data to JSON
            encoded_data = json.dumps(dismantled, cls=DecimalEncoderIn)

            # Save to the database
            await conn.execute(
                text("""
                    INSERT INTO order_management_snapshots (data, snapshot_time)
                    VALUES (:data, NOW())
                """),
                {"data": encoded_data},
            )

            self.logger.debug("Order management snapshot saved successfully.")
            return dismantled
        except Exception as e:
            self.logger.error(
                f"❌ Error saving order management snapshot: {e}",
                exc_info=True
            )

    @staticmethod
    def dismantle_order_management(order_management):
        """Dismantle the order_management structure into simpler dictionaries."""
        dismantled = {
            "non_zero_balances": {},
            "order_tracker": order_management.get("order_tracker", {}),  # Copy as-is
        }

        # Process non_zero_balances
        for asset, position in order_management.get("non_zero_balances", {}).items():
            # Check if the position has a `to_dict` method
            if hasattr(position, "to_dict") and callable(position.to_dict):
                dismantled["non_zero_balances"][asset] = position.to_dict()
            elif isinstance(position, dict):
                dismantled["non_zero_balances"][asset] = position  # Already a dict
            else:
                raise TypeError(
                    f"Unsupported type for position: {type(position).__name__}. "
                    f"Expected PortfolioPosition or dict."
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

    async def clear_old_data(self, conn, table_name):
        """Clear all old data from the specified table."""
        try:
            await conn.execute(
                text(f"DELETE FROM {table_name}")
            )
            self.logger.debug(f"Cleared old data from {table_name}.")
        except Exception as e:
            self.logger.error(
                f"❌ Error clearing old data from {table_name}: {e}", exc_info=True
            )

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
                "symbol": order.get("symbol") or info.get("product_id", "").replace("-", "/") or
                order.get("product_id", "").replace("-", "/"),
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
    async def save_passive_order(self, order_id: str, symbol: str, side: str, order_data: dict):
        try:
            json_safe = self.shared_utils_utility.convert_json_safe(order_data)

            async with self.database_session_manager.async_session() as session:
                async with session.begin():
                    po = PassiveOrder(
                        order_id=order_id,
                        symbol=symbol,
                        side=side,
                        timestamp=datetime.utcnow(),
                        order_data=json_safe
                    )
                    session.add(po)

            self.logger.info(f"✅ Saved passive order: {symbol} {side} {order_id}")

        except Exception as e:
            self.logger.error(f"❌ Failed to save passive order: {e}", exc_info=True)

    async def remove_passive_order(self, order_id: str):
        async with self.database_session_manager.async_session() as session:
            async with session.begin():
                await session.execute(delete(PassiveOrder).where(PassiveOrder.order_id == order_id))

    async def load_all_passive_orders(self) -> list[tuple[str, str, dict]]:
        async with self.database_session_manager.async_session() as session:
            result = await session.execute(select(PassiveOrder))
            rows = result.scalars().all()
            return [(r.symbol, r.side, r.order_data) for r in rows]
