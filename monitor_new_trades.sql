-- Monitor New Trades After Bot Restart
-- Run this periodically to check if new trades are getting correct parent matching

\echo '================================================================================'
\echo 'NEW TRADES MONITOR - Checking trades from last 30 minutes'
\echo '================================================================================'
\echo ''

\echo 'All recent trades (BUY and SELL):'
\echo '--------------------------------------------------------------------------------'
SELECT
    to_char(order_time, 'HH24:MI:SS') as time,
    symbol,
    side,
    SUBSTRING(order_id, 1, 8) as id,
    ROUND(price::numeric, 2) as price,
    CASE
        WHEN side = 'sell' THEN
            COALESCE('parent: ' || to_char((SELECT order_time FROM trade_records b WHERE b.order_id = trade_records.parent_ids[1]), 'MM-DD HH24:MI'), 'parent: NULL ✅')
        ELSE
            'remaining: ' || ROUND(COALESCE(remaining_size, size)::numeric, 8)
    END as info,
    CASE
        WHEN side = 'sell' THEN ROUND(pnl_usd::numeric, 2)
        ELSE NULL
    END as pnl
FROM trade_records
WHERE order_time >= NOW() - INTERVAL '30 minutes'
ORDER BY order_time DESC;

\echo ''
\echo '================================================================================'
\echo 'INTERPRETATION:'
\echo '- For SELL orders, check that parent_time is close to sell time (same day/hour)'
\echo '- If parent is weeks old (Oct/early Nov), the fix is NOT working'
\echo '- If parent is NULL, FIFO logic will compute it (also acceptable)'
\echo '- PnL should be small (cents to few dollars), not $13+ false profits'
\echo '================================================================================'
