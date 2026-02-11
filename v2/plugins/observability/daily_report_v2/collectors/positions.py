"""Open positions collector — reads from v2_positions table."""

from __future__ import annotations

import asyncpg

from ..models import PositionSnapshot


async def collect_positions(pool: asyncpg.Pool) -> list[PositionSnapshot]:
    """Fetch all open positions (qty > 0) from v2_positions."""
    rows = await pool.fetch(
        """
        SELECT symbol, qty, avg_entry_price, cost_basis, unrealized_pnl
        FROM v2_positions
        WHERE qty > 0
        ORDER BY symbol
        """
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
