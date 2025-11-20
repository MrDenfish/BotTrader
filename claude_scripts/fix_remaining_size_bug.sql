-- ================================================
-- CLANKER Position Bug Fix - Part 1: Data Correction
-- ================================================
-- This fixes FILLED buy orders that incorrectly have 
-- remaining_size = size instead of 0
-- ================================================

-- Step 1: Check how many rows will be affected
SELECT 
    'AFFECTED ROWS' as check_type,
    COUNT(*) as count,
    SUM(size) as total_size,
    COUNT(DISTINCT symbol) as unique_symbols
FROM trade_records
WHERE status = 'filled'
  AND side = 'buy'
  AND remaining_size = size
  AND remaining_size IS NOT NULL;

-- Step 2: Show sample of affected records
SELECT 
    'SAMPLE DATA' as info,
    symbol,
    side,
    status,
    size,
    remaining_size,
    order_time
FROM trade_records
WHERE status = 'filled'
  AND side = 'buy'
  AND remaining_size = size
  AND remaining_size IS NOT NULL
ORDER BY order_time DESC
LIMIT 5;

-- Step 3: Apply the fix
UPDATE trade_records
SET remaining_size = 0
WHERE status = 'filled'
  AND side = 'buy'
  AND remaining_size = size
  AND remaining_size IS NOT NULL;

-- Step 4: Verify CLANKER position after fix
SELECT 
    'CLANKER POSITION AFTER FIX' as info,
    symbol,
    SUM(qty_signed) as net_position,
    COUNT(*) as num_trades,
    SUM(CASE WHEN qty_signed > 0 THEN qty_signed ELSE 0 END) as total_buys,
    SUM(CASE WHEN qty_signed < 0 THEN -qty_signed ELSE 0 END) as total_sells
FROM report_trades
WHERE symbol = 'CLANKER-USD'
GROUP BY symbol;

-- Step 5: Check top 10 positions by notional value
SELECT 
    'TOP POSITIONS AFTER FIX' as info,
    symbol,
    position_qty,
    avg_entry_price,
    (position_qty * avg_entry_price) as notional_usd
FROM report_positions
ORDER BY ABS(position_qty * avg_entry_price) DESC
LIMIT 10;

-- Step 6: Summary statistics
SELECT 
    'SUMMARY' as info,
    COUNT(DISTINCT symbol) as num_positions,
    SUM(ABS(position_qty * avg_entry_price)) as total_notional_usd,
    SUM(CASE WHEN position_qty > 0 THEN (position_qty * avg_entry_price) ELSE 0 END) as long_notional,
    SUM(CASE WHEN position_qty < 0 THEN ABS(position_qty * avg_entry_price) ELSE 0 END) as short_notional
FROM report_positions;
