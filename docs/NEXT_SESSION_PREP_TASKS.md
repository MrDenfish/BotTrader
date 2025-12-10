# Next Session: Optimization Preparation Implementation

**Branch:** `strategy-optimization`
**Date:** December 9, 2025
**Context:** Setting up data collection infrastructure for 4-week evaluation period (Jan 7, 2025)

---

## Session Goal
Implement automated data collection and analysis to prepare for ML/optimization evaluation in 4 weeks.

---

## Prerequisites Completed
✅ Report calculation bug investigated and resolved
✅ `verify_report_accuracy.py` created and tested
✅ PEPE-USD performance verified (correctly NOT blacklisted)
✅ Preparation roadmap created (`docs/prepare_for_optimization.md`)
✅ Calendar reminder set for January 7, 2025

---

## Tasks for Next Session

### Priority 1: Create Baseline Strategy Snapshot
**Tool:** Use `database/strategy_snapshot_manager.py`

**Action:**
```bash
# Create baseline snapshot documenting current strategy
python3 database/strategy_snapshot_manager.py create \
  --note "Baseline strategy - Dec 9, 2025. RSI weight reduced from 2.5 to 1.5, added min_indicators_required=2"
```

**Verify:**
```sql
-- Should show new snapshot as active
SELECT * FROM current_strategy;
```

### Priority 2: Set Up Weekly Analysis Infrastructure

**2.1 Create Query Directory:**
```bash
ssh bottrader-aws "mkdir -p /opt/bot/queries"
```

**2.2 Upload Analysis Queries:**
Create and upload these three files from `docs/prepare_for_optimization.md`:
- `/opt/bot/queries/weekly_symbol_performance.sql`
- `/opt/bot/queries/weekly_signal_quality.sql`
- `/opt/bot/queries/weekly_timing_analysis.sql`

**2.3 Create Weekly Report Script:**
- Create `/opt/bot/weekly_strategy_review.sh` (see prepare_for_optimization.md)
- Make executable: `chmod +x /opt/bot/weekly_strategy_review.sh`
- Test run: `/opt/bot/weekly_strategy_review.sh`

**2.4 Install Cron Job:**
```bash
# Runs every Monday at 9am PT
ssh bottrader-aws "echo '0 9 * * 1 /opt/bot/weekly_strategy_review.sh >> /opt/bot/logs/weekly_review_cron.log 2>&1' | crontab -"
```

### Priority 3: Create Market Conditions Table

**Execute:**
```sql
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

-- Add today's market condition
INSERT INTO market_conditions (date, btc_change_pct, volatility_regime, trend, notes)
VALUES (
    CURRENT_DATE,
    NULL,  -- Fill in actual BTC change
    'medium',
    'sideways',
    'Initial baseline entry'
);
```

### Priority 4: Quick Wins - Symbol Blacklist Review

**Run:**
```sql
-- Find consistently losing symbols
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

**Action:** Add any new symbols to:
- `.env`: `EXCLUDED_SYMBOLS` (if exists)
- `sighook/trading_strategy.py`: `self.excluded_symbols` list

### Priority 5: Verify Trade Linkage

**Check:**
```sql
-- Verify trade_strategy_link is working
SELECT
    COUNT(DISTINCT tr.order_id) as total_trades,
    COUNT(DISTINCT tsl.order_id) as linked_trades,
    ROUND((COUNT(DISTINCT tsl.order_id)::decimal / NULLIF(COUNT(DISTINCT tr.order_id), 0) * 100)::numeric, 1) as link_rate_pct
FROM trade_records tr
LEFT JOIN trade_strategy_link tsl ON tsl.order_id = tr.order_id
WHERE tr.order_time >= NOW() - INTERVAL '7 days';
```

**Expected:** ~100% link rate
**If Low:** Investigate why trades aren't being linked to strategies

---

## Success Criteria

After this session, you should have:
- ✅ Baseline strategy snapshot created and marked as active
- ✅ Weekly analysis queries saved and tested
- ✅ Automated weekly report script running on cron
- ✅ Market conditions table created
- ✅ Symbol blacklist updated based on 30-day data
- ✅ Verification that trade linkage is working

---

## Files to Reference

- **Main Guide:** `docs/prepare_for_optimization.md`
- **Report Verification:** `verify_report_accuracy.py`
- **Snapshot Manager:** `database/strategy_snapshot_manager.py`
- **Migration:** `database/migrations/002_create_strategy_snapshots_table.sql`

---

## Key Context from Previous Session

**Report Bug Investigation:**
- Dec 9 report showed -$1.82 but database had -$0.12
- Likely stale data or old code version
- Current code verified as correct
- Container rebuilt to ensure latest code

**Performance Status:**
- PEPE-USD: +$4.44 mean PnL (star performer, NOT blacklisted)
- PENGU-USD: -$13.34 total (correctly blacklisted)
- Current 24h: -$1.18 (small sample, 6 allocations)

**Strategy Changes (Dec 9):**
- RSI weight: 2.5 → 1.5
- Min indicators required: 0 → 2
- Symbol blacklist: A8-USD, PENGU-USD

---

## Timeline

**Week 1 (Dec 9-15):** Setup infrastructure (this session)
**Weeks 2-4:** Collect data, review weekly reports
**January 7, 2025:** Evaluation - decide on optimization approach

---

## Notes

- All work stays in `strategy-optimization` branch
- Don't merge to main until after Jan 7 evaluation
- Weekly reports will automatically generate every Monday 9am PT
- Review `prepare_for_optimization.md` for detailed SQL queries and rationale
