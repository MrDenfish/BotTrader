"""WebSocket data provider for live market data.

Connects to Coinbase Advanced Trade WebSocket for real-time
ticker_batch events. Emits TickerEvents on the event bus.
Aggregates tickers into 1-minute OHLCV candles and emits CandleEvents.

Ported from webhook/listener.py and webhook/websocket_helper.py.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from v2.core import registry
from v2.core.event_bus import EventBus
from v2.core.interfaces import DataProvider
from v2.core.types import Candle, CandleEvent, TickerEvent

logger = logging.getLogger(__name__)


@registry.plugin("data", "websocket")
class WebSocketDataProvider(DataProvider):
    """Live WebSocket data provider using Coinbase Advanced Trade WS.

    Connects to the market WebSocket and subscribes to ``ticker_batch``
    and ``heartbeats`` channels. Publishes TickerEvent for each price
    update.

    Reconnection uses exponential backoff with jitter, matching the
    v1 listener.py behavior.
    """

    name = "websocket"

    def __init__(
        self,
        event_bus: EventBus | None = None,
        api_key: str | None = None,
        api_secret: str | None = None,
        api_key_env: str = "COINBASE_API_KEY",
        api_secret_env: str = "COINBASE_API_SECRET",
        key_file: str | None = None,
        ws_url: str = "wss://advanced-trade-ws.coinbase.com",
        channels: list[str] | None = None,
        idle_timeout: float = 90.0,
        **kwargs: Any,
    ) -> None:
        from v2.utils.credentials import resolve_credentials

        self._bus = event_bus
        self._api_key, self._api_secret = resolve_credentials(
            api_key, api_secret, api_key_env, api_secret_env, key_file,
        )
        self._ws_url = ws_url
        self._channels = channels or ["ticker_batch", "heartbeats"]
        self._idle_timeout = idle_timeout

        self._symbols: list[str] = []
        self._ws_task: asyncio.Task | None = None
        self._running = False

        # Track last message time for idle watchdog
        self._last_message_time: float = 0.0
        self._watchdog_task: asyncio.Task | None = None

        # Candle aggregation: per-symbol partial 1-minute bars
        # Key: symbol, Value: {minute: datetime, open, high, low, close}
        self._candle_state: dict[str, dict] = {}
        self._candle_flush_task: asyncio.Task | None = None

        # Flag: set when pair discovery updates the symbol list
        self._symbols_changed = asyncio.Event()

        # Volume backfill: cache of real volumes from REST API
        # {symbol: {minute_datetime: volume_float}}
        self._volume_cache: dict[str, dict[datetime, float]] = {}
        self._volume_task: asyncio.Task | None = None

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

        # Subscribe to dynamic symbol updates from pair discovery
        if self._bus:
            from v2.core.types import SymbolsUpdatedEvent
            self._bus.subscribe(SymbolsUpdatedEvent, self._on_symbols_updated)

        logger.info(
            "WebSocket data provider starting (symbols=%s, channels=%s)",
            symbols, self._channels,
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
        # Flush any remaining partial candles
        self._flush_all_candles()
        logger.info("WebSocket data provider stopped")

    def _on_symbols_updated(self, event: Any) -> None:
        """Handle dynamic symbol list changes from pair discovery."""
        new_symbols = list(event.symbols)
        if set(new_symbols) == set(self._symbols):
            return
        logger.info(
            "Symbol list updated: %d -> %d (added=%d, removed=%d)",
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
                    # Subscribe to channels
                    await self._subscribe(ws)
                    logger.info("Market WebSocket connected")
                    backoff = 1.0
                    self._last_message_time = time.time()

                    # Wait for first frame within 8 seconds (gate)
                    try:
                        first = await asyncio.wait_for(ws.recv(), timeout=8.0)
                        self._last_message_time = time.time()
                        self._process_message(first)
                    except asyncio.TimeoutError:
                        logger.warning("No first frame within 8s — reconnecting")
                        continue

                    # Main message loop
                    async for raw_msg in ws:
                        self._last_message_time = time.time()
                        try:
                            self._process_message(raw_msg)
                        except Exception:
                            logger.exception("Error processing market WS message")

                        # Check if pair discovery updated the symbol list
                        if self._symbols_changed.is_set():
                            self._symbols_changed.clear()
                            logger.info("Symbols changed — reconnecting WebSocket")
                            break  # Clean close → reconnect with new symbols

            except asyncio.CancelledError:
                break
            except Exception as e:
                if self._running:
                    jitter = random.uniform(0, backoff * 0.3)
                    logger.warning(
                        "Market WS disconnected: %s — reconnecting in %.1fs",
                        e, backoff + jitter,
                    )
                    await asyncio.sleep(backoff + jitter)
                    backoff = min(backoff * 2, 60.0)

    async def _subscribe(self, ws) -> None:
        """Send subscription message for configured channels."""
        from coinbase import jwt_generator

        jwt_token = jwt_generator.build_rest_jwt(
            self._ws_url, self._api_key, self._api_secret,
        )
        for channel in self._channels:
            msg = {
                "type": "subscribe",
                "channel": channel,
                "product_ids": self._symbols,
                "jwt": jwt_token,
            }
            await ws.send(json.dumps(msg))
            logger.debug("Subscribed to %s for %s", channel, self._symbols)

    # ------------------------------------------------------------------
    # Message processing
    # ------------------------------------------------------------------

    def _process_message(self, raw_msg: str) -> None:
        """Parse and route WebSocket message."""
        try:
            data = json.loads(raw_msg)
        except json.JSONDecodeError:
            logger.warning("Invalid JSON from market WS")
            return

        channel = data.get("channel", "")

        if channel == "ticker_batch":
            self._process_ticker_batch(data)
        elif channel == "heartbeats":
            pass  # Just keep-alive, already tracked by _last_message_time
        elif channel == "subscriptions":
            logger.debug("Subscription confirmed: %s", data)
        elif data.get("type") == "error":
            logger.error("Market WS error: %s", data.get("message"))

    def _process_ticker_batch(self, data: dict) -> None:
        """Extract ticker data from ticker_batch events and publish."""
        events = data.get("events", [])
        for event in events:
            tickers = event.get("tickers", [])
            for ticker in tickers:
                symbol = ticker.get("product_id", "")
                if not symbol:
                    continue

                price_str = ticker.get("price", "0")
                try:
                    price = float(price_str)
                except (ValueError, TypeError):
                    continue

                # Extract 24h change percentage
                change_24h = None
                pct_str = ticker.get("price_percent_chg_24_h")
                if pct_str is not None:
                    try:
                        change_24h = float(pct_str)
                    except (ValueError, TypeError):
                        pass

                # Extract best bid/ask
                bid = None
                ask = None
                try:
                    if "best_bid" in ticker:
                        bid = float(ticker["best_bid"])
                    if "best_ask" in ticker:
                        ask = float(ticker["best_ask"])
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
    # 1-minute candle aggregation
    # ------------------------------------------------------------------

    def _minute_key(self, ts: datetime) -> datetime:
        """Truncate timestamp to the start of the minute."""
        return ts.replace(second=0, microsecond=0)

    def _update_candle(self, symbol: str, price: float, ts: datetime) -> None:
        """Update the current 1-minute candle bar for a symbol.

        If the tick falls in a new minute, emit the completed candle
        as a CandleEvent and start a fresh bar.
        """
        minute = self._minute_key(ts)
        state = self._candle_state.get(symbol)

        if state is None:
            # First tick for this symbol — start a new bar
            self._candle_state[symbol] = {
                "minute": minute,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
            }
            return

        if minute > state["minute"]:
            # New minute — close previous bar and emit
            self._emit_candle(symbol, state)
            # Start new bar
            self._candle_state[symbol] = {
                "minute": minute,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
            }
        else:
            # Same minute — update OHLC
            state["high"] = max(state["high"], price)
            state["low"] = min(state["low"], price)
            state["close"] = price

    def _emit_candle(self, symbol: str, state: dict) -> None:
        """Publish a completed CandleEvent."""
        if not self._bus:
            return
        # Merge real volume from REST API cache if available
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
            "Candle emitted: %s %s O=%.2f H=%.2f L=%.2f C=%.2f V=%.4f",
            symbol, state["minute"].strftime("%H:%M"),
            candle.open, candle.high, candle.low, candle.close, candle.volume,
        )

    def _flush_all_candles(self) -> None:
        """Emit all pending partial candles (used on stop and at minute boundaries)."""
        for symbol, state in list(self._candle_state.items()):
            self._emit_candle(symbol, state)
        self._candle_state.clear()

    async def _candle_flusher(self) -> None:
        """Periodically emit completed candles at minute boundaries.

        Ensures candles are emitted even during quiet market periods
        when no new ticks arrive to trigger the minute rollover.
        """
        while self._running:
            try:
                # Sleep until just past the next minute boundary
                now = datetime.now(timezone.utc)
                seconds_until_next = 60 - now.second - now.microsecond / 1_000_000
                await asyncio.sleep(seconds_until_next + 0.5)

                if not self._running:
                    break

                current_minute = self._minute_key(datetime.now(timezone.utc))
                for symbol, state in list(self._candle_state.items()):
                    if state["minute"] < current_minute:
                        self._emit_candle(symbol, state)
                        # Remove stale state — next tick starts fresh
                        del self._candle_state[symbol]

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Error in candle flusher")

    # ------------------------------------------------------------------
    # Volume backfill via REST API
    # ------------------------------------------------------------------

    async def _volume_fetcher(self) -> None:
        """Periodically fetch real OHLCV candles from Coinbase REST API.

        Runs every 5 minutes, fetches 1-minute candles for each active
        symbol, and caches volume data. The cached volume is merged into
        candles when they are emitted by _emit_candle().
        """
        import aiohttp
        from coinbase import jwt_generator

        # Wait 60s before first fetch to let WebSocket stabilize
        await asyncio.sleep(60)

        while self._running:
            try:
                now = datetime.now(timezone.utc)
                start_ts = int((now - timedelta(minutes=6)).timestamp())
                end_ts = int(now.timestamp())

                async with aiohttp.ClientSession() as session:
                    for symbol in list(self._symbols):
                        if not self._running:
                            break
                        try:
                            await self._fetch_volume_for_symbol(
                                session, jwt_generator, symbol, start_ts, end_ts,
                            )
                        except Exception:
                            logger.debug("Volume fetch failed for %s", symbol, exc_info=True)
                        # Rate limiting between symbols
                        await asyncio.sleep(0.1)

                # Prune stale cache entries (older than 10 minutes)
                cutoff = now - timedelta(minutes=10)
                for sym in list(self._volume_cache.keys()):
                    cache = self._volume_cache[sym]
                    stale = [k for k in cache if k < cutoff]
                    for k in stale:
                        del cache[k]
                    if not cache:
                        del self._volume_cache[sym]

                # Sleep ~1 minute before next fetch cycle so volume
                # is available for most 1-min candles before emission
                await asyncio.sleep(65)

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Error in volume fetcher")
                await asyncio.sleep(60)

    async def _fetch_volume_for_symbol(
        self,
        session: Any,
        jwt_generator: Any,
        symbol: str,
        start_ts: int,
        end_ts: int,
    ) -> None:
        """Fetch 1-minute candles from Coinbase REST API and cache volume."""
        path = f"/api/v3/brokerage/products/{symbol}/candles"
        url = f"https://api.coinbase.com{path}"
        params = {
            "start": str(start_ts),
            "end": str(end_ts),
            "granularity": "ONE_MINUTE",
        }

        jwt_uri = jwt_generator.format_jwt_uri("GET", path)
        jwt_token = jwt_generator.build_rest_jwt(
            jwt_uri, self._api_key, self._api_secret,
        )
        headers = {"Authorization": f"Bearer {jwt_token}"}

        async with session.get(url, params=params, headers=headers) as resp:
            if resp.status != 200:
                logger.debug("Volume API %d for %s", resp.status, symbol)
                return
            data = await resp.json()

        candles = data.get("candles", [])
        if not candles:
            return

        if symbol not in self._volume_cache:
            self._volume_cache[symbol] = {}

        cached = 0
        for c in candles:
            try:
                epoch = int(c["start"])
                volume = float(c.get("volume", 0))
                minute_dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
                self._volume_cache[symbol][minute_dt] = volume
                cached += 1
            except (KeyError, ValueError, TypeError):
                continue

        if cached:
            logger.debug("Volume cached: %s — %d candles", symbol, cached)

    # ------------------------------------------------------------------
    # Idle watchdog
    # ------------------------------------------------------------------

    async def _idle_watchdog(self) -> None:
        """Monitor for idle WebSocket and force reconnect."""
        while self._running:
            try:
                await asyncio.sleep(self._idle_timeout / 2)
                if not self._running:
                    break

                elapsed = time.time() - self._last_message_time
                if self._last_message_time > 0 and elapsed > self._idle_timeout:
                    logger.warning(
                        "Market WS idle for %.0fs (threshold: %.0fs) — forcing reconnect",
                        elapsed, self._idle_timeout,
                    )
                    # Cancel the WS task to trigger reconnection
                    if self._ws_task and not self._ws_task.done():
                        self._ws_task.cancel()
                        try:
                            await self._ws_task
                        except asyncio.CancelledError:
                            pass
                    # Restart
                    if self._running:
                        self._ws_task = asyncio.create_task(self._ws_loop())

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Error in idle watchdog")
