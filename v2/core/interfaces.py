"""Plugin abstract base classes.

Each interface defines one job. A plugin implements exactly one interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from decimal import Decimal
from typing import Any

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
# ExchangeAdapter
# ---------------------------------------------------------------------------

class ExchangeAdapter(ABC):
    """Connects to an exchange for order management and account data."""

    name: str

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def disconnect(self) -> None: ...

    # Orders
    @abstractmethod
    async def submit_order(self, order: Order) -> Order: ...

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool: ...

    @abstractmethod
    async def get_order(self, order_id: str) -> Order: ...

    @abstractmethod
    async def get_open_orders(self, symbol: str | None = None) -> list[Order]: ...

    # Account
    @abstractmethod
    async def get_balances(self) -> dict[str, Decimal]: ...

    @abstractmethod
    async def get_fee_rates(self) -> dict[str, Decimal]: ...

    # Market data (basic — full data via DataProvider plugins)
    @abstractmethod
    async def get_ticker(self, symbol: str) -> TickerEvent: ...


# ---------------------------------------------------------------------------
# DataProvider
# ---------------------------------------------------------------------------

class DataProvider(ABC):
    """Supplies market data (candles, tickers).

    Publishes CandleEvent and/or TickerEvent via the event bus.
    """

    name: str

    @abstractmethod
    async def start(self, symbols: list[str]) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------

class Strategy(ABC):
    """Trading decision logic. Receives candles, emits signals."""

    name: str
    version: str

    @abstractmethod
    def configure(self, config: Any) -> None: ...

    @abstractmethod
    def warmup_bars(self) -> dict[str, int]:
        """Return minimum bars needed per timeframe before trading."""
        ...

    def start(self) -> None:
        """Called when the app starts, after configure()."""

    def stop(self) -> None:
        """Called when the app shuts down."""

    @abstractmethod
    def on_candle(self, candle: Candle, indicators: dict) -> Signal | None: ...

    def on_fill(self, fill: Fill) -> Signal | None:
        """Called when an order fill arrives. Override if needed."""
        return None

    def on_ticker(self, ticker: TickerEvent) -> Signal | None:
        """Called on live ticker updates. Override if needed."""
        return None

    def get_state(self) -> dict:
        """Serialize internal state for persistence."""
        return {}

    def load_state(self, state: dict) -> None:
        """Restore internal state from persistence."""


# ---------------------------------------------------------------------------
# RiskManager
# ---------------------------------------------------------------------------

class RiskManager(ABC):
    """Validates signals against risk limits before execution."""

    name: str

    @abstractmethod
    def configure(self, config: Any) -> None: ...

    @abstractmethod
    def check_signal(self, signal: Signal, portfolio: Portfolio) -> Signal | None:
        """Return the signal (approved) or None (vetoed).

        May publish RiskEvent via event bus.
        """
        ...

    def on_fill(self, fill: Fill, portfolio: Portfolio) -> None:
        """Update internal state after a fill."""

    def on_position_update(self, position: Position) -> Signal | None:
        """Return an exit Signal if risk limits require closing a position."""
        return None


# ---------------------------------------------------------------------------
# ExecutionManager
# ---------------------------------------------------------------------------

class ExecutionManager(ABC):
    """Translates signals into exchange orders."""

    name: str

    @abstractmethod
    def configure(self, config: Any) -> None: ...

    @abstractmethod
    async def execute_signal(
        self, signal: Signal, exchange: ExchangeAdapter
    ) -> Order | None:
        """Handle price adjustment, sizing, TP/SL, retries."""
        ...


# ---------------------------------------------------------------------------
# StorageAdapter
# ---------------------------------------------------------------------------

class StorageAdapter(ABC):
    """Persists trades, positions, and state."""

    name: str

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def disconnect(self) -> None: ...

    @abstractmethod
    async def record_fill(self, fill: Fill) -> None: ...

    @abstractmethod
    async def record_order(self, order: Order) -> None: ...

    @abstractmethod
    async def get_positions(self, symbol: str | None = None) -> list[Position]: ...

    @abstractmethod
    async def get_trades(
        self, symbol: str | None = None, since: datetime | None = None
    ) -> list[Fill]: ...

    @abstractmethod
    async def save_state(self, key: str, state: dict) -> None: ...

    @abstractmethod
    async def load_state(self, key: str) -> dict | None: ...


# ---------------------------------------------------------------------------
# Observer
# ---------------------------------------------------------------------------

class Observer(ABC):
    """Observability: logging, metrics, alerts.

    Subscribes to all events via ``subscribe_all`` and handles each type.
    """

    name: str

    @abstractmethod
    def on_event(self, event: Any) -> None: ...


# ---------------------------------------------------------------------------
# PairDiscovery
# ---------------------------------------------------------------------------

class PairDiscovery(ABC):
    """Discovers tradeable pairs from an exchange based on volume/liquidity.

    Fetches available products, filters by volume and other criteria,
    and publishes ``SymbolsUpdatedEvent`` when the active set changes.
    """

    name: str

    @abstractmethod
    def configure(self, config: Any) -> None: ...

    @abstractmethod
    async def discover(self) -> list[str]:
        """Fetch and filter trading pairs. Returns list of symbols."""
        ...

    @abstractmethod
    async def start(self) -> None:
        """Start periodic refresh background task."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Stop refresh task and close resources."""
        ...
