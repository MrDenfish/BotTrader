
from sqlalchemy import Column, Integer, String, Float, DateTime, Index
from TableModels.base import Base


class TradeRecord(Base):
    """Stores finalized completed trades for performance analysis."""
    __tablename__ = 'trade_records'

    order_id = Column(String, primary_key=True, unique=True)
    parent_id = Column(String,nullable=True)
    symbol = Column(String, nullable=False, index=True)
    side = Column(String, nullable=False)
    order_time = Column(DateTime(timezone=True), nullable=False)
    price = Column(Float, nullable=False)
    size = Column(Float, nullable=False)
    pnl_usd = Column(Float, nullable=True)  # Can calculate automatically later
    total_fees_usd = Column(Float, nullable=True)
    trigger = Column(String, nullable=True)  # e.g., 'roc_buy', 'score'
    order_type = Column(String, nullable=True)
    status = Column(String, nullable=True)

    __table_args__ = (
        Index('idx_trade_records_symbol_order_time', 'symbol', 'order_time'),
    )
