# BotTrader Monitoring Command Reference
**Last Updated**: January 1, 2026
**Purpose**: Quick reference for monitoring bot activity and health

---

## Quick Health Check

### Check All Container Status
```bash
ssh bottrader-aws "cd /opt/bot && docker compose -f docker-compose.aws.yml ps"
```
**Expected**: All containers show `(healthy)` status

---

## Webhook Monitoring

### Check for Recent Webhooks (Last 10 minutes)
```bash
ssh bottrader-aws "docker logs webhook --tail 100 --since 10m 2>&1 | grep 'Receiving webhook'"
```
**Shows**: All webhooks received in last 10 minutes with full payload

### Check Metadata Caching Debug Logs
```bash
ssh bottrader-aws "docker logs webhook --tail 100 --since 10m 2>&1 | grep 'STRATEGY_CACHE'"
```
**Shows**: Whether `_cache_strategy_metadata()` is being called
**Expected**: Should see logs like:
- `🔧 [DEBUG] About to call _cache_strategy_metadata for {symbol}`
- `🔧 [DEBUG] _cache_strategy_metadata ENTERED with trade_data keys: [...]`

### Check HTTP Server Status
```bash
ssh bottrader-aws "docker logs webhook --tail 50 2>&1 | grep 'HTTP_SERVER'"
```
**Shows**: HTTP server startup messages
**Expected**: `🔧 [HTTP_SERVER_DEBUG] Calling site.start() - HTTP server starting...`

---

## Sighook (Signal Generation) Monitoring

### Check Current Signals Above Threshold
```bash
ssh bottrader-aws "docker logs sighook --tail 50 2>&1 | grep -E \"above threshold|'ok'\)\""
```
**Shows**: All symbols with scores >= 2.0 (current threshold)
**Example**: `│ AAVE │ AAVE │ (1, 2.5, 2.0, 'ok') │ (0, 0.0, 2.0, 'below threshold') │`

### Check Recent Signal Matrix
```bash
ssh bottrader-aws "docker logs sighook --tail 200 2>&1 | tail -50"
```
**Shows**: Full buy/sell matrix with all symbols and scores

### Check for Webhook Sends
```bash
ssh bottrader-aws "docker logs sighook --tail 100 --since 5m 2>&1 | grep -E 'webhook|POST|HTTP'"
```
**Shows**: Webhook POST requests sent to webhook container

---

## Position Monitor & Trading Activity

### Check Active Positions Being Monitored
```bash
ssh bottrader-aws "docker logs webhook --tail 100 2>&1 | grep 'POS_MONITOR'"
```
**Shows**: Position monitoring activity, P&L calculations, exit triggers
**Example**: `[POS_MONITOR] {symbol}: P&L_raw={x}%, P&L_net={y}%`

### Check Fee-Aware P&L Calculations
```bash
ssh bottrader-aws "docker logs webhook --tail 100 2>&1 | grep 'P&L_raw\|P&L_net'"
```
**Shows**: Both raw and fee-aware P&L for positions
**Expected**: `P&L_raw` (before fees) and `P&L_net` (after 0.75% fees)

### Check Trade Executions
```bash
ssh bottrader-aws "docker logs webhook --tail 100 --since 10m 2>&1 | grep -E 'Order (placed|filled|cancelled)'"
```
**Shows**: Recent order activity

---

## Performance & Metrics

### Check Recent Trade Records (Database)
```bash
ssh bottrader-aws "docker exec db psql -U bot_user -d bot_trader_db -c \"SELECT order_time AT TIME ZONE 'UTC' as time, symbol, side, ROUND(price::numeric, 4) as price, ROUND(pnl_usd::numeric, 2) as pnl FROM trade_records WHERE order_time >= NOW() - INTERVAL '24 hours' AND side = 'sell' ORDER BY order_time DESC LIMIT 10;\""
```
**Shows**: Last 10 sell trades with P&L

### Check Today's Trading Summary
```bash
ssh bottrader-aws "docker exec db psql -U bot_user -d bot_trader_db -c \"SELECT COUNT(*) as trades, SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END) as wins, SUM(CASE WHEN pnl_usd < 0 THEN 1 ELSE 0 END) as losses, ROUND(SUM(pnl_usd)::numeric, 2) as net_pnl FROM trade_records WHERE DATE(order_time AT TIME ZONE 'UTC') = CURRENT_DATE AND side = 'sell';\""
```
**Shows**: Today's win/loss count and net P&L

---

## Configuration Verification

### Check Current Risk Parameters
```bash
ssh bottrader-aws "grep -E 'ORDER_SIZE.*=|MAX_LOSS_PCT|HARD_STOP_PCT|MIN_PROFIT_PCT|TRAILING_ACTIVATION' /opt/bot/.env | grep -v '^#'"
```
**Shows**: Position sizes, stop-losses, profit targets

### Check Momentum Thresholds
```bash
ssh bottrader-aws "grep -E 'SCORE.*TARGET' /opt/bot/.env | grep -v '^#'"
```
**Shows**: Current buy/sell score thresholds

### Check Excluded Symbols
```bash
ssh bottrader-aws "grep 'EXCLUDED_SYMBOLS' /opt/bot/.env"
```
**Shows**: List of blocked symbols

---

## Troubleshooting Commands

### Check Container Logs for Errors (Last 5 minutes)
```bash
# Webhook errors
ssh bottrader-aws "docker logs webhook --since 5m 2>&1 | grep -E 'ERROR|Exception|Traceback'"

# Sighook errors
ssh bottrader-aws "docker logs sighook --since 5m 2>&1 | grep -E 'ERROR|Exception|Traceback'"
```

### Check Database Connectivity
```bash
ssh bottrader-aws "docker exec db pg_isready -U bot_user -d bot_trader_db"
```
**Expected**: `accepting connections`

### Restart Specific Container
```bash
# Restart webhook
ssh bottrader-aws "cd /opt/bot && docker compose -f docker-compose.aws.yml restart webhook"

# Restart sighook
ssh bottrader-aws "cd /opt/bot && docker compose -f docker-compose.aws.yml restart sighook"

# Restart both
ssh bottrader-aws "cd /opt/bot && docker compose -f docker-compose.aws.yml restart webhook sighook"
```

### View Full Container Logs
```bash
# Last 100 lines of webhook logs
ssh bottrader-aws "docker logs webhook --tail 100"

# Last 100 lines of sighook logs
ssh bottrader-aws "docker logs sighook --tail 100"

# Follow webhook logs in real-time (Ctrl+C to stop)
ssh bottrader-aws "docker logs webhook --follow"
```

---

## Current Configuration (As of Jan 1, 2026)

### Risk Parameters (Session 2)
- **Position Size**: $15 (ORDER_SIZE_WEBHOOK, ORDER_SIZE_ROC, ORDER_SIZE_SIGNAL)
- **Soft Stop**: -3.0% (MAX_LOSS_PCT)
- **Hard Stop**: -4.5% (HARD_STOP_PCT)
- **Blocked Symbols**: XRP-USD, SAPIEN-USD, FARTCOIN-USD, MON-USD, WET-USD, SOL-USD, PUMP-USD, RLS-USD

### Strategy Parameters (Session 3)
- **Take Profit**: 2.0% (MIN_PROFIT_PCT)
- **Trailing Activation**: 2.0% (TRAILING_ACTIVATION_PCT)
- **Buy/Sell Threshold**: 2.0 (SCORE_BUY_TARGET, SCORE_SELL_TARGET)

### Expected Behavior
- **Fee-aware P&L**: All position monitoring uses 0.75% round-trip fees (0.25% maker + 0.50% taker)
- **Signals**: Any symbol scoring >= 2.0 should trigger webhook
- **Exits**: Positions exit at +2.0% profit or -3.0% loss (fee-inclusive)

---

## Quick Diagnostic Checklist

When checking bot health, verify:

- [ ] All containers healthy: `docker compose ps`
- [ ] Recent webhooks received: Check webhook logs for "Receiving webhook"
- [ ] Signals being generated: Check sighook logs for scores >= 2.0
- [ ] Metadata caching working: Check for `STRATEGY_CACHE_DEBUG` logs
- [ ] No errors in last hour: Check both containers for ERROR/Exception
- [ ] Database responsive: `pg_isready` check
- [ ] Today's P&L: Run trading summary query

---

## Useful Shortcuts

### Create Background Monitor for Webhooks
```bash
# In Claude Code, this creates a long-running background process
ssh bottrader-aws "docker logs webhook --follow 2>&1 | grep -E 'Receiving webhook|STRATEGY_CACHE'" &
```
**Note**: Background monitors disconnect when containers restart. Re-create after restarts.

### One-Line Health Check
```bash
ssh bottrader-aws "cd /opt/bot && docker compose -f docker-compose.aws.yml ps && echo '---' && grep -E 'ORDER_SIZE_WEBHOOK|SCORE_BUY_TARGET|MIN_PROFIT_PCT' /opt/bot/.env | grep -v '^#'"
```

### Quick P&L Check (Last 24 hours)
```bash
ssh bottrader-aws "docker exec db psql -U bot_user -d bot_trader_db -c \"SELECT COUNT(*) as sells, SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END) as wins, ROUND(SUM(pnl_usd)::numeric, 2) as net_pnl FROM trade_records WHERE order_time >= NOW() - INTERVAL '24 hours' AND side = 'sell';\""
```

---

## Important Notes

1. **Container Restarts**: Background monitors (`docker logs --follow`) disconnect when containers restart. Always re-create monitors after restarts.

2. **Timezone**: Database stores times in UTC. Use `AT TIME ZONE 'UTC'` in queries or convert to your local timezone.

3. **P&L Discrepancy**: Database P&L has cost_basis calculation bug (Session 1 finding). For accurate P&L, download Coinbase transaction CSV.

4. **Debug Logging**: Metadata caching debug logs were added in commit cc95e62. Only appears after container restart (Jan 1, 2026 00:45 UTC).

5. **Slow Markets**: With SCORE threshold at 2.0, expect 1-5 signals per hour in normal conditions, fewer in very slow markets.

---

**End of Reference Guide**

For session notes and detailed change history, see:
- `.claude/sessions/2026-01-01-session1-critical-bugs.md`
- `.claude/sessions/2026-01-01-session2-risk-management.md`
- `.claude/sessions/2026-01-01-session3-strategy-optimization.md`
