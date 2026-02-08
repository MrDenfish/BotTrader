"""Coinbase Advanced Trade exchange adapter.

Ports REST order management + WebSocket fill events from
Api_manager/coinbase_api.py and webhook/listener.py into a single
ExchangeAdapter plugin.

Authentication uses Coinbase JWT (``coinbase.jwt_generator``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import aiohttp

from v2.core import registry
from v2.core.event_bus import EventBus
from v2.core.interfaces import ExchangeAdapter
from v2.core.types import (
    Fill,
    FillEvent,
    Order,
    OrderEvent,
    OrderStatus,
    OrderType,
    Side,
    TickerEvent,
)

logger = logging.getLogger(__name__)

# Map Coinbase order status strings to our OrderStatus enum
_STATUS_MAP = {
    "PENDING": OrderStatus.PENDING,
    "OPEN": OrderStatus.OPEN,
    "FILLED": OrderStatus.FILLED,
    "CANCELLED": OrderStatus.CANCELLED,
    "CANCELED": OrderStatus.CANCELLED,
    "EXPIRED": OrderStatus.CANCELLED,
    "FAILED": OrderStatus.REJECTED,
}


@registry.plugin("exchange", "coinbase")
class CoinbaseExchange(ExchangeAdapter):
    """Live Coinbase Advanced Trade exchange adapter.

    Handles:
    - JWT authentication (auto-refresh)
    - REST order submission, cancellation, queries
    - Account balances and fee rates
    - WebSocket user stream for fill events
    """

    name = "coinbase"

    def __init__(
        self,
        event_bus: EventBus | None = None,
        api_key: str | None = None,
        api_secret: str | None = None,
        api_key_env: str = "COINBASE_API_KEY",
        api_secret_env: str = "COINBASE_API_SECRET",
        rest_url: str = "https://api.coinbase.com",
        ws_url: str = "wss://advanced-trade-ws-user.coinbase.com",
        **kwargs: Any,
    ) -> None:
        self._bus = event_bus
        self._api_key = api_key or os.environ.get(api_key_env, "")
        self._api_secret = api_secret or os.environ.get(api_secret_env, "")
        self._rest_url = rest_url
        self._ws_url = ws_url

        self._session: aiohttp.ClientSession | None = None
        self._jwt_token: str | None = None
        self._jwt_expiry: datetime | None = None

        # WebSocket user stream
        self._ws_task: asyncio.Task | None = None
        self._ws_running = False

        # Order semaphore (limit concurrent REST calls)
        self._order_sem = asyncio.Semaphore(10)

        # Track orders locally
        self._orders: dict[str, Order] = {}

        # Seen fill IDs for deduplication
        self._seen_fills: set[str] = set()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        self._session = aiohttp.ClientSession()
        logger.info("Coinbase exchange connected (REST: %s)", self._rest_url)

        # Start user WebSocket for fill events
        if self._bus and self._api_key:
            self._ws_running = True
            self._ws_task = asyncio.create_task(self._ws_user_loop())

    async def disconnect(self) -> None:
        self._ws_running = False
        if self._ws_task:
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
        if self._session and not self._session.closed:
            await self._session.close()
        logger.info("Coinbase exchange disconnected")

    # ------------------------------------------------------------------
    # JWT Authentication
    # ------------------------------------------------------------------

    def _generate_jwt(self, method: str = "GET", path: str = "/api/v3/brokerage/orders") -> str:
        """Generate JWT for Coinbase Advanced Trade API."""
        from coinbase import jwt_generator

        jwt_uri = jwt_generator.format_jwt_uri(method, path)
        token = jwt_generator.build_rest_jwt(jwt_uri, self._api_key, self._api_secret)
        if not token:
            raise RuntimeError("JWT generation returned empty token")
        self._jwt_token = token
        self._jwt_expiry = datetime.now(timezone.utc) + timedelta(minutes=5)
        return token

    def _get_headers(self, method: str, path: str) -> dict[str, str]:
        """Return auth headers, refreshing JWT if needed."""
        now = datetime.now(timezone.utc)
        if (
            not self._jwt_token
            or not self._jwt_expiry
            or now >= self._jwt_expiry - timedelta(seconds=60)
        ):
            self._generate_jwt(method, path)
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._jwt_token}",
        }

    # ------------------------------------------------------------------
    # REST helpers
    # ------------------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        json_data: dict | None = None,
        params: dict | None = None,
    ) -> dict:
        """Execute authenticated REST request with error handling."""
        if not self._session or self._session.closed:
            self._session = aiohttp.ClientSession()

        headers = self._get_headers(method, path)
        url = f"{self._rest_url}{path}"

        async with self._order_sem:
            try:
                async with self._session.request(
                    method, url, headers=headers, json=json_data, params=params,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    text = await resp.text()
                    if resp.status == 200:
                        return await resp.json()
                    logger.error("[%d] %s %s: %s", resp.status, method, path, text)
                    return {"error": f"HTTP {resp.status}", "details": text}
            except asyncio.TimeoutError:
                logger.error("Timeout: %s %s", method, path)
                return {"error": "timeout"}
            except aiohttp.ClientError as e:
                logger.error("Network error: %s %s: %s", method, path, e)
                return {"error": "network", "details": str(e)}

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------

    async def submit_order(self, order: Order) -> Order:
        """Submit order to Coinbase REST API."""
        side_str = "BUY" if order.side == Side.BUY else "SELL"

        # Build order configuration based on type
        if order.order_type == OrderType.MARKET:
            order_config = {
                "market_market_ioc": {
                    "base_size": str(order.qty),
                }
            }
        else:
            # Default: limit GTC
            order_config = {
                "limit_limit_gtc": {
                    "base_size": str(order.qty),
                    "limit_price": str(order.price),
                    "post_only": order.metadata.get("post_only", True),
                }
            }

        # Attached TP/SL bracket if present
        payload: dict[str, Any] = {
            "client_order_id": order.order_id,
            "product_id": order.symbol,
            "side": side_str,
            "order_configuration": order_config,
        }

        if "tp_price" in order.metadata and "sl_price" in order.metadata:
            payload["attached_order_configuration"] = {
                "trigger_bracket_gtc": {
                    "limit_price": str(order.metadata["tp_price"]),
                    "stop_trigger_price": str(order.metadata["sl_price"]),
                }
            }

        resp = await self._request("POST", "/api/v3/brokerage/orders", json_data=payload)

        if resp.get("success") and resp.get("success_response", {}).get("order_id"):
            cb_order_id = resp["success_response"]["order_id"]
            submitted = Order(
                order_id=cb_order_id,
                symbol=order.symbol,
                side=order.side,
                order_type=order.order_type,
                price=order.price,
                qty=order.qty,
                status=OrderStatus.OPEN,
                timestamp=datetime.now(timezone.utc),
                metadata={**order.metadata, "client_order_id": order.order_id},
            )
            self._orders[cb_order_id] = submitted
            if self._bus:
                self._bus.publish(OrderEvent(order=submitted, event_type="submitted"))
            return submitted

        # Order rejected
        error_resp = resp.get("error_response", {})
        rejected = Order(
            order_id=order.order_id,
            symbol=order.symbol,
            side=order.side,
            order_type=order.order_type,
            price=order.price,
            qty=order.qty,
            status=OrderStatus.REJECTED,
            timestamp=datetime.now(timezone.utc),
            metadata={
                **order.metadata,
                "reject_reason": error_resp.get("preview_failure_reason", "unknown"),
                "reject_message": error_resp.get("message", str(resp)),
            },
        )
        return rejected

    async def cancel_order(self, order_id: str) -> bool:
        resp = await self._request(
            "POST",
            "/api/v3/brokerage/orders/batch_cancel",
            json_data={"order_ids": [order_id]},
        )
        results = resp.get("results", [])
        if results and results[0].get("success"):
            self._orders.pop(order_id, None)
            return True
        return False

    async def get_order(self, order_id: str) -> Order:
        """Get order by ID — check local cache first, then REST."""
        if order_id in self._orders:
            return self._orders[order_id]

        resp = await self._request(
            "GET",
            "/api/v3/brokerage/orders/historical/batch",
            params={"order_ids": order_id},
        )
        orders = resp.get("orders", [])
        if orders:
            return self._parse_order(orders[0])
        return None

    async def get_open_orders(self, symbol: str | None = None) -> list[Order]:
        params = {"order_status": "OPEN", "limit": "100"}
        if symbol:
            params["product_id"] = symbol

        resp = await self._request(
            "GET", "/api/v3/brokerage/orders/historical/batch", params=params,
        )
        orders = resp.get("orders", [])
        return [self._parse_order(o) for o in orders]

    # ------------------------------------------------------------------
    # Account
    # ------------------------------------------------------------------

    async def get_balances(self) -> dict[str, Decimal]:
        resp = await self._request("GET", "/api/v3/brokerage/accounts", params={"limit": "250"})
        accounts = resp.get("accounts", [])
        balances: dict[str, Decimal] = {}
        for acc in accounts:
            currency = acc.get("currency", "")
            avail = acc.get("available_balance", {}).get("value", "0")
            hold = acc.get("hold", {}).get("value", "0")
            total = Decimal(avail) + Decimal(hold)
            if total > 0:
                balances[currency] = Decimal(avail)
        return balances

    async def get_fee_rates(self) -> dict[str, Decimal]:
        resp = await self._request("GET", "/api/v3/brokerage/transaction_summary")
        tier = resp.get("fee_tier", {})
        return {
            "maker": Decimal(tier.get("maker_fee_rate", "0.006")),
            "taker": Decimal(tier.get("taker_fee_rate", "0.012")),
        }

    async def get_ticker(self, symbol: str) -> TickerEvent:
        resp = await self._request(
            "GET", "/api/v3/brokerage/best_bid_ask",
            params={"product_ids": symbol},
        )
        pricebooks = resp.get("pricebooks", [])
        if pricebooks:
            pb = pricebooks[0]
            bids = pb.get("bids", [])
            asks = pb.get("asks", [])
            bid = float(bids[0]["price"]) if bids else 0.0
            ask = float(asks[0]["price"]) if asks else 0.0
            mid = (bid + ask) / 2 if bid and ask else bid or ask
            return TickerEvent(
                symbol=symbol,
                price=mid,
                timestamp=datetime.now(timezone.utc),
                bid=bid,
                ask=ask,
            )
        return TickerEvent(symbol=symbol, price=0.0, timestamp=datetime.now(timezone.utc))

    # ------------------------------------------------------------------
    # Order book (for execution plugins)
    # ------------------------------------------------------------------

    async def get_best_bid_ask(self, symbol: str) -> dict[str, Decimal]:
        """Return best bid/ask for a symbol."""
        resp = await self._request(
            "GET", "/api/v3/brokerage/best_bid_ask",
            params={"product_ids": symbol},
        )
        pricebooks = resp.get("pricebooks", [])
        if pricebooks:
            pb = pricebooks[0]
            bids = pb.get("bids", [])
            asks = pb.get("asks", [])
            return {
                "bid": Decimal(bids[0]["price"]) if bids else Decimal("0"),
                "ask": Decimal(asks[0]["price"]) if asks else Decimal("0"),
            }
        return {"bid": Decimal("0"), "ask": Decimal("0")}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_order(data: dict) -> Order:
        """Parse Coinbase order JSON into Order type."""
        side = Side.BUY if data.get("side", "").upper() == "BUY" else Side.SELL
        status = _STATUS_MAP.get(data.get("status", "").upper(), OrderStatus.PENDING)

        # Extract price from order_configuration
        oc = data.get("order_configuration", {})
        price = Decimal("0")
        qty = Decimal("0")
        for key, cfg in oc.items():
            if "limit_price" in cfg:
                price = Decimal(cfg["limit_price"])
            if "base_size" in cfg:
                qty = Decimal(cfg["base_size"])

        return Order(
            order_id=data.get("order_id", ""),
            symbol=data.get("product_id", ""),
            side=side,
            order_type=OrderType.LIMIT,
            price=price,
            qty=qty,
            status=status,
            timestamp=datetime.now(timezone.utc),
            metadata={"client_order_id": data.get("client_order_id", "")},
        )

    # ------------------------------------------------------------------
    # WebSocket user stream (fill events)
    # ------------------------------------------------------------------

    async def _ws_user_loop(self) -> None:
        """Connect to user WebSocket and process fill events."""
        import websockets

        backoff = 1.0
        while self._ws_running:
            try:
                async with websockets.connect(
                    self._ws_url,
                    ping_interval=20,
                    ping_timeout=60,
                    open_timeout=10,
                ) as ws:
                    # Subscribe to user channel
                    from coinbase import jwt_generator
                    jwt_token = jwt_generator.build_rest_jwt(
                        self._ws_url, self._api_key, self._api_secret,
                    )
                    subscribe_msg = {
                        "type": "subscribe",
                        "channel": "user",
                        "jwt": jwt_token,
                    }
                    await ws.send(json.dumps(subscribe_msg))
                    logger.info("User WebSocket connected and subscribed")
                    backoff = 1.0  # Reset on successful connection

                    async for raw_msg in ws:
                        try:
                            data = json.loads(raw_msg)
                            channel = data.get("channel", "")
                            if channel == "user":
                                await self._process_user_events(data)
                        except json.JSONDecodeError:
                            logger.warning("Invalid JSON from user WS")
                        except Exception:
                            logger.exception("Error processing user WS message")

            except asyncio.CancelledError:
                break
            except Exception as e:
                if self._ws_running:
                    logger.warning(
                        "User WS disconnected: %s — reconnecting in %.1fs", e, backoff,
                    )
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 60.0)

    async def _process_user_events(self, data: dict) -> None:
        """Process user channel events (fills, order updates)."""
        events = data.get("events", [])
        for event in events:
            event_type = event.get("type", "")
            orders = event.get("orders", [])

            for order_data in orders:
                status = order_data.get("status", "").upper()
                order_id = order_data.get("order_id", "")

                # Process fills
                if status in ("FILLED", "PARTIALLY_FILLED"):
                    filled_value = order_data.get("total_value_after_fees", "0")
                    filled_size = order_data.get("cumulative_quantity", "0")
                    avg_price = order_data.get("average_filled_price", "0")

                    # Create dedup key
                    fill_key = f"{order_id}:{filled_size}"
                    if fill_key in self._seen_fills:
                        continue
                    self._seen_fills.add(fill_key)

                    side = Side.BUY if order_data.get("side", "").upper() == "BUY" else Side.SELL
                    fee_str = order_data.get("total_fees", "0")

                    fill = Fill(
                        fill_id=f"{order_id}-{len(self._seen_fills)}",
                        order_id=order_id,
                        symbol=order_data.get("product_id", ""),
                        side=side,
                        price=Decimal(avg_price) if avg_price else Decimal("0"),
                        qty=Decimal(filled_size) if filled_size else Decimal("0"),
                        fee=Decimal(fee_str) if fee_str else Decimal("0"),
                        fee_currency="USD",
                        is_maker=order_data.get("order_type", "").upper() == "LIMIT",
                        timestamp=datetime.now(timezone.utc),
                        metadata={
                            "client_order_id": order_data.get("client_order_id", ""),
                        },
                    )

                    if self._bus:
                        self._bus.publish(FillEvent(fill=fill))

                    logger.info(
                        "Fill: %s %s %s @ %s (fee: %s)",
                        fill.side.value, fill.qty, fill.symbol, fill.price, fill.fee,
                    )
