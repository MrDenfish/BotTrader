
from sqlalchemy import Column, Integer, String, Float, DateTime, Text, Index, CheckConstraint, func
from sqlalchemy.types import DECIMAL
from TableModels.base import Base


class CashTransaction(Base):
    """Stores USD deposits and withdrawals from Coinbase accounts.

    Used for accurate cash balance calculations and equity curve generation.
    Tracks all cash movements since trading inception (2023-11-22).
    """
    __tablename__ = 'cash_transactions'

    id = Column(Integer, primary_key=True)
    transaction_id = Column(String(50), unique=True, index=True, nullable=False)
    transaction_date = Column(DateTime(timezone=True), nullable=False, index=True)
    transaction_type = Column(String(30), nullable=False)  # Raw type from CSV
    normalized_type = Column(String(20), nullable=False, index=True)  # 'deposit' or 'withdrawal'
    asset = Column(String(10), nullable=False)  # Always 'USD'
    quantity = Column(DECIMAL(20, 8), nullable=False)  # Can be negative for withdrawals
    amount_usd = Column(DECIMAL(20, 8), nullable=False)  # Absolute value, always positive
    subtotal = Column(DECIMAL(20, 8), nullable=True)
    total = Column(DECIMAL(20, 8), nullable=True)
    fees = Column(DECIMAL(20, 8), default=0, nullable=True)
    notes = Column(Text, nullable=True)
    source = Column(String(50), nullable=True)  # 'coinbase', 'coinbase_pro', 'coinbase_advanced'
    imported_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint('asset = \'USD\'', name='valid_asset'),
        CheckConstraint('amount_usd >= 0', name='valid_amount'),
        Index('idx_cash_tx_date', 'transaction_date'),
        Index('idx_cash_tx_type', 'normalized_type'),
        Index('idx_cash_tx_id', 'transaction_id'),
    )
