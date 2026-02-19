-- Weekly Time-of-Day Performance Analysis
-- Run every Monday to identify profitable/unprofitable trading hours
-- Purpose: Determine if time-based trading restrictions would be beneficial

SELECT
    EXTRACT(HOUR FROM sell_time AT TIME ZONE 'America/Los_Angeles') as hour_pt,
    COUNT(*) as trades,
    ROUND(AVG(pnl_usd)::numeric, 4) as avg_pnl,
    ROUND(SUM(pnl_usd)::numeric, 4) as total_pnl,
    COUNT(CASE WHEN pnl_usd > 0 THEN 1 END) as wins,
    COUNT(CASE WHEN pnl_usd < 0 THEN 1 END) as losses,
    ROUND((COUNT(CASE WHEN pnl_usd > 0 THEN 1 END)::decimal / NULLIF(COUNT(*), 0) * 100)::numeric, 1) as win_rate_pct
FROM fifo_allocations
WHERE allocation_version = 2
  AND sell_time >= NOW() - INTERVAL '7 days'
GROUP BY hour_pt
HAVING COUNT(*) >= 2  -- At least 2 trades
ORDER BY avg_pnl DESC;
