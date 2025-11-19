
from sqlalchemy import Column, String, DateTime, JSON, Float
from datetime import datetime, timezone
from .base import Base


class WebhookLimitOnlyPosition(Base):
    """
    Stores limit-only positions created by webhook orders for monitoring.

    These positions don't have bracket TP/SL orders attached, so they need
    to be monitored by passive_order_manager to place limit exit orders
    when TP/SL conditions are triggered.
    """
    __tablename__ = 'webhook_limit_only_positions'

    order_id = Column(String, primary_key=True)
    symbol = Column(String, nullable=False)
    entry_price = Column(Float, nullable=False)
    size = Column(Float, nullable=False)
    tp_price = Column(Float, nullable=False)
    sl_price = Column(Float, nullable=False)
    timestamp = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    source = Column(String, nullable=False, default='webhook_limit_only')
    order_data = Column(JSON, nullable=True)  # Full OrderData dict for reconstruction
