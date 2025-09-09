# thin wrappers calling SQL, returning dicts
from __future__ import annotations

from typing import Dict, Any, Optional, Union
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncConnection
import sqlalchemy


# Inline-binds version (no CTE, no ::timestamptz casts)
TRADE_STATS_SQL_TR = sqlalchemy.text("""
WITH trades AS (
  SELECT realized_profit::numeric AS pnl
  FROM public.trade_records
  WHERE order_time >= :start_ts AND order_time < :end_ts
)
SELECT
  COUNT(*)                                                   AS n_total,
  COUNT(*) FILTER (WHERE pnl > 0)                            AS n_wins,
  COUNT(*) FILTER (WHERE pnl < 0)                            AS n_losses,
  COUNT(*) FILTER (WHERE pnl = 0)                            AS n_breakeven,
  ROUND(AVG(pnl) FILTER (WHERE pnl > 0), 4)                  AS avg_win,
  ROUND(AVG(CASE WHEN pnl < 0 THEN -pnl END), 4)             AS avg_loss_abs,
  SUM(pnl) FILTER (WHERE pnl > 0)                            AS gross_profit,
  SUM(CASE WHEN pnl < 0 THEN -pnl END)                       AS gross_loss_abs
FROM trades
""")

TRADE_STATS_SQL_RT = sqlalchemy.text("""
WITH trades AS (
  SELECT realized_pnl::numeric AS pnl
  FROM public.report_trades
  WHERE ts >= :start_ts AND ts < :end_ts
)
SELECT
  COUNT(*)                                                   AS n_total,
  COUNT(*) FILTER (WHERE pnl > 0)                            AS n_wins,
  COUNT(*) FILTER (WHERE pnl < 0)                            AS n_losses,
  COUNT(*) FILTER (WHERE pnl = 0)                            AS n_breakeven,
  ROUND(AVG(pnl) FILTER (WHERE pnl > 0), 4)                  AS avg_win,
  ROUND(AVG(CASE WHEN pnl < 0 THEN -pnl END), 4)             AS avg_loss_abs,
  SUM(pnl) FILTER (WHERE pnl > 0)                            AS gross_profit,
  SUM(CASE WHEN pnl < 0 THEN -pnl END)                       AS gross_loss_abs
FROM trades
""")


async def fetch_trade_stats(
    conn: AsyncConnection,
    start_ts: Union[datetime, str],
    end_ts: Union[datetime, str],
    *,
    use_report_trades: bool = False
) -> Optional[Dict[str, Any]]:
    """
    Compute Trade Stats (Avg Win, Avg Loss, PF, Win Rate, Expectancy) for [start_ts, end_ts).
    Accepts aware datetimes or ISO strings; binds directly (no CAST in SQL).
    """
    sql = TRADE_STATS_SQL_RT if use_report_trades else TRADE_STATS_SQL_TR

    # Let SQLAlchemy/asyncpg handle the types; datetimes preferred
    params = {"start_ts": start_ts, "end_ts": end_ts}

    res = await conn.execute(sql, params)
    base = res.mappings().first()
    if not base:
        return None

    n_total = int(base["n_total"] or 0)
    n_wins = int(base["n_wins"] or 0)
    n_losses = int(base["n_losses"] or 0)
    n_be = int(base["n_breakeven"] or 0)
    avg_win = float(base["avg_win"] or 0.0)
    avg_loss_abs = float(base["avg_loss_abs"] or 0.0)
    gp = float(base["gross_profit"] or 0.0)
    gl = float(base["gross_loss_abs"] or 0.0)

    # Derived fields
    win_rate = (100.0 * n_wins / n_total) if n_total else 0.0
    profit_factor = (gp / gl) if gl else 0.0
    expectancy = ((n_wins / n_total) * avg_win - (n_losses / n_total) * avg_loss_abs) if n_total else 0.0

    return {
        "n_total": n_total,
        "n_wins": n_wins,
        "n_losses": n_losses,
        "n_breakeven": n_be,
        "win_rate_pct": round(win_rate, 1),
        "avg_win": round(avg_win, 4),
        "avg_loss": round(-avg_loss_abs, 4),      # negative
        "profit_factor": round(profit_factor, 3),
        "expectancy_per_trade": round(expectancy, 6),
    }

