# thin wrappers calling SQL, returning dicts
from __future__ import annotations

from typing import Dict, Any, Optional
from sqlalchemy.ext.asyncio import AsyncConnection
import sqlalchemy


# Single source of truth for the “Trade Stats (Window)” SQL
TRADE_STATS_SQL = """
WITH params AS (
  SELECT :start_ts::timestamptz AS start_ts, :end_ts::timestamptz AS end_ts
),
trades AS (
  SELECT realized_profit::numeric AS pnl, order_time::timestamptz AS ts
  FROM public.trade_records, params
  WHERE order_time >= params.start_ts AND order_time < params.end_ts
),
agg AS (
  SELECT
    COUNT(*)                                                   AS n_total,
    COUNT(*) FILTER (WHERE pnl > 0)                            AS n_wins,
    COUNT(*) FILTER (WHERE pnl < 0)                            AS n_losses,
    COUNT(*) FILTER (WHERE pnl = 0)                            AS n_breakeven,
    AVG(pnl) FILTER (WHERE pnl > 0)                            AS avg_win,
    AVG(CASE WHEN pnl < 0 THEN -pnl END)                       AS avg_loss_abs,
    SUM(pnl) FILTER (WHERE pnl > 0)                            AS gross_profit,
    SUM(CASE WHEN pnl < 0 THEN -pnl END)                       AS gross_loss_abs
  FROM trades
)
SELECT
  n_total,
  n_wins,
  n_losses,
  n_breakeven,
  ROUND(100.0 * n_wins::numeric / NULLIF(n_total,0), 1)        AS win_rate_incl_breakeven_pct,
  ROUND(avg_win, 4)                                            AS avg_win,
  ROUND(-avg_loss_abs, 4)                                      AS avg_loss,         -- negative
  ROUND(NULLIF(gross_profit,0) / NULLIF(gross_loss_abs,0), 3)  AS profit_factor,
  ROUND(
    ( (n_wins::numeric/NULLIF(n_total,0)) * COALESCE(avg_win,0) )
    - ( (n_losses::numeric/NULLIF(n_total,0)) * COALESCE(avg_loss_abs,0) )
  , 6)                                                         AS expectancy_per_trade
FROM agg;
"""


async def fetch_trade_stats(
    conn: AsyncConnection,
    start_ts: str,
    end_ts: str,
    *,
    use_report_trades: bool = False
) -> Optional[Dict[str, Any]]:
    """
    Compute Trade Stats (Avg Win, Avg Loss, PF, Win Rate, Expectancy) for [start_ts, end_ts).
    If use_report_trades=True, run the same math but on public.report_trades (ts, realized_pnl).
    """
    if use_report_trades:
        sql = TRADE_STATS_SQL.replace(
            "public.trade_records, params\n  WHERE order_time >= params.start_ts AND order_time < params.end_ts",
            "public.report_trades, params\n  WHERE ts >= params.start_ts AND ts < params.end_ts"
        ).replace("realized_profit", "realized_pnl").replace("order_time", "ts")
    else:
        sql = TRADE_STATS_SQL

    res = await conn.execute(
        sqlalchemy.text(sql),
        {"start_ts": start_ts, "end_ts": end_ts}
    )
    row = res.mappings().first()
    if not row:
        return None

    return {
        "n_total": int(row["n_total"] or 0),
        "n_wins": int(row["n_wins"] or 0),
        "n_losses": int(row["n_losses"] or 0),
        "n_breakeven": int(row["n_breakeven"] or 0),
        "win_rate_pct": float(row["win_rate_incl_breakeven_pct"] or 0.0),
        "avg_win": float(row["avg_win"] or 0.0),
        "avg_loss": float(row["avg_loss"] or 0.0),
        "profit_factor": float(row["profit_factor"] or 0.0),
        "expectancy_per_trade": float(row["expectancy_per_trade"] or 0.0),
    }
