-- Delete Bad SELL Records for Re-reconciliation
-- These will be re-fetched by next reconciliation cycle with correct FIFO logic

\echo '================================================================================'
\echo 'DELETING BAD SELL RECORDS FOR RE-RECONCILIATION'
\echo '================================================================================'
\echo ''

\echo 'Records to be deleted (will be re-reconciled with fixed code):'
\echo ''
SELECT
    symbol,
    to_char(order_time, 'MM-DD HH24:MI:SS') as time,
    SUBSTRING(order_id, 1, 8) as id,
    ROUND(pnl_usd::numeric, 2) as bad_pnl,
    to_char((SELECT order_time FROM trade_records WHERE order_id = trade_records.parent_ids[1]), 'MM-DD') as old_parent_date
FROM trade_records
WHERE side = 'sell'
  AND order_time >= '2025-11-19 22:26:00+00'
  AND parent_ids[1] IN (
      SELECT order_id FROM trade_records
      WHERE side = 'buy' AND order_time < '2025-11-19 00:00:00+00'
  )
ORDER BY order_time;

\echo ''
\echo 'Deleting these records...'
\echo ''

-- Backup first
CREATE TABLE IF NOT EXISTS trade_records_backup_bad_sells_20251120 AS
SELECT * FROM trade_records
WHERE side = 'sell'
  AND order_time >= '2025-11-19 22:26:00+00'
  AND parent_ids[1] IN (
      SELECT order_id FROM trade_records
      WHERE side = 'buy' AND order_time < '2025-11-19 00:00:00+00'
  );

-- Delete the bad sells
DELETE FROM trade_records
WHERE side = 'sell'
  AND order_time >= '2025-11-19 22:26:00+00'
  AND parent_ids[1] IN (
      SELECT order_id FROM trade_records
      WHERE side = 'buy' AND order_time < '2025-11-19 00:00:00+00'
  );

\echo ''
\echo '================================================================================'
\echo 'DONE - Bad sells deleted and backed up'
\echo '================================================================================'
\echo ''
\echo 'Next steps:'
\echo '1. Wait for next reconciliation cycle (runs hourly) OR restart bot to trigger'
\echo '2. Bot will re-fetch these orders with fixed code'
\echo '3. They will get correct FIFO parent matching'
\echo '4. Run verify_pnl_fix.sql to confirm'
\echo ''
\echo 'Backup table: trade_records_backup_bad_sells_20251120'
\echo '================================================================================'
