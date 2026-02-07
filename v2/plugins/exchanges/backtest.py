"""Backtest exchange adapter.

Minimal exchange for backtesting. In the hybrid_4h_maker strategy, fill
simulation is handled internally by the strategy's state machine (checking
bar.low <= entry_price, etc.), so this exchange just tracks orders and
provides account state.
"""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from typing import Any

from v2.core import registry
from v2.core.event_bus import EventBus
from v2.core.interfaces import ExchangeAdapter
from v2.core.types import (
    Order,
    OrderStatus,
    OrderType,
    Side,
    TickerEvent,
)

logger = logging.getLogger(__name__)


@registry.plugin("exchange", "backtest")
class BacktestExchange(ExchangeAdapter):
    """Simulated exchange for backtesting.

    The hybrid_4h_maker strategy manages fills internally via its state
    machine, so this exchange adapter is intentionally minimal — it tracks
    submitted orders and provides balances/fees but does not simulate fills.
    """

    name = "backtest"

    def __init__(
        self,
        event_bus: EventBus | None = None,
        initial_balance_usd: float = 10_000.0,
        maker_fee: float = 0.006,
        taker_fee: float = 0.012,
        **kwargs: Any,
    ) -> None:
        self._bus = event_bus
        self._balance = Decimal(str(initial_balance_usd))
        self._maker_fee = Decimal(str(maker_fee))
        self._taker_fee = Decimal(str(taker_fee))
        self._orders: dict[str, Order] = {}
        self._next_order_id = 1

    async def connect(self) -> None:
        logger.info("Backtest exchange connected (balance=$%s)", self._balance)

    async def disconnect(self) -> None:
        logger.info("Backtest exchange disconnected")

    # Orders
    async def submit_order(self, order: Order) -> Order:
        self._orders[order.order_id] = order
        return order

    async def cancel_order(self, order_id: str) -> bool:
        if order_id in self._orders:
            del self._orders[order_id]
            return True
        return False

    async def get_order(self, order_id: str) -> Order:
        return self._orders.get(order_id)

    async def get_open_orders(self, symbol: str | None = None) -> list[Order]:
        orders = list(self._orders.values())
        if symbol:
            orders = [o for o in orders if o.symbol == symbol]
        return [o for o in orders if o.status in (OrderStatus.PENDING, OrderStatus.OPEN)]

    # Account
    async def get_balances(self) -> dict[str, Decimal]:
        return {"USD": self._balance}

    async def get_fee_rates(self) -> dict[str, Decimal]:
        return {"maker": self._maker_fee, "taker": self._taker_fee}

    async def get_ticker(self, symbol: str) -> TickerEvent:
        return TickerEvent(
            symbol=symbol, price=0.0, timestamp=datetime.now(),
        )
