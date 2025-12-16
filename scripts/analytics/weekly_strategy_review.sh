#!/bin/bash

REPORT_DATE=$(date +%Y-%m-%d)
OUTPUT_FILE="/opt/bot/logs/weekly_review_${REPORT_DATE}.txt"

echo "========================================" > "$OUTPUT_FILE"
echo "Weekly Strategy Review - $REPORT_DATE" >> "$OUTPUT_FILE"
echo "========================================" >> "$OUTPUT_FILE"
echo "" >> "$OUTPUT_FILE"

# 1. Overall Performance
echo "=== OVERALL PERFORMANCE (Last 7 Days) ===" >> "$OUTPUT_FILE"
docker exec db psql -U bot_user -d bot_trader_db -t -c "
SELECT
    'Total Trades: ' || COUNT(*),
    'Total PnL: \$' || ROUND(SUM(pnl_usd)::numeric, 2),
    'Avg PnL: \$' || ROUND(AVG(pnl_usd)::numeric, 4),
    'Win Rate: ' || ROUND((COUNT(CASE WHEN pnl_usd > 0 THEN 1 END)::decimal / COUNT(*) * 100)::numeric, 1) || '%'
FROM fifo_allocations
WHERE allocation_version = 2
  AND sell_time >= NOW() - INTERVAL '7 days';
" >> "$OUTPUT_FILE"

echo "" >> "$OUTPUT_FILE"

# 2. Symbol Performance
echo "=== SYMBOL PERFORMANCE ===" >> "$OUTPUT_FILE"
docker exec db psql -U bot_user -d bot_trader_db -t -c "
$(cat /opt/bot/queries/weekly_symbol_performance.sql)
" >> "$OUTPUT_FILE"

echo "" >> "$OUTPUT_FILE"

# 3. Signal Quality
echo "=== SIGNAL QUALITY ===" >> "$OUTPUT_FILE"
docker exec db psql -U bot_user -d bot_trader_db -t -c "
$(cat /opt/bot/queries/weekly_signal_quality.sql)
" >> "$OUTPUT_FILE"

echo "" >> "$OUTPUT_FILE"

# 4. Time of Day Analysis
echo "=== TIME OF DAY ANALYSIS ===" >> "$OUTPUT_FILE"
docker exec db psql -U bot_user -d bot_trader_db -t -c "
$(cat /opt/bot/queries/weekly_timing_analysis.sql)
" >> "$OUTPUT_FILE"

echo "" >> "$OUTPUT_FILE"

# 5. Readiness Check (after 4 weeks)
WEEKS_ELAPSED=$(( ($(date +%s) - $(date -d "2025-12-10" +%s)) / 604800 ))
if [ $WEEKS_ELAPSED -ge 4 ]; then
    echo "🎯 === OPTIMIZATION READINESS CHECK ===" >> "$OUTPUT_FILE"
    echo "" >> "$OUTPUT_FILE"

    # Check if we have enough data
    TOTAL_TRADES=$(docker exec db psql -U bot_user -d bot_trader_db -t -c "
        SELECT COUNT(*) FROM fifo_allocations
        WHERE allocation_version = 2
          AND sell_time >= '2025-12-10';
    " | tr -d ' ')

    echo "Total trades since Dec 10: $TOTAL_TRADES" >> "$OUTPUT_FILE"

    if [ "$TOTAL_TRADES" -gt 500 ]; then
        echo "✅ Sufficient data for optimization (500+ trades)" >> "$OUTPUT_FILE"
        echo "" >> "$OUTPUT_FILE"
        echo "RECOMMENDATION: Review this report and consider building optimizer" >> "$OUTPUT_FILE"
    else
        echo "⏳ Need more data. Target: 500 trades, Current: $TOTAL_TRADES" >> "$OUTPUT_FILE"
    fi
fi

echo "" >> "$OUTPUT_FILE"
echo "========================================" >> "$OUTPUT_FILE"
echo "Report saved to: $OUTPUT_FILE" >> "$OUTPUT_FILE"
echo "========================================" >> "$OUTPUT_FILE"

# Print to console
cat "$OUTPUT_FILE"
