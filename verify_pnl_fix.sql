-- Verify PnL Fix Status
-- This script checks if the parent matching is now correct after code fixes

\echo '================================================================================'
\echo 'VERIFICATION QUERIES FOR PNL FIX'
\echo '================================================================================'
\echo ''

\echo '1. Check current DASH-USD parent matching (should show recent BUYs matched to recent SELLs):'
\echo '--------------------------------------------------------------------------------'
SELECT
    s.order_id as sell_id,
    to_char(s.order_time, 'MM-DD HH24:MI') as sell_time,
    ROUND(s.price::numeric, 2) as sell_price,
    s.parent_ids[1] as parent_id,
    to_char(b.order_time, 'MM-DD HH24:MI') as parent_time,
    ROUND(b.price::numeric, 2) as parent_price,
    ROUND(s.pnl_usd::numeric, 2) as pnl,
    s.source,
    s.ingest_via
FROM trade_records s
LEFT JOIN trade_records b ON b.order_id = s.parent_ids[1]
WHERE s.symbol = 'DASH-USD'
  AND s.side = 'sell'
  AND s.order_time >= NOW() - INTERVAL '24 hours'
ORDER BY s.order_time DESC
LIMIT 10;

\echo ''
\echo '2. Check DASH-USD available inventory (should show recent BUYs with remaining_size > 0):'
\echo '--------------------------------------------------------------------------------'
SELECT
    order_id,
    to_char(order_time, 'MM-DD HH24:MI') as time,
    ROUND(price::numeric, 2) as price,
    ROUND(size::numeric, 8) as size,
    ROUND(remaining_size::numeric, 8) as remaining,
    CASE
        WHEN COALESCE(remaining_size, 0) > 0 THEN 'AVAILABLE'
        ELSE 'CONSUMED'
    END as status
FROM trade_records
WHERE symbol = 'DASH-USD'
  AND side = 'buy'
  AND order_time >= NOW() - INTERVAL '24 hours'
ORDER BY order_time DESC;

\echo ''
\echo '3. Calculate actual DASH-USD PnL (last 8 hours):'
\echo '--------------------------------------------------------------------------------'
WITH buy_cost AS (
    SELECT SUM(price * size + total_fees_usd) as total
    FROM trade_records
    WHERE symbol = 'DASH-USD'
      AND side = 'buy'
      AND order_time >= NOW() - INTERVAL '8 hours'
),
sell_proceeds AS (
    SELECT SUM(price * size - total_fees_usd) as total
    FROM trade_records
    WHERE symbol = 'DASH-USD'
      AND side = 'sell'
      AND order_time >= NOW() - INTERVAL '8 hours'
),
db_pnl AS (
    SELECT SUM(pnl_usd) as total
    FROM trade_records
    WHERE symbol = 'DASH-USD'
      AND side = 'sell'
      AND order_time >= NOW() - INTERVAL '8 hours'
)
SELECT
    ROUND(b.total::numeric, 2) as buy_cost,
    ROUND(s.total::numeric, 2) as sell_proceeds,
    ROUND((s.total - b.total)::numeric, 2) as expected_pnl,
    ROUND(d.total::numeric, 2) as database_pnl,
    ROUND((d.total - (s.total - b.total))::numeric, 2) as discrepancy
FROM buy_cost b, sell_proceeds s, db_pnl d;

\echo ''
\echo '4. Same for ZEC-USD:'
\echo '--------------------------------------------------------------------------------'
WITH buy_cost AS (
    SELECT SUM(price * size + total_fees_usd) as total
    FROM trade_records
    WHERE symbol = 'ZEC-USD'
      AND side = 'buy'
      AND order_time >= NOW() - INTERVAL '8 hours'
),
sell_proceeds AS (
    SELECT SUM(price * size - total_fees_usd) as total
    FROM trade_records
    WHERE symbol = 'ZEC-USD'
      AND side = 'sell'
      AND order_time >= NOW() - INTERVAL '8 hours'
),
db_pnl AS (
    SELECT SUM(pnl_usd) as total
    FROM trade_records
    WHERE symbol = 'ZEC-USD'
      AND side = 'sell'
      AND order_time >= NOW() - INTERVAL '8 hours'
)
SELECT
    ROUND(b.total::numeric, 2) as buy_cost,
    ROUND(s.total::numeric, 2) as sell_proceeds,
    ROUND((s.total - b.total)::numeric, 2) as expected_pnl,
    ROUND(d.total::numeric, 2) as database_pnl,
    ROUND((d.total - (s.total - b.total))::numeric, 2) as discrepancy
FROM buy_cost b, sell_proceeds s, db_pnl d;

\echo ''
\echo '================================================================================'
\echo 'INTERPRETATION:'
\echo '- If parent dates match sell dates (same day), fix is working'
\echo '- If discrepancy is near 0, PnL is correct'
\echo '- If discrepancy is large (>$30), old data still has wrong parents'
\echo '================================================================================'
