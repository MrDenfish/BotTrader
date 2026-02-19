"""Trade statistics collector — fill counts, volume, fees from v2_fills."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import asyncpg

from ..models import TradeStats


async def collect_trade_stats(
    pool: asyncpg.Pool,
    start: datetime,
    end: datetime,
    exchange: str = "",
) -> TradeStats:
    """Aggregate fill statistics for the reporting period."""
    exchange_clause, params = _exchange_filter(exchange, start, end)
    rows = await pool.fetch(
        f"""
        SELECT side,
               COUNT(*)::int AS cnt,
               COALESCE(SUM(price * qty), 0) AS volume,
               COALESCE(SUM(fee), 0) AS fees
        FROM v2_fills
        WHERE timestamp >= $1 AND timestamp < $2{exchange_clause}
        GROUP BY side
        """,
        *params,
    )

    stats = TradeStats()
    for row in rows:
        side = row["side"].upper()
        if side == "BUY":
            stats.buy_count = row["cnt"]
            stats.buy_volume_usd = Decimal(str(row["volume"]))
        elif side == "SELL":
            stats.sell_count = row["cnt"]
            stats.sell_volume_usd = Decimal(str(row["volume"]))
        stats.total_fees += Decimal(str(row["fees"]))

    symbols = await pool.fetch(
        f"""
        SELECT DISTINCT symbol FROM v2_fills
        WHERE timestamp >= $1 AND timestamp < $2{exchange_clause}
        ORDER BY symbol
        """,
        *params,
    )
    stats.symbols_traded = [r["symbol"] for r in symbols]
    return stats


def _exchange_filter(exchange: str, start: datetime, end: datetime) -> tuple[str, list]:
    """Build exchange WHERE clause and params list (start, end[, exchange])."""
    params: list = [start, end]
    if exchange:
        params.append(exchange)
        return f" AND exchange = ${len(params)}", params
    return "", params
