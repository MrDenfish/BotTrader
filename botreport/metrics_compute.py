# thin wrappers calling SQL, returning dicts
from __future__ import annotations

import os
import json
import sqlalchemy
import pandas as pd

from pathlib import Path
from decimal import Decimal
from statistics import median
from sqlalchemy.sql import text
from sqlalchemy import bindparam, Numeric
from collections import Counter, defaultdict
from sqlalchemy.engine import Engine, Connection
from datetime import datetime, timedelta, timezone
from sqlalchemy.ext.asyncio import AsyncConnection
from Config.config_manager import CentralConfig as Config
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

config = Config()
SCORE_JSONL_PATH = config.score_jsonl_path()
# -------------------------
# Helpers
# -------------------------

def _parse_ts(s):
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None

def _is_df(obj):
    try:
        import pandas as pd  # noqa
        return hasattr(obj, "to_dict")
    except Exception:
        return False

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
def load_score_jsonl(path: str = None, since_hours: int = 24) -> pd.DataFrame:
    """
    Read score JSONL and return a pandas DataFrame filtered to the last `since_hours`.
    Returns an empty DataFrame if the file doesn't exist or is unreadable.
    """
    base = Path(SCORE_JSONL_PATH)

    # If directory or file is missing, just return empty DF
    if not base.parent.exists():
        return pd.DataFrame() if "pd" in globals() else []

    candidates = [base] + sorted(base.parent.glob(base.name + ".*"))
    rows = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    for fp in candidates:
        if not fp.exists():
            continue
        try:
            with fp.open("r") as f:
                for line in f:
                    s = line.strip()
                    if not s:
                        continue
                    if s[0] != "{":
                        j = s.find("{")
                        if j < 0:
                            continue
                        s = s[j:]
                    try:
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
    if "pd" in globals():
        return pd.DataFrame(rows)

    return rows

def score_snapshot_metrics_from_jsonl(df_or_rows, *, max_recent: int = 15):
    """
    INPUT: pandas.DataFrame or list[dict] of score events with schema like:
      {
        "ts": "...",
        "symbol": "BTC-USD",
        "action": "buy|sell|hold",
        "trigger": "score|...",

        "buy_score": float,
        "sell_score": float,
        "top_buy_components": [{"indicator","decision","value","threshold","weight","contribution"}, ...],
        "top_sell_components": [{"indicator","decision","value","threshold","weight","contribution"}, ...],
        ...
      }

    OUTPUT (extends prior keys without breaking them):
      {
        "empty": bool,
        "rows": int,
        "symbols": int,
        "top_buy": list[(indicator, total_contribution)],
        "top_sell": list[(indicator, total_contribution)],
        "entries": list[...],  # unchanged if present
        # NEW:
        "buy_table": list[dict],  # for HTML rendering
        "sell_table": list[dict],
      }
    """
    # Normalize to a list[dict]
    if df_or_rows is None:
        rows = []
    elif _is_df(df_or_rows):
        rows = [r for r in df_or_rows.to_dict(orient="records")]
    else:
        rows = list(df_or_rows)

    if not rows:
        return {"empty": True}

    # Aggregate contributions, "fires", weights, thresholds (by side/indicator)
    contrib = {"buy": Counter(), "sell": Counter()}
    fires   = {"buy": Counter(), "sell": Counter()}  # decision==1 count
    weights = {"buy": defaultdict(list), "sell": defaultdict(list)}
    thresh  = {"buy": defaultdict(list), "sell": defaultdict(list)}

    symbols = set()
    # keep your "entries" concept (recent action rows) but don't rely on it
    entries = []
    recent_all = []
    trigger_counts = Counter()

    for e in rows:
        sym = e.get("symbol")
        if sym: symbols.add(sym)
        trig = (e.get("trigger") or "").strip() or "<none>"
        trigger_counts[trig] += 1

        # keep recent entries if you'd like (buy/sell actions)
        act = (e.get("action") or "").lower()
        if act in ("buy","sell"):
            entries.append({
                "ts": e.get("ts"),
                "symbol": sym,
                "action": act,
                "trigger": e.get("trigger"),
                "buy_score": e.get("buy_score"),
                "sell_score": e.get("sell_score"),
            })

        for side, key in (("buy","top_buy_components"),("sell","top_sell_components")):
            comps = e.get(key) or []
            for c in comps:
                ind = c.get("indicator")
                if not ind:
                    continue
                contrib_val = float(c.get("contribution", 0.0) or 0.0)
                contrib[side][ind] += contrib_val

                # decision==1 means the indicator "fired"
                try:
                    if int(c.get("decision", 0) or 0) == 1:
                        fires[side][ind] += 1
                except Exception:
                    pass
                # collect medians for config sanity
                w = c.get("weight")
                t = c.get("threshold")
                try:
                    if w is not None:
                        weights[side][ind].append(float(w))
                except Exception:
                    pass
                try:
                    if t is not None:
                        thresh[side][ind].append(float(t))
                except Exception:
                    pass

    # old outputs (preserved)
    top_buy  = contrib["buy"].most_common(10)
    top_sell = contrib["sell"].most_common(10)

    # NEW: normalized tables for HTML
    def _table_for(side):
        total = sum(abs(v) for v in contrib[side].values()) or 1.0
        rows_ = []
        for ind, tot_c in contrib[side].most_common():
            r = {
                "indicator": ind,
                "fires": int(fires[side][ind] or 0),
                "total_contrib": float(tot_c),
                "contrib_pct": (abs(tot_c)/total*100.0),
                "median_weight": (median(weights[side][ind]) if weights[side][ind] else None),
                "median_threshold": (median(thresh[side][ind]) if thresh[side][ind] else None),
            }
            rows_.append(r)
        return rows_

    buy_table  = _table_for("buy")
    sell_table = _table_for("sell")

      # Build a truly “most recent” list (no action/trigger filter)
      # Sort newest → oldest by ts
    def _dt(row):
        return _parse_ts(row.get("ts")) or datetime.min.replace(tzinfo=timezone.utc)

  # keep your original buy/sell limited list
    entries = sorted(entries, key=_dt)[-20:]
  # the unfiltered “recent” (max_recent newest rows)
    recent_all = sorted(rows, key=_dt, reverse=True)[:max_recent]
  # convenience split views (optional)
    recent_overrides = [r for r in recent_all if (r.get("trigger") == "roc_momo_override")]
    recent_weighted = [r for r in recent_all if str(r.get("trigger", "")).startswith("score")]

    return {
        "empty": False,
        "rows": len(rows),
        "symbols": len(symbols),
        "top_buy": top_buy,
        "top_sell": top_sell,
        "entries": entries,
        "recent": [
                       {
                               "ts": r.get("ts"),
                               "symbol": r.get("symbol"),
                               "action": r.get("action"),
                               "trigger": r.get("trigger"),
                               "price": r.get("price"),
                               "buy_score": r.get("buy_score"),
                               "sell_score": r.get("sell_score"),
               } for r in recent_all
                           ],
               "recent_overrides": [
                       {
                               "ts": r.get("ts"),
                               "symbol": r.get("symbol"),
                               "action": r.get("action"),
                               "trigger": r.get("trigger"),
                               "price": r.get("price"),
                               "buy_score": r.get("buy_score"),
                               "sell_score": r.get("sell_score"),
               } for r in recent_overrides
                           ],
               "recent_weighted": [
                       {
                               "ts": r.get("ts"),
                               "symbol": r.get("symbol"),
                               "action": r.get("action"),
                               "trigger": r.get("trigger"),
                               "price": r.get("price"),
                               "buy_score": r.get("buy_score"),
                               "sell_score": r.get("sell_score"),
               } for r in recent_weighted
                           ],
               "trigger_mix": dict(sorted(trigger_counts.items(), key=lambda kv: -kv[1])),
        # NEW enriched tables:
        "buy_table": buy_table,
        "sell_table": sell_table,
    }



def render_score_section_jsonl(metrics: dict) -> str:
    """Enriched HTML for the Signal Score Snapshot (non-breaking)."""
    if not metrics or metrics.get("empty"):
        return "<h2>Signal Score Snapshot (last 24h)</h2><p>No score data found.</p>"

    def _fmt(x, nd=3):
        if x is None: return "—"
        try: return f"{float(x):.{nd}f}"
        except Exception: return str(x)

    def _tbl(title, rows):
        head = (
            "<tr>"
            "<th>Indicator</th>"
            "<th>Fires</th>"
            "<th>Total<br/>Contribution</th>"
            "<th>Contrib %</th>"
            "<th>Median Weight</th>"
            "<th>Median Threshold</th>"
            "</tr>"
        )
        body = []
        if not rows:
            body.append("<tr><td colspan='99'>None</td></tr>")
        else:
            for r in rows[:12]:
                body.append(
                    "<tr>"
                    f"<td>{r['indicator']}</td>"
                    f"<td>{r['fires']}</td>"
                    f"<td>{_fmt(r['total_contrib'])}</td>"
                    f"<td>{_fmt(r['contrib_pct'],2)}%</td>"
                    f"<td>{_fmt(r['median_weight'])}</td>"
                    f"<td>{_fmt(r['median_threshold'])}</td>"
                    "</tr>"
                )
        return (
            f"<h4>{title}</h4>"
            "<table border='1' cellpadding='6' cellspacing='0'>"
            f"{head}{''.join(body)}"
            "</table>"
        )

    html = []
    html.append("<h2>Signal Score Snapshot (last 24h)</h2>")
    html.append(f"<p>Symbols: <b>{metrics.get('symbols',0)}</b> &nbsp; Rows: <b>{metrics.get('rows',0)}</b></p>")

    # explanatory legend
    html.append(
        "<p style='color:#555;font-size:90%'>"
        "<b>Fires</b>: count of bars where the indicator's decision==1. "
        "<b>Total Contribution</b>: sum of raw contributions used by your scorer (can be ±). "
        "<b>Contrib %</b>: absolute contribution normalized within side. "
        "Medians help sanity-check the configured <i>weight</i> and <i>threshold</i> actually observed in production."
        "</p>"
    )

    html.append(_tbl("Top contributing indicators (Buy)",  metrics.get("buy_table")  or []))
    html.append(_tbl("Top contributing indicators (Sell)", metrics.get("sell_table") or []))

    # ---- Most recent (unfiltered, truly recent) ----
    recent = metrics.get("recent") or []

    if recent:
        head = ("<tr><th>Time (UTC)</th><th>Symbol</th><th>Action</th>"
                "<th>Trigger</th><th>Price</th><th>Buy</th><th>Sell</th></tr>")
        body = "".join(
            "<tr>"
            f"<td>{r.get('ts', '')}</td>"
            f"<td>{r.get('symbol', '')}</td>"
            f"<td>{r.get('action', '')}</td>"
            f"<td>{r.get('trigger', '')}</td>"
            f"<td style='text-align:right'>{_fmt(r.get('price'))}</td>"
            f"<td style='text-align:right'>{_fmt(r.get('buy_score'))}</td>"
            f"<td style='text-align:right'>{_fmt(r.get('sell_score'))}</td>"
            "</tr>"
            for r in recent
       )
        html.append("<h4>Most recent score entries</h4>")
        html.append("<table border='1' cellpadding='6' cellspacing='0'>" + head + body + "</table>")

    # (Optional) Splits to verify paths at a glance
    ro = metrics.get("recent_overrides") or []
    rw = metrics.get("recent_weighted") or []
    def _sub(title, rows):
        if not rows: return ""
        body = "".join(
            "<tr>"
            f"<td>{r.get('ts', '')}</td><td>{r.get('symbol', '')}</td>"
            f"<td>{r.get('action', '')}</td>"
            f"<td style='text-align:right'>{_fmt(r.get('price'))}</td>"
            f"<td style='text-align:right'>{_fmt(r.get('buy_score'))}</td>"
            f"<td style='text-align:right'>{_fmt(r.get('sell_score'))}</td>"
            "</tr>" for r in rows
       )
        return ("<h5 style='margin:8px 0 2px 0'>" + title + "</h5>"
                "<table border='1' cellpadding='6' cellspacing='0'>"
                "<tr><th>Time</th><th>Symbol</th><th>Action</th><th>Price</th><th>Buy</th><th>Sell</th></tr>"
                + body + "</table>")
    html.append(_sub("Overrides (roc_momo_override)", ro))
    html.append(_sub("Weighted scorer (score*)", rw))


    return "\n".join(html)



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
  SELECT COALESCE(realized_profit, pnl_usd)::numeric AS pnl
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
  PERCENTILE_DISC(0.50) WITHIN GROUP (ORDER BY pnl) 
    FILTER (WHERE pnl > 0)                                   AS p50_win,
  PERCENTILE_DISC(0.90) WITHIN GROUP (ORDER BY pnl) 
    FILTER (WHERE pnl > 0)                                   AS p90_win,
  PERCENTILE_DISC(0.50) WITHIN GROUP (ORDER BY -pnl) 
    FILTER (WHERE pnl < 0)                                   AS p50_loss_abs,
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

def load_tpsl_jsonl(path: str, since_hours: int = 24):
    """
    Read tpsl.jsonl and return list[dict] for the last `since_hours` hours.
    Robust to garbage lines / partial writes.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    out = []
    if not os.path.exists(path):
        return out
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            # if the file sometimes has prefixes, try to find the JSON start:
            if not line.startswith("{"):
                j = line.find("{")
                if j == -1:
                    continue
                line = line[j:]
            try:
                o = json.loads(line)
                ts = o.get("ts")
                if not ts:
                    continue
                t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                if t.tzinfo is None:
                    t = t.replace(tzinfo=timezone.utc)
                if t >= cutoff:
                    out.append(o)
            except Exception:
                continue
    return out

def aggregate_tpsl(rows):
    """
    Returns per-symbol aggregates and global summaries.
    """
    per = defaultdict(lambda: {
        "n": 0, "avg_rr": 0.0, "p50_rr": None, "p10_rr": None,
        "avg_tp_pct": 0.0, "avg_stop_pct": 0.0,
        "avg_atr_pct": 0.0, "avg_spread_pct": 0.0, "avg_fee_pct": 0.0
    })
    by_symbol_rr = defaultdict(list)

    for r in rows:
        sym = r.get("symbol", "?")
        per[sym]["n"] += 1
        for k_src, k_dst in [
            ("rr", "avg_rr"),
            ("tp_pct", "avg_tp_pct"),
            ("stop_pct", "avg_stop_pct"),
            ("atr_pct", "avg_atr_pct"),
            ("cushion_spread", "avg_spread_pct"),
            ("cushion_fee", "avg_fee_pct"),
        ]:
            v = r.get(k_src)
            if isinstance(v, (int, float)) and v == v:
                per[sym][k_dst] += v
        if isinstance(r.get("rr"), (int, float)):
            by_symbol_rr[sym].append(float(r["rr"]))

    # finalize avgs & simple quantiles
    for sym, agg in per.items():
        n = agg["n"] or 1
        agg["avg_rr"]        /= n
        agg["avg_tp_pct"]    /= n
        agg["avg_stop_pct"]  /= n
        agg["avg_atr_pct"]   /= n
        agg["avg_spread_pct"]/= n
        agg["avg_fee_pct"]   /= n
        if by_symbol_rr[sym]:
            arr = sorted(by_symbol_rr[sym])
            mid = len(arr)//2
            agg["p50_rr"] = arr[mid] if len(arr)%2==1 else 0.5*(arr[mid-1]+arr[mid])
            p10_idx = max(0, int(0.10*len(arr))-1)
            agg["p10_rr"] = arr[p10_idx]

    # global summary
    total = sum(v["n"] for v in per.values())
    global_rrs = [rr for lst in by_symbol_rr.values() for rr in lst]
    global_summary = {
        "n": total,
        "avg_rr": (sum(global_rrs)/len(global_rrs)) if global_rrs else 0.0,
        "p50_rr": (sorted(global_rrs)[len(global_rrs)//2] if global_rrs else None),
        "bad_rr_share": (sum(1 for rr in global_rrs if rr < 1.0)/len(global_rrs)) if global_rrs else 0.0,
    }
    return per, global_summary

def render_tpsl_section(per, global_summary, max_rows=10):
    if global_summary["n"] == 0:
        return "<h3>TP/SL (last 24h)</h3><p>No TP/SL decisions recorded.</p>"

    rows = sorted(per.items(), key=lambda kv: kv[1]["avg_rr"])[:max_rows]

    def pct(x):
        return f"{x*100:.2f}%" if isinstance(x, (int, float)) else "—"

    html = [
        "<h3>TP/SL Decision Quality (last 24h)</h3>",
        (
            f"<p>Total: <b>{global_summary['n']}</b> | "
            f"Avg R:R: <b>{global_summary['avg_rr']:.2f}</b> | "
            f"Median R:R: <b>{(global_summary['p50_rr'] or 0):.2f}</b> | "
            f"RR&lt;1.0 share: <b>{pct(global_summary['bad_rr_share'])}</b></p>"
        ),
        "<table border='1' cellspacing='0' cellpadding='4'>",
        "<tr><th>Symbol</th><th>N</th><th>Avg R:R</th><th>P10 R:R</th>"
        "<th>Avg TP%</th><th>Avg Stop%</th><th>ATR%</th><th>Spread%</th><th>Fee%</th></tr>"
    ]

    for sym, agg in rows:
        p10_val = agg["p10_rr"]
        p10_str = "-" if p10_val is None else f"{p10_val:.2f}"

        html.append(
            "<tr>"
            f"<td>{sym}</td>"
            f"<td>{agg['n']}</td>"
            f"<td>{agg['avg_rr']:.2f}</td>"
            f"<td>{p10_str}</td>"
            f"<td>{pct(agg['avg_tp_pct'])}</td>"
            f"<td>{pct(agg['avg_stop_pct'])}</td>"
            f"<td>{pct(agg['avg_atr_pct'])}</td>"
            f"<td>{pct(agg['avg_spread_pct'])}</td>"
            f"<td>{pct(agg['avg_fee_pct'])}</td>"
            "</tr>"
        )
    html.append("</table>")
    html.append("<p style='font-size:12px;color:#666'>"
                "ⓘ <b>ATR%</b> and <b>Spread%</b> are <i>tunable</i> inputs—exposing them here helps you adjust strategy thresholds."
                "</p>")
    return "\n".join(html)

def render_tpsl_suggestions(per: dict, global_summary: dict) -> str:
    notes = []
    if not global_summary or not global_summary.get("n"):
        return ""

    # Example heuristics — tweak to taste
    if global_summary.get("avg_rr", 0) < 1.10:
        notes.append("Average R:R < 1.10 — consider raising TP or loosening SL (or both).")

    high_spread = [s for s, a in per.items()
                   if (a.get("avg_spread_pct") or 0) > 0.003]  # >0.3%
    if high_spread:
        notes.append(f"High spread on {', '.join(high_spread[:10])} — consider excluding or widening cushions.")

    if not notes:
        return ""

    return "<h4>Suggested Tweaks</h4><ul>" + "".join(f"<li>{n}</li>" for n in notes) + "</ul>"
