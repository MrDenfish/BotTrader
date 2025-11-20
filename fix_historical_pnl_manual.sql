-- Manual Fix for Historical PnL Data
-- This script removes SELL records with wrong parent linkages
-- and lets them be re-reconciled with the fixed code

\echo '================================================================================'
\echo 'MANUAL PNL FIX - Delete and Re-reconcile'
\echo '================================================================================'
\echo ''
\echo 'This will:'
\echo '1. Delete SELL records that have stale parent_ids from Coinbase'
\echo '2. Bot will re-reconcile them on next run with correct FIFO logic'
\echo ''
\echo 'IMPORTANT: Only run this AFTER committing the code fixes!'
\echo ''

\echo 'Step 1: Backup affected records (just in case)...'
CREATE TABLE IF NOT EXISTS trade_records_backup_20251119 AS
SELECT * FROM trade_records
WHERE side = 'sell'
  AND source = 'websocket'
  AND ingest_via = 'rest'
  AND order_time >= '2025-11-19 00:00:00+00';

\echo ''
\echo 'Backup complete. Records backed up to: trade_records_backup_20251119'
\echo ''

\echo 'Step 2: Delete SELL records with wrong parents (from today)...'
\echo ''

BEGIN;

-- Delete SELLs that were ingested via REST with potentially stale parents
DELETE FROM trade_records
WHERE side = 'sell'
  AND source = 'websocket'
  AND ingest_via = 'rest'
  AND order_time >= '2025-11-19 00:00:00+00';

\echo ''
SELECT COUNT(*) as deleted_records FROM trade_records_backup_20251119;
\echo ''

COMMIT;

\echo ''
\echo '================================================================================'
\echo 'MANUAL FIX COMPLETE'
\echo '================================================================================'
\echo ''
\echo 'Next steps:'
\echo '1. Restart the bot to trigger reconciliation'
\echo '2. Bot will re-fetch these orders from Coinbase REST API'
\echo '3. With fixed code, parent_id will be NULL and FIFO logic will compute correct match'
\echo '4. Run verify_pnl_fix.sql again to confirm PnL is now correct'
\echo ''
\echo 'Or alternatively:'
\echo 'Just wait for next reconciliation cycle (runs every hour)'
\echo '================================================================================'
