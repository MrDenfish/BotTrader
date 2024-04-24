from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy import Column, Integer, String, Numeric, DateTime
from sqlalchemy.orm import sessionmaker
from sqlalchemy.sql import func
from contextlib import asynccontextmanager
from sqlalchemy.future import select
from sqlalchemy import delete
import asyncio
from decimal import Decimal
from datetime import datetime
from datetime import timezone
import datetime
from sqlalchemy.future import select
from dateutil import parser
import traceback
import pandas as pd
import os


Base = declarative_base()


class Trade(Base):
    """All closed trades are stored in this table."""
    __tablename__ = 'trades'

    trade_id = Column(String, primary_key=True)  # id {str}
    order_id = Column(String, nullable=True)  # order {str}
    trade_time = Column(DateTime)  # datetime {str}
    symbol = Column(String, nullable=True)  # symbol {str}
    price = Column(Numeric)  # price {float}
    amount = Column(Numeric)  # amount {float}
    cost = Column(Numeric)  # cost {float}
    side = Column(String, nullable=True)  # side {str}
    fee = Column(Numeric, nullable=True)  # fee {float}


class NewTrade(Base):
    """All recently
    closed trades since last update."""
    __tablename__ = 'trades_new'

    trade_id = Column(String, primary_key=True)
    trade_time = Column(DateTime)
    symbol = Column(String, nullable=True)
    cost = Column(Numeric)
    fee = Column(Numeric, nullable=True)


class TradeSummary(Base):
    __tablename__ = 'trade_summary'
    id = Column(Integer, primary_key=True)
    symbol = Column(String)
    total_trades = Column(Integer)
    total_cost = Column(Numeric)
    total_fees = Column(Numeric)
    average_cost_without_fees = Column(Numeric)
    average_cost_with_fees = Column(Numeric)


class Holding(Base):
    """All current holdings are stored in this table."""
    __tablename__ = 'holdings'

    currency = Column(String, primary_key=True)
    symbol = Column(String, nullable=False, index=True)
    first_purchase_date = Column(DateTime)  # Date of the first purchase
    purchase_date = Column(DateTime, default=func.now())  # Date of purchase
    purchase_price = Column(Numeric)  # Price at which the cryptocurrency was purchased
    current_price = Column(Numeric)  # Current price of the cryptocurrency
    purchase_amount = Column(Numeric)  # Quantity of the cryptocurrency purchased
    balance = Column(Numeric)  # Remaining quantity of the cryptocurrency
    average_cost = Column(Numeric)  # Average cost basis of the remaining quantity
    total_cost = Column(Numeric)  # Total cost of the current holdings
    unrealized_profit_loss = Column(Numeric)  # Unrealized profit/loss of the current holdings
    unrealized_pct_change = Column(Numeric)  # Unrealized profit/loss percentage of the current holdings

    @classmethod
    def create_from_trade(cls, trade):
        """Create a new Holding instance from a trade."""
        currency = trade.symbol.split('/')[0]
        return cls(
            currency=currency,
            ticker=trade.symbol,
            purchase_date=trade.trade_time,
            purchase_price=trade.price,
            current_price=trade.price,  # Initial current price is the purchase price
            purchase_amount=trade.amount,
            balance=trade.amount,
            average_cost=trade.price,
            total_cost=trade.cost,
            unrealized_profit_loss=0,  # Initial unrealized profit/loss is 0
            unrealized_pct_change=0  # Initial unrealized percentage change is 0
        )

    @classmethod
    def create_from_aggregated_data(cls, currency, aggregated_data, balance):
        """
        Create a new Holding instance from aggregated trade data.

        Parameters:
        - currency: The currency symbol of the holding.
        - aggregated_data: A dictionary containing aggregated trade data,
          including 'earliest_trade_time', 'total_amount', 'total_cost',
          'average_cost', and 'purchase_price'.
        - balance: The current balance of the cryptocurrency in the holding.

        Returns:
        - An instance of Holding initialized with the provided data.
        """
        return cls(
            currency=currency,
            first_purchase_date=aggregated_data['earliest_trade_time'],
            purchase_date=aggregated_data['earliest_trade_time'],  # or use datetime.utcnow() if more appropriate
            purchase_price=aggregated_data['purchase_price'],
            current_price=aggregated_data['purchase_price'],
            # Assuming current price is the purchase price; adjust as needed
            purchase_amount=aggregated_data['total_amount'],
            balance=balance,
            average_cost=aggregated_data['average_cost'],
            total_cost=aggregated_data['total_cost'],
            unrealized_profit_loss=0,  # Initialize as 0; adjust based on your logic
            unrealized_pct_change=0  # Initialize as 0; adjust based on your logic
        )

    def update_from_trade(self, trade):
        """Update the Holding instance based on a trade."""
        if trade.side == 'buy':
            total_amount = self.balance + trade.amount
            total_cost = self.total_cost + trade.cost
            self.average_cost = total_cost / total_amount
            self.balance = total_amount
            self.total_cost = total_cost
            # Update purchase_date if this is the earliest trade
            if trade.trade_time < self.purchase_date:
                self.purchase_date = trade.trade_time

        elif trade.side == 'sell':
            # Decrease the balance for sell trades
            self.balance -= trade.amount
            # Recalculate total cost based on the new balance
            self.total_cost = self.average_cost * self.balance


class RealizedProfit(Base):
    """All realized profits are stored in this table."""
    __tablename__ = 'realized_profits'

    id = Column(Integer, primary_key=True)
    currency = Column(String, nullable=False, index=True)
    profit_loss = Column(Numeric)  # Realized profit or loss for the trade
    sell_amount = Column(Numeric)  # The quantity of the cryptocurrency that was sold
    sell_price = Column(Numeric)  # The price at which the cryptocurrency was sold
    timestamp = Column(DateTime, default=func.now())  # Timestamp of when the profit was realized


class ProfitData(Base):
    """Periodic snapshots of the portfolio's performance are stored in this table."""
    __tablename__ = 'profit_data'

    id = Column(Integer, primary_key=True)
    snapshot_date = Column(DateTime, default=func.now())  # The date of the snapshot
    total_realized_profit = Column(Numeric)  # Total realized profit/loss up to the snapshot date
    total_unrealized_profit = Column(Numeric)  # Total unrealized profit/loss at the snapshot date
    portfolio_value = Column(Numeric)  # Total value of the portfolio at the snapshot date
    # Additional performance metrics can be added here


class SymbolUpdate(Base):
    """tracks the symbol and last_update_time"""
    __tablename__ = 'symbol_updates'

    symbol = Column(String, primary_key=True)
    last_update_time = Column(DateTime)


class old_DatabaseManager:
    def __init__(self, utility, exchange, log_manager, ticker_manager, portfolio_manager, app_config):
        self.log_manager = log_manager
        self.exchange = exchange
        self.database_dir = app_config.database_dir
        self.sqlite_db_path = app_config.sqlite_db_path
        self.utility = utility
        self.ticker_manager = ticker_manager
        self.portfolio_manager = portfolio_manager
        self.app_config = app_config
        self.ticker_cache = None
        self.market_cache = None
        self.start_time = None
        self.web_url = None
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{app_config.sqlite_db_path}", echo=True)
        self.AsyncSession = sessionmaker(bind=self.engine, class_=AsyncSession, expire_on_commit=False)
        self.session_lock = asyncio.Lock()

        os.makedirs(self.database_dir, exist_ok=True)  # Ensure the database directory exists
        # Asynchronously create all tables

    async def async_create_tables(self):
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    def set_trade_parameters(self, ticker_cache, market_cache):
        self.ticker_cache = ticker_cache
        self.market_cache = market_cache

    @asynccontextmanager
    async def session_scope(self):
        """Provide a transactional scope around a series of operations."""
        async with self.AsyncSession() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()

    @staticmethod
    async def clear_new_trades(session: AsyncSession):
        """Clear all entries in the trades_new table."""
        await session.execute(delete(NewTrade))
        await session.commit()

    async def update_market_data(self):
        """PART I: Data Gathering and Database Loading. Fetch and prepare market data from various sources."""
        try:
            ticker_cache, market_cache, current_prices, balances = await self.ticker_manager.update_ticker_cache()
            filtered_balances = balances['filtered_balances']
            if not market_cache:
                self.log_manager.sighook_logger.info("Market cache is empty. Unable to fetch historical trades.")
                return None  # or return an empty dictionary {}

            return {
                'ticker_cache': ticker_cache,
                'market_cache': market_cache,
                'current_prices': current_prices,
                'filtered_balances': filtered_balances
            }
        except Exception as e:
            self.log_manager.sighook_logger.error(f"Error updating market data: {e}", exc_info=True)
            return {}  # Return an empty dictionary in case of an error


    @staticmethod
    def create_trade(trade, symbol):
        # Handle ISO 8601 formatted datetime strings
        trade_time_str = trade['datetime'].rstrip('Z')  # Remove the 'Z' if present
        trade_time = datetime.datetime.fromisoformat(trade_time_str)
        return Trade(
            trade_time=trade_time,
            trade_id=trade['id'],
            order_id=trade['order'],
            symbol=symbol,
            price=trade['price'],
            amount=trade['amount'],
            cost=trade['cost'],
            side=trade['side'],
            fee=trade.get('fee', {}).get('cost', None)
        )

    async def update_symbol_update(self, session, symbol, trade_datetime):
        """PART I: Data Gathering and Database Loading. """
        # Fetch the existing SymbolUpdate entry or create a new one
        symbol_update = await session.get(SymbolUpdate, symbol)
        if symbol_update:
            # Update the last update time
            symbol_update.last_update_time = parser.parse(trade_datetime)
        else:
            # Create a new SymbolUpdate record
            symbol_update = SymbolUpdate(symbol=symbol, last_update_time=parser.parse(trade_datetime))
            session.add(symbol_update)
        await session.flush()  # Use the passed session directly


    async def old_fetch_and_process_trades(self, session, symbol, last_update_time):
        """PART I: Data Gathering and Database Loading.  Fetch and process trades for a given symbol using the provided
        session."""
        symbol, trades_list = await self.portfolio_manager.get_my_trades(symbol, last_update_time)  # Unpack the tuple
        if trades_list:  # Check if there are trades to process
            for trade in trades_list:
                existing_trade = await session.get(Trade, trade['id'])
                if not existing_trade:
                    new_trade = self.create_trade(trade, symbol)
                    session.add(new_trade)
                    await self.update_symbol_update(session, symbol, trade['datetime'])


    async def update_trade_summaries_from_new_trades(self, session: AsyncSession):
        """PART I and PART VII: Data Gathering and Database Loading, and Data Processing and Analysis."""
        try:
            stmt = (
                select(
                    NewTrade.symbol,
                    func.count().label('total_trades'),
                    func.sum(NewTrade.cost).label('total_cost'),
                    func.sum(NewTrade.fee).label('total_fees'),
                    (func.sum(NewTrade.cost) / func.count()).label('average_cost_without_fees'),
                    ((func.sum(NewTrade.cost + NewTrade.fee)) / func.count()).label('average_cost_with_fees')
                )
                .group_by(NewTrade.symbol)
            )

            result = await session.execute(stmt)
            summaries = result.all()

            for summary in summaries:
                # Use scalar values to access the computed columns
                total_trades = summary.total_trades
                total_cost = summary.total_cost
                total_fees = summary.total_fees
                average_cost_without_fees = summary.average_cost_without_fees
                average_cost_with_fees = summary.average_cost_with_fees

                existing_summary = await session.execute(
                    select(TradeSummary).filter(TradeSummary.symbol == summary.symbol)
                )
                existing_summary = existing_summary.scalars().first()

                if existing_summary:
                    # Update existing summary
                    existing_summary.total_trades += total_trades
                    existing_summary.total_cost += total_cost
                    existing_summary.total_fees += total_fees
                    existing_summary.average_cost_without_fees = average_cost_without_fees
                    existing_summary.average_cost_with_fees = average_cost_with_fees
                else:
                    # Insert new summary
                    new_summary = TradeSummary(
                        symbol=summary.symbol,
                        total_trades=total_trades,
                        total_cost=total_cost,
                        total_fees=total_fees,
                        average_cost_without_fees=average_cost_without_fees,
                        average_cost_with_fees=average_cost_with_fees,
                    )
                    session.add(new_summary)
            print('Trade summaries updated successfully')
        except Exception as e:
            self.log_manager.sighook_logger.error(f'Error updating trade summaries: {e}', exc_info=True)
            await session.rollback()


    async def update_holdings_from_list(self, session, holdings):
        """PART VI: Profitability Analysis and Order Generation """
        """take holdings list and update the database with the latest information.  This function is designed to be used
            in conjunction with the fetch_holdings method in the portfolio_manager class.  The holdings list is a list of
            current holdings in the portfolio.  The function will update the database with the latest information for each"""
        aggregated_df = []
        try:
            for item in holdings:
                currency = item['Currency']
                # Use the aggregate_trade_data_for_symbol function to get aggregated trade data
                aggregated_data = await self.aggregate_trade_data_for_symbol(session, currency)

                if aggregated_data:
                    aggregated_df.append({'Currency': currency, **aggregated_data})  # Append a new record
                    holding = await session.get(Holding, currency)
                    if holding:
                        # Update existing holding with aggregated data
                        holding.update_from_aggregated_data(aggregated_data, item['Balance'])
                    else:
                        # Create a new Holding instance
                        new_holding = Holding.create_from_aggregated_data(currency, aggregated_data, item['Balance'])
                        session.add(new_holding)
                else:
                    # None indicates no changes to the holding
                    continue  # Skip to the next item in the loop

        except Exception as e:
            error_details = traceback.format_exc()
            self.log_manager.sighook_logger.error(f"Error updating holdings from list: {error_details}, {e}")
            raise  # Allow the calling function to handle the rollback
        df = pd.DataFrame(aggregated_df)
        return df


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
                    timestamp=datetime.datetime.utcnow()
                )
                session.add(new_realized_profit)

            return total_realized_profit
        except Exception as e:
            error_details = traceback.format_exc()
            self.log_manager.sighook_logger.error(f"Error processing sell order FIFO for {symbol}: {error_details}, {e}")
            raise  # Allow the calling function to handle the rollback

# <><><><><><><><><><><><><><><><><> NOT IMPLIMENTED YET <><><><><><><><><><><><><><><><><>

    async def update_partial_fill(self, trade_id, filled_amount):
        async with self.AsyncSession() as session:
            async with session.begin():
                trade = await session.get(Trade, trade_id)
                # trade = session.query(Trade).filter_by(trade_id=trade_id).first()
                if trade:
                    trade.amount = filled_amount
                    session.commit()
                    # Update the corresponding holding
                    await self.update_holding_from_trade(trade)

    async def update_holding_from_trade(self, trade):
        async with self.AsyncSession() as session:
            async with session.begin():
                holding = await session.get(Holding, trade.symbol)
                # holding = await session.get(Holding, trade.symbol.split('/')[0])

                if trade.side == 'buy':
                    if not holding:
                        # Create a new holding if it doesn't exist
                        holding = Holding.create_from_trade(trade)
                        session.add(holding)
                    else:
                        # Update existing holding
                        holding.update_from_trade(trade)

                elif trade.side == 'sell':
                    if holding:
                        # Update the holding for sell trades
                        holding.update_from_trade(trade)
                    # Consider handling the case where holding does not exist for a sell trade

                await session.commit()

    async def x_calculate_and_update_trade_summary(self, session: AsyncSession, symbols=None):
        """PART I and PART VII: Data Gathering and Database Loading, and Data Processing and Analysis.
        Targeted Recalculations: If ever needed,  call calculate_and_update_trade_summary(session, symbols=[...])
        with specific symbols to recalculate summaries for those symbols."""

        if symbols:
            for symbol in symbols:
                stmt = (
                    select(
                        Trade.symbol,
                        func.count().label('total_trades'),
                        func.sum(Trade.cost).label('total_cost'),
                        func.sum(Trade.fee).label('total_fees'),
                        (func.sum(Trade.cost) / func.count()).label('average_cost_without_fees'),
                        ((func.sum(Trade.cost + Trade.fee)) / func.count()).label('average_cost_with_fees')
                    )
                    .filter(Trade.symbol == symbol)
                    .group_by(Trade.symbol)
                )

                result = await session.execute(stmt)
                summary_data = result.one_or_none()

                if summary_data:
                    summary = await session.get(TradeSummary, {'symbol': summary_data.symbol})
                    if summary:
                        # Update existing summary with recalculated data
                        summary.total_trades = summary_data.total_trades
                        summary.total_cost = summary_data.total_cost
                        summary.total_fees = summary_data.total_fees
                        summary.average_cost_without_fees = summary_data.average_cost_without_fees
                        summary.average_cost_with_fees = summary_data.average_cost_with_fees
                    else:
                        # Insert new summary if it doesn't exist
                        new_summary = TradeSummary(**summary_data._asdict())
                        session.add(new_summary)

        else:
            # If no specific symbols are provided, update summaries based on new trades
            await self.update_trade_summaries_from_new_trades(session)
# <><><><><><><><><><><><><><><><><><><><> ADDED TO NEW DATABASE MANAGER <><><><><><><><><><><><><><><><><><><

    async def process_market_data(self, session, market_cache):
        """PART I: Data Gathering and Database Loading. Process all symbols in the market cache concurrently using a
        single session and transaction."""
        try:
            # Retrieve last update times for all symbols
            symbol_updates = await session.execute(select(SymbolUpdate))
            last_update_by_symbol = {update.symbol: update.last_update_time for update in symbol_updates.scalars().all()}

            # Default date if no update time is recorded
            default_last_update = datetime.datetime(2017, 12, 1)

            # Create tasks for each symbol in the market cache
            tasks = []
            for market in market_cache:
                symbol = market['symbol']
                # Get the last update time if available, otherwise use the default
                last_update_time = last_update_by_symbol.get(symbol, default_last_update)
                task = self.process_symbol(session, market, last_update_time)
                tasks.append(task)

            # Use asyncio.gather to process all tasks
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
        except Exception as e:
            self.log_manager.sighook_logger.error(f"Error processing market data: {e}", exc_info=True)
            await session.rollback()
            raise

    async def process_symbol(self, session, market, last_update_time):
        """PART I: Data Gathering and Database Loading. Process a single symbol's market data."""
        try:
            trades = await self.portfolio_manager.get_my_trades(market['symbol'], last_update_time)
            for trade in trades:
                await self.process_trade(session, trade, market['symbol'])  # adds new trade to the trades_new table

            await session.flush()
        except Exception as e:
            self.log_manager.sighook_logger.error(f"Error processing symbol {market['symbol']}: {e}", exc_info=True)
            await session.rollback()
            raise

    async def process_trade(self, session, trade, symbol):
        """PART I: Data Gathering and Database Loading. Process a single trade."""
        """Process a single trade."""
        trade_id = trade.get('id')
        try:
            if trade_id is None:
                self.log_manager.error("Missing trade ID")
                return
            existing_trade = await session.get(Trade, trade_id)
            if not existing_trade:
                new_trade = self.create_trade(trade, symbol)
                session.add(new_trade)

        except Exception as e:
            self.log_manager.sighook_logger.error(f"Error processing trade {trade_id}: {e}", exc_info=True)
            raise  # Reraise the exception to ensure it's handled by the outer transaction management
    async def initialize_db(self, start_time):
        """PART I: Data Gathering and Database Loading.   The database should be initialized one time, called at the start of
                        the program to initialize the database with the latest trade data"""

        market_data = await self.update_market_data()
        if not market_data:
            self.log_manager.sighook_logger.info("No market data available to initialize the database.")
            return

        async with self.session_scope() as session:
            try:
                await self.process_market_data(session, market_data['market_cache'])
                await session.commit()
                return market_data
            except Exception as e:
                await session.rollback()
                self.log_manager.sighook_logger.error("Error processing market data during DB initialization", exc_info=True)
                raise


    async def old_process_symbol(self, session, symbol_data):
        """Process a single symbol's market data."""
        symbol = symbol_data['symbol']
        last_update_time = symbol_data.get('last_update')
        try:
            trades = await self.portfolio_manager.get_my_trades(symbol, last_update_time)
            for trade in trades:
                await self.process_trade(session, trade, symbol)
        except Exception as e:
            self.log_manager.error(f"Error processing symbol {symbol}: {e}", exc_info=True)
            raise

    async def old_initialize_db(self, start_time):
        """PART I: Data Gathering and Database Loading.   The database should be initialized one time, called at the start of
                 the program to initialize the database with the latest trade data"""
        try:
            ticker_cache, market_cache, current_prices, filtered_balances = await self.ticker_manager.update_ticker_cache()
            self.utility.print_elapsed_time(self.start_time, 'Ticker Cache Updated')
            if not market_cache:
                self.log_manager.sighook_logger.info("Market cache is empty. Unable to fetch historical trades.")
                return ticker_cache, market_cache

            async with self.AsyncSession() as session:
                await session.begin()
                try:
                    await self.clear_new_trades(session)  # delete all entries in the trades_new table
                    # Process all symbols concurrently
                    await self.process_symbols(market_cache)
                    self.utility.print_elapsed_time(self.start_time, 'Symbols processed')
                    await session.commit()
                except Exception as e:
                    await session.rollback()
                    self.log_manager.sighook_logger.error(f'initialize_db - processing: {e}', exc_info=True)
                    raise  # Rethrow after logging to handle higher up if needed
            return ticker_cache, market_cache
        except Exception as e:
            self.log_manager.sighook_logger.error(f'initialize_db - initialization: {e}', exc_info=True)
            raise  # Rethrow to allow for further handling/logging

    async def old_process_symbols(self, market_cache):
        """PART I: Data Gathering and Database Loading.  Process all symbols in the market cache using a single session
        and transaction."""
        async with self.AsyncSession() as session:
            await session.begin()
            try:
                symbol_updates = await session.execute(select(SymbolUpdate))
                # Load only the symbol and last_update_time columns
                last_update_by_symbol = {update.symbol: update.last_update_time for update in symbol_updates.scalars().all()}
                symbols = [market['symbol'] for market in market_cache]

                # Limit the number of concurrent tasks
                concurrency = 40  # Adjust based on your system's capability
                semaphore = asyncio.Semaphore(concurrency)

                async def process_symbol(symbol):
                    async with semaphore:
                        return await self.fetch_and_process_trades(session, symbol, last_update_by_symbol.get(symbol))

                tasks = [process_symbol(symbol) for symbol in symbols]
                await asyncio.gather(*tasks, return_exceptions=True)

                await session.commit()  # Commit all changes at once
            except Exception as e:
                await session.rollback()  # Roll back if any error occurs
                self.log_manager.sighook_logger.error(f'Error processing symbols: {e}', exc_info=True)
                raise

    async def initialize_holding_db(self, holdings):
        """PART V: Order Execution"""
        async with self.AsyncSession() as session:  # Use async context manager for session
            async with session.begin():  # ensures that transactions are automatically committed if everything goes well or
                # rolled back in case of an exception.
                try:
                    try:
                        # Process each holding asynchronously
                        await self.process_holdings(session, holdings)
                        await session.commit()
                    except Exception as e:
                        await session.rollback()
                        error_details = traceback.format_exc()
                        self.log_manager.sighook_logger.error(f'initialize_holding_db: {error_details}, {e}')
                finally:
                    await session.close()  # Ensure the session is closed after operations

    async def process_holdings(self, session, holdings):
        """PART V: Order Execution"""
        try:
            for coin in holdings:
                symbol = coin['symbol']
                aggregated_data = await self.aggregate_trade_data_for_symbol(session, symbol)

                # If aggregated_data is None, log and continue to the next coin
                if aggregated_data is None:
                    continue

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
                    existing_holding.purchase_amount = aggregated_data['total_amount']
                    existing_holding.balance = coin['Balance']
                    existing_holding.average_cost = aggregated_data['average_cost']
                    existing_holding.total_cost = aggregated_data['total_cost']

        except Exception as e:
            error_details = traceback.format_exc()
            await session.rollback()  # Roll back the session in case of error
            self.log_manager.sighook_logger.error(f'process_holdings: {error_details}, {e}')

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

