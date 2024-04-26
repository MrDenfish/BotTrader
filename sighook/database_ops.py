from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.future import select
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.exc import SQLAlchemyError, InvalidRequestError
from sqlalchemy.sql import func
from sqlalchemy import delete
from database_table_models import Base, Trade, SymbolUpdate, NewTrade, Holding, RealizedProfit
from datetime import datetime as dt
from dateutil import parser
from decimal import Decimal
import asyncio


class DatabaseOpsManager:
    def __init__(self, utility, exchange, log_manager, ticker_manager, portfolio_manager, app_config):

        self.log_manager = log_manager
        self.exchange = exchange
        self.utility = utility
        self.ticker_manager = ticker_manager
        self.portfolio_manager = portfolio_manager
        self.app_config = app_config
        self.ticker_cache = None
        self.market_cache = None
        self.start_time = None
        self.web_url = None

        # Set up the database engine with more flexible configuration
        self.engine = create_async_engine(
            self.app_config.database_url
        )

        self.AsyncSessionLocal = sessionmaker(bind=self.engine, class_=AsyncSession, expire_on_commit=False)

    def set_trade_parameters(self, ticker_cache, market_cache):
        self.ticker_cache = ticker_cache
        self.market_cache = market_cache

    @staticmethod
    async def add_trade(session, trade, new=False):
        """
        Add a trade to the database.

        :param session: The database session.
        :param trade: The trade data.
        :param new: Boolean flag to determine if this should be added to 'trades_new' or 'trades'.
        """
        if new:
            # Adding to 'trades_new' table
            new_trade = NewTrade(**trade)
            session.add(new_trade)
        else:
            # Adding to 'trades' table
            session.add(trade)

    def create_trade(self, trade_data, symbol):
        """PART I: Data Gathering and Database Loading. Process a single trade."""
        try:
            trade_time = trade_data['datetime']

            # Check if trade_time is a string that needs to be converted to datetime
            if isinstance(trade_time, str):
                # Truncate or properly convert the string to datetime
                if '.' in trade_time:
                    trade_time, _ = trade_time.split('.', 1)  # Splits and takes only the first part before the dot
                    trade_time += 'Z'  # Appends 'Z' if required by your formatting or logic

                trade_time = dt.fromisoformat(trade_time.rstrip('Z'))  # Converts string to datetime object

            # Ensure trade_time is a datetime object
            if not isinstance(trade_time, dt):
                raise TypeError("trade_time must be a datetime.datetime object")

            # Construct and return the Trade object
            return Trade(
                trade_time=trade_time,
                trade_id=trade_data['id'],
                order_id=trade_data.get('order'),  # Use get in case 'order' is not in trade_data
                symbol=symbol,
                price=trade_data['price'],
                amount=trade_data['amount'],
                cost=trade_data['cost'],
                side=trade_data['side'],
                fee=trade_data['fee']['cost'] if isinstance(trade_data.get('fee'), dict) else trade_data.get('fee', 0)
            )
        except Exception as e:
            self.log_manager.sighook_logger.error(f"Error creating trade: {e}", exc_info=True)
            return None

    def create_new_trade(self, trade_data):
        """
        Create a NewTrade instance from trade data.
        """
        try:
            fee = trade_data.get('fee', {})
            if isinstance(fee, dict):
                fee_cost = fee.get('cost', 0)
            else:
                fee_cost = fee  # directly use the float value
            return NewTrade(
                trade_id=trade_data['id'],
                trade_time=trade_data['datetime'],
                symbol=trade_data['symbol'],
                cost=trade_data['cost'],
                fee=fee_cost
            )
        except Exception as e:
            self.log_manager.sighook_logger.error(f"Error creating new trade: {e}", exc_info=True)
            return None

    @staticmethod
    async def get_all_trades(session):
        result = await session.execute(select(Trade))
        trades = result.scalars().all()
        return trades

    @staticmethod
    async def clear_new_trades(session: AsyncSession):
        """Clear all entries in the trades_new table."""
        await session.execute(delete(NewTrade))
        await session.commit()

    async def process_market_data(self, session: AsyncSession, market_cache):
        """PART I: Data Gathering and Database Loading. Process all symbols in the market cache concurrently using a
        single session and transaction."""
        try:
            # Retrieve last update times for all symbols
            result = await session.execute(select(SymbolUpdate))
            symbol_updates = result.scalars().all()
            last_update_by_symbol = {update.symbol: update.last_update_time for update in symbol_updates}

            # Create tasks for each symbol in the market cache
            tasks = []
            for market in market_cache:
                symbol = market['symbol']
                last_update_time = last_update_by_symbol.get(symbol, dt(2017, 12, 1))
                task = self.process_symbol(market, last_update_time)
                tasks.append(task)
            # Use asyncio.gather to process all tasks
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
        except Exception as e:
            self.log_manager.sighook_logger.error(f"Error processing market data: {e}", exc_info=True)
            await session.rollback()
            raise

    async def process_symbol(self, market, last_update_time):
        """PART I: Data Gathering and Database Loading. Process a single symbol's market data."""
        async with (self.AsyncSessionLocal() as session):  # Ensure an independent session for each symbol
            try:
                trades = await self.portfolio_manager.get_my_trades(market['symbol'], last_update_time)
                if not trades:
                    return
                else:
                    self.log_manager.sighook_logger.debug(f"Processing {len(trades)} new trades for {market['symbol']}")

                trade_objects = []
                latest_trade_time = last_update_time

                for trade in trades:
                    trade_id = trade.get('id')
                    if trade_id is None:
                        self.log_manager.sighook_logger.error("Missing trade ID")
                        continue

                    trade_datetime = dt.fromisoformat(trade['datetime']) if isinstance(trade['datetime'], str) else trade[
                        'datetime']
                    is_new_trade = trade_datetime > last_update_time
                    if is_new_trade and trade_datetime > latest_trade_time:
                        latest_trade_time = trade_datetime

                    existing_trade = await session.get(Trade, trade_id)
                    if not existing_trade:
                        trade_obj = self.create_trade(trade, market['symbol'])
                        trade_objects.append(trade_obj)
                        if is_new_trade:
                            new_trade_obj = self.create_new_trade(trade)
                            trade_objects.append(new_trade_obj)

                if trade_objects:
                    session.add_all(trade_objects)

                # Update the last trade time once per symbol, after all trades have been processed
                if latest_trade_time > last_update_time:
                    await self.update_last_trade_time(session, market['symbol'], latest_trade_time)

                await session.commit()

            except Exception as e:
                await session.rollback()
                self.log_manager.sighook_logger.error(f"Error processing symbol {market['symbol']}: {e}")
                raise
            finally:
                await session.flush()

    async def process_trade(self, session, trade, symbol, new):
        """PART I: Data Gathering and Database Loading. Process a single trade."""
        """Process a single trade.

    :param session: The database session.
    :param trade: Trade data.
    :param symbol: Symbol of the trade.
    :param new: Boolean flag if this is a new trade not previously recorded."""

        trade_id = trade.get('id')
        try:
            if trade_id is None:
                self.log_manager.error("Missing trade ID")
                return
            # Ensure there is no ongoing transaction before adding a new object
            if not session.is_active:
                self.log_manager.error("Session is not active")
                return
            existing_trade = await session.get(Trade if not new else NewTrade, trade_id)
            if not existing_trade:
                trade_data = {
                    'trade_id': trade_id,
                    'trade_time': trade['datetime'],
                    'symbol': symbol,
                    'price': trade['price'],
                    'amount': trade['amount'],
                    'cost': trade['cost'],
                    'side': trade['side'],
                    'fee': trade['fee']['cost'] if trade['fee'] else 0
                }
                trade_obj = self.create_trade(trade_data, symbol) if not new else self.create_new_trade(trade_data)

                # Add the new trade object to the session
                session.add(trade_obj)
                # Consider a flush here if needed, or manage your flushes carefully
                await session.commit()
        except InvalidRequestError as e:
            self.log_manager.error(f"Session inactive: {e}")
        except SQLAlchemyError as e:
            self.log_manager.sighook_logger.error(f"Error processing trade {trade_id}: {e}", exc_info=True)
            await session.rollback()

    async def initialize_holding_db(self, session, holdings, current_prices=None):
        """PART V: Order Execution"""
        """PART VI: Profitability Analysis and Order Generation """
        try:
            try:
                # Process each holding asynchronously
                await self.process_holdings(session, holdings, current_prices)  # holdings is alist of dictionaries
            except Exception as e:
                await session.rollback()
                self.log_manager.sighook_logger.error(f'initialize_holding_db: {e}', exc_info=True)
        finally:
            pass

    @staticmethod
    async def get_updated_holdings(session):
        """PART VI: Profitability Analysis and Order Generation  Fetch the updated contents of the holdings table using
        the provided session."""
        result = await session.execute(select(Holding))
        return result.scalars().all()

    async def process_holdings(self, session: AsyncSession, holdings, current_prices):
        """PART V: Order Execution"""
        """PART VI: Profitability Analysis and Order Generation """
        for coin in holdings:
            try:
                await self.process_single_holding(session, coin, current_prices)
                await session.commit()  # Commit after each successful processing

            except Exception as e:
                await session.rollback()  # Roll back only the current holding processing
                self.log_manager.sighook_logger.error(f'Error processing holding for {coin["symbol"]}: {e}', exc_info=True)

    async def process_single_holding(self, session: AsyncSession, coin, current_prices):
        """PART V: Order Execution"""
        """PART VI: Profitability Analysis and Order Generation """
        try:
            # Get the latest trade data for the symbol
            symbol = coin['symbol']

            aggregated_data = await self.aggregate_trade_data_for_symbol(session, symbol)

            # If aggregated_data is None, log and continue to the next coin
            if aggregated_data is None:
                return

            # Check if the holding already exists
            stmt = select(Holding).where(Holding.currency == coin['Currency'])
            result = await session.execute(stmt)
            existing_holding = result.scalars().first()
            if not existing_holding:
                # If the holding doesn't exist, create a new one
                new_holding = Holding(
                    currency=coin['Currency'],
                    symbol=coin['symbol'],
                    purchase_date=aggregated_data['earliest_trade_time'],
                    purchase_price=aggregated_data['purchase_price'],
                    current_price=current_prices.get(symbol, 0),
                    purchase_amount=aggregated_data['total_amount'],
                    balance=coin['Balance'],
                    average_cost=aggregated_data['average_cost'],
                    total_cost=aggregated_data['total_cost'],
                )
                session.add(new_holding)
            else:
                # Update existing holding
                existing_holding.purchase_date = aggregated_data['earliest_trade_time']
                existing_holding.purchase_price = aggregated_data['purchase_price']
                existing_holding.current_price = current_prices.get(symbol, 0)
                existing_holding.purchase_amount = aggregated_data['total_amount']
                existing_holding.balance = coin['Balance']
                existing_holding.average_cost = aggregated_data['average_cost']
                existing_holding.total_cost = aggregated_data['total_cost']
        except Exception as e:
            self.log_manager.sighook_logger.error(f'process_single_holding: {e}', exc_info=True)

    async def aggregate_trade_data_for_symbol(self, session: AsyncSession, symbol: str):
        """PART VI: Profitability Analysis and Order Generation """
        # Aggregate trade data for the given symbol, considering only 'buy' trades for purchase data
        try:
            stmt = (
                select(
                    func.min(Trade.trade_time).label('earliest_trade_time'),
                    func.sum(Trade.amount).label('total_amount'),
                    func.sum(Trade.cost).label('total_cost'),
                )
                .filter(Trade.symbol == symbol, Trade.side == 'buy')  # Consider only buy trades
                .group_by(Trade.symbol)
            )

            result = await session.execute(stmt)
            aggregation = result.one_or_none()

            if aggregation and aggregation.total_amount > 0:
                # Calculate weighted average price (total cost / total amount)
                weighted_average_price = aggregation.total_cost / aggregation.total_amount
                return {
                    'earliest_trade_time': aggregation.earliest_trade_time,
                    'total_amount': aggregation.total_amount,
                    'total_cost': aggregation.total_cost,
                    'average_cost': weighted_average_price,  # This now also represents the purchase price
                    'purchase_price': weighted_average_price,  # Explicitly stating it as purchase_price for clarity
                }
            else:
                # Handle the case where there are no 'buy' trades for the given symbol
                return None
        except Exception as e:
            self.log_manager.sighook_logger.error(f'aggregate_trade_data_for_symbol:  {e}', exc_info=True)
            return None

    async def process_sell_order_fifo(self, session, symbol, sell_amount, sell_price):
        """PART VI: Profitability Analysis and Order Generation """
        try:
            buy_trades = await session.execute(
                select(Trade).filter(Trade.symbol == symbol, Trade.side == 'buy').order_by(Trade.trade_time.asc()))
            buy_trades = buy_trades.scalars().all()

            remaining_sell_amount = sell_amount
            total_realized_profit = Decimal('0')

            for buy_trade in buy_trades:
                if remaining_sell_amount <= 0:
                    break  # All sold

                available_for_sale = min(buy_trade.amount, remaining_sell_amount)
                realized_profit = (sell_price - buy_trade.price) * available_for_sale - buy_trade.fee
                total_realized_profit += realized_profit

                # Update the holding quantity
                buy_trade.amount -= available_for_sale
                flag_modified(buy_trade, "amount")
                remaining_sell_amount -= available_for_sale

                # Log realized profit
                new_realized_profit = RealizedProfit(
                    currency=symbol,
                    profit_loss=realized_profit,
                    sell_amount=available_for_sale,
                    sell_price=sell_price,
                    timestamp=dt.utcnow()
                )
                session.add(new_realized_profit)

            return total_realized_profit
        except Exception as e:

            self.log_manager.sighook_logger.error(f"Error processing sell order FIFO for {symbol}: {e}", exc_info=True)
            raise  # Allow the calling function to handle the rollback

    async def get_last_update_time_for_symbol(self, session, symbol):
        """Part I & PART VI: Profitability Analysis and Order Generation """
        """Retrieve the time of each symbol's most recent closed trade from the database.

    Parameters:
    - session (AsyncSession): The SQLAlchemy asynchronous session.
    - symbol (str): The trading symbol to query the last update time for.

    Returns:
    - datetime: The last update time for the symbol, or a default datetime if not found."""
        try:
            # Query the database for the symbol's last update time
            symbol_update = await session.get(SymbolUpdate, symbol)
            # The session is already managed by the calling function

            if symbol_update:
                return symbol_update.last_update_time
            else:
                return dt(2017, 12, 1)  # Example default date
        except Exception as e:
            # Log the error and decide on the appropriate error handling strategy
            self.log_manager.sighook_logger.error(f'Error getting last update time for {symbol}: {e}', exc_info=True)
            # Depending on your error handling strategy, you might return a default value or re-raise the exception
        return dt(2017, 12, 1)  # Return a default date as a fallback

    async def set_last_update_time(self, session, symbol, last_update_trade_time):
        """ PART VI: Profitability Analysis and Order Generation Updates or sets the last update time for a given trading
        symbol in the database."""
        try:
            # Query the database for the symbol's last update time
            symbol_update = await session.get(SymbolUpdate, symbol)

            if symbol_update:
                # If a record exists, update the last update time
                symbol_update.last_update_time = last_update_trade_time
            else:
                # If no record exists, create a new one with the last update time
                new_symbol_update = SymbolUpdate(
                    symbol=symbol,
                    last_update_time=last_update_trade_time
                )
                session.add(new_symbol_update)

        except Exception as e:
            # Log the error or raise an exception as per your error handling policy
            self.log_manager.sighook_logger.error(f"Error setting last update time for {symbol}: {e}", exc_info=True)
            raise

    async def update_last_trade_time(self, session, symbol, last_trade_time):
        """Update or insert the last trade time for a given symbol."""
        try:
            # Attempt to fetch the existing record
            symbol_update = await session.get(SymbolUpdate, symbol)
            if symbol_update:
                # If a record exists, update it
                symbol_update.last_update_time = last_trade_time
            else:
                # Otherwise, create a new record
                symbol_update = SymbolUpdate(symbol=symbol, last_update_time=last_trade_time)
                session.add(symbol_update)
            await session.commit()
        except Exception as e:
            self.log_manager.sighook_logger.error(f"Error updating last trade time for {symbol}: {e}")
            await session.rollback()
            raise

    async def fetch_new_trades_for_symbols(self, session, symbols):
        """PART VI: Profitability Analysis and Order Generation """
        all_new_trades = {}
        for symbol in symbols:
            try:
                # Determine the last update time for this symbol
                last_update = await self.get_last_update_time_for_symbol(session, symbol)

                # Fetch new trades from the exchange since the last update time
                raw_trades = await self.fetch_trades(session, symbol, last_update)

                # Process raw trade data into a standardized format
                new_trades = [self.process_trade_data(trade) for trade in raw_trades]

                # Update the last update time for the symbol if new trades were fetched
                if new_trades:
                    await self.set_last_update_time(session, symbol, new_trades[-1]['trade_time'])

                all_new_trades[symbol] = new_trades

            except Exception as e:
                self.log_manager.sighook_logger.error(f'Error fetching new trades for {symbol}: {e}', exc_info=True)

                raise
        return all_new_trades


