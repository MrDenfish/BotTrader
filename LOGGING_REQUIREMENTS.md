# Logging System Requirements - Phase 2 Session 4

**Project:** BotTrader
**Task:** Replace print statements with structured logging system
**Context:** Completed validation system (Phase 2 Session 3), now improving observability

---

## Current State

### Problems
- Using `print()` statements throughout codebase
- No timestamps or log levels
- Hard to debug production issues
- Can't parse or search logs effectively
- No performance tracking

### Example Current Code
```python
# In strategies/signal_manager.py
print(f"[INFO] Processing signal for {symbol}")
print(f"[ERROR] Database error: {e}")

# In botreport/aws_daily_report.py
print(f"Loaded {len(rows)} rows from database")

# In data_access/data_access.py
print(f"Connecting to database at {host}:{port}")
```

---

## Requirements

### 1. Structured Logging (JSON Format)

**Output Format:**
```json
{
  "timestamp": "2025-01-06T15:30:45.123Z",
  "level": "INFO",
  "component": "signal_manager",
  "event": "signal_processed",
  "symbol": "BTC-USD",
  "action": "buy",
  "score": 6.2,
  "duration_ms": 145
}
```

**Benefits:**
- Parseable by log aggregators
- Searchable by any field
- Machine-readable for metrics

### 2. Context Injection

**Automatic Context:**
- `component` - Which module/file
- `timestamp` - ISO 8601 format
- `level` - DEBUG/INFO/WARNING/ERROR/CRITICAL
- `environment` - Desktop vs Docker

**Optional Context:**
- `trade_id` - For trade-related logs
- `symbol` - For market data logs
- `strategy` - For strategy logs
- `user_action` - For manual operations

### 3. Performance Tracking

**Decorator for timing:**
```python
@log_performance
def execute_trade(symbol, side, size):
    # Automatically logs duration
    pass

# Output:
# {"event": "execute_trade_complete", "duration_ms": 145, ...}
```

### 4. Log Levels Per Module

**Configuration:**
```python
# Config/logging_config.py
LOGGING_CONFIG = {
    "version": 1,
    "loggers": {
        "signal_manager": {"level": "DEBUG"},  # Verbose in dev
        "data_access": {"level": "INFO"},      # Less verbose
        "botreport": {"level": "INFO"},
    }
}
```

**Environment-based:**
- Desktop (dev): DEBUG for most modules
- Docker (prod): INFO or WARNING only

### 5. Log Rotation

**Requirements:**
- Max file size: 50 MB
- Keep last 5 files
- Compress old logs
- Separate files per module (optional)

**File Structure:**
```
logs/
├── signal_manager.log
├── signal_manager.log.1.gz
├── data_access.log
├── botreport.log
└── app.log (everything)
```

---

## Proposed Architecture

### File Structure
```
Config/
├── logging_config.py      # Centralized logging setup
└── formatters.py          # JSON/text formatters

Shared_Utils/  (or Utils/)
├── logger.py              # Logger factory/wrapper
└── performance.py         # Performance decorators

# Update existing files:
strategies/signal_manager.py
botreport/aws_daily_report.py
data_access/data_access.py
# (and others as needed)
```

### Core Components

#### 1. Config/logging_config.py
```python
"""
Centralized logging configuration.
Handles JSON formatting, file rotation, log levels.
"""
import os
import logging
import logging.handlers
from pathlib import Path

def setup_logging():
    """Configure logging for entire application."""
    # Environment-based settings
    # File handlers with rotation
    # JSON formatter
    # Return configured logger
    pass
```

#### 2. Shared_Utils/logger.py
```python
"""
Logger wrapper with context injection.
"""
from contextvars import ContextVar

# Context storage
current_trade_id = ContextVar('trade_id', default=None)
current_symbol = ContextVar('symbol', default=None)

class ContextLogger:
    """Logger that auto-injects context."""
    
    def info(self, event, **kwargs):
        # Add context variables to kwargs
        # Log with structured format
        pass
    
    def error(self, event, exc_info=None, **kwargs):
        # Log errors with stack traces
        pass

def get_logger(component: str) -> ContextLogger:
    """Get logger for a component."""
    pass
```

#### 3. Shared_Utils/performance.py
```python
"""
Performance tracking decorators.
"""
import time
import functools

def log_performance(logger=None, level="INFO"):
    """Decorator to log function execution time."""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            start = time.perf_counter()
            try:
                result = func(*args, **kwargs)
                duration_ms = (time.perf_counter() - start) * 1000
                # Log success with duration
                return result
            except Exception as e:
                duration_ms = (time.perf_counter() - start) * 1000
                # Log failure with duration
                raise
        return wrapper
    return decorator
```

---

## Implementation Phases

### Phase 1: Foundation (30 min)
1. Create Config/logging_config.py
2. Create Shared_Utils/logger.py
3. Create Shared_Utils/performance.py
4. Test basic logging works

### Phase 2: Update Core Modules (30 min)
5. Update strategies/signal_manager.py
6. Update data_access/data_access.py
7. Update botreport/aws_daily_report.py
8. Test in both environments

### Phase 3: Systematic Replacement (30 min)
9. Find all print() statements
10. Replace with structured logging
11. Add context where appropriate
12. Test thoroughly

### Phase 4: Polish (15 min)
13. Add performance decorators
14. Configure log levels
15. Test log rotation
16. Update documentation

---

## Key Design Decisions

### 1. JSON vs Plain Text
**Decision:** JSON for production, optional text for development
**Reason:** JSON is parseable, text is readable

### 2. Single File vs Multiple Files
**Decision:** Single app.log + optional per-module files
**Reason:** Easier to search everything, but can separate if needed

### 3. Log Level Defaults
**Decision:** INFO for production, DEBUG for development
**Reason:** Balance between verbosity and disk usage

### 4. Context Storage
**Decision:** Use contextvars for thread-safe context
**Reason:** Async-compatible, clean API

---

## Integration Points

### With Existing Code

#### Current Config System
```python
# Use existing Config/config_manager.py
from Config.config_manager import CentralConfig
config = CentralConfig()

# Get log directory
log_dir = config.log_dir or "/app/logs"
```

#### Current Error Handling
```python
# Integrate with Config/exceptions.py
from Config.exceptions import ConfigError

logger = get_logger("config")
try:
    config = load_config()
except ConfigError as e:
    logger.error("config_load_failed", error=str(e), exc_info=True)
```

#### Current Validation
```python
# Log validation results
from Config.validators import validate_all_config

logger = get_logger("startup")
result = validate_all_config(raise_on_error=False)
if not result.is_valid:
    logger.warning("config_validation_failed", 
                   errors=result.errors)
```

---

## Testing Strategy

### Manual Testing
```python
# Test script: test_logging.py
from Shared_Utils.logger import get_logger

logger = get_logger("test")

# Test levels
logger.debug("debug_message", detail="extra info")
logger.info("info_message", status="ok")
logger.warning("warning_message", threshold=0.9)
logger.error("error_message", error="something broke")

# Test context
with logger.context(trade_id="T123", symbol="BTC-USD"):
    logger.info("trade_executed", side="buy", size=0.01)
    # Should include trade_id and symbol automatically
```

### Verify Output
- Check logs/ directory created
- Verify JSON format
- Check log rotation works
- Confirm context injection
- Test performance decorator

---

## Migration Pattern

### Before (print)
```python
print(f"[INFO] Processing {symbol} - Buy score: {buy_score:.2f}")
```

### After (structured)
```python
logger.info("signal_processed",
    symbol=symbol,
    action="buy",
    score=buy_score,
    threshold=SCORE_BUY_TARGET)
```

### Benefits of New Pattern
- Searchable: `grep 'signal_processed' logs/app.log`
- Parseable: `jq '.score' logs/app.log`
- Filterable: `jq 'select(.symbol=="BTC-USD")' logs/app.log`
- Context: Automatically includes timestamp, level, component

---

## Environment Variables

### New Variables
```bash
# .env_tradebot and .env_runtime
LOG_LEVEL=INFO                    # DEBUG, INFO, WARNING, ERROR
LOG_FORMAT=json                   # json or text
LOG_DIR=/app/logs                 # Where to write logs
LOG_MAX_BYTES=52428800           # 50 MB
LOG_BACKUP_COUNT=5               # Keep 5 old files
LOG_TO_CONSOLE=true              # Also print to stdout
```

---

## Success Criteria

After implementation:
- [ ] No more raw print() statements (except startup)
- [ ] All logs have timestamps and levels
- [ ] JSON format in production
- [ ] Context automatically injected
- [ ] Performance tracked for key operations
- [ ] Logs rotate automatically
- [ ] Can search/parse logs easily
- [ ] Works in both Desktop and Docker environments

---

## Reference Files

### Review These Files First
- `strategies/signal_manager.py` - Heavy logging needed
- `botreport/aws_daily_report.py` - Report generation logs
- `data_access/data_access.py` - Database operation logs
- `Config/config_manager.py` - Config loading logs

### Existing Patterns
- Multi-environment config (Session 2)
- Custom exceptions (Session 3)
- Health checks (Session 3)

---

## Notes

### Don't Break
- Existing functionality
- Config system
- Validation system
- Report generation

### Nice to Have (Optional)
- Log aggregation (Grafana/Loki integration)
- Log alerts (on ERROR level)
- Metrics from logs
- Performance dashboards

---

## Questions to Resolve

1. **Single vs multiple log files?**
   - Recommendation: Single app.log for simplicity

2. **JSON always or env-based?**
   - Recommendation: JSON in Docker, text option for Desktop

3. **Performance decorator everywhere?**
   - Recommendation: Only on key operations (trades, DB queries)

4. **Context: explicit vs implicit?**
   - Recommendation: Hybrid (contextvars + explicit kwargs)

---

**Ready to implement!** 

Start with Phase 1 (foundation), then we can iterate on the rest.
