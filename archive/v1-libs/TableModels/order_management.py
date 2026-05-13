from sqlalchemy import Column, Integer, DateTime, func, String, Index
from TableModels.base import Base



class OrderManagementSnapshot(Base):
    """Stores periodic snapshots of order management data."""
    __tablename__ = 'order_management_snapshots'

    id = Column(Integer, primary_key=True)
    snapshot_time = Column(DateTime(timezone=True), default=func.now(), nullable=False)
    data = Column(String, nullable=False)  # JSON-encoded order management data

    __table_args__ = (
        Index('idx_order_management_snapshot_time', 'snapshot_time'),
    )
