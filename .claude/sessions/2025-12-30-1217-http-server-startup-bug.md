# HTTP Server Startup Bug Investigation

**Session Start**: December 30, 2025 12:17 PM PST

---

## Session Overview

Investigating critical bug where webhook container's HTTP server on port 5003 fails to start, preventing sighook from delivering order metadata and causing 0% trade-strategy linkage rate.

**Context from Previous Session**:
- ROC momentum score bug fix deployed and **WORKING** (commit `842f107`)
- Sighook successfully generates valid scores and snapshot_ids
- But webhook HTTP server not starting → sighook requests fail → 0% linkage
- Debug logging added (commit `0b24d32`) to identify exact failure point

---

## Goals

1. **Deploy debug logging** to AWS webhook container
2. **Collect diagnostic data** from container startup logs
3. **Identify exact failure point** where HTTP server startup fails
4. **Root cause analysis** - determine why `site.start()` isn't being called or failing
5. **Implement fix** to restore HTTP server functionality
6. **Verify fix** - achieve healthy container status and >0% linkage rate

---

## Progress

### Deployment Status
- [ ] Debug logging code deployed to AWS
- [ ] Webhook container rebuilt with new logging
- [ ] Container restarted and monitoring startup

### Investigation Findings
- [ ] Debug logs collected
- [ ] Failure point identified
- [ ] Root cause determined

### Fix Implementation
- [ ] Fix implemented
- [ ] Fix tested locally
- [ ] Fix deployed to AWS
- [ ] HTTP server verified running (port 5003 healthy)
- [ ] Linkage system verified working (>0% rate)

---

## Notes

**Key Files**:
- `/Users/Manny/Python_Projects/BotTrader/docs/HTTP_SERVER_STARTUP_BUG.md` - Complete investigation guide
- `main.py` - Debug logging added to `init_webhook()` and `run_webhook()`
- `docker-compose.aws.yml` - Webhook service configuration

**Debug Log Markers**:
- `🔧 [HTTP_SERVER_DEBUG] RUN_MODE=webhook detected`
- `🔧 [HTTP_SERVER_DEBUG] init_webhook() called`
- `🔧 [HTTP_SERVER_DEBUG] About to call listener.create_app()`
- `🔧 [HTTP_SERVER_DEBUG] create_app() returned`
- `🔧 [HTTP_SERVER_DEBUG] Creating TCPSite on 0.0.0.0:5003`
- `🔧 [HTTP_SERVER_DEBUG] Calling site.start()`
- `✅ TradeBot HTTP server started successfully`

**Expected Outcome**: Last debug message will show where execution stops, revealing the failure point.

---

## Session Timeline

**12:17 PM** - Session started

**12:30 PM** - Debug logging deployed and HTTP server started successfully
- Rebuilt webhook container with debug logging (commit 0b24d32)
- HTTP server now starting successfully on port 5003
- Container status: **healthy** ✅
- Health endpoint responding: `{"status": "ok"}`
- Sighook can reach webhook server ✅

**12:35 PM** - Sighook rebuilt with ROC score fix
- Deployed commit 842f107 (ROC score fix)
- Sighook running and analyzing symbols
- Detecting sell signals for ETH, ZEC, AVNT

**Root Cause Analysis**:
The previous webhook container (from 03:12 deployment) likely crashed during startup, causing Docker to restart it. The restart cleared early logs, hiding the crash. The container was stuck in a crash-loop with only recent logs visible (19:03 onwards), explaining the 16-hour gap in logs.

Rebuilding the container resolved the issue - HTTP server now starts cleanly.

**Next**: Monitor for webhook metadata delivery and linkage creation
