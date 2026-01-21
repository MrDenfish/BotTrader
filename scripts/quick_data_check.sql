-- Quick Data Check for Backtesting
-- Run this to see what data is available

-- OHLCV Data Summary
SELECT
    'OHLCV Data' as dataset,
    MIN(time) as earliest,
    MAX(time) as latest,
    COUNT(*) as total_rows,
    COUNT(DISTINCT symbol) as symbols
FROM ohlcv_data;

-- Trade Records Summary
SELECT
    'Trade Records' as dataset,
    MIN(filled_at) as earliest,
    MAX(filled_at) as latest,
    COUNT(*) as total_trades,
    COUNT(CASE WHEN side = 'buy' THEN 1 END) as buys,
    COUNT(CASE WHEN side = 'sell' THEN 1 END) as sells
FROM trade_records
WHERE status = 'filled';

-- Top 10 Symbols by Data Volume
SELECT
    symbol,
    MIN(time) as earliest,
    MAX(time) as latest,
    COUNT(*) as rows
FROM ohlcv_data
GROUP BY symbol
ORDER BY rows DESC
LIMIT 10;
