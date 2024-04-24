from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_scoped_session
from contextlib import asynccontextmanager
from sqlalchemy.orm import sessionmaker
import pandas as pd
import asyncio


class DatabaseSessionManager:
    """Handles the creation and management of database sessions."""
    """Handles the creation and management of database sessions."""

    def __init__(self, database_ops, logmanager, app_config):
        self.log_manager = logmanager
        self.app_config = app_config
        self.database_ops = database_ops

        # Check if the database URL is properly set
        if not self.app_config.database_url:
            self.log_manager.error("Database URL is not configured properly.")
            raise ValueError("Database URL is not configured. Please check your configuration.")

        # Setup the database engine with more flexible configuration
        self.engine = create_async_engine(
            self.app_config.database_url
        )

        self.AsyncSessionLocal = sessionmaker(bind=self.engine, class_=AsyncSession, expire_on_commit=False)

    async def process_data(self, market_data, start_time):
        """PART I: Data Gathering and Database Loading.   The database should be initialized one time, called at the start of
                        the program to initialize the database with the latest trade data"""

        if not market_data:
            self.log_manager.sighook_logger.info("No market data available to initialize the database.")
            return

        async with self.AsyncSessionLocal() as session:
            try:
                await self.database_ops.load_db(session, market_data, start_time)
                await self.database_ops.clear_new_trades(session)  # clear out the new_trades table every time we load the db
                # For example: await session.execute(...)
                await session.commit()
                # tasks = [self.database_ops.process_symbol(symbol, start_time) for symbol in market_data['market_cache']]
                # await asyncio.gather(*tasks)
                return
            except Exception as e:
                await session.rollback()
                self.log_manager.sighook_logger.error(f"Failed to process data: {e}")
                raise
            finally:
                await session.close()

    async def process_holding_db(self, holdings_list):
        """PART V, PART VI: Order Execution Process data using a database session."""
        async with self.AsyncSessionLocal() as session:
            try:
                # Initialize and potentially update holdings in the database
                await self.database_ops.initialize_holding_db(session, holdings_list)
                await session.commit()
                # Fetch the updated contents of the holdings table
                # Fetch the updated contents of the holdings table
                updated_holdings = await self.database_ops.get_updated_holdings(session)
                # Convert holdings data to a DataFrame
                df = pd.DataFrame([{
                    'Currency': holding.currency,
                    'symbol': holding.symbol,
                    'Balance': holding.balance,
                    'average_cost': holding.average_cost
                } for holding in updated_holdings])

                return df
            except Exception as e:
                await session.rollback()
                self.log_manager.sighook_logger.error(f"Failed to process data: {e}")
                raise
            finally:
                await session.close()

    async def sell_order_fifo(self, symbol, sell_amount, sell_price, updated_holdings_df, updated_holdings_list):
        """PART VI: Order Execution Process data using a database session."""
        async with self.AsyncSessionLocal() as session:
            try:
                realized_profit = await self.database_ops.process_sell_order_fifo(session, symbol, sell_amount, sell_price)
                await self.database_ops.initialize_holding_db(session, updated_holdings_list)
                await session.commit()
                return realized_profit

            except Exception as e:
                await session.rollback()
                self.log_manager.sighook_logger.error(f"Failed to process data: {e}")
                raise
            finally:
                await session.close()
