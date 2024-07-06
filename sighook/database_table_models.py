from sqlalchemy import create_engine, Column, String, Numeric, DateTime, Integer, ForeignKey, Float
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from decimal import Decimal

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
        proceeds (Numeric): Total proceeds of the trade (amount * price).
        side (str): Trade side, 'buy' or 'sell', nullable if not applicable.
        fee (Numeric): Trading fee incurred, nullable if not applicable.
    """
    """Holds all closed trades."""

    __tablename__ = 'trades'

    trade_id = Column(String, primary_key=True, nullable=False, unique=True)  # id {str}
    order_id = Column(String, nullable=True, unique=False)  # order {str}
    trade_time = Column(DateTime(timezone=True))  # datetime {str}
    transaction_type = Column(String, nullable=True)
    asset = Column(String, nullable=True)  # symbol {str}
    amount = Column(Float)  # Ensure amount is defined as Float  # amount {float}
    balance = Column(Float)  # Remaining balance
    currency = Column(String, ForeignKey('holdings.currency'))  # Link to Holdings via currency
    price = Column(Float)  # price {float}
    cost = Column(Float, default=0)  # cost {float} only buy orders, defaults to 0
    proceeds = Column(Float, default=0)  # Only sell trades, defaults to 0
    fee = Column(Float, nullable=True)  # fee {float}
    total = Column(Float)  # total {float}
    holding = relationship("Holding", back_populates="trades", overlaps="trades")
    notes = Column(String, nullable=True)

    @classmethod
    async def create_trade_from_row(cls, session, trade, asset, trade_time, csv=False):
        """Create Trade objects from a CSV row."""
        transaction_type = trade.get('Transaction Type', '').lower()
        trade_id = trade.get('ID')
        currency = trade.get('Price Currency')
        try:
            # Handle different transaction types
            if 'convert' in transaction_type:
                # Assume Notes field explains the conversion: "Converted X ETH to Y ETH2"
                details = trade['Notes'].split()
                from_amount = float(details[1])
                asset_from = details[2]
                amount_to = float(details[4])
                asset_to = details[5]

                # Log the conversion details
                cls.log_manager.sighook_logger.debug(f"Creating conversion trades: from {asset_from} to {asset_to}")

                # Create sell trade for the asset being converted from
                sell_trade = cls(
                    trade_time=trade_time,
                    trade_id=trade_id,
                    order_id=trade.get('order', 'na'),
                    asset=asset['from_asset'],
                    amount=-from_amount,
                    balance=0,
                    price=float(trade.get('Price at Transaction')),
                    currency=currency,
                    cost=0,
                    proceeds=float(trade.get('Subtotal')),
                    fee=float(trade.get('Fees and/or Spread', 0)),
                    transaction_type='sell',
                    total=float(trade.get('Subtotal')) - float(trade.get('Fees and/or Spread', 0))
                )

                # Create buy trade for the asset being converted to
                buy_trade = cls(
                    trade_time=trade_time,
                    trade_id=trade_id + '-convert',
                    order_id=trade.get('order', 'na'),
                    asset=asset['to_asset'],
                    amount=amount_to,
                    balance=0,
                    price=float(trade.get('Price at Transaction')),  # Assuming same price for simplification
                    currency=currency,  # This needs to be adjusted if currency differs
                    cost=float(trade.get('Subtotal')),
                    proceeds=0,
                    fee=float(trade.get('Fees and/or Spread', 0)),
                    transaction_type='buy',
                    total=float(trade.get('Subtotal')) + float(trade.get('Fees and/or Spread', 0))
                )

                cls.log_manager.sighook_logger.debug(f"Created sell trade: {sell_trade}")
                cls.log_manager.sighook_logger.debug(f"Created buy trade: {buy_trade}")

                return sell_trade, buy_trade

            else:  # Process buy or sell normally
                is_buy = 'buy' in transaction_type or 'Receive' in transaction_type
                price = float(trade.get('Price at Transaction'))
                amount = float(trade.get('Quantity Transacted'))
                if amount == 0.0:
                    cls.log_manager.sighook_logger.warning(f"Zero amount trade: {trade_id}")
                fee = float(trade.get('Fees and/or Spread', 0))
                cost = float(trade.get('Subtotal') if is_buy else 0)
                proceeds = float(trade.get('Subtotal') if not is_buy else 0)

                # Log the trade details
                cls.log_manager.sighook_logger.debug(
                    f"Creating trade: id={trade_id}, amount={amount}, cost={cost}, proceeds={proceeds}")

                return cls(
                    trade_time=trade_time,
                    trade_id=trade_id,
                    order_id=trade.get('order', 'na'),  # Consider handling 'order' ID extraction better
                    asset=asset['asset'],
                    price=price,
                    amount=amount if is_buy else -amount,
                    balance=0,
                    currency=currency,
                    cost=cost,
                    proceeds=proceeds,
                    transaction_type=transaction_type,
                    fee=fee,
                    total=cost - fee if is_buy else proceeds - fee
                )
        except Exception as e:
            await session.rollback()
            cls.log_manager.sighook_logger.error(f"Failed to create trade from row: {e}", exc_info=True)
            raise ValueError(f"Failed to create trade from row: {e}")


class NewTrade(Base):
    """All recently
    closed trades since last update."""

    """Holds all closed trades that are newly added to the database."""

    __tablename__ = 'trades_new'

    trade_id = Column(String, primary_key=True)
    order_id = Column(String, nullable=True)
    trade_time = Column(DateTime(timezone=True))
    transaction_type = Column(String, nullable=True)
    asset = Column(String, nullable=True)
    amount = Column(Float)  # amount {float}
    balance = Column(Float)  # Remaining balance
    currency = Column(String, ForeignKey('holdings.currency'))  # Link to Holdings via currency
    price = Column(Float)  # price {float}
    cost = Column(Float)  # buy orders only
    proceeds = Column(Float, default=0)  # Only sell trades, defaults to 0
    fee = Column(Float, nullable=True)
    total = Column(Float)  # total {float}
    holding = relationship("Holding", back_populates="new_trades")
    notes = Column(String, nullable=True)


class TradeSummary(Base):
    """Summary of all trades for a specific symbol."""
    __tablename__ = 'trade_summary'
    id = Column(Integer, primary_key=True)
    asset = Column(String)
    total_trades = Column(Integer)
    total_cost = Column(Float)
    total_proceeds = Column(Float)
    total_fees = Column(Float)
    average_cost_without_fees = Column(Float)
    average_cost_with_fees = Column(Float)


class Holding(Base):
    """All current holdings are stored in this table."""
    """Holds all Current holdings."""
    __tablename__ = 'holdings'

    # Using asset and currency as a composite primary key
    currency = Column(String, primary_key=True)
    asset = Column(String, primary_key=True)

    purchase_date = Column(DateTime(timezone=True), default=func.now())
    purchase_price = Column(Float)
    current_price = Column(Float)
    purchase_amount = Column(Float)
    initial_investment = Column(Float)
    market_value = Column(Float)
    balance = Column(Float)
    weighted_average_cost = Column(Float)
    unrealized_profit_loss = Column(Float)
    unrealized_pct_change = Column(Float)

    # Relationships with other tables
    trades = relationship("Trade", back_populates="holding")
    new_trades = relationship("NewTrade", back_populates="holding")

    def __repr__(self):
        return f"<Holding(asset={self.asset}, currency={self.currency})>"

    # @classmethod
    # def create_from_aggregated_data(cls, currency, aggregated_data, balance):
    #     # used by old_database_manager
    #     """
    #     Create a new Holding instance from aggregated trade data.
    #
    #     Parameters:
    #     - currency: The currency symbol of the holding.
    #     - aggregated_data: A dictionary containing aggregated trade data,
    #       including 'earliest_trade_time', 'total_amount', 'total_cost',
    #       'average_cost', and 'purchase_price'.
    #     - balance: The current balance of the cryptocurrency in the holding.
    #
    #     Returns:
    #     - An instance of Holding initialized with the provided data.
    #     """
    #     return cls(
    #         currency=currency,
    #         first_purchase_date=aggregated_data['earliest_trade_time'],
    #         purchase_date=aggregated_data['earliest_trade_time'],  # or use datetime.utcnow() if more appropriate
    #         purchase_price=aggregated_data['purchase_price'],
    #         current_price=aggregated_data['purchase_price'],
    #         # Assuming current price is the purchase price; adjust as needed
    #         purchase_amount=aggregated_data['total_amount'],
    #         initial_investment=aggregated_data['total_cost'],
    #         market_value=aggregated_data['total_amount'] * trade.price,
    #         balance=balance,
    #         weighted_average_cost=aggregated_data['weighted_average_cost'],
    #         unrealized_profit_loss=0,  # Initialize as 0; adjust based on your logic
    #         unrealized_pct_change=0  # Initialize as 0; adjust based on your logic
    #     )

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
    timestamp = Column(DateTime(timezone=True), default=func.now())  # Timestamp of when the profit was realized


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
    last_update_time = Column(DateTime(timezone=True))
