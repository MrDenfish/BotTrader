"""Application lifecycle — discover, configure, wire, start, run, stop."""

from __future__ import annotations

import asyncio
import logging
import signal
from decimal import Decimal
from pathlib import Path
from typing import Any

from v2.core.config import Config
from v2.core.event_bus import EventBus
from v2.core import registry
from v2.core.types import (
    CandleEvent,
    FillEvent,
    Portfolio,
    SignalEvent,
    TickerEvent,
)

logger = logging.getLogger(__name__)


class App:
    """Main application — discovers plugins, wires events, runs the loop."""

    def __init__(self, config_path: str | Path | None = None, overrides: dict | None = None) -> None:
        self.config_path = config_path
        self.overrides = overrides
        self.config: Config | None = None
        self.bus = EventBus()
        self.portfolio = Portfolio(cash_balance=Decimal("0"))
        self._shutdown = asyncio.Event()

        # Plugin instances (populated in _setup)
        self._exchange = None
        self._data_providers: list = []
        self._strategies: list = []
        self._risk = None
        self._execution = None
        self._storage = None
        self._observers: list = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Full lifecycle: setup → run → teardown."""
        self.config = Config.load(self.config_path, self.overrides)
        logger.info(
            "Starting BotTrader v2 — mode=%s, symbols=%s",
            self.config.app.mode,
            self.config.app.symbols,
        )

        # Discover plugins from v2.plugins
        registry.discover_plugins()

        await self._setup()
        try:
            await self._run_loop()
        finally:
            await self._teardown()

    # ------------------------------------------------------------------
    # Setup: instantiate plugins + wire events
    # ------------------------------------------------------------------

    async def _setup(self) -> None:
        cfg = self.config

        # Instantiate plugins
        self._exchange = registry.create_exchange(
            cfg.exchange.type, event_bus=self.bus, **cfg.exchange.config
        )
        self._data_providers = [
            registry.create_data(dp.type, event_bus=self.bus, **dp.config)
            for dp in cfg.data_providers
        ]
        self._strategies = [
            registry.create_strategy(s.type, event_bus=self.bus, **s.config)
            for s in cfg.strategies
        ]
        if cfg.risk.type:
            self._risk = registry.create_risk(
                cfg.risk.type, event_bus=self.bus, **cfg.risk.config
            )
        if cfg.execution.type:
            self._execution = registry.create_execution(
                cfg.execution.type, event_bus=self.bus, **cfg.execution.config
            )
        if cfg.storage.type:
            self._storage = registry.create_storage(
                cfg.storage.type, event_bus=self.bus, **cfg.storage.config
            )
        self._observers = [
            registry.create_observer(o.type, event_bus=self.bus, **o.config)
            for o in cfg.observers
        ]

        # Wire event subscriptions
        self._wire_events()

        # Connect external services
        await self._exchange.connect()
        if self._storage:
            await self._storage.connect()

        # Configure and start strategies
        for s in self._strategies:
            # Find matching strategy config from the config list
            matching = [
                ref for ref in cfg.strategies if ref.type == s.name
            ]
            if matching:
                s.configure(matching[0].config)
            s.start()

        # Start data providers
        for dp in self._data_providers:
            await dp.start(cfg.app.symbols)

        logger.info("Setup complete — %d strategies, %d data providers",
                     len(self._strategies), len(self._data_providers))

    def _wire_events(self) -> None:
        """Subscribe plugins to appropriate event types."""
        bus = self.bus

        # Strategies receive candles, fills, tickers
        for strategy in self._strategies:
            bus.subscribe(CandleEvent, lambda e, s=strategy: self._on_candle(s, e))
            bus.subscribe(FillEvent, lambda e, s=strategy: s.on_fill(e.fill))
            bus.subscribe(TickerEvent, lambda e, s=strategy: s.on_ticker(e))

        # Risk manager checks signals
        if self._risk:
            bus.subscribe(
                SignalEvent,
                lambda e: self._on_signal_risk_check(e),
            )

        # Persistence receives fills and orders
        if self._storage:
            bus.subscribe(FillEvent, lambda e: self._on_fill_storage(e))

        # Portfolio updates on fills
        bus.subscribe(FillEvent, lambda e: self._on_fill_portfolio(e))

        # Observers get everything
        for observer in self._observers:
            bus.subscribe_all(observer.on_event)

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _on_candle(self, strategy: Any, event: CandleEvent) -> None:
        """Route candle to strategy, publish signal if produced."""
        result = strategy.on_candle(event.candle, indicators={})
        if result is not None:
            self.bus.publish(SignalEvent(signal=result, strategy_name=strategy.name))

    def _on_signal_risk_check(self, event: SignalEvent) -> None:
        """Risk manager vets signal, then forward to execution."""
        approved = self._risk.check_signal(event.signal, self.portfolio)
        if approved and self._execution:
            asyncio.ensure_future(
                self._execution.execute_signal(approved, self._exchange)
            )

    def _on_fill_storage(self, event: FillEvent) -> None:
        if self._storage:
            asyncio.ensure_future(self._storage.record_fill(event.fill))

    def _on_fill_portfolio(self, event: FillEvent) -> None:
        """Update portfolio position from fill."""
        fill = event.fill
        symbol = fill.symbol
        side_sign = Decimal("1") if fill.side.value == "buy" else Decimal("-1")
        delta_qty = fill.qty * side_sign

        if symbol in self.portfolio.positions:
            pos = self.portfolio.positions[symbol]
            pos.qty += delta_qty
        else:
            from v2.core.types import Position
            self.portfolio.positions[symbol] = Position(
                symbol=symbol,
                qty=delta_qty,
                avg_entry_price=fill.price,
                cost_basis=fill.price * fill.qty,
                entry_time=fill.timestamp,
            )

    # ------------------------------------------------------------------
    # Run loop + shutdown
    # ------------------------------------------------------------------

    async def _run_loop(self) -> None:
        """Block until shutdown is requested."""
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self._request_shutdown)
        logger.info("Running — press Ctrl+C to stop")
        await self._shutdown.wait()
        logger.info("Shutdown requested")

    def _request_shutdown(self) -> None:
        self._shutdown.set()

    async def _teardown(self) -> None:
        """Stop plugins in reverse order."""
        for strategy in self._strategies:
            strategy.stop()
        for dp in self._data_providers:
            await dp.stop()
        if self._storage:
            await self._storage.disconnect()
        await self._exchange.disconnect()
        logger.info("Teardown complete")
