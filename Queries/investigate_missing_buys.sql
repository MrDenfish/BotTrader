-- Investigation: Missing Buy Records for Unmatched Sells
-- ============================================================
-- This query investigates the 19 unmatched sells to determine if they have
-- corresponding buy orders that are missing from the database

-- Step 1: Get all unmatched sells with their details
WITH unmatched_sells AS (
    SELECT
        fa.sell_order_id,
        tr.symbol,
        tr.size,
        tr.price,
        tr.order_time,
        fa.notes
    FROM fifo_allocations fa
    JOIN trade_records tr ON tr.order_id = fa.sell_order_id
    WHERE fa.allocation_version = 1
      AND fa.buy_order_id IS NULL
    ORDER BY tr.symbol, tr.order_time
),

-- Step 2: For each symbol with unmatched sells, get ALL buys and sells
symbol_trade_analysis AS (
    SELECT
        tr.symbol,
        COUNT(*) FILTER (WHERE tr.side = 'buy') as buy_count,
        COUNT(*) FILTER (WHERE tr.side = 'sell') as sell_count,
        SUM(tr.size) FILTER (WHERE tr.side = 'buy') as total_buy_size,
        SUM(tr.size) FILTER (WHERE tr.side = 'sell') as total_sell_size,
        MIN(tr.order_time) FILTER (WHERE tr.side = 'buy') as first_buy_time,
        MIN(tr.order_time) FILTER (WHERE tr.side = 'sell') as first_sell_time,
        MAX(tr.order_time) FILTER (WHERE tr.side = 'buy') as last_buy_time,
        MAX(tr.order_time) FILTER (WHERE tr.side = 'sell') as last_sell_time
    FROM trade_records tr
    WHERE tr.symbol IN (SELECT DISTINCT symbol FROM unmatched_sells)
    GROUP BY tr.symbol
),

-- Step 3: Check inventory exhaustion timing
inventory_check AS (
    SELECT
        us.symbol,
        us.sell_order_id,
        us.size as sell_size,
        us.order_time as sell_time,
        sta.total_buy_size,
        sta.total_sell_size,
        (sta.total_buy_size - sta.total_sell_size) as net_inventory,
        sta.buy_count,
        sta.sell_count,
        us.notes,
        -- Calculate cumulative inventory AT THE TIME of this sell
        (
            SELECT
                SUM(t2.size) FILTER (WHERE t2.side = 'buy') -
                SUM(t2.size) FILTER (WHERE t2.side = 'sell')
            FROM trade_records t2
            WHERE t2.symbol = us.symbol
              AND t2.order_time <= us.order_time
        ) as inventory_at_sell_time
    FROM unmatched_sells us
    JOIN symbol_trade_analysis sta ON sta.symbol = us.symbol
)

-- Final output
SELECT
    symbol,
    sell_order_id,
    sell_size,
    TO_CHAR(sell_time, 'YYYY-MM-DD HH24:MI:SS') as sell_time,
    buy_count,
    sell_count,
    ROUND(total_buy_size::numeric, 4) as total_buy_size,
    ROUND(total_sell_size::numeric, 4) as total_sell_size,
    ROUND(net_inventory::numeric, 4) as net_inventory,
    ROUND(inventory_at_sell_time::numeric, 4) as inventory_at_sell_time,
    CASE
        WHEN buy_count = 0 THEN 'NO BUYS IN DATABASE'
        WHEN inventory_at_sell_time < 0 THEN '⚠️  NEGATIVE INVENTORY - MISSING BUYS'
        WHEN inventory_at_sell_time < sell_size THEN '⚠️  INSUFFICIENT INVENTORY - MISSING BUYS'
        ELSE 'Legitimate exhaustion (or timing issue)'
    END as diagnosis,
    notes
FROM inventory_check
ORDER BY
    CASE
        WHEN buy_count = 0 THEN 1
        WHEN inventory_at_sell_time < 0 THEN 2
        WHEN inventory_at_sell_time < sell_size THEN 3
        ELSE 4
    END,
    symbol,
    sell_time;


-- Additional diagnostic: Check for gaps in order_time sequences
-- ============================================================
\echo ''
\echo '============================================================'
\echo 'TIME GAP ANALYSIS: Looking for suspicious gaps in trade times'
\echo '============================================================'
\echo ''

WITH trade_gaps AS (
    SELECT
        symbol,
        side,
        order_id,
        order_time,
        LAG(order_time) OVER (PARTITION BY symbol, side ORDER BY order_time) as prev_time,
        order_time - LAG(order_time) OVER (PARTITION BY symbol, side ORDER BY order_time) as time_gap
    FROM trade_records
    WHERE symbol IN (
        SELECT DISTINCT tr.symbol
        FROM fifo_allocations fa
        JOIN trade_records tr ON tr.order_id = fa.sell_order_id
        WHERE fa.allocation_version = 1 AND fa.buy_order_id IS NULL
    )
)
SELECT
    symbol,
    side,
    COUNT(*) as gap_count,
    MAX(time_gap) as max_gap,
    AVG(time_gap) as avg_gap
FROM trade_gaps
WHERE time_gap > INTERVAL '1 hour'  -- Suspicious gaps > 1 hour
GROUP BY symbol, side
ORDER BY symbol, side;


-- Check for round-trip trades (buy then sell same size within short time)
-- ============================================================
\echo ''
\echo '============================================================'
\echo 'ROUND-TRIP TRADES: Looking for buy→sell pairs with same size'
\echo '============================================================'
\echo ''

WITH buys AS (
    SELECT
        order_id,
        symbol,
        size,
        order_time
    FROM trade_records
    WHERE side = 'buy'
      AND symbol IN (
          SELECT DISTINCT tr.symbol
          FROM fifo_allocations fa
          JOIN trade_records tr ON tr.order_id = fa.sell_order_id
          WHERE fa.allocation_version = 1 AND fa.buy_order_id IS NULL
      )
),
sells AS (
    SELECT
        order_id,
        symbol,
        size,
        order_time
    FROM trade_records
    WHERE side = 'sell'
      AND symbol IN (
          SELECT DISTINCT tr.symbol
          FROM fifo_allocations fa
          JOIN trade_records tr ON tr.order_id = fa.sell_order_id
          WHERE fa.allocation_version = 1 AND fa.buy_order_id IS NULL
      )
)
SELECT
    b.symbol,
    COUNT(*) as matching_round_trips,
    ARRAY_AGG(DISTINCT b.size ORDER BY b.size) as common_sizes
FROM buys b
JOIN sells s ON s.symbol = b.symbol
    AND ABS(s.size - b.size) < 0.0001  -- Same size
    AND s.order_time > b.order_time  -- Sell after buy
    AND s.order_time - b.order_time < INTERVAL '1 day'  -- Within 1 day
GROUP BY b.symbol
ORDER BY matching_round_trips DESC;
