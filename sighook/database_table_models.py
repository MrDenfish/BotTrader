from sqlalchemy import create_engine, Column, String, Numeric, DateTime, Integer, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func


Base = declarative_base()


class Trade(Base):
    """Represents a closed trade transaction.

    Attributes:
        trade_id (str): Unique identifier for the trade.
        order_id (str): Associated order identifier, nullable if not applicable.
        trade_time (datetime): Timestamp when the trade was executed.
        symbol (str): Market symbol for the trade, nullable if not known at the time of recording.
        price (Numeric): Execution price of the trade.
        amount (Numeric): Quantity traded.
        cost (Numeric): Total cost of the trade (amount * price).
        side (str): Trade side, 'buy' or 'sell', nullable if not applicable.
        fee (Numeric): Trading fee incurred, nullable if not applicable.
    """
    """Holds all closed trades."""

    __tablename__ = 'trades'

    trade_id = Column(String, primary_key=True)  # id {str}
    order_id = Column(String, nullable=True)  # order {str}
    trade_time = Column(DateTime)  # datetime {str}
    transaction_type = Column(String, nullable=True)
    asset = Column(String, nullable=True)  # symbol {str}
    amount = Column(Numeric)  # amount {decimal}
    currency = Column(String, ForeignKey('holdings.currency'))  # Link to Holdings via currency
    price = Column(Numeric)  # price {decimal}
    cost = Column(Numeric)  # cost {decimal}
    total = Column(Numeric)  # total {decimal}
    fee = Column(Numeric, nullable=True)  # fee {float}
    holding = relationship("Holding", back_populates="trades", overlaps="trades")
    notes = Column(String, nullable=True)


class NewTrade(Base):
    """All recently
    closed trades since last update."""

    """Holds all closed trades that are newly added to the database."""

    __tablename__ = 'trades_new'

    trade_id = Column(String, primary_key=True)
    trade_time = Column(DateTime)
    transaction_type = Column(String, nullable=True)
    asset = Column(String, nullable=True)
    amount = Column(Numeric)  # amount {decimal}
    currency = Column(String, ForeignKey('holdings.currency'))  # Link to Holdings via currency
    price = Column(Numeric)  # price {decimal}
    cost = Column(Numeric)
    total = Column(Numeric)  # total {decimal}
    fee = Column(Numeric, nullable=True)
    holding = relationship("Holding", back_populates="new_trades")
    notes = Column(String, nullable=True)


class TradeSummary(Base):
    """Summary of all trades for a specific symbol."""
    __tablename__ = 'trade_summary'
    id = Column(Integer, primary_key=True)
    asset = Column(String)
    total_trades = Column(Integer)
    total_cost = Column(Numeric)
    total_fees = Column(Numeric)
    average_cost_without_fees = Column(Numeric)
    average_cost_with_fees = Column(Numeric)


class Holding(Base):
    """All current holdings are stored in this table."""
    """Holds all Current holdings."""

    __tablename__ = 'holdings'
    currency = Column(String, primary_key=True)
    asset = Column(String, nullable=False, index=True)
    purchase_date = Column(DateTime, default=func.now())
    purchase_price = Column(Numeric)
    current_price = Column(Numeric)
    purchase_amount = Column(Numeric)
    balance = Column(Numeric)
    average_cost = Column(Numeric)
    total_cost = Column(Numeric)
    unrealized_profit_loss = Column(Numeric)
    unrealized_pct_change = Column(Numeric)
    trades = relationship("Trade", back_populates="holding")
    new_trades = relationship("NewTrade", back_populates="holding")

    @classmethod
    def create_from_trade(cls, trade):
        # used by old_database_manager
        """Create a new Holding instance from a trade."""
        currency = trade.symbol.split('/')[0]
        return cls(
            currency=currency,
            ticker=trade.asset,
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
        # used by old_database_manager
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
    """tracks each symbols  most recent trade time,  last_update_time"""
    __tablename__ = 'symbol_updates'

    symbol = Column(String, primary_key=True)
    last_update_time = Column(DateTime)
