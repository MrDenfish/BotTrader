from database_session_manager import DatabaseSessionManager
from database_table_models import Base
from sqlalchemy.ext.asyncio import create_async_engine


class DatabaseInitializer:
    """Ensures that all required database tables are present before the application starts processing any data."""
    def __init__(self, db_manager):
        self.db_manager = db_manager

    async def create_tables(self):
        """Create database tables within a transaction."""
        async with self.db_manager.engine.begin() as conn:
            try:
                await conn.run_sync(Base.metadata.create_all)
                print("Tables created successfully.")
            except Exception as e:
                print(f"Failed to create tables: {e}")
                raise  # Re-raise the exception after logging for further handling if needed.


    # async def main(self):
    #     db_manager = DatabaseSessionManager(...)
    #     await self.create_tables()

