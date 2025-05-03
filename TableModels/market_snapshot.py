from sqlalchemy import Column, Integer, DateTime, func, String, Index
from models.base import Base


class MarketDataSnapshot(Base):
    """Stores periodic snapshots of market data."""
    __tablename__ = 'market_data_snapshots'

    id = Column(Integer, primary_key=True)
    snapshot_time = Column(DateTime(timezone=True), default=func.now(), nullable=False)
    data = Column(String, nullable=False)  # JSON-encoded market data

    __table_args__ = (
        Index('idx_market_data_snapshot_time', 'snapshot_time'),
    )
