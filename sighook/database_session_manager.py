from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_scoped_session
from contextlib import asynccontextmanager
from sqlalchemy.orm import sessionmaker
import pandas as pd
import asyncio


class DatabaseSessionManager:
    """Handles the creation and management of database sessions."""
    """Handles the creation and management of database sessions."""

    def __init__(self, database_ops, csv_manager, logmanager, app_config):
        self.log_manager = logmanager
        self.csv_manager = csv_manager
        self.app_config = app_config
        self.database_ops = database_ops

        # Check if the database URL is properly set
        if not self.app_config.database_url:
            self.log_manager.sighook_logger.error("Database URL is not configured properly.")
            raise ValueError("Database URL is not configured. Please check your configuration.")

        # Setup the database engine with more flexible configuration
        self.engine = create_async_engine(
            self.app_config.database_url
        )

        self.AsyncSessionLocal = sessionmaker(bind=self.engine, class_=AsyncSession, expire_on_commit=False)

    async def process_data(self, market_data, start_time, csv_dir=None):
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
                if not csv_dir:
                    await self.database_ops.process_market_data(session, market_data['market_cache'])
                else:
                    await self.csv_manager.process_csv_data(session, csv_dir)

                # load holdings into the holdings table

                await session.commit()
                # load the most recent trade for each symbol into the database symbol_updates table
                # symbols_processed = [market['asset'] for market in market_data['market_cache']]
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
                    'asset': holding.asset,
                    'Balance': holding.balance,
                    'current_price': holding.current_price,
                    'average_cost': holding.average_cost,
                    'unrealized_profit_loss': holding.unrealized_profit_loss,
                    'unrealized_pct_change': holding.unrealized_pct_change
                } for holding in updated_holdings])

                return df
            except Exception as e:
                await session.rollback()
                self.log_manager.sighook_logger.error(f"Failed to process data: {e}")
                raise
            finally:
                await session.close()

    async def batch_update_holdings(self, holdings_to_update, current_prices):
        async with self.AsyncSessionLocal() as session:
            try:
                for holding in holdings_to_update:
                    await self.database_ops.update_single_holding(session, holding, current_prices)
                await session.commit()
            except Exception as e:
                await session.rollback()
                self.log_manager.sighook_logger.error(f"Failed to batch update holdings: {e}")
                raise
            finally:
                await session.close()

    async def process_sell_orders_fifo(self, sell_orders, holdings_list, current_prices):
        async with self.AsyncSessionLocal() as session:
            realized_profit = 0
            try:
                for (asset, sell_amount, sell_price, holding) in sell_orders:
                    profit = await self.database_ops.process_sell_order_fifo(session, asset, sell_amount, sell_price)
                    realized_profit += profit

                await self.database_ops.initialize_holding_db(session, holdings_list, current_prices)
                await session.commit()
                return realized_profit

            except Exception as e:
                await session.rollback()
                self.log_manager.sighook_logger.error(f"Failed to process sell orders: {e}")
                raise
            finally:
                await session.close()

    async def fetch_new_trades_for_symbols(self, assets):
        """PART VI: Profitability Analysis and Order Generation """
        async with self.AsyncSessionLocal() as session:
            all_new_trades = {}
            try:
                for asset in assets:

                    # Determine the last update time for this symbol
                    symbol = asset
                    asset = asset.split('/')[0]
                    last_update = await self.database_ops.get_last_update_time_for_symbol(session, asset)

                    # Fetch new trades from the exchange since the last update time
                    raw_trades = await self.database_ops.fetch_trades(session, symbol, last_update)

                    # Process raw trade data into a standardized format
                    new_trades = [self.database_ops.process_trade_data(trade) for trade in raw_trades]

                    # Update the last update time for the symbol if new trades were fetched
                    if new_trades:
                        await self.database_ops.set_last_update_time(session, asset, new_trades[-1]['trade_time'])

                    all_new_trades[asset] = new_trades
                await session.commit()
                return all_new_trades
            except Exception as e:
                await session.rollback()
                self.log_manager.sighook_logger.error(f"Failed to process data: {e}")
                raise

            finally:
                await session.close()





