
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy import Column, Integer, String, Numeric, DateTime, create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.sql import func
from datetime import datetime
from dateutil import parser
import sqlite3  # Only for sqlite3.Error
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
    timestamp = Column(DateTime, nullable=True)  # timestamp {int}


class Holding(Base):
    """All current holdings are stored in this table."""
    __tablename__ = 'holdings'

    currency = Column(String, primary_key=True, nullable=False, index=True)
    purchase_date = Column(DateTime, default=func.now())  # Date of purchase
    purchase_price = Column(Numeric)  # Price at which the cryptocurrency was purchased
    current_price = Column(Numeric)  # Current price of the cryptocurrency
    purchase_amount = Column(Numeric)  # Quantity of the cryptocurrency purchased
    balance = Column(Numeric)  # Remaining quantity of the cryptocurrency
    average_cost = Column(Numeric)  # Average cost basis of the remaining quantity
    total_cost = Column(Numeric)  # Total cost of the current holdings
    unrealized_profit_loss = Column(Numeric)  # Unrealized profit/loss of the current holdings
    unrealized_pct_change = Column(Numeric)  # Unrealized profit/loss percentage of the current holdings

class RealizedProfit(Base):
    """All realized profits are stored in this table."""
    __tablename__ = 'realized_profits'

    id = Column(Integer, primary_key=True)
    symbol = Column(String, nullable=False, index=True)
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


class DatabaseManager:
    def __init__(self, utility, log_manager, ticker_manager, portfolio_manager, app_config):
        self.log_manager = log_manager
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
        self.holdings = None

        os.makedirs(self.database_dir, exist_ok=True)  # Ensure the database directory exists
        self.engine = create_engine(f'sqlite:///{self.sqlite_db_path}')  # Use SQLAlchemy engine with the correct URI
        Base.metadata.create_all(self.engine)  # Create tables based on models
        self.Session = sessionmaker(bind=self.engine)

    def set_trade_parameters(self, start_time, ticker_cache, market_cache, hist_holdings):
        self.start_time = start_time
        self.ticker_cache = ticker_cache
        self.market_cache = market_cache
        self.holdings = hist_holdings

    def initialize_db(self):
        # Update ticker cache to get market data
        ticker_cache, market_cache = self.ticker_manager.update_ticker_cache()

        # Ensure market_cache is not empty
        if not market_cache:
            print("Market cache is empty. Unable to fetch historical trades.")
            return ticker_cache, market_cache  # Return early if market_cache is empty

        #  SQLAlchemy session for database operations
        session = self.Session()
        try:
            # Fetch and insert historical trades
            for market in market_cache:
                ticker = market.get('symbol')

                try:
                    trades = self.portfolio_manager.get_my_trades(ticker)
                    if trades:
                        for trade in trades:
                            # Check if the trade already exists
                            existing_trade = session.query(Trade).get(trade.get('id'))
                            if not existing_trade:
                                # Create a new Trade instance and add it to the session
                                fee_dict = trade.get('fee')  # This gets the fee dictionary
                                cost_value = fee_dict.get('cost') if fee_dict else None
                                new_trade = Trade(trade_time=parser.parse(trade.get('datetime')),
                                                  trade_id=trade.get('id'),
                                                  order_id=trade.get('order'),
                                                  symbol=trade.get('symbol'),
                                                  price=trade.get('price'),
                                                  amount=trade.get('amount'),
                                                  cost=trade.get('cost'),
                                                  side=trade.get('side'),
                                                  fee=cost_value,
                                                  timestamp=datetime.utcfromtimestamp(trade.get('timestamp') / 1000.0))
                                session.add(new_trade)
                except Exception as e:
                    error_details = traceback.format_exc()
                    self.log_manager.sighook_logger.error(f'initialize_db: {error_details}, ticker:{ticker}, {e}')

            session.commit()  # Commit the transactions after processing all markets
        except Exception as e:
            error_details = traceback.format_exc()
            self.log_manager.sighook_logger.error(f'initialize_db: {error_details}, {e}')
            session.rollback()  # Rollback in case of error
        finally:
            session.close()  # Ensure the session is closed after operations

        return ticker_cache, market_cache  # Return the updated ticker and market cache

    def update_trades_db(self, session, trades):
        existing_symbols = {symbol[0] for symbol in session.query(Trade.symbol).distinct().all()}
        try:
            new_trades = []  # Initialize an empty list to collect new Trade objects
            if trades:
                for trade in trades:
                    # Check if the trade already exists
                    existing_trade = session.query(Trade).get(trade.get('id'))
                    if not existing_trade:
                        # Create a new Trade instance and add it to the session
                        fee_dict = trade.get('fee')  # This gets the fee dictionary
                        cost_value = fee_dict.get('cost') if fee_dict else None
                        new_trade = Trade(
                            trade_time=parser.parse(trade.get('datetime')),
                            trade_id=trade.get('id'),
                            order_id=trade.get('order'),
                            symbol=trade.get('symbol'),
                            price=trade.get('price'),
                            amount=trade.get('amount'),
                            cost=trade.get('cost'),
                            side=trade.get('side'),
                            fee=cost_value,
                            timestamp=datetime.utcfromtimestamp(trade.get('timestamp') / 1000.0)
                        )
                        new_trades.append(new_trade)  # Append the new Trade object to the list
                session.bulk_save_objects(new_trades)
                session.commit()
        except Exception as e:
            session.rollback()
            error_details = traceback.format_exc()
            self.log_manager.sighook_logger.error(f'save_trade_to_db: {error_details}, {e}')

    def update_holdings_table(self, session, holdings):

        try:
            new_position = []  # Initialize an empty list to collect new Trade objects
            if holdings:
                for coin in holdings:
                    # Check if the trade already exists
                    existing_trade = session.query(Trade).get(coin.get('id'))
                    if not existing_trade:

                        new_holding = Holding(
                            purchase_date=parser.parse(coin.get('datetime')),
                            trade_id=coin.get('id'),
                            order_id=coin.get('order'),
                            currency=coin.get('symbol'),
                            purchase_price=coin.get('price'),
                            purchase_amount=coin.get('amount'),
                            average_cost=coin.get('cost'),
                            balance=coin.get('balance'),
                            total_cost=coin.get('total_cost'),
                            timestamp=datetime.utcfromtimestamp(coin.get('timestamp') / 1000.0)
                        )
                        new_position.append(new_holding)  # Append the new Trade object to the list
                session.bulk_save_objects(new_position)
                session.commit()
        except Exception as e:
            session.rollback()
            error_details = traceback.format_exc()
            self.log_manager.sighook_logger.error(f'save_trade_to_db: {error_details}, {e}')


    def update_profit_data_db(self, session, symbol, unrealized_pct, profit_loss, total_cost, current_value, balance):
        try:
            # Attempt to find an existing profit data record for the given symbol
            existing_profit_data = session.query(ProfitData).filter(ProfitData.symbol == symbol).first()

            if existing_profit_data:
                # If found, update the existing record
                existing_profit_data.unrealized_pct = unrealized_pct
                existing_profit_data.profit_loss = profit_loss
                existing_profit_data.total_cost = total_cost
                existing_profit_data.current_value = current_value
                existing_profit_data.balance = balance
            else:
                # Otherwise, create a new ProfitData instance and add it to the session
                new_profit_data = ProfitData(
                    symbol=symbol,
                    unrealized_pct=unrealized_pct,
                    profit_loss=profit_loss,
                    total_cost=total_cost,
                    current_value=current_value,
                    balance=balance
                )
                session.add(new_profit_data)

            # Commit the transaction
            session.commit()

        except Exception as e:
            # Rollback the session in case of an error and log the error
            session.rollback()
            error_details = traceback.format_exc()
            self.log_manager.sighook_logger.error(f'update_profit_data_db: {error_details}, {e}')




