# Structured Logging - Phase 1 Implementation Guide

## Overview

Phase 1 establishes the foundation for BotTrader's structured logging system with:
- **JSON format** for production environments
- **Context injection** (trade_id, symbol, component)
- **Performance tracking** with decorators
- **Log rotation** (50MB max, 5 backups)
- **Custom trading log levels** (BUY, SELL, ORDER_SENT, etc.)

## Files Created

### 1. `Config/logging_config.py`
Central logging configuration with:
- `JSONFormatter`: Structured JSON output for production
- `ColoredConsoleFormatter`: Colored output for development
- `LoggingConfig`: Configuration manager with auto-environment detection
- `RotatingFileHandler`: 50MB max size with 5 backups

### 2. `Shared_Utils/logger.py`
Enhanced logger interface with:
- `StructuredLogger`: Context-aware logger adapter
- `get_logger()`: Factory function for creating loggers
- `log_context()`: Context manager for temporary context
- `log_performance()`: Decorator for tracking execution time
- Custom trading methods: `.buy()`, `.sell()`, `.order_sent()`, etc.

### 3. `test_structured_logging.py`
Comprehensive test suite validating all features

## Quick Start

### Basic Usage

```python
from Shared_Utils.logger import get_logger

logger = get_logger('webhook')
logger.info('Order received')
logger.error('Failed to process order', exc_info=True)
```

### With Context

```python
from Shared_Utils.logger import get_logger

logger = get_logger('webhook', context={'component': 'order_processor'})
logger.info('Processing order', extra={'trade_id': '12345', 'symbol': 'BTC-USD'})
```

### Using Context Manager

```python
from Shared_Utils.logger import get_logger, log_context

logger = get_logger('webhook')

with log_context(trade_id='12345', symbol='BTC-USD'):
    logger.info('Order validated')  # Includes trade_id and symbol
    logger.info('Order sent')       # Includes trade_id and symbol
```

### Custom Trading Log Levels

```python
logger.buy('BTC-USD buy order executed')
logger.sell('ETH-USD sell order executed')
logger.order_sent('Order sent to exchange')
logger.take_profit('Take profit triggered')
logger.stop_loss('Stop loss triggered')
logger.bad_order('Invalid order detected')
logger.insufficient_funds('Insufficient funds')
```

### Performance Tracking

```python
from Shared_Utils.logger import log_performance

@log_performance('webhook', level='INFO')
async def process_order(order):
    # ... order processing logic ...
    return result

# Automatically logs:
# - Function entry
# - Execution time
# - Errors with stack trace
```

## JSON Output Example

```json
{
  "timestamp": "2025-11-08T08:34:38.115318Z",
  "level": "INFO",
  "logger": "webhook",
  "message": "Order processed",
  "module": "order_processor",
  "function": "process_order",
  "line": 145,
  "context": {
    "trade_id": "12345",
    "symbol": "BTC-USD",
    "component": "webhook"
  },
  "extra": {
    "duration_ms": 125.5
  }
}
```

## Configuration

### Development (Colored Console)
```python
from Shared_Utils.logger import setup_structured_logging

config = setup_structured_logging(
    log_dir='logs',
    console_level='DEBUG',
    use_json=False  # Colored console output
)
```

### Production (JSON)
```python
config = setup_structured_logging(
    log_dir='/app/logs',
    console_level='INFO',
    use_json=True  # JSON output
)
```

### Auto-Detection
By default, the system auto-detects the environment:
- **Development**: Colored console output
- **Docker/Production**: JSON output

## Features

### ✅ Implemented in Phase 1

- [x] JSON formatter for production
- [x] Colored console formatter for development
- [x] Context injection (trade_id, symbol, component)
- [x] Thread-local context management
- [x] Performance tracking decorators
- [x] Log rotation (50MB max, 5 backups)
- [x] Custom trading log levels (BUY, SELL, etc.)
- [x] Backward compatibility with existing LoggerManager
- [x] Component-based loggers

### 🔄 Next Phase

- [ ] Update 2-3 example files to use new logger
- [ ] Migrate print() statements
- [ ] Add structured logging to critical paths
- [ ] Integration with existing services

## Testing

Run the test suite:
```bash
python test_structured_logging.py
```

Expected output:
- ✓ All 11 tests pass
- ✓ Log files created in `logs/test/` and `logs/test_json/`
- ✓ JSON formatting verified
- ✓ Context injection working
- ✓ Performance tracking functional

## Migration Notes

### Backward Compatibility

The new system is designed to work alongside the existing `LoggerManager`:

1. **Existing code**: Continue to use `LoggerManager` (no changes required)
2. **New code**: Use `get_logger()` from `Shared_Utils.logger`
3. **Gradual migration**: Update files incrementally

### Custom Log Levels

The existing custom log levels are preserved:
- `BUY` (level 21)
- `SELL` (level 19)
- `ORDER_SENT` (level 23)
- `TAKE_PROFIT` (level 17)
- `TAKE_LOSS` (level 11)
- `STOP_LOSS` (level 15)
- `INSUFFICIENT_FUNDS` (level 13)
- `BAD_ORDER` (level 25)

## Best Practices

1. **Use context injection** for trade-related operations
2. **Use performance decorators** for time-sensitive functions
3. **Log JSON in production** for easy parsing and analysis
4. **Include trade_id and symbol** in all trade-related logs
5. **Use appropriate log levels**:
   - DEBUG: Detailed diagnostic info
   - INFO: General informational messages
   - WARNING: Warning messages
   - ERROR: Error messages
   - CRITICAL: Critical system failures

## Next Steps

**Phase 2**: Update 2-3 example files
- Select candidate files with print() statements
- Replace print() with structured logging
- Add context injection
- Test thoroughly
- Wait for approval before continuing

---

**Status**: Phase 1 Complete ✅
**Branch**: `claude/structured-logging-foundation-011CUv7LVh354k4hoB15Epoa`
**Date**: 2025-11-08
