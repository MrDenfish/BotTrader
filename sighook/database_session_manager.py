from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import sessionmaker
from database_table_models import Trade
from database_table_models import OHLCVData
from database_ops import DatabaseOpsManager
from database_ops_holdings import DatabaseOpsHoldingsManager
from Utils.logging_manager import LoggerManager
import pandas as pd
import logging


class DatabaseSessionManager:
    """Handles the creation and management of database sessions."""
    """Handles the creation and management of database sessions."""

    def __init__(self, csv_manager, log_manager, profit_extras, app_config):
        self.log_manager = log_manager
        self.csv_manager = csv_manager
        self.app_config = app_config
        self.profit_extras = profit_extras

        # Ensure that database_url is correctly set
        if not self.app_config.database_url:
            self.log_manager.error("Database URL is not configured properly.")
            raise ValueError("Database URL is not configured. Please check your configuration.")

        # Use the new database_url method
        self.engine = create_async_engine(
            self.app_config.database_url,
            connect_args={"timeout": 30, "check_same_thread": False},
            echo=False  # For debugging purposes, consider turning this on if needed
        )

        self.AsyncSessionLocal = sessionmaker(bind=self.engine, class_=AsyncSession, expire_on_commit=False)
        # Initialize DatabaseOpsManager instance here or later when all components are ready
        self.database_ops = None  # Will be set later after components are initialized
        self.database_ops_holdings = None

    def get_database_ops(self, *args, **kwargs):
        """Returns the DatabaseOpsManager instance, creating it if necessary."""
        if self.database_ops is None:
            self.database_ops = DatabaseOpsManager(
                self.log_manager, self.csv_manager, self.profit_extras, self.app_config, self.engine, self.AsyncSessionLocal, *args, **kwargs
            )
        return self.database_ops

    def get_database_ops_holdings(self, *args, **kwargs):
        """Returns the DatabaseOpsHoldingsManager instance, creating it if necessary."""
        if self.database_ops_holdings is None:
            self.database_ops_holdings = DatabaseOpsHoldingsManager(
                self.log_manager, self.AsyncSessionLocal, *args, **kwargs
            )
        return self.database_ops_holdings

    async def process_data(self, market_data, holdings_list, holdings_df, current_prices, csv_dir=None):
        """Handles session management and delegates processing to DatabaseOpsManager."""
        async with self.AsyncSessionLocal() as session:
            try:
                await self.database_ops.process_data(session, market_data, holdings_list, holdings_df, current_prices,
                                                     csv_dir)
            except Exception as e:
                self.log_manager.error(f"Failed to process data in session manager: {e}")
                await session.rollback()
                raise
            finally:
                await session.close()

    async def check_ohlcv_initialized(self):
        """PART III - Order Cancellation and Data Collection. Check if the OHLCV data is initialized in the database."""
        async with self.AsyncSessionLocal() as session:
            result = await session.execute(select(OHLCVData).limit(1))
            return result.first() is not None

    async def create_performance_snapshot(self):
        async with self.AsyncSessionLocal() as session:
            try:
                profit_data = await self.profit_extras.performance_snapshot(session)
                await session.commit()
                return profit_data
            except Exception as e:
                await session.rollback()
                self.log_manager.error(f"Failed to create performance snapshot: {e}")
                raise
            finally:
                await session.close()

    async def process_holding_db(self, holding_list, holdings_df, current_prices, open_orders):
        """PART V, PART VI: Order Execution Process data using a database session."""
        async with self.AsyncSessionLocal() as session:
            try:
                # Initialize and potentially update holdings in the database
                await self.database_ops_holdings.clear_holdings(session)
                await self.database_ops_holdings.initialize_holding_db(session, holding_list, holdings_df, current_prices,
                                                                       open_orders=open_orders)
                await session.commit()
                # Fetch the updated contents of the holdings table
                updated_holdings = await self.database_ops_holdings.get_updated_holdings(session)
                trailing_stop_orders = open_orders[open_orders['trigger_status'] == 'STOP_PENDING']

                # Convert holdings data to a DataFrame
                df = pd.DataFrame([{
                    'symbol': holding.asset + '/' + holding.currency,
                    'quote': holding.currency,
                    'asset': holding.asset,
                    'balance': holding.balance,
                    'amount': holding.purchase_amount,
                    'current_price': holding.current_price,
                    'weighted_average_price': holding.weighted_average_price,
                    'initial_investment': holding.initial_investment, # this is wrong
                    'unrealized_profit_loss': holding.unrealized_profit_loss,
                    'unrealized_pct_change': holding.unrealized_pct_change,
                    'trailing_stop': trailing_stop_orders
                } for holding in updated_holdings])

                return df
            except Exception as e:
                await session.rollback()
                self.log_manager.error(f"Failed to process data: {e}", exc_info=True)
                raise
            finally:
                await session.close()

    async def batch_update_holdings(self, holdings_to_update, current_prices, open_orders):
        """PART VI: Profitability Analysis and Order Generation"""
        async with self.AsyncSessionLocal() as session:
            try:
                await self.database_ops.clear_new_trades(session)
                for holding in holdings_to_update:
                    await self.database_ops_holdings.update_single_holding(session, holding, current_prices, open_orders)
                await session.commit()
            except Exception as e:
                await session.rollback()
                self.log_manager.error(f"Failed to batch update holdings: {e}")
                raise
            finally:
                await session.close()

    async def process_sell_orders_fifo(self, market_cache, sell_orders, holdings_list, holdings_df, current_prices):
        """PART VI: Profitability Analysis and Order Generation"""
        async with self.AsyncSessionLocal() as session:
            await self.log_trade_amounts(session, "Before process_market_data")  # Log before calling the function
            await self.database_ops.process_market_data(session, market_cache)  # update trades
            await self.log_trade_amounts(session, "After process_market_data")  # Log after calling the function

            realized_profit = 0
            try:
                for (asset, sell_amount, sell_price, holding) in sell_orders:
                    profit = await self.database_ops.process_sell_fifo(session, asset, sell_amount, sell_price)
                    realized_profit += profit
                    await session.commit()
                    break  # debug
                await self.database_ops_holdings.initialize_holding_db(session, holdings_list, holdings_df, current_prices,
                                                              sell_orders=sell_orders, open_orders=None)
                await session.commit()
                return realized_profit
            except Exception as e:
                await session.rollback()
                self.log_manager.error(f"Failed to process sell orders: {e}")
                raise
            finally:
                await session.close()

    async def log_trade_amounts(self, session, log_point):
        """PART VI: Profitability Analysis and Order Generation"""
        try:
            trades = await session.execute(select(Trade))
            trades = trades.scalars().all()
            for trade in trades:
                if trade.asset == 'BTC' or trade.asset == 'MNDE':
                    if trade.amount == 0:
                        self.log_manager.debug(f"{log_point} - Asset: {trade.asset} Trade ID:"
                                                              f" {trade.trade_id}, Amount: {trade.amount}")

        except Exception as e:
            self.log_manager.error(f"Error logging trade amounts at {log_point}: {e}")

    async def fetch_new_trades_for_symbols(self, symbols):
        """PART VI: Profitability Analysis and Order Generation """
        async with self.AsyncSessionLocal() as session:
            all_new_trades = {}
            try:
                for symbol in symbols:
                    # Determine the last update time for this symbol
                    asset = symbol.split('/')[0]
                    last_update = await self.database_ops.get_last_update_time_for_symbol(session, symbol)

                    # Fetch new trades from the exchange since the last update time
                    raw_trades = await self.database_ops.fetch_trades(session, symbol, last_update)

                    # Process raw trade data into a standardized format, filtering out None values
                    new_trades = [self.database_ops.process_trade_data(trade) for trade in raw_trades if trade is not None]

                    # Update the last update time for the symbol if new trades were fetched
                    if new_trades:
                        await self.database_ops.set_last_update_time(session, symbol, new_trades[-1]['trade_time'])

                    all_new_trades[asset] = new_trades
                await session.commit()
                return all_new_trades
            except Exception as e:
                self.log_manager.error(f"Failed to process data: {e}")
                await session.rollback()
                raise

            finally:
                # Revert SQL logging to default level
                LoggerManager.setup_sqlalchemy_logging(logging.WARNING)
                await session.close()
