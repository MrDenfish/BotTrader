
from sqlalchemy import (
    Column, Integer, String, Float, DateTime, MetaData,
    UniqueConstraint, Index
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.sql import func, select
import json

# Metadata object for async database usage
metadata = MetaData()

Base = declarative_base(metadata=metadata)

class DatabaseTables:
    def __init__(self):
        # Register each model as an attribute
        self.OHLCVData = OHLCVData
        self.MarketDataSnapshot = MarketDataSnapshot
        self.OrderManagementSnapshot = OrderManagementSnapshot


class MarketDataSnapshot(Base):
    """Stores periodic snapshots of market data."""
    __tablename__ = 'market_data_snapshots'

    id = Column(Integer, primary_key=True)
    snapshot_time = Column(DateTime(timezone=True), default=func.now(), nullable=False)
    data = Column(String, nullable=False)  # JSON-encoded market data

    __table_args__ = (
        Index('idx_market_data_snapshot_time', 'snapshot_time'),
    )

class OrderManagementSnapshot(Base):
    """Stores periodic snapshots of order management data."""
    __tablename__ = 'order_management_snapshots'

    id = Column(Integer, primary_key=True)
    snapshot_time = Column(DateTime(timezone=True), default=func.now(), nullable=False)
    data = Column(String, nullable=False)  # JSON-encoded order management data

    __table_args__ = (
        Index('idx_order_management_snapshot_time', 'snapshot_time'),
    )

class SharedData(Base):
    """Table to store shared market data and order management dictionaries."""
    __tablename__ = 'shared_data'

    id = Column(Integer, primary_key=True)
    last_updated = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    data_type = Column(String, nullable=False, unique=True)  # 'market_data' or 'order_management'
    data = Column(String, nullable=False)  # JSON-encoded data


    __table_args__ = (
        Index('idx_shared_data_type', 'data_type'),
    )

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
        query = insert(self.db_tables.MarketDataSnapshot).values(
            data=json.dumps(data)
        )
        await self.database.execute(query)

    async def save_order_management_snapshot(self, data: dict):
        """Save a snapshot of order management data."""
        query = insert(self.db_tables.OrderManagementSnapshot).values(
            data=json.dumps(data)
        )
        await self.database.execute(query)


    async def fetch_recent_market_data_snapshot(self):
        """Fetch the most recent market data snapshot."""
        query = (
            select(self.db_tables.MarketDataSnapshot)
            .order_by(self.db_tables.MarketDataSnapshot.snapshot_time.desc())
            .limit(1)
        )
        result = await self.database.fetch_one(query)
        return json.loads(result["data"]) if result else None

    async def fetch_recent_order_management_snapshot(self):
        """Fetch the most recent order management snapshot."""
        query = (
            select(self.db_tables.OrderManagementSnapshot)
            .order_by(self.db_tables.OrderManagementSnapshot.snapshot_time.desc())
            .limit(1)
        )
        result = await self.database.fetch_one(query)
        return json.loads(result["data"]) if result else None


