from datetime import datetime
import asyncio
from decimal import Decimal
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, List
from database_manager import Base
from database_manager import Trade, Holding, RealizedProfit, ProfitData


class ProfitabilityManager:
    def __init__(self, exchange, ccxt_api, utility, database_manager, order_manager, portfolio_manager,
                 trading_strategy, profit_helper, logmanager, config):

        self.exchange = exchange
        self.ccxt_exceptions = ccxt_api
        self._take_profit = Decimal(config.take_profit)
        self._stop_loss = Decimal(config.stop_loss)
        self.database_dir = config.database_dir
        self.sqlite_db_path = config.sqlite_db_path
        self.ledger_cache = None
        self.utility = utility
        self.database_manager = database_manager
        self.order_manager = order_manager
        self.portfolio_manager = portfolio_manager
        self.trading_strategy = trading_strategy
        self.profit_helper = profit_helper
        self.log_manager = logmanager
        self.ticker_cache = None
        self.session = None
        self.market_cache = None
        self.start_time = None
        self.web_url = None
        self.holdings = None

        self.engine = create_engine(f'sqlite:///{self.sqlite_db_path}')  # Use SQLAlchemy engine with the correct URI
        Base.metadata.create_all(self.engine)  # Create tables based on models
        self.Session = sessionmaker(bind=self.engine)

    def set_trade_parameters(self, start_time, ticker_cache, market_cache, web_url, hist_holdings):
        self.start_time = start_time
        # self.session = session
        self.ticker_cache = ticker_cache
        self.market_cache = market_cache
        self.web_url = web_url
        self.holdings = hist_holdings

    @property
    def stop_loss(self):
        return self._stop_loss

    @property
    def take_profit(self):
        return self._take_profit

    async def check_profit_level(self, holdings):  # async
        """PART VI: Profitability Analysis and Order Generation """
        try:
            async with self.database_manager.AsyncSession() as session:
                async with session.begin():
                    # Update and process holdings
                    aggregated_df = await self.update_and_process_holdings(session, holdings)  # await
                    # Fetch current market prices for these symbols
                    symbols = [holding['symbol'] for holding in holdings]
                    current_market_prices = await self.profit_helper.fetch_current_market_prices(symbols)  # await
                    #self.profit_extras.create_performance_snapshot(session, current_market_prices)  # Create a snapshot of current
                    # portfolio
                    # performance
                    return aggregated_df
        except Exception as e:
            self.log_manager.sighook_logger.error(f'check_profit_level: {e} e', exc_info=True)
        finally:
            await self.session.close()

    async def update_and_process_holdings(self, session, holdings_list):
        """PART VI: Profitability Analysis and Order Generation """
        try:

            # Load or update holdings
            aggregated_df = await self.database_manager.update_holdings_from_list(session, holdings_list)
            current_prices = await self.profit_helper.calculate_unrealized_profit_loss(session)
            await self.check_and_execute_sell_orders(session, current_prices, holdings_list)  # await

            #await self.order_manager.process_sell_orders(session, product_id, current_price, holdings, trigger)  # await

            # # # Fetch new trades for all currencies in holdings
            # currency = [item['Currency'] for item in holdings_list]
            # symbols = [item['symbol'] for item in holdings_list]
            # all_new_trades = await self.fetch_new_trades_for_symbols(session, symbols)  # await
            # #
            # # # Process new trades for each currency from the dictionary of all new trades
            # for currency, new_trades in all_new_trades.items():
            #     self.process_new_trades_for_currency(currency, new_trades)
            # return aggregated_df

        except Exception as e:
            self.log_manager.sighook_logger.error(f'update_and_process_holdings: {e}', exc_info=True)
            await session.rollback()  # Rollback the session in case of an error

    async def check_and_execute_sell_orders(self, session, current_prices, holdings_list):
        """PART VI: Profitability Analysis and Order Generation """
        try:
            realized_profit = 0
            # Fetch holdings from the database

            holdings = await session.execute(select(Holding))
            holdings = holdings.scalars().all()

            for holding in holdings:
                current_market_price = current_prices.get(holding.symbol, 0)
                if self.profit_helper.should_place_sell_order(holding, current_market_price):
                    sell_amount = holding.balance  # or any other logic to determine the amount to sell
                    sell_price = Decimal(current_market_price)  # or any other logic to determine the sell price
                    realized_profit = await self.database_manager.process_sell_order_fifo(session, holding.symbol,
                                                                                          sell_amount, sell_price)
                    if realized_profit > 0:
                        trigger = 'profit'
                    else:
                        trigger = 'loss'
                    symbol = holding.symbol
                    action = 'sell'
                    sell_price = current_market_price
                    # holdings = self.profit_helper.get_holdings(session)
                    # Construct the 'order' dictionary for this holding
                    order = {
                        'symbol': holding.symbol,
                        'action': 'sell',
                        'price': sell_price,
                        'trigger': trigger,  # Define how you determine the trigger
                        'bollinger_df': None,  # If applicable
                        'action_data': {
                            'action': 'sell',
                            'trigger': trigger,  # Define how you determine the trigger
                            'updates': {
                                holding.currency: {
                                    # Include relevant action data here
                                    'Sell Signal': trigger  # Define how you determine the sell signal
                                }
                            },
                            'sell_cond': trigger  # Define how you determine the sell condition
                        }
                    }

                    await self.order_manager.handle_actions(order, holdings_list)
                    # Log or take further action based on realized_profit...
                    # Update or delete the holding record as necessary...

        except Exception as e:
            self.log_manager.sighook_logger.error(f'check_and_execute_sell_orders:  {e}', exc_info=True)
            raise

    async def fetch_new_trades_for_symbols(self, session, symbols):
        """PART VI: Profitability Analysis and Order Generation """
        all_new_trades = {}
        for symbol in symbols:
            try:
                # Determine the last update time for this symbol
                last_update = await self.profit_helper.get_last_update_time_for_symbol(session, symbol)

                # Fetch new trades from the exchange since the last update time
                raw_trades = await self.fetch_trades(session, symbol, last_update)

                # Process raw trade data into a standardized format
                new_trades = [self.profit_helper.process_trade_data(trade) for trade in raw_trades]

                # Update the last update time for the symbol if new trades were fetched
                if new_trades:
                    await self.profit_helper.set_last_update_time(session, symbol, new_trades[-1]['trade_time'])

                all_new_trades[symbol] = new_trades

            except Exception as e:
                self.log_manager.sighook_logger.error(f'Error fetching new trades for {symbol}: {e}', exc_info=True)
                # Depending on your error handling strategy, you might choose to continue to the next symbol or halt the process
                raise
        return all_new_trades

    async def fetch_trades(self,  session: AsyncSession, symbol: str, last_update: Optional[datetime] = None) -> List[dict]:
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
                    last_update = datetime(2017, 12, 1)
                else:
                    # Asynchronously fetch the most recent trade for the symbol
                    most_recent_trade = await session.execute(
                        select(Trade)
                        .filter(Trade.symbol == symbol)
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
                new_trades = [self.profit_helper.process_trade_data(trade) for trade in raw_trades if trade]
            else:
                return []
            # Update the last update time if new trades were fetched
            latest_time = self.utility.convert_timestamp(last_update_unix)
            if new_trades:
                print(f'New trades since {latest_time} for {symbol}')
                await self.profit_helper.set_last_update_time(session, symbol, new_trades[-1]['trade_time'])
            else:
                print(f'No new trades since {latest_time} for {symbol}')  # Debug message
            return new_trades

        except Exception as e:
            self.log_manager.sighook_logger.error(f'Error fetching new trades for {symbol}: {e}', exc_info=True)
            return []


    #  <><><><><><><><><><><><><><><><><><><>><><><><><><><><><><><><><><><><><><><>><>><><><><><><><><><><><><><><><><><><><>


    def process_trade(self, session, symbol, new_trades):
        for trade in new_trades:
            # Assuming 'process_trade_data' converts API trade data to your application's format
            processed_trade = self.profit_helper.process_trade_data(trade)

            if processed_trade['side'] == 'buy':
                self.profit_helper.update_holding_from_buy(session, symbol, processed_trade)
            elif processed_trade['side'] == 'sell':
                pass
                # call handle_action to process the sell trade


        # Implement logic to handle sell trades, potentially recording realized profits

        # Consider adding the processed_trade to your Trade table here

        # Update Holding's last update time, average cost, etc., outside this loop for efficiency


