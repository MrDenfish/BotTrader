from datetime import datetime
import asyncio
from decimal import Decimal
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, List
from old_database_manager import Base
from old_database_manager import Trade, Holding, RealizedProfit, ProfitData


class ProfitabilityManager:
    def __init__(self, exchange, ccxt_api, utility, portfolio_manager, database_session_mngr, order_manager,
                 trading_strategy, profit_helper, profit_extras, logmanager, app_config):

        self.exchange = exchange
        self.ccxt_exceptions = ccxt_api
        self._take_profit = Decimal(app_config.take_profit)
        self._stop_loss = Decimal(app_config.stop_loss)
        self.database_dir = app_config.database_dir
        self.sqlite_db_path = app_config.sqlite_db_path
        self.ledger_cache = None
        self.utility = utility
        self.database_manager = database_session_mngr
        self.order_manager = order_manager
        self.portfolio_manager = portfolio_manager
        self.trading_strategy = trading_strategy
        self.profit_helper = profit_helper
        self.profit_extras = profit_extras
        self.log_manager = logmanager
        self.ticker_cache = None
        self.session = None
        self.market_cache = None
        self.start_time = None
        self.web_url = None
        self.holdings = None

        # self.engine = create_engine(f'sqlite:///{self.sqlite_db_path}')  # Use SQLAlchemy engine with the correct URI
        # Base.metadata.create_all(self.engine)  # Create tables based on models
        # self.Session = sessionmaker(bind=self.engine)

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
        # await self.database_session_mngr.process_holding_db(holdings, self.start_time)
        try:
            # Update and process holdings
            aggregated_df = await self.update_and_process_holdings(holdings)  # await
            # Fetch current market prices for these symbols
            symbols = [holding['symbol'] for holding in holdings]
            current_market_prices = await self.profit_helper.fetch_current_market_prices(symbols)  # await
            # self.profit_extras.create_performance_snapshot(session, current_market_prices)  # Create a snapshot
            # of current portfolio performance

            return aggregated_df
        except Exception as e:
            self.log_manager.sighook_logger.error(f'check_profit_level: {e} e', exc_info=True)

    async def update_and_process_holdings(self, holdings_list):
        """PART VI: Profitability Analysis and Order Generation """
        try:

            # Load or update holdings
            aggregated_df = await self.database_manager.process_holding_db(holdings_list)
            updated_holdings_df = await self.profit_helper.calculate_unrealized_profit_loss(aggregated_df)
            await self.check_and_execute_sell_orders(updated_holdings_df)  # await

            # await self.order_manager.process_sell_orders(session, product_id, current_price, holdings, trigger)  # await

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

    import pandas as pd
    from decimal import Decimal

    async def check_and_execute_sell_orders(self, updated_holdings_df):
        """PART VI: Profitability Analysis and Order Generation"""
        try:
            realized_profit = 0
            updated_holdings_list = updated_holdings_df.to_dict('records')  # Convert DataFrame to list of dictionaries

            for holding in updated_holdings_list:
                symbol = holding['symbol']
                current_market_price = holding['current_price']
                if self.profit_helper.should_place_sell_order(holding, current_market_price):
                    sell_amount = holding['Balance']
                    sell_price = Decimal(current_market_price)

                    # Assuming process_sell_order_fifo is properly adjusted to handle the DataFrame format
                    realized_profit += \
                        await self.database_manager.sell_order_fifo(symbol, sell_amount, sell_price,updated_holdings_df,
                                                                    updated_holdings_list)

                    trigger = 'profit' if realized_profit > 0 else 'loss'
                    order = {
                        'symbol': holding['symbol'],
                        'action': 'sell',
                        'price': sell_price,
                        'trigger': trigger,
                        'bollinger_df': None,  # If applicable
                        'action_data': {
                            'action': 'sell',
                            'trigger': trigger,
                            'updates': {
                                holding['Currency']: {
                                    'Sell Signal': trigger
                                }
                            },
                            'sell_cond': trigger
                        },
                        'value': holding['Balance'] * sell_price  # Calculate the value of the order
                    }

                    # Here, handle_actions needs to accept order and holdings_list
                    await self.order_manager.handle_actions(order, updated_holdings_list)

            return realized_profit  # It might be useful to return the realized profit
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
