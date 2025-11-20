SELECT
  symbol,
  ROUND(SUM(pnl_usd)::numeric, 2) AS realized_pnl_usd_24h
FROM trade_records
WHERE pnl_usd IS NOT NULL
  AND order_time >= (NOW() AT TIME ZONE 'UTC') - INTERVAL '24 hours'
GROUP BY symbol
ORDER BY realized_pnl_usd_24h DESC NULLS LAST;
