"""Backtest exchange with fill simulation.

Unlike the no-op BacktestExchange (which leaves fill simulation to the
strategy), this exchange immediately fills every submitted order at the
current bar's price with configurable slippage and fees.  It publishes
FillEvent on the event bus so the full v2 pipeline (portfolio tracking,
risk managers, observers) processes each fill identically to live mode.

Designed for strategies like composite_scoring that rely on the execution
pipeline for fill handling rather than managing fills internally.
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


@registry.plugin("exchange", "backtest_sim")
class BacktestFillSimExchange(ExchangeAdapter):
    """Simulated exchange for backtesting with immediate fill simulation.

    Subscribes to TickerEvent to track the latest price per symbol.
    Every order submitted is filled immediately at the current price
    (with slippage applied), and a FillEvent is published on the bus.
    """

    name = "backtest_sim"

    def __init__(
        self,
        event_bus: EventBus | None = None,
        initial_balance_usd: float = 10_000.0,
        maker_fee: float = 0.0025,
        taker_fee: float = 0.004,
        slippage_bps: float = 1.0,
        exchange_name: str = "",
        **kwargs: Any,
    ) -> None:
        if exchange_name:
            self.name = exchange_name
        self._bus = event_bus
        self._maker_fee = Decimal(str(maker_fee))
        self._taker_fee = Decimal(str(taker_fee))
        self._slippage_bps = Decimal(str(slippage_bps))

        # Account state
        self._balances: dict[str, Decimal] = {"USD": Decimal(str(initial_balance_usd))}
        self._orders: dict[str, Order] = {}

        # Latest prices and simulated time from ticker events
        self._latest_price: dict[str, Decimal] = {}
        self._simulated_time: datetime = datetime.now(timezone.utc)

    async def connect(self) -> None:
        if self._bus:
            self._bus.subscribe(TickerEvent, self._on_ticker)
        logger.info(
            "Backtest fill-sim exchange connected (balance=$%s, maker=%.4f, taker=%.4f)",
            self._balances["USD"], self._maker_fee, self._taker_fee,
        )

    async def disconnect(self) -> None:
        logger.info("Backtest fill-sim exchange disconnected")

    def _on_ticker(self, event: TickerEvent) -> None:
        if event.price:
            self._latest_price[event.symbol] = Decimal(str(event.price))
        if event.timestamp:
            self._simulated_time = event.timestamp

    # ------------------------------------------------------------------
    # Orders — all fill immediately
    # ------------------------------------------------------------------

    async def submit_order(self, order: Order) -> Order:
        price = self._latest_price.get(order.symbol)
        if price is None:
            rejected = Order(
                order_id=order.order_id,
                symbol=order.symbol,
                side=order.side,
                order_type=order.order_type,
                price=order.price,
                qty=order.qty,
                status=OrderStatus.REJECTED,
                timestamp=self._simulated_time,
                metadata={**order.metadata, "reject_reason": "no_price_data"},
            )
            self._orders[order.order_id] = rejected
            return rejected

        # Apply slippage: buy higher, sell lower
        slip = price * self._slippage_bps / Decimal("10000")
        if order.side == Side.BUY:
            fill_price = price + slip
        else:
            fill_price = price - slip

        # Use limit price if it's better for the trader
        if order.order_type == OrderType.LIMIT and order.price:
            if order.side == Side.BUY and order.price < fill_price:
                fill_price = order.price
            elif order.side == Side.SELL and order.price > fill_price:
                fill_price = order.price

        # Determine fee: limit orders are maker, market orders are taker
        is_maker = order.order_type == OrderType.LIMIT
        fee_rate = self._maker_fee if is_maker else self._taker_fee
        fee = fill_price * order.qty * fee_rate

        # Update balances
        base = order.symbol.split("-")[0] if "-" in order.symbol else order.symbol
        if order.side == Side.BUY:
            cost = fill_price * order.qty + fee
            self._balances["USD"] -= cost
            self._balances[base] = self._balances.get(base, Decimal("0")) + order.qty
        else:
            current = self._balances.get(base, Decimal("0"))
            sell_qty = order.qty
            # Clamp dust differences
            if sell_qty > current and (sell_qty - current) / max(sell_qty, Decimal("1e-18")) < Decimal("1e-9"):
                sell_qty = current
            if current < sell_qty:
                logger.warning(
                    "Backtest sell rejected: %s balance %s < qty %s",
                    base, current, sell_qty,
                )
                rejected = Order(
                    order_id=order.order_id,
                    symbol=order.symbol,
                    side=order.side,
                    order_type=order.order_type,
                    price=order.price,
                    qty=order.qty,
                    status=OrderStatus.REJECTED,
                    timestamp=self._simulated_time,
                    metadata={**order.metadata, "reject_reason": "insufficient_balance"},
                )
                self._orders[order.order_id] = rejected
                return rejected
            proceeds = fill_price * sell_qty - fee
            self._balances["USD"] += proceeds
            self._balances[base] = current - sell_qty

        # Record filled order
        filled = Order(
            order_id=order.order_id,
            symbol=order.symbol,
            side=order.side,
            order_type=order.order_type,
            price=fill_price,
            qty=order.qty,
            status=OrderStatus.FILLED,
            timestamp=self._simulated_time,
            metadata=order.metadata,
        )
        self._orders[order.order_id] = filled

        # Publish fill event
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
            timestamp=self._simulated_time,
            metadata=order.metadata,
        )
        if self._bus:
            self._bus.publish(FillEvent(fill=fill))

        return filled

    async def cancel_order(self, order_id: str) -> bool:
        return order_id in self._orders

    async def get_order(self, order_id: str) -> Order:
        return self._orders.get(order_id)

    async def get_open_orders(self, symbol: str | None = None) -> list[Order]:
        return []  # All orders fill immediately

    # ------------------------------------------------------------------
    # Account
    # ------------------------------------------------------------------

    async def get_balances(self) -> dict[str, Decimal]:
        return dict(self._balances)

    async def get_fee_rates(self) -> dict[str, Decimal]:
        return {"maker": self._maker_fee, "taker": self._taker_fee}

    async def get_ticker(self, symbol: str) -> TickerEvent:
        price = float(self._latest_price.get(symbol, 0))
        # Synthetic bid/ask with slippage-width spread
        slip = price * float(self._slippage_bps) / 10_000
        return TickerEvent(
            symbol=symbol, price=price, timestamp=self._simulated_time,
            bid=price - slip, ask=price + slip,
        )
