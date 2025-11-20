-- Fix Recent SELL Records with Stale Parent IDs
-- These 9 sells have parents from weeks/months ago instead of same-day buys

\echo '================================================================================'
\echo 'FIXING RECENT SELLS WITH STALE PARENT IDS'
\echo '================================================================================'
\echo ''

\echo 'Affected records (before fix):'
SELECT
    s.symbol,
    to_char(s.order_time, 'MM-DD HH24:MI:SS') as sell_time,
    SUBSTRING(s.order_id, 1, 8) as sell_id,
    to_char(b.order_time, 'MM-DD') as old_parent_date,
    ROUND(s.pnl_usd::numeric, 2) as old_pnl
FROM trade_records s
LEFT JOIN trade_records b ON b.order_id = s.parent_ids[1]
WHERE s.order_time >= '2025-11-19 22:26:00+00'
  AND s.side = 'sell'
  AND b.order_time < '2025-11-19 00:00:00+00'
ORDER BY s.order_time;

\echo ''
\echo 'Recomputing FIFO parent linkages for these 9 sells...'
\echo ''

-- STRK-USD: 3 sells
UPDATE trade_records SET
    parent_id = (
        SELECT order_id FROM trade_records b
        WHERE b.symbol = trade_records.symbol
          AND b.side = 'buy'
          AND b.order_time <= trade_records.order_time
          AND COALESCE(b.remaining_size, b.size) > 0
        ORDER BY b.order_time ASC
        LIMIT 1
    ),
    parent_ids = ARRAY[(
        SELECT order_id FROM trade_records b
        WHERE b.symbol = trade_records.symbol
          AND b.side = 'buy'
          AND b.order_time <= trade_records.order_time
          AND COALESCE(b.remaining_size, b.size) > 0
        ORDER BY b.order_time ASC
        LIMIT 1
    )],
    cost_basis_usd = (
        SELECT (b.price * trade_records.size + b.total_fees_usd * (trade_records.size / b.size))
        FROM trade_records b
        WHERE b.symbol = trade_records.symbol
          AND b.side = 'buy'
          AND b.order_time <= trade_records.order_time
          AND COALESCE(b.remaining_size, b.size) > 0
        ORDER BY b.order_time ASC
        LIMIT 1
    ),
    pnl_usd = (price * size - total_fees_usd) - (
        SELECT (b.price * trade_records.size + b.total_fees_usd * (trade_records.size / b.size))
        FROM trade_records b
        WHERE b.symbol = trade_records.symbol
          AND b.side = 'buy'
          AND b.order_time <= trade_records.order_time
          AND COALESCE(b.remaining_size, b.size) > 0
        ORDER BY b.order_time ASC
        LIMIT 1
    ),
    realized_profit = (price * size - total_fees_usd) - (
        SELECT (b.price * trade_records.size + b.total_fees_usd * (trade_records.size / b.size))
        FROM trade_records b
        WHERE b.symbol = trade_records.symbol
          AND b.side = 'buy'
          AND b.order_time <= trade_records.order_time
          AND COALESCE(b.remaining_size, b.size) > 0
        ORDER BY b.order_time ASC
        LIMIT 1
    ),
    sale_proceeds_usd = (price * size),
    net_sale_proceeds_usd = (price * size - total_fees_usd)
WHERE symbol IN ('STRK-USD', 'FET-USD', 'SOL-USD', 'BCH-USD', 'CLANKER-USD')
  AND side = 'sell'
  AND order_time >= '2025-11-19 22:26:00+00'
  AND parent_ids[1] IN (
      SELECT order_id FROM trade_records
      WHERE side = 'buy' AND order_time < '2025-11-19 00:00:00+00'
  );

\echo ''
\echo 'Verification (after fix):'
SELECT
    s.symbol,
    to_char(s.order_time, 'MM-DD HH24:MI:SS') as sell_time,
    SUBSTRING(s.order_id, 1, 8) as sell_id,
    to_char(b.order_time, 'MM-DD HH24:MI') as new_parent_time,
    ROUND(s.pnl_usd::numeric, 2) as new_pnl
FROM trade_records s
LEFT JOIN trade_records b ON b.order_id = s.parent_ids[1]
WHERE s.symbol IN ('STRK-USD', 'FET-USD', 'SOL-USD', 'BCH-USD', 'CLANKER-USD')
  AND s.side = 'sell'
  AND s.order_time >= '2025-11-19 22:26:00+00'
ORDER BY s.order_time;

\echo ''
\echo '================================================================================'
\echo 'DONE - Parent linkages should now show recent dates'
\echo '================================================================================'
