
from sqlalchemy import Column, String, DateTime, JSON
from datetime import datetime, timezone
from .base import Base



class PassiveOrder(Base):
    __tablename__ = 'passive_orders'

    order_id = Column(String, primary_key=True)
    symbol = Column(String, nullable=False)
    side = Column(String, nullable=False)
    timestamp = Column(DateTime(timezone=True), nullable=False)
    order_data = Column(JSON, nullable=False)  # JSON-safe OrderData