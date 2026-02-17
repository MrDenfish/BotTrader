"""Trade log collector — individual fills with per-sell FIFO P&L."""

from __future__ import annotations

import json
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
    """Fetch every fill in the period and annotate sells with FIFO P&L.

    Pre-populates buy queues with all historical buys before the period
    so that cross-period sells get correct FIFO P&L instead of $0.00.
    """
    # Fetch all fills BEFORE the period to build prior buy queues
    prior_rows = await pool.fetch(
        """
        SELECT symbol, side, price, qty, fee, metadata
        FROM v2_fills
        WHERE timestamp < $1
        ORDER BY timestamp ASC
        """,
        start,
    )
    prior_queues = _build_prior_buy_queues(prior_rows)

    # Fetch fills in this period
    rows = await pool.fetch(
        """
        SELECT symbol, side, price, qty, fee, is_maker, timestamp, metadata
        FROM v2_fills
        WHERE timestamp >= $1 AND timestamp < $2
        ORDER BY timestamp ASC
        """,
        start,
        end,
    )
    return _annotate_with_pnl(rows, prior_queues)


def _build_prior_buy_queues(rows: list) -> dict[str, deque]:
    """Replay all historical fills to compute remaining buy lots per symbol."""
    buy_queues: dict[str, deque] = defaultdict(deque)
    for row in rows:
        symbol = row["symbol"]
        side = row["side"].upper()
        qty = float(row["qty"])
        price = float(row["price"])
        fee = float(row["fee"])

        if side == "BUY":
            fee_per_unit = fee / qty if qty else 0.0
            buy_queues[symbol].append((qty, price, fee_per_unit))
        elif side == "SELL":
            remaining = qty
            while remaining > 1e-12 and buy_queues[symbol]:
                bq_qty, bq_price, bq_fee_per_unit = buy_queues[symbol][0]
                matched = min(remaining, bq_qty)
                leftover = bq_qty - matched
                if leftover > 1e-12:
                    buy_queues[symbol][0] = (leftover, bq_price, bq_fee_per_unit)
                else:
                    buy_queues[symbol].popleft()
                remaining -= matched
    return buy_queues


def _annotate_with_pnl(
    rows: list,
    prior_queues: dict[str, deque] | None = None,
) -> list[TradeLogEntry]:
    """Attach realized P&L to sell fills using FIFO matching."""
    # Per-symbol buy queue: deque of (remaining_qty, price, fee_per_unit)
    buy_queues: dict[str, deque] = prior_queues if prior_queues else defaultdict(deque)
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

        # Extract exit_reason from metadata JSONB
        exit_reason = None
        raw_meta = row.get("metadata")
        if raw_meta:
            try:
                meta = json.loads(raw_meta) if isinstance(raw_meta, str) else raw_meta
                exit_reason = meta.get("exit_reason") or meta.get("signal_reason")
            except (json.JSONDecodeError, TypeError, AttributeError):
                pass

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
            exit_reason=exit_reason,
        ))

    return entries
