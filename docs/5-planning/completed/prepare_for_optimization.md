# Preparation for Optimization - 4 Week Plan
**Start Date:** December 9, 2025
**Evaluation Date:** January 7, 2025
**Goal:** Collect rich data to enable informed optimization decisions

---

## Phase 1: Enhanced Data Collection (Implement This Week)

### 1.1 Snapshot Management
**Current State:** You have `strategy_snapshots` table but need to use it consistently.

**Action Required:**
```sql
-- Create initial baseline snapshot (if not already done)
INSERT INTO strategy_snapshots (
    score_buy_target,
    score_sell_target,
    indicator_weights,
    rsi_buy_threshold,
    rsi_sell_threshold,
    tp_threshold,
    sl_threshold,
    cooldown_bars,
    flip_hysteresis_pct,
    min_indicators_required,
    excluded_symbols,
    config_hash,
    notes,
    created_by
) VALUES (
    2.5,  -- Current buy target
    2.5,  -- Current sell target
    '{"Buy RSI": 1.5, "Sell RSI": 1.5, "Buy MACD": 1.8, "Sell MACD": 1.8, "Buy Touch": 1.5, "Sell Touch": 1.5}'::jsonb,
    25.0,  -- RSI buy threshold
    75.0,  -- RSI sell threshold
    0.035, -- TP 3.5%
    0.045, -- SL 4.5%
    7,     -- Cooldown bars
    0.10,  -- Flip hysteresis 10%
    2,     -- Min indicators required (NEW!)
    ARRAY['A8-USD', 'PENGU-USD'],  -- Excluded symbols
    encode(sha256('baseline_dec9_2025'::bytea), 'hex'),
    'Baseline strategy - Dec 9, 2025. RSI weight reduced from 2.5 to 1.5, added min_indicators_required=2',
    'manual'
);

-- Mark this as the currently active snapshot
UPDATE strategy_snapshots
SET active_until = NOW()
WHERE active_until IS NULL
  AND snapshot_id != (SELECT snapshot_id FROM strategy_snapshots ORDER BY created_at DESC LIMIT 1);
```

**Why:** This creates a clean baseline to compare future experiments against.

---

### 1.2 Enhance Trade Metadata
**Problem:** `trade_strategy_link` might not be capturing all context.

**Check Current Data:**
```sql
-- See what's being captured
SELECT
    COUNT(*) as total_links,
    COUNT(buy_score) as has_buy_score,
    COUNT(sell_score) as has_sell_score,
    COUNT(trigger_type) as has_trigger,
    COUNT(indicator_breakdown) as has_breakdown
FROM trade_strategy_link;
```

**If any counts are low, we need to ensure the link is created for every trade.**

---

### 1.3 Market Condition Tagging
**New Feature:** Tag each trading day with market conditions for later analysis.

```sql
-- Create market_conditions table
CREATE TABLE IF NOT EXISTS market_conditions (
    id SERIAL PRIMARY KEY,
    date DATE NOT NULL UNIQUE,
    btc_change_pct DECIMAL(10, 4),  -- BTC daily change (market proxy)
    volatility_regime VARCHAR(20),  -- 'low', 'medium', 'high'
    trend VARCHAR(20),              -- 'bull', 'bear', 'sideways'
    avg_volume_ratio DECIMAL(10, 4), -- Today's volume / 7-day avg
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Manual entry (or automate this later)
-- Example for today
INSERT INTO market_conditions (date, btc_change_pct, volatility_regime, trend, notes)
VALUES (
    '2025-12-09',
    -2.5,  -- BTC down 2.5% today
    'medium',
    'sideways',
    'Choppy market, no clear direction'
);
```

**Why:** You'll be able to see "Bot performs well in sideways markets but poorly in high volatility" - this is GOLD for optimization.

---

## Phase 2: Weekly Analysis Queries (Run Every Monday)

### 2.1 Symbol Performance Tracker
```sql
-- Save this as: /opt/bot/queries/weekly_symbol_performance.sql

-- Top 5 and Bottom 5 symbols (last 7 days)
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
        WHEN total_pnl > 1.00 AND win_rate_pct > 40 THEN '⭐ Star Performer'
        ELSE '➖ Neutral'
    END as recommendation
FROM last_week
ORDER BY total_pnl ASC;  -- Worst performers first
```

**Action:** Run this every Monday, save results to a spreadsheet or log file.

---

### 2.2 Parameter Effectiveness Check
```sql
-- Save this as: /opt/bot/queries/weekly_signal_quality.sql

-- How well are different signal strengths performing?
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
```

**Insight:** If "Weak (2.5-3.0)" signals are losing money, you should increase `SCORE_BUY_TARGET` to 3.0.

---

### 2.3 Time-of-Day Analysis
```sql
-- Save this as: /opt/bot/queries/weekly_timing_analysis.sql

-- Are certain hours more profitable?
SELECT
    EXTRACT(HOUR FROM sell_time AT TIME ZONE 'America/Los_Angeles') as hour_pt,
    COUNT(*) as trades,
    ROUND(AVG(pnl_usd)::numeric, 4) as avg_pnl,
    ROUND(SUM(pnl_usd)::numeric, 4) as total_pnl,
    COUNT(CASE WHEN pnl_usd > 0 THEN 1 END) as wins,
    COUNT(CASE WHEN pnl_usd < 0 THEN 1 END) as losses
FROM fifo_allocations
WHERE allocation_version = 2
  AND sell_time >= NOW() - INTERVAL '7 days'
GROUP BY hour_pt
HAVING COUNT(*) >= 2  -- At least 2 trades
ORDER BY avg_pnl DESC;
```

**Insight:** If 2am-6am PT is consistently negative, add time-based trading restrictions.

---

## Phase 3: Automated Weekly Report (Set Up This Week)

Create a cron job that runs every Monday at 9am PT:

```bash
# Create: /opt/bot/weekly_strategy_review.sh
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
    'Total PnL: $' || ROUND(SUM(pnl_usd)::numeric, 2),
    'Avg PnL: $' || ROUND(AVG(pnl_usd)::numeric, 4),
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

# 4. Readiness Check (after 4 weeks)
WEEKS_ELAPSED=$(( ($(date +%s) - $(date -d "2025-12-09" +%s)) / 604800 ))
if [ $WEEKS_ELAPSED -ge 4 ]; then
    echo "🎯 === OPTIMIZATION READINESS CHECK ===" >> "$OUTPUT_FILE"
    echo "" >> "$OUTPUT_FILE"

    # Check if we have enough data
    TOTAL_TRADES=$(docker exec db psql -U bot_user -d bot_trader_db -t -c "
        SELECT COUNT(*) FROM fifo_allocations
        WHERE allocation_version = 2
          AND sell_time >= '2025-12-09';
    " | tr -d ' ')

    echo "Total trades since Dec 9: $TOTAL_TRADES" >> "$OUTPUT_FILE"

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

# Email the report (optional)
# mail -s "Weekly Bot Strategy Review - $REPORT_DATE" your@email.com < "$OUTPUT_FILE"

# Print to console
cat "$OUTPUT_FILE"
```

**Install the cron job:**
```bash
# Make executable
chmod +x /opt/bot/weekly_strategy_review.sh

# Add to crontab (runs every Monday at 9am PT)
echo "0 9 * * 1 /opt/bot/weekly_strategy_review.sh >> /opt/bot/logs/weekly_review_cron.log 2>&1" | ssh bottrader-aws "crontab -"
```

---

## Phase 4: Quick Wins to Implement Now

### 4.1 Symbol Blacklist Review
Run this NOW and update your blacklist:

```sql
-- Symbols that have lost money consistently
SELECT
    symbol,
    COUNT(*) as trades,
    SUM(pnl_usd) as total_loss,
    AVG(pnl_usd) as avg_loss
FROM fifo_allocations
WHERE allocation_version = 2
  AND sell_time >= NOW() - INTERVAL '30 days'
GROUP BY symbol
HAVING COUNT(*) >= 5
   AND SUM(pnl_usd) < -0.50
   AND AVG(pnl_usd) < -0.10
ORDER BY total_loss ASC;
```

**Action:** Add these symbols to `excluded_symbols` in `.env` and `trading_strategy.py`.

### 4.2 Verify Strategy Snapshot Linkage
Make sure every trade is being linked to the current strategy:

```sql
-- Check if trade_strategy_link is being populated
SELECT
    COUNT(DISTINCT tr.order_id) as total_trades,
    COUNT(DISTINCT tsl.order_id) as linked_trades,
    ROUND((COUNT(DISTINCT tsl.order_id)::decimal / NULLIF(COUNT(DISTINCT tr.order_id), 0) * 100)::numeric, 1) as link_rate_pct
FROM trade_records tr
LEFT JOIN trade_strategy_link tsl ON tsl.order_id = tr.order_id
WHERE tr.order_time >= NOW() - INTERVAL '7 days';
```

**Expected:** 100% link rate. If lower, there's a bug in how trades are being recorded.

---

## Summary: Your Action Checklist

### This Week (Dec 9-15):
- [ ] Create baseline strategy snapshot (SQL above)
- [ ] Create `market_conditions` table
- [ ] Save weekly analysis queries to `/opt/bot/queries/`
- [ ] Create and test `weekly_strategy_review.sh`
- [ ] Set up weekly cron job
- [ ] Run symbol blacklist query and update exclusions
- [ ] Verify trade_strategy_link is working (100% linkage)

### Weekly (Every Monday):
- [ ] Review automated weekly report
- [ ] Log market conditions for the week
- [ ] Note any significant events (strategy changes, bot downtime, etc.)

### January 7, 2025:
- [ ] Review all 4 weekly reports
- [ ] Run final optimization readiness check
- [ ] Decide: Continue manual tuning OR build optimizer
- [ ] Start new Claude Code session with collected insights

---

## Benefits of This Approach

1. **Rich Context:** You'll know WHEN and WHY performance changed
2. **Pattern Discovery:** "Bot does well when BTC is sideways, poorly when volatile"
3. **Informed Decisions:** "We tried X for 2 weeks, it didn't work, revert"
4. **Optimization Ready:** When you build the optimizer, you'll have labeled data showing what works

**This is the difference between:**
- ❌ "Let's try random parameter changes"
- ✅ "Data shows weak signals (<3.0) lose money, so raise threshold to 3.5"
