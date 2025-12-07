# Trigger Format Inconsistency Analysis

## Summary
Database contains two trigger formats:
- **JSON format**: `{"trigger": "LIMIT"}` (344 records) - from websocket fills
- **Plain string format**: `"limit"` (39 records) - from sighook/TradingView signals

## Root Cause

### Websocket Fills Handler (SELL orders)
**File**: webhook/websocket_market_manager.py

**Line 302**:
```python
"trigger": {"trigger": order_type},
```

**Line 350**:
```python
trigger = {"trigger": "websocket", "trigger_note": "trigger unknown..."}
```

Websocket creates triggers as **JSON dictionaries** with a nested structure.

### Sighook Trading Strategy (BUY orders)
**File**: sighook (logs show)

Sighook sends webhook payloads like:
```python
'trigger': 'roc_momo'  # Plain string
'trigger': 'score'     # Plain string
```

## Database Evidence
```sql
SELECT order_id, symbol, trigger::text, side, order_time
FROM trade_records
WHERE trigger IS NOT NULL
ORDER BY order_time DESC LIMIT 30;
```

**Pattern observed**:
- **BUY orders**: `"limit"`, `"roc_momo"`, `"score"` (plain strings)
- **SELL orders**: `{"trigger": "LIMIT"}` (JSON format)

Example:
```
877bc5af-... | BONK-USD | {"trigger": "LIMIT"} | buy  | 2025-12-07 06:25:12  ← FROM WEBSOCKET
4fd29bcb-... | PEPE-USD | "limit"              | buy  | 2025-12-07 02:44:09  ← FROM SIGHOOK
13757b0d-... | SEI-USD  | {"trigger": "LIMIT"} | sell | 2025-12-07 02:40:25  ← FROM WEBSOCKET
d6c8611e-... | SEI-USD  | "limit"              | buy  | 2025-12-07 02:22:59  ← FROM SIGHOOK
```

## Impact on Reporting
The Dec 6/7 reports had to handle both formats using:
```python
# botreport/aws_daily_report.py:1936-1945
try:
    if isinstance(t_val, dict):
        trigger_str = t_val.get("trigger", "UNKNOWN")
    elif isinstance(t_val, str):
        try:
            parsed = json.loads(t_val)
            trigger_str = parsed.get("trigger", "UNKNOWN") if isinstance(parsed, dict) else t_val
        except Exception:
            trigger_str = t_val
```

This workaround successfully handles both formats, but the root cause should be fixed for consistency.

## Recommended Fix

### Option 1: Standardize to JSON Format (Recommended)
Change sighook to send triggers in JSON format matching websocket:

**Change in**: sighook/alerts_msgs_webhooks.py or wherever webhook payload is built

**From**:
```python
'trigger': 'roc_momo'
```

**To**:
```python
'trigger': {'trigger': 'roc_momo'}
```

### Option 2: Standardize to Plain String Format
Change websocket_market_manager.py to send plain strings:

**Change**: webhook/websocket_market_manager.py:302, 350

**From**:
```python
"trigger": {"trigger": order_type}
```

**To**:
```python
"trigger": order_type
```

**Pros of Option 1 (JSON)**:
- Websocket code already uses this format extensively
- Allows for additional metadata (trigger_note, etc.)
- Report parsing already handles JSON as primary format
- Less code to change (only sighook webhook payload)

**Pros of Option 2 (Plain String)**:
- Simpler format
- Matches what sighook already sends
- More database-friendly

## Recommendation
**Option 1** is recommended because:
1. Less risky - websocket fills are core functionality
2. JSON format allows extensibility (notes, metadata)
3. Smaller code change surface (only sighook payload building)

## Files to Modify (Option 1)
Search for where sighook builds webhook payloads and wrap trigger value:
```bash
grep -rn "'trigger':" sighook/
```

Then change from:
```python
payload = {
    'trigger': some_trigger_string
}
```

To:
```python
payload = {
    'trigger': {'trigger': some_trigger_string}
}
```

## Verification
After fix, verify all new records use JSON format:
```sql
SELECT trigger::text, COUNT(*)
FROM trade_records
WHERE order_time > NOW() - INTERVAL '1 hour'
GROUP BY trigger::text;
```

Should only see JSON format like `{"trigger": "..."}`, no more plain strings.
