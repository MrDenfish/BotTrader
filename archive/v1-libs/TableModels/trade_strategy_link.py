from sqlalchemy import Column, String, Integer, Float, DateTime, Index, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from datetime import datetime, timezone
from TableModels.base import Base


class TradeStrategyLink(Base):
    """Links trades to strategy snapshots for parameter optimization analysis."""
    __tablename__ = 'trade_strategy_link'

    id = Column(Integer, primary_key=True, autoincrement=True)
    order_id = Column(String(100), ForeignKey('trade_records.order_id'), nullable=False, unique=True)
    snapshot_id = Column(UUID(as_uuid=True), ForeignKey('strategy_snapshots.snapshot_id'), nullable=False)
    buy_score = Column(Float(precision=10, decimal_return_scale=3), nullable=True)
    sell_score = Column(Float(precision=10, decimal_return_scale=3), nullable=True)
    trigger_type = Column(String(50), nullable=True)  # 'signal_flip', 'take_profit', 'stop_loss', etc.
    indicators_fired = Column(Integer, nullable=True)  # Count of indicators that triggered
    indicator_breakdown = Column(JSONB, nullable=True)  # {"Buy RSI": 1.5, "Buy MACD": 1.8, ...}
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index('idx_trade_strategy_link_order', 'order_id'),
        Index('idx_trade_strategy_link_snapshot', 'snapshot_id'),
    )
