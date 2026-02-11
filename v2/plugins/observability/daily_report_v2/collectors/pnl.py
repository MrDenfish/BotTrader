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
) -> PnLSummary:
    """Compute FIFO-based realized P&L for fills in the period."""
    rows = await pool.fetch(
        """
        SELECT symbol, side, price, qty, fee
        FROM v2_fills
        WHERE timestamp >= $1 AND timestamp < $2
        ORDER BY timestamp ASC
        """,
        start,
        end,
    )
    return compute_fifo_pnl(rows)


def compute_fifo_pnl(rows: list) -> PnLSummary:
    """Pure computation: FIFO P&L from a list of fill rows.

    Each row must have: symbol, side, price, qty, fee.
    Accepts asyncpg Records or dicts.
    """
    # Per-symbol buy queue: deque of (remaining_qty, price, fee_per_unit)
    buy_queues: dict[str, deque] = defaultdict(deque)

    trades: list[tuple[str, Decimal]] = []  # (symbol, realized_pnl)
    total_fees = Decimal("0")
    by_symbol: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))

    for row in rows:
        symbol = row["symbol"]
        side = row["side"].upper()
        price = Decimal(str(row["price"]))
        qty = Decimal(str(row["qty"]))
        fee = Decimal(str(row["fee"]))
        total_fees += fee

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
    )
