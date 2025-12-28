# 24-Hour Verification Checklist - December 29, 2025

**Deployment Date**: December 28, 2025 ~21:00 UTC
**Verification Due**: December 29, 2025 ~21:00 UTC
**Branch**: feature/strategy-optimization
**Changes**: Blacklist expansion (FARM-USD, XLM-USD, AVT-USD)

---

## Quick Verification Commands

Run these commands ~24 hours after deployment:

### 1. ✅ No Trades on Blacklisted Symbols
```bash
ssh bottrader-aws "docker exec db psql -U bot_user -d bot_trader_db -c \"
SELECT
    symbol,
    COUNT(*) as trades,
    MIN(order_time) as first_trade,
    MAX(order_time) as last_trade
FROM trade_records
WHERE symbol IN ('FARM-USD', 'XLM-USD', 'AVT-USD')
  AND order_time >= '2025-12-28 21:00:00'
GROUP BY symbol;
\""
```
**Expected**: `0 rows` (no trades)
**Pass/Fail**: ___________

---

### 2. ✅ Skip Events in Logs
```bash
ssh bottrader-aws "docker logs sighook 2>&1 | grep -E '⛔.*(FARM|XLM|AVT)-USD' | wc -l"
```
**Expected**: `5-20` skip events
**Actual Count**: ___________
**Pass/Fail**: ___________

---

### 3. ✅ Normal Trading Continues
```bash
ssh bottrader-aws "docker exec db psql -U bot_user -d bot_trader_db -c \"
SELECT
    COUNT(*) as total_trades,
    SUM(CASE WHEN side = 'buy' THEN 1 ELSE 0 END) as buys,
    SUM(CASE WHEN side = 'sell' THEN 1 ELSE 0 END) as sells,
    COUNT(DISTINCT symbol) as unique_symbols
FROM trade_records
WHERE order_time >= '2025-12-28 21:00:00';
\""
```
**Expected**:
- total_trades: 20-50
- buys ≈ sells
- unique_symbols: 10-30

**Actual Results**:
- total_trades: ___________
- buys: ___________
- sells: ___________
- unique_symbols: ___________
**Pass/Fail**: ___________

---

### 4. ✅ Container Stability
```bash
ssh bottrader-aws "docker ps -a | grep sighook"
```
**Check**: Uptime should be ~24 hours
**Actual Uptime**: ___________
**Pass/Fail**: ___________

```bash
ssh bottrader-aws "docker logs sighook 2>&1 | grep -i -E 'error|exception|failed' | grep -v 'INVALID_LIMIT_PRICE_POST_ONLY' | tail -20"
```
**Expected**: No critical errors (ignore SPK-USD OCO errors - pre-existing)
**Critical Errors Found**: ___________
**Pass/Fail**: ___________

---

## Success Criteria

All must be TRUE to proceed:

- [ ] Zero trades on FARM-USD, XLM-USD, AVT-USD
- [ ] At least 3 skip log messages for new symbols
- [ ] Normal trading continues (20+ trades on other symbols)
- [ ] No crashes or container restarts
- [ ] No critical errors in logs

**Overall Result**: ⬜ PASS / ⬜ FAIL

---

## If All Checks Pass ✅

Next session priorities:
1. **Complete trade-strategy linkage integration**
   - Cache strategy metadata in shared_data_manager
   - Hook into webhook order placement
   - Call create_strategy_link() when trades fill
   - Target: 100% linkage rate

2. **Deploy weekly automation**
   - Verify `scripts/analytics/weekly_strategy_review.sh` on server
   - Set up cron job (Monday 9am PT)
   - Test manual run

3. **Build daily performance aggregation job**
   - Create `jobs/daily_performance_summary.py`
   - Populate strategy_performance_summary table
   - Set up cron (daily at 00:10 UTC)

---

## If Any Checks Fail ❌

### Investigation Steps:
1. Review full sighook logs for errors
2. Check if dynamic filter is interfering with static blacklist
3. Verify git commit on server matches expected (4368726)
4. Review database for unexpected trades

### Rollback if Needed:
```bash
ssh bottrader-aws "cd /opt/bot && git checkout main"
ssh bottrader-aws "cd /opt/bot && docker compose -f docker-compose.aws.yml build sighook"
ssh bottrader-aws "docker start sighook"
```

---

## Notes Section

**Observations**:
_____________________________________________________________
_____________________________________________________________
_____________________________________________________________

**Issues Found**:
_____________________________________________________________
_____________________________________________________________

**Decision**:
⬜ Proceed with strategy linkage work
⬜ Investigate issues first
⬜ Rollback required

---

**Verified By**: ___________________
**Date**: December 29, 2025
**Time**: ___________________
