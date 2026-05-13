from sqlalchemy import Column, String, Integer, DECIMAL, DateTime, Text, Index, CheckConstraint, ARRAY, text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from datetime import datetime, timezone
from TableModels.base import Base


class StrategySnapshot(Base):
    """Configuration snapshots for A/B testing and performance tracking."""
    __tablename__ = 'strategy_snapshots'

    id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_id = Column(UUID(as_uuid=True), nullable=False, unique=True, server_default=text("gen_random_uuid()"))
    active_from = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    active_until = Column(DateTime(timezone=True), nullable=True)  # NULL = currently active

    # Core Strategy Settings
    score_buy_target = Column(DECIMAL(10, 3), nullable=True)
    score_sell_target = Column(DECIMAL(10, 3), nullable=True)

    # Indicator Weights (JSON for flexibility)
    indicator_weights = Column(JSONB, nullable=False)  # {"Buy RSI": 2.5, "Sell RSI": 2.5, ...}

    # Indicator Thresholds
    rsi_buy_threshold = Column(DECIMAL(10, 3), nullable=True)
    rsi_sell_threshold = Column(DECIMAL(10, 3), nullable=True)
    roc_buy_threshold = Column(DECIMAL(10, 3), nullable=True)
    roc_sell_threshold = Column(DECIMAL(10, 3), nullable=True)
    macd_signal_threshold = Column(DECIMAL(10, 3), nullable=True)

    # Risk Management
    tp_threshold = Column(DECIMAL(10, 3), nullable=True)  # Take profit %
    sl_threshold = Column(DECIMAL(10, 3), nullable=True)  # Stop loss %

    # Trade Guardrails
    cooldown_bars = Column(Integer, nullable=True)
    flip_hysteresis_pct = Column(DECIMAL(10, 3), nullable=True)
    min_indicators_required = Column(Integer, server_default=text("0"))  # Multi-indicator confirmation

    # Symbol Filters
    excluded_symbols = Column(ARRAY(Text), nullable=True)  # Array of blacklisted symbols
    max_spread_pct = Column(DECIMAL(10, 3), nullable=True)  # Max allowed spread %

    # Metadata
    config_hash = Column(String(64), nullable=False, unique=True)  # SHA-256 of all settings for deduplication
    notes = Column(Text, nullable=True)  # User notes about this configuration
    created_by = Column(String(50), server_default=text("'system'"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))

    __table_args__ = (
        CheckConstraint('active_until IS NULL OR active_until > active_from', name='valid_active_period'),
        Index('idx_strategy_snapshots_active', 'active_from', 'active_until'),
        Index('idx_strategy_snapshots_hash', 'config_hash'),
    )
