"""Tests for v2.core.types — verify dataclass behavior and enums."""

from datetime import datetime
from decimal import Decimal

from v2.core.types import (
    Candle,
    CandleEvent,
    Direction,
    Fill,
    FillEvent,
    Order,
    OrderStatus,
    OrderType,
    Portfolio,
    Position,
    Side,
    Signal,
    SignalEvent,
)


class TestEnums:
    def test_direction_values(self):
        assert Direction.BUY.value == "buy"
        assert Direction.SELL.value == "sell"
        assert Direction.HOLD.value == "hold"

    def test_side_values(self):
        assert Side.BUY.value == "buy"
        assert Side.SELL.value == "sell"

    def test_order_type_values(self):
        assert OrderType.LIMIT.value == "limit"
        assert OrderType.MARKET.value == "market"
        assert OrderType.STOP.value == "stop"

    def test_order_status_values(self):
        assert OrderStatus.PENDING.value == "pending"
        assert OrderStatus.FILLED.value == "filled"
        assert OrderStatus.PARTIALLY_FILLED.value == "partially_filled"


class TestCandle:
    def test_create_candle(self):
        ts = datetime(2026, 1, 1, 12, 0, 0)
        c = Candle(
            symbol="BTC-USD", timestamp=ts,
            open=100.0, high=105.0, low=99.0, close=103.0,
            volume=1000.0, timeframe="4h",
        )
        assert c.symbol == "BTC-USD"
        assert c.timeframe == "4h"
        assert c.close == 103.0

    def test_candle_is_frozen(self):
        ts = datetime(2026, 1, 1)
        c = Candle(symbol="X", timestamp=ts, open=1, high=2, low=0, close=1, volume=1)
        try:
            c.close = 999
            assert False, "Should have raised"
        except AttributeError:
            pass

    def test_default_timeframe(self):
        ts = datetime(2026, 1, 1)
        c = Candle(symbol="X", timestamp=ts, open=1, high=2, low=0, close=1, volume=1)
        assert c.timeframe == "1m"


class TestSignal:
    def test_signal_defaults(self):
        ts = datetime(2026, 1, 1)
        s = Signal(direction=Direction.BUY, symbol="BTC-USD", timestamp=ts)
        assert s.order_type == OrderType.LIMIT
        assert s.reason == ""
        assert s.metadata == {}
        assert s.price is None


class TestOrder:
    def test_order_creation(self):
        ts = datetime(2026, 1, 1)
        o = Order(
            order_id="abc123", symbol="BTC-USD", side=Side.BUY,
            order_type=OrderType.LIMIT, price=Decimal("97500"),
            qty=Decimal("0.001"), status=OrderStatus.PENDING,
            timestamp=ts,
        )
        assert o.order_id == "abc123"
        assert o.price == Decimal("97500")


class TestFill:
    def test_fill_creation(self):
        ts = datetime(2026, 1, 1)
        f = Fill(
            fill_id="f1", order_id="o1", symbol="BTC-USD",
            side=Side.BUY, price=Decimal("97500"), qty=Decimal("0.001"),
            fee=Decimal("0.585"), fee_currency="USD", is_maker=True,
            timestamp=ts,
        )
        assert f.is_maker is True
        assert f.fee == Decimal("0.585")


class TestPortfolio:
    def test_empty_portfolio(self):
        p = Portfolio(cash_balance=Decimal("10000"))
        assert len(p.positions) == 0
        assert p.total_equity == Decimal("0")

    def test_portfolio_with_position(self):
        pos = Position(
            symbol="BTC-USD", qty=Decimal("0.1"),
            avg_entry_price=Decimal("97000"),
            cost_basis=Decimal("9700"),
        )
        p = Portfolio(
            cash_balance=Decimal("300"),
            positions={"BTC-USD": pos},
            total_equity=Decimal("10000"),
        )
        assert "BTC-USD" in p.positions
        assert p.positions["BTC-USD"].qty == Decimal("0.1")


class TestEvents:
    def test_candle_event_wraps_candle(self):
        ts = datetime(2026, 1, 1)
        c = Candle(symbol="X", timestamp=ts, open=1, high=2, low=0, close=1, volume=1)
        e = CandleEvent(candle=c)
        assert e.candle is c

    def test_signal_event_includes_strategy_name(self):
        ts = datetime(2026, 1, 1)
        s = Signal(direction=Direction.BUY, symbol="BTC-USD", timestamp=ts)
        e = SignalEvent(signal=s, strategy_name="test_strategy")
        assert e.strategy_name == "test_strategy"

    def test_fill_event(self):
        ts = datetime(2026, 1, 1)
        f = Fill(
            fill_id="f1", order_id="o1", symbol="BTC-USD",
            side=Side.BUY, price=Decimal("100"), qty=Decimal("1"),
            fee=Decimal("0.1"), fee_currency="USD", is_maker=True,
            timestamp=ts,
        )
        e = FillEvent(fill=f)
        assert e.fill.fill_id == "f1"
