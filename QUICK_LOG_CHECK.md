# Quick Log Check Commands

## Essential Commands (Run on Server at /opt/bot)

### 1. Quick Health Check (30 seconds)
```bash
cd /opt/bot

# Show log files and sizes
ls -lh logs/*.log

# Get comprehensive summary
python analyze_logs.py --dir logs
```

### 2. Check for Problems
```bash
# Any errors?
python analyze_logs.py --dir logs --level ERROR

# Any warnings?
python analyze_logs.py --dir logs --level WARNING
```

### 3. Verify Trading Activity
```bash
# Show all trades
python analyze_logs.py --dir logs --trading-only

# Last hour of activity
python analyze_logs.py --dir logs --last 1h
```

### 4. Watch Real-Time Activity
```bash
# Follow webhook logs
tail -f logs/webhook.log

# Follow with JSON formatting (if jq installed)
tail -f logs/webhook.log | jq '.'

# Stop watching: Ctrl+C
```

## One-Line Checks

```bash
# Count total log entries
wc -l logs/*.log

# Count errors across all logs
grep -c '"level":"ERROR"' logs/*.log

# Count trading events
grep -c '"level":"ORDER_SENT"' logs/*.log

# Show last 10 log entries from webhook
tail -10 logs/webhook.log

# Find specific symbol (e.g., BTC-USD)
grep 'BTC-USD' logs/webhook.log | tail -5
```

## Copy Analysis Tool to Server

If you haven't already copied the analysis script to your server:

```bash
# From your local machine:
scp analyze_logs.py your-server:/opt/bot/
scp LOG_EVALUATION_GUIDE.md your-server:/opt/bot/

# Then on server:
chmod +x /opt/bot/analyze_logs.py
```

## Expected Results

**Healthy System:**
- ✅ Multiple .log files in logs/ directory
- ✅ Recent timestamps in logs (within minutes)
- ✅ No (or very few) ERROR entries
- ✅ ORDER_SENT, BUY, SELL levels present if trading occurred
- ✅ Log files under 50MB each (rotation working)

**Issues to Investigate:**
- ❌ ERROR level entries → Check error details
- ⚠️  Many WARNING entries → Review warnings
- ❌ No recent timestamps → Program may not be running
- ❌ Missing trading levels → Check if trades expected
- ❌ Log files over 50MB → Rotation not working

## Quick Troubleshooting

```bash
# Is program running?
ps aux | grep python

# Check last 20 entries
tail -20 logs/webhook.log

# Get help with analysis tool
python analyze_logs.py --help
```

---
See **LOG_EVALUATION_GUIDE.md** for comprehensive documentation.
