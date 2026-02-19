# Test Suite

Unit and integration tests for BotTrader components.

## Running Tests

```bash
# Run all tests
pytest tests/

# Run specific test file
pytest tests/test_fifo_engine.py

# Run with coverage
pytest --cov=. tests/

# Run with verbose output
pytest -v tests/
```

## Test Files

- **`test_config.py`** - Configuration validation and environment tests
- **`test_fifo_engine.py`** - FIFO allocation engine logic tests
- **`test_fifo_report.py`** - FIFO reporting and P&L calculation tests
- **`test_structured_logging.py`** - Logging system tests
- **`test_trailing_stop.py`** - Trailing stop loss logic tests

## Adding New Tests

1. Create test file with `test_` prefix
2. Use pytest fixtures for common setup
3. Follow existing test patterns
4. Run tests before committing code

## Test Coverage

Run coverage report to see which code is tested:

```bash
pytest --cov=. --cov-report=html tests/
open htmlcov/index.html
```

---

**Last Updated:** December 15, 2025
