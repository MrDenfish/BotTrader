"""Open positions collector — reads from v2_positions table."""

from __future__ import annotations

import asyncpg

from ..models import PositionSnapshot


async def collect_positions(pool: asyncpg.Pool, exchange: str = "") -> list[PositionSnapshot]:
    """Fetch all open positions (qty > 0) from v2_positions."""
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
    return [
        PositionSnapshot(
            symbol=row["symbol"],
            qty=row["qty"],
            avg_entry=row["avg_entry_price"],
            cost_basis=row["cost_basis"],
            current_price=None,
            unrealized_pnl=row["unrealized_pnl"],
        )
        for row in rows
    ]
