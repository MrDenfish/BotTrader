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
) -> TradeStats:
    """Aggregate fill statistics for the reporting period."""
    rows = await pool.fetch(
        """
        SELECT side,
               COUNT(*)::int AS cnt,
               COALESCE(SUM(price * qty), 0) AS volume,
               COALESCE(SUM(fee), 0) AS fees
        FROM v2_fills
        WHERE timestamp >= $1 AND timestamp < $2
        GROUP BY side
        """,
        start,
        end,
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
        """
        SELECT DISTINCT symbol FROM v2_fills
        WHERE timestamp >= $1 AND timestamp < $2
        ORDER BY symbol
        """,
        start,
        end,
    )
    stats.symbols_traded = [r["symbol"] for r in symbols]
    return stats
