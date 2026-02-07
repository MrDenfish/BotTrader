"""Tests for Milestone 2 backtest plugins."""

import asyncio
from datetime import datetime
from decimal import Decimal

import pytest

from v2.core import registry
from v2.core.event_bus import EventBus
from v2.core.types import (
    Fill,
    Order,
    OrderStatus,
    OrderType,
    Side,
)


class TestBacktestExchange:
    def setup_method(self):
        registry.discover_plugins()

    @pytest.mark.asyncio
    async def test_connect_disconnect(self):
        ex = registry.create_exchange("backtest", event_bus=EventBus())
        await ex.connect()
        await ex.disconnect()

    @pytest.mark.asyncio
    async def test_balances(self):
        ex = registry.create_exchange(
            "backtest", event_bus=EventBus(), initial_balance_usd=5000.0
        )
        await ex.connect()
        balances = await ex.get_balances()
        assert balances["USD"] == Decimal("5000.0")
        await ex.disconnect()

    @pytest.mark.asyncio
    async def test_fee_rates(self):
        ex = registry.create_exchange("backtest", event_bus=EventBus())
        await ex.connect()
        fees = await ex.get_fee_rates()
        assert "maker" in fees
        assert "taker" in fees
        await ex.disconnect()

    @pytest.mark.asyncio
    async def test_submit_and_get_order(self):
        ex = registry.create_exchange("backtest", event_bus=EventBus())
        await ex.connect()

        order = Order(
            order_id="test-1", symbol="BTC-USD", side=Side.BUY,
            order_type=OrderType.LIMIT, price=Decimal("97000"),
            qty=Decimal("0.001"), status=OrderStatus.PENDING,
            timestamp=datetime.now(),
        )
        result = await ex.submit_order(order)
        assert result.order_id == "test-1"

        retrieved = await ex.get_order("test-1")
        assert retrieved is not None
        assert retrieved.order_id == "test-1"

        await ex.disconnect()


class TestSqliteStorage:
    def setup_method(self):
        registry.discover_plugins()

    @pytest.mark.asyncio
    async def test_connect_disconnect(self):
        store = registry.create_storage("sqlite", event_bus=EventBus())
        await store.connect()
        await store.disconnect()

    @pytest.mark.asyncio
    async def test_record_and_get_fill(self):
        store = registry.create_storage("sqlite", event_bus=EventBus())
        await store.connect()

        fill = Fill(
            fill_id="f1", order_id="o1", symbol="BTC-USD",
            side=Side.BUY, price=Decimal("97000"), qty=Decimal("0.001"),
            fee=Decimal("0.582"), fee_currency="USD", is_maker=True,
            timestamp=datetime(2026, 1, 15, 12, 0, 0),
        )
        await store.record_fill(fill)

        trades = await store.get_trades(symbol="BTC-USD")
        assert len(trades) == 1
        assert trades[0].fill_id == "f1"
        assert trades[0].price == Decimal("97000")

        await store.disconnect()

    @pytest.mark.asyncio
    async def test_save_and_load_state(self):
        store = registry.create_storage("sqlite", event_bus=EventBus())
        await store.connect()

        await store.save_state("test_key", {"foo": "bar", "count": 42})
        state = await store.load_state("test_key")
        assert state["foo"] == "bar"
        assert state["count"] == 42

        missing = await store.load_state("nonexistent")
        assert missing is None

        await store.disconnect()


class TestBasicRiskManager:
    def setup_method(self):
        registry.discover_plugins()

    def test_pass_through(self):
        from v2.core.types import Direction, Signal, Portfolio

        risk = registry.create_risk("basic", event_bus=EventBus())
        portfolio = Portfolio(cash_balance=Decimal("10000"))

        sig = Signal(
            direction=Direction.BUY, symbol="BTC-USD",
            timestamp=datetime.now(), price=97000.0,
        )
        result = risk.check_signal(sig, portfolio)
        assert result is sig  # pass-through


class TestMakerOnlyExecution:
    def setup_method(self):
        registry.discover_plugins()

    @pytest.mark.asyncio
    async def test_noop_in_backtest(self):
        from v2.core.types import Direction, Signal

        execution = registry.create_execution("maker_only", event_bus=EventBus())
        exchange = registry.create_exchange("backtest", event_bus=EventBus())

        sig = Signal(
            direction=Direction.BUY, symbol="BTC-USD",
            timestamp=datetime.now(), price=97000.0,
        )
        result = await execution.execute_signal(sig, exchange)
        assert result is None


class TestStructuredLogObserver:
    def setup_method(self):
        registry.discover_plugins()

    def test_handles_signal_event(self):
        from v2.core.types import Direction, Signal, SignalEvent

        obs = registry.create_observer("structured_log", event_bus=EventBus())
        sig = Signal(
            direction=Direction.BUY, symbol="BTC-USD",
            timestamp=datetime.now(), price=97000.0, reason="TEST",
        )
        # Should not raise
        obs.on_event(SignalEvent(signal=sig, strategy_name="test"))

    def test_handles_unknown_event(self):
        obs = registry.create_observer("structured_log", event_bus=EventBus())
        obs.on_event({"arbitrary": "event"})  # Should not raise


class TestPluginDiscovery:
    def setup_method(self):
        registry.discover_plugins()

    def test_all_milestone2_plugins_registered(self):
        plugins = registry.list_plugins()
        assert "backtest" in plugins["exchange"]
        assert "csv_replay" in plugins["data"]
        assert "hybrid_4h_maker" in plugins["strategy"]
        assert "basic" in plugins["risk"]
        assert "maker_only" in plugins["execution"]
        assert "sqlite" in plugins["storage"]
        assert "structured_log" in plugins["observer"]
