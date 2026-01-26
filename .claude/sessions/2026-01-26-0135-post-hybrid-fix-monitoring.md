# Session: Post-Hybrid Fix Monitoring and Performance Analysis
**Date**: January 26, 2026 01:35 AM PST
**Status**: 🔜 Ready to Start
**Branch**: `feature/strategy-optimization`

---

## Session Context

This session continues the position_monitor bug fix initiative. The hybrid entry price calculation fix has been successfully deployed to production (commit `9f40405`).

**Previous Session**: `.claude/sessions/2026-01-25-2315-position-monitor-bug-investigation.md`

---

## Current State

### ✅ Completed (Previous Session)
1. Deep dive investigation of position_monitor 0% win rate bug
2. Root cause identified: API unrealized_pnl corruption + wide validation threshold
3. Hybrid fix implemented:
   - Primary: Database query for actual buy orders (ground truth)
   - Fallback: API with tightened validation (±30% instead of ±100%)
   - Safety: Skip exits if no reliable entry price
   - Staleness check: Reject bid/ask data >60s old
4. Deployed to AWS production with container rebuild
5. Verification: New code confirmed running

### 🔄 In Progress
- Multi-ROC strategies remain **disabled** (ROC_MOMO_20M, ROC_MOMO_24H)
- Test 2 configuration active (8.5% ROC threshold, RSI 45-55, 4% TP, 2% SL)
- Position monitor **re-enabled** with hybrid fix and extensive logging
- Monitoring phase: Waiting for positions to open

---

## Session Goals

### Primary Objectives

1. **Monitor Position Monitor Activity** (Next 24-48 hours)
   - Verify database queries successfully return entry prices
   - Confirm data source logging appears in position checks
   - Watch for any API fallback cases (should be rare)
   - Check for exit decisions and their reasoning

2. **Performance Analysis** (After 24-48 hours)
   - Query position_monitor exits since hybrid fix deployment
   - Calculate win rate (target: >30%, previous: 0%)
   - Compare database vs API data source usage
   - Evaluate exit decision quality
   - Measure P&L improvement

3. **Strategy Evaluation** (7-14 days)
   - Assess Test 2 performance vs backtest expectations
   - Compare position_monitor vs rearm_oco exit effectiveness
   - Decide on Multi-ROC re-enablement strategy
   - Optimize exit logic coordination

---

## Monitoring Checklist

### Immediate (Next Position Opens)
- [ ] Check logs for `[POS_MONITOR_DB]` entries showing database queries
- [ ] Verify `P&L Analysis` logs include `source:database` or `source:api_validated`
- [ ] Confirm no `NO RELIABLE ENTRY PRICE` errors (safety mechanism working)
- [ ] Watch for any unexpected behavior or errors

### 24-Hour Check
- [ ] Run SQL query: Count position_monitor exits since `2026-01-26 01:30:00`
- [ ] Check win rate: Should be >0% (any wins = improvement)
- [ ] Verify data source distribution (expect 90%+ from database)
- [ ] Review any API fallback cases for patterns

### 48-Hour Check
- [ ] Full performance analysis comparing before/after fix
- [ ] Calculate metrics:
  - Win rate (target: 30-40%)
  - Avg win/loss amounts
  - Exit trigger distribution
  - Data source reliability
- [ ] Document findings in this session file

### 7-Day Check
- [ ] Compare to Test 2 backtest expectations:
  - Expected: 57.9% win rate, 19 trades/60 days, -$1.94 total
  - Actual: TBD after monitoring period
- [ ] Evaluate position_monitor + rearm_oco coordination
- [ ] Decision point: Keep Test 2 only or re-enable Multi-ROC selectively

---

## SQL Queries for Analysis

### Query 1: Position Monitor Exits Since Fix

```sql
-- Get all position_monitor exits after hybrid fix deployment
SELECT
    fa.symbol,
    fa.sell_time,
    CAST(fa.sell_price AS NUMERIC(12,6)) as sell_price,
    CAST(fa.buy_price AS NUMERIC(12,6)) as actual_entry_price,
    CAST(fa.allocated_size AS NUMERIC(12,8)) as quantity,
    CAST(fa.pnl_usd AS NUMERIC(8,2)) as pnl_usd,
    CAST((fa.sell_price - fa.buy_price) / fa.buy_price AS NUMERIC(8,4)) as actual_pnl_pct,
    tr_sell.exit_reason,
    tr_sell.trigger->>'trigger' as trigger_type,
    CASE
        WHEN fa.pnl_usd > 0 THEN 'WIN'
        ELSE 'LOSS'
    END as outcome
FROM fifo_allocations fa
JOIN trade_records tr_sell ON fa.sell_order_id = tr_sell.order_id
WHERE fa.sell_time >= '2026-01-26 01:30:00'
  AND tr_sell.trigger->>'trigger' = 'position_monitor_exit'
ORDER BY fa.sell_time DESC;
```

### Query 2: Win Rate Comparison

```sql
-- Compare win rates before and after fix
WITH before_fix AS (
    SELECT
        COUNT(*) as total_exits,
        SUM(CASE WHEN fa.pnl_usd > 0 THEN 1 ELSE 0 END) as wins,
        SUM(fa.pnl_usd) as total_pnl
    FROM fifo_allocations fa
    JOIN trade_records tr_sell ON fa.sell_order_id = tr_sell.order_id
    WHERE fa.sell_time >= '2026-01-14'
      AND fa.sell_time < '2026-01-26 01:30:00'
      AND tr_sell.trigger->>'trigger' = 'position_monitor_exit'
),
after_fix AS (
    SELECT
        COUNT(*) as total_exits,
        SUM(CASE WHEN fa.pnl_usd > 0 THEN 1 ELSE 0 END) as wins,
        SUM(fa.pnl_usd) as total_pnl
    FROM fifo_allocations fa
    JOIN trade_records tr_sell ON fa.sell_order_id = tr_sell.order_id
    WHERE fa.sell_time >= '2026-01-26 01:30:00'
      AND tr_sell.trigger->>'trigger' = 'position_monitor_exit'
)
SELECT
    'BEFORE FIX' as period,
    total_exits,
    wins,
    CAST(wins::float / NULLIF(total_exits, 0) * 100 AS NUMERIC(5,2)) as win_rate_pct,
    CAST(total_pnl AS NUMERIC(8,2)) as total_pnl
FROM before_fix
UNION ALL
SELECT
    'AFTER FIX' as period,
    total_exits,
    wins,
    CAST(wins::float / NULLIF(total_exits, 0) * 100 AS NUMERIC(5,2)) as win_rate_pct,
    CAST(total_pnl AS NUMERIC(8,2)) as total_pnl
FROM after_fix;
```

### Query 3: Overall Strategy Performance

```sql
-- Full strategy breakdown post-fix
SELECT
    COALESCE(tr_buy.trigger->>'trigger', 'unknown') as entry_strategy,
    COUNT(*) as trades,
    SUM(CASE WHEN fa.pnl_usd > 0 THEN 1 ELSE 0 END) as wins,
    CAST(SUM(CASE WHEN fa.pnl_usd > 0 THEN 1 ELSE 0 END)::float / COUNT(*) * 100 AS NUMERIC(5,2)) as win_rate_pct,
    CAST(AVG(CASE WHEN fa.pnl_usd > 0 THEN fa.pnl_usd END) AS NUMERIC(8,2)) as avg_win,
    CAST(AVG(CASE WHEN fa.pnl_usd <= 0 THEN fa.pnl_usd END) AS NUMERIC(8,2)) as avg_loss,
    CAST(SUM(fa.pnl_usd) AS NUMERIC(8,2)) as total_pnl
FROM fifo_allocations fa
JOIN trade_records tr_sell ON fa.sell_order_id = tr_sell.order_id
LEFT JOIN trade_records tr_buy ON fa.buy_order_id = tr_buy.order_id
WHERE fa.sell_time >= '2026-01-26 01:30:00'
GROUP BY COALESCE(tr_buy.trigger->>'trigger', 'unknown')
ORDER BY total_pnl DESC;
```

---

## Expected Results

### Success Criteria (24-48 hours)

| Metric | Before Fix | Target | Status |
|--------|-----------|--------|--------|
| Position monitor win rate | 0% (0/18) | >30% | TBD |
| Avg loss per exit | -$0.610 | -$0.30 or better | TBD |
| Data source: database | 0% | >90% | TBD |
| False exits avoided | 0% | 100% | TBD |

### Success Criteria (7-14 days)

| Metric | Before Fixes | Target | Status |
|--------|-------------|--------|--------|
| Overall win rate | 26.7% | 50-60% | TBD |
| Trade frequency | 15.5/day | 0.3-0.5/day | TBD |
| Total P&L | -$43/11 days | -$5/7 days or better | TBD |
| Position monitor contribution | -73% of losses | Neutral or positive | TBD |

---

## Decision Points

### Re-enable Multi-ROC? (After Test 2 evaluation)

**Option A: Keep Test 2 Only**
- If Test 2 performs close to backtest expectations
- Simpler strategy, easier to manage
- Trade frequency remains low (~0.5/day)

**Option B: Re-enable ROC_MOMO_24H Only**
- Had best Multi-ROC performance (34% win rate)
- Add diversification to Test 2
- Monitor combined performance

**Option C: Re-enable Both Multi-ROC**
- If position_monitor fix improves their win rates
- Requires careful monitoring of trade frequency
- May need parameter tuning

**Option D: Run New Backtest**
- Backtest Multi-ROC with current market conditions
- Compare to Test 2 performance
- Data-driven decision

---

## Tasks for Next Session

1. **When positions open**:
   - Monitor logs for database query success
   - Verify data source logging
   - Check for any errors or unexpected behavior

2. **After 24 hours**:
   - Run SQL queries (Query 1 & 2 above)
   - Calculate initial metrics
   - Document findings

3. **After 48 hours**:
   - Full performance analysis (Query 3)
   - Compare to pre-fix baseline
   - Update this document with results

4. **After 7 days**:
   - Comprehensive strategy evaluation
   - Compare to Test 2 backtest expectations
   - Make decision on Multi-ROC re-enablement

---

## Notes Section

*Session notes and findings will be documented here as monitoring progresses...*

### Log Samples to Watch For

**Database Success**:
```
[POS_MONITOR_DB] BTC-USD: ✅ Database entry price | avg_entry=$95000.50, qty=0.00105000, orders=3
[POS_MONITOR] BTC-USD: P&L Analysis | Entry=$95000.50 (source:database), Current=$96000.00, P&L_raw=+1.05%, P&L_net=+0.93%
```

**API Fallback**:
```
[POS_MONITOR_DB] ETH-USD: No open buy orders found in database
[POS_MONITOR] ETH-USD: Using API entry price (DB unavailable) | entry=$3200.00, source=api_validated
```

**Safety Mechanism**:
```
[POS_MONITOR] XRP-USD: ❌ NO RELIABLE ENTRY PRICE | API validation FAILED, database unavailable. ⚠️ SKIPPING EXIT CHECK
```

---

**Document Status**: 🔜 Ready to Start
**Created**: January 26, 2026 01:35 AM PST
**Next Update**: After first position opens or 24-hour check
