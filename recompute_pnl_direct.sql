-- Direct FIFO Recomputation for Affected Symbols
-- This fixes parent linkages and PnL without deleting data

\echo '================================================================================'
\echo 'DIRECT FIFO PNL RECOMPUTATION'
\echo '================================================================================'
\echo ''

-- For DASH-USD, match recent sells to recent buys in FIFO order
\echo 'Fixing DASH-USD parent linkages...'

-- Recent sells (Nov 19)
UPDATE trade_records SET
    parent_id = (
        SELECT order_id FROM trade_records b
        WHERE b.symbol = 'DASH-USD'
          AND b.side = 'buy'
          AND b.order_time <= trade_records.order_time
          AND COALESCE(b.remaining_size, b.size) > 0
          AND b.order_id NOT LIKE '%-FILL-%'
          AND b.order_time >= '2025-11-19 00:00:00+00'
        ORDER BY b.order_time ASC
        LIMIT 1
    ),
    parent_ids = ARRAY[(
        SELECT order_id FROM trade_records b
        WHERE b.symbol = 'DASH-USD'
          AND b.side = 'buy'
          AND b.order_time <= trade_records.order_time
          AND COALESCE(b.remaining_size, b.size) > 0
          AND b.order_id NOT LIKE '%-FILL-%'
          AND b.order_time >= '2025-11-19 00:00:00+00'
        ORDER BY b.order_time ASC
        LIMIT 1
    )],
    cost_basis_usd = (
        SELECT (b.price * trade_records.size + b.total_fees_usd * (trade_records.size / b.size))
        FROM trade_records b
        WHERE b.symbol = 'DASH-USD'
          AND b.side = 'buy'
          AND b.order_time <= trade_records.order_time
          AND COALESCE(b.remaining_size, b.size) > 0
          AND b.order_id NOT LIKE '%-FILL-%'
          AND b.order_time >= '2025-11-19 00:00:00+00'
        ORDER BY b.order_time ASC
        LIMIT 1
    ),
    sale_proceeds_usd = (price * size),
    net_sale_proceeds_usd = (price * size - total_fees_usd),
    pnl_usd = (price * size - total_fees_usd) - (
        SELECT (b.price * trade_records.size + b.total_fees_usd * (trade_records.size / b.size))
        FROM trade_records b
        WHERE b.symbol = 'DASH-USD'
          AND b.side = 'buy'
          AND b.order_time <= trade_records.order_time
          AND COALESCE(b.remaining_size, b.size) > 0
          AND b.order_id NOT LIKE '%-FILL-%'
          AND b.order_time >= '2025-11-19 00:00:00+00'
        ORDER BY b.order_time ASC
        LIMIT 1
    ),
    realized_profit = (price * size - total_fees_usd) - (
        SELECT (b.price * trade_records.size + b.total_fees_usd * (trade_records.size / b.size))
        FROM trade_records b
        WHERE b.symbol = 'DASH-USD'
          AND b.side = 'buy'
          AND b.order_time <= trade_records.order_time
          AND COALESCE(b.remaining_size, b.size) > 0
          AND b.order_id NOT LIKE '%-FILL-%'
          AND b.order_time >= '2025-11-19 00:00:00+00'
        ORDER BY b.order_time ASC
        LIMIT 1
    )
WHERE symbol = 'DASH-USD'
  AND side = 'sell'
  AND order_time >= '2025-11-19 00:00:00+00';

\echo 'Done!'
\echo ''
\echo 'Fixing ZEC-USD parent linkages...'

-- Same for ZEC-USD
UPDATE trade_records SET
    parent_id = (
        SELECT order_id FROM trade_records b
        WHERE b.symbol = 'ZEC-USD'
          AND b.side = 'buy'
          AND b.order_time <= trade_records.order_time
          AND COALESCE(b.remaining_size, b.size) > 0
          AND b.order_id NOT LIKE '%-FILL-%'
          AND b.order_time >= '2025-11-19 00:00:00+00'
        ORDER BY b.order_time ASC
        LIMIT 1
    ),
    parent_ids = ARRAY[(
        SELECT order_id FROM trade_records b
        WHERE b.symbol = 'ZEC-USD'
          AND b.side = 'buy'
          AND b.order_time <= trade_records.order_time
          AND COALESCE(b.remaining_size, b.size) > 0
          AND b.order_id NOT LIKE '%-FILL-%'
          AND b.order_time >= '2025-11-19 00:00:00+00'
        ORDER BY b.order_time ASC
        LIMIT 1
    )],
    cost_basis_usd = (
        SELECT (b.price * trade_records.size + b.total_fees_usd * (trade_records.size / b.size))
        FROM trade_records b
        WHERE b.symbol = 'ZEC-USD'
          AND b.side = 'buy'
          AND b.order_time <= trade_records.order_time
          AND COALESCE(b.remaining_size, b.size) > 0
          AND b.order_id NOT LIKE '%-FILL-%'
          AND b.order_time >= '2025-11-19 00:00:00+00'
        ORDER BY b.order_time ASC
        LIMIT 1
    ),
    sale_proceeds_usd = (price * size),
    net_sale_proceeds_usd = (price * size - total_fees_usd),
    pnl_usd = (price * size - total_fees_usd) - (
        SELECT (b.price * trade_records.size + b.total_fees_usd * (trade_records.size / b.size))
        FROM trade_records b
        WHERE b.symbol = 'ZEC-USD'
          AND b.side = 'buy'
          AND b.order_time <= trade_records.order_time
          AND COALESCE(b.remaining_size, b.size) > 0
          AND b.order_id NOT LIKE '%-FILL-%'
          AND b.order_time >= '2025-11-19 00:00:00+00'
        ORDER BY b.order_time ASC
        LIMIT 1
    ),
    realized_profit = (price * size - total_fees_usd) - (
        SELECT (b.price * trade_records.size + b.total_fees_usd * (trade_records.size / b.size))
        FROM trade_records b
        WHERE b.symbol = 'ZEC-USD'
          AND b.side = 'buy'
          AND b.order_time <= trade_records.order_time
          AND COALESCE(b.remaining_size, b.size) > 0
          AND b.order_id NOT LIKE '%-FILL-%'
          AND b.order_time >= '2025-11-19 00:00:00+00'
        ORDER BY b.order_time ASC
        LIMIT 1
    )
WHERE symbol = 'ZEC-USD'
  AND side = 'sell'
  AND order_time >= '2025-11-19 00:00:00+00';

\echo 'Done!'
\echo ''
\echo '================================================================================'
\echo 'RECOMPUTATION COMPLETE'
\echo '================================================================================'
\echo ''
\echo 'Verification: Run verify_pnl_fix.sql to confirm PnL is now correct'
\echo '================================================================================'
