"""Tests for v2.core.app.App lifecycle.

Verifies the app can start and shut down cleanly with minimal/no plugins.
"""

import asyncio
from decimal import Decimal
from typing import Any

import pytest

from v2.core import registry
from v2.core.app import App
from v2.core.interfaces import (
    DataProvider,
    ExchangeAdapter,
    ExecutionManager,
    RiskManager,
    StorageAdapter,
)
from v2.core.types import (
    Fill,
    Order,
    Portfolio,
    Position,
    Signal,
    TickerEvent,
)


# ---------------------------------------------------------------------------
# Minimal plugin stubs for lifecycle testing
# ---------------------------------------------------------------------------

class StubExchange(ExchangeAdapter):
    name = "stub"

    def __init__(self, **kwargs):
        self.connected = False
        self.disconnected = False

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.disconnected = True

    async def submit_order(self, order):
        return order

    async def cancel_order(self, order_id):
        return True

    async def get_order(self, order_id):
        return None

    async def get_open_orders(self, symbol=None):
        return []

    async def get_balances(self):
        return {"USD": Decimal("10000")}

    async def get_fee_rates(self):
        return {"maker": Decimal("0.006"), "taker": Decimal("0.012")}

    async def get_ticker(self, symbol):
        return None


class StubStorage(StorageAdapter):
    name = "stub"

    def __init__(self, **kwargs):
        self.connected = False

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.connected = False

    async def record_fill(self, fill): pass
    async def record_order(self, order): pass
    async def get_positions(self, symbol=None): return []
    async def get_trades(self, symbol=None, since=None): return []
    async def save_state(self, key, state): pass
    async def load_state(self, key): return None


class StubRisk(RiskManager):
    name = "stub"

    def __init__(self, **kwargs):
        pass

    def configure(self, config): pass

    def check_signal(self, signal, portfolio):
        return signal


class StubExecution(ExecutionManager):
    name = "stub"

    def __init__(self, **kwargs):
        pass

    def configure(self, config): pass

    async def execute_signal(self, signal, exchange):
        return None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestAppLifecycle:
    def setup_method(self):
        registry.clear()
        registry.register("exchange", "stub", StubExchange)
        registry.register("storage", "stub", StubStorage)
        registry.register("risk", "stub", StubRisk)
        registry.register("execution", "stub", StubExecution)

    @pytest.mark.asyncio
    async def test_app_starts_and_stops_cleanly(self, tmp_path):
        config_file = tmp_path / "test.yaml"
        config_file.write_text("""
app:
  mode: backtest
  symbols: ["BTC-USD"]
exchange:
  type: stub
risk:
  type: stub
execution:
  type: stub
storage:
  type: stub
""")
        app = App(config_path=str(config_file))
        app.config = app.config  # will be loaded in run

        # Manually run setup + teardown instead of full run loop
        from v2.core.config import Config
        app.config = Config.load(str(config_file))
        await app._setup()
        assert app._exchange.connected is True
        assert app._storage.connected is True

        await app._teardown()
        assert app._exchange.disconnected is True
        assert app._storage.connected is False

    @pytest.mark.asyncio
    async def test_app_no_optional_plugins(self, tmp_path):
        """App works with just an exchange (no strategies, storage, etc.)."""
        config_file = tmp_path / "test.yaml"
        config_file.write_text("""
app:
  mode: paper
  symbols: ["BTC-USD"]
exchange:
  type: stub
risk:
  type: stub
execution:
  type: stub
storage:
  type: stub
""")
        app = App(config_path=str(config_file))
        from v2.core.config import Config
        app.config = Config.load(str(config_file))
        await app._setup()
        await app._teardown()
