-- ================================================
-- CLANKER Position Bug - Investigation Script
-- ================================================
-- Run this FIRST to understand the scope of the problem
-- ================================================

-- 1. Show the current CLANKER position problem
SELECT 
    '=== CURRENT CLANKER PROBLEM ===' as section,
    '' as col1, '' as col2, '' as col3, '' as col4;

SELECT 
    'Raw Trade Records' as data_type,
    side,
    COUNT(*) as num_trades,
    SUM(size) as total_size,
    SUM(remaining_size) as sum_remaining_size,
    AVG(remaining_size) as avg_remaining_size
FROM trade_records
WHERE symbol = 'CLANKER-USD'
  AND status = 'filled'
GROUP BY side
ORDER BY side;

-- 2. Show problematic buy records
SELECT 
    '=== PROBLEMATIC BUY RECORDS ===' as section,
    '' as col1, '' as col2, '' as col3, '' as col4;

SELECT 
    'Sample Bad Data' as info,
    symbol,
    side,
    status,
    size,
    remaining_size,
    price,
    order_time
FROM trade_records
WHERE side = 'buy'
  AND status = 'filled'
  AND remaining_size = size
  AND remaining_size IS NOT NULL
ORDER BY order_time DESC
LIMIT 10;

-- 3. Count affected records across all symbols
SELECT 
    '=== SCOPE OF PROBLEM ===' as section,
    '' as col1, '' as col2, '' as col3, '' as col4;

SELECT 
    'Affected Records Summary' as metric,
    COUNT(*) as bad_buy_records,
    COUNT(DISTINCT symbol) as affected_symbols,
    SUM(size) as total_affected_qty,
    SUM(size * price) as total_affected_notional
FROM trade_records
WHERE status = 'filled'
  AND side = 'buy'
  AND remaining_size = size
  AND remaining_size IS NOT NULL;

-- 4. Show current position calculations
SELECT 
    '=== CURRENT POSITION CALCULATIONS ===' as section,
    '' as col1, '' as col2, '' as col3, '' as col4;

SELECT 
    'From report_trades' as source,
    symbol,
    COUNT(*) as num_trades,
    SUM(qty_signed) as net_position,
    SUM(CASE WHEN qty_signed > 0 THEN qty_signed ELSE 0 END) as buys,
    SUM(CASE WHEN qty_signed < 0 THEN -qty_signed ELSE 0 END) as sells
FROM report_trades
WHERE symbol = 'CLANKER-USD'
GROUP BY symbol;

-- 5. Show what the position SHOULD be
SELECT 
    '=== WHAT POSITION SHOULD BE ===' as section,
    '' as col1, '' as col2, '' as col3, '' as col4;

SELECT 
    'Corrected Calculation' as source,
    symbol,
    SUM(CASE WHEN side = 'buy' THEN size ELSE 0 END) as total_buys,
    SUM(CASE WHEN side = 'sell' THEN size ELSE 0 END) as total_sells,
    SUM(CASE WHEN side = 'buy' THEN size ELSE -size END) as correct_net_position
FROM trade_records
WHERE symbol = 'CLANKER-USD'
  AND status = 'filled'
GROUP BY symbol;

-- 6. Top positions by notional (current incorrect state)
SELECT 
    '=== CURRENT TOP POSITIONS (INCORRECT) ===' as section,
    '' as col1, '' as col2, '' as col3, '' as col4;

SELECT 
    symbol,
    position_qty,
    avg_entry_price,
    (position_qty * avg_entry_price) as notional_usd,
    CASE 
        WHEN position_qty > 0 THEN 'LONG'
        WHEN position_qty < 0 THEN 'SHORT'
        ELSE 'FLAT'
    END as position_type
FROM report_positions
ORDER BY ABS(position_qty * avg_entry_price) DESC
LIMIT 15;

-- 7. Summary totals
SELECT 
    '=== SUMMARY ===' as section,
    '' as col1, '' as col2, '' as col3, '' as col4;

SELECT 
    'Current (Incorrect)' as calculation_type,
    COUNT(DISTINCT symbol) as num_positions,
    SUM(ABS(position_qty * avg_entry_price)) as total_notional,
    SUM(CASE WHEN position_qty > 0 THEN position_qty * avg_entry_price ELSE 0 END) as long_notional,
    SUM(CASE WHEN position_qty < 0 THEN ABS(position_qty * avg_entry_price) ELSE 0 END) as short_notional
FROM report_positions;
