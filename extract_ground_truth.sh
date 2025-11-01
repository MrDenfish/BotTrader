#!/bin/bash
set -e

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
OUTPUT_DIR="/opt/bot/validation"
mkdir -p "$OUTPUT_DIR"

echo "Extracting ground truth at $(date)..."

docker compose -f docker-compose.aws.yml exec -T db psql -U bot_user -d bot_trader_db << 'SQL' > "$OUTPUT_DIR/ground_truth_${TIMESTAMP}.txt"

\echo '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━'
\echo 'GROUND TRUTH: Last 24 Hours'
\echo '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━'

\echo ''
\echo 'Time Window:'
SELECT NOW() as report_time,
       NOW() - INTERVAL '24 hours' as window_start;

\echo ''
\echo '━━━ POSITIONS (Net by Symbol) ━━━'
SELECT
    symbol,
    COUNT(*) as trades,
    ROUND(SUM(CASE
        WHEN LOWER(side::text) = 'buy' THEN size
        WHEN LOWER(side::text) = 'sell' THEN -size
        ELSE 0
    END)::numeric, 4) as net_position,
    ROUND(AVG(price)::numeric, 2) as avg_price
FROM trade_records
WHERE order_time >= NOW() - INTERVAL '24 hours'
  AND status = 'filled'
GROUP BY symbol
HAVING ABS(SUM(CASE WHEN LOWER(side::text) = 'buy' THEN size ELSE -size END)) > 0.0001
ORDER BY ABS(SUM(CASE WHEN LOWER(side::text) = 'buy' THEN size ELSE -size END) * AVG(price)) DESC;

\echo ''
\echo '━━━ PNL & WIN RATE ━━━'
SELECT
    ROUND(COALESCE(SUM(realized_profit), 0)::numeric, 2) as realized_pnl,
    COUNT(*) FILTER (WHERE realized_profit > 0) as wins,
    COUNT(*) FILTER (WHERE realized_profit < 0) as losses,
    COUNT(*) as total_trades,
    ROUND((COUNT(*) FILTER (WHERE realized_profit > 0)::numeric /
           NULLIF(COUNT(*), 0) * 100)::numeric, 2) as win_rate_pct
FROM trade_records
WHERE order_time >= NOW() - INTERVAL '24 hours'
  AND status = 'filled'
  AND realized_profit IS NOT NULL;

\echo ''
\echo '━━━ TRADE STATISTICS ━━━'
SELECT
    ROUND(AVG(realized_profit) FILTER (WHERE realized_profit > 0)::numeric, 2) as avg_win,
    ROUND(AVG(realized_profit) FILTER (WHERE realized_profit < 0)::numeric, 2) as avg_loss
FROM trade_records
WHERE order_time >= NOW() - INTERVAL '24 hours'
  AND status = 'filled';

SQL

echo "✅ Ground truth saved: $OUTPUT_DIR/ground_truth_${TIMESTAMP}.txt"
BASH

chmod +x scripts/extract_ground_truth.sh