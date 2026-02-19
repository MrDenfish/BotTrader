"""
Critical Path Test Fixtures

Shared fixtures for money-critical path testing.
These fixtures provide minimal, fast setup for critical tests.
"""

import pytest
from decimal import Decimal
from datetime import datetime, timedelta
from unittest.mock import MagicMock, AsyncMock


@pytest.fixture
def sample_prices():
    """Sample price data for testing"""
    return {
        "BTC-USD": Decimal("40000.00"),
        "ETH-USD": Decimal("2500.00"),
        "SOL-USD": Decimal("100.00")
    }


@pytest.fixture
def sample_trade_buy():
    """Sample buy trade"""
    return {
        "order_id": "test-buy-001",
        "symbol": "BTC-USD",
        "side": "buy",
        "size": Decimal("0.001"),
        "price": Decimal("40000.00"),
        "filled_at": datetime(2026, 1, 1, 12, 0, 0),
        "status": "filled",
        "fees": Decimal("0.40")  # 1% fee
    }


@pytest.fixture
def sample_trade_sell():
    """Sample sell trade"""
    return {
        "order_id": "test-sell-001",
        "symbol": "BTC-USD",
        "side": "sell",
        "size": Decimal("0.001"),
        "price": Decimal("42000.00"),
        "filled_at": datetime(2026, 1, 2, 12, 0, 0),
        "status": "filled",
        "fees": Decimal("0.42")  # 1% fee
    }


@pytest.fixture
def sample_position():
    """Sample open position"""
    return {
        "symbol": "BTC-USD",
        "qty": Decimal("0.001"),
        "cost_basis": Decimal("40000.00"),
        "entry_time": datetime(2026, 1, 1, 12, 0, 0),
        "current_price": Decimal("40000.00")
    }


@pytest.fixture
def mock_config():
    """Mock configuration"""
    return {
        "MIN_ORDER_SIZE_USD": Decimal("1.00"),
        "MAX_POSITION_SIZE_USD": Decimal("1000.00"),
        "TP_THRESHOLD": Decimal("0.035"),  # 3.5%
        "SL_THRESHOLD": Decimal("0.045"),  # 4.5%
        "FEE_RATE": Decimal("0.01"),  # 1%
        "SCORE_BUY_TARGET": Decimal("2.0"),
        "SCORE_SELL_TARGET": Decimal("2.0"),
        "MIN_INDICATORS_REQUIRED": 3
    }


@pytest.fixture
def mock_logger():
    """Mock logger"""
    logger = MagicMock()
    logger.info = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    logger.debug = MagicMock()
    return logger


@pytest.fixture
def mock_database():
    """Mock database for isolated testing"""
    db = AsyncMock()
    db.fetch_one = AsyncMock(return_value=None)
    db.fetch_all = AsyncMock(return_value=[])
    db.execute = AsyncMock(return_value=None)
    return db


# Test data generators
def generate_ohlcv_candles(symbol="BTC-USD", count=100, start_price=40000.0):
    """Generate OHLCV candle data for testing"""
    candles = []
    price = start_price
    start_time = datetime.now() - timedelta(minutes=count)

    for i in range(count):
        # Random price movement ±1%
        import random
        change = random.uniform(-0.01, 0.01)
        price = price * (1 + change)

        candle = {
            "symbol": symbol,
            "ts": start_time + timedelta(minutes=i),
            "open": Decimal(str(round(price * 0.999, 2))),
            "high": Decimal(str(round(price * 1.001, 2))),
            "low": Decimal(str(round(price * 0.998, 2))),
            "close": Decimal(str(round(price, 2))),
            "volume": Decimal(str(random.randint(100, 1000)))
        }
        candles.append(candle)

    return candles


def generate_trade_sequence(symbol="BTC-USD", pairs=5):
    """Generate buy/sell trade pairs for FIFO testing"""
    trades = []
    base_price = 40000.0
    start_time = datetime.now() - timedelta(days=pairs)

    for i in range(pairs):
        # Buy trade
        buy_price = base_price * (1 + (i * 0.01))  # Incrementing price
        trades.append({
            "order_id": f"buy-{i:03d}",
            "symbol": symbol,
            "side": "buy",
            "size": Decimal("0.001"),
            "price": Decimal(str(round(buy_price, 2))),
            "filled_at": start_time + timedelta(hours=i*2),
            "status": "filled"
        })

        # Sell trade (slightly higher price for profit)
        sell_price = buy_price * 1.02
        trades.append({
            "order_id": f"sell-{i:03d}",
            "symbol": symbol,
            "side": "sell",
            "size": Decimal("0.001"),
            "price": Decimal(str(round(sell_price, 2))),
            "filled_at": start_time + timedelta(hours=i*2+1),
            "status": "filled"
        })

    return trades
