import asyncio
import json
from decimal import Decimal
from inspect import stack  # debugging

import pandas as pd
from sqlalchemy.sql import text


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
            return float(obj)  # Convert Decimal to float
        elif isinstance(obj, PortfolioPosition):
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

class PortfolioPosition:
    def __init__(self, asset, account_uuid, total_balance_fiat, total_balance_crypto):
        self.asset = asset
        self.account_uuid = account_uuid
        self.total_balance_fiat = total_balance_fiat
        self.total_balance_crypto = total_balance_crypto

class SharedDataManager:
    _instance = None  # Singleton instance

    @classmethod
    def get_instance(cls, logger_manager, database_session_manager):
        """Ensures only one instance of SharedDataManager is created."""
        if cls._instance is None:
            cls._instance = cls(logger_manager, database_session_manager)
        return cls._instance

    def __init__(self, logger_manager, database_session_manager):
        if SharedDataManager._instance is not None:
            raise Exception("This class is a singleton! Use get_instance() instead.")

        self.logger = logger_manager.get_logger('webhook_logger')

        self.database_session_manager = database_session_manager
        self.market_data = {}
        self.order_management = {}
        self.lock = asyncio.Lock()

    async def initialize(self):
        """Initialize SharedDataManager."""
        try:
            # Ensure DatabaseSessionManager is connected
            await self.database_session_manager.initialize()

        except Exception as e:
            self.logger.error(f"Failed to initialize SharedDataManager: {e}", exc_info=True)
            raise

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
        async with self.lock:
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
    def validate_market_data(market_data):
        if not isinstance(market_data.get("ticker_cache"), pd.DataFrame):
            raise TypeError("ticker_cache is not a DataFrame.")
        if not isinstance(market_data.get("usd_pairs_cache"), pd.DataFrame):
            raise TypeError("usd_pairs_cache is not a DataFrame.")
        if not isinstance(market_data.get("avg_quote_volume"), Decimal):
            raise TypeError("avg_quote_volume is not a Decimal.")
        return market_data

    @staticmethod
    def validate_order_management_data(order_management_data):
        if not isinstance(order_management_data.get("non_zero_balances"), dict):
            raise TypeError("non_zero_balances is not a Dictionary.")
        if not isinstance(order_management_data.get("order_tracker"), dict):
            raise TypeError("order_tracker is not a Dictionary.")
        return order_management_data

    async def fetch_market_data(self):
        """Fetch market_data from the database via DatabaseSessionManager."""
        try:
            result = await self.database_session_manager.fetch_market_data()
            market_data = json.loads(result["data"], cls=CustomJSONDecoder) if result else {}
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
                market_data = self.market_data.copy()
                order_management = self.order_management.copy()
                return market_data, order_management
            except Exception as e:
                self.logger.error(f"❌ Error fetching snapshots: {e}", exc_info=True)
                return {}, {}

    # async def get_snapshots(self):
    #     """Take a snapshot of market data and order management."""
    #     async with self.lock:
    #         # Return a copy of the data
    #         try:
    #
    #             market_data = self.market_data.copy()
    #             order_management = self.order_management.copy()
    #             return market_data, order_management
    #         except Exception as e:
    #             self.logger.error(f"❌ Error fetching snapshots: {e}", exc_info=True)
    #             return {}, {}



    async def update_market_data(self, new_market_data, new_order_management):
        """Update the shared market data and order management."""
        async with self.lock:
            if new_market_data:
                self.market_data = new_market_data  # Replace instead of update
            if new_order_management:
                self.order_management = new_order_management  # Replace instead of update

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
                reassembled["non_zero_balances"][asset] = PortfolioPosition(
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











