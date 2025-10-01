
from sqlalchemy import Column, String, Float, DateTime, Index
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from TableModels.base import Base


class TradeRecord(Base):
    """Stores finalized completed trades for performance analysis."""
    __tablename__ = 'trade_records'

    order_id = Column(String, primary_key=True, unique=True)
    parent_id = Column(String, nullable=True)
    parent_ids = Column(ARRAY(String), nullable=True)  # NEW: multiple parent buy IDs for audit trail
    symbol = Column(String, nullable=False, index=True)
    side = Column(String, nullable=False)
    order_time = Column(DateTime(timezone=True), nullable=False)
    price = Column(Float, nullable=False)
    size = Column(Float, nullable=False)
    pnl_usd = Column(Float, nullable=True)  # Will be calculated for sells
    total_fees_usd = Column(Float, nullable=True)
    trigger: Mapped[dict] = mapped_column(JSONB)  # e.g., 'roc_buy', 'score'
    order_type = Column(String, nullable=True)
    status = Column(String, nullable=True)
    source = Column(String, nullable=True)
    cost_basis_usd = Column(Float, nullable=True)  # NEW: for performance tracking
    sale_proceeds_usd = Column(Float, nullable=True)  # NEW: for performance tracking
    net_sale_proceeds_usd = Column(Float, nullable=True)  # NEW: for performance tracking
    remaining_size = Column(Float, nullable=True)
    realized_profit = Column(Float, nullable=True)  # NEW: for performance tracking
    ingest_via = Column(String)
    last_reconciled_at = Column(DateTime(timezone=True))
    last_reconciled_via = Column(String)


    __table_args__ = (
        Index('idx_trade_records_symbol_order_time', 'symbol', 'order_time'),
    )
