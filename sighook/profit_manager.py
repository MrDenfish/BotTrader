import logging
from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker
from decimal import Decimal, ROUND_DOWN
from database_manager import Base
from database_manager import Trade, Holding, RealizedProfit, ProfitData
from datetime import datetime
from dateutil import parser
import asyncio
import pandas as pd
import traceback


class ProfitabilityManager:
    def __init__(self, exchange, ccxt_api, utility, database_manager, order_manager, portfolio_manager, profit_helper,
                 logmanager, config):

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

    @property
    def stop_loss(self):
        return self._stop_loss

    @property
    def take_profit(self):
        return self._take_profit

    def set_trade_parameters(self, start_time, ticker_cache, market_cache, web_url, hist_holdings):
        self.start_time = start_time
        # self.session = session
        self.ticker_cache = ticker_cache
        self.market_cache = market_cache
        self.web_url = web_url
        self.holdings = hist_holdings

    def check_profit_level(self, holdings):  # async
        session = self.Session()
        try:
            # Update and process holdings
            self.update_and_process_holdings(session, holdings)  # await
            # Fetch current market prices for these symbols
            # symbols = [holding['symbol'] for holding in holdings]
            # current_market_prices = self.profit_helper.fetch_current_market_prices(symbols)  # await
            session.commit()
            # self.create_performance_snapshot(session, current_market_prices)  # Create a snapshot of current portfolio
            # performance

        except Exception as e:
            session.rollback()
            error_details = traceback.format_exc()
            self.log_manager.sighook_logger.error(f'check_profit_level: {error_details},{e} e')
        finally:
            session.close()

    def update_and_process_holdings(self, session, holdings_list):
        try:
            # Load or update holdings
            self.profit_helper.update_holdings_from_list(session, holdings_list)
            current_prices = self.profit_helper.calculate_unrealized_profit_loss(session)
            realized_profit = self.check_and_execute_sell_orders(session, current_prices)  # await
            if realized_profit > 0:
                trigger = 'profit'
            else:
                trigger = 'loss'

            # self.order_manager.process_sell_orders(session, product_id, current_price, holdings, trigger)  # await

            # # Fetch new trades for all symbols in holdings
            # symbols = [item['symbol'] for item in holdings_list]
            # all_new_trades = self.fetch_new_trades_for_symbols(session, symbols)
            #
            # # Process new trades for each symbol from the dictionary of all new trades
            # for symbol, new_trades in all_new_trades.items():
            #     self.process_new_trades_for_symbol(session, symbol, new_trades)

        except Exception as e:
            error_details = traceback.format_exc()
            self.log_manager.sighook_logger.error(f'update_and_process_holdings: {error_details}, {e}')
            session.rollback()
            raise  # Re-raise the exception after logging

    def check_and_execute_sell_orders(self, session, current_prices):
        try:
            # Fetch holdings from the database
            holdings = session.query(Holding).all()

            for holding in holdings:
                current_market_price = current_prices.get(holding.currency, 0)
                if self.profit_helper.should_place_sell_order(holding, current_market_price):
                    sell_amount = holding.balance  # or any other logic to determine the amount to sell
                    sell_price = Decimal(current_market_price)  # or any other logic to determine the sell price
                    realized_profit = self.profit_helper.process_sell_order_fifo(session, holding.currency, sell_amount,
                                                                                 sell_price)
                    # Log or take further action based on realized_profit...
                    # Update or delete the holding record as necessary...

        except Exception as e:
            error_details = traceback.format_exc()
            self.log_manager.sighook_logger.error(f'check_and_execute_sell_orders: {error_details}, {e}')
            session.rollback()
            raise

    #  <><><><><><><><><><><><><><><><><><><>><><><><><><><><><><><><><><><><><><><>><>><><><><><><><><><><><><><><><><><><><>

    def fetch_new_trades_for_symbols(self, session, symbols):
        all_new_trades = {}
        for symbol in symbols:
            try:
                # Determine the last update time for this symbol
                last_update = self.profit_helper.get_last_update_time_for_symbol(session, symbol)

                # Fetch new trades from the exchange since the last update time
                raw_trades = self.fetch_trades(session, symbol, last_update)

                # Process raw trade data into a standardized format
                new_trades = [self.profit_helper.process_trade_data(trade) for trade in raw_trades]

                # Update the last update time for the symbol if new trades were fetched
                if new_trades:
                    self.profit_helper.set_last_update_time(session, symbol, new_trades[-1]['timestamp'])

                all_new_trades[symbol] = new_trades

            except Exception as e:
                error_details = traceback.format_exc()
                self.log_manager.sighook_logger.error(f'Error fetching new trades for {symbol}: {error_details}, {e}')
                # Depending on your error handling strategy, you might choose to continue to the next symbol or halt the process

        return all_new_trades

    def fetch_trades(self, session, symbol, last_update=None):  # async
        """trades that are not in the trades table and have occurred since the last update time.  This method should be
        called for each symbol in the portfolio."""

        if last_update is None:
            count = session.query(Holding).count()
            if count == 0:
                last_update = datetime(2017, 12, 1)
            else:
                most_recent_trade = session.query(Trade).filter(Trade.symbol == symbol).order_by(
                    Trade.trade_time.desc()).first()
                last_update = most_recent_trade.trade_time if most_recent_trade else None

        last_update = self.utility.time_unix(last_update.strftime("%Y-%m-%d %H:%M:%S.%f")) if last_update else None
        params = {
            'paginate': True,  # Enable automatic pagination
            'paginationCalls': 20  # Set the max number of pagination calls if necessary
        }

        # Fetch trades since the last update time
        raw_trades = self.ccxt_exceptions.ccxt_api_call(  # await
            self.exchange.fetch_my_trades,
            symbol=symbol,
            since=last_update,
            params=params
        )

        new_trades = []
        for trade in raw_trades:
            processed_trade = self.profit_helper.process_trade_data(trade)
            if processed_trade:
                new_trades.append(processed_trade)

        # Update the last update time with the timestamp of the latest trade
        latest_time = self.utility.convert_timestamp(last_update)
        if new_trades:
            print(f'New trades since {latest_time} for {symbol}')
            self.profit_helper.set_last_update_time(session, symbol, new_trades[-1]['timestamp'])
        else:
            print(f'No New trades since {latest_time} for {symbol}')  # debug

        return new_trades

    def process_trade(self, session, symbol, new_trades):
        for trade in new_trades:
            # Assuming 'process_trade_data' converts API trade data to your application's format
            processed_trade = self.profit_helper.process_trade_data(trade)

            if processed_trade['side'] == 'buy':
                self.profit_helper.update_holding_from_buy(session, symbol, processed_trade)
            elif processed_trade['side'] == 'sell':
                pass
        # Implement logic to handle sell trades, potentially recording realized profits

        # Consider adding the processed_trade to your Trade table here

        # Update Holding's last update time, average cost, etc., outside this loop for efficiency

    def process_new_trades_for_symbol(self, session, symbol, new_trades):
        try:
            for trade in new_trades:
                # Process the trade data, potentially transforming it to internal format
                # This step depends on the structure of your `new_trades` data
                processed_trade = self.profit_helper.process_trade_data(trade)  # Assuming you have a method for this
                # fee_dict = trade.get('fee')  # This gets the fee dictionary
                # cost_value = fee_dict.get('cost') if fee_dict else None
                # Update the Trade table with the new trade
                timestamp = trade.get('timestamp')
                if isinstance(timestamp, (int, float)):
                    # If 'timestamp' is a numeric value, convert it from Unix time to datetime
                    timestamp = datetime.utcfromtimestamp(timestamp / 1000.0)
                elif not isinstance(timestamp, datetime):
                    # If 'timestamp' is neither numeric nor datetime, log an error or convert it as needed
                    self.log_manager.sighook_logger.error(f'Unexpected timestamp format: {timestamp}')
                    timestamp = None  # Set to None or handle as needed
                new_trade_record = Trade(trade_time=trade.get('trade_time'),
                                         trade_id=trade.get('id'),
                                         order_id=trade.get('order'),
                                         symbol=trade.get('symbol'),
                                         price=trade.get('price'),
                                         amount=trade.get('amount'),
                                         cost=trade.get('cost'),
                                         side=trade.get('side'),
                                         fee=trade.get('fee'),
                                         timestamp=timestamp)
                session.add(new_trade_record)

                # Update the Holding based on the trade
                if processed_trade['side'] == 'buy':
                    self.profit_helper.update_holding_from_buy(session, symbol, processed_trade)
                elif processed_trade['side'] == 'sell':
                    self.profit_helper.process_sell_order_fifo(session, symbol, processed_trade['amount'],
                                                               processed_trade['price'])


                # After processing each trade, commit the session to save the changes
                # Consider error handling here to manage partial failures
                session.commit()
        except Exception as e:
            error_details = traceback.format_exc()
            self.log_manager.sighook_logger.error(f'process_new_trades_for_symbol: {error_details}, {e}')
            raise



