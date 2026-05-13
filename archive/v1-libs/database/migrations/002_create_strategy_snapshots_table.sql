-- Migration: Create strategy_snapshots table for A/B testing and performance tracking
-- Purpose: Track bot configuration changes and correlate with performance metrics
-- Created: 2025-12-08

-- ====================================================================
-- Table: strategy_snapshots
-- Stores configuration snapshots each time settings change
-- ====================================================================

CREATE TABLE IF NOT EXISTS strategy_snapshots (
    id SERIAL PRIMARY KEY,
    snapshot_id UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    active_from TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    active_until TIMESTAMPTZ,  -- NULL = currently active

    -- Core Strategy Settings
    score_buy_target DECIMAL(10, 3),
    score_sell_target DECIMAL(10, 3),

    -- Indicator Weights (JSON for flexibility)
    indicator_weights JSONB NOT NULL,  -- {"Buy RSI": 2.5, "Sell RSI": 2.5, ...}

    -- Indicator Thresholds
    rsi_buy_threshold DECIMAL(10, 3),
    rsi_sell_threshold DECIMAL(10, 3),
    roc_buy_threshold DECIMAL(10, 3),
    roc_sell_threshold DECIMAL(10, 3),
    macd_signal_threshold DECIMAL(10, 3),

    -- Risk Management
    tp_threshold DECIMAL(10, 3),  -- Take profit %
    sl_threshold DECIMAL(10, 3),  -- Stop loss %

    -- Trade Guardrails
    cooldown_bars INTEGER,
    flip_hysteresis_pct DECIMAL(10, 3),
    min_indicators_required INTEGER DEFAULT 0,  -- NEW: for multi-indicator confirmation

    -- Symbol Filters
    excluded_symbols TEXT[],  -- Array of blacklisted symbols
    max_spread_pct DECIMAL(10, 3),  -- Max allowed spread %

    -- Metadata
    config_hash VARCHAR(64) NOT NULL UNIQUE,  -- SHA-256 of all settings for deduplication
    notes TEXT,  -- User notes about this configuration
    created_by VARCHAR(50) DEFAULT 'system',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Indexes for queries
    CONSTRAINT valid_active_period CHECK (active_until IS NULL OR active_until > active_from)
);

-- Indexes
CREATE INDEX idx_strategy_snapshots_active ON strategy_snapshots(active_from, active_until);
CREATE INDEX idx_strategy_snapshots_hash ON strategy_snapshots(config_hash);
CREATE UNIQUE INDEX idx_strategy_snapshots_current ON strategy_snapshots(active_from DESC)
    WHERE active_until IS NULL;

-- ====================================================================
-- Table: strategy_performance_summary
-- Daily aggregated performance metrics per strategy snapshot
-- ====================================================================

CREATE TABLE IF NOT EXISTS strategy_performance_summary (
    id SERIAL PRIMARY KEY,
    snapshot_id UUID NOT NULL REFERENCES strategy_snapshots(snapshot_id),
    date DATE NOT NULL,

    -- Trade Metrics
    total_trades INTEGER DEFAULT 0,
    winning_trades INTEGER DEFAULT 0,
    losing_trades INTEGER DEFAULT 0,
    breakeven_trades INTEGER DEFAULT 0,

    -- P&L Metrics
    total_pnl_usd DECIMAL(20, 8),
    avg_win_usd DECIMAL(20, 8),
    avg_loss_usd DECIMAL(20, 8),
    largest_win_usd DECIMAL(20, 8),
    largest_loss_usd DECIMAL(20, 8),

    -- Performance Ratios
    win_rate DECIMAL(10, 4),  -- percentage
    profit_factor DECIMAL(10, 4),  -- gross profit / gross loss
    expectancy_usd DECIMAL(20, 8),  -- avg profit per trade

    -- Risk Metrics
    max_drawdown_pct DECIMAL(10, 4),
    sharpe_ratio DECIMAL(10, 4),

    -- Trade Quality
    avg_hold_time_seconds INTEGER,
    median_hold_time_seconds INTEGER,
    fast_exits_count INTEGER,  -- trades held < 60s
    fast_exits_pnl DECIMAL(20, 8),

    -- Signal Quality
    total_signals INTEGER,
    signals_suppressed_cooldown INTEGER,
    signals_suppressed_hysteresis INTEGER,
    signals_executed INTEGER,

    -- Updated tracking
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT unique_snapshot_date UNIQUE(snapshot_id, date)
);

-- Indexes
CREATE INDEX idx_strategy_perf_snapshot ON strategy_performance_summary(snapshot_id);
CREATE INDEX idx_strategy_perf_date ON strategy_performance_summary(date DESC);

-- ====================================================================
-- Table: trade_strategy_link
-- Links each trade to the strategy configuration that generated it
-- ====================================================================

CREATE TABLE IF NOT EXISTS trade_strategy_link (
    id SERIAL PRIMARY KEY,
    order_id VARCHAR(100) NOT NULL UNIQUE,  -- Foreign key to trade_records
    snapshot_id UUID NOT NULL REFERENCES strategy_snapshots(snapshot_id),

    -- Signal Details at Trade Time
    buy_score DECIMAL(10, 3),
    sell_score DECIMAL(10, 3),
    trigger_type VARCHAR(50),  -- 'score', 'roc_momo', 'tp_sl', etc.
    indicators_fired INTEGER,  -- How many indicators contributed
    indicator_breakdown JSONB,  -- {"RSI": 2.5, "MACD": 1.8, "Touch": 1.5}

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_trade_strategy_link_order ON trade_strategy_link(order_id);
CREATE INDEX idx_trade_strategy_link_snapshot ON trade_strategy_link(snapshot_id);

-- ====================================================================
-- Views for Easy Querying
-- ====================================================================

-- View: Current active strategy
CREATE OR REPLACE VIEW current_strategy AS
SELECT * FROM strategy_snapshots
WHERE active_until IS NULL
ORDER BY active_from DESC
LIMIT 1;

-- View: Strategy performance comparison
CREATE OR REPLACE VIEW strategy_comparison AS
SELECT
    ss.snapshot_id,
    ss.active_from,
    ss.active_until,
    ss.score_buy_target,
    ss.min_indicators_required,
    ss.notes,
    COUNT(DISTINCT sps.date) as days_active,
    SUM(sps.total_trades) as total_trades,
    AVG(sps.win_rate) as avg_win_rate,
    SUM(sps.total_pnl_usd) as total_pnl,
    AVG(sps.profit_factor) as avg_profit_factor,
    AVG(sps.expectancy_usd) as avg_expectancy
FROM strategy_snapshots ss
LEFT JOIN strategy_performance_summary sps ON ss.snapshot_id = sps.snapshot_id
GROUP BY ss.snapshot_id, ss.active_from, ss.active_until,
         ss.score_buy_target, ss.min_indicators_required, ss.notes
ORDER BY ss.active_from DESC;

COMMENT ON TABLE strategy_snapshots IS 'Configuration snapshots for A/B testing and performance tracking';
COMMENT ON TABLE strategy_performance_summary IS 'Daily aggregated performance metrics per strategy configuration';
COMMENT ON TABLE trade_strategy_link IS 'Links trades to the strategy configuration that generated them';
