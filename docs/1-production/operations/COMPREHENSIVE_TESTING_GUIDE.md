# Comprehensive Testing Guide for BotTrader

**Created**: January 11, 2026
**Last Updated**: January 11, 2026
**Current Test Coverage**: ~10%
**Target Coverage**: 80%+

---

## Executive Summary

This guide provides **solid methods for completely testing the BotTrader project** at all levels: unit, integration, end-to-end, and system tests. Currently, the project has minimal formal testing infrastructure. This document outlines what exists, what's missing, and how to implement comprehensive testing.

**Key Takeaway**: Testing BotTrader requires a multi-layered approach due to its distributed architecture (multiple containers), external dependencies (Coinbase API), and critical financial operations.

---

## Table of Contents

1. [Current Testing Status](#1-current-testing-status)
2. [Testing Philosophy & Strategy](#2-testing-philosophy--strategy)
3. [Test Levels Overview](#3-test-levels-overview)
4. [Quick Start: Running Tests Today](#4-quick-start-running-tests-today)
5. [Unit Testing](#5-unit-testing)
6. [Integration Testing](#6-integration-testing)
7. [End-to-End Testing](#7-end-to-end-testing)
8. [System Testing](#8-system-testing)
9. [Performance Testing](#9-performance-testing)
10. [Test Infrastructure Setup](#10-test-infrastructure-setup)
11. [Critical Components Testing Matrix](#11-critical-components-testing-matrix)
12. [Test Data Management](#12-test-data-management)
13. [Mocking External Dependencies](#13-mocking-external-dependencies)
14. [CI/CD Integration](#14-cicd-integration)
15. [Troubleshooting & Best Practices](#15-troubleshooting--best-practices)

---

## 1. Current Testing Status

### 1.1 What's Currently Tested (Minimal)

**Existing Test Files** (8 total):

| File | Coverage | Type | Status |
|------|----------|------|--------|
| `tests/test_fifo_engine.py` | FIFO allocation | Integration | ✅ Working |
| `tests/test_config.py` | Configuration loading | Diagnostic | ⚠️ Not pytest |
| `tests/test_fifo_report.py` | Report generation | Integration | ⚠️ Not pytest |
| `tests/test_structured_logging.py` | Logging system | Unit | ✅ Working |
| `tests/test_trailing_stop.py` | Trailing stop logic | Unit | ✅ Working |
| `TestDebugMaintenance/test_buy_action.py` | Buy action handler | Sanity | ⚠️ Minimal |
| `TestDebugMaintenance/test_ohlcv_integrity.py` | OHLCV data | Integration | ✅ Uses pytest |
| `sighook/test_order_sender.py` | Order submission | Manual | ⚠️ CLI tool |

**Estimated Coverage**: <10% of critical code paths

---

### 1.2 What's NOT Tested (Critical Gaps)

**High-Priority Untested Components**:

- ❌ **sighook/sender.py** (TradeBot main loop) - 0% coverage
- ❌ **webhook/listener.py** (1731 lines) - 0% coverage
- ❌ **webhook/webhook_order_manager.py** (945 lines) - 0% coverage
- ❌ **MarketDataManager/position_monitor.py** (51KB) - 0% coverage
- ❌ **MarketDataManager/asset_monitor.py** (75KB) - 0% coverage
- ❌ **SharedDataManager/trade_recorder.py** (58KB) - 0% coverage
- ❌ **Api_manager/coinbase_api.py** (54KB) - 0% coverage
- ❌ **signal_manager.py** (Technical indicators) - 0% coverage
- ❌ **trading_strategy.py** (Entry logic) - 0% coverage
- ❌ **order_manager.py** (Order placement) - 0% coverage

**Total Untested LOC**: ~400,000+ lines (est. 90% of codebase)

---

### 1.3 Testing Infrastructure Status

| Component | Status | Notes |
|-----------|--------|-------|
| **Test Framework** | ⚠️ Inconsistent | Mix of custom runners + minimal pytest |
| **pytest Configuration** | ❌ Missing | No pytest.ini or conftest.py |
| **CI/CD Pipeline** | ❌ Missing | No GitHub Actions or similar |
| **Coverage Measurement** | ❌ Missing | No pytest-cov configured |
| **Mock Framework** | ⚠️ Partial | Hand-rolled mocks, inconsistent |
| **Test Database** | ⚠️ Partial | test_fifo_engine creates tables manually |
| **Fixtures** | ❌ Missing | No shared test fixtures |
| **Test Data** | ⚠️ Scattered | Hardcoded in individual tests |

---

## 2. Testing Philosophy & Strategy

### 2.1 The Test Pyramid

```
        /\
       /E2E\      <- 10% (Full system, slow, brittle)
      /------\
     /INTEGR \   <- 30% (Multi-component, real DB)
    /----------\
   /   UNIT     \ <- 60% (Single component, fast, stable)
  /--------------\
```

**BotTrader Strategy**:
- **60% Unit Tests**: Individual functions, pure logic, mocked dependencies
- **30% Integration Tests**: Database operations, inter-component communication
- **10% E2E Tests**: Full order flow from signal → fill → P&L

---

### 2.2 Testing Priorities (Critical Path First)

**Priority 1: Money-Critical Paths** 🔴
- Order placement logic (prevent accidental orders)
- Position sizing calculations (prevent over-leveraging)
- Stop loss execution (prevent catastrophic losses)
- P&L calculations (ensure accuracy)
- FIFO allocation algorithm (tax reporting accuracy)

**Priority 2: Data Integrity** 🟡
- Trade recording
- Strategy snapshot linkage
- Database transactions
- OHLCV data freshness

**Priority 3: System Reliability** 🟢
- WebSocket reconnection
- Database connection pooling
- Error handling and retries
- Health checks

**Priority 4: Performance** 🔵
- Signal calculation speed
- Database query optimization
- WebSocket throughput

---

### 2.3 Test Characteristics

**Good Tests Are**:
- ✅ **Fast**: Unit tests < 100ms, integration tests < 5s
- ✅ **Isolated**: No shared state, can run in any order
- ✅ **Repeatable**: Same inputs → same outputs
- ✅ **Self-Validating**: Pass/fail, no manual inspection
- ✅ **Timely**: Written with or before code

**Avoid**:
- ❌ Tests that depend on external APIs (mock them)
- ❌ Tests that depend on specific database state (create fixtures)
- ❌ Tests that sleep/wait (use mocks and events)
- ❌ Tests that test implementation details (test behavior)

---

## 3. Test Levels Overview

### 3.1 Unit Tests

**What**: Test individual functions/classes in isolation
**Speed**: < 100ms per test
**Dependencies**: Mocked
**Typical Count**: 500-1000 tests

**Example** (FIFO allocation calculation):
```python
def test_fifo_single_lot_profit():
    """Test FIFO with single buy + single sell (profit scenario)"""
    # Arrange
    buys = [Trade(qty=1.0, price=100.0, time="2026-01-01")]
    sells = [Trade(qty=1.0, price=110.0, time="2026-01-02")]

    # Act
    allocations = compute_fifo_allocations(buys, sells)

    # Assert
    assert len(allocations) == 1
    assert allocations[0].pnl == 10.0  # $10 profit
    assert allocations[0].cost_basis == 100.0
```

---

### 3.2 Integration Tests

**What**: Test multiple components working together
**Speed**: 1-10 seconds per test
**Dependencies**: Real database, mocked external APIs
**Typical Count**: 100-200 tests

**Example** (Trade recording + FIFO):
```python
@pytest.mark.integration
async def test_trade_recording_triggers_fifo(test_db):
    """Test that recording a sell trade triggers FIFO allocation"""
    # Arrange
    trade_recorder = TradeRecorder(test_db)

    # Record buy
    await trade_recorder.record_trade(
        symbol="BTC-USD", side="buy", qty=0.1, price=40000.0
    )

    # Act: Record sell
    await trade_recorder.record_trade(
        symbol="BTC-USD", side="sell", qty=0.1, price=42000.0
    )

    # Assert: FIFO allocation created
    allocations = await test_db.fetch_all(
        "SELECT * FROM fifo_allocations WHERE symbol = 'BTC-USD'"
    )
    assert len(allocations) == 1
    assert allocations[0]["pnl_usd"] == pytest.approx(200.0, rel=0.01)
```

---

### 3.3 End-to-End Tests

**What**: Test complete user workflows through entire system
**Speed**: 10-60 seconds per test
**Dependencies**: Full system (containers, DB, mocked exchange)
**Typical Count**: 20-50 tests

**Example** (Buy signal → Order → Fill → P&L):
```python
@pytest.mark.e2e
@pytest.mark.slow
async def test_complete_buy_sell_cycle(running_system, mock_exchange):
    """Test full cycle: signal generation → order → fill → P&L"""
    # Arrange
    mock_exchange.set_price("BTC-USD", 40000.0)

    # Act 1: Signal generated (sighook)
    await running_system.sighook.generate_signals()

    # Wait for signal to propagate to webhook
    await asyncio.sleep(2)

    # Act 2: Webhook places order
    order = await running_system.webhook.get_pending_order("BTC-USD")
    assert order.side == "buy"

    # Act 3: Mock exchange fills order
    await mock_exchange.fill_order(order.order_id)

    # Wait for fill processing
    await asyncio.sleep(2)

    # Act 4: Price moves up, trigger exit
    mock_exchange.set_price("BTC-USD", 42000.0)
    await running_system.position_monitor.check_exits()

    # Assert: Trade recorded with profit
    pnl = await running_system.db.fetch_one(
        "SELECT SUM(pnl_usd) FROM fifo_allocations WHERE symbol = 'BTC-USD'"
    )
    assert pnl > 0  # Profitable roundtrip
```

---

### 3.4 System Tests

**What**: Test deployed system in production-like environment
**Speed**: Minutes to hours
**Dependencies**: Full AWS environment (test account)
**Typical Count**: 5-10 critical scenarios

**Example** (Smoke test after deployment):
```bash
#!/bin/bash
# Post-deployment smoke test

echo "=== BotTrader System Test ==="

# 1. Verify containers running
docker ps | grep -E "webhook|sighook|db" || exit 1

# 2. Verify database connectivity
docker exec db psql -U bot_user -d bot_trader_db -c "SELECT 1;" || exit 1

# 3. Verify API connectivity
curl -sf http://localhost:5003/health || exit 1

# 4. Verify recent trades exist
TRADE_COUNT=$(docker exec db psql -U bot_user -d bot_trader_db -t -c \
  "SELECT COUNT(*) FROM trade_records WHERE order_time >= NOW() - INTERVAL '24 hours'")

if [ "$TRADE_COUNT" -gt 0 ]; then
    echo "✅ Smoke tests PASSED"
else
    echo "⚠️  No recent trades found"
fi
```

---

## 4. Quick Start: Running Tests Today

### 4.1 Setup Test Environment

```bash
# 1. Install test dependencies
pip install pytest pytest-asyncio pytest-cov pytest-mock

# 2. Create test database
docker compose -f docker-compose.test.yml up -d db-test

# 3. Run existing tests
cd /Users/Manny/Python_Projects/BotTrader

# Run all tests
pytest tests/ -v

# Run specific test file
pytest tests/test_fifo_engine.py -v

# Run with coverage
pytest tests/ --cov=. --cov-report=html
```

---

### 4.2 Run Individual Test Files

```bash
# FIFO engine test (integration)
python -m tests.test_fifo_engine

# Configuration test (diagnostic)
python tests/test_config.py

# FIFO report test
python tests/test_fifo_report.py

# Structured logging test
python tests/test_structured_logging.py

# Trailing stop test
python tests/test_trailing_stop.py

# OHLCV integrity (uses pytest)
pytest TestDebugMaintenance/test_ohlcv_integrity.py -v

# Manual order sender test (CLI tool)
python sighook/test_order_sender.py --symbol BTC-USD --side buy --size 10.00
```

---

### 4.3 Expected Output

**Success**:
```
tests/test_fifo_engine.py::test_initialization PASSED
tests/test_fifo_engine.py::test_computation PASSED
tests/test_trailing_stop.py::test_activation PASSED
tests/test_trailing_stop.py::test_raising PASSED

=========== 4 passed in 3.45s ===========
```

**Failure**:
```
tests/test_fifo_engine.py::test_computation FAILED

AssertionError: Expected allocation count 1, got 0
```

---

## 5. Unit Testing

### 5.1 Unit Testing Strategy

**Target**: Individual functions and pure logic
**Mock**: All external dependencies (DB, API, network)
**Focus**: Edge cases, error conditions, business logic

---

### 5.2 Example: Testing Signal Calculations

**File**: `tests/unit/test_signal_manager.py` (NEW)

```python
import pytest
from sighook.signal_manager import SignalManager

class TestROCCalculation:
    """Unit tests for ROC (Rate of Change) indicator"""

    def test_roc_basic_calculation(self):
        """Test ROC with simple increasing prices"""
        # Arrange
        prices = [100, 105, 110, 115, 120]
        period = 1

        # Act
        roc = SignalManager.calculate_roc(prices, period)

        # Assert
        assert roc[-1] == pytest.approx(4.35, rel=0.01)  # (120-115)/115 * 100

    def test_roc_with_insufficient_data(self):
        """Test ROC returns None when insufficient data"""
        # Arrange
        prices = [100]  # Only 1 price
        period = 14

        # Act
        roc = SignalManager.calculate_roc(prices, period)

        # Assert
        assert roc is None

    def test_roc_with_zero_price(self):
        """Test ROC handles zero price gracefully"""
        # Arrange
        prices = [100, 0, 110]
        period = 1

        # Act & Assert
        with pytest.raises(ValueError, match="Zero price detected"):
            SignalManager.calculate_roc(prices, period)

class TestATRCalculation:
    """Unit tests for ATR (Average True Range) indicator"""

    def test_atr_basic_calculation(self):
        """Test ATR with known values"""
        # Arrange
        highs = [110, 115, 112]
        lows = [100, 105, 108]
        closes = [105, 110, 111]
        period = 2

        # Act
        atr = SignalManager.calculate_atr(highs, lows, closes, period)

        # Assert
        assert atr > 0
        assert atr < 20  # Reasonable range

    def test_atr_with_gaps(self):
        """Test ATR calculation with price gaps"""
        # Arrange
        highs = [100, 150, 145]  # Gap up
        lows = [90, 140, 135]
        closes = [95, 145, 140]
        period = 2

        # Act
        atr = SignalManager.calculate_atr(highs, lows, closes, period)

        # Assert
        assert atr > 10  # Should capture the gap
```

**Why This Works**:
- ✅ No external dependencies (pure calculation)
- ✅ Fast (< 1ms per test)
- ✅ Covers edge cases (insufficient data, zero values, gaps)
- ✅ Uses pytest.approx for floating-point comparisons

---

### 5.3 Example: Testing Order Validation

**File**: `tests/unit/test_order_validation.py` (NEW)

```python
import pytest
from webhook.webhook_order_manager import WebhookOrderManager
from decimal import Decimal

class TestOrderSizeValidation:
    """Unit tests for order size validation"""

    @pytest.fixture
    def order_manager(self):
        """Create order manager with mocked dependencies"""
        return WebhookOrderManager(
            db=MagicMock(),
            api=MagicMock(),
            config={"MIN_ORDER_SIZE_USD": 1.0}
        )

    def test_valid_order_size(self, order_manager):
        """Test valid order size passes validation"""
        # Arrange
        size = Decimal("0.001")  # BTC quantity
        price = Decimal("40000.0")  # USD price
        # Notional = 0.001 * 40000 = $40

        # Act
        result = order_manager.validate_order_size(size, price)

        # Assert
        assert result.is_valid
        assert result.notional == Decimal("40.0")

    def test_order_size_too_small(self, order_manager):
        """Test order below minimum is rejected"""
        # Arrange
        size = Decimal("0.00001")  # Tiny BTC quantity
        price = Decimal("40000.0")
        # Notional = 0.00001 * 40000 = $0.40 (below $1 min)

        # Act
        result = order_manager.validate_order_size(size, price)

        # Assert
        assert not result.is_valid
        assert "below minimum" in result.error_message.lower()

    def test_order_size_exceeds_balance(self, order_manager):
        """Test order exceeding balance is rejected"""
        # Arrange
        order_manager.get_available_balance = MagicMock(return_value=Decimal("100.0"))
        size = Decimal("0.01")  # BTC quantity
        price = Decimal("40000.0")
        # Notional = 0.01 * 40000 = $400 (exceeds $100 balance)

        # Act
        result = order_manager.validate_order_size(size, price)

        # Assert
        assert not result.is_valid
        assert "insufficient balance" in result.error_message.lower()
```

---

### 5.4 Unit Test Best Practices

1. **One Assert Per Test** (mostly)
   ```python
   # Good
   def test_roc_positive():
       assert roc > 0

   def test_roc_within_range():
       assert roc < 100

   # Avoid (multiple unrelated assertions)
   def test_roc():
       assert roc > 0
       assert roc < 100
       assert isinstance(roc, float)
       assert roc != None
   ```

2. **Use Descriptive Names**
   ```python
   # Good
   def test_trailing_stop_raises_on_new_high()

   # Bad
   def test_ts1()
   ```

3. **Arrange-Act-Assert Pattern**
   ```python
   def test_something():
       # Arrange (setup)
       input_data = create_test_data()

       # Act (execute)
       result = function_under_test(input_data)

       # Assert (verify)
       assert result == expected_value
   ```

4. **Test Edge Cases**
   - Empty inputs
   - Zero values
   - Negative numbers
   - Very large numbers
   - None/null values
   - Invalid types

---

## 6. Integration Testing

### 6.1 Integration Testing Strategy

**Target**: Multiple components working together
**Mock**: External APIs only (Coinbase)
**Real**: Database, internal communication

---

### 6.2 Example: Testing Trade Recording with Database

**File**: `tests/integration/test_trade_recorder.py` (NEW)

```python
import pytest
import asyncio
from SharedDataManager.trade_recorder import TradeRecorder
from database_manager.database_session_manager import DatabaseSessionManager

@pytest.fixture
async def test_db():
    """Create isolated test database"""
    # Setup: Create test database with schema
    db = await DatabaseSessionManager.create_test_database()
    await db.execute_schema("database/migrations/001_initial_schema.sql")

    yield db

    # Teardown: Drop test database
    await db.cleanup()

@pytest.mark.integration
@pytest.mark.asyncio
async def test_record_buy_order(test_db):
    """Test recording a buy order creates trade_records entry"""
    # Arrange
    recorder = TradeRecorder(test_db)
    order_data = {
        "order_id": "test-order-123",
        "symbol": "BTC-USD",
        "side": "buy",
        "size": "0.001",
        "price": "40000.00",
        "status": "filled"
    }

    # Act
    await recorder.record_trade(order_data)

    # Assert
    result = await test_db.fetch_one(
        "SELECT * FROM trade_records WHERE order_id = :order_id",
        {"order_id": "test-order-123"}
    )

    assert result is not None
    assert result["symbol"] == "BTC-USD"
    assert result["side"] == "buy"
    assert float(result["size"]) == 0.001

@pytest.mark.integration
@pytest.mark.asyncio
async def test_buy_sell_creates_fifo_allocation(test_db):
    """Test that buy+sell creates FIFO allocation"""
    # Arrange
    recorder = TradeRecorder(test_db)

    buy_order = {
        "order_id": "buy-123",
        "symbol": "BTC-USD",
        "side": "buy",
        "size": "0.001",
        "price": "40000.00",
        "filled_at": "2026-01-01T12:00:00Z"
    }

    sell_order = {
        "order_id": "sell-456",
        "symbol": "BTC-USD",
        "side": "sell",
        "size": "0.001",
        "price": "42000.00",
        "filled_at": "2026-01-02T12:00:00Z"
    }

    # Act
    await recorder.record_trade(buy_order)
    await recorder.record_trade(sell_order)

    # Trigger FIFO computation
    from fifo_engine.engine import FIFOEngine
    engine = FIFOEngine(test_db)
    await engine.compute_allocations(symbol="BTC-USD")

    # Assert
    allocation = await test_db.fetch_one(
        "SELECT * FROM fifo_allocations WHERE sell_order_id = :sell_id",
        {"sell_id": "sell-456"}
    )

    assert allocation is not None
    assert allocation["buy_order_id"] == "buy-123"
    assert float(allocation["pnl_usd"]) == pytest.approx(2.0, rel=0.01)  # (42000-40000) * 0.001
```

---

### 6.3 Example: Testing WebSocket Event Handling

**File**: `tests/integration/test_websocket_handling.py` (NEW)

```python
import pytest
import json
from webhook.listener import WebhookListener
from unittest.mock import AsyncMock, MagicMock

@pytest.fixture
def mock_websocket():
    """Mock WebSocket connection"""
    ws = AsyncMock()
    ws.recv = AsyncMock()
    return ws

@pytest.fixture
async def listener(test_db, mock_websocket):
    """Create listener with test database and mock WebSocket"""
    listener = WebhookListener(
        db=test_db,
        websocket=mock_websocket,
        config={}
    )
    return listener

@pytest.mark.integration
@pytest.mark.asyncio
async def test_order_fill_event_updates_database(listener, mock_websocket, test_db):
    """Test that order fill WebSocket event updates database"""
    # Arrange
    fill_event = {
        "type": "match",
        "order_id": "test-order-789",
        "product_id": "BTC-USD",
        "size": "0.001",
        "price": "40000.00",
        "side": "buy",
        "time": "2026-01-11T10:00:00Z"
    }

    # Mock WebSocket to return our fill event
    mock_websocket.recv.return_value = json.dumps(fill_event)

    # Act
    await listener.process_next_message()

    # Assert: Order status updated in database
    order = await test_db.fetch_one(
        "SELECT * FROM trade_records WHERE order_id = :order_id",
        {"order_id": "test-order-789"}
    )

    assert order["status"] == "filled"
    assert order["filled_at"] is not None

@pytest.mark.integration
@pytest.mark.asyncio
async def test_websocket_reconnect_on_disconnect(listener, mock_websocket):
    """Test WebSocket reconnection logic"""
    # Arrange
    mock_websocket.recv.side_effect = [
        ConnectionError("WebSocket disconnected"),  # First call fails
        '{"type": "heartbeat"}'  # Second call succeeds
    ]

    # Act
    await listener.start()

    # Assert: Listener attempted reconnection
    assert listener.reconnect_count == 1
    assert listener.is_connected
```

---

### 6.4 Integration Test Best Practices

1. **Use Test Database Isolation**
   ```python
   @pytest.fixture(scope="function")  # New DB per test
   async def test_db():
       db = await create_test_db()
       yield db
       await drop_test_db(db)
   ```

2. **Clean Up After Each Test**
   ```python
   async def test_something(test_db):
       # Test logic

       # Cleanup (or use fixture teardown)
       await test_db.execute("TRUNCATE TABLE trade_records CASCADE")
   ```

3. **Test Database Transactions**
   ```python
   @pytest.mark.integration
   async def test_transaction_rollback_on_error(test_db):
       """Test that errors rollback database transactions"""
       async with test_db.transaction() as tx:
           await tx.execute("INSERT INTO trades ...")
           raise ValueError("Simulated error")

       # Assert: No data committed
       count = await test_db.fetch_val("SELECT COUNT(*) FROM trades")
       assert count == 0
   ```

---

## 7. End-to-End Testing

### 7.1 E2E Testing Strategy

**Target**: Complete user workflows
**Scope**: Full system (all containers)
**Environment**: Docker Compose test stack
**Frequency**: Pre-release, critical changes

---

### 7.2 Example: Complete Buy/Sell Cycle

**File**: `tests/end_to_end/test_buy_sell_roundtrip.py` (NEW)

```python
import pytest
import asyncio
import docker

@pytest.fixture(scope="module")
async def running_system():
    """Start full BotTrader system in test mode"""
    # Start Docker Compose test stack
    client = docker.from_env()
    client.compose.up(
        project_name="bottrader-test",
        files=["docker-compose.test.yml"],
        detach=True
    )

    # Wait for health checks
    await asyncio.sleep(30)

    yield SystemTestClient(client)

    # Teardown
    client.compose.down(project_name="bottrader-test")

@pytest.mark.e2e
@pytest.mark.slow
@pytest.mark.asyncio
async def test_complete_profitable_roundtrip(running_system):
    """
    Test complete workflow:
    1. Signal generated (buy BTC at $40k)
    2. Order placed
    3. Order filled
    4. Price rises to $42k
    5. Exit signal generated
    6. Sell order placed
    7. Sell order filled
    8. P&L calculated correctly
    """
    # Arrange
    await running_system.mock_exchange.set_price("BTC-USD", 40000.0)
    await running_system.mock_exchange.set_liquidity("BTC-USD", high=True)

    # Act 1: Trigger signal generation
    await running_system.sighook.trigger_signal_check()

    # Wait for signal propagation
    await asyncio.sleep(5)

    # Assert 1: Buy order created
    buy_order = await running_system.db.fetch_one(
        "SELECT * FROM trade_records WHERE symbol = 'BTC-USD' AND side = 'buy' ORDER BY order_time DESC LIMIT 1"
    )
    assert buy_order is not None
    assert buy_order["status"] == "pending"

    # Act 2: Mock exchange fills buy order
    await running_system.mock_exchange.fill_order(buy_order["order_id"])
    await asyncio.sleep(3)

    # Assert 2: Buy order marked as filled
    buy_order_updated = await running_system.db.fetch_one(
        f"SELECT * FROM trade_records WHERE order_id = '{buy_order['order_id']}'"
    )
    assert buy_order_updated["status"] == "filled"

    # Act 3: Price moves up 5%
    await running_system.mock_exchange.set_price("BTC-USD", 42000.0)
    await running_system.position_monitor.check_exits()
    await asyncio.sleep(5)

    # Assert 3: Sell order created (exit signal)
    sell_order = await running_system.db.fetch_one(
        "SELECT * FROM trade_records WHERE symbol = 'BTC-USD' AND side = 'sell' ORDER BY order_time DESC LIMIT 1"
    )
    assert sell_order is not None

    # Act 4: Mock exchange fills sell order
    await running_system.mock_exchange.fill_order(sell_order["order_id"])
    await asyncio.sleep(3)

    # Assert 4: FIFO allocation created with profit
    allocation = await running_system.db.fetch_one(
        f"SELECT * FROM fifo_allocations WHERE sell_order_id = '{sell_order['order_id']}'"
    )
    assert allocation is not None
    assert float(allocation["pnl_usd"]) > 0  # Profitable trade
    assert float(allocation["pnl_usd"]) == pytest.approx(
        (42000.0 - 40000.0) * float(sell_order["size"]),
        rel=0.01
    )

@pytest.mark.e2e
@pytest.mark.slow
@pytest.mark.asyncio
async def test_stop_loss_triggers_on_price_drop(running_system):
    """Test that stop loss exits position when price drops"""
    # Arrange
    await running_system.mock_exchange.set_price("BTC-USD", 40000.0)

    # Create existing position (simulate previous buy)
    await running_system.db.execute(
        """INSERT INTO trade_records (order_id, symbol, side, size, price, status, filled_at)
           VALUES ('buy-999', 'BTC-USD', 'buy', 0.001, 40000.00, 'filled', NOW())"""
    )

    # Act: Price drops 5% (should trigger SL at -4.5%)
    await running_system.mock_exchange.set_price("BTC-USD", 38000.0)
    await running_system.position_monitor.check_exits()
    await asyncio.sleep(3)

    # Assert: Stop loss sell order created
    sl_order = await running_system.db.fetch_one(
        "SELECT * FROM trade_records WHERE symbol = 'BTC-USD' AND side = 'sell' ORDER BY order_time DESC LIMIT 1"
    )
    assert sl_order is not None
    assert sl_order["parent_id"] == "buy-999"
    assert "stop_loss" in sl_order["notes"].lower()
```

---

### 7.3 E2E Test Infrastructure

**File**: `docker-compose.test.yml` (NEW)

```yaml
version: '3.8'

services:
  db-test:
    image: postgres:16
    environment:
      POSTGRES_DB: test_db
      POSTGRES_USER: test_user
      POSTGRES_PASSWORD: test_pass
    ports:
      - "5433:5432"  # Different port from production

  mock-exchange:
    build:
      context: .
      dockerfile: tests/fixtures/Dockerfile.mock-exchange
    ports:
      - "8080:8080"
    environment:
      MOCK_MODE: "true"

  webhook-test:
    build:
      context: .
      dockerfile: docker/Dockerfile.bot
    environment:
      DB_HOST: db-test
      DB_NAME: test_db
      COINBASE_API_URL: http://mock-exchange:8080
      RUN_MODE: webhook
    depends_on:
      - db-test
      - mock-exchange

  sighook-test:
    build:
      context: .
      dockerfile: docker/Dockerfile.bot
    environment:
      DB_HOST: db-test
      DB_NAME: test_db
      WEBHOOK_BASE_URL: http://webhook-test:5003
      RUN_MODE: sighook
    depends_on:
      - db-test
      - webhook-test
```

---

## 8. System Testing

### 8.1 Smoke Tests (Post-Deployment)

**File**: `tests/system/smoke_test.sh` (NEW)

```bash
#!/bin/bash
# Post-deployment smoke test for BotTrader on AWS

set -e

echo "=========================================="
echo "BotTrader System Smoke Test"
echo "=========================================="

# 1. Verify all containers running
echo "[1/10] Checking Docker containers..."
CONTAINERS="webhook sighook db"
for container in $CONTAINERS; do
    if docker ps | grep -q "$container"; then
        echo "✅ $container is running"
    else
        echo "❌ $container is NOT running"
        exit 1
    fi
done

# 2. Verify database connectivity
echo "[2/10] Checking database connectivity..."
docker exec db psql -U bot_user -d bot_trader_db -c "SELECT 1;" > /dev/null 2>&1
if [ $? -eq 0 ]; then
    echo "✅ Database is accessible"
else
    echo "❌ Database connection failed"
    exit 1
fi

# 3. Verify webhook health endpoint
echo "[3/10] Checking webhook health..."
HEALTH=$(curl -sf http://localhost:5003/health)
if [ $? -eq 0 ]; then
    echo "✅ Webhook health check passed"
else
    echo "❌ Webhook health check failed"
    exit 1
fi

# 4. Verify recent trades exist
echo "[4/10] Checking for recent trading activity..."
TRADE_COUNT=$(docker exec db psql -U bot_user -d bot_trader_db -t -c \
    "SELECT COUNT(*) FROM trade_records WHERE order_time >= NOW() - INTERVAL '24 hours'" | tr -d ' ')
if [ "$TRADE_COUNT" -gt 0 ]; then
    echo "✅ Found $TRADE_COUNT trades in last 24h"
else
    echo "⚠️  No trades in last 24h (may be normal)"
fi

# 5. Verify FIFO allocations up to date
echo "[5/10] Checking FIFO allocation status..."
INCOMPLETE_SELLS=$(docker exec db psql -U bot_user -d bot_trader_db -t -c \
    "SELECT COUNT(*) FROM trade_records tr
     WHERE tr.side = 'sell' AND tr.status IN ('filled', 'done')
       AND NOT EXISTS (SELECT 1 FROM fifo_allocations fa WHERE fa.sell_order_id = tr.order_id AND fa.allocation_version = 2)" | tr -d ' ')
if [ "$INCOMPLETE_SELLS" -eq 0 ]; then
    echo "✅ All sell orders have FIFO allocations"
else
    echo "⚠️  $INCOMPLETE_SELLS sell orders missing FIFO allocations"
fi

# 6. Verify strategy snapshot active
echo "[6/10] Checking strategy snapshot..."
ACTIVE_SNAPSHOT=$(docker exec db psql -U bot_user -d bot_trader_db -t -c \
    "SELECT snapshot_id FROM strategy_snapshots WHERE active_until IS NULL LIMIT 1" | tr -d ' ')
if [ -n "$ACTIVE_SNAPSHOT" ]; then
    echo "✅ Active strategy snapshot: $ACTIVE_SNAPSHOT"
else
    echo "❌ No active strategy snapshot found"
    exit 1
fi

# 7. Verify trade linkage
echo "[7/10] Checking trade-to-strategy linkage..."
LINK_RATE=$(docker exec db psql -U bot_user -d bot_trader_db -t -c \
    "SELECT ROUND((COUNT(DISTINCT tsl.order_id)::decimal / NULLIF(COUNT(DISTINCT tr.order_id), 0) * 100)::numeric, 1)
     FROM trade_records tr
     LEFT JOIN trade_strategy_link tsl ON tsl.order_id = tr.order_id
     WHERE tr.order_time >= NOW() - INTERVAL '24 hours'" | tr -d ' ')
if [ -n "$LINK_RATE" ] && [ "$LINK_RATE" != "" ]; then
    echo "✅ Trade linkage rate: $LINK_RATE%"
else
    echo "⚠️  Could not determine trade linkage rate"
fi

# 8. Verify log files exist and are recent
echo "[8/10] Checking log files..."
LATEST_LOG=$(ssh bottrader-aws "ls -t /opt/bot/logs/*.log 2>/dev/null | head -1")
if [ -n "$LATEST_LOG" ]; then
    LOG_AGE=$(ssh bottrader-aws "stat -c %Y $LATEST_LOG")
    CURRENT_TIME=$(date +%s)
    AGE_SECONDS=$((CURRENT_TIME - LOG_AGE))
    if [ $AGE_SECONDS -lt 3600 ]; then  # < 1 hour old
        echo "✅ Recent log file found (${AGE_SECONDS}s old)"
    else
        echo "⚠️  Latest log is ${AGE_SECONDS}s old"
    fi
else
    echo "❌ No log files found"
fi

# 9. Verify cron jobs scheduled
echo "[9/10] Checking cron jobs..."
CRON_COUNT=$(ssh bottrader-aws "crontab -l 2>/dev/null | grep -c weekly" || echo "0")
if [ "$CRON_COUNT" -gt 0 ]; then
    echo "✅ Weekly report cron job scheduled"
else
    echo "⚠️  No weekly report cron job found"
fi

# 10. Verify disk space
echo "[10/10] Checking disk space..."
DISK_USAGE=$(ssh bottrader-aws "df -h /opt/bot | tail -1 | awk '{print \$5}' | sed 's/%//'")
if [ "$DISK_USAGE" -lt 80 ]; then
    echo "✅ Disk usage: ${DISK_USAGE}%"
else
    echo "⚠️  Disk usage high: ${DISK_USAGE}%"
fi

echo "=========================================="
echo "✅ Smoke tests COMPLETE"
echo "=========================================="
```

---

### 8.2 Load Testing

**File**: `tests/system/load_test.py` (NEW)

```python
import asyncio
import aiohttp
import time
from datetime import datetime

async def send_webhook(session, order_id):
    """Send webhook request"""
    payload = {
        "symbol": "BTC-USD",
        "side": "buy",
        "size": "0.001",
        "price": "40000.00",
        "order_id": order_id,
        "timestamp": datetime.now().isoformat()
    }

    start = time.time()
    async with session.post("http://localhost:5003/webhook", json=payload) as resp:
        duration = time.time() - start
        return resp.status, duration

async def load_test_webhooks(num_requests=100, concurrency=10):
    """Load test webhook endpoint"""
    print(f"Starting load test: {num_requests} requests, {concurrency} concurrent")

    async with aiohttp.ClientSession() as session:
        tasks = []
        for i in range(num_requests):
            task = send_webhook(session, f"load-test-{i}")
            tasks.append(task)

            # Limit concurrency
            if len(tasks) >= concurrency:
                results = await asyncio.gather(*tasks)
                tasks = []

                # Analyze results
                success = sum(1 for status, _ in results if status == 200)
                avg_duration = sum(d for _, d in results) / len(results)
                print(f"Batch: {success}/{len(results)} success, avg {avg_duration:.3f}s")

        # Process remaining
        if tasks:
            results = await asyncio.gather(*tasks)
            success = sum(1 for status, _ in results if status == 200)
            avg_duration = sum(d for _, d in results) / len(results)
            print(f"Final batch: {success}/{len(results)} success, avg {avg_duration:.3f}s")

if __name__ == "__main__":
    asyncio.run(load_test_webhooks(num_requests=1000, concurrency=50))
```

---

## 9. Performance Testing

### 9.1 FIFO Engine Performance Test

**File**: `tests/performance/test_fifo_performance.py` (NEW)

```python
import pytest
import time
from fifo_engine.engine import FIFOEngine
from tests.fixtures.trade_generator import TradeGenerator

@pytest.mark.performance
@pytest.mark.asyncio
async def test_fifo_with_1000_trades(test_db):
    """Test FIFO performance with 1000 trades"""
    # Arrange
    generator = TradeGenerator()
    trades = generator.generate_random_trades(count=1000, symbol="BTC-USD")

    # Insert trades into database
    await test_db.insert_many("trade_records", trades)

    engine = FIFOEngine(test_db)

    # Act
    start = time.perf_counter()
    await engine.compute_allocations(symbol="BTC-USD")
    duration = time.perf_counter() - start

    # Assert
    assert duration < 5.0, f"FIFO took {duration:.2f}s (expected < 5s)"

    # Verify allocations created
    count = await test_db.fetch_val(
        "SELECT COUNT(*) FROM fifo_allocations WHERE symbol = 'BTC-USD'"
    )
    assert count > 0

@pytest.mark.performance
@pytest.mark.slow
@pytest.mark.asyncio
async def test_fifo_with_100k_trades(test_db):
    """Stress test FIFO with 100k trades"""
    # Arrange
    generator = TradeGenerator()

    # Insert in batches to avoid memory issues
    batch_size = 10000
    for i in range(10):
        trades = generator.generate_random_trades(count=batch_size, symbol="BTC-USD")
        await test_db.insert_many("trade_records", trades)

    engine = FIFOEngine(test_db)

    # Act
    start = time.perf_counter()
    await engine.compute_allocations(symbol="BTC-USD")
    duration = time.perf_counter() - start

    # Assert
    print(f"FIFO with 100k trades took {duration:.2f}s")
    assert duration < 300.0, f"FIFO took {duration:.2f}s (expected < 5min)"
```

---

### 9.2 Database Query Performance

**File**: `tests/performance/test_query_performance.py` (NEW)

```python
import pytest
import time

@pytest.mark.performance
@pytest.mark.asyncio
async def test_recent_trades_query_performance(test_db):
    """Test performance of recent trades query"""
    query = """
        SELECT * FROM trade_records
        WHERE order_time >= NOW() - INTERVAL '7 days'
        ORDER BY order_time DESC
    """

    # Act
    start = time.perf_counter()
    results = await test_db.fetch_all(query)
    duration = time.perf_counter() - start

    # Assert
    assert duration < 0.5, f"Query took {duration:.3f}s (expected < 0.5s)"
    print(f"Retrieved {len(results)} trades in {duration:.3f}s")

@pytest.mark.performance
@pytest.mark.asyncio
async def test_fifo_allocation_join_performance(test_db):
    """Test performance of FIFO allocation joins"""
    query = """
        SELECT tr.symbol, tr.order_id, fa.pnl_usd
        FROM trade_records tr
        JOIN fifo_allocations fa ON fa.sell_order_id = tr.order_id
        WHERE tr.order_time >= NOW() - INTERVAL '30 days'
          AND fa.allocation_version = 2
    """

    # Act
    start = time.perf_counter()
    results = await test_db.fetch_all(query)
    duration = time.perf_counter() - start

    # Assert
    assert duration < 1.0, f"Join query took {duration:.3f}s (expected < 1s)"
    print(f"Join returned {len(results)} rows in {duration:.3f}s")
```

---

## 10. Test Infrastructure Setup

### 10.1 Create pytest.ini

**File**: `pytest.ini` (NEW)

```ini
[pytest]
# Async support
asyncio_mode = auto

# Test discovery
testpaths = tests
python_files = test_*.py
python_classes = Test*
python_functions = test_*

# Output
addopts =
    -v
    --tb=short
    --strict-markers
    --disable-warnings
    -ra

# Markers
markers =
    unit: Unit tests (fast, isolated)
    integration: Integration tests (real DB, mocked APIs)
    e2e: End-to-end tests (full system)
    slow: Slow-running tests (> 10s)
    performance: Performance/benchmark tests
    smoke: Smoke tests for deployment verification

# Coverage
[coverage:run]
source = .
omit =
    tests/*
    venv/*
    */migrations/*

[coverage:report]
precision = 2
show_missing = True
skip_covered = False
```

---

### 10.2 Create conftest.py

**File**: `tests/conftest.py` (NEW)

```python
import pytest
import asyncio
from database_manager.database_session_manager import DatabaseSessionManager
from unittest.mock import AsyncMock, MagicMock

# Configure event loop for async tests
@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for session"""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()

@pytest.fixture(scope="function")
async def test_db():
    """Create isolated test database for each test"""
    db = DatabaseSessionManager(
        host="localhost",
        port=5433,  # Test database port
        database="test_db",
        user="test_user",
        password="test_pass"
    )

    await db.initialize()

    # Load schema
    await db.execute_file("database/migrations/001_initial_schema.sql")
    await db.execute_file("database/migrations/002_strategy_snapshots.sql")

    yield db

    # Cleanup
    await db.execute("DROP SCHEMA public CASCADE")
    await db.execute("CREATE SCHEMA public")
    await db.close()

@pytest.fixture
def mock_coinbase_api():
    """Mock Coinbase API"""
    api = AsyncMock()
    api.get_price.return_value = {"price": "40000.00"}
    api.place_order.return_value = {"order_id": "mock-order-123", "status": "pending"}
    api.cancel_order.return_value = {"status": "cancelled"}
    return api

@pytest.fixture
def mock_websocket():
    """Mock WebSocket connection"""
    ws = AsyncMock()
    ws.recv.return_value = '{"type": "heartbeat"}'
    ws.send.return_value = None
    return ws

@pytest.fixture
def mock_logger():
    """Mock logger"""
    logger = MagicMock()
    return logger

@pytest.fixture
async def sample_trades(test_db):
    """Insert sample trades for testing"""
    trades = [
        {
            "order_id": "test-buy-1",
            "symbol": "BTC-USD",
            "side": "buy",
            "size": 0.001,
            "price": 40000.00,
            "status": "filled",
            "filled_at": "2026-01-01T12:00:00Z"
        },
        {
            "order_id": "test-sell-1",
            "symbol": "BTC-USD",
            "side": "sell",
            "size": 0.001,
            "price": 42000.00,
            "status": "filled",
            "filled_at": "2026-01-02T12:00:00Z"
        }
    ]

    for trade in trades:
        await test_db.execute(
            """INSERT INTO trade_records (order_id, symbol, side, size, price, status, filled_at)
               VALUES (:order_id, :symbol, :side, :size, :price, :status, :filled_at)""",
            trade
        )

    yield trades

    # Cleanup handled by test_db fixture

# Add custom markers
def pytest_configure(config):
    config.addinivalue_line("markers", "unit: Unit tests")
    config.addinivalue_line("markers", "integration: Integration tests")
    config.addinivalue_line("markers", "e2e: End-to-end tests")
    config.addinivalue_line("markers", "slow: Slow tests")
    config.addinivalue_line("markers", "performance: Performance tests")
```

---

## 11. Critical Components Testing Matrix

| Component | Unit | Integration | E2E | Priority |
|-----------|------|-------------|-----|----------|
| **FIFO Engine** | ✅ Algorithm tests | ✅ DB integration | ⚠️ Full flow | 🔴 Critical |
| **Position Monitor** | ❌ Exit logic | ❌ DB queries | ❌ Live exits | 🔴 Critical |
| **Order Manager** | ❌ Validation | ❌ API calls | ❌ Full orders | 🔴 Critical |
| **Signal Manager** | ❌ Indicators | ❌ OHLCV data | ❌ Live signals | 🔴 Critical |
| **Trade Recorder** | ❌ CRUD ops | ✅ DB writes | ❌ Full flow | 🟡 High |
| **WebSocket Handler** | ❌ Event parsing | ❌ Reconnection | ❌ Live stream | 🟡 High |
| **Report Generator** | ⚠️ Partial | ⚠️ Partial | ❌ Email send | 🟢 Medium |
| **Config Manager** | ⚠️ Basic | ❌ Env loading | ❌ Full stack | 🟢 Medium |
| **Logger** | ✅ Complete | ✅ File writes | ⚠️ Rotation | 🟢 Medium |

**Legend**:
- ✅ = Tests exist and passing
- ⚠️ = Partial coverage or outdated
- ❌ = No tests exist
- 🔴 = Must test (money critical)
- 🟡 = Should test (data integrity)
- 🟢 = Nice to test (quality of life)

---

## 12. Test Data Management

### 12.1 Fixture Data Files

**File**: `tests/fixtures/sample_trades.json` (NEW)

```json
{
  "profitable_roundtrip": [
    {
      "order_id": "buy-profit-1",
      "symbol": "BTC-USD",
      "side": "buy",
      "size": "0.001",
      "price": "40000.00",
      "filled_at": "2026-01-01T10:00:00Z"
    },
    {
      "order_id": "sell-profit-1",
      "symbol": "BTC-USD",
      "side": "sell",
      "size": "0.001",
      "price": "42000.00",
      "filled_at": "2026-01-01T12:00:00Z"
    }
  ],
  "losing_roundtrip": [
    {
      "order_id": "buy-loss-1",
      "symbol": "ETH-USD",
      "side": "buy",
      "size": "0.01",
      "price": "2500.00",
      "filled_at": "2026-01-02T10:00:00Z"
    },
    {
      "order_id": "sell-loss-1",
      "symbol": "ETH-USD",
      "side": "sell",
      "size": "0.01",
      "price": "2400.00",
      "filled_at": "2026-01-02T14:00:00Z"
    }
  ]
}
```

---

### 12.2 Trade Generator

**File**: `tests/fixtures/trade_generator.py` (NEW)

```python
import random
from datetime import datetime, timedelta
from decimal import Decimal

class TradeGenerator:
    """Generate realistic trade data for testing"""

    def generate_random_trades(self, count=100, symbol="BTC-USD"):
        """Generate random buy/sell trades"""
        trades = []
        base_price = 40000.0
        current_time = datetime.now()

        for i in range(count):
            # Randomize side with slight buy bias
            side = "buy" if random.random() < 0.52 else "sell"

            # Random price variation ±5%
            price_variation = random.uniform(-0.05, 0.05)
            price = base_price * (1 + price_variation)

            # Random size (0.0001 to 0.01 BTC)
            size = random.uniform(0.0001, 0.01)

            # Increment time
            current_time += timedelta(minutes=random.randint(1, 60))

            trade = {
                "order_id": f"gen-{symbol}-{i}",
                "symbol": symbol,
                "side": side,
                "size": round(size, 8),
                "price": round(price, 2),
                "status": "filled",
                "filled_at": current_time.isoformat()
            }

            trades.append(trade)

        return trades

    def generate_fifo_scenario(self, scenario="simple_profit"):
        """Generate specific FIFO test scenarios"""
        scenarios = {
            "simple_profit": [
                {"side": "buy", "qty": 1.0, "price": 100.0},
                {"side": "sell", "qty": 1.0, "price": 110.0}
            ],
            "multiple_lots": [
                {"side": "buy", "qty": 0.5, "price": 100.0},
                {"side": "buy", "qty": 0.5, "price": 105.0},
                {"side": "sell", "qty": 1.0, "price": 110.0}
            ],
            "partial_fill": [
                {"side": "buy", "qty": 1.0, "price": 100.0},
                {"side": "sell", "qty": 0.5, "price": 110.0}
            ]
        }

        return scenarios.get(scenario, [])
```

---

## 13. Mocking External Dependencies

### 13.1 Mock Coinbase API

**File**: `tests/mocks/mock_coinbase_api.py` (NEW)

```python
from unittest.mock import AsyncMock
import random

class MockCoinbaseAPI:
    """Mock Coinbase API for testing"""

    def __init__(self):
        self.orders = {}
        self.prices = {"BTC-USD": 40000.0, "ETH-USD": 2500.0}
        self.balances = {"USD": 10000.0, "BTC": 0.0, "ETH": 0.0}

    async def get_price(self, symbol):
        """Mock get_price"""
        return {"price": str(self.prices.get(symbol, 0.0))}

    async def place_order(self, symbol, side, size, order_type="limit", price=None):
        """Mock place_order"""
        order_id = f"mock-{random.randint(1000, 9999)}"

        order = {
            "order_id": order_id,
            "symbol": symbol,
            "side": side,
            "size": str(size),
            "type": order_type,
            "price": str(price) if price else None,
            "status": "pending"
        }

        self.orders[order_id] = order
        return order

    async def fill_order(self, order_id):
        """Simulate order fill"""
        if order_id in self.orders:
            self.orders[order_id]["status"] = "filled"

            # Update balances
            order = self.orders[order_id]
            symbol = order["symbol"]
            base_currency = symbol.split("-")[0]
            quote_currency = symbol.split("-")[1]

            size = float(order["size"])
            price = float(order["price"]) if order["price"] else self.prices[symbol]
            notional = size * price

            if order["side"] == "buy":
                self.balances[quote_currency] -= notional
                self.balances[base_currency] += size
            else:
                self.balances[base_currency] -= size
                self.balances[quote_currency] += notional

            return {"status": "filled", "filled_price": price}

        return {"error": "Order not found"}

    async def get_balance(self, currency):
        """Mock get_balance"""
        return {"balance": str(self.balances.get(currency, 0.0))}

    def set_price(self, symbol, price):
        """Set mock price for testing"""
        self.prices[symbol] = price

    def reset(self):
        """Reset mock state"""
        self.orders = {}
        self.balances = {"USD": 10000.0, "BTC": 0.0, "ETH": 0.0}
```

---

### 13.2 Mock WebSocket

**File**: `tests/mocks/mock_websocket.py` (NEW)

```python
import asyncio
import json
from collections import deque

class MockWebSocket:
    """Mock WebSocket for testing"""

    def __init__(self):
        self.messages = deque()
        self.sent_messages = []
        self.connected = True

    async def recv(self):
        """Mock recv - return queued messages"""
        if not self.connected:
            raise ConnectionError("WebSocket disconnected")

        if not self.messages:
            # Return heartbeat if no messages queued
            return json.dumps({"type": "heartbeat"})

        return self.messages.popleft()

    async def send(self, message):
        """Mock send - record sent messages"""
        self.sent_messages.append(message)

    def queue_message(self, message_dict):
        """Queue a message to be received"""
        self.messages.append(json.dumps(message_dict))

    def queue_fill_event(self, order_id, symbol, size, price, side):
        """Queue an order fill event"""
        event = {
            "type": "match",
            "order_id": order_id,
            "product_id": symbol,
            "size": str(size),
            "price": str(price),
            "side": side,
            "time": "2026-01-11T10:00:00Z"
        }
        self.queue_message(event)

    def disconnect(self):
        """Simulate disconnection"""
        self.connected = False

    def reconnect(self):
        """Simulate reconnection"""
        self.connected = True
```

---

## 14. CI/CD Integration

### 14.1 GitHub Actions Workflow

**File**: `.github/workflows/test.yml` (NEW)

```yaml
name: Test Suite

on:
  push:
    branches: [ main, feature/* ]
  pull_request:
    branches: [ main ]

jobs:
  test:
    runs-on: ubuntu-latest

    services:
      postgres:
        image: postgres:16
        env:
          POSTGRES_DB: test_db
          POSTGRES_USER: test_user
          POSTGRES_PASSWORD: test_pass
        ports:
          - 5433:5432
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5

    steps:
      - name: Checkout code
        uses: actions/checkout@v3

      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'

      - name: Cache dependencies
        uses: actions/cache@v3
        with:
          path: ~/.cache/pip
          key: ${{ runner.os }}-pip-${{ hashFiles('requirements.txt') }}

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt
          pip install pytest pytest-asyncio pytest-cov pytest-mock

      - name: Run unit tests
        run: |
          pytest tests/ -m "unit" -v --cov=. --cov-report=xml

      - name: Run integration tests
        run: |
          pytest tests/ -m "integration" -v --cov=. --cov-append --cov-report=xml
        env:
          DB_HOST: localhost
          DB_PORT: 5433
          DB_NAME: test_db
          DB_USER: test_user
          DB_PASSWORD: test_pass

      - name: Upload coverage to Codecov
        uses: codecov/codecov-action@v3
        with:
          file: ./coverage.xml
          fail_ci_if_error: true

      - name: Check coverage threshold
        run: |
          COVERAGE=$(grep -oP 'line-rate="\K[0-9.]+' coverage.xml | head -1)
          COVERAGE_PCT=$(echo "$COVERAGE * 100" | bc -l | cut -d. -f1)
          echo "Coverage: ${COVERAGE_PCT}%"
          if [ "$COVERAGE_PCT" -lt 50 ]; then
            echo "❌ Coverage ${COVERAGE_PCT}% below threshold (50%)"
            exit 1
          fi
```

---

### 14.2 Pre-commit Hook

**File**: `.git/hooks/pre-commit` (NEW)

```bash
#!/bin/bash
# Pre-commit hook: Run tests before allowing commit

echo "Running pre-commit tests..."

# Run unit tests only (fast)
pytest tests/ -m "unit" -q

if [ $? -ne 0 ]; then
    echo "❌ Unit tests failed. Commit aborted."
    exit 1
fi

echo "✅ Unit tests passed"
exit 0
```

---

## 15. Troubleshooting & Best Practices

### 15.1 Common Test Failures

**Issue**: `asyncio.run() cannot be called from a running event loop`

**Solution**: Use `pytest-asyncio` and `async def` test functions
```python
# Bad
def test_something():
    asyncio.run(async_function())

# Good
@pytest.mark.asyncio
async def test_something():
    await async_function()
```

---

**Issue**: Database connection errors in tests

**Solution**: Ensure test database is running and isolated
```python
@pytest.fixture(scope="function")  # New DB per test
async def test_db():
    db = await create_test_db()
    yield db
    await cleanup_test_db(db)
```

---

**Issue**: Tests pass individually but fail when run together

**Solution**: Tests are not isolated - shared state issue
```python
# Add cleanup to fixtures
@pytest.fixture
async def sample_data(test_db):
    data = await create_data(test_db)
    yield data
    await test_db.execute("TRUNCATE TABLE trades CASCADE")  # Cleanup
```

---

### 15.2 Best Practices Summary

1. **Test One Thing**
   - Each test should verify one behavior
   - Name tests descriptively

2. **Use Fixtures**
   - Avoid duplicate setup code
   - Share fixtures via conftest.py

3. **Mock External Dependencies**
   - Never call real Coinbase API in tests
   - Use mock objects consistently

4. **Keep Tests Fast**
   - Unit tests < 100ms
   - Use in-memory databases when possible

5. **Test Edge Cases**
   - Empty inputs, None values
   - Boundary conditions
   - Error scenarios

6. **Measure Coverage**
   - Aim for 80%+ on critical paths
   - 100% on money-critical functions

7. **Run Tests Often**
   - Before every commit (pre-commit hook)
   - On every push (CI/CD)

---

## Next Steps

1. **Immediate** (This Week):
   - Create `pytest.ini` and `conftest.py`
   - Convert existing tests to pytest format
   - Add unit tests for FIFO algorithm
   - Set up CI/CD pipeline

2. **Short-term** (Next Sprint):
   - Add integration tests for database operations
   - Mock Coinbase API
   - Test critical money paths (order placement, stop loss)
   - Measure baseline coverage

3. **Long-term** (Next Quarter):
   - Achieve 80%+ coverage on critical components
   - Add E2E tests for complete workflows
   - Performance testing and optimization
   - Continuous monitoring of test health

---

**Document Maintainer**: Development Team
**Last Updated**: January 11, 2026
**Next Review**: After achieving 50% test coverage
