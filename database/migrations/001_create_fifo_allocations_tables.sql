-- Migration: Create FIFO Allocations Tables
-- Version: 001
-- Date: 2025-11-20
-- Description: Create new tables for FIFO allocation system (immutable trade ledger + computed allocations)

-- =============================================================================
-- PART 1: NEW TABLES FOR FIFO ALLOCATIONS
-- =============================================================================

\echo '================================================================================'
\echo 'FIFO ALLOCATIONS MIGRATION - Part 1: Creating New Tables'
\echo '================================================================================'
\echo ''

-- -----------------------------------------------------------------------------
-- Table: fifo_allocations
-- Purpose: Records how each SELL is matched to BUY(s) using FIFO logic
-- -----------------------------------------------------------------------------

\echo 'Creating fifo_allocations table...'

CREATE TABLE IF NOT EXISTS fifo_allocations (
    id BIGSERIAL PRIMARY KEY,

    -- The match
    sell_order_id VARCHAR NOT NULL REFERENCES trade_records(order_id) ON DELETE CASCADE,
    buy_order_id VARCHAR REFERENCES trade_records(order_id) ON DELETE CASCADE,  -- NULL for unmatched
    symbol VARCHAR NOT NULL,

    -- How much was allocated from this buy to this sell
    allocated_size NUMERIC NOT NULL CHECK (allocated_size > 0),

    -- Prices (denormalized for query performance)
    buy_price NUMERIC,                        -- NULL if unmatched
    sell_price NUMERIC NOT NULL,
    buy_fees_per_unit NUMERIC,                -- buy.total_fees_usd / buy.size
    sell_fees_per_unit NUMERIC NOT NULL,      -- sell.total_fees_usd / sell.size

    -- Computed PnL for this allocation
    cost_basis_usd NUMERIC,                   -- (buy_price + buy_fees_per_unit) * allocated_size
    proceeds_usd NUMERIC NOT NULL,            -- sell_price * allocated_size
    net_proceeds_usd NUMERIC NOT NULL,        -- proceeds_usd - (sell_fees_per_unit * allocated_size)
    pnl_usd NUMERIC,                          -- net_proceeds_usd - cost_basis_usd (NULL if unmatched)

    -- Timestamps
    buy_time TIMESTAMP WITH TIME ZONE,        -- NULL if unmatched
    sell_time TIMESTAMP WITH TIME ZONE NOT NULL,

    -- Allocation metadata
    allocation_time TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    allocation_version INT NOT NULL,          -- Increments on recomputation
    allocation_batch_id UUID,                 -- Groups allocations computed together

    -- Notes (for unmatched or special cases)
    notes TEXT,

    -- Constraints
    CHECK (buy_order_id IS NOT NULL OR notes IS NOT NULL),  -- Unmatched must have notes
    CHECK (buy_time IS NULL OR buy_time <= sell_time),      -- Can't sell before buy
    CHECK (cost_basis_usd IS NULL OR cost_basis_usd >= 0),
    CHECK (proceeds_usd >= 0),
    CHECK (net_proceeds_usd >= 0)
);

-- Indexes for performance
CREATE INDEX idx_fifo_allocations_sell ON fifo_allocations(sell_order_id);
CREATE INDEX idx_fifo_allocations_buy ON fifo_allocations(buy_order_id);
CREATE INDEX idx_fifo_allocations_symbol ON fifo_allocations(symbol, allocation_version);
CREATE INDEX idx_fifo_allocations_version ON fifo_allocations(allocation_version);
CREATE INDEX idx_fifo_allocations_batch ON fifo_allocations(allocation_batch_id);
CREATE INDEX idx_fifo_allocations_time ON fifo_allocations(sell_time DESC);

-- Unique constraint: One version can't allocate same buy→sell pair twice
CREATE UNIQUE INDEX idx_fifo_allocations_unique
    ON fifo_allocations(sell_order_id, COALESCE(buy_order_id, ''), allocation_version);

\echo '✅ fifo_allocations table created'
\echo ''

-- -----------------------------------------------------------------------------
-- Table: fifo_computation_log
-- Purpose: Track allocation computation runs for debugging and auditing
-- -----------------------------------------------------------------------------

\echo 'Creating fifo_computation_log table...'

CREATE TABLE IF NOT EXISTS fifo_computation_log (
    id BIGSERIAL PRIMARY KEY,

    -- What was computed
    symbol VARCHAR,                           -- NULL = all symbols
    allocation_version INT NOT NULL,
    allocation_batch_id UUID NOT NULL,

    -- When and how
    computation_start TIMESTAMP WITH TIME ZONE NOT NULL,
    computation_end TIMESTAMP WITH TIME ZONE,
    computation_duration_ms INT,

    -- Results
    status VARCHAR NOT NULL CHECK (status IN ('running', 'completed', 'failed', 'partial')),
    buys_processed INT DEFAULT 0,
    sells_processed INT DEFAULT 0,
    allocations_created INT DEFAULT 0,

    -- Errors
    error_message TEXT,
    error_traceback TEXT,

    -- Statistics
    symbols_processed VARCHAR[],
    total_pnl_computed NUMERIC,

    -- Configuration
    computation_mode VARCHAR,                 -- 'full' | 'incremental' | 'symbol'
    triggered_by VARCHAR,                     -- 'manual' | 'scheduled' | 'api'

    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_fifo_computation_log_version ON fifo_computation_log(allocation_version);
CREATE INDEX idx_fifo_computation_log_batch ON fifo_computation_log(allocation_batch_id);
CREATE INDEX idx_fifo_computation_log_status ON fifo_computation_log(status, computation_start DESC);
CREATE INDEX idx_fifo_computation_log_symbol ON fifo_computation_log(symbol) WHERE symbol IS NOT NULL;

\echo '✅ fifo_computation_log table created'
\echo ''

-- -----------------------------------------------------------------------------
-- Table: fifo_inventory_snapshot
-- Purpose: Periodic snapshots of inventory state for faster incremental computation
-- -----------------------------------------------------------------------------

\echo 'Creating fifo_inventory_snapshot table...'

CREATE TABLE IF NOT EXISTS fifo_inventory_snapshot (
    id BIGSERIAL PRIMARY KEY,
    symbol VARCHAR NOT NULL,
    buy_order_id VARCHAR NOT NULL REFERENCES trade_records(order_id) ON DELETE CASCADE,
    remaining_size NUMERIC NOT NULL CHECK (remaining_size >= 0),
    snapshot_time TIMESTAMP WITH TIME ZONE NOT NULL,
    allocation_version INT NOT NULL,

    UNIQUE(symbol, buy_order_id, allocation_version)
);

CREATE INDEX idx_inventory_snapshot_symbol_version
    ON fifo_inventory_snapshot(symbol, allocation_version, snapshot_time DESC);
CREATE INDEX idx_inventory_snapshot_buy ON fifo_inventory_snapshot(buy_order_id);

\echo '✅ fifo_inventory_snapshot table created'
\echo ''

-- -----------------------------------------------------------------------------
-- Table: manual_review_queue (for unmatched sells)
-- Purpose: Track trades requiring manual investigation
-- -----------------------------------------------------------------------------

\echo 'Creating manual_review_queue table...'

CREATE TABLE IF NOT EXISTS manual_review_queue (
    id BIGSERIAL PRIMARY KEY,
    order_id VARCHAR NOT NULL REFERENCES trade_records(order_id) ON DELETE CASCADE,
    issue_type VARCHAR NOT NULL,              -- 'unmatched_sell' | 'allocation_error' | etc
    severity VARCHAR NOT NULL CHECK (severity IN ('low', 'medium', 'high', 'critical')),
    status VARCHAR NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'in_progress', 'resolved', 'dismissed')),

    -- Details
    description TEXT,
    resolution TEXT,

    -- Timestamps
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    resolved_at TIMESTAMP WITH TIME ZONE,

    -- Resolution tracking
    resolved_by VARCHAR,

    UNIQUE(order_id, issue_type)
);

CREATE INDEX idx_manual_review_queue_status ON manual_review_queue(status, created_at DESC);
CREATE INDEX idx_manual_review_queue_severity ON manual_review_queue(severity, created_at DESC);
CREATE INDEX idx_manual_review_queue_order ON manual_review_queue(order_id);

\echo '✅ manual_review_queue table created'
\echo ''

-- =============================================================================
-- PART 2: VIEWS FOR MONITORING AND REPORTING
-- =============================================================================

\echo '================================================================================'
\echo 'FIFO ALLOCATIONS MIGRATION - Part 2: Creating Views'
\echo '================================================================================'
\echo ''

-- -----------------------------------------------------------------------------
-- View: v_allocation_health
-- Purpose: Quick health check for allocation system
-- -----------------------------------------------------------------------------

\echo 'Creating v_allocation_health view...'

CREATE OR REPLACE VIEW v_allocation_health AS
SELECT
    allocation_version,
    COUNT(*) as total_allocations,
    COUNT(DISTINCT sell_order_id) as sells_matched,
    COUNT(DISTINCT buy_order_id) FILTER (WHERE buy_order_id IS NOT NULL) as buys_used,
    COUNT(*) FILTER (WHERE buy_order_id IS NULL) as unmatched_sells,
    SUM(pnl_usd) as total_pnl,
    MIN(allocation_time) as first_allocation,
    MAX(allocation_time) as last_allocation
FROM fifo_allocations
GROUP BY allocation_version
ORDER BY allocation_version DESC;

\echo '✅ v_allocation_health view created'
\echo ''

-- -----------------------------------------------------------------------------
-- View: v_unmatched_sells
-- Purpose: Track sells with no matching buy (shouldn't exist in normal operation)
-- -----------------------------------------------------------------------------

\echo 'Creating v_unmatched_sells view...'

CREATE OR REPLACE VIEW v_unmatched_sells AS
SELECT
    a.id as allocation_id,
    a.sell_order_id,
    a.symbol,
    a.allocated_size as unmatched_size,
    a.sell_time,
    a.sell_price,
    a.allocation_version,
    a.notes,
    'NEEDS INVESTIGATION' as status
FROM fifo_allocations a
WHERE a.buy_order_id IS NULL
ORDER BY a.sell_time DESC;

\echo '✅ v_unmatched_sells view created'
\echo ''

-- -----------------------------------------------------------------------------
-- View: v_pnl_by_symbol
-- Purpose: Current PnL by symbol (latest version)
-- -----------------------------------------------------------------------------

\echo 'Creating v_pnl_by_symbol view...'

CREATE OR REPLACE VIEW v_pnl_by_symbol AS
WITH latest_version AS (
    SELECT COALESCE(MAX(allocation_version), 0) as version
    FROM fifo_allocations
)
SELECT
    a.symbol,
    COUNT(DISTINCT a.sell_order_id) as num_sells,
    COUNT(DISTINCT a.buy_order_id) FILTER (WHERE a.buy_order_id IS NOT NULL) as num_buys,
    SUM(a.allocated_size) as total_size,
    SUM(a.pnl_usd) as total_pnl,
    AVG(a.pnl_usd) as avg_pnl_per_allocation,
    MIN(a.sell_time) as first_sell,
    MAX(a.sell_time) as last_sell
FROM fifo_allocations a
CROSS JOIN latest_version lv
WHERE a.allocation_version = lv.version
GROUP BY a.symbol
ORDER BY total_pnl DESC;

\echo '✅ v_pnl_by_symbol view created'
\echo ''

-- -----------------------------------------------------------------------------
-- View: v_allocation_discrepancies
-- Purpose: Find sells where allocated size doesn't match sell size
-- -----------------------------------------------------------------------------

\echo 'Creating v_allocation_discrepancies view...'

CREATE OR REPLACE VIEW v_allocation_discrepancies AS
WITH latest_version AS (
    SELECT COALESCE(MAX(allocation_version), 0) as version
    FROM fifo_allocations
)
SELECT
    s.order_id as sell_order_id,
    s.symbol,
    s.size as sell_size,
    COALESCE(SUM(a.allocated_size), 0) as allocated_total,
    s.size - COALESCE(SUM(a.allocated_size), 0) as discrepancy,
    CASE
        WHEN ABS(s.size - COALESCE(SUM(a.allocated_size), 0)) <= 0.00001 THEN 'OK'
        WHEN s.size - COALESCE(SUM(a.allocated_size), 0) > 0.00001 THEN 'UNDER_ALLOCATED'
        ELSE 'OVER_ALLOCATED'
    END as status
FROM trade_records s
CROSS JOIN latest_version lv
LEFT JOIN fifo_allocations a
    ON a.sell_order_id = s.order_id
    AND a.allocation_version = lv.version
WHERE s.side = 'sell'
GROUP BY s.order_id, s.symbol, s.size, lv.version
HAVING ABS(s.size - COALESCE(SUM(a.allocated_size), 0)) > 0.00001
ORDER BY ABS(s.size - COALESCE(SUM(a.allocated_size), 0)) DESC;

\echo '✅ v_allocation_discrepancies view created'
\echo ''

-- =============================================================================
-- PART 3: HISTORICAL DATA SNAPSHOT (BEFORE MIGRATION)
-- =============================================================================

\echo '================================================================================'
\echo 'FIFO ALLOCATIONS MIGRATION - Part 3: Historical Data Snapshot'
\echo '================================================================================'
\echo ''

\echo 'Creating historical snapshot of old PnL data...'

-- Preserve old corrupted data for reference (forensics, tax reporting)
CREATE TABLE IF NOT EXISTS historical_pnl_snapshot_20251120 AS
SELECT
    order_id,
    parent_id as old_parent_id,
    parent_ids as old_parent_ids,
    pnl_usd as old_pnl_usd,
    cost_basis_usd as old_cost_basis,
    sale_proceeds_usd as old_sale_proceeds,
    net_sale_proceeds_usd as old_net_sale_proceeds,
    realized_profit as old_realized_profit,
    remaining_size as old_remaining_size,
    'corrupted_historical' as note,
    NOW() as snapshot_time
FROM trade_records
WHERE side = 'sell'
  AND parent_id IS NOT NULL;  -- Only snapshot sells that had parent linkage

\echo '✅ Historical snapshot created: historical_pnl_snapshot_20251120'
\echo '   Rows preserved: ' || (SELECT COUNT(*) FROM historical_pnl_snapshot_20251120);
\echo ''

-- =============================================================================
-- PART 4: MIGRATION COMPLETE
-- =============================================================================

\echo '================================================================================'
\echo 'FIFO ALLOCATIONS MIGRATION COMPLETE'
\echo '================================================================================'
\echo ''
\echo 'Summary:'
\echo '  ✅ New tables created: fifo_allocations, fifo_computation_log,'
\echo '     fifo_inventory_snapshot, manual_review_queue'
\echo '  ✅ Views created: v_allocation_health, v_unmatched_sells,'
\echo '     v_pnl_by_symbol, v_allocation_discrepancies'
\echo '  ✅ Historical PnL data preserved in: historical_pnl_snapshot_20251120'
\echo ''
\echo 'Next Steps:'
\echo '  1. Run FIFO allocation bootstrap to compute Version 1 allocations'
\echo '  2. Validate allocations using verification queries'
\echo '  3. Parallel operation: Keep old parent_id fields for comparison'
\echo '  4. After validation period, deprecate old columns'
\echo ''
\echo 'Rollback: No destructive changes made to existing tables.'
\echo '  To rollback, simply drop new tables and views.'
\echo '================================================================================'
