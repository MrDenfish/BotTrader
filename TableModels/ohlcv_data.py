

from sqlalchemy import (Column, Integer, String, Float, DateTime, UniqueConstraint, Index, func, select)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.sql import func, select
import json
import models.market_snapshot
import models.order_management
from models.base import Base



class OHLCVData(Base):
    """Stores OHLCV (Open, High, Low, Close, Volume) data for each symbol at specific time intervals."""
    __tablename__ = 'ohlcv_data'

    id = Column(Integer, primary_key=True)
    symbol = Column(String, nullable=False, index=True)  # Trading symbol (e.g., BTC/USD)
    time = Column(DateTime(timezone=True), nullable=False, index=True)  # OHLCV timestamp
    open = Column(Float, nullable=False)  # Opening price at the time interval
    high = Column(Float, nullable=False)  # Highest price at the time interval
    low = Column(Float, nullable=False)  # Lowest price at the time interval
    close = Column(Float, nullable=False)  # Closing price at the time interval
    volume = Column(Float, nullable=False)  # Volume traded during the time interval
    last_updated = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint('symbol', 'time', name='_symbol_time_uc'),  # Enforce unique intervals for each symbol
        Index('idx_ohlcv_symbol_time', 'symbol', 'time'),  # Optimize queries filtering by symbol and time
    )

    async def save_market_data_snapshot(self, data: dict):
        """Save a snapshot of market data."""
        query = insert(models.market_snapshot.MarketDataSnapshot).values(
            data=json.dumps(data)
        )
        await self.database.execute(query)

    async def save_order_management_snapshot(self, data: dict):
        """Save a snapshot of order management data."""
        query = insert(models.order_management.OrderManagementSnapshot).values(
            data=json.dumps(data)
        )
        await self.database.execute(query)


    async def fetch_recent_market_data_snapshot(self):
        """Fetch the most recent market data snapshot."""
        query = (
            select(models.market_snapshot.MarketDataSnapshot)
            .order_by(models.market_snapshot.MarketDataSnapshot.snapshot_time.desc())
            .limit(1)
        )
        result = await self.database.fetch_one(query)
        return json.loads(result["data"]) if result else None

    async def fetch_recent_order_management_snapshot(self):
        """Fetch the most recent order management snapshot."""
        query = (
            select(models.order_management.OrderManagementSnapshot)
            .order_by(models.order_management.OrderManagementSnapshot.snapshot_time.desc())
            .limit(1)
        )
        result = await self.database.fetch_one(query)
        return json.loads(result["data"]) if result else None
