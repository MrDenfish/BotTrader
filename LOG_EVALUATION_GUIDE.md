# BotTrader Log Evaluation Guide

## Overview

Your structured logging system is now deployed and running. This guide shows how to evaluate and analyze the production logs at `/opt/bot/logs/`.

## Quick Start

### 1. Check What Logs Exist

```bash
cd /opt/bot
ls -lh logs/

# See all log files with sizes
find logs/ -name "*.log" -type f -exec ls -lh {} \;
```

Expected log files:
- `logs/main.log` - Main application startup and lifecycle
- `logs/webhook.log` - Webhook listener and order processing
- `logs/order_manager.log` - Order management
- `logs/daily_report.log` - Daily reporting
- And others based on your components

### 2. View Recent Activity (Quick Check)

```bash
# Last 20 lines from webhook log (most active)
tail -20 logs/webhook.log

# Last 50 lines from all logs
tail -50 logs/*.log

# Follow logs in real-time
tail -f logs/webhook.log
```

### 3. Use the Analysis Tool (Recommended)

```bash
# Comprehensive analysis summary
python analyze_logs.py --dir logs

# Show only errors
python analyze_logs.py --dir logs --level ERROR

# Show only trading activity (BUY, SELL, ORDER_SENT)
python analyze_logs.py --dir logs --trading-only

# Activity in last hour
python analyze_logs.py --dir logs --last 1h

# Activity in last 24 hours
python analyze_logs.py --dir logs --last 24h

# Filter by symbol
python analyze_logs.py --dir logs --symbol BTC-USD

# Show more entries (default is 50)
python analyze_logs.py --dir logs --trading-only --limit 100

# Export filtered results
python analyze_logs.py --dir logs --level ERROR --export errors.json
```

## Manual Log Analysis (Without Tool)

### Using `jq` for JSON Parsing

If `jq` is installed, you can parse JSON logs directly:

```bash
# Pretty-print last entry
tail -1 logs/webhook.log | jq '.'

# Show only errors
cat logs/webhook.log | jq 'select(.level == "ERROR")'

# Show trading activity
cat logs/webhook.log | jq 'select(.level | IN("BUY", "SELL", "ORDER_SENT"))'

# Extract specific fields
cat logs/webhook.log | jq '{time: .timestamp, level: .level, message: .message}'

# Count errors
cat logs/webhook.log | jq 'select(.level == "ERROR")' | wc -l

# Show errors with context
cat logs/webhook.log | jq 'select(.level == "ERROR") | {time: .timestamp, message: .message, context: .context}'

# Filter by symbol
cat logs/webhook.log | jq 'select(.context.symbol == "BTC-USD")'

# Show ORDER_SENT with details
cat logs/webhook.log | jq 'select(.level == "ORDER_SENT") | {time: .timestamp, message: .message, context: .context, extra: .extra}'
```

### Using `grep` for Quick Searches

```bash
# Find all errors
grep '"level":"ERROR"' logs/*.log

# Find all ORDER_SENT entries
grep '"level":"ORDER_SENT"' logs/*.log

# Find specific symbol mentions
grep '"symbol":"BTC-USD"' logs/webhook.log

# Find ROC trading alerts
grep 'ROC' logs/*.log

# Find exceptions
grep 'exc_info' logs/*.log

# Count errors per file
for f in logs/*.log; do
    echo "$f: $(grep -c '"level":"ERROR"' $f 2>/dev/null || echo 0)"
done
```

### Using Python One-Liners

```bash
# Count entries by level
python3 -c "
import json, sys
from collections import Counter
levels = Counter()
for line in sys.stdin:
    try:
        entry = json.loads(line.strip())
        levels[entry.get('level', 'UNKNOWN')] += 1
    except: pass
for level, count in levels.most_common():
    print(f'{level:20} {count:6}')
" < logs/webhook.log

# Show all unique symbols traded
python3 -c "
import json, sys
symbols = set()
for line in sys.stdin:
    try:
        entry = json.loads(line.strip())
        ctx = entry.get('context', {})
        if 'symbol' in ctx:
            symbols.add(ctx['symbol'])
    except: pass
for s in sorted(symbols):
    print(s)
" < logs/webhook.log
```

## What to Look For

### 1. Verify Logging is Working

```bash
# Should see recent timestamps
tail -5 logs/webhook.log | python3 -m json.tool

# Check if all components are logging
python analyze_logs.py --dir logs
# Look at "Top Loggers" and "Top Components" sections
```

### 2. Check for Errors

```bash
# Analyze all errors
python analyze_logs.py --dir logs --level ERROR

# Or manually:
grep '"level":"ERROR"' logs/*.log | tail -10
```

Common errors to investigate:
- Database connection errors
- API/Exchange communication errors
- Validation errors
- Order placement failures

### 3. Verify Trading Activity

```bash
# Show all trading events
python analyze_logs.py --dir logs --trading-only

# Count each type
python3 -c "
import json, sys
from collections import Counter
trades = Counter()
for line in sys.stdin:
    try:
        entry = json.loads(line.strip())
        level = entry.get('level', '')
        if level in ['BUY', 'SELL', 'ORDER_SENT', 'TAKE_PROFIT', 'STOP_LOSS']:
            trades[level] += 1
    except: pass
print('Trading Activity:')
for level, count in trades.most_common():
    print(f'  {level:15} {count:4}')
" < logs/webhook.log
```

### 4. Check Warnings

```bash
# Warnings might indicate issues
python analyze_logs.py --dir logs --level WARNING

# Common warnings to review:
# - FILLED orders with amount=0
# - Configuration validation warnings
# - Subscription issues
```

### 5. Performance Analysis

```bash
# Find slow operations (duration_ms in extra)
cat logs/webhook.log | jq 'select(.extra.duration_ms > 1000) | {time: .timestamp, func: .function, duration: .extra.duration_ms, message: .message}'

# Average duration for specific function
cat logs/webhook.log | jq -r 'select(.message | contains("completed")) | .extra.duration_ms' | awk '{sum+=$1; n++} END {if(n>0) print "Average:", sum/n, "ms"}'
```

### 6. Context Verification

Check that context injection is working:

```bash
# Verify trade_id is present in order processing
cat logs/webhook.log | jq 'select(.context.trade_id) | {trade_id: .context.trade_id, symbol: .context.symbol, message: .message}' | head -10

# Verify component tracking
cat logs/*.log | jq -r '.context.component' | sort | uniq -c | sort -rn
```

## Example Analysis Session

Here's a complete example of evaluating your logs after several hours:

```bash
# 1. Navigate to bot directory
cd /opt/bot

# 2. Quick overview
echo "=== Log Files ==="
ls -lh logs/*.log

# 3. Run comprehensive analysis
echo -e "\n=== Full Analysis ==="
python analyze_logs.py --dir logs

# 4. Check for any errors
echo -e "\n=== Errors (if any) ==="
python analyze_logs.py --dir logs --level ERROR --limit 10

# 5. Review warnings
echo -e "\n=== Warnings (if any) ==="
python analyze_logs.py --dir logs --level WARNING --limit 10

# 6. Trading activity
echo -e "\n=== Trading Activity ==="
python analyze_logs.py --dir logs --trading-only --limit 20

# 7. Recent activity (last hour)
echo -e "\n=== Last Hour ==="
python analyze_logs.py --dir logs --last 1h --limit 30
```

## Expected Healthy Output

A healthy log analysis should show:

✅ **No CRITICAL or ERROR entries** (or very few, explained errors)
✅ **Structured JSON format** in all entries
✅ **Context fields present** (trade_id, symbol, component) in relevant logs
✅ **Multiple components active** (webhook, order_manager, etc.)
✅ **Trading levels working** (ORDER_SENT, BUY, SELL visible)
✅ **Recent timestamps** (logs are current)
✅ **Performance metrics** (duration_ms present in some entries)

## Troubleshooting

### Problem: No logs or empty logs

```bash
# Check if program is running
ps aux | grep python

# Check if log directory exists
ls -la logs/

# Check permissions
ls -l logs/

# Check recent activity
tail -f logs/webhook.log
```

### Problem: Logs not in JSON format

```bash
# Check if structured logging is initialized
grep "setup_structured_logging" logs/*.log

# Verify environment detection
python3 -c "from Config.logging_config import get_logging_config; print(get_logging_config().use_json)"
```

### Problem: Missing context fields

```bash
# Check if context is being set
cat logs/webhook.log | jq 'select(.context | length > 0)' | head -5

# Look for examples with full context
cat logs/webhook.log | jq 'select(.context.trade_id and .context.symbol)' | head -3
```

## Next Steps

After evaluating your logs:

1. **If errors found**: Investigate root causes using the detailed error logs
2. **If warnings found**: Review and determine if they're expected or need fixing
3. **If performance issues**: Look at `duration_ms` metrics to identify slow operations
4. **If all looks good**: You can proceed to merge the feature branch!

## Additional Tools

### Install jq (if not available)

```bash
# Ubuntu/Debian
sudo apt-get install jq

# macOS
brew install jq

# CentOS/RHEL
sudo yum install jq
```

### Create log rotation check

```bash
# Check if logs are rotating properly (should stay under 50MB)
ls -lh logs/*.log | awk '$5 ~ /M/ {print $9, $5}'
```

### Monitor logs in real-time with filtering

```bash
# Watch only errors in real-time
tail -f logs/webhook.log | jq 'select(.level == "ERROR")'

# Watch trading activity in real-time
tail -f logs/webhook.log | jq 'select(.level | IN("BUY", "SELL", "ORDER_SENT"))'
```

---

**Status**: Ready for production log evaluation
**Branch**: `claude/structured-logging-foundation-011CUv7LVh354k4hoB15Epoa`
**Date**: 2025-11-08
