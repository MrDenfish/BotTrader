

#!/usr/bin/env python3

""" AWS Daily Report Emailer
Scheduling is managed externally
 - host-level cron job
 - 6 hour cycle.

 """
import os
import io
import csv
import ssl
import json
import boto3
import pandas as pd
import pg8000.native as pg



from pathlib import Path
from decimal import Decimal
from email.mime.text import MIMEText
from email.utils import getaddresses
from statistics import mean, pstdev
from datetime import datetime, timezone, timedelta
from sqlalchemy import text, create_engine
from urllib.parse import urlparse, parse_qs
from collections import defaultdict, Counter
from typing import Optional, List, Dict, Tuple
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from email_report_print_format import build_console_report
from metrics_compute import load_score_jsonl, score_snapshot_metrics_from_jsonl, render_score_section_jsonl


from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePublicKeyWithSerialization

# -------------------------
# Config / Environment
# -------------------------
SENDER = RECIPIENTS= REGION=None

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None

REPORT_EXECUTIONS_TABLE = os.getenv("REPORT_EXECUTIONS_TABLE", "public.trade_records")
# Where SignalManager writes JSONL; can override via env
SCORE_JSONL_PATH = os.getenv("SCORE_JSONL_PATH", os.path.join("logs", "score_log.jsonl"))

def load_report_dotenv():
    """
    Local-only env loader for the email report.
    - If RUNNING_IN_DOCKER=true: do nothing (Compose/entrypoint env wins).
    - Else: load ENV_FILE (absolute path) or project-root/.env_runtime by default.
    - Does NOT override already-set env vars (override=False).
    - Falls back to .env_tradebot only if .env_runtime is missing.
    """
    if os.getenv("IN_DOCKER", "false").lower() == "true":
        return
    if load_dotenv is None:
        return

    here = Path(__file__).resolve()
    project_root = here.parents[1]  # .../BotTrader/
    in_docker = os.getenv("IN_DOCKER")
    env_path = Path(project_root / ".env_runtime") if in_docker else (project_root / ".env_tradebot")
    if not env_path.is_absolute():
        env_path = (project_root / env_path).resolve()

    if env_path.exists():
        load_dotenv(dotenv_path=env_path, override=False)
        return

    fallback = (project_root / ".env_tradebot")
    if fallback.exists():
        load_dotenv(dotenv_path=fallback, override=False)
IN_DOCKER = os.getenv("IN_DOCKER", "false").lower() == "true"
REGION = os.getenv("AWS_REGION", "us-west-2")
SENDER = os.getenv("REPORT_SENDER", "reports@a1zoobies.com").strip()
RECIPIENTS = [addr for _, addr in getaddresses([os.getenv("REPORT_RECIPIENTS", "dennfish@gmail.com")]) if addr]
if not SENDER or not RECIPIENTS:
    raise ValueError(f"Bad email config. REPORT_SENDER={SENDER!r}, REPORT_RECIPIENTS={os.getenv('REPORT_RECIPIENTS')!r}")

TAKER_FEE = Decimal(os.getenv("TAKER_FEE", "0.0040"))
MAKER_FEE = Decimal(os.getenv("MAKER_FEE", "0.0025"))

DEBUG = os.getenv("REPORT_DEBUG", "0").strip() in {"1", "true", "TRUE", "yes", "Yes"}

# Trading tables
REPORT_TRADES_TABLE = os.getenv("REPORT_TRADES_TABLE", "public.trade_records")
REPORT_POSITIONS_TABLE = os.getenv("REPORT_POSITIONS_TABLE", "public.report_positions")
REPORT_PNL_TABLE = os.getenv("REPORT_PNL_TABLE", "public.trade_records")

# Column overrides (optional)
REPORT_COL_SYMBOL = os.getenv("REPORT_COL_SYMBOL")     # symbol
REPORT_COL_SIDE   = os.getenv("REPORT_COL_SIDE")       # side
REPORT_COL_PRICE  = os.getenv("REPORT_COL_PRICE")      # price
REPORT_COL_SIZE   = os.getenv("REPORT_COL_SIZE")       # qty_signed
REPORT_COL_TIME   = os.getenv("REPORT_COL_TIME")       # ts
REPORT_COL_POS_QTY = os.getenv("REPORT_COL_POS_QTY")   # position_qty
REPORT_COL_PNL    = os.getenv("REPORT_COL_PNL")        # realized_profit

# Price source for unrealized PnL
REPORT_PRICE_TABLE      = os.getenv("REPORT_PRICE_TABLE", "public.report_prices")
REPORT_PRICE_COL        = os.getenv("REPORT_PRICE_COL")        # e.g. "price","last","mid"
REPORT_PRICE_TIME_COL   = os.getenv("REPORT_PRICE_TIME_COL")   # e.g. "ts","updated_at"
REPORT_PRICE_SYM_COL    = os.getenv("REPORT_PRICE_SYM_COL")    # e.g. "symbol","product_id","ticker"

# Win rate table (optional override)
REPORT_WINRATE_TABLE = os.getenv("REPORT_WINRATE_TABLE")  # default: REPORT_TRADES_TABLE

# Cash balances (optional)
REPORT_BALANCES_TABLE  = os.getenv("REPORT_BALANCES_TABLE", "public.report_balances")
REPORT_CASH_SYM_COL    = os.getenv("REPORT_CASH_SYM_COL")      # currency/asset/symbol
REPORT_CASH_AMT_COL    = os.getenv("REPORT_CASH_AMT_COL")      # balance/available/free
REPORT_CASH_SYMBOLS    = [s.strip().upper() for s in os.getenv("REPORT_CASH_SYMBOLS", "USD,USDC,USDT").split(",") if s.strip()]

# Window semantics
REPORT_USE_PT_DAY = os.getenv("REPORT_USE_PT_DAY", "0").strip() in {"1","true","TRUE","yes","Yes"}
REPORT_LOOKBACK_HOURS = int(os.getenv("REPORT_LOOKBACK_HOURS", "24"))

# Overview vs Details
REPORT_SHOW_DETAILS = os.getenv("REPORT_SHOW_DETAILS", "0").strip() in {"1","true","TRUE","yes","Yes"}

# Clients
ssm = boto3.client("ssm", region_name=REGION)
ses = boto3.client("ses", region_name=REGION)

# -------------------------
# DB Connection Helpers
# -------------------------

def get_param(name: str) -> str:
    return ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]

def _maybe_ssl_context(require_ssl: bool):
    if not require_ssl:
        return None
    ctx = ssl.create_default_context()
    bundle = os.getenv("RDS_CA_BUNDLE", "/etc/ssl/certs/ca-certificates.crt")
    if os.path.exists(bundle):
        try:
            ctx.load_verify_locations(cafile=bundle)
        except Exception:
            pass
    return ctx

def _env_or_ssm(env_key: str, ssm_param_name: Optional[str], default: Optional[str] = None):
    v = os.getenv(env_key)
    if v:
        return v
    if ssm_param_name:
        return get_param(ssm_param_name)
    if default is not None:
        return default
    raise RuntimeError(f"Missing {env_key} and no SSM fallback provided.")

def get_db_conn():
    try:
        url = os.getenv("DATABASE_URL")

        def _log_conn_plan(source, host, port, user, name, ssl):
            print(f"[DB] source={source} host={host} port={port} user={user} db={name} ssl={'on' if ssl else 'off'}")

        if url:
            u = urlparse(url)
            host = u.hostname or "db"
            port = int(u.port or 5432)
            user = u.username
            pwd  = u.password
            name = (u.path or "/").lstrip("/")
            qs = parse_qs(u.query or "")
            sslmode = (qs.get("sslmode", [""])[0] or "").lower()
            require_ssl = sslmode in {"require", "verify-ca", "verify-full"} or "+ssl" in (u.scheme or "")
            _log_conn_plan("DATABASE_URL", host, port, user, name, require_ssl)
            return pg.Connection(
                user=user, password=pwd, host=host, port=port, database=name,
                ssl_context=_maybe_ssl_context(require_ssl)
            )

        in_docker = IN_DOCKER
        default_host = "db" if in_docker else "localhost"
        default_user = "DB_USER" if in_docker else "Manny"
        host = _env_or_ssm("DB_HOST", None, default_host)
        port = int(_env_or_ssm("DB_PORT", None, "5432"))
        name = _env_or_ssm("DB_NAME", None, None)
        user = _env_or_ssm("DB_USER", None, default_user)
        pwd  = _env_or_ssm("DB_PASSWORD", None, None)
        db_ssl = (os.getenv("DB_SSL", "disable").lower() in {"require", "true", "1"})
        _log_conn_plan("ENV_VARS", host, port, user, name, db_ssl)
        return pg.Connection(
            user=user, password=pwd, host=host, port=port, database=name,
            ssl_context=_maybe_ssl_context(db_ssl)
        )
    except Exception as e:
        raise RuntimeError(f"DB connection failed: {e}")

def get_sa_engine():
    in_docker = IN_DOCKER
    url = os.getenv("DATABASE_URL")
    if url:
        u = urlparse(url)
        host = u.hostname or ("db" if in_docker else "127.0.0.1")
        port = u.port or 5432
        user = u.username or ""
        pwd  = u.password or ""
        name = (u.path or "/").lstrip("/")
        return create_engine(f"postgresql+pg8000://{user}:{pwd}@{host}:{port}/{name}")

    host = (os.getenv("DB_HOST") or ("db" if in_docker else "127.0.0.1")).strip()
    port = int((os.getenv("DB_PORT") or "5432").strip() or 5432)
    name = (os.getenv("DB_NAME") or "").strip()
    user = (os.getenv("DB_USER") or "").strip()
    pwd  = (os.getenv("DB_PASSWORD") or "").strip()

    return create_engine(f"postgresql+pg8000://{user}:{pwd}@{host}:{port}/{name}")

# -------------------------
# Identifier / info_schema
# -------------------------

def split_schema_table(qualified: str):
    q = qualified.strip().strip('"')
    if "." in q:
        s, t = q.split(".", 1)
        return (s.strip('"') or "public", t.strip('"'))
    return ("public", q)

def qident(name: str) -> str:
    return '"' + name.replace('"','""') + '"'

def qualify(qualified: str) -> str:
    sch, tbl = split_schema_table(qualified)
    return f"{qident(sch)}.{qident(tbl)}"

def _sql_str(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"

def table_columns(conn, qualified: str):
    sch, tbl = split_schema_table(qualified)
    sql = f"""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = {_sql_str(sch)} AND table_name = {_sql_str(tbl)}
    """
    rows = conn.run(sql)
    return {r[0] for r in rows}

def pick_first_available(cols_present, candidates):
    for c in candidates:
        if c and c in cols_present:
            return c
    return None

# -------------------------
# Time Window Helper
# -------------------------

def _time_window_sql(ts_expr: str):
    if REPORT_USE_PT_DAY:
        time_window_sql = (
            f"{ts_expr} >= (DATE_TRUNC('day', (NOW() AT TIME ZONE 'America/Anchorage')) "
            f"AT TIME ZONE 'America/Anchorage')"
        )
    else:
        time_window_sql = f"{ts_expr} >= (NOW() AT TIME ZONE 'UTC' - INTERVAL '{REPORT_LOOKBACK_HOURS} hours')"

    upper_bound_sql = f"AND {ts_expr} < (NOW() AT TIME ZONE 'UTC')"
    return time_window_sql, upper_bound_sql

# -------------------------
# Core Queries (schema-aware)
# -------------------------

# -------------------------
# Score JSONL Parsing & Summary
# -------------------------

# def _read_score_jsonl(path: str, since_hours: int = 24) -> list[dict]:
#     """Read JSONL score snapshots (robust to '📊 score_snapshot ...' prefix)."""
#     if not os.path.exists(path):
#         return []
#     rows = []
#     cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
#     with open(path, "r") as f:
#         for line in f:
#             s = line.strip()
#             if not s:
#                 continue
#             try:
#                 if s[0] != "{":
#                     # handle lines like: "📊 score_snapshot {...}"
#                     jstart = s.find("{")
#                     if jstart == -1:
#                         continue
#                     s = s[jstart:]
#                 obj = json.loads(s)
#             except Exception:
#                 continue
#             # filter by ts if present
#             ts = obj.get("ts")
#             try:
#                 if ts:
#                     # supports ISO with or without Z
#                     t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
#                     if t.tzinfo is None:
#                         t = t.replace(tzinfo=timezone.utc)
#                     if t < cutoff:
#                         continue
#             except Exception:
#                 pass
#             rows.append(obj)
#     return rows

# def summarize_score_jsonl(events: list[dict]) -> dict:
#     """Aggregate top contributing indicators and recent BUY/SELL entries."""
#     if not events:
#         return {"empty": True}
#
#     # Aggregate contributions from the 'top_*_components' arrays
#     contrib = {"buy": Counter(), "sell": Counter()}
#     symbols = set()
#     entries = []  # most recent BUY/SELL actions
#
#     for e in events:
#         symbol = e.get("symbol")
#         if symbol: symbols.add(symbol)
#
#         # collect recent entries (one per event)
#         action = e.get("action")
#         if action in ("buy", "sell"):
#             entries.append({
#                 "ts": e.get("ts"),
#                 "symbol": symbol,
#                 "action": action,
#                 "trigger": e.get("trigger"),
#                 "buy_score": e.get("buy_score"),
#                 "sell_score": e.get("sell_score"),
#             })
#
#         for side, key in (("buy", "top_buy_components"), ("sell", "top_sell_components")):
#             comps = e.get(key) or []
#             for c in comps:
#                 ind = c.get("indicator")
#                 contrib_val = float(c.get("contribution", 0.0) or 0.0)
#                 if ind:
#                     contrib[side][ind] += contrib_val
#
#     # top 10 by side
#     top_buy = contrib["buy"].most_common(10)
#     top_sell = contrib["sell"].most_common(10)
#
#     # keep last 20 entries by ts
#     def _dt(s):
#         try:
#             return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
#         except Exception:
#             return datetime.min.replace(tzinfo=timezone.utc)
#     entries = sorted(entries, key=lambda r: _dt(r["ts"]))[-20:]
#
#     return {
#         "empty": False,
#         "rows": len(events),
#         "symbols": len(symbols),
#         "top_buy": top_buy,
#         "top_sell": top_sell,
#         "entries": entries,
#     }

def render_score_section_html(metrics: dict) -> str:
    """Return a small HTML section matching your report’s table style."""
    if metrics.get("empty"):
        return "<h3>Signal Score Snapshot (last 24h)</h3><p>No score data found.</p>"

    def _table(headers: list[str], rows: list[list[str]]) -> str:
        head = "<tr>" + "".join(f"<th>{h}</th>" for h in headers) + "</tr>"
        body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows) or \
               "<tr><td colspan='99'>None</td></tr>"
        return f"<table border='1' cellpadding='6' cellspacing='0'>{head}{body}</table>"

    # Top contributors
    buy_rows = [[ind, f"{val:.3f}"] for ind, val in (metrics.get("top_buy") or [])]
    sell_rows = [[ind, f"{val:.3f}"] for ind, val in (metrics.get("top_sell") or [])]

    # Recent entries
    ent = metrics.get("entries") or []
    entry_rows = [[
        e.get("ts",""),
        e.get("symbol",""),
        e.get("action",""),
        e.get("trigger",""),
        f"{(e.get('buy_score') or 0):.3f}",
        f"{(e.get('sell_score') or 0):.3f}",
    ] for e in ent]

    html = []
    html.append("<h3>Signal Score Snapshot (last 24h)</h3>")
    html.append(f"<p>Symbols: <b>{metrics.get('symbols',0)}</b> &nbsp; Rows: <b>{metrics.get('rows',0)}</b></p>")

    html.append("<h4>Top contributing indicators (Buy)</h4>")
    html.append(_table(["Indicator","Contribution"], buy_rows))

    html.append("<h4>Top contributing indicators (Sell)</h4>")
    html.append(_table(["Indicator","Contribution"], sell_rows))

    if entry_rows:
        html.append("<h4>Most recent entries</h4>")
        html.append(_table(["Time (UTC)","Symbol","Action","Trigger","Buy Score","Sell Score"], entry_rows))

    return "\n".join(html)





def run_queries(conn):
    errors = []
    detect_notes = ["Build:v6"]

    # Realized PnL (windowed if timestamp exists)
    try:
        tbl_pnl = REPORT_PNL_TABLE
        cols_present = table_columns(conn, tbl_pnl)
        if DEBUG:
            detect_notes.append(f"Columns({tbl_pnl}): {sorted(cols_present)}")

        pnl_candidates = ["pnl_usd", "realized_pnl_usd", "realized_pnl", "pnl", "profit", "realized_profit"]
        pnl_col = REPORT_COL_PNL if REPORT_COL_PNL else pick_first_available(cols_present, pnl_candidates)

        if not pnl_col:
            total_pnl = 0
            detect_notes.append(f"No pnl-like column found on {tbl_pnl}")
        else:
            ts_candidates = ["order_time", "exec_time", "time", "ts", "timestamp", "created_at", "updated_at"]
            ts_col = pick_first_available(cols_present, ts_candidates)

            if ts_col:
                low_sql, high_sql = _time_window_sql(qident(ts_col))
                q = f"""
                            SELECT COALESCE(SUM({qident(pnl_col)}), 0)
                            FROM {qualify(tbl_pnl)}
                            WHERE {low_sql} {high_sql}
                        """
                detect_notes.append(f"PnL windowed: table=({tbl_pnl}) col={pnl_col} ts={ts_col}")
            else:
                q = f"SELECT COALESCE(SUM({qident(pnl_col)}), 0) FROM {qualify(tbl_pnl)}"
                detect_notes.append(f"PnL ALL-TIME (no ts col): table=({tbl_pnl}) col={pnl_col}")

            total_pnl = conn.run(q)[0][0]
    except Exception as e:
        total_pnl = 0
        errors.append(f"PnL query failed: {e}")

    # Positions
    open_pos = []
    try:
        tbl_pos = REPORT_POSITIONS_TABLE
        cols_pos = table_columns(conn, tbl_pos)
        if DEBUG:
            detect_notes.append(f"Columns({tbl_pos}): {sorted(cols_pos)}")
        if not cols_pos:
            raise RuntimeError(f"Table not found: {tbl_pos}")

        qty_col = REPORT_COL_POS_QTY if (REPORT_COL_POS_QTY and REPORT_COL_POS_QTY in cols_pos) else \
                  pick_first_available(cols_pos, ["position_qty","pos_qty","qty","size","amount"])
        if not qty_col:
            raise RuntimeError(f"No qty-like column found on {tbl_pos}")

        price_col = pick_first_available(cols_pos, ["avg_entry_price","avg_price","price"])
        price_sql = f"{qident(price_col)} AS avg_price" if price_col else "NULL::numeric AS avg_price"

        q = f"""
            SELECT symbol,
                   {qident(qty_col)} AS qty,
                   {price_sql}
            FROM {qualify(tbl_pos)}
            WHERE COALESCE({qident(qty_col)}, 0) <> 0
            ORDER BY symbol
        """
        open_pos = conn.run(q)
        detect_notes.append(f"Positions source: {tbl_pos} qty_col={qty_col} price_col={price_col or 'NULL'}")
    except Exception as e1:
        errors.append(f"Positions query failed: {e1}")

    # Trades (window)
    def run_trades_for(table_name: str, use_mappings: bool = True):
        cols_tr = table_columns(conn, table_name)
        if DEBUG:
            detect_notes.append(f"Columns({table_name}): {sorted(cols_tr)}")
        if not cols_tr:
            raise RuntimeError(f"Table not found: {table_name}")

        sym_col = REPORT_COL_SYMBOL if (use_mappings and REPORT_COL_SYMBOL in cols_tr) else \
                  pick_first_available(cols_tr, ["symbol","product_id"])
        if not sym_col:
            raise RuntimeError(f"No symbol-like column on {table_name}")

        if use_mappings and REPORT_COL_SIDE in cols_tr if REPORT_COL_SIDE else False:
            side_expr = qident(REPORT_COL_SIDE)
        elif "side" in cols_tr:
            side_expr = "side"
        else:
            amt_for_side = pick_first_available(cols_tr, ["qty_signed","amount","size","executed_size","filled_size","base_amount","remaining_size"])
            if amt_for_side:
                side_expr = (
                    f"CASE WHEN {qident(amt_for_side)} < 0 THEN 'sell' "
                    f"WHEN {qident(amt_for_side)} > 0 THEN 'buy' END"
                )
            else:
                side_expr = "'?'::text"

        pr_col = REPORT_COL_PRICE if (use_mappings and REPORT_COL_PRICE in cols_tr) else \
                 pick_first_available(cols_tr, ["price","fill_price","executed_price","avg_price","avg_fill_price","limit_price"])
        price_expr = qident(pr_col) if pr_col else "NULL::numeric"

        amt_col = REPORT_COL_SIZE if (use_mappings and REPORT_COL_SIZE in cols_tr) else \
                  pick_first_available(cols_tr, ["qty_signed","amount","size","executed_size","filled_size","base_amount","remaining_size"])
        amt_expr = qident(amt_col) if amt_col else "NULL::numeric"

        ts_col = REPORT_COL_TIME if (use_mappings and REPORT_COL_TIME in cols_tr) else \
                 pick_first_available(cols_tr, ["trade_time","filled_at","completed_at","order_time","ts","created_at","executed_at"])
        if not ts_col:
            raise RuntimeError(f"No time-like column on {table_name}")
        ts_expr = qident(ts_col)

        time_window_sql, upper_bound_sql = _time_window_sql(ts_expr)

        status_filter = ""
        if "status" in cols_tr:
            status_filter = "AND COALESCE(status,'filled') IN ('filled','done')"

        q = f"""
            SELECT {qident(sym_col)} AS symbol,
                   {side_expr} AS side,
                   {price_expr} AS price,
                   {amt_expr} AS amount,
                   {ts_expr} AS ts
            FROM {qualify(table_name)}
            WHERE {time_window_sql}
              {upper_bound_sql}
              {status_filter}
            ORDER BY {ts_expr} DESC
            LIMIT 1000
        """
        return conn.run(q)

    recent_trades = []
    try:
        recent_trades = run_trades_for(REPORT_TRADES_TABLE, use_mappings=True)
        detect_notes.append(f"Trades source: {REPORT_TRADES_TABLE} (mapped where possible)")
    except Exception as e:
        errors.append(f"Trades query failed on {REPORT_TRADES_TABLE}: {e}")
        recent_trades = []

    if len(recent_trades) < 5 and REPORT_TRADES_TABLE != "public.trade_records":
        try:
            alt = run_trades_for("public.trade_records", use_mappings=False)
            if len(alt) > len(recent_trades):
                recent_trades = alt
                detect_notes.append("Trades fallback: public.trade_records (auto-detected columns)")
        except Exception as e:
            errors.append(f"Trades fallback query failed: {e}")

    return total_pnl, open_pos, recent_trades, errors, detect_notes

# -------------------------
# Exposure Snapshot
# -------------------------

def compute_exposures(open_pos, top_n: int = 3):
    items = []
    total = 0.0
    for row in open_pos or []:
        try:
            sym, qty, px = row[0], float(row[1] or 0), float(row[2] or 0)
        except Exception:
            continue
        notional = abs(qty) * px
        if notional <= 0:
            continue
        total += notional
        items.append({
            "symbol": sym,
            "notional": notional,
            "qty": qty,
            "price": px,
            "side": "short" if qty < 0 else "long",
            "pct": 0.0,
        })
    items.sort(key=lambda x: x["notional"], reverse=True)
    for it in items:
        it["pct"] = (it["notional"] / total * 100.0) if total > 0 else 0.0
    return {"total_notional": total, "items": items[:top_n], "all_items": items}

# -------------------------
# Prices & Metrics
# -------------------------

def _latest_price_map(conn, price_table: str):
    cols = table_columns(conn, price_table)
    if not cols:
        return {}

    if REPORT_PRICE_SYM_COL and REPORT_PRICE_SYM_COL in cols:
        symcol = REPORT_PRICE_SYM_COL
    else:
        symcol = pick_first_available(cols, ["symbol","product_id","ticker"])
    if not symcol:
        return {}

    pcol = REPORT_PRICE_COL if (REPORT_PRICE_COL and REPORT_PRICE_COL in cols) else \
           pick_first_available(cols, ["price","last","mid","close","mark"])
    tcol = REPORT_PRICE_TIME_COL if (REPORT_PRICE_TIME_COL and REPORT_PRICE_TIME_COL in cols) else \
           pick_first_available(cols, ["ts","timestamp","updated_at","created_at","time"])

    if not pcol:
        return {}

    if tcol:
        q = f"""
            SELECT {qident(symcol)} AS symbol,
                   {qident(pcol)}  AS price
            FROM {qualify(price_table)}
            WHERE ({qident(symcol)}, {qident(tcol)}) IN (
                SELECT {qident(symcol)}, MAX({qident(tcol)})
                FROM {qualify(price_table)}
                GROUP BY {qident(symcol)}
            )
        """
    else:
        q = f"""
            SELECT {qident(symcol)} AS symbol,
                   {qident(pcol)}  AS price
            FROM {qualify(price_table)}
        """
    out = {}
    for s, p in conn.run(q):
        try:
            out[s] = float(p)
        except Exception:
            pass
    return out

def compute_unrealized_pnl(conn, open_pos):
    notes = []
    price_map = _latest_price_map(conn, REPORT_PRICE_TABLE)
    if not price_map:
        notes.append(f"No prices available from {REPORT_PRICE_TABLE}")
        return 0.0, notes

    unreal = 0.0
    for row in open_pos or []:
        try:
            sym, qty, avg_px = row[0], float(row[1] or 0), float(row[2] or 0)
        except Exception:
            continue
        if qty == 0 or avg_px <= 0:
            continue
        px = price_map.get(sym)
        if px is None:
            notes.append(f"Missing price for {sym} in {REPORT_PRICE_TABLE}")
            continue
        unreal += qty * (px - avg_px)
    return unreal, notes

def compute_win_rate(conn):
    notes = []
    tbl = REPORT_WINRATE_TABLE or REPORT_TRADES_TABLE
    cols = table_columns(conn, tbl)
    if not cols:
        return 0.0, 0, 0, [f"WinRate: table not found: {tbl}"]

    pnl_col = pick_first_available(cols, ["realized_profit","realized_pnl","pnl","profit"])
    if not pnl_col:
        return 0.0, 0, 0, [f"WinRate: no pnl-like column found on {tbl}"]

    ts_col = pick_first_available(cols, ["trade_time","filled_at","completed_at","order_time","ts","created_at","executed_at"])
    if not ts_col:
        return 0.0, 0, 0, [f"WinRate: no time-like column on {tbl}"]

    ts_expr = qident(ts_col)
    time_window_sql, upper_bound_sql = _time_window_sql(ts_expr)

    q = f"""
        SELECT
            COUNT(*) FILTER (WHERE {qident(pnl_col)} > 0) AS wins,
            COUNT(*) FILTER (WHERE {qident(pnl_col)} < 0) AS losses,
            COUNT(*) AS total
        FROM {qualify(tbl)}
        WHERE {time_window_sql}
          {upper_bound_sql}
          AND {qident(pnl_col)} IS NOT NULL
    """
    wins, losses, total = conn.run(q)[0]
    total = int(total or 0)
    wins = int(wins or 0)
    win_rate = (wins / total * 100.0) if total > 0 else 0.0

    notes.append(f"WinRate source: {tbl} pnl_col={pnl_col} ts_col={ts_col} (denominator includes breakeven)")
    return win_rate, wins, total, notes

def compute_trade_stats_windowed(conn):
    notes = []
    tbl = REPORT_WINRATE_TABLE or REPORT_TRADES_TABLE
    cols = table_columns(conn, tbl)
    if not cols:
        return 0.0, 0.0, None, [f"TradeStats: table not found: {tbl}"]

    pnl_col = pick_first_available(cols, ["realized_profit","realized_pnl","pnl","profit"])
    if not pnl_col:
        return 0.0, 0.0, None, [f"TradeStats: no pnl-like column found on {tbl}"]

    ts_col = pick_first_available(cols, ["trade_time","filled_at","completed_at","order_time","ts","created_at","executed_at"])
    if not ts_col:
        return 0.0, 0.0, None, [f"TradeStats: no time-like column on {tbl}"]

    ts_expr = qident(ts_col)
    time_window_sql, upper_bound_sql = _time_window_sql(ts_expr)

    # NOTE: No ROUND() with double+int; compute raw aggregates and format at the edge
    q = f"""
        SELECT
          AVG(CASE WHEN {qident(pnl_col)} > 0 THEN {qident(pnl_col)} END) AS avg_win,
          AVG(CASE WHEN {qident(pnl_col)} < 0 THEN {qident(pnl_col)} END) AS avg_loss_neg,
          SUM(CASE WHEN {qident(pnl_col)} > 0 THEN {qident(pnl_col)} ELSE 0 END) AS gross_profit,
          SUM(CASE WHEN {qident(pnl_col)} < 0 THEN -{qident(pnl_col)} ELSE 0 END) AS gross_loss_abs
        FROM {qualify(tbl)}
        WHERE {time_window_sql}
          {upper_bound_sql}
          AND {qident(pnl_col)} IS NOT NULL
    """
    avg_win, avg_loss_neg, gross_profit, gross_loss_abs = conn.run(q)[0]
    avg_win = float(avg_win or 0.0)
    avg_loss_neg = float(avg_loss_neg or 0.0)
    pf = None
    try:
        pf = (float(gross_profit or 0.0) / float(gross_loss_abs or 0.0)) if float(gross_loss_abs or 0.0) > 0 else None
    except Exception:
        pf = None

    notes.append(f"TradeStats source: {tbl} pnl_col={pnl_col} ts_col={ts_col}")
    return avg_win, avg_loss_neg, pf, notes

def compute_max_drawdown(conn):
    notes = []
    tbl = REPORT_PNL_TABLE
    cols = table_columns(conn, tbl)
    if not cols:
        return 0.0, 0.0, 0.0, 0.0, [f"Drawdown: table not found: {tbl}"]

    pnl_col = REPORT_COL_PNL if (REPORT_COL_PNL and REPORT_COL_PNL in cols) else \
              pick_first_available(cols, ["realized_profit","realized_pnl","pnl","profit"])
    if not pnl_col:
        return 0.0, 0.0, 0.0, 0.0, [f"Drawdown: no pnl-like column on {tbl}"]

    ts_col = pick_first_available(cols, ["trade_time","filled_at","completed_at","order_time","ts","created_at","executed_at"])
    if not ts_col:
        return 0.0, 0.0, 0.0, 0.0, [f"Drawdown: no time-like column on {tbl}"]

    q = f"""
        WITH t AS (
          SELECT {qident(ts_col)} AS ts, COALESCE({qident(pnl_col)},0) AS pnl
          FROM {qualify(tbl)}
          WHERE {qident(pnl_col)} IS NOT NULL
        ),
        c AS (
          SELECT ts,
                 SUM(pnl) OVER (ORDER BY ts ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS equity
          FROM t
        ),
        d AS (
          SELECT ts, equity,
                 MAX(equity) OVER (ORDER BY ts ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS run_max
          FROM c
        )
        SELECT
          MIN((equity - run_max) / NULLIF(run_max,0.0)) AS min_frac,
          MIN(equity - run_max) AS min_abs,
          MAX(run_max) AS peak_eq,
          MIN(equity) AS trough_eq
        FROM d
    """
    min_frac, min_abs, peak_eq, trough_eq = conn.run(q)[0]
    if min_frac is None or peak_eq in (None, 0):
        dd_pct = 0.0
    else:
        dd_pct = abs(float(min_frac) * 100.0)
    dd_abs = abs(float(min_abs or 0.0))
    notes.append(f"Drawdown source: {tbl} pnl_col={pnl_col} ts_col={ts_col}")
    return dd_pct, dd_abs, float(peak_eq or 0.0), float(trough_eq or 0.0), notes

def compute_cash_vs_invested(conn, exposures):
    invested = float(exposures.get("total_notional", 0.0) if exposures else 0.0)
    notes = []
    tbl = REPORT_BALANCES_TABLE
    cols = table_columns(conn, tbl)
    if not cols:
        notes.append(f"Cash: table not found: {tbl}")
        return 0.0, invested, 0.0, notes

    sym_col = REPORT_CASH_SYM_COL if (REPORT_CASH_SYM_COL and REPORT_CASH_SYM_COL in cols) else \
              pick_first_available(cols, ["currency","asset","symbol","coin"])
    amt_col = REPORT_CASH_AMT_COL if (REPORT_CASH_AMT_COL and REPORT_CASH_AMT_COL in cols) else \
              pick_first_available(cols, ["available","balance","free","amount","qty","quantity"])

    if not sym_col or not amt_col:
        notes.append(f"Cash: missing sym/amt columns on {tbl}")
        return 0.0, invested, 0.0, notes

    syms_list = ",".join(f"{_sql_str(s)}" for s in REPORT_CASH_SYMBOLS)
    q = f"""
        SELECT SUM({qident(amt_col)})
        FROM {qualify(tbl)}
        WHERE UPPER({qident(sym_col)}) IN ({syms_list})
    """
    row = conn.run(q)[0][0] if cols else 0
    cash = float(row or 0.0)
    total_cap = cash + invested
    invested_pct = (invested / total_cap * 100.0) if total_cap > 0 else 0.0
    notes.append(f"Cash source: {tbl} sym_col={sym_col} amt_col={amt_col} symbols={REPORT_CASH_SYMBOLS}")
    return cash, invested, invested_pct, notes

# -------------------------
# Fast roundtrips (≤60s)
# -------------------------

def build_fast_rt_sql(table_name: str):
    return text(f"""
WITH execs AS (
  SELECT
      e.order_id                                     AS id,
      e.symbol                                       AS symbol,
      e.side                                         AS side,
      e.size::numeric(38,18)                         AS qty,
      e.price::numeric(38,18)                        AS price,
      COALESCE(e.total_fees_usd, 0)::numeric(38,18)  AS fee,
      e.order_time::timestamptz                      AS exec_time,
      e.parent_id                                    AS link_key,
      e.status                                       AS status
  FROM {table_name} e
  WHERE e.status = 'filled'
),
paired AS (
  SELECT
      a.id               AS entry_id,
      a.symbol,
      a.link_key,
      a.side             AS entry_side,
      a.qty              AS entry_qty,
      a.price            AS entry_price,
      a.fee              AS entry_fee,
      a.exec_time        AS entry_time,
      b.id               AS exit_id,
      b.side             AS exit_side,
      b.qty              AS exit_qty,
      b.price            AS exit_price,
      b.fee              AS exit_fee,
      b.exec_time        AS exit_time,
      EXTRACT(EPOCH FROM (b.exec_time - a.exec_time))::int AS hold_seconds
  FROM execs a
  JOIN LATERAL (
      SELECT *
      FROM execs b
      WHERE b.symbol = a.symbol
        AND (
             (a.link_key IS NOT NULL AND b.link_key = a.link_key)
             OR
             (a.link_key IS NULL AND b.link_key IS NULL)
        )
        AND b.exec_time > a.exec_time
        AND b.side <> a.side
      ORDER BY b.exec_time
      LIMIT 1
  ) b ON TRUE
)
SELECT
    entry_id, exit_id, symbol,
    entry_side, entry_qty, entry_price, entry_fee, entry_time,
    exit_side,  exit_qty,  exit_price,  exit_fee,  exit_time,
    hold_seconds,
    LEAST(entry_qty, exit_qty) * entry_price                                              AS notional_entry,
    LEAST(entry_qty, exit_qty) * (exit_price - entry_price) * CASE WHEN entry_side='buy' THEN 1 ELSE -1 END AS pnl_abs,
    100.0 * (exit_price / NULLIF(entry_price,0) - 1.0) * CASE WHEN entry_side='buy' THEN 1 ELSE -1 END     AS pnl_pct
FROM paired
WHERE hold_seconds BETWEEN 0 AND 60
ORDER BY entry_time DESC
LIMIT 200;
""")

def fetch_fast_roundtrips(engine, csv_path="/app/logs/fast_roundtrips.csv"):
    try:
        with engine.begin() as conn:
            df = pd.read_sql(build_fast_rt_sql(os.getenv("REPORT_EXECUTIONS_TABLE", REPORT_EXECUTIONS_TABLE)), conn)
    except Exception as e:
        msg = f"{type(e).__name__}: {str(e)}"
        if os.getenv("REPORT_DEBUG") == "1":
            print(f"[fast_roundtrips] error: {msg}")
        html = f"""
        <h3>Near-Instant Roundtrips (≤60s)</h3>
        <p style="color:#b00;">Could not compute fast roundtrips. Ensure REPORT_EXECUTIONS_TABLE points to your fills table and column names match:
        <code>order_time, price, size, total_fees_usd, side, status, parent_id, symbol, order_id</code>.<br/>
        <small>{msg}</small></p>
        """
        return html.strip(), None, None

    if df.empty:
        html = """
        <h3>Near-Instant Roundtrips (≤60s)</h3>
        <p>No roundtrips ≤60 seconds in this window.</p>
        """
        return html.strip(), None, df

    df["entry_time"] = pd.to_datetime(df["entry_time"]).dt.tz_convert("UTC")
    df["exit_time"]  = pd.to_datetime(df["exit_time"]).dt.tz_convert("UTC")

    n = len(df)
    median_hold = int(df["hold_seconds"].median())
    total_pnl = float(df["pnl_abs"].sum())

    view_cols = ["symbol", "entry_time", "exit_time", "hold_seconds", "entry_price", "exit_price", "pnl_abs", "pnl_pct"]
    show = df[view_cols].copy()
    show["entry_time"] = show["entry_time"].dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    show["exit_time"]  = show["exit_time"].dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    show["pnl_abs"]    = show["pnl_abs"].map(lambda x: f"${x:,.2f}")
    show["pnl_pct"]    = show["pnl_pct"].map(lambda x: f"{x:,.2f}%")
    show["entry_price"]= show["entry_price"].map(lambda x: f"{x:.8f}".rstrip('0').rstrip('.'))
    show["exit_price"] = show["exit_price"].map(lambda x: f"{x:.8f}".rstrip('0').rstrip('.'))

    html_table = show.head(8).to_html(index=False, border=1, justify="center")

    html = f"""
    <h3>Near-Instant Roundtrips (≤60s)</h3>
    <p>Count: <b>{n}</b> &nbsp; Median hold: <b>{median_hold}s</b> &nbsp; Total PnL: <b>${total_pnl:,.2f}</b></p>
    {html_table}
    <p style="color:#666;margin-top:6px">Full list (up to 200) saved as CSV on the server.</p>
    """

    csv_out = None
    try:
        df.to_csv(csv_path, index=False)
        csv_out = csv_path
    except Exception:
        pass

    return html.strip(), csv_out, df

# -------------------------
# Email / CSV Builders
# -------------------------

def build_html(total_pnl,
               open_pos,
               recent_trades,
               errors,
               detect_notes,
               exposures=None,
               unrealized_pnl: float = 0.0,
               win_rate: float = 0.0,
               wins: int = 0,
               total_trades: int = 0,
               avg_win: float = 0.0,
               avg_loss: float = 0.0,
               profit_factor: Optional[float] = None,
               max_dd_pct: float = 0.0,
               cash_usd: float = 0.0,
               invested_usd: float = 0.0,
               invested_pct: float = 0.0,
               strat_rows: Optional[list] = None,
               show_details: bool = False,
               *,
               score_section_html: str = ""
               ):
    def rows(rows_):
        if not rows_:
            return "<tr><td colspan='99'>None</td></tr>"
        out = []
        for r in rows_:
            rr = []
            for c in r:
                rr.append(c.isoformat() if hasattr(c, "isoformat") else c)
            out.append("<tr>" + "".join(f"<td>{c}</td>" for c in rr) + "</tr>")
        return "".join(out)

    now_utc = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    key_metrics_html = f"""
    <h3>Key Metrics</h3>
    <table border="1" cellpadding="6" cellspacing="0">
      <tr><th>Realized PnL (USD)</th><th>Unrealized PnL (USD)</th><th>Win Rate</th></tr>
      <tr>
        <td>{round(float(total_pnl or 0), 2):,.2f}</td>
        <td>{round(float(unrealized_pnl or 0), 2):,.2f}</td>
        <td>{win_rate:.1f}% ({wins}/{total_trades})</td>
      </tr>
    </table>
    """

    pf_txt = "—" if (profit_factor is None) else f"{profit_factor:.2f}"
    avg_loss_txt = f"-{abs(avg_loss):,.2f}" if avg_loss else "0.00"
    win_loss_ratio = ("—" if not avg_loss else f"{(avg_win / abs(avg_loss)):.2f}") if avg_win else "—"
    trade_stats_html = f"""
    <h4>Trade Stats (Window)</h4>
    <table border="1" cellpadding="6" cellspacing="0">
      <tr><th>Avg Win</th><th>Avg Loss</th><th>Profit Factor</th><th>Avg W / Avg L</th></tr>
      <tr>
        <td>${avg_win:,.2f}</td>
        <td>${avg_loss_txt}</td>
        <td>{pf_txt}</td>
        <td>{win_loss_ratio}</td>
      </tr>
    </table>
    """

    risk_cap_html = f"""
    <h3>Risk &amp; Capital</h3>
    <table border="1" cellpadding="6" cellspacing="0">
      <tr><th>Max Drawdown (since inception)</th><th>Cash (USD)</th><th>Invested Notional (USD)</th><th>Invested %</th></tr>
      <tr>
        <td>{max_dd_pct:.1f}%</td>
        <td>${cash_usd:,.2f}</td>
        <td>${invested_usd:,.2f}</td>
        <td>{invested_pct:.1f}%</td>
      </tr>
    </table>
    """

    exposure_html = ""
    if exposures and exposures.get("total_notional", 0) > 0:
        total_notional = exposures["total_notional"]
        lines = []
        for it in exposures["items"]:
            lines.append(
                f"<tr><td>{it['symbol']}</td>"
                f"<td>${it['notional']:,.2f}</td>"
                f"<td>{it['pct']:.1f}%</td>"
                f"<td>{it['side']}</td>"
                f"<td>{it['qty']}</td>"
                f"<td>{it['price']}</td></tr>"
            )
        warn = ""
        if exposures["items"] and exposures["items"][0]["pct"] >= 25.0:
            warn = "<p style='color:#b00'><b>Note:</b> Largest single exposure ≥ 25% of total.</p>"
        exposure_html = f"""
        <h3>Exposure Snapshot</h3>
        <p><b>Total Notional:</b> ${total_notional:,.2f}</p>
        <table border="1" cellpadding="6" cellspacing="0">
          <tr><th>Symbol</th><th>Notional</th><th>% of Total</th><th>Side</th><th>Qty</th><th>Avg Price</th></tr>
          {''.join(lines)}
        </table>
        {warn}
        """

    strat_html = ""
    if strat_rows:
        body = []
        for r in strat_rows:
            body.append(
                f"<tr><td>{r['strategy']}</td><td>{r['total']}</td><td>{r['wins']}</td>"
                f"<td>{r['win_rate']:.1f}%</td><td>{r['pnl']:,.2f}</td></tr>"
            )
        strat_html = f"""
        <h3>Strategy Breakdown (Window)</h3>
        <table border="1" cellpadding="6" cellspacing="0">
          <tr><th>Strategy</th><th>Trades</th><th>Wins</th><th>Win Rate</th><th>Realized PnL (USD)</th></tr>
          {''.join(body)}
        </table>
        """

    notes_html = ""
    if errors or detect_notes:
        items = "".join(f"<li>{e}</li>" for e in errors + detect_notes)
        notes_html = f"<h3>Notes</h3><ul>{items}</ul>"

    details_html = ""
    if show_details:
        details_html = f"""
        <h3>Open Positions</h3>
        <table border="1" cellpadding="6" cellspacing="0">
          <tr><th>Symbol</th><th>Qty</th><th>Avg Price</th></tr>{rows(open_pos)}
        </table>
        <h3>Trades (Window)</h3>
        <table border="1" cellpadding="6" cellspacing="0">
          <tr><th>Symbol</th><th>Side</th><th>Price</th><th>Amount</th><th>Time</th></tr>{rows(recent_trades)}
        </table>
        """

    return f"""<html><body style="font-family:Arial,Helvetica,sans-serif">
    <h2>Daily Trading Bot Report</h2><p><b>As of:</b> {now_utc}</p>
    {key_metrics_html}
    {trade_stats_html}
    {risk_cap_html}
    {score_section_html}
    {exposure_html}
    {strat_html}
    {details_html}
    {notes_html}
    <p style="color:#666">CSV attachment includes these tables.</p>
    </body></html>"""

def build_csv(total_pnl,
              open_pos,
              recent_trades,
              exposures=None,
              unrealized_pnl: float = 0.0,
              win_rate: float = 0.0,
              wins: int = 0,
              total_trades: int = 0,
              avg_win: float = 0.0,
              avg_loss: float = 0.0,
              profit_factor: Optional[float] = None,
              max_dd_pct: float = 0.0,
              cash_usd: float = 0.0,
              invested_usd: float = 0.0,
              invested_pct: float = 0.0,
              strat_rows: Optional[list] = None):
    buf = io.StringIO(); w = csv.writer(buf)
    w.writerow(["Daily Trading Bot Report"])
    w.writerow(["Generated (UTC)", datetime.utcnow().isoformat()])
    w.writerow([])

    w.writerow(["Key Metrics"])
    w.writerow(["Realized PnL (USD)", round(float(total_pnl or 0), 2)])
    w.writerow(["Unrealized PnL (USD)", round(float(unrealized_pnl or 0), 2)])
    w.writerow(["Win Rate", f"{win_rate:.1f}% ({wins}/{total_trades})"])
    w.writerow([])

    w.writerow(["Trade Stats (Window)"])
    w.writerow(["Avg Win", round(float(avg_win or 0), 2)])
    w.writerow(["Avg Loss", round(float(avg_loss or 0), 2)])
    w.writerow(["Profit Factor", "" if profit_factor is None else round(float(profit_factor), 2)])
    w.writerow(["Avg W / Avg L", "" if not avg_loss else round(float((avg_win / abs(avg_loss))), 2)])
    w.writerow([])

    w.writerow(["Risk & Capital"])
    w.writerow(["Max Drawdown (since inception) %", round(float(max_dd_pct or 0.0), 2)])
    w.writerow(["Cash (USD)", round(float(cash_usd or 0.0), 2)])
    w.writerow(["Invested Notional (USD)", round(float(invested_usd or 0.0), 2)])
    w.writerow(["Invested %", round(float(invested_pct or 0.0), 2)])
    w.writerow([])

    if exposures and exposures.get("total_notional", 0) > 0:
        w.writerow(["Exposure Snapshot"])
        w.writerow(["Total Notional (USD)", f"{exposures['total_notional']:.2f}"])
        w.writerow(["Symbol","Notional","% of Total","Side","Qty","Avg Price"])
        for it in exposures["items"]:
            w.writerow([it["symbol"], f"{it['notional']:.2f}", f"{it['pct']:.1f}%", it["side"], it["qty"], it["price"]])
        w.writerow([])

    if strat_rows:
        w.writerow(["Strategy Breakdown (Window)"])
        w.writerow(["Strategy","Trades","Wins","Win Rate %","Realized PnL (USD)"])
        for r in strat_rows:
            w.writerow([r["strategy"], r["total"], r["wins"], round(r["win_rate"], 2), round(r["pnl"], 2)])
        w.writerow([])

    w.writerow(["Open Positions"]); w.writerow(["Symbol","Qty","Avg Price"])
    for r in open_pos:
        r = [c.isoformat() if hasattr(c, "isoformat") else c for c in r]
        w.writerow(r)
    w.writerow([])

    w.writerow(["Trades (Window)"]); w.writerow(["Symbol","Side","Price","Amount","Time"])
    for r in recent_trades:
        r = [c.isoformat() if hasattr(c, "isoformat") else c for c in r]
        w.writerow(r)

    return buf.getvalue().encode("utf-8")


# -------------------------
# Strategy breakdown (optional)
# -------------------------

def compute_strategy_breakdown_windowed(conn):
    tbl = REPORT_TRADES_TABLE
    cols = table_columns(conn, tbl)
    out = []
    notes = []
    if not cols:
        return out, ["Strategy: table not found"]

    strat_col = pick_first_available(cols, ["strategy","module","algo","tag"])
    pnl_col = pick_first_available(cols, ["realized_profit","realized_pnl","pnl","profit"])
    ts_col = pick_first_available(cols, ["trade_time","filled_at","completed_at","order_time","ts","created_at","executed_at"])
    if not strat_col or not pnl_col or not ts_col:
        miss = []
        if not strat_col: miss.append("strategy-like column")
        if not pnl_col: miss.append("pnl-like column")
        if not ts_col: miss.append("time-like column")
        notes.append("Strategy: missing " + ", ".join(miss))
        return out, notes

    ts_expr = qident(ts_col)
    time_window_sql, upper_bound_sql = _time_window_sql(ts_expr)
    q = f"""
        SELECT {qident(strat_col)} AS strat,
               COUNT(*) AS total,
               COUNT(*) FILTER (WHERE {qident(pnl_col)} > 0) AS wins,
               COALESCE(SUM({qident(pnl_col)}),0) AS pnl
        FROM {qualify(tbl)}
        WHERE {time_window_sql}
          {upper_bound_sql}
          AND {qident(pnl_col)} IS NOT NULL
        GROUP BY {qident(strat_col)}
        ORDER BY total DESC
        LIMIT 20
    """
    for strat, total, wins, pnl in conn.run(q):
        total = int(total or 0)
        wins = int(wins or 0)
        wr = (wins / total * 100.0) if total > 0 else 0.0
        out.append({"strategy": strat, "total": total, "wins": wins, "win_rate": wr, "pnl": float(pnl or 0.0)})
    notes.append(f"Strategy source: {tbl} strat_col={strat_col} pnl_col={pnl_col} ts_col={ts_col}")
    return out, notes


# -------------------------
# IO helpers
# -------------------------
def derive_extra_metrics(win_rate_pct: float | None,
                         avg_win: float | None,
                         avg_loss: float | None,
                         recent_trades: list[dict] | None):
    """
    Returns a dict containing:
      - expectancy_per_trade
      - mean_pnl_per_trade
      - stdev_pnl_per_trade
      - sharpe_like  (mean/stdev using population stdev; None if stdev==0 or no data)
    Tries to pull signed PnL from recent_trades using common field names.
    Falls back to expectancy formula if no per-trade PnL list is available.
    """
    # 1) Expectancy by definition if we have win rate and avg win/loss
    expectancy = None
    if win_rate_pct is not None and avg_win is not None and avg_loss is not None:
        p_win = float(win_rate_pct) / 100.0
        p_loss = 1.0 - p_win
        expectancy = p_win * float(avg_win) + p_loss * float(avg_loss)

    # 2) Try to build a signed PnL list from recent_trades
    pnl_keys = ("pnl", "pnl_usd", "pnl_signed", "pnl_abs")  # preference order
    pnl_list: list[float] = []
    if recent_trades:
        for r in recent_trades:
            val = None
            for k in pnl_keys:
                if k in r and r[k] is not None:
                    val = float(r[k])
                    break
            # If only 'pnl_abs' is present, we can’t infer sign reliably; skip it.
            if val is not None and ("pnl_abs" not in r or "pnl" in r or "pnl_usd" in r or "pnl_signed" in r):
                pnl_list.append(val)

    mean_pnl = None
    sd_pnl = None
    sharpe_like = None

    if pnl_list:
        mean_pnl = mean(pnl_list)

        # population stdev (pstdev) to be stable with small N; switch to stdev if you prefer sample stdev
        sd_pnl = pstdev(pnl_list)
        if sd_pnl and sd_pnl > 0:
            sharpe_like = mean_pnl / sd_pnl

        # If expectancy was unknown but we do have a per-trade mean, use it:
        if expectancy is None:
            expectancy = mean_pnl

    return {
        "expectancy_per_trade": expectancy,
        "mean_pnl_per_trade": mean_pnl,
        "stdev_pnl_per_trade": sd_pnl,
        "sharpe_like": sharpe_like,
    }

def save_report_copy(csv_bytes: bytes, out_dir="/app/logs"):
    os.makedirs(out_dir, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S_UTC")
    with open(os.path.join(out_dir, f"trading_report_{ts}.csv"), "wb") as f:
        f.write(csv_bytes)

def send_email(html, csv_bytes):
    msg = MIMEMultipart("mixed")
    msg["Subject"]="Daily Trading Bot Report"
    msg["From"]=SENDER
    msg["To"]=",".join(RECIPIENTS) if RECIPIENTS else SENDER
    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText("Your client does not support HTML.", "plain"))
    alt.attach(MIMEText(html, "html"))
    msg.attach(alt)
    part = MIMEApplication(csv_bytes, Name="trading_report.csv")
    part.add_header("Content-Disposition", 'attachment; filename="trading_report.csv"')
    msg.attach(part)
    ses.send_raw_email(Source=SENDER, Destinations=RECIPIENTS or [SENDER], RawMessage={"Data": msg.as_string().encode("utf-8")})




def main():
    conn = get_db_conn()
    detect_notes = []
    fast_html = ""
    try:
        # Core queries (windowed where possible)
        total_pnl, open_pos, recent_trades, errors, detect_notes = run_queries(conn)

        # Derived metrics
        unreal_pnl, unreal_notes = compute_unrealized_pnl(conn, open_pos)
        detect_notes.extend(unreal_notes)

        win_rate, wins, total_trades, wr_notes = compute_win_rate(conn)
        detect_notes.extend(wr_notes)

        avg_win, avg_loss, profit_factor, ts_notes = compute_trade_stats_windowed(conn)
        detect_notes.extend(ts_notes)

        max_dd_pct, max_dd_abs, peak_eq, trough_eq, dd_notes = compute_max_drawdown(conn)
        detect_notes.extend(dd_notes)

        exposures = compute_exposures(open_pos, top_n=3)
        cash_usd, invested_usd, invested_pct, cash_notes = compute_cash_vs_invested(conn, exposures)
        detect_notes.extend(cash_notes)

        strat_rows, strat_notes = compute_strategy_breakdown_windowed(conn)
        detect_notes.extend(strat_notes)

        extras = derive_extra_metrics(
            win_rate_pct=win_rate,
            avg_win=avg_win,
            avg_loss=avg_loss,
            recent_trades=recent_trades,
        )
    finally:
        conn.close()

    # Respect local vs Docker detail verbosity
    open_pos_out = open_pos if REPORT_SHOW_DETAILS else []
    trades_out = recent_trades if REPORT_SHOW_DETAILS else []
    # 1) Load JSONL (last 24h)
    score_df = load_score_jsonl(since_hours=24)

    # 2) Summarize
    score_metrics = score_snapshot_metrics_from_jsonl(score_df)

    # 3) Render HTML snippet
    score_html = render_score_section_jsonl(score_metrics)
    # Build HTML body
    html = build_html(
        total_pnl,
        open_pos_out,
        trades_out,
        errors,
        detect_notes,
        exposures=exposures,
        unrealized_pnl=unreal_pnl,
        win_rate=win_rate,
        wins=wins,
        total_trades=total_trades,
        avg_win=avg_win,
        avg_loss=avg_loss,
        profit_factor=profit_factor,
        max_dd_pct=max_dd_pct,
        cash_usd=cash_usd,
        invested_usd=invested_usd,
        invested_pct=invested_pct,
        strat_rows=strat_rows,
        show_details=REPORT_SHOW_DETAILS,
        score_section_html=score_html,
    )

    # Near-instant roundtrips (≤60s) + CSV saved server-side when available
    try:
        sa_engine = get_sa_engine()
        fast_html, fast_csv_path, fast_df = fetch_fast_roundtrips(sa_engine)
    except Exception as e:
        if DEBUG:
            print(f"[fast_roundtrips] error: {e}")
        fast_html = (
            "<h3>Near-Instant Roundtrips (≤60s)</h3>"
            "<p style='color:#b00;'>Error computing fast roundtrips.</p>"
        )
        fast_df = None
    finally:
        if fast_html:
            if "</body></html>" in html:
                html = html.replace("</body></html>", fast_html + "\n</body></html>")
            else:
                html = html + "\n" + fast_html

    # Build CSV attachment / local artifact
    csvb = build_csv(
        total_pnl,
        open_pos_out,
        trades_out,
        exposures=exposures,
        unrealized_pnl=unreal_pnl,
        win_rate=win_rate,
        wins=wins,
        total_trades=total_trades,
        avg_win=avg_win,
        avg_loss=avg_loss,
        profit_factor=profit_factor,
        max_dd_pct=max_dd_pct,
        cash_usd=cash_usd,
        invested_usd=invested_usd,
        invested_pct=invested_pct,
        strat_rows=strat_rows,
    )

    if IN_DOCKER:
        # Docker: save copy and email via SES
        save_report_copy(csvb)
        send_email(html, csvb)
    else:
        # Local dev: pretty console output + local CSV, no email
        as_of_utc = datetime.now(timezone.utc)
        window_label = "last 24h"  # or derive from REPORT_USE_PT_DAY/LOOKBACK
        source_label = "report_trades"

        console_text = build_console_report(
            as_of_utc=as_of_utc,
            window_label=window_label,
            source_label=source_label,
            total_pnl=total_pnl,
            unrealized_pnl=unreal_pnl,
            win_rate=win_rate,
            wins=wins,
            total_trades=total_trades,
            avg_win=avg_win,
            avg_loss=avg_loss,
            profit_factor=profit_factor,
            expectancy_per_trade=extras.get("expectancy_per_trade"),
            mean_pnl_per_trade=extras.get("mean_pnl_per_trade"),
            stdev_pnl_per_trade=extras.get("stdev_pnl_per_trade"),
            sharpe_like=extras.get("sharpe_like"),
            max_dd_pct_window=max_dd_pct,
            exposures_table=exposures,     # accepts dict or list
            strat_rows=strat_rows,
            notes=detect_notes,
            fast_df=fast_df,
        )

        with open("trading_report_local.csv", "wb") as f:
            f.write(csvb)

        print(console_text)
        print("\n[Saved CSV] trading_report_local.csv")


if __name__ == "__main__":
    load_report_dotenv()
    main()

