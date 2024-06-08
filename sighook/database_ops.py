from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.future import select
from sqlalchemy.orm.attributes import flag_modified
from collections import defaultdict
from sqlalchemy.sql import func, and_
from sqlalchemy import delete, case
from database_table_models import Trade, SymbolUpdate, NewTrade, Holding, RealizedProfit
from datetime import datetime as dt
import pytz
from typing import Optional, List
from decimal import Decimal, ROUND_DOWN
import asyncio
import datetime


class DatabaseOpsManager:
    def __init__(self, debugger_function, db_tables, utility, exchange, ccxt_api,  log_manager, ticker_manager,
                 portfolio_manager, app_config):

        self.log_manager = log_manager
        self.exchange = exchange
        self.debug_func = debugger_function
        self.db_tables = db_tables
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
        await session.commit()

    @staticmethod
    async def clear_holdings(session: AsyncSession):
        """Clear all entries in the holdings table."""
        await session.execute(delete(Holding))
        await session.commit()

    async def process_market_data(self, session: AsyncSession, market_cache):
        """PART I: Data Gathering and Database Loading. Process all symbols in the market cache concurrently using a
        single session and transaction."""
        try:
            result = await session.execute(select(SymbolUpdate))
            symbol_updates = result.scalars().all()
            last_update_by_symbol = {update.symbol: update.last_update_time for update in symbol_updates}
            for symbol, date_time in last_update_by_symbol.items():
                if date_time.tzinfo is None:
                    last_update_by_symbol[symbol] = date_time.replace(tzinfo=pytz.UTC)

            default_date = datetime.datetime(2017, 12, 1).replace(tzinfo=pytz.UTC)  # Define the default date
            tasks = []
            for market in market_cache:
                market_symbol = market['symbol']
                last_update_time = last_update_by_symbol.get(market_symbol, default_date)
                task = self.process_symbol(session, market, last_update_time)
                tasks.append(task)

            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    continue
                symbol, last_processed_time = result
                if symbol not in ["USDT/USDT", "USD/USD"] and last_processed_time != default_date:
                    await self.set_last_update_time(session, symbol, last_processed_time)

        except Exception as e:
            self.log_manager.sighook_logger.error(f"Error processing market data: {e}", exc_info=True)
            await session.rollback()
            raise

    async def process_symbol(self, session: AsyncSession, market, last_update_time):
        """PART I: Data Gathering and Database Loading. Process a single symbol's market data."""
        try:
            # await self.log_all_trades(session, "Before processing symbol") # debug
            most_recent_trade_time = None
            trades = await self.portfolio_manager.get_my_trades(market['symbol'], last_update_time)
            if not trades:
                return market['symbol'], last_update_time  # No new trades, return current last_update_time

            self.log_manager.sighook_logger.debug(f"Fetched {len(trades)} trades for {market['symbol']}")  # debug
            trade_objects = []
            temp_trade_objects = []

            # Get the last trade for this asset to determine the starting balance
            last_trade_query = (
                select(Trade)
                .filter(Trade.asset == market['asset'].split('/')[0])
                .order_by(Trade.trade_time.desc())
                .limit(1)
            )
            last_trade_result = await session.execute(last_trade_query)
            last_trade = last_trade_result.scalar_one_or_none()
            starting_balance = last_trade.balance if last_trade else 0.0

            for trade in trades:
                self.log_manager.sighook_logger.debug(f"Processing trade: {trade}")  # debug
                if 'id' not in trade or not trade['id']:
                    self.log_manager.sighook_logger.error("Missing trade ID")
                    continue

                # Round trade times to the nearest second for comparison
                trade_time = trade['datetime'].replace(microsecond=0)
                trade_amount = float(trade['amount'])
                trade_price = float(trade['price'])
                trade_amount = trade_amount if trade['side'].lower() == 'buy' else -trade_amount

                # Log the trade data being compared
                self.log_manager.sighook_logger.debug(
                    f"Comparing trade: time={trade_time}, asset={market['asset'].split('/')[0]}, amount={trade_amount}, "
                    f"price={trade_price}")

                # Check for existing trade using key attributes with tolerance for timestamp and numeric values
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

                # Log the existing trade data
                if existing_trade:
                    self.log_manager.sighook_logger.debug(
                        f"Existing trade found: time={existing_trade.trade_time}, asset={existing_trade.asset}, "
                        f"amount={existing_trade.amount}, price={existing_trade.price}")
                else:
                    self.log_manager.sighook_logger.debug("No matching trade found.")

                if existing_trade:
                    continue

                # Calculate the new balance
                starting_balance += trade_amount
                trade_obj = await self.create_trade_object(trade, market['asset'], starting_balance)  # Create a Trade object
                if trade_obj.amount == 0.0:
                    self.log_manager.sighook_logger.error(f"Trade amount is zero: {trade}")
                    continue

                trade_objects.append(trade_obj)

                if trade['datetime'] > last_update_time:
                    temp_trade_object = self.create_temp_trade(trade_obj)  # Add to temporary table
                    temp_trade_objects.append(temp_trade_object)
                # Update most recent trade time if this trade is newer
                if not most_recent_trade_time or trade_obj.trade_time > most_recent_trade_time:
                    most_recent_trade_time = trade_obj.trade_time

            if trade_objects:
                session.add_all(trade_objects)  # add all trade objects to the session trades table
                self.log_manager.sighook_logger.debug(f"Added {len(trade_objects)} trade objects to session")

            if temp_trade_objects:
                session.add_all(temp_trade_objects)  # add all trade objects to the session trades_new table
                self.log_manager.sighook_logger.debug(f"Added {len(temp_trade_objects)} temp trade objects to session")

            # await self.log_all_trades(session, "After processing symbol") # debug

            return market['symbol'], most_recent_trade_time if most_recent_trade_time else last_update_time

        except Exception as e:
            self.log_manager.sighook_logger.error(f"Error processing symbol {market['asset']}: {e}", exc_info=True)
            await session.rollback()
            raise

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

            return Trade(
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
        except Exception as e:
            self.log_manager.sighook_logger.error(f"Error creating trade object: {e}", exc_info=True)
            return None

    @staticmethod
    def create_temp_trade(trade_obj):
        """Creates a temporary trade object from a persistent Trade object."""
        # Create a new trade object, excluding '_sa_instance_state' and other unwanted attributes
        trade_data = {key: value for key, value in trade_obj.__dict__.items() if key != '_sa_instance_state'}
        return NewTrade(**trade_data)

    @staticmethod
    def is_new_trade(trade_datetime, last_update_time):
        """Determine if a trade is new based on the given datetime."""

        return trade_datetime > last_update_time

    async def initialize_holding_db(self, session, holdings, current_prices=None):
        """Handle the initialization or update of holdings in the database based on provided data."""
        try:
            for holding in holdings:
                await self.update_single_holding(session, holding, current_prices)
            pass
        except Exception as e:
            self.log_manager.sighook_logger.error(f'initialize_holding_db: {e}', exc_info=True)
            await session.rollback()

    async def update_single_holding(self, session, holding, current_prices):
        """PART V: Order Execution"""
        """PART VI: Profitability Analysis and Order Generation """
        try:

            aggregated_data = await self.aggregate_trade_data_for_symbol(session, holding['asset'])
            if aggregated_data is None:
                return

            stmt = select(Holding).where(Holding.asset == holding['asset'], Holding.currency == holding['quote_currency'])
            result = await session.execute(stmt)
            existing_holding = result.scalars().first()
            symbol = holding['asset'] + '/' + holding['quote_currency']
            current_price = current_prices.get(symbol)
            if not existing_holding:
                # Create new holding if it does not exist
                new_holding = Holding(
                    currency=holding['quote_currency'],
                    asset=holding['asset'],
                    purchase_date=aggregated_data['most_recent_trade_time'],
                    purchase_price=aggregated_data['purchase_price'],  # Default to 0 if not found
                    current_price=current_price,  # Default to 0 if not found
                    purchase_amount=float(holding.get('Balance', 0)),
                    initial_investment=aggregated_data['purchase_price'] * float(holding['Balance']),
                    market_value=float(holding['Balance']) * float(current_prices.get(holding['asset'], 0)),
                    balance=float(holding['Balance']),
                    weighted_average_cost=aggregated_data['weighted_average_cost'],
                    unrealized_profit_loss=holding.get('unrealized_profit_loss', 0),
                    unrealized_pct_change=holding.get('unrealized_pct_change', 0),

                )
                session.add(new_holding)
            else:
                # Get from current_prices or fallback to existing

                # Update existing holding with new data
                update_fields = {
                    'purchase_date': aggregated_data['most_recent_trade_time'],
                    'purchase_price': aggregated_data['purchase_price'],
                    'balance': holding['Balance'],
                    'current_price': current_price,
                    # Use existing price as fallback
                    'initial_investment': aggregated_data['purchase_price']*holding['Balance'],
                    'weighted_average_cost': aggregated_data.get('weighted_average_cost',
                                                                 existing_holding.weighted_average_cost),
                    'market_value': holding['Balance'] * current_price,
                    'unrealized_profit_loss': holding.get('unrealized_profit_loss', existing_holding.unrealized_profit_loss),
                    'unrealized_pct_change': holding.get('unrealized_pct_change', existing_holding.unrealized_pct_change),
                }
                for key, value in update_fields.items():
                    setattr(existing_holding, key, value)

        except Exception as e:
            self.log_manager.sighook_logger.error(f'update_single_holding: {e}', exc_info=True)
            await session.rollback()  # Roll back only the current holding processing

    @staticmethod
    async def get_updated_holdings(session):
        """PART VI: Profitability Analysis and Order Generation  Fetch the updated contents of the holdings table using
        the provided session."""
        result = await session.execute(select(Holding))
        return result.scalars().all()

    async def aggregate_trade_data_for_symbol(self, session: AsyncSession, asset: str):
        """PART VI: Profitability Analysis and Order Generation """
        try:
            # Find the most recent trade time for the asset
            most_recent_time_result = await session.execute(
                select(func.max(Trade.trade_time)).filter(Trade.asset == asset)
            )
            most_recent_time = most_recent_time_result.scalar()

            # Aggregate trade data from the trades table
            aggregation_query = (
                select(
                    func.min(Trade.trade_time).label('earliest_trade_time'),
                    func.max(Trade.trade_time).label('most_recent_trade_time'),
                    func.sum(case((Trade.amount > 0, Trade.amount), else_=0)).label('purchase_amount'),
                    func.sum(case((Trade.amount < 0, Trade.amount), else_=0)).label('sold_amount'),
                    func.sum(Trade.total).label('total'),
                    func.sum(Trade.amount).label('amount'),
                    func.sum(Trade.balance).label('balance'),
                    func.sum(Trade.cost).label('cost'),
                    func.sum(Trade.fee).label('fee'),
                    func.sum(Trade.proceeds).label('proceeds'),
                    func.sum(case((Trade.amount != 0, Trade.total), else_=0)).label('initial_investment'),
                )
                .filter(Trade.asset == asset)
                .group_by(Trade.asset)
            )
            aggregation_result = await session.execute(aggregation_query)
            aggregation = aggregation_result.one_or_none()

            # Fetch the most recent non-zero cost entry for the asset
            most_recent_trade_id = (
                select(Trade)
                .filter(Trade.asset == asset, Trade.order_id)
                .order_by(Trade.trade_time.desc())
                .limit(1)
            )
            most_recent_cost_query = (
                select(Trade)
                .filter(Trade.asset == asset, Trade.cost < 0)
                .order_by(Trade.trade_time.desc())
                .limit(1)
            )
            most_recent_proceeds_query = (
                select(Trade)
                .filter(Trade.asset == asset, Trade.proceeds > 0)
                .order_by(Trade.trade_time.desc())
                .limit(1)
            )
            most_recent_buy_result = await session.execute(most_recent_cost_query)
            most_recent_buy = most_recent_buy_result.scalar_one_or_none()
            most_recent_sell_result = await session.execute(most_recent_proceeds_query)
            most_recent_sell = most_recent_sell_result.scalar_one_or_none()

            # Debugging output for most recent buy and sell trades
            if most_recent_buy and most_recent_sell:
                self.log_manager.sighook_logger.debug(
                    f'Most recent buy trade: {most_recent_buy.trade_time}, amount: {most_recent_buy.amount}')
                self.log_manager.sighook_logger.debug(
                    f'Most recent sell trade: {most_recent_sell.trade_time}, amount: {most_recent_sell.amount}')
            if most_recent_buy.amount == 0.0:
                pass
            # Fetch all trades for the asset to verify totals
            all_trades_query = (
                select(Trade)
                .filter(Trade.asset == asset)
                .order_by(Trade.trade_time)
            )
            all_trades_result = await session.execute(all_trades_query)
            all_trades = all_trades_result.fetchall()

            # Aggregate trades by order_id
            # Aggregate trades by order_id
            trades_by_order = defaultdict(lambda: {'amount': 0.0, 'cost': 0.0, 'proceeds': 0.0})

            for trade_tuple in all_trades:
                trade = trade_tuple[0]
                trades_by_order[trade.order_id]['amount'] += float(trade.amount)
                if trade.amount == 0.0:
                    pass
                trades_by_order[trade.order_id]['cost'] += float(trade.cost)
                trades_by_order[trade.order_id]['proceeds'] += float(trade.proceeds)

            total_amount = sum(order['amount'] for order in trades_by_order.values())
            if total_amount == 0.0:
                print(f"Total amount is zero for asset {asset}")
                pass  # debug
            total_cost = sum(order['cost'] for order in trades_by_order.values())
            total_proceeds = sum(order['proceeds'] for order in trades_by_order.values())

            self.log_manager.sighook_logger.debug(f'Total amount for all trades: {total_amount}')
            self.log_manager.sighook_logger.debug(f'Total cost for all trades: {total_cost}')
            self.log_manager.sighook_logger.debug(f'Total proceeds for all trades: {total_proceeds}')

            # Separate purchases and sales for further analysis
            purchase_amount = sum(order['amount'] for order in trades_by_order.values() if order['amount'] > 0)
            sold_amount = sum(order['amount'] for order in trades_by_order.values() if order['amount'] < 0)

            self.log_manager.sighook_logger.debug(f'Purchase amount for all trades: {purchase_amount}')
            self.log_manager.sighook_logger.debug(f'Sold amount for all trades: {sold_amount}')

            if aggregation and aggregation.purchase_amount and aggregation.purchase_amount > 0:
                # Calculate net balance
                net_balance = aggregation.purchase_amount + aggregation.sold_amount
                balance = aggregation.balance
                # Fetch the purchase price from the most recent non-zero cost entry
                purchase_price = most_recent_buy.price if most_recent_buy else 0

                # Calculate initial investment using the simplified approach
                initial_investment = purchase_price * aggregation.purchase_amount

                return {
                    'earliest_trade_time': aggregation.earliest_trade_time,
                    'most_recent_trade_time': aggregation.most_recent_trade_time,
                    'purchase_amount': aggregation.purchase_amount,
                    'sold_amount': aggregation.sold_amount,
                    'initial_investment': initial_investment,
                    'net_balance': net_balance,
                    'balance': balance,
                    'market_value': total_cost,
                    'purchase_price': purchase_price,
                    'weighted_average_cost': initial_investment / abs(
                        aggregation.purchase_amount) if aggregation.purchase_amount else 0,
                    'entry_price': purchase_price,
                }
            else:
                # Log if no valid trades found
                self.log_manager.sighook_logger.debug(
                    f"No valid trades found for asset {asset}. Total amount: "
                    f"{aggregation.purchase_amount if aggregation else 'None'}")

                return None
        except Exception as e:
            self.log_manager.sighook_logger.debug(f'aggregate_trade_data_for_symbol error: {e}', exc_info=True)
            return None

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

                self.log_manager.sighook_logger.debug(
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
            self.log_manager.sighook_logger.error(f"Error processing sell order FIFO for {asset}: {e}", exc_info=True)
            raise  # Allow the calling function to handle the rollback

    async def log_all_trades(self, session, log_point):
        try:
            trades = await session.execute(select(Trade))
            trades = trades.scalars().all()
            for trade in trades:
                if trade.asset == 'BTC' or trade.asset == 'MNDE':
                    if trade.amount == 0:
                        self.log_manager.sighook_logger.debug(f"{log_point} - Asset: {trade.asset} Trade ID:"
                                                              f" {trade.trade_id}, Amount: {trade.amount}, Transaction Type:"
                                                              f" {trade.transaction_type}")
        except Exception as e:
            self.log_manager.sighook_logger.error(f"Error logging all trades at {log_point}: {e}")

    async def get_last_update_time_for_symbol(self, session, asset):
        """Part I & PART VI: Profitability Analysis and Order Generation """
        """Retrieve the time of each symbol's most recent closed trade from the database.

    Parameters:
    - session (AsyncSession): The SQLAlchemy asynchronous session.
    - symbol (str): The trading symbol to query the last update time for.

    Returns:
    - datetime: The last update time for the symbol, or a default datetime if not found."""
        aware_datetime = dt(2017, 12, 1).replace(tzinfo=pytz.UTC)
        try:
            # Query the database for the symbol's last update time
            symbol_update = await session.get(SymbolUpdate, asset)
            # The session is already managed by the calling function
            if symbol_update:
                return symbol_update.last_update_time
            else:
                return aware_datetime  # Example default date
        except Exception as e:
            # Log the error and decide on the appropriate error handling strategy
            self.log_manager.sighook_logger.error(f'Error getting last update time for {asset}: {e}', exc_info=True)
            # Depending on your error handling strategy, you might return a default value or re-raise the exception
            return aware_datetime  # Return a default date as a fallback

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
            self.log_manager.sighook_logger.error(f"Error setting last update time for {symbol}: {e}", exc_info=True)
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
            latest_time = self.utility.convert_timestamp(last_update_unix)
            if new_trades:
                print(f'New trades since {latest_time} for {symbol}')
                await self.set_last_update_time(session, symbol, new_trades[-1]['trade_time'])
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

    # <><><>><><><> TRouble shooting functions that may be deleted when no longer needed <><><><><
