# thin wrappers calling SQL, returning dicts
from __future__ import annotations

import os
import json
import sqlalchemy
import pandas as pd

from pathlib import Path
from decimal import Decimal
from sqlalchemy.sql import text
from sqlalchemy import bindparam, Numeric
from sqlalchemy.engine import Engine, Connection
from datetime import datetime, timedelta, timezone
from sqlalchemy.ext.asyncio import AsyncConnection
from typing import Dict, Any, Optional, Union, Tuple, List


# -------------------------
# Env-driven table / column config (back-compat)
# -------------------------

TRADES_TABLE        = os.getenv("REPORT_TRADES_TABLE", "public.trade_records")
POSITIONS_TABLE     = os.getenv("REPORT_POSITIONS_TABLE", "public.report_positions")
PRICES_TABLE        = os.getenv("REPORT_PRICE_TABLE", "public.report_prices")
BALANCES_TABLE      = os.getenv("REPORT_BALANCES_TABLE", "public.report_balances")

COL_SYMBOL          = os.getenv("REPORT_COL_SYMBOL") or "symbol"
COL_SIDE            = os.getenv("REPORT_COL_SIDE")   or "side"
COL_PRICE           = os.getenv("REPORT_COL_PRICE")  or "price"
COL_SIZE            = os.getenv("REPORT_COL_SIZE")   or "qty_signed"
COL_TIME            = os.getenv("REPORT_COL_TIME")   or "ts"
COL_POS_QTY         = os.getenv("REPORT_COL_POS_QTY") or "position_qty"
COL_PNL             = os.getenv("REPORT_COL_PNL")    or "realized_profit"  # fallback "pnl_usd"
COL_PNL_FALLBACK    = "pnl_usd"

PRICE_COL           = os.getenv("REPORT_PRICE_COL")      or "price"
PRICE_TIME_COL      = os.getenv("REPORT_PRICE_TIME_COL") or "ts"
PRICE_SYM_COL       = os.getenv("REPORT_PRICE_SYM_COL")  or "symbol"

CASH_SYM_COL        = os.getenv("REPORT_CASH_SYM_COL")   or "symbol"
CASH_AMT_COL        = os.getenv("REPORT_CASH_AMT_COL")   or "balance"
CASH_SYMBOLS        = [s.strip().upper() for s in os.getenv("REPORT_CASH_SYMBOLS", "USD,USDC,USDT").split(",") if s.strip()]

# -------------------------
# Helpers
# -------------------------

def _ensure_conn(engine_or_conn: Engine | Connection) -> Tuple[Connection, bool]:
    """Return (conn, own_conn) where own_conn indicates we opened it."""
    if hasattr(engine_or_conn, "execute") and not hasattr(engine_or_conn, "begin"):
        # legacy style
        conn = engine_or_conn  # type: ignore
        return conn, False
    raise TypeError("Expected SQLAlchemy Engine or Connection")

def _safe_decimal(x) -> Optional[float]:
    if x is None:
        return None
    if isinstance(x, Decimal):
        return float(x)
    try:
        return float(x)
    except Exception:
        return None

def _table_exists(conn: Connection, fqtn: str) -> bool:
    """
    Check if a fully-qualified table name exists (e.g., 'public.report_positions').
    """
    if "." in fqtn:
        schema, name = fqtn.split(".", 1)
    else:
        schema, name = "public", fqtn
    chk = text("""
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = :schema AND table_name = :name
        LIMIT 1
    """)
    row = conn.execute(chk, {"schema": schema, "name": name}).fetchone()
    return bool(row)
# -------------------------
# Queries (minimal; adjust to your schema as needed)
# -------------------------

def query_trade_pnls(engine_or_conn: Engine | Connection, start: datetime, end: datetime) -> Tuple[List[float], List[float], List[float], int]:
    """
    Pull closed-trade PnL from trade_records within [start, end).
    - Uses realized_profit primarily; falls back to pnl_usd if realized_profit is NULL.
    - Breakevens counted with an epsilon to avoid float noise.
    """
    eps = float(os.getenv("BREAKEVEN_EPS", "1e-9"))

    # COALESCE(realized_profit, pnl_usd) covers both older and newer rows
    sql = text(f"""
        SELECT COALESCE({COL_PNL}, {COL_PNL_FALLBACK}) AS pnl
        FROM {TRADES_TABLE}
        WHERE {COL_TIME} >= :start
          AND {COL_TIME} <  :end
          AND COALESCE({COL_PNL}, {COL_PNL_FALLBACK}) IS NOT NULL
    """)

    conn, own = _ensure_conn(engine_or_conn)
    try:
        rows = conn.execute(sql, {"start": start, "end": end}).fetchall()
    finally:
        if own: conn.close()

    closed, wins, losses = [], [], []
    breakevens = 0
    for (pnl,) in rows:
        v = _safe_decimal(pnl)
        if v is None:
            continue
        closed.append(v)
        if v > eps:
            wins.append(v)
        elif v < -eps:
            losses.append(v)
        else:
            breakevens += 1

    return closed, wins, losses, breakevens

# --------------------------------------------
# Score Snapshot from JSONL log (if available)
# --------------------------------------------
def load_score_jsonl(path: str | None = None, since_hours: int = 24):
    """Reads SCORE_JSONL_PATH and normalizes the JSON lines.
         Single source of truth for score data."""
    path = path or os.getenv("SCORE_JSONL_PATH", "/app/logs/score_log.jsonl")
    base = Path(path)
    if not base.parent.exists():

        return pd.DataFrame() if "pd" in globals() else []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)

    # collect current file + any rotated siblings (… .YYYY-MM-DD)
    candidates = [p for p in [base] + sorted(base.parent.glob(base.name + ".*")) if p.exists()]

    rows = []
    for fp in candidates:
        try:
            # skip files clearly older than the window based on mtime
            mtime = datetime.fromtimestamp(fp.stat().st_mtime, tz=timezone.utc)
            if mtime < cutoff - timedelta(hours=1):
                continue
            with fp.open("r") as f:
                for line in f:
                    s = line.strip()
                    if not s:
                        continue
                    try:
                        if not s.startswith("{"):
                            jstart = s.find("{")
                            if jstart == -1:
                                continue
                            s = s[jstart:]
                        obj = json.loads(s)
                    except Exception:
                        continue
                    ts = obj.get("ts")
                    if ts:
                        try:
                            t = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                            if t.tzinfo is None:
                                t = t.replace(tzinfo=timezone.utc)
                            if t < cutoff:
                                continue
                        except Exception:
                            pass
                    rows.append(obj)
        except Exception:
            continue

    # return DataFrame if you're already using pandas downstream; otherwise list
    try:
        import pandas as pd
        return pd.json_normalize(rows)
    except Exception:
        return rows
def load_score_jsonl(path: str | None = None, since_hours: int = 24) -> pd.DataFrame:
    """Reads SCORE_JSONL_PATH and normalizes the JSON lines.
     Single source of truth for score data."""

    path = path or os.getenv("SCORE_JSONL_PATH", "logs/score_log.jsonl")
    if not os.path.exists(path):
        return pd.DataFrame()
    rows = []
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                # also support lines that look like: "📊 score_snapshot {...}"
                try:
                    jstart = line.index("{")
                    obj = json.loads(line[jstart:])
                except Exception:
                    continue
            rows.append(obj)
    if not rows:
        return pd.DataFrame()
    df = pd.json_normalize(rows)
    if "ts" in df.columns:
        df["ts"] = pd.to_datetime(df["ts"], errors="coerce", utc=True)
        cutoff = pd.Timestamp.utcnow() - pd.Timedelta(hours=since_hours)
        df = df[df["ts"] >= cutoff]
    return df

def score_snapshot_metrics_from_jsonl(df: pd.DataFrame) -> dict:
    """Aggregates contributions (top buy/sell indicators) and recent buy/sell entries.
    This is the metrics payload the email renderer will consume."""

    if df.empty:
        return {"empty": True}

    # Build contributions table from top components
    contrib_rows = []
    for _, r in df.iterrows():
        for side, col in (("buy", "top_buy_components"), ("sell", "top_sell_components")):
            comps = r.get(col, []) or []
            for c in comps:
                contrib_rows.append({
                    "side": side,
                    "indicator": c.get("indicator"),
                    "contribution": float(c.get("contribution", 0.0))
                })
    contrib = pd.DataFrame(contrib_rows)
    top_buy = pd.DataFrame()
    top_sell = pd.DataFrame()
    if not contrib.empty:
        g = contrib.groupby(["side", "indicator"], dropna=False)["contribution"].sum().reset_index()
        top_buy  = g[g["side"]=="buy"].nlargest(10, "contribution")[["indicator","contribution"]]
        top_sell = g[g["side"]=="sell"].nlargest(10, "contribution")[["indicator","contribution"]]

    # Recent entries (one per (ts,symbol))
    have_cols = [c for c in ["ts","symbol","action","trigger","buy_score","sell_score"] if c in df.columns]
    entries = df[have_cols].drop_duplicates(subset=["ts","symbol"]) if {"ts","symbol"}.issubset(df.columns) else pd.DataFrame()
    entries = entries[entries["action"].isin(["buy","sell"])] if not entries.empty else entries

    return {
        "empty": False,
        "rows": int(len(df)),
        "symbols": int(df["symbol"].nunique()) if "symbol" in df else 0,
        "top_buy": top_buy,
        "top_sell": top_sell,
        "entries": entries,
    }

def render_score_section_jsonl(metrics) -> str:
    """Produces a small HTML block with tables that stylistically match the current report
       (bordered tables, simple headings)."""

    if metrics.get("empty"):
        return "<h2>Signal Score Snapshot (last 24h)</h2><p>No score data found.</p>"

    def _to_html(df):
        try:
            return df.to_html(index=False, border=0, classes="table table-sm", float_format=lambda x: f"{x:.4g}")
        except Exception:
            return "<p>(table render error)</p>"

    parts = []
    parts.append("<h2>Signal Score Snapshot (last 24h)</h2>")
    parts.append(f"<p>Symbols: <b>{metrics['symbols']}</b> &nbsp; Rows: <b>{metrics['rows']}</b></p>")
    parts.append("<h3>Top contributing indicators (Buy)</h3>")
    parts.append(_to_html(metrics["top_buy"]))
    parts.append("<h3>Top contributing indicators (Sell)</h3>")
    parts.append(_to_html(metrics["top_sell"]))
    if "entries" in metrics and not metrics["entries"].empty:
        tail = metrics["entries"].sort_values("ts").tail(20)
        cols = [c for c in ["ts","symbol","action","trigger","buy_score","sell_score"] if c in tail.columns]
        parts.append("<h3>Most recent entries</h3>")
        parts.append(_to_html(tail[cols]))
    return "\n".join(parts)


# -------------------------
def query_open_positions(engine_or_conn: Engine | Connection, as_of: datetime) -> List[dict]:
    """
    Return list of open positions with schema:
      {symbol, side, qty, avg_price, notional, pct_total}
    If you don't have a positions table, compute from your own holdings view.
    """
    # This is a generic shape. Adjust columns to your table.
    sql = text(f"""
        SELECT
            {COL_SYMBOL} AS symbol,
            {COL_SIDE}   AS side,
            {COL_POS_QTY} AS qty,
            {COL_PRICE}  AS avg_price
        FROM {POSITIONS_TABLE}
        WHERE {COL_POS_QTY} IS NOT NULL
          AND ABS({COL_POS_QTY}) > 0
    """)
    conn, own = _ensure_conn(engine_or_conn)
    try:
        rows = conn.execute(sql).fetchall()
    finally:
        if own: conn.close()

    out = []
    total_notional = 0.0
    for symbol, side, qty, avg_price in rows:
        qty_f = _safe_decimal(qty) or 0.0
        px_f  = _safe_decimal(avg_price) or 0.0
        notional = abs(qty_f) * px_f
        total_notional += notional
        out.append({
            "symbol": symbol,
            "side": (str(side).lower() if side is not None else ("long" if qty_f >= 0 else "short")),
            "qty": qty_f,
            "avg_price": px_f,
            "notional": notional,
            # pct_total filled later once total is known
        })

    # fill % of total
    if total_notional > 0:
        for row in out:
            row["pct_total"] = (100.0 * row["notional"] / total_notional)
    else:
        for row in out:
            row["pct_total"] = 0.0
    return out

def query_cash_balances(engine_or_conn: Engine | Connection, as_of: datetime) -> float:
    """
    Sum balances for cash-like symbols (USD/USDC/USDT by default).
    Adjust the filter if your table uses different naming.
    """
    if not BALANCES_TABLE:
        return 0.0

    placeholders = ", ".join([f":s{i}" for i in range(len(CASH_SYMBOLS))]) or "''"
    sql = text(f"""
        SELECT COALESCE(SUM({CASH_AMT_COL}), 0) AS cash_total
        FROM {BALANCES_TABLE}
        WHERE UPPER({CASH_SYM_COL}) IN ({placeholders})
    """)
    params = {f"s{i}": sym for i, sym in enumerate(CASH_SYMBOLS)}

    conn, own = _ensure_conn(engine_or_conn)
    try:
        row = conn.execute(sql, params).fetchone()
    finally:
        if own: conn.close()

    return float(row[0] or 0.0) if row else 0.0

def compute_exposure_totals(open_positions: List[dict], equity_usd: Optional[float]) -> dict:
    """
    Turn positions list into the exposure_totals dict expected by the report.
    """
    total_notional = sum(abs(p["notional"] or 0.0) for p in open_positions) if open_positions else 0.0
    long_notional  = sum((p["notional"] or 0.0) for p in open_positions if (p.get("side") == "long" or (p.get("qty", 0.0) >= 0)))
    short_notional = sum(abs(p["notional"] or 0.0) for p in open_positions if (p.get("side") == "short" or (p.get("qty", 0.0) < 0)))
    net_exposure   = long_notional - short_notional

    invested_pct   = (100.0 * total_notional / equity_usd) if equity_usd and equity_usd > 0 else None
    leverage_used  = (total_notional / equity_usd) if equity_usd and equity_usd > 0 else None
    net_pct        = (100.0 * net_exposure / equity_usd) if equity_usd and equity_usd > 0 else None

    return {
        "total_notional": round(total_notional, 2),
        "invested_pct_of_equity": (round(invested_pct, 2) if invested_pct is not None else None),
        "leverage_used": (round(leverage_used, 3) if leverage_used is not None else None),
        "long_notional": round(long_notional, 2),
        "short_notional": round(short_notional, 2),
        "net_abs": round(net_exposure, 2),
        "net_pct": (round(net_pct, 2) if net_pct is not None else None),
    }

def query_unrealized_pnl_placeholder(open_positions: List[dict]) -> Optional[float]:
    """
    If you don't have a live price table plugged yet, keep unrealized at 0 or None.
    Replace this with a join to PRICES_TABLE on symbol=PRICE_SYM_COL, latest <= end.
    """
    return 0.0

# -------------------------
# Public: compute everything the report needs for a window
# -------------------------

def compute_windowed_metrics(engine_or_conn: Engine | Connection,
                             start: datetime,
                             end: datetime,
                             source: str) -> dict:
    closed, wins, losses, breakevens = query_trade_pnls(engine_or_conn, start, end)
    open_pos = query_open_positions(engine_or_conn, end)
    cash_usd = query_cash_balances(engine_or_conn, end)
    exposure_totals = compute_exposure_totals(open_pos, equity_usd=cash_usd if cash_usd > 0 else None)

    realized_pnl   = sum(closed) if closed else 0.0
    unrealized_pnl = query_unrealized_pnl_placeholder(open_pos)

    return {
        "as_of_iso": end.isoformat(),
        "closed_trade_pnls": closed,
        "wins": wins,
        "losses": losses,
        "breakevens": breakevens,
        "open_positions": open_pos,
        "exposure_totals": exposure_totals,
        "realized_pnl": realized_pnl,
        "unrealized_pnl": unrealized_pnl,
    }
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

