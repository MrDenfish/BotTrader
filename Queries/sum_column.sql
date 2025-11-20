SELECT COALESCE(SUM(pnl_usd), 0) AS realized_pnl_usd
FROM trade_records;