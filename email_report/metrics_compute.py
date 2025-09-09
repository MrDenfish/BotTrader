# thin wrappers calling SQL, returning dicts
from __future__ import annotations

from typing import Dict, Any, Optional, Union
from datetime import datetime
import sqlalchemy
from sqlalchemy import bindparam, Numeric
from sqlalchemy.ext.asyncio import AsyncConnection


# -------- Trade Stats (Avg Win/Loss, PF, Expectancy, Win Rate) --------

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
    sql = TRADE_STATS_SQL_RT if use_report_trades else TRADE_STATS_SQL_TR
    row = (await conn.execute(sql, {"start_ts": start_ts, "end_ts": end_ts})).mappings().first()
    if not row:
        return None

    n_total = int(row["n_total"] or 0)
    n_wins  = int(row["n_wins"] or 0)
    n_loss  = int(row["n_losses"] or 0)
    n_be    = int(row["n_breakeven"] or 0)
    avg_win = float(row["avg_win"] or 0.0)
    avg_loss_abs = float(row["avg_loss_abs"] or 0.0)
    gp = float(row["gross_profit"] or 0.0)
    gl = float(row["gross_loss_abs"] or 0.0)

    win_rate = (100.0 * n_wins / n_total) if n_total else 0.0
    profit_factor = (gp / gl) if gl else 0.0
    expectancy = ((n_wins / n_total) * avg_win - (n_loss / n_total) * avg_loss_abs) if n_total else 0.0

    return {
        "n_total": n_total,
        "n_wins": n_wins,
        "n_losses": n_loss,
        "n_breakeven": n_be,
        "win_rate_pct": round(win_rate, 1),
        "avg_win": round(avg_win, 4),
        "avg_loss": round(-avg_loss_abs, 4),      # negative
        "profit_factor": round(profit_factor, 3),
        "expectancy_per_trade": round(expectancy, 6),
        "avg_w_over_avg_l": round((avg_win / avg_loss_abs), 3) if avg_loss_abs else 0.0,
    }


# ---------------------- Sharpe-like (per trade) -----------------------

SHARPE_TRADE_SQL_TR = sqlalchemy.text("""
WITH t AS (
  SELECT realized_profit::numeric AS pnl
  FROM public.trade_records
  WHERE order_time >= :start_ts AND order_time < :end_ts
)
SELECT AVG(pnl) AS mean_pnl, STDDEV_SAMP(pnl) AS stdev_pnl FROM t
""")

SHARPE_TRADE_SQL_RT = sqlalchemy.text("""
WITH t AS (
  SELECT realized_pnl::numeric AS pnl
  FROM public.report_trades
  WHERE ts >= :start_ts AND ts < :end_ts
)
SELECT AVG(pnl) AS mean_pnl, STDDEV_SAMP(pnl) AS stdev_pnl FROM t
""")

async def fetch_sharpe_trade(
    conn: AsyncConnection,
    start_ts: Union[datetime, str],
    end_ts: Union[datetime, str],
    *,
    use_report_trades: bool = False
) -> Optional[Dict[str, float]]:
    sql = SHARPE_TRADE_SQL_RT if use_report_trades else SHARPE_TRADE_SQL_TR
    row = (await conn.execute(sql, {"start_ts": start_ts, "end_ts": end_ts})).mappings().first()
    if not row:
        return None
    mean = float(row["mean_pnl"] or 0.0)
    stdev = float(row["stdev_pnl"] or 0.0)
    sharpe = (mean / stdev) if stdev else 0.0
    return {
        "mean_pnl_per_trade": round(mean, 6),
        "stdev_pnl_per_trade": round(stdev, 6),
        "sharpe_like_per_trade": round(sharpe, 6),
    }


# ----------------------- Max Drawdown (window) ------------------------

MDD_SQL_TR = sqlalchemy.text("""
WITH t AS (
  SELECT order_time AS ts, realized_profit::numeric AS pnl
  FROM public.trade_records
  WHERE order_time >= :start_ts AND order_time < :end_ts
  ORDER BY ts
),
curve AS (
  SELECT ts, SUM(pnl) OVER (ORDER BY ts) + :starting_equity AS equity
  FROM t
),
peaks AS (
  SELECT ts, equity, MAX(equity) OVER (ORDER BY ts) AS peak
  FROM curve
),
dd AS (
  SELECT
    ts,
    equity,
    peak,
    (equity - peak)                                        AS dd_abs,
    CASE WHEN peak > 0 THEN (equity - peak)/peak ELSE NULL END AS dd_pct
  FROM peaks
)
SELECT MIN(dd_pct) AS min_dd_pct, MIN(dd_abs) AS min_dd_abs FROM dd
""").bindparams(
    bindparam("start_ts"),
    bindparam("end_ts"),
    bindparam("starting_equity", type_=Numeric())
)

MDD_SQL_RT = sqlalchemy.text("""
WITH t AS (
  SELECT ts, realized_pnl::numeric AS pnl
  FROM public.report_trades
  WHERE ts >= :start_ts AND ts < :end_ts
  ORDER BY ts
),
curve AS (
  SELECT ts, SUM(pnl) OVER (ORDER BY ts) + :starting_equity AS equity
  FROM t
),
peaks AS (
  SELECT ts, equity, MAX(equity) OVER (ORDER BY ts) AS peak
  FROM curve
),
dd AS (
  SELECT
    ts,
    equity,
    peak,
    (equity - peak)                                        AS dd_abs,
    CASE WHEN peak > 0 THEN (equity - peak)/peak ELSE NULL END AS dd_pct
  FROM peaks
)
SELECT MIN(dd_pct) AS min_dd_pct, MIN(dd_abs) AS min_dd_abs FROM dd
""").bindparams(
    bindparam("start_ts"),
    bindparam("end_ts"),
    bindparam("starting_equity", type_=Numeric())
)

async def fetch_max_drawdown(
    conn: AsyncConnection,
    start_ts,
    end_ts,
    *,
    starting_equity: float,
    use_report_trades: bool = False,
):
    sql = MDD_SQL_RT if use_report_trades else MDD_SQL_TR
    row = (await conn.execute(sql, {
        "start_ts": start_ts,
        "end_ts": end_ts,
        "starting_equity": starting_equity
    })).mappings().first()
    if not row or row["min_dd_pct"] is None:
        return {"max_drawdown_pct": 0.0, "max_drawdown_abs": 0.0}
    return {
        "max_drawdown_pct": round(float(row["min_dd_pct"]) * 100.0, 2),
        "max_drawdown_abs": round(float(row["min_dd_abs"]), 2),
    }

# ---------------------- Capital & Exposure (positions) ----------------------

EXPOSURE_SQL = sqlalchemy.text("""
SELECT
  symbol,
  position_qty::numeric AS qty,
  avg_entry_price::numeric AS avg_price
FROM public.report_positions
""")

async def fetch_exposure_snapshot(
    conn: AsyncConnection,
    *,
    equity_usd: float,
    top_n: int = 5
) -> Dict[str, Any]:
    rows = (await conn.execute(EXPOSURE_SQL)).mappings().all()
    if not rows:
        return {
            "total_notional_usd": 0.0,
            "invested_pct": 0.0,
            "leverage": 0.0,
            "long_notional_usd": 0.0,
            "short_notional_usd": 0.0,
            "net_exposure_usd": 0.0,
            "net_exposure_pct": 0.0,
            "largest_exposure_pct": 0.0,
            "positions": []
        }

    positions = []
    total_notional = 0.0
    long_notional = 0.0
    short_notional = 0.0

    for r in rows:
        qty = float(r["qty"] or 0.0)
        avg_price = float(r["avg_price"] or 0.0)
        notional = abs(qty * avg_price)
        side = "long" if qty >= 0 else "short"
        total_notional += notional
        if side == "long":
            long_notional += notional
        else:
            short_notional += notional
        positions.append({
            "symbol": r["symbol"],
            "qty": qty,
            "avg_price": avg_price,
            "notional_usd": notional,
            "side": side,
        })

    # Sort + percentages
    positions.sort(key=lambda x: x["notional_usd"], reverse=True)
    if total_notional > 0:
        for p in positions:
            p["pct_of_total"] = round(100.0 * p["notional_usd"] / total_notional, 2)
        largest_exposure_pct = positions[0]["pct_of_total"]
    else:
        for p in positions:
            p["pct_of_total"] = 0.0
        largest_exposure_pct = 0.0

    invested_pct = (100.0 * total_notional / equity_usd) if equity_usd > 0 else 0.0
    leverage = (total_notional / equity_usd) if equity_usd > 0 else 0.0
    net_exposure_usd = long_notional - short_notional
    net_exposure_pct = (100.0 * net_exposure_usd / equity_usd) if equity_usd > 0 else 0.0

    return {
        "total_notional_usd": round(total_notional, 2),
        "invested_pct": round(invested_pct, 2),
        "leverage": round(leverage, 3),
        "long_notional_usd": round(long_notional, 2),
        "short_notional_usd": round(short_notional, 2),
        "net_exposure_usd": round(net_exposure_usd, 2),       # long - short (can be negative)
        "net_exposure_pct": round(net_exposure_pct, 2),       # % of equity
        "largest_exposure_pct": round(largest_exposure_pct, 2),
        "positions": positions[:top_n],
    }

