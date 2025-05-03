
from sqlalchemy import (Column, Integer, String, DateTime, Index)
from sqlalchemy.sql import func
from TableModels.base import Base


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
