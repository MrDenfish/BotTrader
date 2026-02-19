# Critical Path Test Suite

## Overview

This directory contains **money-critical** tests that validate the financial accuracy and risk management logic of BotTrader. These tests MUST pass before deploying any changes to production.

**Priority**: 🔴 **CRITICAL** - Direct impact on financial outcomes

## What is Tested

### 1. FIFO Allocation (`test_fifo_allocation.py`)
- **Why Critical**: Tax reporting accuracy
- **Tests**: Cost basis calculations, P&L allocation, multi-lot handling
- **Impact**: IRS compliance, accurate tax forms

### 2. Order Validation (`test_order_validation.py`)
- **Why Critical**: Prevents over-leveraging and invalid trades
- **Tests**: Order size limits, balance checks, position limits
- **Impact**: Risk management, capital preservation

### 3. Stop Loss Logic (`test_stop_loss.py`)
- **Why Critical**: Prevents catastrophic losses
- **Tests**: Stop loss triggers, take profit logic, ROC peak tracking
- **Impact**: Loss prevention, profit protection

### 4. P&L Calculation (`test_pnl_calculation.py`)
- **Why Critical**: Financial reporting accuracy
- **Tests**: Realized/unrealized P&L, fee inclusion, tax reporting
- **Impact**: Accurate financial statements, tax compliance

## Quick Start

### Run All Critical Tests

```bash
# From project root
./tests/critical/run_critical_tests.sh

# Or using pytest directly
pytest tests/critical/ -m critical -v
```

### Fast Mode (Skip Coverage)

```bash
./tests/critical/run_critical_tests.sh --fast
```

### Verbose Mode (Detailed Output)

```bash
./tests/critical/run_critical_tests.sh --verbose
```

## Expected Output

### ✅ Success (All Tests Pass)

```
═══════════════════════════════════════════════════════════
  BotTrader Critical Path Test Suite
═══════════════════════════════════════════════════════════

📋 Running money-critical tests...

tests/critical/test_fifo_allocation.py::TestFIFOAllocationLogic::test_simple_fifo_profit PASSED
tests/critical/test_fifo_allocation.py::TestFIFOAllocationLogic::test_simple_fifo_loss PASSED
...
[All tests passing]
...

═══════════════════════════════════════════════════════════
✅ All critical tests PASSED
═══════════════════════════════════════════════════════════

✅ System is ready for deployment
```

### ❌ Failure (Tests Fail)

```
═══════════════════════════════════════════════════════════
❌ CRITICAL TESTS FAILED
═══════════════════════════════════════════════════════════

⚠️  DO NOT DEPLOY TO PRODUCTION
Please fix failing tests before deploying.
```

## Test Requirements

### Prerequisites

```bash
pip install pytest pytest-asyncio pytest-cov
```

### Python Version

- Python 3.10+

### Dependencies

All tests use:
- `Decimal` for financial precision
- `pytest.mark.critical` marker
- Fixtures from `conftest.py`

## Test Structure

```
tests/critical/
├── README.md                    # This file
├── conftest.py                  # Shared fixtures
├── test_fifo_allocation.py      # FIFO/tax tests
├── test_order_validation.py     # Order validation tests
├── test_stop_loss.py            # Stop loss/exit tests
├── test_pnl_calculation.py      # P&L calculation tests
└── run_critical_tests.sh        # Test runner script
```

## Test Coverage

Each test file contains multiple test classes:

### `test_fifo_allocation.py` (15+ tests)
- `TestFIFOAllocationLogic` - Core FIFO algorithm
- `TestFIFOEdgeCases` - Edge cases and errors
- `TestFIFOVersionConsistency` - Version isolation
- `TestFIFOPerformance` - Performance with many lots

### `test_order_validation.py` (20+ tests)
- `TestOrderSizeValidation` - Size and notional checks
- `TestPositionSizeValidation` - Position limits
- `TestBalanceValidation` - Available balance checks
- `TestOrderPriceValidation` - Price validation
- `TestOrderSymbolValidation` - Symbol validation
- `TestOrderSideValidation` - Buy/sell side validation
- `TestOrderValidationComprehensive` - Full validation flow

### `test_stop_loss.py` (20+ tests)
- `TestStopLossTriggerLogic` - Stop loss triggers
- `TestTakeProfitLogic` - Take profit triggers
- `TestROCPeakTrackingExit` - ROC peak tracking
- `TestExitConditionPriority` - Exit priority logic
- `TestExitExecutionValidation` - Exit order validation
- `TestTimeBasedExitConditions` - Time-based exits

### `test_pnl_calculation.py` (25+ tests)
- `TestUnrealizedPnL` - Open position P&L
- `TestRealizedPnL` - Closed position P&L
- `TestCumulativePnL` - Multi-trade P&L tracking
- `TestWinRateMetrics` - Win rate and statistics
- `TestPnLPrecision` - Decimal precision
- `TestTaxReportingPnL` - Tax reporting calculations

## Running Individual Test Files

```bash
# Test FIFO allocation only
pytest tests/critical/test_fifo_allocation.py -v

# Test order validation only
pytest tests/critical/test_order_validation.py -v

# Test stop loss logic only
pytest tests/critical/test_stop_loss.py -v

# Test P&L calculations only
pytest tests/critical/test_pnl_calculation.py -v
```

## Running Specific Tests

```bash
# Run a specific test class
pytest tests/critical/test_fifo_allocation.py::TestFIFOAllocationLogic -v

# Run a specific test method
pytest tests/critical/test_fifo_allocation.py::TestFIFOAllocationLogic::test_simple_fifo_profit -v
```

## Test Fixtures

Available fixtures from `conftest.py`:

- `sample_prices` - Sample price data for BTC, ETH, SOL
- `sample_trade_buy` - Sample buy trade
- `sample_trade_sell` - Sample sell trade
- `sample_position` - Sample open position
- `mock_config` - Mock configuration with thresholds
- `mock_logger` - Mock logger
- `mock_database` - Mock database

### Test Data Generators

- `generate_ohlcv_candles(symbol, count, start_price)` - Generate OHLCV data
- `generate_trade_sequence(symbol, pairs)` - Generate buy/sell pairs

## Continuous Integration

### Pre-Deployment Checklist

Before deploying to production:

1. ✅ All critical tests pass
2. ✅ No warnings or errors in test output
3. ✅ Coverage report reviewed (if not using `--fast`)
4. ✅ Manual smoke test on staging

### Automated Testing (Future)

```yaml
# .github/workflows/critical-tests.yml
name: Critical Path Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - name: Run Critical Tests
        run: ./tests/critical/run_critical_tests.sh
```

## Troubleshooting

### Tests Fail to Run

```bash
# Check pytest installation
pytest --version

# Install missing dependencies
pip install -r requirements.txt
pip install pytest pytest-asyncio pytest-cov
```

### Import Errors

```bash
# Ensure project root is in PYTHONPATH
export PYTHONPATH="${PYTHONPATH}:/Users/Manny/Python_Projects/BotTrader"
```

### Fixture Not Found

- Ensure `conftest.py` is in the same directory
- Check fixture name spelling
- Verify pytest is discovering `conftest.py`

## Best Practices

### Writing New Critical Tests

1. **Use `@pytest.mark.critical` marker**
   ```python
   @pytest.mark.critical
   def test_new_critical_feature(self):
       # Test code
   ```

2. **Follow Arrange-Act-Assert pattern**
   ```python
   # Arrange: Set up test data
   entry_price = Decimal("40000.00")

   # Act: Perform action
   result = calculate_pnl(entry_price, current_price)

   # Assert: Verify outcome
   assert result == expected_value
   ```

3. **Use Decimal for financial calculations**
   ```python
   # ✅ Correct
   price = Decimal("40000.00")

   # ❌ Wrong (floating point errors)
   price = 40000.00
   ```

4. **Add clear docstrings**
   ```python
   def test_feature(self):
       """
       Test: Clear description

       Given: Preconditions
       And: Additional context
       Then: Expected outcome
       """
   ```

## When to Run These Tests

### Always Run Before:
- ✅ Deploying to production
- ✅ Merging to main branch
- ✅ Releasing new version
- ✅ Modifying financial calculations
- ✅ Changing order validation logic
- ✅ Updating stop loss thresholds
- ✅ Altering P&L calculations

### Run After:
- ✅ Writing new financial features
- ✅ Refactoring existing code
- ✅ Updating dependencies
- ✅ Modifying database schemas
- ✅ Changing configuration values

## Maintenance

### Adding New Tests

1. Create test in appropriate file
2. Use `@pytest.mark.critical` marker
3. Update this README if adding new test class
4. Ensure test uses fixtures from `conftest.py`

### Updating Fixtures

- Edit `conftest.py`
- Update all affected tests
- Run full test suite to verify

### Test Performance

- Critical tests should run in < 5 seconds
- If tests slow down, investigate and optimize
- Use `--fast` mode for quick feedback

## Support

### Documentation

- Full testing guide: `docs/active/guides/COMPREHENSIVE_TESTING_GUIDE.md`
- pytest documentation: https://docs.pytest.org/

### Getting Help

If tests fail and you're unsure why:

1. Read the test docstring for expected behavior
2. Check recent code changes that might affect the test
3. Review fixture data in `conftest.py`
4. Run test with `-vv` for detailed output
5. Check test file for similar passing tests

## Summary

**These tests are the last line of defense against financial errors.**

- ⚡ Fast execution (< 5 seconds)
- 🎯 Focused on money-critical paths
- 📊 Comprehensive coverage of financial logic
- 🔒 Must pass before production deployment

**Remember**: A failing critical test is a production bug waiting to happen.

---

**Last Updated**: January 11, 2026
**Maintainer**: BotTrader Development Team
