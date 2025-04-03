
import datetime

import aiosqlite


class DatabaseIntegrity:
    _instance = None

    @classmethod
    def get_instance(cls, app_config, db_tables, log_manager):
        if cls._instance is None:
            cls._instance = cls(app_config, db_tables, log_manager)
        return cls._instance

    def __init__(self, app_config, db_tables, log_manager):

        self.log_manager = log_manager
        self.db_tables = db_tables
        self.app_config = app_config

    @staticmethod
    async def check_database_integrity(db_path):
        try:
            # Connect to the database
            async with aiosqlite.connect(db_path) as conn:
                # Run the integrity check
                async with conn.execute("PRAGMA integrity_check;") as cursor:
                    result = await cursor.fetchone()

                # Check and return the result
                if result[0] == "ok":
                    return True
                else:
                    print(f"Database integrity check failed: {result[0]}")
                    return False
        except Exception as e:
            print(f"❌ Database error during integrity check: {e}")
            return False

    async def conditional_database_check(self, db_path):
        last_check_time = self.app_config.last_check_time  # Store last check time in config
        current_time = datetime.datetime.now()

        # Check only if 24 hours have passed since the last check
        if not last_check_time or (current_time - last_check_time).days >= 1:
            self.app_config.last_check_time = current_time  # Update last check time
            if not await self.check_database_integrity(db_path):
                self.log_manager.error(f"Database integrity check failed for {db_path}")
                return False
        return True

