# HTTP Server Startup Failure - Prevention Strategy

**Date**: December 30, 2025
**Purpose**: Prevent webhook HTTP server startup failures and detect them quickly if they occur
**Related**: [HTTP_SERVER_STARTUP_BUG.md](./HTTP_SERVER_STARTUP_BUG.md)

---

## Problem Recap

The webhook HTTP server on port 5003 failed to start during a previous deployment, causing:
- 0% trade-strategy linkage rate
- Container entered crash-loop (restarted by Docker)
- Early startup logs cleared, hiding the actual failure
- 16-hour gap in logs (03:12 → 19:03 UTC)
- Misleading "healthy" appearance (container running, but server not listening)

**Root Cause**: Container crashed during startup, Docker restarted it, logs were cleared leaving only recent logs visible.

---

## Multi-Layer Prevention Strategy

### Layer 1: Enhanced Startup Validation

#### 1.1 HTTP Server Readiness Check

**Purpose**: Verify HTTP server is actually listening before considering startup complete

**Implementation** (`main.py`):
```python
async def verify_http_server_ready(port: int, max_retries: int = 5, delay: float = 0.5):
    """Verify HTTP server is actually listening and responding"""
    import aiohttp

    for attempt in range(max_retries):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"http://127.0.0.1:{port}/health", timeout=aiohttp.ClientTimeout(total=2)) as resp:
                    if resp.status == 200:
                        return True
        except Exception:
            if attempt < max_retries - 1:
                await asyncio.sleep(delay)
            continue
    return False

# In init_webhook(), after site.start():
await site.start()

webhook_logger.info("🔧 [HTTP_SERVER] Verifying server is ready...")
if await verify_http_server_ready(config.webhook_port):
    webhook_logger.info("✅ TradeBot HTTP server verified and ready", extra={'port': config.webhook_port})
else:
    raise RuntimeError(f"❌ HTTP server started but not responding on port {config.webhook_port}")
```

**Benefits**:
- Catches scenarios where `site.start()` succeeds but server isn't actually listening
- Fails fast with clear error message
- Prevents silent startup failures

#### 1.2 Startup Timeout Protection

**Purpose**: Prevent indefinite hangs during startup

**Implementation**:
```python
# In main.py, wrap init_webhook() call
try:
    listener, websocket_manager, app, runner = await asyncio.wait_for(
        init_webhook(...),
        timeout=120  # 2 minutes max for startup
    )
except asyncio.TimeoutError:
    shared_logger.error("❌ Webhook initialization timed out after 120s")
    raise RuntimeError("Webhook startup timeout - check logs for blocking operations")
```

**Benefits**:
- Prevents container from appearing to run while actually stuck
- Clear error message for troubleshooting
- Forces container to fail (and restart) rather than hang

---

### Layer 2: Docker Healthcheck Improvements

#### 2.1 Startup Grace Period

**Current Issue**: Healthcheck starts immediately, may fail during normal startup
**Solution**: Increase `start_period` to allow initialization to complete

**Implementation** (`docker-compose.aws.yml`):
```yaml
webhook:
  healthcheck:
    test: [ "CMD", "curl", "-sf", "http://127.0.0.1:5003/health" ]
    interval: 10s
    timeout: 3s
    retries: 10
    start_period: 60s  # ← INCREASED from 10s to 60s
```

**Benefits**:
- Prevents false "unhealthy" status during initialization
- Allows FIFO maintenance, DB setup, etc. to complete
- More realistic health assessment

#### 2.2 Better Healthcheck Logging

**Purpose**: Log healthcheck failures for debugging

**Implementation** (create `/app/scripts/healthcheck.sh`):
```bash
#!/bin/bash
set -e

RESPONSE=$(curl -sf http://127.0.0.1:5003/health 2>&1)
EXIT_CODE=$?

if [ $EXIT_CODE -ne 0 ]; then
    echo "[HEALTHCHECK_FAIL] $(date -Iseconds) Exit=$EXIT_CODE Response=$RESPONSE" >> /app/logs/healthcheck.log
    exit $EXIT_CODE
fi

# Check if response is valid JSON with status=ok
if echo "$RESPONSE" | grep -q '"status".*:.*"ok"'; then
    exit 0
else
    echo "[HEALTHCHECK_FAIL] $(date -Iseconds) Invalid response: $RESPONSE" >> /app/logs/healthcheck.log
    exit 1
fi
```

Update `docker-compose.aws.yml`:
```yaml
healthcheck:
  test: [ "CMD", "/app/scripts/healthcheck.sh" ]
```

**Benefits**:
- Persistent log of healthcheck failures
- Helps diagnose intermittent issues
- Log survives container restarts

---

### Layer 3: Monitoring & Alerting

#### 3.1 Startup Success Notification

**Purpose**: Confirm successful startup via external notification

**Implementation** (`main.py`):
```python
# In init_webhook(), after server verified ready:
if alert:  # AlertSystem instance
    alert.callhome(
        subject="Webhook HTTP Server Started",
        message=f"✅ HTTP server started successfully on port {config.webhook_port} at {datetime.now(timezone.utc).isoformat()}",
        mode="email"
    )
```

**Benefits**:
- Positive confirmation of successful startup
- If email not received, indicates startup failure
- Useful after deployments

#### 3.2 Container Restart Detection

**Purpose**: Alert on unexpected container restarts

**Script** (`scripts/monitor_restarts.sh`):
```bash
#!/bin/bash
# Run via cron every 5 minutes: */5 * * * * /opt/bot/scripts/monitor_restarts.sh

RESTART_COUNT=$(docker inspect webhook --format='{{.RestartCount}}')
RESTART_FILE="/opt/bot/.webhook_restart_count"

if [ ! -f "$RESTART_FILE" ]; then
    echo "$RESTART_COUNT" > "$RESTART_FILE"
    exit 0
fi

PREVIOUS_COUNT=$(cat "$RESTART_FILE")

if [ "$RESTART_COUNT" -gt "$PREVIOUS_COUNT" ]; then
    # Container restarted - send alert
    RESTARTS=$((RESTART_COUNT - PREVIOUS_COUNT))
    docker exec webhook python3 -c "
from Shared_Utils.alert_system import AlertSystem
from Shared_Utils.logging_manager import LoggerManager
alert = AlertSystem(LoggerManager({'log_level': 20}))
alert.callhome(
    subject='Webhook Container Restarted',
    message='⚠️ Webhook container restarted $RESTARTS time(s). Check logs for errors.',
    mode='email'
)
"
    echo "$RESTART_COUNT" > "$RESTART_FILE"
fi
```

**Benefits**:
- Immediate notification of crash-loops
- Helps identify intermittent issues
- No dependency on container staying alive

#### 3.3 Linkage Rate Monitoring

**Purpose**: Alert when linkage rate drops to 0%

**Implementation**: Add to existing report (`botreport/aws_daily_report.py`):
```python
def check_linkage_health(self):
    """Alert if linkage rate is critically low"""
    stats = self.compute_strategy_linkage_stats(hours=6)
    linkage_pct = stats.get('linkage_pct', 0)

    if linkage_pct == 0 and stats.get('total_trades', 0) > 5:
        # 0% linkage with >5 trades is abnormal
        self.alert.callhome(
            subject="🚨 Critical: 0% Strategy Linkage Rate",
            message=f"Zero linkage detected over {stats['total_trades']} trades in past 6h. "
                    f"Webhook HTTP server may not be receiving metadata.",
            mode="email"
        )
```

**Benefits**:
- Detects linkage failures quickly
- Can trigger investigation before issue persists
- Automated monitoring of core functionality

---

### Layer 4: Debug Logging (Permanent)

#### 4.1 Keep HTTP Server Debug Logs

**Current**: Debug logs added temporarily (commit 0b24d32)
**Recommendation**: **Keep them permanently** (convert from 🔧 to INFO level)

**Rationale**:
- Minimal performance impact
- Critical for troubleshooting startup issues
- Helps confirm server started in production logs
- Small log volume (7 lines per startup)

**Implementation**:
```python
# Change emoji from 🔧 to ✅ and keep as INFO level
webhook_logger.info("✅ HTTP server startup: calling site.start()")
await site.start()
webhook_logger.info("✅ HTTP server startup: complete", extra={'port': config.webhook_port})
```

#### 4.2 Log Rotation for Healthcheck Logs

**Purpose**: Prevent `/app/logs/healthcheck.log` from growing indefinitely

**Implementation** (add to `docker/entrypoint/entrypoint.bot.sh`):
```bash
# Rotate healthcheck log if >10MB
if [ -f /app/logs/healthcheck.log ] && [ $(stat -f%z /app/logs/healthcheck.log 2>/dev/null || stat -c%s /app/logs/healthcheck.log) -gt 10485760 ]; then
    mv /app/logs/healthcheck.log /app/logs/healthcheck.log.old
    gzip /app/logs/healthcheck.log.old &
fi
```

---

### Layer 5: Deployment Best Practices

#### 5.1 Pre-Deployment Validation

**Checklist before deploying**:
```bash
# 1. Verify Docker build succeeds
docker compose -f docker-compose.aws.yml build webhook

# 2. Test locally first (if possible)
docker compose up webhook
# Wait 30s, then: curl http://localhost:5003/health

# 3. Check for syntax errors
docker run --rm bottrader-aws-webhook python -m py_compile main.py

# 4. Review recent commits for risky changes
git log --oneline -5
```

#### 5.2 Staged Deployment

**Process**:
1. Deploy to dev/staging environment first
2. Monitor for 1 hour
3. Check logs for HTTP_SERVER_DEBUG messages
4. Verify healthcheck passing
5. Only then deploy to production

#### 5.3 Post-Deployment Verification

**Immediate checks** (within 5 minutes of deployment):
```bash
# 1. Check container status
docker ps | grep webhook
# Should show "Up X minutes (healthy)"

# 2. Check startup logs
docker logs webhook 2>&1 | grep "HTTP server started successfully"

# 3. Test health endpoint
curl http://localhost:5003/health
# Should return: {"status": "ok", ...}

# 4. Check for errors
docker logs webhook 2>&1 | grep -i error | tail -20

# 5. Verify restart count is 0
docker inspect webhook --format='{{.RestartCount}}'
# Should be: 0
```

---

## Implementation Priority

### Immediate (Deploy Next)
1. **Enhanced healthcheck** with start_period=60s
2. **Startup success notification** via AlertSystem
3. **Keep HTTP server debug logs** (make permanent)

### Short-term (Next Sprint)
1. **HTTP server readiness check** with verification
2. **Startup timeout protection** (120s max)
3. **Healthcheck failure logging**

### Medium-term (Within Month)
1. **Container restart monitoring** cron job
2. **Linkage rate alerting** in reports
3. **Deployment validation checklist** in runbook

---

## Testing the Prevention Measures

### Simulated Failure Test

**Purpose**: Verify detection mechanisms work

**Scenario 1: Blocked Port**
```bash
# Bind port 5003 externally before starting webhook
docker run -d -p 5003:5003 nginx:alpine
docker compose up webhook
# Expected: Startup should fail with clear error about port in use
```

**Scenario 2: Slow Startup**
```bash
# Add artificial delay to init_webhook()
await asyncio.sleep(65)
# Expected: Healthcheck should wait (start_period), then pass once server ready
```

**Scenario 3: Server Hangs**
```bash
# Comment out site.start() line
# Expected: verify_http_server_ready() should fail, container should error
```

---

## Monitoring Dashboard (Future Enhancement)

**Grafana Metrics** to track:
- Webhook container uptime
- Restart count over time
- Healthcheck failure rate
- HTTP /health endpoint response time
- Linkage rate (% of trades with metadata)
- Time between deployments and first successful trade

---

## Runbook: HTTP Server Startup Failure

**Symptoms**:
- Container showing as "running" but health check failing
- Curl to port 5003 fails with "connection refused"
- No "HTTP server started successfully" in logs
- 0% linkage rate in reports

**Diagnosis**:
```bash
# 1. Check container health
docker inspect webhook --format='{{.State.Health.Status}}'

# 2. Check if HTTP server started
docker logs webhook 2>&1 | grep "HTTP server started"

# 3. Check for startup errors
docker logs webhook 2>&1 | grep -A10 "HTTP_SERVER_DEBUG" | tail -20

# 4. Check restart count
docker inspect webhook --format='{{.RestartCount}}'

# 5. Check port binding
docker exec webhook ss -tlnp | grep 5003
```

**Resolution**:
```bash
# Step 1: Rebuild container (often fixes the issue)
cd /opt/bot
docker compose -f docker-compose.aws.yml build webhook
docker compose -f docker-compose.aws.yml up -d webhook

# Step 2: Wait 60s for startup
sleep 60

# Step 3: Verify health
curl http://localhost:5003/health

# Step 4: If still failing, check logs for specific error
docker logs webhook 2>&1 | tail -100

# Step 5: Last resort - restart both sighook and webhook
docker compose -f docker-compose.aws.yml restart sighook webhook
```

---

## Success Metrics

**Indicators of successful prevention**:
- ✅ Zero unexpected container restarts in 30 days
- ✅ 100% of deployments result in healthy container within 60s
- ✅ Startup success email received within 2 minutes of deployment
- ✅ Linkage rate >90% consistently
- ✅ No gaps in container logs >5 minutes

---

**Last Updated**: December 30, 2025
**Status**: Prevention measures documented, awaiting implementation
**Owner**: DevOps / Development Team
