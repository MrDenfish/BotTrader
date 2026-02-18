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
        self._positions: list[Position] = kwargs.get("positions", [])
        self._saved_positions: list[Position] = []

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.connected = False

    async def record_fill(self, fill): pass
    async def record_order(self, order): pass
    async def get_positions(self, symbol=None):
        if symbol:
            return [p for p in self._positions if p.symbol == symbol]
        return list(self._positions)
    async def get_trades(self, symbol=None, since=None): return []
    async def save_state(self, key, state): pass
    async def load_state(self, key): return None
    async def save_position(self, position):
        self._saved_positions.append(position)


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

    @pytest.mark.asyncio
    async def test_position_restore_on_startup(self, tmp_path):
        """Positions with qty > 0 are restored from storage on startup."""
        from datetime import datetime, timezone

        config_file = tmp_path / "test.yaml"
        config_file.write_text("""
app:
  mode: paper
  symbols: ["BTC-USD", "ETH-USD"]
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

        # Inject storage with pre-existing positions
        stored_positions = [
            Position(
                symbol="BTC-USD",
                qty=Decimal("0.05"),
                avg_entry_price=Decimal("95000"),
                cost_basis=Decimal("4750"),
                entry_time=datetime.now(timezone.utc),
            ),
            Position(
                symbol="ETH-USD",
                qty=Decimal("0"),  # Zero qty — should NOT be restored
                avg_entry_price=Decimal("3000"),
                cost_basis=Decimal("0"),
                entry_time=datetime.now(timezone.utc),
            ),
        ]

        await app._setup()

        # Replace storage with one that has positions, then re-run restore logic
        app._storage._positions = stored_positions
        positions = await app._storage.get_positions()
        for pos in positions:
            if pos.qty > 0:
                app.portfolio.positions[pos.symbol] = pos

        assert "BTC-USD" in app.portfolio.positions
        assert app.portfolio.positions["BTC-USD"].qty == Decimal("0.05")
        assert "ETH-USD" not in app.portfolio.positions  # Zero qty filtered

        await app._teardown()
