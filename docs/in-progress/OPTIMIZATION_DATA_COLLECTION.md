# Strategy Optimization Data Collection

**Created:** December 27, 2025
**Last Updated:** January 11, 2026
**Deployed:** January 11, 2026
**Evaluation Date:** **January 27, 2026**
**Status:** ✅ **DEPLOYED AND COLLECTING DATA**

---

## Executive Summary

This document consolidates all planning, deployment steps, and timeline analysis for the strategy optimization data collection effort. The goal is to collect 4 weeks of rich trading data to enable informed optimization decisions on **January 27, 2026**.

**Current Status:**
- ✅ SQL query files deployed to AWS
- ✅ Weekly analysis scripts deployed and tested
- ✅ Cron job installed (Mondays 9am PT)
- ✅ **DEPLOYED TO AWS - January 11, 2026**
- ✅ Baseline strategy snapshot active (Jan 8, 2026)
- ✅ Trade linkage verified (75% rate, 3/4 recent trades)

---

## Timeline

### Confirmed Timeline (Updated Jan 10, 2026)

| Milestone | Date | Status |
|-----------|------|--------|
| Original evaluation target | Jan 7, 2026 | ❌ Pushed (insufficient time) |
| **New evaluation target** | **Jan 27, 2026** | ✅ **CONFIRMED** |
| **Deployment completed** | **Jan 11, 2026** | ✅ **DEPLOYED** |
| Data collection duration | 2+ weeks (min) | ✅ In progress |
| Schema cleanup milestone | Jan 17, 2026 | Separate task |

### Recommended Deployment Schedule

**Option 1: Start ASAP (Recommended)**
- Deploy: Within 48 hours
- Data collection: 4 weeks from deployment date
- Evaluation: Jan 27, 2026
- Weekly reports: Every Monday 9am PT

**Benefits:**
- Maximum data collection time
- 4 complete weekly reports for trend analysis
- Evaluation happens AFTER schema cleanup (Jan 17)
- Aligns with professional ML/optimization best practices

---

## Infrastructure Status

### Files Prepared (Locally)

| File | Location | Purpose | Status |
|------|----------|---------|--------|
| weekly_symbol_performance.sql | queries/ | Symbol-level performance metrics | ✅ Ready |
| weekly_signal_quality.sql | queries/ | Signal quality analysis | ✅ Ready |
| weekly_timing_analysis.sql | queries/ | Entry/exit timing patterns | ✅ Ready |
| weekly_strategy_review.sh | scripts/ | Automated report generator | ✅ Ready |

### AWS Deployment Checklist

- [x] Create `/opt/bot/queries/` directory (already existed from Jan 9)
- [x] Upload 3 SQL query files (already present from Jan 9)
- [x] Upload weekly_strategy_review.sh script (deployed Jan 11)
- [x] Make script executable (chmod +x Jan 11)
- [x] Create `market_conditions` table (table exists, baseline entry added Jan 11)
- [x] Create baseline strategy snapshot (snapshot from Jan 8 active)
- [x] Install weekly cron job (already installed, verified Jan 11)
- [x] Verify trade strategy linkage (75% rate, 3/4 recent trades linked)
- [x] Test manual report generation (successful Jan 11, report saved)

**Deployment Status:** ✅ **ALL STEPS COMPLETE - January 11, 2026**

---

## Deployment Steps

### Step 1: Upload Query Files to AWS

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

### Step 2: Upload and Configure Weekly Report Script

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

### Step 3: Create Market Conditions Table

```bash
ssh bottrader-aws "docker exec db psql -U bot_user -d bot_trader_db" <<'SQL'
CREATE TABLE IF NOT EXISTS market_conditions (
    id SERIAL PRIMARY KEY,
    date DATE NOT NULL UNIQUE,
    btc_change_pct DECIMAL(10, 4),
    volatility_regime VARCHAR(20),  -- 'low', 'medium', 'high'
    trend VARCHAR(20),              -- 'bull', 'bear', 'sideways'
    avg_volume_ratio DECIMAL(10, 4),
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Add initial baseline entry
INSERT INTO market_conditions (date, volatility_regime, trend, notes)
VALUES (
    CURRENT_DATE,
    'medium',
    'sideways',
    'Initial baseline entry for strategy optimization data collection'
)
ON CONFLICT (date) DO NOTHING;
SQL
```

**Verify:**
```bash
ssh bottrader-aws "docker exec db psql -U bot_user -d bot_trader_db -c 'SELECT * FROM market_conditions;'"
```

---

### Step 4: Create Baseline Strategy Snapshot

**Option A: Using strategy_snapshot_manager.py (if available)**

```bash
ssh bottrader-aws "cd /opt/bot && python3 database/strategy_snapshot_manager.py create \
  --note 'Baseline strategy - Jan 2026. RSI weight 1.5, min_indicators_required=2'"
```

**Option B: Manual SQL Insert**

```bash
ssh bottrader-aws "docker exec db psql -U bot_user -d bot_trader_db" <<'SQL'
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
    ARRAY['A8-USD', 'PENGU-USD'],  -- Excluded symbols
    encode(sha256(('baseline_jan2026_' || CURRENT_DATE::text)::bytea), 'hex'),
    'Baseline strategy - Jan 2026. RSI weight 1.5, min_indicators_required=2',
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

### Step 5: Install Cron Job for Weekly Reports

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

### Step 6: Verify Trade Strategy Linkage

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

**If low:** Investigate why trades aren't being linked in SharedDataManager/trade_recorder.py

---

### Step 7: Review Symbol Blacklist (Optional Quick Win)

Identify underperforming symbols to blacklist:

```bash
ssh bottrader-aws "docker exec db psql -U bot_user -d bot_trader_db -c \"
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
\""
```

**Action:** Add any consistently losing symbols to:
- `.env`: `EXCLUDED_SYMBOLS` list
- `sighook/trading_strategy.py`: `self.excluded_symbols`

---

## Weekly Analysis Queries

### Query 1: Symbol Performance (weekly_symbol_performance.sql)

Analyzes per-symbol win rate, P&L, and trade frequency over the past week.

**Purpose:** Identify which symbols are profitable vs unprofitable

**Key Metrics:**
- Trade count per symbol
- Win rate percentage
- Average win/loss
- Total P&L
- Symbol-specific patterns

---

### Query 2: Signal Quality (weekly_signal_quality.sql)

Evaluates trigger effectiveness and signal accuracy.

**Purpose:** Determine which triggers/indicators perform best

**Key Metrics:**
- Trigger type breakdown
- Buy vs sell signal performance
- Indicator correlation with profitability
- False signal rate

---

### Query 3: Timing Analysis (weekly_timing_analysis.sql)

Examines entry/exit timing patterns and hold duration.

**Purpose:** Optimize entry timing and exit strategies

**Key Metrics:**
- Average hold duration for wins vs losses
- Time-of-day performance patterns
- Exit reason effectiveness
- TP/SL hit rates

---

## Data Collection Metrics

### Success Criteria

After 4 weeks of data collection, we should have:

- ✅ **100+ trades** across multiple symbols
- ✅ **4 weekly reports** showing trend progression
- ✅ **Market conditions** tagged for each trading day
- ✅ **Trade-to-strategy links** for >95% of trades
- ✅ **Symbol performance** data across different market regimes
- ✅ **Trigger effectiveness** breakdown by specific indicators

### Monitoring During Collection

**Weekly Checkpoints (Every Monday):**
1. Review automated weekly report
2. Check trade strategy linkage rate
3. Update market conditions table
4. Note any anomalies or system issues
5. Identify emerging patterns

**Ad-hoc Queries:**
```bash
# Check data collection progress
ssh bottrader-aws "docker exec db psql -U bot_user -d bot_trader_db -c \"
SELECT
    DATE(order_time) as trade_date,
    COUNT(*) as trades,
    COUNT(DISTINCT symbol) as unique_symbols,
    SUM(CASE WHEN side = 'buy' THEN 1 ELSE 0 END) as buys,
    SUM(CASE WHEN side = 'sell' THEN 1 ELSE 0 END) as sells
FROM trade_records
WHERE order_time >= NOW() - INTERVAL '7 days'
GROUP BY DATE(order_time)
ORDER BY trade_date DESC;
\""
```

---

## Evaluation Criteria (Jan 27, 2026)

### Questions to Answer

1. **Symbol Selection:**
   - Which symbols consistently outperform?
   - Which should be blacklisted?
   - Do we need to expand/reduce the symbol universe?

2. **Indicator Weights:**
   - Is RSI weight (1.5) optimal or should it be adjusted?
   - Are MACD signals (1.8) performing well?
   - Should we add/remove any indicators?

3. **Entry/Exit Thresholds:**
   - Is score_buy_target (2.5) too conservative/aggressive?
   - Are TP (3.5%) and SL (4.5%) levels optimal?
   - Do different symbols need different thresholds?

4. **Market Regime Sensitivity:**
   - Does performance vary by market conditions (bull/bear/sideways)?
   - Should strategy parameters adapt to volatility regime?
   - Are there patterns in timing (time of day, day of week)?

5. **Optimization Approach:**
   - Is manual tuning sufficient or should we implement ML optimization?
   - What's the cost/benefit of parameter grid search?
   - Should we A/B test strategy variants?

---

## Post-Evaluation Options

### Option A: Manual Parameter Tuning
- Adjust weights based on observed patterns
- Implement quick wins (blacklist bad symbols)
- Deploy and monitor for 2 weeks
- Iterate if needed

### Option B: ML-Based Optimization
- Build parameter optimization pipeline
- Use collected data for training
- Implement automated backtesting
- Deploy best-performing variant

### Option C: Hybrid Approach
- Quick manual fixes first (symbols, obvious weights)
- Plan ML optimization as Phase 2
- Continue data collection during manual tuning
- Evaluate ML feasibility after 8 weeks total data

---

## Files Referenced

### Local Repository
- `queries/weekly_symbol_performance.sql`
- `queries/weekly_signal_quality.sql`
- `queries/weekly_timing_analysis.sql`
- `scripts/weekly_strategy_review.sh`

### AWS Paths (after deployment)
- `/opt/bot/queries/` - SQL analysis queries
- `/opt/bot/weekly_strategy_review.sh` - Report generation script
- `/opt/bot/logs/weekly_review_YYYY-MM-DD.txt` - Weekly reports
- `/opt/bot/logs/weekly_review_cron.log` - Cron job output

### Database Tables
- `strategy_snapshots` - Strategy configuration versions
- `trade_strategy_link` - Links trades to active strategy
- `fifo_allocations` - Trade P&L via FIFO allocation
- `trade_records` - All order details
- `market_conditions` - Daily market regime tagging (new)

---

## Next Steps

**Immediate Actions Needed:**
1. ⚠️ **User Decision:** Confirm Jan 27, 2026 evaluation date
2. ⚠️ **Deployment:** Execute Steps 1-7 above to deploy infrastructure
3. ⚠️ **Baseline Snapshot:** Create initial strategy snapshot
4. ⚠️ **Cron Verification:** Ensure weekly reports generate successfully

**Then:**
- Monitor first weekly report (first Monday after deployment)
- Review data collection metrics weekly
- Make no strategy changes during data collection period
- Prepare for Jan 27 evaluation session

---

## Related Documentation

- **Schema Cleanup:** `docs/planning/NEXT_SESSION_SCHEMA_CLEANUP.md` (Jan 17 target)
- **Strategy Snapshots:** `database/migrations/002_create_strategy_snapshots_table.sql`
- **Trade Linkage:** Session documentation showing 100% linkage rate achieved
- **FIFO System:** `docs/active/architecture/FIFO_ALLOCATIONS_DESIGN.md`

---

**Status:** ⚠️ **ACTION REQUIRED** - Awaiting deployment to AWS to begin 4-week data collection

**Timeline:** Deploy ASAP → Collect 4 weeks → Evaluate Jan 27, 2026
