from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.future import select
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.exc import SQLAlchemyError, InvalidRequestError
from sqlalchemy.sql import func
from sqlalchemy import delete
from database_table_models import Trade, SymbolUpdate, NewTrade, Holding, RealizedProfit
from datetime import datetime as dt
from typing import Optional, List
from decimal import Decimal, ROUND_DOWN
import asyncio
import datetime


class DatabaseOpsManager:
    def __init__(self, utility, exchange, ccxt_api,  log_manager, ticker_manager, portfolio_manager, app_config):

        self.log_manager = log_manager
        self.exchange = exchange
        self.ccxt_exceptions = ccxt_api
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

    def create_trade(self, trade_data, asset):
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
                asset=asset,
                price=trade_data['price'],
                amount=trade_data['amount'],
                cost=trade_data['cost'],
                transaction_type=trade_data['side'],
                fee=(trade_data['fee']['cost'] if isinstance(trade_data.get('fee'), dict) else trade_data.get('fee', 0))
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
                asset=trade_data['symbol'].split('/')[0],
                cost=trade_data['cost'],
                fee=-1*fee_cost
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
                asset = market['asset'].split('/')[0]
                last_update_time = last_update_by_symbol.get(asset, dt(2017, 12, 1))
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
                trades = await self.portfolio_manager.get_my_trades(market['asset'], last_update_time)
                if not trades:
                    return
                else:
                    self.log_manager.sighook_logger.debug(f"Processing {len(trades)} new trades for {market['asset']}")

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
                        trade_obj = self.create_trade(trade, market['asset'])
                        trade_objects.append(trade_obj)
                        if is_new_trade:
                            new_trade_obj = self.create_new_trade(trade)
                            trade_objects.append(new_trade_obj)

                if trade_objects:
                    session.add_all(trade_objects)

                # Update the last trade time once per symbol, after all trades have been processed
                if latest_trade_time > last_update_time:
                    await self.set_last_update_time(session, market['asset'], latest_trade_time)

                await session.commit()

            except Exception as e:
                await session.rollback()
                self.log_manager.sighook_logger.error(f"Error processing symbol {market['asset']}: {e}")
                raise
            finally:
                await session.flush()

    async def process_trade(self, session, trade, asset, new):
        """PART I: Data Gathering and Database Loading. Process a single trade."""
        """Process a single trade.

    :param session: The database session.
    :param trade: Trade data.
    :param symbol: Symbol of the trade.
    :param new: Boolean flag if this is a new trade not previously recorded."""

        trade_id = trade.get('id')
        try:
            if trade_id is None:
                self.log_manager.sighook_logger.error("Missing trade ID")
                return
            # Ensure there is no ongoing transaction before adding a new object
            if not session.is_active:
                self.log_manager.sighook_logger.error("Session is not active")
                return
            existing_trade = await session.get(Trade if not new else NewTrade, trade_id)
            if not existing_trade:
                trade_data = {
                    'trade_id': trade_id,
                    'trade_time': trade['datetime'],
                    'asset': asset,
                    'price': trade['price'],
                    'amount': trade['amount'],
                    'cost': trade['cost'],
                    'side': trade['side'],
                    'fee': trade['fee']['cost'] if trade['fee'] else 0
                }
                trade_obj = self.create_trade(trade_data, asset) if not new else self.create_new_trade(trade_data)

                # Add the new trade object to the session
                session.add(trade_obj)
                # Consider a flush here if needed, or manage your flushes carefully
                await session.commit()
        except InvalidRequestError as e:
            self.log_manager.sighook_logger.error(f"Session inactive: {e}")
        except SQLAlchemyError as e:
            self.log_manager.sighook_logger.error(f"Error processing trade {trade_id}: {e}", exc_info=True)
            await session.rollback()

    async def initialize_holding_db(self, session, holdings, current_prices=None):
        """Handle the initialization or update of holdings in the database based on provided data."""
        try:
            for holding in holdings:
                await self.update_single_holding(session, holding, current_prices)
        except Exception as e:
            await session.rollback()
            self.log_manager.sighook_logger.error(f'initialize_holding_db: {e}', exc_info=True)

    async def update_single_holding(self, session, holding, current_prices):
        """PART V: Order Execution"""
        """PART VI: Profitability Analysis and Order Generation """
        try:
            aggregated_data = await self.aggregate_trade_data_for_symbol(session, holding['Currency'])
            if aggregated_data is None:
                return

            stmt = select(Holding).where(Holding.currency == holding['Currency'])
            result = await session.execute(stmt)
            existing_holding = result.scalars().first()

            if not existing_holding:
                # Create new holding if it does not exist
                new_holding = Holding(
                    currency=holding['Currency'],
                    asset=holding['asset'],
                    balance=holding['Balance'],
                    average_cost=aggregated_data['average_cost'],
                    purchase_date=aggregated_data['most_recent_trade_time'],
                    purchase_price=aggregated_data['purchase_price'],  # Default to 0 if not found
                    purchase_amount=holding.get('Balance', 0),
                    total_cost=holding.get('total_cost', 0),
                )
                session.add(new_holding)
            else:
                # Get from current_prices or fallback to existing
                current_price = current_prices.get(holding['asset'], existing_holding.current_price)
                # Update existing holding with new data
                update_fields = {
                    'balance': holding['Balance'],
                    'current_price': current_price,
                    # Use existing price as fallback
                    'purchase_date': aggregated_data['most_recent_trade_time'],
                    'average_cost': aggregated_data.get('average_cost', existing_holding.average_cost),
                    'unrealized_profit_loss': holding.get('unrealized_profit_loss', existing_holding.unrealized_profit_loss),
                    'unrealized_pct_change': holding.get('unrealized_pct_change', existing_holding.unrealized_pct_change),
                    'total_cost': aggregated_data.get('total_cost', existing_holding.total_cost),
                }
                for key, value in update_fields.items():
                    setattr(existing_holding, key, value)

        except Exception as e:
            self.log_manager.sighook_logger.error(f'update_single_holding: {e}', exc_info=True)

    @staticmethod
    async def get_updated_holdings(session):
        """PART VI: Profitability Analysis and Order Generation  Fetch the updated contents of the holdings table using
        the provided session."""
        result = await session.execute(select(Holding))
        return result.scalars().all()

    async def process_holdings(self, session: AsyncSession, holding, current_prices):
        """PART V: Order Execution"""
        """PART VI: Profitability Analysis and Order Generation """
        for coin in holding:
            try:
                await self.update_single_holding(session, holding, current_prices)
                await session.commit()  # Commit after each successful processing

            except Exception as e:
                await session.rollback()  # Roll back only the current holding processing
                self.log_manager.sighook_logger.error(f'Error processing holding for {coin["symbol"]}: {e}', exc_info=True)

    from decimal import Decimal

    async def aggregate_trade_data_for_symbol(self, session: AsyncSession, asset: str):
        """PART VI: Profitability Analysis and Order Generation """
        # Aggregate trade data for the given symbol, considering only 'buy' trades for purchase data
        try:
            # find the most recent trade time for the asset
            most_recent_time = await session.execute(
                select(func.max(Trade.trade_time))
                .filter(Trade.asset == asset, Trade.transaction_type.like('%buy%'))
            )
            most_recent_time = most_recent_time.scalar()
            # aggregate the other trade data
            aggregation_query = (
                select(
                    func.min(Trade.trade_time).label('earliest_trade_time'),
                    func.sum(Trade.amount).label('total_amount'),
                    func.sum(Trade.cost).label('total_cost'),
                )
                .filter(Trade.asset == asset, Trade.transaction_type.like('%buy%'))
                .group_by(Trade.asset)
            )
            aggregation = await session.execute(aggregation_query)
            aggregation = aggregation.one_or_none()

            # Convert Decimal to float for easier comparison and handling
            if aggregation:
                total_amount = float(aggregation.total_amount) if aggregation.total_amount is not None else 0
                if total_amount > 0:
                    # Calculate weighted average price (total cost / total amount)
                    weighted_average_price = aggregation.total_cost / Decimal(total_amount)
                    # fetch the price at the most recent trade time
                    most_recent_price_query = select(Trade.price).filter(
                        Trade.asset == asset,
                        Trade.trade_time == most_recent_time,
                        Trade.transaction_type.like('%buy%')
                    )
                    most_recent_price = await session.execute(most_recent_price_query)
                    most_recent_price = most_recent_price.scalar()
                    return {
                        'earliest_trade_time': aggregation.earliest_trade_time,
                        'most_recent_trade_time': most_recent_time,
                        'total_amount': total_amount,
                        'total_cost': aggregation.total_cost,
                        'average_cost': weighted_average_price,  # This now also represents the purchase price
                        'purchase_price': most_recent_price,  # Explicitly stating it as purchase_price for clarity
                    }
                else:
                    # Handle the case where there are no 'buy' trades for the given symbol
                    self.log_manager.sighook_logger.debug(
                        f"No valid trades found for asset {asset}. Total amount: {total_amount}")
                    return None
            else:
                # Handle the case where no aggregation data is found
                self.log_manager.sighook_logger.debug(f"No aggregation data found for asset {asset}")
                return None
        except Exception as e:
            self.log_manager.sighook_logger.debug(f'aggregate_trade_data_for_symbol:  {e}', exc_info=True)
            return None

    async def process_sell_order_fifo(self, session, asset, sell_amount, sell_price):
        """PART VI: Profitability Analysis and Order Generation """
        try:
            buy_trades = await session.execute(
                select(Trade).filter(Trade.asset == asset, Trade.transaction_type == 'buy').order_by(Trade.trade_time.asc()))
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
                    currency=asset,
                    profit_loss=realized_profit,
                    sell_amount=available_for_sale,
                    sell_price=sell_price,
                    timestamp=dt.utcnow()
                )
                session.add(new_realized_profit)

            return total_realized_profit
        except Exception as e:

            self.log_manager.sighook_logger.error(f"Error processing sell order FIFO for {asset}: {e}", exc_info=True)
            raise  # Allow the calling function to handle the rollback

    async def get_last_update_time_for_symbol(self, session, asset):
        """Part I & PART VI: Profitability Analysis and Order Generation """
        """Retrieve the time of each symbol's most recent closed trade from the database.

    Parameters:
    - session (AsyncSession): The SQLAlchemy asynchronous session.
    - symbol (str): The trading symbol to query the last update time for.

    Returns:
    - datetime: The last update time for the symbol, or a default datetime if not found."""
        try:
            # Query the database for the symbol's last update time
            symbol_update = await session.get(SymbolUpdate, asset)
            # The session is already managed by the calling function

            if symbol_update:
                return symbol_update.last_update_time
            else:
                return dt(2017, 12, 1)  # Example default date
        except Exception as e:
            # Log the error and decide on the appropriate error handling strategy
            self.log_manager.sighook_logger.error(f'Error getting last update time for {asset}: {e}', exc_info=True)
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
            await session.rollback()
            raise

    async def fetch_trades(self, session: AsyncSession, symbol: str,
                           last_update: Optional[datetime.datetime] = None) -> List[dict]:

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
                    last_update = datetime.datetime(2017, 12, 1)
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
            latest_time = self.utility.convert_timestamp(last_update_unix)
            if new_trades:
                print(f'New trades since {latest_time} for {symbol}')
                await self.set_last_update_time(session, symbol, new_trades[-1]['trade_time'])
            else:
                print(f'No new trades since {latest_time} for {symbol}')  # Debug message
            return new_trades

        except Exception as e:
            self.log_manager.sighook_logger.error(f'Error fetching new trades for {symbol}: {e}', exc_info=True)
            return []

    def process_trade_data(self, trade):
        """PART VI: Profitability Analysis and Order Generation """
        try:
            # Initialize fee_cost
            fee_cost = None

            # Extract the trade time, handling the case where 'info' may or may not be present
            if 'info' in trade and 'trade_time' in trade['info']:
                trade_time_str = trade['info']['trade_time']
                # Truncate the string to limit the number of decimal places to 6
                trade_time_str = trade_time_str.split('.')[0] + '.' + trade_time_str.split('.')[1][:6]
                trade_time = dt.fromisoformat(trade_time_str.rstrip("Z"))  # Assuming ISO format string
            elif 'trade_time' in trade and isinstance(trade['trade_time'], dt):
                trade_time = trade['trade_time']  # Directly use the datetime object
            else:
                self.log_manager.sighook_logger.error(
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
                    self.log_manager.sighook_logger.error(f"Unexpected 'fee' format in trade data for {trade.get('asset')}")

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
            self.log_manager.sighook_logger.error(f'process_trade_data: {e}', exc_info=True)
            return None
        # Perform any necessary validation or transformation on the extracted data
        # For example, you might want to ensure that 'side' is either 'buy' or 'sell'
