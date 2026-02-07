"""WebSocket data provider for live market data.

Connects to Coinbase Advanced Trade WebSocket for real-time
ticker_batch events. Emits TickerEvents on the event bus.

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
from v2.core.types import TickerEvent

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
        ws_url: str = "wss://advanced-trade-ws.coinbase.com",
        channels: list[str] | None = None,
        idle_timeout: float = 90.0,
        **kwargs: Any,
    ) -> None:
        self._bus = event_bus
        self._api_key = api_key or os.environ.get(api_key_env, "")
        self._api_secret = api_secret or os.environ.get(api_secret_env, "")
        self._ws_url = ws_url
        self._channels = channels or ["ticker_batch", "heartbeats"]
        self._idle_timeout = idle_timeout

        self._symbols: list[str] = []
        self._ws_task: asyncio.Task | None = None
        self._running = False

        # Track last message time for idle watchdog
        self._last_message_time: float = 0.0
        self._watchdog_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # DataProvider interface
    # ------------------------------------------------------------------

    async def start(self, symbols: list[str]) -> None:
        self._symbols = symbols
        self._running = True
        self._ws_task = asyncio.create_task(self._ws_loop())
        self._watchdog_task = asyncio.create_task(self._idle_watchdog())
        logger.info(
            "WebSocket data provider starting (symbols=%s, channels=%s)",
            symbols, self._channels,
        )

    async def stop(self) -> None:
        self._running = False
        for task in (self._ws_task, self._watchdog_task):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        logger.info("WebSocket data provider stopped")

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

                if self._bus:
                    self._bus.publish(TickerEvent(
                        symbol=symbol,
                        price=price,
                        timestamp=datetime.now(timezone.utc),
                        change_24h_pct=change_24h,
                        bid=bid,
                        ask=ask,
                    ))

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
