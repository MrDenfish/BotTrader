from sqlalchemy.ext.asyncio import  AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.sql import func, and_
from sqlalchemy import delete
from database_ops_holdings import DatabaseOpsHoldingsManager
from database_table_models import Trade, SymbolUpdate, NewTrade, Holding, RealizedProfit
from datetime import datetime as dt
import pytz
from Utils.logging_manager import LoggerManager
from typing import Optional, List
from decimal import Decimal, ROUND_DOWN
import asyncio
import logging


class DatabaseOpsManager:

    def __init__(self, exchange, ccxt_api, log_manager, csv_manager, profit_extras, app_config, utility, portfolio_manager,
                 holdings_manager, engine, async_session_factory):

        self.log_manager = log_manager
        self.exchange = exchange
        #self.db_tables = db_tables
        self.ccxt_exceptions = ccxt_api
        self.utility = utility
        #self.ticker_manager = ticker_manager
        self.portfolio_manager = portfolio_manager
        self.profit_extras = profit_extras
        self.app_config = app_config
        self.holdings_manager = holdings_manager
        self.csv_manager = csv_manager
        self.ticker_cache = None
        self.market_cache = None
        self.start_time = None
        self.web_url = None
        self.session_lock = asyncio.Lock()

        # Use the provided engine and session factory
        self.engine = engine
        self.AsyncSessionLocal = async_session_factory
        self.holdings_manager = DatabaseOpsHoldingsManager(log_manager, async_session_factory)

        #self.AsyncSessionLocal = sessionmaker(bind=self.engine, class_=AsyncSession, expire_on_commit=False)

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
        transaction_type = trade['side'].lower()

        price = float(trade['price'])
        amount = float(trade['amount'])
        if amount == 0.0:
            pass  # debug
        is_buy = transaction_type == 'buy'
        cost = -1*float(trade['cost']) if is_buy else 0
        proceeds = float(trade['cost']) if not is_buy else 0
        fee = float(trade['fee']['cost'] if 'fee' in trade else 0)
        total = float(trade['cost']) + fee if is_buy else (trade['cost']) - fee

        if new:
            # Adding to 'trades_new' table
            new_trade = NewTrade(
                trade_id=trade.trade_id,
                order_id=trade.get('order', 'na'),  # 'na' as a default order id might not be ideal
                trade_time=trade.trade_time,
                transaction_type=transaction_type,
                asset=trade.symbol.split('/')[0],
                amount=amount,
                currency=trade.symbol.split('/')[1],
                price=price,
                cost=cost,
                proceeds=proceeds,
                fee=fee,
                total=total
            )
            await session.add(new_trade)
        else:
            # Adding to 'trades' table
            await session.add(trade)

    @staticmethod
    async def get_all_trades(session):
        result = await session.execute(select(Trade))
        trades = result.scalars().all()
        return trades

    @staticmethod
    async def clear_new_trades(session: AsyncSession):
        """Clear all entries in the trades_new table."""
        await session.execute(delete(NewTrade))
        await session.execute(delete(SymbolUpdate))
        # await session.execute(delete(ProfitData))
        await session.commit()

    @staticmethod
    async def clear_symbol_updates(session: AsyncSession):
        """Clear all entries in the symbol_updates table."""
        await session.execute(delete(SymbolUpdate))
        await session.commit()


    async def process_data(self, session, market_data, holdings_list, holdings_df, current_prices, csv_dir=None):
        """
        PART I: Data Gathering and Database Loading.
        This method handles processing market data and updating the database.
        """
        try:
            # Step 1: Enable detailed SQL logging (optional)
            if self.app_config.log_level == 'DEBUG':
                LoggerManager.setup_sqlalchemy_logging(logging.INFO)
            else:
                LoggerManager.setup_sqlalchemy_logging(logging.WARNING)

            # Step 2: Clear new trades table if necessary
            await self.clear_new_trades(session)

            if not market_data:
                self.log_manager.info("No market data available to process.")
                return

            # Step 3: Process market data or CSV files
            if not csv_dir:
                await self.process_market_data(session, market_data['market_cache'])
            else:
                await self.csv_manager.process_csv_data(session, csv_dir)
                await session.commit()

            await self.process_market_data(session, market_data['market_cache'])

            # Step 5: Initialize or Update Holdings Database
            await self.holdings_manager.initialize_holding_db(session, holdings_list, holdings_df, current_prices)

            # Step 6: Process additional trade data (if necessary)
            # self.log_manager.info("Processing additional trade data...")
            # self.process_trade_data(market_data['market_cache'])

            # Commit the session
            await session.commit()
            self.log_manager.debug("Session committed successfully.")

        except Exception as e:
            await session.rollback()
            self.log_manager.error(f"Failed to process data: {e}")
            raise

    async def process_market_data(self, session: AsyncSession, market_cache):
        """
        Process market data for all symbols concurrently using a single session and transaction.
        This function loads data from `market_cache` and stores it in the database.
        """
        try:
            tasks = []

            # Retrieve the last update time for each symbol from the database
            for market in market_cache:
                last_update_time = await self.get_last_update_time_for_symbol(session, market['asset'])
                task = self.process_symbol_with_new_session(market, last_update_time)
                tasks.append(task)

            # Run tasks concurrently
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Handle any exceptions that may have occurred during task execution
            for result in results:
                if isinstance(result, Exception):
                    self.log_manager.error(f"Error processing market data: {result}", exc_info=True)
                    continue
        except Exception as e:
            self.log_manager.error(f"Error processing market data: {e}", exc_info=True)
            await session.rollback()
            raise




    async def process_symbol_with_new_session(self, market: object, last_update_time: object) -> object:
        async with self.AsyncSessionLocal() as session:
            return await self.process_symbol(session, market, last_update_time)


    async def process_symbol(self, session: AsyncSession, market, last_update_time):
        """PART I: Data Gathering and Database Loading. Process a single symbol's market data."""
        try:
            most_recent_trade_time = None
            batch_size = 1000  # Define your batch size here
            trade_objects = []  # List to hold trades before committing in batches

            if "-" in market['symbol']:
                self.log_manager.debug(f"Symbol format for {market['symbol']} in not correct and should be "
                                                      f"fixed, use '/' instead of '-'")
                pass  # debug

            # Fetch trades for the symbol from the last update time
            trades = await self.portfolio_manager.get_my_trades(market['symbol'], last_update_time)
            if not trades:
                self.log_manager.debug(f"No trades found for {market['symbol']} since {last_update_time}")
                return market['symbol'], last_update_time

            # Fetch the most recent trade for this asset to get the starting balance
            last_trade_query = (
                select(Trade)
                .filter(Trade.asset == market['asset'].split('/')[0])
                .order_by(Trade.trade_time.desc())
                .limit(1)
            )
            last_trade_result = await session.execute(last_trade_query)
            last_trade = last_trade_result.scalar_one_or_none()
            starting_balance = last_trade.balance if last_trade else 0.0  # If no previous trades, balance is 0

            for trade in trades:
                if 'id' not in trade or not trade['id']:
                    self.log_manager.error("Missing trade ID")
                    continue

                trade_time = trade['datetime'].replace(microsecond=0)
                trade_amount = float(trade['amount'])
                trade_price = float(trade['price'])
                trade_amount = trade_amount if trade['side'].lower() == 'buy' else -trade_amount

                # Check if the trade already exists to avoid duplicates
                existing_trade_query = (
                    select(Trade)
                    .filter(
                        and_(
                            func.strftime('%Y-%m-%d %H:%M:%S', Trade.trade_time) == trade_time.strftime('%Y-%m-%d %H:%M:%S'),
                            Trade.asset == market['asset'].split('/')[0],
                            func.abs(Trade.amount - Decimal(trade_amount)) < Decimal('1e-8'),
                            func.abs(Trade.price - Decimal(trade_price)) < Decimal('1e-8')
                        )
                    )
                )
                existing_trade_result = await session.execute(existing_trade_query)
                existing_trade = existing_trade_result.scalar()

                if existing_trade:
                    self.log_manager.debug(
                        f"Existing trade found: time={existing_trade.trade_time}, asset={existing_trade.asset}, "
                        f"amount={existing_trade.amount}, price={existing_trade.price}")
                    continue

                # Update the running balance
                starting_balance += trade_amount

                # Create the trade object, passing the starting_balance
                trade_obj = await self.create_trade_object(trade, market['asset'], starting_balance)
                if trade_obj is None:
                    self.log_manager.error(f"Failed to create trade object: {trade}")
                    continue

                if trade_obj.amount == 0.0:
                    self.log_manager.error(f"Trade amount is zero: {trade}")
                    continue

                trade_objects.append(trade_obj)

                if len(trade_objects) >= batch_size:
                    # Add the batch of trades to the session
                    session.add_all(trade_objects)
                    await session.flush()
                    trade_objects.clear()  # Clear the list for the next batch

                # Update the most recent trade time
                if not most_recent_trade_time or trade_obj.trade_time > most_recent_trade_time:
                    most_recent_trade_time = trade_obj.trade_time

            # Commit any remaining trades after the loop
            if trade_objects:
                session.add_all(trade_objects)
                await session.flush()

            await session.commit()  # Ensure the transaction is committed

            return market['symbol'], most_recent_trade_time if most_recent_trade_time else last_update_time

        except Exception as e:
            self.log_manager.error(f"Error processing symbol {market['asset']}: {e}", exc_info=True)
            await session.rollback()
            raise

    async def bulk_insert_trades(self, session, trade_objects):
        """Bulk insert trades."""
        try:
            await session.bulk_save_objects(trade_objects)
            await session.flush()
        except Exception as e:
            self.log_manager.error(f"Error during bulk insert: {e}", exc_info=True)

    @staticmethod
    async def get_existing_trades(session, market_asset, trades):
        """Fetch all existing trades for a given asset."""
        trade_ids = [trade['id'] for trade in trades]
        existing_trades_query = (
            select(Trade)
            .filter(Trade.trade_id.in_(trade_ids), Trade.asset == market_asset.split('/')[0])
        )
        result = await session.execute(existing_trades_query)
        return result.scalars().all()

    async def create_trade_object(self, trade_data, asset, balance):
        try:
            trade_time = trade_data['datetime']
            transaction_type = trade_data['side'].lower()

            is_buy = transaction_type == 'buy'
            cost = -1 * float(trade_data['cost']) if is_buy else 0
            proceeds = float(trade_data['cost']) if not is_buy else 0
            amount = float(trade_data['amount'])
            fee = float(trade_data['fee']['cost'] if 'fee' in trade_data else 0)
            total = cost - fee if is_buy else proceeds - fee

            temp = Trade(
                trade_time=trade_time,
                trade_id=trade_data['id'],
                order_id=trade_data.get('order', 'na'),
                transaction_type=transaction_type,
                asset=asset.split('/')[0],
                currency=trade_data['symbol'].split('/')[1],
                price=float(trade_data['price']),
                amount=amount if is_buy else -amount,
                balance=balance,  # Set the running balance
                cost=cost,
                proceeds=proceeds,
                fee=fee,
                total=total
            )
            return temp
        except Exception as e:
            self.log_manager.error(f"Error creating trade object: {e}", exc_info=True)
            return None


    def create_temp_trade(self,trade_obj):
        """Creates a temporary trade object from a persistent Trade object."""
        try:
            # Create a new trade object, excluding '_sa_instance_state' and other unwanted attributes
            trade_data = {key: value for key, value in trade_obj.__dict__.items() if key != '_sa_instance_state'}
            return NewTrade(**trade_data)
        except Exception as e:
            self.log_manager.error(f"Error creating temp trade object: {e}", exc_info=True)
            return None

    @staticmethod
    def is_new_trade(trade_datetime, last_update_time):
        """Determine if a trade is new based on the given datetime."""

        return trade_datetime > last_update_time


    async def process_sell_fifo(self, session, asset, sell_amount, sell_price):
        """PART VI: Profitability Analysis and Order Generation.  handle the selling of assets using the
        First-In-First-Out (FIFO) accounting method
        - fetch all buy trades for a specific asset
        - iterate through buy trades and deduct sold amounts from trades in the order they were bought.
        - for each buy, calculate the available amount that can be sold
        - calculate realized profit for each portion of the asset that is sold
        - """
        try:
            buy_trades = await session.execute(
                select(Trade).filter(Trade.asset == asset,
                                     Trade.transaction_type == 'buy').order_by(Trade.trade_time.asc()))
            buy_trades = buy_trades.scalars().all()

            remaining_sell_amount = float(sell_amount)
            total_realized_profit = float('0')  # initialize total realized profit

            for buy_trade in buy_trades:
                if remaining_sell_amount <= 0:
                    break  # All sold

                available_for_sale = min(buy_trade.balance, remaining_sell_amount)
                realized_profit = (float(sell_price) - buy_trade.price) * available_for_sale - buy_trade.fee
                total_realized_profit += realized_profit

                # Update the holding balance
                buy_trade.balance -= available_for_sale
                flag_modified(buy_trade, "balance")
                remaining_sell_amount -= available_for_sale

                self.log_manager.debug(
                    f"Updated buy trade after update: {buy_trade.trade_id}, new balance: {buy_trade.balance}")

                # Log realized profit
                new_realized_profit = RealizedProfit(
                    currency=asset,
                    profit_loss=realized_profit,
                    sell_amount=available_for_sale,
                    sell_price=sell_price,
                    timestamp=dt.utcnow()
                )
                session.add(new_realized_profit)

            await session.commit()
            return total_realized_profit
        except Exception as e:
            await session.rollback()
            self.log_manager.error(f"Error processing sell order FIFO for {asset}: {e}", exc_info=True)
            raise  # Allow the calling function to handle the rollback

    async def log_all_trades(self, session, log_point):
        try:
            trades = await session.execute(select(Trade))
            trades = trades.scalars().all()
            for trade in trades:
                if trade.asset == 'BTC' or trade.asset == 'MNDE':
                    if trade.amount == 0:
                        self.log_manager.debug(f"{log_point} - Asset: {trade.asset} Trade ID:"
                                                              f" {trade.trade_id}, Amount: {trade.amount}, Transaction Type:"
                                                              f" {trade.transaction_type}")
        except Exception as e:
            self.log_manager.error(f"Error logging all trades at {log_point}: {e}")

    async def get_last_update_time_for_symbol(self, session, asset):
        """
        Retrieve the time of each symbol's most recent closed trade from the database.
        If no record is found, return a default datetime value.

        Parameters:
        - session (AsyncSession): The SQLAlchemy asynchronous session.
        - asset (str): The trading asset to query the last update time for.

        Returns:
        - datetime: The last update time for the asset, or a default datetime if not found.
        """
        default_datetime = dt(2017, 12, 1).replace(tzinfo=pytz.UTC)
        try:
            # Query the database for the symbol's last update time
            symbol_update = await session.get(SymbolUpdate, asset)

            # If found, return the last update time, otherwise return the default datetime
            return symbol_update.last_update_time if symbol_update else default_datetime
        except Exception as e:
            # Log the error and return the default date as a fallback
            self.log_manager.error(f'Error getting last update time for {asset}: {e}', exc_info=True)
            return default_datetime

    async def set_last_update_time(self, session, symbol, last_update_trade_time):
        """ PART VI: Profitability Analysis and Order Generation Updates or sets the last update time for a given trading
        symbol in the database."""
        try:
            symbol_update = await session.get(SymbolUpdate, symbol)
            if symbol_update:
                symbol_update.last_update_time = last_update_trade_time
            else:
                aware_datetime = dt(2017, 12, 1).replace(tzinfo=pytz.UTC)
                if last_update_trade_time and last_update_trade_time.tzinfo is None:
                    last_update_trade_time = last_update_trade_time.replace(tzinfo=pytz.UTC)

                if last_update_trade_time > aware_datetime:
                    new_symbol_update = SymbolUpdate(
                        symbol=symbol,
                        last_update_time=last_update_trade_time
                    )
                    session.add(new_symbol_update)
            # await session.commit()
            return last_update_trade_time
        except Exception as e:
            self.log_manager.error(f"Error setting last update time for {symbol}: {e}", exc_info=True)
            await session.rollback()
            raise

    async def fetch_trades(self, session: AsyncSession, symbol: str,
                           last_update: Optional[dt] = None) -> List[dict]:

        """PART VI: Profitability Analysis and Order Generation
            Fetch trades that are not in the trades table and have occurred since the last update time.
            This method should be called for each symbol in the portfolio.

            Parameters:
            - session (AsyncSession): The SQLAlchemy asynchronous session.
            - symbol (str): The trading symbol to fetch trades for.
            - last_update (Optional[datetime]): The last time trades were updated. If None, a default time will be used.

            Returns:
            - List[dict]: A list of new trades processed into a standardized format."""

        try:
            if last_update is None:
                # Asynchronously count holdings to determine if a default last update time should be used
                count = await session.execute(select(Holding))
                count = count.scalar_one_or_none()

                if count == 0:
                    last_update = dt(2017, 12, 1)
                else:
                    # Asynchronously fetch the most recent trade for the symbol
                    most_recent_trade = await session.execute(
                        select(Trade)
                        .filter(Trade.asset == symbol)
                        .order_by(Trade.trade_time.desc())
                        .limit(1)
                    )
                    most_recent_trade = most_recent_trade.scalar_one_or_none()
                    last_update = most_recent_trade.trade_time if most_recent_trade else None

            # Convert last_update to Unix timestamp if it's not None
            last_update_unix = self.utility.time_unix(last_update.strftime("%Y-%m-%d %H:%M:%S.%f")) if last_update else None

            # Parameters for the API call
            params = {'paginate': True, 'paginationCalls': 20}
            endpoint = 'private'  # For rate limiting

            # Await the asynchronous API call to fetch trades since the last update time
            raw_trades = await self.ccxt_exceptions.ccxt_api_call(
                self.exchange.fetch_my_trades,
                endpoint,
                symbol=symbol,
                since=last_update_unix,
                params=params
            )

            # Process the raw trades into a standardized format
            if raw_trades is not None or raw_trades is []:
                new_trades = [self.process_trade_data(trade) for trade in raw_trades if trade]
            else:
                return []
            # Update the last update time if new trades were fetched
            # latest_time = self.utility.convert_timestamp(last_update_unix)
            if new_trades:
                # print(f'New trades since {latest_time} for {symbol}')  # debug
                await self.set_last_update_time(session, symbol, new_trades[-1]['trade_time'])
            return new_trades

        except Exception as e:
            self.log_manager.error(f'Error fetching new trades for {symbol}: {e}', exc_info=True)
            return []

    def process_trade_data(self, trade):
        """PART VI: Profitability Analysis and Order Generation """
        try:
            # Initialize fee_cost
            fee_cost = None
            if trade is None:
                return None

            # Extract the trade time, handling the case where 'info' may or may not be present
            if 'info' in trade and 'trade_time' in trade['info']:
                trade_time_str = trade['info']['trade_time']
                # Check if the trade_time_str contains a decimal part
                if '.' in trade_time_str:
                    trade_time_str = trade_time_str.split('.')[0] + '.' + trade_time_str.split('.')[1][:6]
                else:
                    trade_time_str = trade_time_str.rstrip("Z")
                trade_time = dt.fromisoformat(trade_time_str.rstrip("Z"))  # Assuming ISO format string
            elif 'trade_time' in trade and isinstance(trade['trade_time'], dt):
                trade_time = trade['trade_time']  # Directly use the datetime object
            else:
                self.log_manager.error(
                    f"Unexpected or missing 'trade_time' in trade data for {trade.get('asset')}")
                trade_time = None  # Handle the unexpected format or missing 'trade_time'

            # Handle the 'fee' field, which can be a dictionary or a direct numeric value
            if 'fee' in trade:
                fee = trade['fee']
                if isinstance(fee, dict) and 'cost' in fee:
                    fee_cost = fee['cost']
                elif isinstance(fee, (Decimal, float, int)):
                    fee_cost = fee
                else:
                    self.log_manager.error(f"Unexpected 'fee' format in trade data for {trade.get('asset')}")

            # Construct the processed trade dictionary
            processed_trade = {
                'trade_time': trade_time,
                'id': trade.get('id'),
                'order_id': trade.get('order_id'),
                'asset': trade.get('asset'),
                'price': Decimal(trade['price']).quantize(Decimal('0.01'), ROUND_DOWN) if 'price' in trade else None,
                'amount': Decimal(trade['amount']).quantize(Decimal('0.00000001'),
                                                            ROUND_DOWN) if 'amount' in trade else None,
                'cost': Decimal(trade['cost']).quantize(Decimal('0.01'), ROUND_DOWN) if 'cost' in trade else None,
                'side': trade.get('side').lower() if 'side' in trade else None,
                'fee': Decimal(str(fee_cost)).quantize(Decimal('0.01'), ROUND_DOWN) if fee_cost is not None else None,
            }

            return processed_trade
        except Exception as e:
            self.log_manager.error(f'process_trade_data: {e}', exc_info=True)
            return None

        # Perform any necessary validation or transformation on the extracted data
        # For example, you might want to ensure that 'side' is either 'buy' or 'sell'

    # <><><>><><><> TRouble shooting functions that may be deleted when no longer needed <><><><><

