-- Weekly Signal Quality Analysis
-- Run every Monday to assess parameter effectiveness
-- Purpose: Determine if signal thresholds should be adjusted

WITH signal_analysis AS (
    SELECT
        tsl.trigger_type,
        CASE
            WHEN tsl.buy_score >= 4.0 THEN 'Strong (4.0+)'
            WHEN tsl.buy_score >= 3.0 THEN 'Medium (3.0-4.0)'
            WHEN tsl.buy_score >= 2.5 THEN 'Weak (2.5-3.0)'
            ELSE 'Below Target (<2.5)'
        END as signal_strength,
        tsl.indicators_fired,
        fa.pnl_usd
    FROM trade_strategy_link tsl
    JOIN fifo_allocations fa ON fa.sell_order_id = tsl.order_id
    WHERE fa.allocation_version = 2
      AND fa.sell_time >= NOW() - INTERVAL '7 days'
)
SELECT
    signal_strength,
    COUNT(*) as trades,
    AVG(indicators_fired) as avg_indicators,
    ROUND(AVG(pnl_usd)::numeric, 4) as avg_pnl,
    ROUND((COUNT(CASE WHEN pnl_usd > 0 THEN 1 END)::decimal / COUNT(*) * 100)::numeric, 1) as win_rate_pct,
    CASE
        WHEN AVG(pnl_usd) < 0 THEN '❌ Losing strategy'
        WHEN AVG(pnl_usd) > 0.10 THEN '✅ Profitable'
        ELSE '⚠️ Marginal'
    END as verdict
FROM signal_analysis
GROUP BY signal_strength
ORDER BY
    CASE signal_strength
        WHEN 'Strong (4.0+)' THEN 1
        WHEN 'Medium (3.0-4.0)' THEN 2
        WHEN 'Weak (2.5-3.0)' THEN 3
        ELSE 4
    END;
