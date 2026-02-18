"""Kraken WebSocket v2 data provider for live market data.

Connects to Kraken's public WebSocket v2 for real-time ticker events.
Aggregates tickers into 1-minute OHLCV candles and emits CandleEvents.
Mirrors the Coinbase ``WebSocketDataProvider`` pattern.

Public data only — no authentication required for market data.
Volume backfill uses the public REST OHLC endpoint.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp

from v2.core import registry
from v2.core.event_bus import EventBus
from v2.core.interfaces import DataProvider
from v2.core.types import Candle, CandleEvent, TickerEvent
from v2.utils.symbol_mapper import KrakenSymbolMapper

logger = logging.getLogger(__name__)

_WS_PUBLIC_URL = "wss://ws.kraken.com/v2"
_REST_API_URL = "https://api.kraken.com"


@registry.plugin("data", "kraken_websocket")
class KrakenWebSocketDataProvider(DataProvider):
    """Live WebSocket data provider using Kraken WS v2.

    Connects to the public WebSocket and subscribes to the ``ticker``
    channel. Publishes TickerEvent for each price update and aggregates
    ticks into 1-minute candles (same state machine as Coinbase provider).

    Reconnection uses exponential backoff with jitter.
    """

    name = "kraken_websocket"

    def __init__(
        self,
        event_bus: EventBus | None = None,
        key_file: str | None = None,
        ws_url: str = _WS_PUBLIC_URL,
        channels: list[str] | None = None,
        idle_timeout: float = 90.0,
        mapper: KrakenSymbolMapper | None = None,
        **kwargs: Any,
    ) -> None:
        self._bus = event_bus
        self._key_file = key_file
        self._ws_url = ws_url
        self._channels = channels or ["ticker"]
        self._idle_timeout = idle_timeout
        self._mapper = mapper or KrakenSymbolMapper()

        self._symbols: list[str] = []
        self._ws_task: asyncio.Task | None = None
        self._running = False

        # Track last message time for idle watchdog
        self._last_message_time: float = 0.0
        self._watchdog_task: asyncio.Task | None = None

        # Per-channel activity tracking for silent channel detection
        self._channel_last_seen: dict[str, float] = {}
        self._ticker_silent_threshold: float = float(kwargs.get("ticker_silent_threshold", 120.0))

        # Candle aggregation: per-symbol partial 1-minute bars
        self._candle_state: dict[str, dict] = {}
        self._candle_flush_task: asyncio.Task | None = None

        # Symbol list change flag
        self._symbols_changed = asyncio.Event()

        # Volume backfill cache: {symbol: {minute_datetime: volume_float}}
        self._volume_cache: dict[str, dict[datetime, float]] = {}
        self._volume_task: asyncio.Task | None = None

    @property
    def mapper(self) -> KrakenSymbolMapper:
        return self._mapper

    @mapper.setter
    def mapper(self, value: KrakenSymbolMapper) -> None:
        self._mapper = value

    # ------------------------------------------------------------------
    # DataProvider interface
    # ------------------------------------------------------------------

    async def start(self, symbols: list[str]) -> None:
        self._symbols = symbols
        self._running = True
        self._ws_task = asyncio.create_task(self._ws_loop())
        self._watchdog_task = asyncio.create_task(self._idle_watchdog())
        self._candle_flush_task = asyncio.create_task(self._candle_flusher())
        self._volume_task = asyncio.create_task(self._volume_fetcher())

        # Subscribe to dynamic symbol updates
        if self._bus:
            from v2.core.types import SymbolsUpdatedEvent
            self._bus.subscribe(SymbolsUpdatedEvent, self._on_symbols_updated)

        logger.info(
            "Kraken WebSocket data provider starting (symbols=%d, channels=%s)",
            len(symbols), self._channels,
        )

    async def stop(self) -> None:
        self._running = False
        for task in (self._ws_task, self._watchdog_task, self._candle_flush_task, self._volume_task):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._flush_all_candles()
        logger.info("Kraken WebSocket data provider stopped")

    def _on_symbols_updated(self, event: Any) -> None:
        """Handle dynamic symbol list changes from pair discovery."""
        new_symbols = list(event.symbols)
        if set(new_symbols) == set(self._symbols):
            return
        logger.info(
            "Kraken symbol list updated: %d -> %d (added=%d, removed=%d)",
            len(self._symbols), len(new_symbols),
            len(event.added), len(event.removed),
        )
        self._symbols = new_symbols
        self._symbols_changed.set()

    # ------------------------------------------------------------------
    # WebSocket connection loop
    # ------------------------------------------------------------------

    async def _ws_loop(self) -> None:
        """Main WebSocket loop with reconnection and backoff."""
        import websockets

        backoff = 1.0
        while self._running:
            try:
                async with websockets.connect(
                    self._ws_url,
                    ping_interval=20,
                    ping_timeout=60,
                    open_timeout=10,
                ) as ws:
                    await self._subscribe(ws)
                    logger.info("Kraken market WebSocket connected")
                    backoff = 1.0
                    self._last_message_time = time.time()

                    # Wait for first frame
                    try:
                        first = await asyncio.wait_for(ws.recv(), timeout=8.0)
                        self._last_message_time = time.time()
                        self._process_message(first)
                    except asyncio.TimeoutError:
                        logger.warning("Kraken WS: no first frame within 8s — reconnecting")
                        continue

                    async for raw_msg in ws:
                        self._last_message_time = time.time()
                        try:
                            self._process_message(raw_msg)
                        except Exception:
                            logger.exception("Error processing Kraken WS message")

                        if self._symbols_changed.is_set():
                            self._symbols_changed.clear()
                            logger.info("Kraken symbols changed — reconnecting WebSocket")
                            break

            except asyncio.CancelledError:
                break
            except Exception as e:
                if self._running:
                    jitter = random.uniform(0, backoff * 0.3)
                    logger.warning(
                        "Kraken WS disconnected: %s — reconnecting in %.1fs",
                        e, backoff + jitter,
                    )
                    await asyncio.sleep(backoff + jitter)
                    backoff = min(backoff * 2, 60.0)

    async def _subscribe(self, ws) -> None:
        """Send subscription messages for configured channels."""
        # Translate symbols to Kraken WS v2 format (BTC-USD -> BTC/USD)
        ws_symbols = [self._mapper.to_kraken_ws(s) for s in self._symbols]

        for channel in self._channels:
            msg = {
                "method": "subscribe",
                "params": {
                    "channel": channel,
                    "symbol": ws_symbols,
                },
            }
            await ws.send(json.dumps(msg))
            logger.debug("Kraken subscribed to %s for %d symbols", channel, len(ws_symbols))

    # ------------------------------------------------------------------
    # Message processing
    # ------------------------------------------------------------------

    def _process_message(self, raw_msg: str) -> None:
        """Parse and route Kraken WS v2 message."""
        try:
            data = json.loads(raw_msg)
        except json.JSONDecodeError:
            logger.warning("Invalid JSON from Kraken WS")
            return

        channel = data.get("channel", "")
        msg_type = data.get("type", "")

        # Track per-channel activity for silent channel detection
        if channel:
            self._channel_last_seen[channel] = time.time()

        if channel == "ticker" and msg_type == "update":
            self._process_ticker(data)
        elif channel == "ticker" and msg_type == "snapshot":
            self._process_ticker(data)
        elif channel == "heartbeat":
            pass  # Keep-alive
        elif channel == "status":
            status = data.get("data", [{}])[0] if data.get("data") else {}
            logger.debug("Kraken WS status: %s", status.get("system", "unknown"))
        elif msg_type == "error":
            logger.error("Kraken WS error: %s", data.get("error", "unknown"))
        elif data.get("method") == "subscribe" and data.get("success"):
            logger.debug("Kraken subscription confirmed: %s", data.get("result", {}).get("channel"))

    def _process_ticker(self, data: dict) -> None:
        """Extract ticker data from Kraken WS v2 ticker messages."""
        tickers = data.get("data", [])
        for ticker in tickers:
            ws_symbol = ticker.get("symbol", "")
            if not ws_symbol:
                continue

            # Translate to internal format
            symbol = self._mapper.from_kraken_ws(ws_symbol)

            last_str = ticker.get("last")
            if last_str is None:
                continue
            try:
                price = float(last_str)
            except (ValueError, TypeError):
                continue

            # Extract optional fields
            bid = None
            ask = None
            change_24h = None
            try:
                if "bid" in ticker:
                    bid = float(ticker["bid"])
                if "ask" in ticker:
                    ask = float(ticker["ask"])
                if "change_pct" in ticker:
                    change_24h = float(ticker["change_pct"])
            except (ValueError, TypeError):
                pass

            now = datetime.now(timezone.utc)

            if self._bus:
                self._bus.publish(TickerEvent(
                    symbol=symbol,
                    price=price,
                    timestamp=now,
                    change_24h_pct=change_24h,
                    bid=bid,
                    ask=ask,
                ))

            # Aggregate into 1-minute candle
            self._update_candle(symbol, price, now)

    # ------------------------------------------------------------------
    # 1-minute candle aggregation (same state machine as Coinbase)
    # ------------------------------------------------------------------

    def _minute_key(self, ts: datetime) -> datetime:
        """Truncate timestamp to the start of the minute."""
        return ts.replace(second=0, microsecond=0)

    def _update_candle(self, symbol: str, price: float, ts: datetime) -> None:
        """Update the current 1-minute candle bar for a symbol."""
        minute = self._minute_key(ts)
        state = self._candle_state.get(symbol)

        if state is None:
            self._candle_state[symbol] = {
                "minute": minute,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
            }
            return

        if minute > state["minute"]:
            self._emit_candle(symbol, state)
            self._candle_state[symbol] = {
                "minute": minute,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
            }
        else:
            state["high"] = max(state["high"], price)
            state["low"] = min(state["low"], price)
            state["close"] = price

    def _emit_candle(self, symbol: str, state: dict) -> None:
        """Publish a completed CandleEvent."""
        if not self._bus:
            return
        volume = 0.0
        sym_cache = self._volume_cache.get(symbol)
        if sym_cache and state["minute"] in sym_cache:
            volume = sym_cache.pop(state["minute"])
        candle = Candle(
            symbol=symbol,
            timestamp=state["minute"],
            open=state["open"],
            high=state["high"],
            low=state["low"],
            close=state["close"],
            volume=volume,
            timeframe="1m",
        )
        self._bus.publish(CandleEvent(candle=candle))
        logger.debug(
            "Kraken candle: %s %s O=%.2f H=%.2f L=%.2f C=%.2f V=%.4f",
            symbol, state["minute"].strftime("%H:%M"),
            candle.open, candle.high, candle.low, candle.close, candle.volume,
        )

    def _flush_all_candles(self) -> None:
        """Emit all pending partial candles."""
        for symbol, state in list(self._candle_state.items()):
            self._emit_candle(symbol, state)
        self._candle_state.clear()

    async def _candle_flusher(self) -> None:
        """Periodically emit completed candles at minute boundaries."""
        while self._running:
            try:
                now = datetime.now(timezone.utc)
                seconds_until_next = 60 - now.second - now.microsecond / 1_000_000
                await asyncio.sleep(seconds_until_next + 0.5)

                if not self._running:
                    break

                current_minute = self._minute_key(datetime.now(timezone.utc))
                for symbol, state in list(self._candle_state.items()):
                    if state["minute"] < current_minute:
                        self._emit_candle(symbol, state)
                        del self._candle_state[symbol]

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Error in Kraken candle flusher")

    # ------------------------------------------------------------------
    # Volume backfill via REST API (public, no auth)
    # ------------------------------------------------------------------

    async def _volume_fetcher(self) -> None:
        """Periodically fetch real OHLCV candles from Kraken REST API."""
        await asyncio.sleep(60)  # Wait for WS to stabilize

        while self._running:
            try:
                async with aiohttp.ClientSession() as session:
                    for symbol in list(self._symbols):
                        if not self._running:
                            break
                        try:
                            await self._fetch_volume_for_symbol(session, symbol)
                        except Exception:
                            logger.debug("Kraken volume fetch failed for %s", symbol, exc_info=True)
                        await asyncio.sleep(0.2)  # Rate limiting

                # Prune stale cache entries
                cutoff = datetime.now(timezone.utc) - timedelta(minutes=10)
                for sym in list(self._volume_cache.keys()):
                    cache = self._volume_cache[sym]
                    stale = [k for k in cache if k < cutoff]
                    for k in stale:
                        del cache[k]
                    if not cache:
                        del self._volume_cache[sym]

                await asyncio.sleep(65)

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Error in Kraken volume fetcher")
                await asyncio.sleep(60)

    async def _fetch_volume_for_symbol(
        self, session: aiohttp.ClientSession, symbol: str
    ) -> None:
        """Fetch 1-minute OHLC candles from Kraken public REST API."""
        rest_pair = self._mapper.to_kraken_rest(symbol)
        now = datetime.now(timezone.utc)
        since_ts = int((now - timedelta(minutes=6)).timestamp())

        url = f"{_REST_API_URL}/0/public/OHLC"
        params = {
            "pair": rest_pair,
            "interval": "1",  # 1-minute
            "since": str(since_ts),
        }

        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                logger.debug("Kraken OHLC API %d for %s", resp.status, symbol)
                return
            data = await resp.json()

        errors = data.get("error", [])
        if errors:
            logger.debug("Kraken OHLC error for %s: %s", symbol, errors)
            return

        result = data.get("result", {})
        # Result has pair name as key, plus "last" timestamp
        candles = None
        for key, value in result.items():
            if key != "last" and isinstance(value, list):
                candles = value
                break

        if not candles:
            return

        if symbol not in self._volume_cache:
            self._volume_cache[symbol] = {}

        cached = 0
        for c in candles:
            try:
                # Format: [time, open, high, low, close, vwap, volume, count]
                epoch = int(c[0])
                volume = float(c[6])
                minute_dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
                self._volume_cache[symbol][minute_dt] = volume
                cached += 1
            except (IndexError, ValueError, TypeError):
                continue

        if cached:
            logger.debug("Kraken volume cached: %s — %d candles", symbol, cached)

    # ------------------------------------------------------------------
    # Idle watchdog
    # ------------------------------------------------------------------

    async def _idle_watchdog(self) -> None:
        """Monitor for idle WebSocket and force reconnect.

        Two checks:
        1. Global idle: no messages at all for idle_timeout seconds
        2. Silent ticker: heartbeats alive but ticker channel silent
        """
        while self._running:
            try:
                await asyncio.sleep(self._idle_timeout / 2)
                if not self._running:
                    break

                now = time.time()
                should_reconnect = False
                reason = ""

                # Check 1: Global idle
                elapsed = now - self._last_message_time
                if self._last_message_time > 0 and elapsed > self._idle_timeout:
                    should_reconnect = True
                    reason = f"globally idle for {elapsed:.0f}s"

                # Check 2: Silent ticker channel
                if not should_reconnect:
                    hb_last = self._channel_last_seen.get("heartbeat", 0)
                    ticker_last = self._channel_last_seen.get("ticker", 0)
                    if ticker_last > 0 and hb_last > 0:
                        ticker_silent = now - ticker_last
                        hb_age = now - hb_last
                        if ticker_silent > self._ticker_silent_threshold and hb_age < 30:
                            should_reconnect = True
                            reason = (
                                f"ticker silent for {ticker_silent:.0f}s "
                                f"but heartbeats alive ({hb_age:.0f}s ago)"
                            )

                if should_reconnect:
                    logger.warning("Kraken WS: %s — forcing reconnect", reason)
                    if self._ws_task and not self._ws_task.done():
                        self._ws_task.cancel()
                        try:
                            await self._ws_task
                        except asyncio.CancelledError:
                            pass
                    self._channel_last_seen.clear()
                    if self._running:
                        self._ws_task = asyncio.create_task(self._ws_loop())

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Error in Kraken idle watchdog")
