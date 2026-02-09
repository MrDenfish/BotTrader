"""Paper trading exchange adapter.

Simulates order fills using live market data. Connects to a live
DataProvider (WebSocket or REST) but executes orders locally with
configurable slippage and maker/taker fees.

The paper exchange subscribes to TickerEvents on the event bus and uses
the latest bid/ask to simulate fills.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

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


@registry.plugin("exchange", "paper")
class PaperExchange(ExchangeAdapter):
    """Simulated exchange for paper trading with live data.

    Subscribes to TickerEvent to track current prices.
    When an order is submitted:
    - Market orders fill immediately at current price + slippage
    - Limit orders fill if the price crosses the limit
    """

    name = "paper"

    def __init__(
        self,
        event_bus: EventBus | None = None,
        initial_balance_usd: float = 10_000.0,
        maker_fee: float = 0.006,
        taker_fee: float = 0.012,
        slippage_bps: float = 1.0,
        **kwargs: Any,
    ) -> None:
        self._bus = event_bus
        self._maker_fee = Decimal(str(maker_fee))
        self._taker_fee = Decimal(str(taker_fee))
        self._slippage_bps = Decimal(str(slippage_bps))

        # Account state
        self._balances: dict[str, Decimal] = {"USD": Decimal(str(initial_balance_usd))}

        # Order tracking
        self._orders: dict[str, Order] = {}
        self._open_orders: dict[str, Order] = {}

        # Latest prices from ticker
        self._latest_bid: dict[str, Decimal] = {}
        self._latest_ask: dict[str, Decimal] = {}
        self._latest_price: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        # Subscribe to ticker events to track live prices
        if self._bus:
            self._bus.subscribe(TickerEvent, self._on_ticker)
        logger.info(
            "Paper exchange connected (balance=$%s, maker=%.4f, taker=%.4f)",
            self._balances["USD"], self._maker_fee, self._taker_fee,
        )

    async def disconnect(self) -> None:
        open_count = len(self._open_orders)
        if open_count:
            logger.info("Paper exchange: cancelling %d open orders on disconnect", open_count)
        self._open_orders.clear()
        logger.info("Paper exchange disconnected")

    # ------------------------------------------------------------------
    # Ticker handler
    # ------------------------------------------------------------------

    def _on_ticker(self, event: TickerEvent) -> None:
        """Track latest prices and check resting limit orders."""
        symbol = event.symbol
        self._latest_price[symbol] = event.price

        if event.bid is not None:
            self._latest_bid[symbol] = Decimal(str(event.bid))
        elif event.price:
            self._latest_bid[symbol] = Decimal(str(event.price))
        if event.ask is not None:
            self._latest_ask[symbol] = Decimal(str(event.ask))
        elif event.price:
            self._latest_ask[symbol] = Decimal(str(event.price))

        # Check if any resting limit orders should fill
        self._check_limit_fills(symbol)

    def _check_limit_fills(self, symbol: str) -> None:
        """Check resting limit orders against current prices."""
        to_fill = []
        for oid, order in list(self._open_orders.items()):
            if order.symbol != symbol:
                continue
            if order.order_type != OrderType.LIMIT:
                continue

            bid = self._latest_bid.get(symbol)
            ask = self._latest_ask.get(symbol)
            if bid is None or ask is None:
                continue

            # Buy limit fills when ask <= limit price
            if order.side == Side.BUY and ask <= order.price:
                to_fill.append((oid, order, ask, True))
            # Sell limit fills when bid >= limit price
            elif order.side == Side.SELL and bid >= order.price:
                to_fill.append((oid, order, bid, True))

        for oid, order, fill_price, is_maker in to_fill:
            self._execute_fill(order, fill_price, is_maker)

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------

    async def submit_order(self, order: Order) -> Order:
        """Submit order — market orders fill immediately, limits rest."""
        self._orders[order.order_id] = order

        if order.order_type == OrderType.MARKET:
            # Fill immediately at current price + slippage
            price = self._get_market_fill_price(order.symbol, order.side)
            if price is None:
                rejected = Order(
                    order_id=order.order_id,
                    symbol=order.symbol,
                    side=order.side,
                    order_type=order.order_type,
                    price=order.price,
                    qty=order.qty,
                    status=OrderStatus.REJECTED,
                    timestamp=datetime.now(timezone.utc),
                    metadata={**order.metadata, "reject_reason": "no_price_data"},
                )
                return rejected

            self._execute_fill(order, price, is_maker=False)
            return Order(
                order_id=order.order_id,
                symbol=order.symbol,
                side=order.side,
                order_type=order.order_type,
                price=price,
                qty=order.qty,
                status=OrderStatus.FILLED,
                timestamp=datetime.now(timezone.utc),
                metadata=order.metadata,
            )

        # Limit order — check for immediate fill, otherwise rest
        bid = self._latest_bid.get(order.symbol)
        ask = self._latest_ask.get(order.symbol)

        can_fill = False
        fill_price = order.price
        if bid is not None and ask is not None:
            if order.side == Side.BUY and ask <= order.price:
                can_fill = True
                fill_price = ask
            elif order.side == Side.SELL and bid >= order.price:
                can_fill = True
                fill_price = bid

        # Post-only: reject if it would cross
        if can_fill and order.metadata.get("post_only", False):
            rejected = Order(
                order_id=order.order_id,
                symbol=order.symbol,
                side=order.side,
                order_type=order.order_type,
                price=order.price,
                qty=order.qty,
                status=OrderStatus.REJECTED,
                timestamp=datetime.now(timezone.utc),
                metadata={**order.metadata, "reject_reason": "post_only_would_cross"},
            )
            return rejected

        if can_fill:
            self._execute_fill(order, fill_price, is_maker=False)
            return Order(
                order_id=order.order_id,
                symbol=order.symbol,
                side=order.side,
                order_type=order.order_type,
                price=fill_price,
                qty=order.qty,
                status=OrderStatus.FILLED,
                timestamp=datetime.now(timezone.utc),
                metadata=order.metadata,
            )

        # Rest as open limit order
        open_order = Order(
            order_id=order.order_id,
            symbol=order.symbol,
            side=order.side,
            order_type=order.order_type,
            price=order.price,
            qty=order.qty,
            status=OrderStatus.OPEN,
            timestamp=datetime.now(timezone.utc),
            metadata=order.metadata,
        )
        self._open_orders[order.order_id] = open_order
        self._orders[order.order_id] = open_order

        if self._bus:
            self._bus.publish(OrderEvent(order=open_order, event_type="submitted"))

        return open_order

    async def cancel_order(self, order_id: str) -> bool:
        if order_id in self._open_orders:
            del self._open_orders[order_id]
            return True
        return False

    async def get_order(self, order_id: str) -> Order:
        return self._orders.get(order_id)

    async def get_open_orders(self, symbol: str | None = None) -> list[Order]:
        orders = list(self._open_orders.values())
        if symbol:
            orders = [o for o in orders if o.symbol == symbol]
        return orders

    # ------------------------------------------------------------------
    # Account
    # ------------------------------------------------------------------

    async def get_balances(self) -> dict[str, Decimal]:
        return dict(self._balances)

    async def get_fee_rates(self) -> dict[str, Decimal]:
        return {"maker": self._maker_fee, "taker": self._taker_fee}

    async def get_ticker(self, symbol: str) -> TickerEvent:
        price = self._latest_price.get(symbol, 0.0)
        bid = float(self._latest_bid.get(symbol, 0))
        ask = float(self._latest_ask.get(symbol, 0))
        # Fallback: use last price as bid/ask when ticker_batch omits them
        if not bid and price:
            bid = price
        if not ask and price:
            ask = price
        return TickerEvent(
            symbol=symbol,
            price=price,
            timestamp=datetime.now(timezone.utc),
            bid=bid if bid else None,
            ask=ask if ask else None,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_market_fill_price(self, symbol: str, side: Side) -> Decimal | None:
        """Get fill price for a market order with slippage."""
        if side == Side.BUY:
            ask = self._latest_ask.get(symbol)
            if ask is None and symbol in self._latest_price:
                ask = Decimal(str(self._latest_price[symbol]))
            if ask is None:
                return None
            slip = ask * self._slippage_bps / Decimal("10000")
            return ask + slip
        else:
            bid = self._latest_bid.get(symbol)
            if bid is None and symbol in self._latest_price:
                bid = Decimal(str(self._latest_price[symbol]))
            if bid is None:
                return None
            slip = bid * self._slippage_bps / Decimal("10000")
            return bid - slip

    def _execute_fill(self, order: Order, fill_price: Decimal, is_maker: bool) -> None:
        """Simulate a fill — update balances and publish event."""
        fee_rate = self._maker_fee if is_maker else self._taker_fee
        fee = fill_price * order.qty * fee_rate

        # Update balances
        base_currency = order.symbol.split("-")[0] if "-" in order.symbol else order.symbol
        if order.side == Side.BUY:
            cost = fill_price * order.qty + fee
            self._balances["USD"] = self._balances.get("USD", Decimal("0")) - cost
            self._balances[base_currency] = (
                self._balances.get(base_currency, Decimal("0")) + order.qty
            )
        else:
            proceeds = fill_price * order.qty - fee
            self._balances["USD"] = self._balances.get("USD", Decimal("0")) + proceeds
            self._balances[base_currency] = (
                self._balances.get(base_currency, Decimal("0")) - order.qty
            )

        # Remove from open orders
        self._open_orders.pop(order.order_id, None)

        # Update order status
        filled_order = Order(
            order_id=order.order_id,
            symbol=order.symbol,
            side=order.side,
            order_type=order.order_type,
            price=fill_price,
            qty=order.qty,
            status=OrderStatus.FILLED,
            timestamp=datetime.now(timezone.utc),
            metadata=order.metadata,
        )
        self._orders[order.order_id] = filled_order

        fill = Fill(
            fill_id=str(uuid.uuid4()),
            order_id=order.order_id,
            symbol=order.symbol,
            side=order.side,
            price=fill_price,
            qty=order.qty,
            fee=fee,
            fee_currency="USD",
            is_maker=is_maker,
            timestamp=datetime.now(timezone.utc),
        )

        if self._bus:
            self._bus.publish(FillEvent(fill=fill))

        logger.info(
            "Paper fill: %s %s %s @ %s (fee: %s, maker: %s)",
            fill.side.value, fill.qty, fill.symbol, fill.price, fill.fee, is_maker,
        )
