"""Trade log collector — individual fills with per-sell FIFO P&L."""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime
from decimal import Decimal

import asyncpg

from ..models import TradeLogEntry


async def collect_trade_log(
    pool: asyncpg.Pool,
    start: datetime,
    end: datetime,
) -> list[TradeLogEntry]:
    """Fetch every fill in the period and annotate sells with FIFO P&L."""
    rows = await pool.fetch(
        """
        SELECT symbol, side, price, qty, fee, is_maker, timestamp
        FROM v2_fills
        WHERE timestamp >= $1 AND timestamp < $2
        ORDER BY timestamp ASC
        """,
        start,
        end,
    )
    return _annotate_with_pnl(rows)


def _annotate_with_pnl(rows: list) -> list[TradeLogEntry]:
    """Attach realized P&L to sell fills using FIFO matching."""
    # Per-symbol buy queue: deque of (remaining_qty, price, fee_per_unit)
    buy_queues: dict[str, deque] = defaultdict(deque)
    entries: list[TradeLogEntry] = []

    for row in rows:
        symbol = row["symbol"]
        side = row["side"].upper()
        price = float(row["price"])
        qty = float(row["qty"])
        fee = float(row["fee"])
        is_maker = bool(row["is_maker"]) if row["is_maker"] is not None else False
        ts = row["timestamp"]

        notional = price * qty
        realized_pnl = None

        if side == "BUY":
            fee_per_unit = fee / qty if qty else 0.0
            buy_queues[symbol].append((qty, price, fee_per_unit))
        elif side == "SELL":
            sell_fee_per_unit = fee / qty if qty else 0.0
            remaining = qty
            trade_pnl = 0.0

            while remaining > 1e-12 and buy_queues[symbol]:
                bq_qty, bq_price, bq_fee_per_unit = buy_queues[symbol][0]
                matched = min(remaining, bq_qty)

                gross = (price - bq_price) * matched
                buy_fees = bq_fee_per_unit * matched
                sell_fees = sell_fee_per_unit * matched
                trade_pnl += gross - buy_fees - sell_fees

                leftover = bq_qty - matched
                if leftover > 1e-12:
                    buy_queues[symbol][0] = (leftover, bq_price, bq_fee_per_unit)
                else:
                    buy_queues[symbol].popleft()

                remaining -= matched

            realized_pnl = trade_pnl

        entries.append(TradeLogEntry(
            timestamp=ts,
            symbol=symbol,
            side=side,
            price=price,
            qty=qty,
            notional=notional,
            fee=fee,
            is_maker=is_maker,
            realized_pnl=realized_pnl,
        ))

    return entries
