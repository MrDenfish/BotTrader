# Deployment Guide: Blacklist Expansion (Dec 28, 2025)

**Branch**: `feature/strategy-optimization`
**Commits**: `935062d`, `b13b65d`, `775ccda`
**Risk Level**: 🟢 LOW (blacklist expansion only)
**Estimated Downtime**: < 2 minutes

---

## Changes Summary

### 1. Blacklist Expansion
**File**: `sighook/trading_strategy.py:60-72`

**Added 3 symbols** based on 28-day performance analysis:
- `FARM-USD`: 0% win rate (0/11 trades), -$8.40 loss
- `XLM-USD`: 0% win rate (0/5 trades), -$5.00 loss
- `AVT-USD`: 32.56% win rate (14/43 trades), -$10.16 loss

**Expected Impact**: Save ~$23-25/month in preventable losses

### 2. Infrastructure (No Immediate Effect)
**Files**:
- `TableModels/trade_strategy_link.py` (new model)
- `SharedDataManager/trade_recorder.py` (new methods)

**Purpose**: Preparation for optimization (not yet integrated)
**Risk**: None - methods not called yet

---

## Pre-Deployment Checklist

### Verify Current State
```bash
# Check current branch on AWS
ssh bottrader-aws "cd /opt/bot && git branch"

# Confirm bot is running
ssh bottrader-aws "docker ps | grep -E 'sighook|webhook'"

# Check recent trading activity (should be normal)
ssh bottrader-aws "docker exec db psql -U bot_user -d bot_trader_db -c \"SELECT COUNT(*) FROM trade_records WHERE order_time >= NOW() - INTERVAL '1 hour';\""
```

**Expected**:
- Branch: `main` (we'll switch to feature branch)
- Containers: Both running (healthy)
- Recent trades: 1-5 trades in last hour

---

## Deployment Steps

### Step 1: Pull Latest Code
```bash
ssh bottrader-aws "cd /opt/bot && git fetch origin"
ssh bottrader-aws "cd /opt/bot && git checkout feature/strategy-optimization"
ssh bottrader-aws "cd /opt/bot && git pull origin feature/strategy-optimization"
```

**Verify**:
```bash
ssh bottrader-aws "cd /opt/bot && git log --oneline -3"
```

**Expected output** should include:
```
935062d feat: Expand blacklist with 3 worst performing symbols
b13b65d feat: Add trade-strategy linkage infrastructure (WIP)
775ccda docs: Add optimization readiness assessment (Dec 28, 2025)
```

---

### Step 2: Rebuild Sighook Container
```bash
ssh bottrader-aws "cd /opt/bot && docker compose -f docker-compose.aws.yml build sighook"
```

**Why rebuild**: Code changes require image rebuild (restart alone won't pick up changes)

**Expected**: Build completes without errors (1-2 minutes)

---

### Step 3: Restart Sighook
```bash
ssh bottrader-aws "cd /opt/bot && docker compose -f docker-compose.aws.yml up -d sighook"
```

**Alternative** (if webhook dependency issues):
```bash
ssh bottrader-aws "docker start sighook"
```

---

### Step 4: Verify Deployment
```bash
# Check container is running
ssh bottrader-aws "docker ps | grep sighook"

# Check startup logs (should show no errors)
ssh bottrader-aws "docker logs sighook 2>&1 | tail -30"

# Verify blacklist loaded (should see skip messages within 5-10 min)
ssh bottrader-aws "docker logs sighook 2>&1 | grep -E '⛔.*Skipping excluded' | tail -10"
```

**Expected**:
- ✅ Container running
- ✅ No startup errors
- ✅ Logs show normal operation
- ✅ Skip messages for excluded symbols

---

## 24-Hour Verification Plan

### Immediate Checks (First Hour)

**Every 15 minutes for first hour:**
```bash
# Check for new blacklist skip events
ssh bottrader-aws "docker logs sighook 2>&1 | grep -E '⛔.*(FARM|XLM|AVT)-USD' | tail -20"
```

**Expected**: Should see at least 1-2 skip messages in first hour

---

### 4-Hour Checkpoint

```bash
# Verify no trades on blacklisted symbols
ssh bottrader-aws "docker exec db psql -U bot_user -d bot_trader_db -c \"
SELECT COUNT(*) as blocked_symbol_trades
FROM trade_records
WHERE symbol IN ('FARM-USD', 'XLM-USD', 'AVT-USD')
  AND order_time >= NOW() - INTERVAL '4 hours';
\""
```

**Expected**: `blocked_symbol_trades = 0`

**If not 0**: 🚨 Blacklist not working, investigate immediately

```bash
# Verify normal trading continues
ssh bottrader-aws "docker exec db psql -U bot_user -d bot_trader_db -c \"
SELECT COUNT(*) as total_trades
FROM trade_records
WHERE order_time >= NOW() - INTERVAL '4 hours';
\""
```

**Expected**: `total_trades = 5-20` (normal activity)

**If 0**: 🚨 Bot stopped trading, check logs for errors

---

### 24-Hour Final Verification

**Run these commands after 24 hours:**

#### 1. Blacklist Effectiveness
```bash
ssh bottrader-aws "docker exec db psql -U bot_user -d bot_trader_db -c \"
SELECT
    symbol,
    COUNT(*) as trades,
    MIN(order_time) as first_trade,
    MAX(order_time) as last_trade
FROM trade_records
WHERE symbol IN ('FARM-USD', 'XLM-USD', 'AVT-USD')
  AND order_time >= NOW() - INTERVAL '24 hours'
GROUP BY symbol;
\""
```

**Expected**: `0 rows` (no trades on blacklisted symbols)

---

#### 2. Skip Log Count
```bash
ssh bottrader-aws "docker logs sighook 2>&1 | grep -E '⛔.*(FARM|XLM|AVT)-USD' | wc -l"
```

**Expected**: `5-15` skip events (depends on market activity)

**If 0**: ⚠️ Symbols didn't appear in ticker feed (not necessarily a problem, just no opportunities)

---

#### 3. Overall Trading Health
```bash
ssh bottrader-aws "docker exec db psql -U bot_user -d bot_trader_db -c \"
SELECT
    COUNT(*) as total_trades,
    SUM(CASE WHEN side = 'buy' THEN 1 ELSE 0 END) as buys,
    SUM(CASE WHEN side = 'sell' THEN 1 ELSE 0 END) as sells,
    COUNT(DISTINCT symbol) as unique_symbols
FROM trade_records
WHERE order_time >= NOW() - INTERVAL '24 hours';
\""
```

**Expected**:
- `total_trades`: 20-50
- `buys` ≈ `sells` (balanced)
- `unique_symbols`: 10-30

---

#### 4. Container Stability
```bash
# Check for crashes/restarts
ssh bottrader-aws "docker ps -a | grep sighook"

# Check for error patterns
ssh bottrader-aws "docker logs sighook 2>&1 | grep -i -E 'error|exception|failed' | tail -20"
```

**Expected**:
- Container uptime = ~24 hours
- No critical errors in logs

---

## Success Criteria (24-Hour Test)

All of these must be TRUE to proceed with strategy linkage work:

- [ ] **Zero trades** on FARM-USD, XLM-USD, AVT-USD
- [ ] **At least 3** skip log messages for new symbols
- [ ] **Normal trading** continues (20-50 trades on other symbols)
- [ ] **No crashes** or container restarts
- [ ] **No critical errors** in logs

---

## Rollback Plan (If Needed)

If any critical issue is found:

```bash
# Stop sighook
ssh bottrader-aws "docker stop sighook"

# Switch back to main branch
ssh bottrader-aws "cd /opt/bot && git checkout main"

# Rebuild and restart
ssh bottrader-aws "cd /opt/bot && docker compose -f docker-compose.aws.yml build sighook"
ssh bottrader-aws "docker start sighook"

# Verify
ssh bottrader-aws "docker logs sighook 2>&1 | tail -30"
```

---

## Timeline

| Time | Action |
|------|--------|
| **T+0** (Now) | Deploy to AWS |
| **T+15min** | First verification check |
| **T+1hr** | Confirm skip messages appearing |
| **T+4hr** | Mid-point check (no blocked trades) |
| **T+24hr** | Final verification (run all checks) |
| **T+24hr+1hr** | Review results, decide to proceed |

---

## Post-Deployment Notes

### What Changed
- ✅ 3 new symbols added to static blacklist fallback
- ✅ TradeStrategyLink model added (not yet used)
- ✅ Strategy linkage methods added to TradeRecorder (not yet called)

### What Didn't Change
- Trading strategy parameters (same as before)
- TP/SL thresholds (same as before)
- Position monitor logic (same as before)
- Webhook order flow (same as before)

### Expected Behavior
- Fewer trades overall (blocking 3 losing symbols)
- No change in win rate on remaining symbols
- Slight reduction in daily PnL volatility (removing losers)

---

## Next Steps (After 24-Hour Verification)

If all success criteria met:

1. ✅ Mark blacklist expansion as **VERIFIED**
2. ✅ Document actual skip counts and savings
3. ✅ Begin strategy linkage integration work
4. ✅ Plan full deployment to main branch (after linkage complete)

---

**Deployment Date**: December 28, 2025
**Deployed By**: [Your Name]
**Verification Due**: December 29, 2025 (same time)

---

## Quick Reference Commands

**Check deployment:**
```bash
ssh bottrader-aws "cd /opt/bot && git log --oneline -1"
```

**Watch logs live:**
```bash
ssh bottrader-aws "docker logs -f --tail 50 sighook"
```

**Emergency stop:**
```bash
ssh bottrader-aws "docker stop sighook"
```

**Emergency restart:**
```bash
ssh bottrader-aws "docker start sighook"
```
