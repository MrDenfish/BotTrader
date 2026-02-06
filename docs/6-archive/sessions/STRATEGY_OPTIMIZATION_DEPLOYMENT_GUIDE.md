# Strategy Optimization Preparation - Deployment Guide

**Created:** December 27, 2025
**Evaluation Date:** January 7, 2025 (11 days away)
**Purpose:** Set up automated data collection and weekly analysis for strategy optimization

---

## Overview

This guide walks through setting up infrastructure to collect 4 weeks of rich trading data that will enable informed optimization decisions on January 7, 2025.

---

## Prerequisites

- [ ] AWS server accessible via SSH (`bottrader-aws`)
- [ ] Database tables: `strategy_snapshots`, `trade_strategy_link`, `fifo_allocations`
- [ ] Files prepared locally (already created in this repo):
  - `queries/weekly_symbol_performance.sql`
  - `queries/weekly_signal_quality.sql`
  - `queries/weekly_timing_analysis.sql`
  - `scripts/weekly_strategy_review.sh`

---

## Step 1: Upload Query Files to AWS

```bash
# Create queries directory on AWS
ssh bottrader-aws "mkdir -p /opt/bot/queries"

# Upload the three SQL query files
scp queries/weekly_symbol_performance.sql bottrader-aws:/opt/bot/queries/
scp queries/weekly_signal_quality.sql bottrader-aws:/opt/bot/queries/
scp queries/weekly_timing_analysis.sql bottrader-aws:/opt/bot/queries/

# Verify upload
ssh bottrader-aws "ls -la /opt/bot/queries/"
```

**Expected output:** Three .sql files in `/opt/bot/queries/`

---

## Step 2: Upload and Configure Weekly Report Script

```bash
# Upload the weekly review script
scp scripts/weekly_strategy_review.sh bottrader-aws:/opt/bot/

# Make it executable
ssh bottrader-aws "chmod +x /opt/bot/weekly_strategy_review.sh"

# Test run manually (optional)
ssh bottrader-aws "/opt/bot/weekly_strategy_review.sh"
```

**Expected output:** Report generated in `/opt/bot/logs/weekly_review_YYYY-MM-DD.txt`

---

## Step 3: Create Market Conditions Table

```bash
ssh bottrader-aws "docker exec db psql -U bot_user -d bot_trader_db" <<'SQL'
CREATE TABLE IF NOT EXISTS market_conditions (
    id SERIAL PRIMARY KEY,
    date DATE NOT NULL UNIQUE,
    btc_change_pct DECIMAL(10, 4),
    volatility_regime VARCHAR(20),
    trend VARCHAR(20),
    avg_volume_ratio DECIMAL(10, 4),
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Add initial baseline entry
INSERT INTO market_conditions (date, btc_change_pct, volatility_regime, trend, notes)
VALUES (
    CURRENT_DATE,
    NULL,
    'medium',
    'sideways',
    'Initial baseline entry for strategy optimization preparation'
)
ON CONFLICT (date) DO NOTHING;
SQL
```

**Verify:**
```bash
ssh bottrader-aws "docker exec db psql -U bot_user -d bot_trader_db -c 'SELECT * FROM market_conditions;'"
```

---

## Step 4: Create Baseline Strategy Snapshot

**Option A: Using strategy_snapshot_manager.py**
```bash
# If the tool exists and works
ssh bottrader-aws "cd /opt/bot && python3 database/strategy_snapshot_manager.py create \
  --note 'Baseline strategy - Dec 27, 2025. RSI weight 1.5, min_indicators_required=2'"
```

**Option B: Manual SQL Insert**
```bash
ssh bottrader-aws "docker exec db psql -U bot_user -d bot_trader_db" <<'SQL'
-- Create baseline snapshot
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
    2,     -- Min indicators required
    ARRAY['A8-USD', 'PENGU-USD'],
    encode(sha256('baseline_dec27_2025'::bytea), 'hex'),
    'Baseline strategy - Dec 27, 2025. RSI weight 1.5, min_indicators_required=2',
    'manual'
)
ON CONFLICT DO NOTHING;

-- Mark this as the currently active snapshot
UPDATE strategy_snapshots
SET active_until = NOW()
WHERE active_until IS NULL
  AND snapshot_id != (SELECT snapshot_id FROM strategy_snapshots ORDER BY created_at DESC LIMIT 1);
SQL
```

**Verify:**
```bash
ssh bottrader-aws "docker exec db psql -U bot_user -d bot_trader_db -c 'SELECT snapshot_id, created_at, notes FROM strategy_snapshots ORDER BY created_at DESC LIMIT 3;'"
```

---

## Step 5: Install Cron Job for Weekly Reports

```bash
# View current crontab
ssh bottrader-aws "crontab -l"

# Add weekly report cron job (runs every Monday at 9am PT)
ssh bottrader-aws "{ crontab -l 2>/dev/null; echo '0 9 * * 1 /opt/bot/weekly_strategy_review.sh >> /opt/bot/logs/weekly_review_cron.log 2>&1'; } | crontab -"

# Verify it was added
ssh bottrader-aws "crontab -l | grep weekly"
```

**Expected output:** `0 9 * * 1 /opt/bot/weekly_strategy_review.sh >> /opt/bot/logs/weekly_review_cron.log 2>&1`

---

## Step 6: Verify Trade Strategy Linkage

Check that trades are being linked to strategy snapshots:

```bash
ssh bottrader-aws "docker exec db psql -U bot_user -d bot_trader_db -c \"
SELECT
    COUNT(DISTINCT tr.order_id) as total_trades,
    COUNT(DISTINCT tsl.order_id) as linked_trades,
    ROUND((COUNT(DISTINCT tsl.order_id)::decimal / NULLIF(COUNT(DISTINCT tr.order_id), 0) * 100)::numeric, 1) as link_rate_pct
FROM trade_records tr
LEFT JOIN trade_strategy_link tsl ON tsl.order_id = tr.order_id
WHERE tr.order_time >= NOW() - INTERVAL '7 days';
\""
```

**Expected:** Link rate ~100%

**If low:** Investigate why trades aren't being linked to strategies in the trading code.

---

## Step 7: Review Symbol Blacklist (Optional Quick Win)

Identify underperforming symbols:

```bash
ssh bottrader-aws "docker exec db psql -U bot_user -d bot_trader_db -c \"
SELECT
    symbol,
    COUNT(*) as trades,
    ROUND(SUM(pnl_usd)::numeric, 2) as total_loss,
    ROUND(AVG(pnl_usd)::numeric, 4) as avg_loss
FROM fifo_allocations
WHERE allocation_version = 2
  AND sell_time >= NOW() - INTERVAL '30 days'
GROUP BY symbol
HAVING COUNT(*) >= 5
   AND SUM(pnl_usd) < -0.50
   AND AVG(pnl_usd) < -0.10
ORDER BY total_loss ASC;
\""
```

**Action:** If any symbols appear, consider adding them to the blacklist:
- Update `.env`: `EXCLUDED_SYMBOLS`
- Update `sighook/trading_strategy.py`: `self.excluded_symbols` list

---

## Step 8: Test Weekly Report Manually

```bash
# Run the weekly report script manually to verify it works
ssh bottrader-aws "/opt/bot/weekly_strategy_review.sh"

# Check the output
ssh bottrader-aws "cat /opt/bot/logs/weekly_review_$(date +%Y-%m-%d).txt"
```

**Expected:** Complete report with:
- Overall performance metrics
- Symbol performance breakdown
- Signal quality analysis
- Time-of-day analysis

---

## Success Criteria

After completing all steps, you should have:

- ✅ Three SQL query files uploaded to `/opt/bot/queries/`
- ✅ Weekly report script executable at `/opt/bot/weekly_strategy_review.sh`
- ✅ Market conditions table created with baseline entry
- ✅ Baseline strategy snapshot created and active
- ✅ Cron job scheduled for Monday 9am PT weekly reports
- ✅ Trade strategy linkage verified (~100%)
- ✅ Symbol blacklist reviewed and updated if needed
- ✅ Manual test run of weekly report successful

---

## Timeline

**Now - Jan 6, 2025:** Data collection phase
- Weekly reports generated every Monday
- Market conditions updated manually or automatically
- No strategy changes (keep baseline stable)

**January 7, 2025:** Evaluation
- Review 4 weeks of data
- Decide on optimization approach (manual tuning vs ML)
- Identify parameter adjustments based on data

---

## Monitoring

### Weekly Actions (Every Monday)
1. Review the weekly report generated automatically
2. Update `market_conditions` table with current week's data:
   ```sql
   INSERT INTO market_conditions (date, btc_change_pct, volatility_regime, trend, notes)
   VALUES (CURRENT_DATE, <btc_change>, '<volatility>', '<trend>', '<notes>');
   ```
3. Note any patterns or anomalies

### What to Look For
- **Symbol Performance:** Consistently losing symbols → add to blacklist
- **Signal Quality:** Weak signals losing money → increase thresholds
- **Time-of-Day:** Certain hours consistently negative → add time restrictions
- **Market Conditions:** Bot performance correlation with market regime

---

## Troubleshooting

### Weekly report not generating
- Check cron is running: `ssh bottrader-aws "service cron status"`
- Check cron logs: `ssh bottrader-aws "cat /opt/bot/logs/weekly_review_cron.log"`
- Verify script permissions: `ssh bottrader-aws "ls -la /opt/bot/weekly_strategy_review.sh"`

### Query files not found
- Verify files exist: `ssh bottrader-aws "ls -la /opt/bot/queries/"`
- Re-upload if missing (see Step 1)

### Trade linkage low (<100%)
- Check `trade_strategy_link` table is being populated
- Review trading code to ensure links are created on every trade
- May need to add instrumentation to trading logic

---

## Reference Documents

- **Planning Doc:** `docs/planning/NEXT_SESSION_PREP_TASKS.md`
- **Optimization Guide:** `docs/planning/prepare_for_optimization.md`
- **Snapshot Manager:** `database/strategy_snapshot_manager.py`
- **Migration:** `database/migrations/002_create_strategy_snapshots_table.sql`

---

**Created:** December 27, 2025
**Last Updated:** December 27, 2025
**Status:** Ready for deployment (AWS currently offline from desktop)
