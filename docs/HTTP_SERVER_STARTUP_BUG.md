# Webhook HTTP Server Startup Failure - Critical Bug Investigation

**Date**: December 30, 2025
**Status**: 🚨 **CRITICAL BUG IDENTIFIED - UNDER INVESTIGATION**
**Impact**: 0% trade-strategy linkage rate due to webhook HTTP server not starting
**Branch**: `feature/strategy-optimization`
**Commits**:
- `842f107` - ROC score fix (WORKING)
- `0b24d32` - Debug logging added (DEPLOYED FOR INVESTIGATION)

---

## Executive Summary

The ROC momentum score bug fix is **working correctly** - sighook is generating valid scores and snapshot_ids. However, **0% linkage persists** because the webhook container's HTTP server on port 5003 is **not starting**, preventing sighook from delivering order metadata to webhook.

---

## Problem Statement

### What Should Happen
```
Sighook → HTTP POST to http://webhook:5003/webhook (with scores + snapshot_id)
       ↓
Webhook HTTP server receives request
       ↓
Metadata cached by product_id
       ↓
Trade fills via WebSocket
       ↓
Trade recorder retrieves cached metadata
       ↓
Linkage created in trade_strategy_link table ✅
```

### What's Actually Happening
```
Sighook → HTTP POST to http://webhook:5003/webhook (with scores + snapshot_id)
       ↓
❌ CONNECTION REFUSED (HTTP server not listening on port 5003)
       ↓
No metadata cached
       ↓
Trade fills via WebSocket
       ↓
Trade recorder: "No metadata cached for {symbol}, skipping linkage"
       ↓
0% linkage rate ❌
```

---

## Evidence

### 1. Container Health Status
```bash
$ docker inspect webhook --format='{{json .State.Health}}' | jq
{
    "Status": "unhealthy",
    "FailingStreak": 24,
    "Log": [
        {
            "ExitCode": 22,  # curl: HTTP returned error
            "Output": ""
        }
    ]
}
```

**Healthcheck**: `curl -sf http://127.0.0.1:5003/health`
**Result**: Exit code 22 (HTTP error - server not responding)

### 2. ROC Score Fix IS Working

Sighook logs show valid scores being sent:
```json
{
  "snapshot_id": "14deae94-a407-4d38-a1f5-5d183bbfc8ef",
  "score": {"Buy Score": 3.2, "Sell Score": 4.0},  // ✅ NOT NULL!
  "trigger": "roc_momo",
  "pair": "ZRX-USD"
}
```

### 3. Webhook Never Receives HTTP Requests

**Searched for**:
- `TradeBot started` (should appear at `main.py:550`) → **NOT FOUND**
- `STRATEGY_CACHE` (metadata caching) → **NOT FOUND**
- `POST /webhook` (HTTP request received) → **NOT FOUND**
- Any HTTP server startup logs → **NOT FOUND**

**Found instead**:
- WebSocket listener logs (market data) ✅
- Passive market making logs ✅
- Trade recording logs ✅
- `"[STRATEGY_LINK] No metadata cached for {symbol}, skipping linkage"` (every trade) ❌

### 4. All Trades Have source='websocket'

All trades after ROC fix deployment:
```sql
SELECT symbol, side, source, order_time
FROM trade_records
WHERE symbol IN ('ZRX-USD', 'AVNT-USD')
  AND order_time >= '2025-12-30 03:00:00';

 symbol    | side | source    | order_time
-----------|------|-----------|------------------
 ZRX-USD   | buy  | websocket | 2025-12-30 03:14:48
 AVNT-USD  | sell | websocket | 2025-12-30 03:15:29
 ZRX-USD   | sell | websocket | 2025-12-30 03:46:47
 ZRX-USD   | buy  | websocket | 2025-12-30 04:46:55
```

These are **manual/external trades**, not sighook-originated. Sighook's webhook requests are failing silently.

### 5. Sighook Orders Placed But Not Filled

Sighook successfully sent 2 orders:
- Order ID: `697620df-1aed-4855-8740-0ed1c60820b3` (ZRX-USD)
- Order ID: `f19454b8-625f-4886-835b-d840b5e3a15b` (ZRX-USD)

These orders likely never reached webhook (HTTP request failed) → never placed on exchange → never filled.

---

## Root Cause Analysis

### What's Running in Webhook Container

✅ **Components Running**:
1. WebSocket listener for market data (fills, orders, candles)
2. Passive market making
3. Asset monitoring
4. Position monitoring
5. Trade recording
6. Database operations

❌ **Component NOT Running**:
- HTTP server on port 5003 (`aiohttp.web.TCPSite`)
- `/webhook` POST endpoint
- `/health` GET endpoint

### Where HTTP Server Should Start

**File**: `main.py`
**Function**: `init_webhook()` (lines 445-552)
**Expected Flow**:
```python
async def init_webhook(...):
    # ... initialization ...

    app = await listener.create_app()          # Line 531 - Create aiohttp app
    runner = web.AppRunner(app)                # Line 538 - Create runner
    await runner.setup()                       # Line 541 - Setup runner
    site = web.TCPSite(runner, '0.0.0.0', 5003) # Line 544 - Create TCP site
    await site.start()                         # Line 547 - START HTTP SERVER

    webhook_logger.info("✅ TradeBot HTTP server started successfully")  # Line 550
```

**Expected Log**: `"✅ TradeBot HTTP server started successfully port=5003"`
**Actual Result**: **LOG NEVER APPEARS**

### Possible Failure Points

1. **`init_webhook()` never called**
   - `args.run == 'webhook'` condition not matching?
   - RUN_MODE environment variable incorrect?

2. **Exception during initialization** (before reaching HTTP server code)
   - Exception in `listener.async_init()` (line 487)
   - Exception in `build_websocket_components()` (line 494)
   - Exception in `market_data_updater.update_market_data()` (line 512)

3. **Exception during HTTP server creation**
   - `listener.create_app()` returns None
   - `runner.setup()` fails
   - `site.start()` fails with port conflict or permission error

4. **Silent failure without logging**
   - Code execution never reaches HTTP server creation
   - Background task/coroutine not awaited
   - Deadlock or infinite loop before server startup

---

## Debug Logging Added (Commit 0b24d32)

Added comprehensive logging to track execution flow:

### 1. Entry Point Detection
```python
if args.run == 'webhook':
    shared_logger.info("🔧 [HTTP_SERVER_DEBUG] RUN_MODE=webhook detected, calling run_webhook()")
```

### 2. Initialization Start
```python
async def init_webhook(...):
    webhook_init_logger.info("🔧 [HTTP_SERVER_DEBUG] init_webhook() called - starting webhook initialization")
```

### 3. HTTP Server Creation Steps
```python
webhook_logger.info("🔧 [HTTP_SERVER_DEBUG] About to call listener.create_app()")
app = await listener.create_app()
webhook_logger.info(f"🔧 [HTTP_SERVER_DEBUG] create_app() returned: {app}")

webhook_logger.info("🔧 [HTTP_SERVER_DEBUG] Creating AppRunner")
runner = web.AppRunner(app)

webhook_logger.info("🔧 [HTTP_SERVER_DEBUG] Calling runner.setup()")
await runner.setup()

webhook_logger.info(f"🔧 [HTTP_SERVER_DEBUG] Creating TCPSite on 0.0.0.0:{config.webhook_port}")
site = web.TCPSite(runner, '0.0.0.0', config.webhook_port)

webhook_logger.info("🔧 [HTTP_SERVER_DEBUG] Calling site.start() - HTTP server starting...")
await site.start()

webhook_logger.info("✅ TradeBot HTTP server started successfully", extra={'port': config.webhook_port})
```

---

## Next Steps for Investigation

### 1. Deploy Debug Logging

```bash
# Local
git pull origin feature/strategy-optimization  # Get commit 0b24d32

# AWS Server
ssh bottrader-aws
cd /opt/bot
git fetch origin
git checkout feature/strategy-optimization
git pull origin feature/strategy-optimization

# Verify correct commit
git log --oneline -1
# Should show: 0b24d32 debug: Add comprehensive HTTP server startup logging

# Rebuild webhook container
docker compose -f docker-compose.aws.yml build webhook
docker compose -f docker-compose.aws.yml up -d webhook
```

### 2. Monitor Startup Logs

```bash
# Watch container startup in real-time
docker logs -f webhook 2>&1 | grep -E 'HTTP_SERVER_DEBUG|TradeBot|RUN_MODE'

# After container starts, check all debug logs
docker logs webhook 2>&1 | grep 'HTTP_SERVER_DEBUG'
```

### 3. Expected Debug Output

**If working correctly**:
```
🔧 [HTTP_SERVER_DEBUG] RUN_MODE=webhook detected, calling run_webhook()
🔧 [HTTP_SERVER_DEBUG] init_webhook() called - starting webhook initialization
🔧 [HTTP_SERVER_DEBUG] About to call listener.create_app()
🔧 [HTTP_SERVER_DEBUG] create_app() returned: <Application ...>
🔧 [HTTP_SERVER_DEBUG] Creating AppRunner
🔧 [HTTP_SERVER_DEBUG] Calling runner.setup()
🔧 [HTTP_SERVER_DEBUG] Creating TCPSite on 0.0.0.0:5003
🔧 [HTTP_SERVER_DEBUG] Calling site.start() - HTTP server starting...
✅ TradeBot HTTP server started successfully port=5003
```

**Identify the failure point**:
- If no logs appear → `args.run != 'webhook'` (RUN_MODE issue)
- If stops after "init_webhook() called" → Exception during listener/component initialization
- If stops after "About to call listener.create_app()" → Exception in create_app()
- If stops after "Calling site.start()" → Port conflict or permission error

### 4. Verify RUN_MODE Environment Variable

```bash
docker exec webhook printenv | grep RUN_MODE
# Should output: RUN_MODE=webhook
```

### 5. Check for Exceptions

```bash
# Look for any exceptions during startup
docker logs webhook 2>&1 | grep -iE 'exception|error|traceback|failed' | head -50
```

---

## Configuration Verification

### docker-compose.aws.yml (Lines 25-84)

```yaml
webhook:
  build:
    context: .
    dockerfile: ./docker/Dockerfile.bot
  container_name: webhook
  environment:
    RUN_MODE: webhook  # ✅ Correct
  ports:
    - "127.0.0.1:5003:5003"  # ✅ Port exposed
  expose:
    - "5003"  # ✅ Internal exposure
  healthcheck:
    test: [ "CMD", "curl", "-sf", "http://127.0.0.1:5003/health" ]
    interval: 10s
    timeout: 3s
    retries: 10
```

### Entrypoint Script (docker/entrypoint/entrypoint.bot.sh)

```bash
start_app() {
  export PYTHONUNBUFFERED=1
  local mode="${RUN_MODE:-both}"  # Reads RUN_MODE env var

  log "Starting main (mode=${mode})..."
  exec python -u -m main --run "${mode}"
}
```

**Expected command**: `python -u -m main --run webhook`

---

## Workaround (Temporary)

While investigating, sighook can be temporarily disabled or webhook URL can be pointed to a test server to capture the payloads.

**NOT RECOMMENDED**: This would stop all automated trading.

---

## Success Criteria

Once bug is fixed:

1. ✅ Webhook container health: `healthy`
2. ✅ Log appears: `"✅ TradeBot HTTP server started successfully port=5003"`
3. ✅ Healthcheck passes: `curl http://localhost:5003/health` returns 200
4. ✅ Sighook webhook delivery succeeds (no connection refused errors)
5. ✅ Metadata cached: `"[STRATEGY_CACHE] Cached metadata for {symbol}"`
6. ✅ Linkage created: `"[STRATEGY_LINK] Created linkage for order {order_id}"`
7. ✅ Database linkage rate >0% (ideally >90%)

---

## Related Documentation

- [ROC Momentum Score Bug Analysis](./ROC_MOMENTUM_SCORE_BUG_ANALYSIS.md) - Root cause of NULL scores (FIXED)
- [Order Flow Documentation](./ORDER_FLOW_DOCUMENTATION.md) - All order entry points
- [Linkage Metadata Gap Analysis](./LINKAGE_METADATA_GAP_ANALYSIS.md) - Cache architecture analysis
- [Linkage Integration Deployment](./LINKAGE_INTEGRATION_DEPLOYMENT.md) - Original deployment guide

---

**Status**: Debug logging deployed (commit 0b24d32), awaiting container restart to gather diagnostic data.

**Next Session**: Analyze debug logs to identify exact failure point and implement fix.
