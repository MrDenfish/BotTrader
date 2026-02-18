"""Application lifecycle — discover, configure, wire, start, run, stop."""

from __future__ import annotations

import asyncio
import logging
import signal
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

from v2.core.config import Config
from v2.core.event_bus import EventBus
from v2.core import registry
from v2.core.types import (
    CandleEvent,
    Direction,
    FillEvent,
    OrderEvent,
    OrderStatus,
    Portfolio,
    RiskEvent,
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
        self._pair_discovery = None
        self._exchange = None
        self._data_providers: list = []
        self._strategies: list = []
        self._risk_managers: list = []
        self._execution = None
        self._storage = None
        self._observers: list = []

        # Backtest results
        self.backtest_results: dict | None = None

        # Sell de-duplication: {symbol: timestamp} of last approved sell
        self._last_sell_approved: dict[str, float] = {}
        self._sell_dedup_window: float = 30.0  # seconds

        # Stale order check throttle
        self._last_stale_check: float = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(self) -> dict | None:
        """Full lifecycle: setup → run → teardown.

        Returns backtest results dict in backtest mode, None otherwise.
        """
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
            if self.config.app.mode == "backtest":
                self.backtest_results = self._run_backtest()
                return self.backtest_results
            else:
                await self._run_loop()
                return None
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
        self._risk_managers = [
            registry.create_risk(r.type, event_bus=self.bus, **r.config)
            for r in cfg.risk
            if r.type
        ]
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

        # Wire exchange reference to risk managers that need it (e.g. exit_manager for fee refresh)
        for rm in self._risk_managers:
            if hasattr(rm, "_exchange") and rm._exchange is None:
                rm._exchange = self._exchange

        # Wire event subscriptions
        self._wire_events()

        # Connect external services
        await self._exchange.connect()
        if self._storage:
            await self._storage.connect()

            # Restore positions from storage on startup
            try:
                positions = await self._storage.get_positions()
                restored = 0
                for pos in positions:
                    if pos.qty > 0:
                        self.portfolio.positions[pos.symbol] = pos
                        restored += 1
                if restored:
                    logger.info("Restored %d positions from storage", restored)
            except Exception:
                logger.warning("Failed to restore positions from storage", exc_info=True)

        # Pair discovery (optional — replaces static symbol list)
        if cfg.pair_discovery and cfg.pair_discovery.type:
            self._pair_discovery = registry.create_pair_discovery(
                cfg.pair_discovery.type, event_bus=self.bus, **cfg.pair_discovery.config,
            )
            # The PluginRef.config may wrap the real config under a "config" key
            inner_cfg = cfg.pair_discovery.config.get("config", cfg.pair_discovery.config)
            self._pair_discovery.configure(inner_cfg)
            try:
                discovered = await self._pair_discovery.discover()
                if discovered:
                    logger.info(
                        "Pair discovery: %d symbols (replacing %d static)",
                        len(discovered), len(cfg.app.symbols),
                    )
                    cfg.app.symbols = discovered
                else:
                    logger.warning("Pair discovery returned empty — using YAML symbols")
            except Exception:
                logger.exception("Pair discovery failed — using YAML symbols")

        # Configure and start strategies
        for s in self._strategies:
            matching = [
                ref for ref in cfg.strategies if ref.type == s.name
            ]
            if matching:
                s.configure(matching[0].config)
            s.start()

        # Start data providers
        for dp in self._data_providers:
            await dp.start(cfg.app.symbols)

        # Start pair discovery refresh loop (after data providers are running)
        if self._pair_discovery:
            await self._pair_discovery.start()

        logger.info("Setup complete — %d strategies, %d data providers",
                     len(self._strategies), len(self._data_providers))

    def _wire_events(self) -> None:
        """Subscribe plugins to appropriate event types."""
        bus = self.bus

        # Strategies receive candles, fills, tickers, and (optionally) risk events
        for strategy in self._strategies:
            bus.subscribe(CandleEvent, lambda e, s=strategy: self._on_candle(s, e))
            bus.subscribe(FillEvent, lambda e, s=strategy: s.on_fill(e.fill))
            bus.subscribe(TickerEvent, lambda e, s=strategy: s.on_ticker(e))
            if hasattr(strategy, "on_risk_event"):
                bus.subscribe(RiskEvent, lambda e, s=strategy: s.on_risk_event(e))

        # Risk managers check signals, receive fills, track rejections, and
        # (optionally) monitor tickers for dynamic exits
        if self._risk_managers:
            bus.subscribe(SignalEvent, lambda e: self._on_signal_risk_check(e))
            bus.subscribe(FillEvent, lambda e: self._on_fill_risk(e))
            bus.subscribe(OrderEvent, lambda e: self._on_order_rejection(e))
            for rm in self._risk_managers:
                if hasattr(rm, "on_ticker"):
                    bus.subscribe(TickerEvent, lambda e, r=rm: r.on_ticker(e, self.portfolio))
                if hasattr(rm, "on_candle"):
                    bus.subscribe(CandleEvent, lambda e, r=rm: r.on_candle(e.candle))

        # Execution: forward fills/orders for stale order tracking
        if self._execution and hasattr(self._execution, "on_fill"):
            bus.subscribe(FillEvent, lambda e: self._execution.on_fill(e.fill.order_id))
        if self._execution and hasattr(self._execution, "on_order_event"):
            bus.subscribe(OrderEvent, lambda e: self._execution.on_order_event(e.order))

        # Execution: ticker-driven stale order check (throttled)
        if self._execution and hasattr(self._execution, "check_stale_orders"):
            bus.subscribe(TickerEvent, lambda e: self._check_stale_orders())

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
        """Chain signal through all risk managers, then forward to execution."""
        approved = event.signal

        # De-duplicate sell signals: only one sell per symbol within the window
        if approved.direction == Direction.SELL:
            now = time.monotonic()
            last = self._last_sell_approved.get(approved.symbol, 0.0)
            if now - last < self._sell_dedup_window:
                logger.debug(
                    "Sell de-dup: %s already sold %.1fs ago, skipping",
                    approved.symbol, now - last,
                )
                return

        for rm in self._risk_managers:
            approved = rm.check_signal(approved, self.portfolio)
            if approved is None:
                return

        if approved and self._execution:
            if approved.direction == Direction.SELL:
                self._last_sell_approved[approved.symbol] = time.monotonic()
            asyncio.ensure_future(
                self._execution.execute_signal(approved, self._exchange)
            )

    def _on_fill_risk(self, event: FillEvent) -> None:
        """Forward fills to all risk managers for tracking."""
        for rm in self._risk_managers:
            rm.on_fill(event.fill, self.portfolio)

    def _on_order_rejection(self, event: OrderEvent) -> None:
        """Forward order rejections to risk managers that track them."""
        if event.order.status == OrderStatus.REJECTED:
            for rm in self._risk_managers:
                if hasattr(rm, "on_rejection"):
                    rm.on_rejection()

    def _on_fill_storage(self, event: FillEvent) -> None:
        if self._storage:
            asyncio.ensure_future(self._storage.record_fill(event.fill))

    def _on_fill_portfolio(self, event: FillEvent) -> None:
        """Update portfolio position from fill and persist to storage."""
        fill = event.fill
        symbol = fill.symbol
        side_sign = Decimal("1") if fill.side.value == "buy" else Decimal("-1")
        delta_qty = fill.qty * side_sign

        if symbol in self.portfolio.positions:
            pos = self.portfolio.positions[symbol]
            pos.qty += delta_qty
        else:
            from v2.core.types import Position
            pos = Position(
                symbol=symbol,
                qty=delta_qty,
                avg_entry_price=fill.price,
                cost_basis=fill.price * fill.qty,
                entry_time=fill.timestamp,
            )
            self.portfolio.positions[symbol] = pos

        # Persist updated position to storage
        if self._storage and hasattr(self._storage, "save_position"):
            pos = self.portfolio.positions[symbol]
            asyncio.ensure_future(self._storage.save_position(pos))

    def _check_stale_orders(self) -> None:
        """Throttled stale order check — runs at most every 30 seconds."""
        now = time.monotonic()
        if now - self._last_stale_check < 30:
            return
        self._last_stale_check = now
        asyncio.ensure_future(
            self._execution.check_stale_orders(self._exchange)
        )

    # ------------------------------------------------------------------
    # Backtest mode
    # ------------------------------------------------------------------

    def _run_backtest(self) -> dict:
        """Synchronous bar-by-bar backtest.

        In backtest mode, the CsvReplayProvider has pre-loaded and pre-
        computed all data/indicators. We iterate bar-by-bar synchronously,
        calling each strategy's ``on_backtest_bar()`` method directly.
        This bypasses the async event loop for deterministic execution.
        """
        from v2.plugins.data.csv_replay import CsvReplayProvider, BacktestCandleEvent

        cfg = self.config
        results = {}

        # Find the CSV replay provider
        csv_providers = [
            dp for dp in self._data_providers
            if isinstance(dp, CsvReplayProvider)
        ]
        if not csv_providers:
            logger.error("No csv_replay data provider configured for backtest")
            return {"error": "No csv_replay data provider"}

        provider = csv_providers[0]

        for symbol in cfg.app.symbols:
            logger.info("Backtesting %s...", symbol)

            df = provider._aligned_data.get(symbol)
            if df is None:
                logger.warning("%s: no data loaded — skipping", symbol)
                results[symbol] = {"error": "No data"}
                continue

            ts_4h = provider._4h_timestamps.get(symbol, set())
            ts_1d = provider._1d_timestamps.get(symbol, set())
            total_bars = len(df)

            for idx, (ts, row) in enumerate(df.iterrows()):
                from v2.core.types import Candle
                candle = Candle(
                    symbol=symbol,
                    timestamp=ts.to_pydatetime(),
                    open=row["open"],
                    high=row["high"],
                    low=row["low"],
                    close=row["close"],
                    volume=row.get("volume", 0.0),
                    timeframe=provider._resolution,
                )

                indicators = {
                    "4h": {
                        "atr_pct": row.get("atr_pct_4h", 0),
                        "donch_high_prev": row.get("donch_high_prev_4h", 0),
                        "bb_width_pct": row.get("bb_width_pct_4h", 0),
                        "bb_width": row.get("bb_width_4h", 0),
                        "roc_score_4h": row.get("roc_score_4h", 0),
                    },
                    "1D": {
                        "ema200": row.get("ema200_1d", 0),
                        "ema50": row.get("ema50_1d", 0),
                        "ema200_slope": row.get("ema200_slope_1d", 0),
                        "ema50_slope": row.get("ema50_slope_1d", 0),
                    },
                }

                context = {
                    "is_4h_close": ts in ts_4h,
                    "is_1d_close": ts in ts_1d,
                }

                # Call each strategy
                for strategy in self._strategies:
                    if hasattr(strategy, "on_backtest_bar"):
                        sig = strategy.on_backtest_bar(
                            symbol, candle, indicators, context,
                        )
                        if sig is not None:
                            self.bus.publish(
                                SignalEvent(signal=sig, strategy_name=strategy.name)
                            )

                if (idx + 1) % 50_000 == 0:
                    logger.info("  %s: %d / %d bars", symbol, idx + 1, total_bars)

            logger.info("  %s: complete (%d bars)", symbol, total_bars)

            # Collect results from strategies
            for strategy in self._strategies:
                if hasattr(strategy, "get_statistics"):
                    results[symbol] = strategy.get_statistics()

        # If single symbol, return flat
        if len(results) == 1:
            return list(results.values())[0]
        return results

    # ------------------------------------------------------------------
    # Run loop + shutdown (live/paper mode)
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
        if self._pair_discovery:
            await self._pair_discovery.stop()
        for strategy in self._strategies:
            strategy.stop()
        for dp in self._data_providers:
            await dp.stop()
        if self._storage:
            await self._storage.disconnect()
        await self._exchange.disconnect()
        logger.info("Teardown complete")
