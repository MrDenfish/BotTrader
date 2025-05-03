
from sqlalchemy import Column, Integer, String, Float, DateTime, Index
from models.base import Base


class TradeRecord(Base):
    """Stores finalized completed trades for performance analysis."""
    __tablename__ = 'trade_records'

    id = Column(Integer, primary_key=True)
    symbol = Column(String, nullable=False, index=True)  # e.g., 'BTC-USD'
    buy_time = Column(DateTime(timezone=True), nullable=False)
    sell_time = Column(DateTime(timezone=True), nullable=False)
    buy_price = Column(Float, nullable=False)
    sell_price = Column(Float, nullable=False)
    size = Column(Float, nullable=False)
    pnl_usd = Column(Float, nullable=True)  # Can calculate automatically later
    total_fees_usd = Column(Float, nullable=True)
    trigger = Column(String, nullable=True)  # e.g., 'roc_buy', 'score'
    comments = Column(String, nullable=True)

    __table_args__ = (
        Index('idx_trade_records_symbol_buy_time', 'symbol', 'buy_time'),
    )
