-- Rollback Migration: Remove FIFO Allocations Tables
-- Version: 001
-- Date: 2025-11-20
-- Description: Rollback script to remove all FIFO allocation tables and views

\echo '================================================================================'
\echo 'FIFO ALLOCATIONS ROLLBACK - Removing Tables and Views'
\echo '================================================================================'
\echo ''
\echo 'WARNING: This will delete all FIFO allocation data!'
\echo '         Historical snapshot (historical_pnl_snapshot_20251120) will be preserved.'
\echo ''

-- Confirm before proceeding
\prompt 'Type YES to confirm rollback: ' confirmation

\if :'confirmation' = 'YES'

    \echo 'Proceeding with rollback...'
    \echo ''

    -- Drop views first (they depend on tables)
    \echo 'Dropping views...'
    DROP VIEW IF EXISTS v_allocation_discrepancies CASCADE;
    DROP VIEW IF EXISTS v_pnl_by_symbol CASCADE;
    DROP VIEW IF EXISTS v_unmatched_sells CASCADE;
    DROP VIEW IF EXISTS v_allocation_health CASCADE;
    \echo '✅ Views dropped'
    \echo ''

    -- Drop tables
    \echo 'Dropping tables...'
    DROP TABLE IF EXISTS fifo_inventory_snapshot CASCADE;
    DROP TABLE IF EXISTS fifo_computation_log CASCADE;
    DROP TABLE IF EXISTS manual_review_queue CASCADE;
    DROP TABLE IF EXISTS fifo_allocations CASCADE;
    \echo '✅ Tables dropped'
    \echo ''

    -- Note: We preserve historical_pnl_snapshot_20251120 for forensic analysis
    \echo 'Historical snapshot preserved: historical_pnl_snapshot_20251120'
    \echo ''

    \echo '================================================================================'
    \echo 'ROLLBACK COMPLETE'
    \echo '================================================================================'
    \echo ''
    \echo 'The FIFO allocation system has been removed.'
    \echo 'The old parent_id/pnl_usd columns in trade_records remain unchanged.'
    \echo ''

\else

    \echo 'Rollback cancelled.'

\endif
