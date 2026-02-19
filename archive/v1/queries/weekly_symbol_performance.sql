-- Weekly Symbol Performance Tracker
-- Run every Monday to identify top and bottom performing symbols
-- Purpose: Identify symbols to blacklist or prioritize

WITH last_week AS (
    SELECT
        symbol,
        COUNT(*) as trades,
        SUM(pnl_usd) as total_pnl,
        AVG(pnl_usd) as avg_pnl,
        STDDEV(pnl_usd) as pnl_stddev,
        COUNT(CASE WHEN pnl_usd > 0 THEN 1 END) as wins,
        COUNT(CASE WHEN pnl_usd < 0 THEN 1 END) as losses
    FROM fifo_allocations
    WHERE allocation_version = 2
      AND sell_time >= NOW() - INTERVAL '7 days'
    GROUP BY symbol
    HAVING COUNT(*) >= 3  -- At least 3 trades
)
SELECT
    symbol,
    trades,
    ROUND(total_pnl::numeric, 4) as total_pnl,
    ROUND(avg_pnl::numeric, 4) as avg_pnl,
    ROUND(pnl_stddev::numeric, 4) as volatility,
    ROUND((wins::decimal / NULLIF(trades, 0) * 100)::numeric, 1) as win_rate_pct,
    CASE
        WHEN total_pnl < -0.50 AND avg_pnl < -0.10 THEN '🚨 Consider Blacklist'
        WHEN total_pnl > 1.00 AND (wins::decimal / NULLIF(trades, 0) * 100) > 40 THEN '⭐ Star Performer'
        ELSE '➖ Neutral'
    END as recommendation
FROM last_week
ORDER BY total_pnl ASC;  -- Worst performers first
