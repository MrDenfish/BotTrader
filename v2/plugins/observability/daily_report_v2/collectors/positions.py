"""Open positions collector — reads from v2_positions table."""

from __future__ import annotations

import asyncpg

from ..models import PositionSnapshot


async def collect_positions(
    pool: asyncpg.Pool,
    exchange: str = "",
    last_prices: dict[str, float] | None = None,
) -> list[PositionSnapshot]:
    """Fetch all open positions (qty > 0) from v2_positions.

    If *last_prices* is provided, compute mark-to-market unrealized P&L
    from live ticker prices instead of using the (always-zero) DB column.
    """
    params: list = []
    exchange_clause = ""
    if exchange:
        params.append(exchange)
        exchange_clause = f" AND exchange = ${len(params)}"

    rows = await pool.fetch(
        f"""
        SELECT symbol, qty, avg_entry_price, cost_basis, unrealized_pnl
        FROM v2_positions
        WHERE qty > 0{exchange_clause}
        ORDER BY symbol
        """,
        *params,
    )
    positions = []
    for row in rows:
        symbol = row["symbol"]
        qty = row["qty"]
        cost_basis = row["cost_basis"]
        current_price = None
        unrealized = row["unrealized_pnl"]

        if last_prices and symbol in last_prices:
            current_price = last_prices[symbol]
            unrealized = qty * current_price - cost_basis

        positions.append(PositionSnapshot(
            symbol=symbol,
            qty=qty,
            avg_entry=row["avg_entry_price"],
            cost_basis=cost_basis,
            current_price=current_price,
            unrealized_pnl=unrealized,
        ))
    return positions
