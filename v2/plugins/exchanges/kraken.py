"""Kraken exchange adapter.

Implements the ExchangeAdapter ABC for Kraken's REST + WebSocket APIs.

Authentication uses HMAC-SHA512 signatures (``kraken_auth.py``).
WebSocket fill events come from the authenticated ``executions`` channel.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
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
from v2.utils.kraken_auth import kraken_nonce, kraken_signature
from v2.utils.symbol_mapper import KrakenSymbolMapper

logger = logging.getLogger(__name__)

_REST_URL = "https://api.kraken.com"
_WS_AUTH_URL = "wss://ws-auth.kraken.com/v2"

# Map Kraken order status strings to our OrderStatus enum
_STATUS_MAP = {
    "pending": OrderStatus.PENDING,
    "new": OrderStatus.OPEN,
    "open": OrderStatus.OPEN,
    "partially_filled": OrderStatus.OPEN,
    "filled": OrderStatus.FILLED,
    "closed": OrderStatus.FILLED,
    "canceled": OrderStatus.CANCELLED,
    "cancelled": OrderStatus.CANCELLED,
    "expired": OrderStatus.CANCELLED,
    "rejected": OrderStatus.REJECTED,
}


@registry.plugin("exchange", "kraken")
class KrakenExchange(ExchangeAdapter):
    """Live Kraken exchange adapter.

    Handles:
    - HMAC-SHA512 authentication
    - REST order submission, cancellation, queries
    - Account balances and fee rates
    - WebSocket user stream for fill events (``executions`` channel)
    """

    name = "kraken"

    def __init__(
        self,
        event_bus: EventBus | None = None,
        api_key: str | None = None,
        api_secret: str | None = None,
        api_key_env: str = "KRAKEN_API_KEY",
        api_secret_env: str = "KRAKEN_API_SECRET",
        key_file: str | None = None,
        rest_url: str = _REST_URL,
        ws_auth_url: str = _WS_AUTH_URL,
        mapper: KrakenSymbolMapper | None = None,
        **kwargs: Any,
    ) -> None:
        from v2.utils.credentials import resolve_credentials

        self._bus = event_bus
        self._api_key, self._api_secret = resolve_credentials(
            api_key, api_secret, api_key_env, api_secret_env, key_file,
            key_file_format="kraken",
        )
        self._rest_url = rest_url
        self._ws_auth_url = ws_auth_url
        self._mapper = mapper or KrakenSymbolMapper()

        self._session: aiohttp.ClientSession | None = None

        # WebSocket user stream
        self._ws_task: asyncio.Task | None = None
        self._ws_running = False
        self._ws_token: str | None = None

        # Order semaphore (limit concurrent REST calls)
        self._order_sem = asyncio.Semaphore(10)

        # Track orders locally
        self._orders: dict[str, Order] = {}

        # Seen fill IDs for deduplication
        self._seen_fills: set[str] = set()

    @property
    def mapper(self) -> KrakenSymbolMapper:
        return self._mapper

    @mapper.setter
    def mapper(self, value: KrakenSymbolMapper) -> None:
        self._mapper = value

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        self._session = aiohttp.ClientSession()
        logger.info("Kraken exchange connected (REST: %s)", self._rest_url)

        # Get WebSocket token and start user stream
        if self._bus and self._api_key:
            self._ws_token = await self._get_ws_token()
            if self._ws_token:
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
        logger.info("Kraken exchange disconnected")

    # ------------------------------------------------------------------
    # REST helpers
    # ------------------------------------------------------------------

    async def _private_request(
        self, path: str, data: dict | None = None
    ) -> dict:
        """Execute authenticated POST to a Kraken private endpoint."""
        if not self._session or self._session.closed:
            self._session = aiohttp.ClientSession()

        if data is None:
            data = {}
        data["nonce"] = kraken_nonce()

        sig = kraken_signature(self._api_secret, path, data)
        headers = {
            "API-Key": self._api_key,
            "API-Sign": sig,
            "Content-Type": "application/x-www-form-urlencoded",
        }

        url = f"{self._rest_url}{path}"
        async with self._order_sem:
            try:
                async with self._session.post(
                    url, data=data, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    body = await resp.json()
                    errors = body.get("error", [])
                    if errors:
                        logger.error("Kraken %s error: %s", path, errors)
                        return {"error": errors}
                    return body.get("result", {})
            except asyncio.TimeoutError:
                logger.error("Timeout: POST %s", path)
                return {"error": ["timeout"]}
            except aiohttp.ClientError as e:
                logger.error("Network error: POST %s: %s", path, e)
                return {"error": [str(e)]}

    async def _public_request(
        self, path: str, params: dict | None = None
    ) -> dict:
        """Execute unauthenticated GET to a Kraken public endpoint."""
        if not self._session or self._session.closed:
            self._session = aiohttp.ClientSession()

        url = f"{self._rest_url}{path}"
        try:
            async with self._session.get(
                url, params=params,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                body = await resp.json()
                errors = body.get("error", [])
                if errors:
                    logger.error("Kraken %s error: %s", path, errors)
                    return {"error": errors}
                return body.get("result", {})
        except asyncio.TimeoutError:
            logger.error("Timeout: GET %s", path)
            return {"error": ["timeout"]}
        except aiohttp.ClientError as e:
            logger.error("Network error: GET %s: %s", path, e)
            return {"error": [str(e)]}

    # ------------------------------------------------------------------
    # WebSocket token
    # ------------------------------------------------------------------

    async def _get_ws_token(self) -> str | None:
        """Get a WebSocket authentication token via REST."""
        result = await self._private_request("/0/private/GetWebSocketsToken")
        if "error" in result:
            logger.error("Failed to get Kraken WS token: %s", result["error"])
            return None
        token = result.get("token", "")
        if token:
            logger.info("Kraken WS auth token acquired")
        return token or None

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------

    async def submit_order(self, order: Order) -> Order:
        """Submit order to Kraken REST API."""
        rest_pair = self._mapper.to_kraken_rest(order.symbol)
        side_str = "buy" if order.side == Side.BUY else "sell"
        cl_ord_id = order.order_id or str(uuid.uuid4())

        data: dict[str, Any] = {
            "pair": rest_pair,
            "type": side_str,
            "volume": str(order.qty),
            "cl_ord_id": cl_ord_id,
        }

        if order.order_type == OrderType.MARKET:
            data["ordertype"] = "market"
        else:
            data["ordertype"] = "limit"
            data["price"] = str(order.price)
            if order.metadata.get("post_only", True):
                data["oflags"] = "post"

        result = await self._private_request("/0/private/AddOrder", data)

        if "error" in result:
            rejected = Order(
                order_id=cl_ord_id,
                symbol=order.symbol,
                side=order.side,
                order_type=order.order_type,
                price=order.price,
                qty=order.qty,
                status=OrderStatus.REJECTED,
                timestamp=datetime.now(timezone.utc),
                metadata={
                    **order.metadata,
                    "reject_reason": str(result["error"]),
                },
            )
            return rejected

        # Success: Kraken returns {"descr": {...}, "txid": ["O..."]}
        txids = result.get("txid", [])
        kraken_order_id = txids[0] if txids else cl_ord_id

        submitted = Order(
            order_id=kraken_order_id,
            symbol=order.symbol,
            side=order.side,
            order_type=order.order_type,
            price=order.price,
            qty=order.qty,
            status=OrderStatus.OPEN,
            timestamp=datetime.now(timezone.utc),
            metadata={**order.metadata, "cl_ord_id": cl_ord_id},
        )
        self._orders[kraken_order_id] = submitted
        if self._bus:
            self._bus.publish(OrderEvent(order=submitted, event_type="submitted"))
        return submitted

    async def cancel_order(self, order_id: str) -> bool:
        result = await self._private_request(
            "/0/private/CancelOrder", {"txid": order_id}
        )
        if "error" in result:
            return False
        count = result.get("count", 0)
        if count > 0:
            self._orders.pop(order_id, None)
            return True
        return False

    async def get_order(self, order_id: str) -> Order | None:
        """Get order by ID — check local cache first, then REST."""
        if order_id in self._orders:
            return self._orders[order_id]

        result = await self._private_request(
            "/0/private/QueryOrders", {"txid": order_id}
        )
        if "error" in result or not result:
            return None

        order_data = result.get(order_id)
        if order_data:
            return self._parse_order(order_id, order_data)
        return None

    async def get_open_orders(self, symbol: str | None = None) -> list[Order]:
        result = await self._private_request("/0/private/OpenOrders")
        if "error" in result:
            return []

        open_orders = result.get("open", {})
        orders = []
        for txid, data in open_orders.items():
            order = self._parse_order(txid, data)
            if symbol is None or order.symbol == symbol:
                orders.append(order)
        return orders

    # ------------------------------------------------------------------
    # Account
    # ------------------------------------------------------------------

    async def get_balances(self) -> dict[str, Decimal]:
        result = await self._private_request("/0/private/Balance")
        if "error" in result:
            return {}

        balances: dict[str, Decimal] = {}
        for asset, amount_str in result.items():
            try:
                amount = Decimal(str(amount_str))
                if amount > 0:
                    normalized = KrakenSymbolMapper.normalize_asset(asset)
                    balances[normalized] = amount
            except (ValueError, TypeError):
                continue
        return balances

    async def get_fee_rates(self) -> dict[str, Decimal]:
        result = await self._private_request(
            "/0/private/TradeVolume", {"pair": "XBTUSD"}
        )
        if "error" in result:
            return {
                "maker": Decimal("0.0025"),
                "taker": Decimal("0.004"),
            }

        fees = result.get("fees", {})
        # Use first pair's fees as representative
        for pair_name, fee_info in fees.items():
            return {
                "maker": Decimal(str(fee_info.get("fee", "0.0025"))).quantize(Decimal("0.0001")),
                "taker": Decimal(str(fee_info.get("fee", "0.004"))).quantize(Decimal("0.0001")),
            }

        # Also check fees_maker for actual maker rate
        fees_maker = result.get("fees_maker", {})
        maker_rate = Decimal("0.0025")
        taker_rate = Decimal("0.004")
        for pair_name, fee_info in fees.items():
            taker_rate = Decimal(str(fee_info.get("fee", "0.004"))).quantize(Decimal("0.0001"))
            break
        for pair_name, fee_info in fees_maker.items():
            maker_rate = Decimal(str(fee_info.get("fee", "0.0025"))).quantize(Decimal("0.0001"))
            break

        return {"maker": maker_rate, "taker": taker_rate}

    async def get_ticker(self, symbol: str) -> TickerEvent:
        rest_pair = self._mapper.to_kraken_rest(symbol)
        result = await self._public_request(
            "/0/public/Ticker", {"pair": rest_pair}
        )
        if "error" in result or not result:
            return TickerEvent(symbol=symbol, price=0.0, timestamp=datetime.now(timezone.utc))

        # Result has the pair name as key
        for pair_name, data in result.items():
            try:
                # c = [last_price, lot_volume]
                last = float(data["c"][0])
                bid = float(data["b"][0])
                ask = float(data["a"][0])
                return TickerEvent(
                    symbol=symbol,
                    price=last,
                    timestamp=datetime.now(timezone.utc),
                    bid=bid,
                    ask=ask,
                )
            except (KeyError, IndexError, ValueError, TypeError):
                break

        return TickerEvent(symbol=symbol, price=0.0, timestamp=datetime.now(timezone.utc))

    async def get_best_bid_ask(self, symbol: str) -> dict[str, Decimal]:
        """Return best bid/ask for a symbol."""
        rest_pair = self._mapper.to_kraken_rest(symbol)
        result = await self._public_request(
            "/0/public/Ticker", {"pair": rest_pair}
        )
        if "error" in result or not result:
            return {"bid": Decimal("0"), "ask": Decimal("0")}

        for pair_name, data in result.items():
            try:
                return {
                    "bid": Decimal(data["b"][0]),
                    "ask": Decimal(data["a"][0]),
                }
            except (KeyError, IndexError, ValueError, TypeError):
                break

        return {"bid": Decimal("0"), "ask": Decimal("0")}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _parse_order(self, txid: str, data: dict) -> Order:
        """Parse Kraken order JSON into Order type."""
        descr = data.get("descr", {})
        side = Side.BUY if descr.get("type", "").lower() == "buy" else Side.SELL
        status = _STATUS_MAP.get(data.get("status", "").lower(), OrderStatus.PENDING)

        # Translate pair back to internal format
        pair = descr.get("pair", "")
        symbol = self._mapper.from_kraken_rest(pair)

        price = Decimal(str(descr.get("price", "0") or "0"))
        qty = Decimal(str(data.get("vol", "0")))
        filled_qty = Decimal(str(data.get("vol_exec", "0")))

        return Order(
            order_id=txid,
            symbol=symbol,
            side=side,
            order_type=OrderType.LIMIT if descr.get("ordertype") == "limit" else OrderType.MARKET,
            price=price,
            qty=qty,
            status=status,
            timestamp=datetime.now(timezone.utc),
            metadata={
                "filled_qty": str(filled_qty),
                "cl_ord_id": data.get("cl_ord_id", ""),
            },
        )

    # ------------------------------------------------------------------
    # WebSocket user stream (fill events)
    # ------------------------------------------------------------------

    async def _ws_user_loop(self) -> None:
        """Connect to authenticated WebSocket and process fill events."""
        import websockets

        backoff = 1.0
        while self._ws_running:
            try:
                # Refresh token if needed
                if not self._ws_token:
                    self._ws_token = await self._get_ws_token()
                    if not self._ws_token:
                        logger.error("Cannot get WS token — retrying in %.1fs", backoff)
                        await asyncio.sleep(backoff)
                        backoff = min(backoff * 2, 60.0)
                        continue

                async with websockets.connect(
                    self._ws_auth_url,
                    ping_interval=20,
                    ping_timeout=60,
                    open_timeout=10,
                ) as ws:
                    # Subscribe to executions channel
                    subscribe_msg = {
                        "method": "subscribe",
                        "params": {
                            "channel": "executions",
                            "token": self._ws_token,
                            "snap_orders": True,
                        },
                    }
                    await ws.send(json.dumps(subscribe_msg))
                    logger.info("Kraken user WebSocket connected and subscribed")
                    backoff = 1.0

                    async for raw_msg in ws:
                        try:
                            data = json.loads(raw_msg)
                            channel = data.get("channel", "")
                            if channel == "executions":
                                self._process_executions(data)
                            elif data.get("method") == "subscribe" and data.get("success"):
                                logger.debug("Kraken executions subscription confirmed")
                            elif data.get("type") == "error" or data.get("error"):
                                logger.error("Kraken user WS error: %s", data)
                        except json.JSONDecodeError:
                            logger.warning("Invalid JSON from Kraken user WS")
                        except Exception:
                            logger.exception("Error processing Kraken user WS message")

            except asyncio.CancelledError:
                break
            except Exception as e:
                if self._ws_running:
                    # Token may have expired — clear it to force refresh
                    self._ws_token = None
                    logger.warning(
                        "Kraken user WS disconnected: %s — reconnecting in %.1fs",
                        e, backoff,
                    )
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 60.0)

    def _process_executions(self, data: dict) -> None:
        """Process executions channel events (fills, order updates)."""
        msg_type = data.get("type", "")
        executions = data.get("data", [])

        for exec_data in executions:
            exec_type = exec_data.get("exec_type", "")

            if exec_type == "filled" or exec_type == "trade":
                self._handle_fill(exec_data)
            elif exec_type in ("canceled", "expired"):
                order_id = exec_data.get("order_id", "")
                self._orders.pop(order_id, None)

    def _handle_fill(self, exec_data: dict) -> None:
        """Process a trade/fill execution event."""
        exec_id = exec_data.get("exec_id", "")
        if exec_id in self._seen_fills:
            return
        self._seen_fills.add(exec_id)

        order_id = exec_data.get("order_id", "")
        ws_symbol = exec_data.get("symbol", "")
        symbol = self._mapper.from_kraken_ws(ws_symbol) if ws_symbol else ""

        side_str = exec_data.get("side", "").lower()
        side = Side.BUY if side_str == "buy" else Side.SELL

        try:
            price = Decimal(str(exec_data.get("last_price", "0")))
            qty = Decimal(str(exec_data.get("last_qty", "0")))
            fee = Decimal(str(exec_data.get("fee_paid", "0")))
        except (ValueError, TypeError):
            logger.warning("Kraken fill: invalid numeric data in exec_id=%s", exec_id)
            return

        fee_currency = exec_data.get("fee_currency", "USD")
        fee_currency = KrakenSymbolMapper.normalize_asset(fee_currency)

        fill = Fill(
            fill_id=exec_id,
            order_id=order_id,
            symbol=symbol,
            side=side,
            price=price,
            qty=qty,
            fee=fee,
            fee_currency=fee_currency,
            is_maker=exec_data.get("order_type", "").lower() == "limit",
            timestamp=datetime.now(timezone.utc),
            metadata={
                "cl_ord_id": exec_data.get("cl_ord_id", ""),
            },
        )

        if self._bus:
            self._bus.publish(FillEvent(fill=fill))

        logger.info(
            "Kraken fill: %s %s %s @ %s (fee: %s %s)",
            fill.side.value, fill.qty, fill.symbol, fill.price, fill.fee, fill.fee_currency,
        )
