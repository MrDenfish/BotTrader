SELECT
  ROUND(COALESCE(SUM(pnl_usd)::numeric, 0), 2) AS realized_pnl_alltime,
  ROUND(COALESCE(SUM(
      CASE WHEN order_time >= (NOW() AT TIME ZONE 'UTC') - INTERVAL '24 hours'
           THEN pnl_usd END
  )::numeric, 0), 2) AS realized_pnl_24h
FROM trade_records
WHERE symbol = 'ELA-USD'
  AND pnl_usd IS NOT NULL ;