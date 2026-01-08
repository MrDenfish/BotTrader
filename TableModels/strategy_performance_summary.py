from sqlalchemy import Column, String, Integer, DECIMAL, DateTime, Date, Index, ForeignKey, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from datetime import datetime, timezone
from TableModels.base import Base


class StrategyPerformanceSummary(Base):
    """Daily aggregated performance metrics per strategy configuration."""
    __tablename__ = 'strategy_performance_summary'

    id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_id = Column(UUID(as_uuid=True), ForeignKey('strategy_snapshots.snapshot_id'), nullable=False)
    date = Column(Date, nullable=False)

    # Trade Metrics
    total_trades = Column(Integer, server_default=text('0'))
    winning_trades = Column(Integer, server_default=text('0'))
    losing_trades = Column(Integer, server_default=text('0'))
    breakeven_trades = Column(Integer, server_default=text('0'))

    # P&L Metrics
    total_pnl_usd = Column(DECIMAL(20, 8), nullable=True)
    avg_win_usd = Column(DECIMAL(20, 8), nullable=True)
    avg_loss_usd = Column(DECIMAL(20, 8), nullable=True)
    largest_win_usd = Column(DECIMAL(20, 8), nullable=True)
    largest_loss_usd = Column(DECIMAL(20, 8), nullable=True)

    # Performance Ratios
    win_rate = Column(DECIMAL(10, 4), nullable=True)  # percentage
    profit_factor = Column(DECIMAL(10, 4), nullable=True)  # gross profit / gross loss
    expectancy_usd = Column(DECIMAL(20, 8), nullable=True)  # avg profit per trade

    # Risk Metrics
    max_drawdown_pct = Column(DECIMAL(10, 4), nullable=True)
    sharpe_ratio = Column(DECIMAL(10, 4), nullable=True)

    # Trade Quality
    avg_hold_time_seconds = Column(Integer, nullable=True)
    median_hold_time_seconds = Column(Integer, nullable=True)
    fast_exits_count = Column(Integer, nullable=True)  # trades held < 60s
    fast_exits_pnl = Column(DECIMAL(20, 8), nullable=True)

    # Signal Quality
    total_signals = Column(Integer, nullable=True)
    signals_suppressed_cooldown = Column(Integer, nullable=True)
    signals_suppressed_hysteresis = Column(Integer, nullable=True)
    signals_executed = Column(Integer, nullable=True)

    # Updated tracking
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))

    __table_args__ = (
        UniqueConstraint('snapshot_id', 'date', name='unique_snapshot_date'),
        Index('idx_strategy_perf_snapshot', 'snapshot_id'),
        Index('idx_strategy_perf_date', 'date'),
    )
