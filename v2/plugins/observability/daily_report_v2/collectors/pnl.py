"""FIFO P&L collector — per-symbol buy queue, realized P&L from v2_fills."""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime
from decimal import Decimal

import asyncpg

from ..models import PnLSummary


async def collect_pnl(
    pool: asyncpg.Pool,
    start: datetime,
    end: datetime,
    exchange: str = "",
) -> PnLSummary:
    """Compute FIFO-based realized P&L for fills in the period.

    Pre-populates buy queues from all fills before the period so that
    cross-period sells (bought earlier, sold in this window) get correct
    FIFO P&L instead of $0.
    """
    prior_params: list = [start]
    exchange_clause_1 = ""
    if exchange:
        prior_params.append(exchange)
        exchange_clause_1 = f" AND exchange = ${len(prior_params)}"

    # Fetch all fills BEFORE the period to build prior buy queues
    prior_rows = await pool.fetch(
        f"""
        SELECT symbol, side, price, qty, fee
        FROM v2_fills
        WHERE timestamp < $1{exchange_clause_1}
        ORDER BY timestamp ASC
        """,
        *prior_params,
    )
    prior_queues = _build_prior_buy_queues(prior_rows)

    # Fetch fills in this period
    period_params: list = [start, end]
    exchange_clause_2 = ""
    if exchange:
        period_params.append(exchange)
        exchange_clause_2 = f" AND exchange = ${len(period_params)}"
    rows = await pool.fetch(
        f"""
        SELECT symbol, side, price, qty, fee
        FROM v2_fills
        WHERE timestamp >= $1 AND timestamp < $2{exchange_clause_2}
        ORDER BY timestamp ASC
        """,
        *period_params,
    )
    return compute_fifo_pnl(rows, prior_queues=prior_queues)


def _build_prior_buy_queues(rows: list) -> dict[str, deque]:
    """Replay all historical fills to compute remaining buy lots per symbol."""
    buy_queues: dict[str, deque] = defaultdict(deque)
    for row in rows:
        symbol = row["symbol"]
        side = row["side"].upper()
        qty = Decimal(str(row["qty"]))
        price = Decimal(str(row["price"]))
        fee = Decimal(str(row["fee"]))

        if side == "BUY":
            fee_per_unit = fee / qty if qty else Decimal("0")
            buy_queues[symbol].append((qty, price, fee_per_unit))
        elif side == "SELL":
            remaining = qty
            while remaining > Decimal("1e-12") and buy_queues[symbol]:
                bq_qty, bq_price, bq_fee_per_unit = buy_queues[symbol][0]
                matched = min(remaining, bq_qty)
                leftover = bq_qty - matched
                if leftover > Decimal("1e-12"):
                    buy_queues[symbol][0] = (leftover, bq_price, bq_fee_per_unit)
                else:
                    buy_queues[symbol].popleft()
                remaining -= matched
    return buy_queues


def compute_fifo_pnl(
    rows: list,
    prior_queues: dict[str, deque] | None = None,
) -> PnLSummary:
    """Pure computation: FIFO P&L from a list of fill rows.

    Each row must have: symbol, side, price, qty, fee.
    Accepts asyncpg Records or dicts.

    If *prior_queues* is provided, it pre-populates the buy queues so
    that cross-period sells get correct FIFO P&L.
    """
    # Per-symbol buy queue: deque of (remaining_qty, price, fee_per_unit)
    # Wrap prior_queues in defaultdict so new symbols work seamlessly.
    buy_queues: dict[str, deque] = defaultdict(deque)
    if prior_queues:
        buy_queues.update(prior_queues)

    trades: list[tuple[str, Decimal]] = []  # (symbol, realized_pnl)
    total_fees = Decimal("0")
    by_symbol: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    fills_by_symbol: dict[str, dict[str, int]] = defaultdict(lambda: {"BUY": 0, "SELL": 0})

    for row in rows:
        symbol = row["symbol"]
        side = row["side"].upper()
        price = Decimal(str(row["price"]))
        qty = Decimal(str(row["qty"]))
        fee = Decimal(str(row["fee"]))
        total_fees += fee

        fills_by_symbol[symbol][side] += 1

        if side == "BUY":
            fee_per_unit = fee / qty if qty else Decimal("0")
            buy_queues[symbol].append((qty, price, fee_per_unit))
        elif side == "SELL":
            sell_fee_per_unit = fee / qty if qty else Decimal("0")
            remaining = qty
            trade_pnl = Decimal("0")

            while remaining > Decimal("0") and buy_queues[symbol]:
                bq_qty, bq_price, bq_fee_per_unit = buy_queues[symbol][0]
                matched = min(remaining, bq_qty)

                # P&L = (sell - buy) * qty - fees on both sides
                gross = (price - bq_price) * matched
                buy_fees = bq_fee_per_unit * matched
                sell_fees = sell_fee_per_unit * matched
                trade_pnl += gross - buy_fees - sell_fees

                leftover = bq_qty - matched
                if leftover > Decimal("0"):
                    buy_queues[symbol][0] = (leftover, bq_price, bq_fee_per_unit)
                else:
                    buy_queues[symbol].popleft()

                remaining -= matched

            trades.append((symbol, trade_pnl))
            by_symbol[symbol] += trade_pnl

    # Aggregate
    wins = [(s, p) for s, p in trades if p > 0]
    losses = [(s, p) for s, p in trades if p <= 0]
    total_realized = sum(p for _, p in trades) if trades else Decimal("0")

    return PnLSummary(
        realized_pnl=total_realized,
        total_fees=total_fees,
        net_pnl=total_realized,  # Fees already deducted in FIFO calc
        win_count=len(wins),
        loss_count=len(losses),
        win_rate=len(wins) / len(trades) if trades else 0.0,
        avg_win=sum(p for _, p in wins) / len(wins) if wins else Decimal("0"),
        avg_loss=sum(p for _, p in losses) / len(losses) if losses else Decimal("0"),
        best_trade=max(trades, key=lambda t: t[1]) if trades else None,
        worst_trade=min(trades, key=lambda t: t[1]) if trades else None,
        by_symbol=dict(by_symbol),
        fills_by_symbol=dict(fills_by_symbol),
    )
