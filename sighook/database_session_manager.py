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
                # load all historical trades into the database
                await self.database_ops.clear_new_trades(session)  # clear out the new_trades table every time we load the db
                # load the new trades into the database
                await self.database_ops.process_market_data(session, market_data['market_cache'])

                # load holdings into the holdings table
                # For example: await session.execute(...)
                await session.commit()
                # load the most recent trade for each symbol into the database symbol_updates table
                # symbols_processed = [market['symbol'] for market in market_data['market_cache']]
                # summarize the trades for each symbol and load the summary into the trade_summary table
                # await self.database_ops.update_last_trade_times(symbols_processed)
                # tasks = [self.database_ops.process_symbol(symbol, start_time) for symbol in market_data['market_cache']]
                # await asyncio.gather(*tasks)

            except Exception as e:
                await session.rollback()
                self.log_manager.sighook_logger.error(f"Failed to process data: {e}")
                raise
            finally:
                await session.close()


        return

    async def process_holding_db(self, holdings_list, current_prices):
        """PART V, PART VI: Order Execution Process data using a database session."""
        async with self.AsyncSessionLocal() as session:
            try:
                # Initialize and potentially update holdings in the database
                await self.database_ops.initialize_holding_db(session, holdings_list, current_prices)
                await session.commit()
                # Fetch the updated contents of the holdings table
                # Fetch the updated contents of the holdings table
                updated_holdings = await self.database_ops.get_updated_holdings(session)
                # Convert holdings data to a DataFrame
                df = pd.DataFrame([{
                    'Currency': holding.currency,
                    'symbol': holding.symbol,
                    'Balance': holding.balance,
                    'current_price': holding.current_price,
                    'average_cost': holding.average_cost,
                    'unrealized_profit': holding.unrealized_profit_loss,
                    'unrealized_profit_percent': holding.unrealized_pct_change
                } for holding in updated_holdings])

                return df
            except Exception as e:
                await session.rollback()
                self.log_manager.sighook_logger.error(f"Failed to process data: {e}")
                raise
            finally:
                await session.close()

    async def sell_order_fifo(self, symbol, sell_amount, sell_price, updated_holdings_list, holding, current_prices):
        """PART VI: Order Execution Process data using a database session."""
        async with self.AsyncSessionLocal() as session:
            try:
                realized_profit = await self.database_ops.process_sell_order_fifo(session, symbol, sell_amount, sell_price)
                await self.database_ops.initialize_holding_db(session, updated_holdings_list, current_prices)
                await session.commit()
                return realized_profit

            except Exception as e:
                await session.rollback()
                self.log_manager.sighook_logger.error(f"Failed to process data: {e}")
                raise
            finally:
                await session.close()
