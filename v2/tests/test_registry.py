"""Tests for v2.core.registry."""

from decimal import Decimal
from typing import Any

import pytest

from v2.core import registry
from v2.core.event_bus import EventBus
from v2.core.interfaces import (
    ExchangeAdapter,
    Observer,
    RiskManager,
    Strategy,
)
from v2.core.types import (
    Candle,
    Fill,
    Order,
    Portfolio,
    Position,
    Signal,
    TickerEvent,
)


# ---------------------------------------------------------------------------
# Fake plugins for testing
# ---------------------------------------------------------------------------

class FakeExchange(ExchangeAdapter):
    name = "fake"

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def connect(self): pass
    async def disconnect(self): pass
    async def submit_order(self, order): return order
    async def cancel_order(self, order_id): return True
    async def get_order(self, order_id): pass
    async def get_open_orders(self, symbol=None): return []
    async def get_balances(self): return {}
    async def get_fee_rates(self): return {}
    async def get_ticker(self, symbol): pass


class FakeStrategy(Strategy):
    name = "fake_strat"
    version = "1.0"

    def configure(self, config): pass
    def warmup_bars(self): return {}
    def on_candle(self, candle, indicators): return None


class FakeRisk(RiskManager):
    name = "fake_risk"

    def configure(self, config): pass
    def check_signal(self, signal, portfolio): return signal


class FakeObserver(Observer):
    name = "fake_obs"

    def on_event(self, event): pass


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRegister:
    def setup_method(self):
        registry.clear()

    def test_register_and_create(self):
        registry.register("exchange", "fake", FakeExchange)
        ex = registry.create_exchange("fake")
        assert isinstance(ex, FakeExchange)

    def test_register_wrong_category_raises(self):
        with pytest.raises(ValueError, match="Unknown plugin category"):
            registry.register("invalid_category", "x", FakeExchange)

    def test_register_wrong_abc_raises(self):
        with pytest.raises(TypeError, match="does not implement"):
            registry.register("exchange", "bad", FakeStrategy)

    def test_create_unknown_name_raises(self):
        with pytest.raises(KeyError, match="No 'exchange' plugin named 'missing'"):
            registry.create_exchange("missing")


class TestPluginDecorator:
    def setup_method(self):
        registry.clear()

    def test_decorator_registers_class(self):
        @registry.plugin("observer", "test_obs")
        class TestObserver(Observer):
            name = "test_obs"
            def on_event(self, event): pass

        obs = registry.create_observer("test_obs")
        assert isinstance(obs, TestObserver)


class TestFactoryKwargs:
    def setup_method(self):
        registry.clear()

    def test_kwargs_passed_to_constructor(self):
        registry.register("exchange", "fake", FakeExchange)
        ex = registry.create_exchange("fake", event_bus="bus", api_key="123")
        assert ex.kwargs["event_bus"] == "bus"
        assert ex.kwargs["api_key"] == "123"


class TestListAndClear:
    def setup_method(self):
        registry.clear()

    def test_list_plugins_empty(self):
        result = registry.list_plugins()
        assert all(len(v) == 0 for v in result.values())

    def test_list_plugins_after_register(self):
        registry.register("exchange", "fake", FakeExchange)
        registry.register("strategy", "fake_strat", FakeStrategy)

        result = registry.list_plugins()
        assert "fake" in result["exchange"]
        assert "fake_strat" in result["strategy"]

    def test_list_plugins_by_category(self):
        registry.register("exchange", "fake", FakeExchange)
        result = registry.list_plugins("exchange")
        assert "exchange" in result
        assert "strategy" not in result

    def test_clear_removes_all(self):
        registry.register("exchange", "fake", FakeExchange)
        registry.clear()
        result = registry.list_plugins()
        assert all(len(v) == 0 for v in result.values())
