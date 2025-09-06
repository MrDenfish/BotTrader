#!/usr/bin/env python3
import os
import io
import csv
import ssl
import boto3
import pg8000.native as pg
from decimal import Decimal
from email.mime.text import MIMEText
from email.utils import getaddresses
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timezone

REGION = os.getenv("AWS_REGION", "us-west-2")
SENDER = os.getenv("REPORT_SENDER", "").strip()
RECIPIENTS = [addr for _, addr in getaddresses([os.getenv("REPORT_RECIPIENTS", "")]) if addr]
if not SENDER or not RECIPIENTS:
    raise ValueError(f"Bad email config. REPORT_SENDER={SENDER!r}, REPORT_RECIPIENTS={os.getenv('REPORT_RECIPIENTS')!r}")

TAKER_FEE = Decimal(os.getenv("TAKER_FEE", "0.0040"))
MAKER_FEE = Decimal(os.getenv("MAKER_FEE", "0.0025"))

DEBUG = os.getenv("REPORT_DEBUG", "0").strip() in {"1", "true", "TRUE", "yes", "Yes"}

ssm = boto3.client("ssm", region_name=REGION)
ses = boto3.client("ses", region_name=REGION)

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

def _env_or_ssm(env_key: str, ssm_param_name: str | None, default: str | None = None):
    v = os.getenv(env_key)
    if v:
        return v
    if ssm_param_name:
        return get_param(ssm_param_name)
    if default is not None:
        return default
    raise RuntimeError(f"Missing {env_key} and no SSM fallback provided.")

def get_db_conn():
    url = os.getenv("DATABASE_URL")
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
        return pg.Connection(user=user, password=pwd, host=host, port=port, database=name,
                             ssl_context=_maybe_ssl_context(require_ssl))

    host = _env_or_ssm("DB_HOST", None, "db")
    port = int(_env_or_ssm("DB_PORT", None, "5432"))
    name = _env_or_ssm("DB_NAME", None, None)
    user = _env_or_ssm("DB_USER", None, None)
    pwd  = _env_or_ssm("DB_PASSWORD", None, None)
    db_ssl = (os.getenv("DB_SSL", "disable").lower() in {"require","true","1"})
    return pg.Connection(user=user, password=pwd, host=host, port=port, database=name,
                         ssl_context=_maybe_ssl_context(db_ssl))

# ---------- Identifier / information_schema helpers ----------

def split_schema_table(qualified: str) -> tuple[str, str]:
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
    # safe single-quoted string literal
    return "'" + s.replace("'", "''") + "'"

def table_columns(conn, qualified: str) -> set[str]:
    """
    Build a literal (non-parameterized) info-schema query. This avoids pg8000 parameter
    quirks in some environments.
    """
    sch, tbl = split_schema_table(qualified)
    sql = f"""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = {_sql_str(sch)} AND table_name = {_sql_str(tbl)}
    """
    rows = conn.run(sql)
    return {r[0] for r in rows}

def pick_first_available(cols_present: set[str], candidates: list[str]) -> str | None:
    for c in candidates:
        if c and c in cols_present:
            return c
    return None

# ---------- Core queries (schema-aware, Build:v3) ----------

def run_queries(conn):
    """
    Returns (total_pnl, open_positions, recent_trades, errors, detect_notes)

    - Uses env mappings where provided.
    - Builds column expressions ONLY from columns that exist in the table.
    - Applies status filter only if the table has such a column.
    - 24h window is UTC using the best-available timestamp column.
    - Falls back from REPORT_TRADES_TABLE -> public.trade_records if sparse.
    """
    errors = []
    detect_notes = ["Build:v3"]

    # Env-driven config
    tbl_trades = os.getenv("REPORT_TRADES_TABLE", "public.trade_records")
    tbl_pos    = os.getenv("REPORT_POSITIONS_TABLE", "public.report_positions")
    tbl_pnl    = os.getenv("REPORT_PNL_TABLE", "public.trade_records")

    col_symbol = os.getenv("REPORT_COL_SYMBOL")      # symbol
    col_side   = os.getenv("REPORT_COL_SIDE")        # side
    col_price  = os.getenv("REPORT_COL_PRICE")       # price
    col_size   = os.getenv("REPORT_COL_SIZE")        # qty_signed
    col_time   = os.getenv("REPORT_COL_TIME")        # ts
    col_posqty = os.getenv("REPORT_COL_POS_QTY")     # position_qty
    col_pnl    = os.getenv("REPORT_COL_PNL")         # realized_profit

    # ----- PnL -----
    try:
        if col_pnl:
            total_pnl = conn.run(f"SELECT COALESCE(SUM({qident(col_pnl)}),0) FROM {qualify(tbl_pnl)}")[0][0]
            detect_notes.append(f"PnL override: table=({tbl_pnl}) col={col_pnl}")
        else:
            cols_present = table_columns(conn, tbl_pnl)
            choice = pick_first_available(cols_present, ["realized_profit","realized_pnl","pnl","profit"])
            if not choice:
                total_pnl = 0
                detect_notes.append(f"No pnl-like column found on {tbl_pnl}")
            else:
                total_pnl = conn.run(f"SELECT COALESCE(SUM({qident(choice)}),0) FROM {qualify(tbl_pnl)}")[0][0]
                detect_notes.append(f"PnL auto: table=({tbl_pnl}) col={choice}")
    except Exception as e:
        total_pnl = 0
        errors.append(f"PnL query failed: {e}")

    # ----- Positions (non-zero qty) -----
    open_pos = []
    try:
        cols_pos = table_columns(conn, tbl_pos)
        if DEBUG:
            detect_notes.append(f"Columns({tbl_pos}): {sorted(cols_pos)}")
        if not cols_pos:
            raise RuntimeError(f"Table not found: {tbl_pos}")

        qty_col = col_posqty if (col_posqty and col_posqty in cols_pos) else \
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

    # ----- Trades (last 24h) -----
    def run_trades_for(table_name: str, use_mappings: bool = True):
        cols_tr = table_columns(conn, table_name)
        if DEBUG:
            detect_notes.append(f"Columns({table_name}): {sorted(cols_tr)}")
        if not cols_tr:
            raise RuntimeError(f"Table not found: {table_name}")

        # symbol
        sym_col = col_symbol if (use_mappings and col_symbol in cols_tr) else \
            pick_first_available(cols_tr, ["symbol", "product_id"])
        if not sym_col:
            raise RuntimeError(f"No symbol-like column on {table_name}")

        # side
        if use_mappings and col_side in cols_tr:
            side_expr = qident(col_side)
        elif "side" in cols_tr:
            side_expr = "side"
        else:
            amt_for_side = pick_first_available(
                cols_tr,
                ["qty_signed", "amount", "size", "executed_size", "filled_size", "base_amount", "remaining_size"]
            )
            if amt_for_side:
                side_expr = (
                    f"CASE WHEN {qident(amt_for_side)} < 0 THEN 'sell' "
                    f"WHEN {qident(amt_for_side)} > 0 THEN 'buy' END"
                )
            else:
                side_expr = "'?'::text"

        # price
        pr_col = col_price if (use_mappings and col_price in cols_tr) else \
            pick_first_available(cols_tr, ["price", "fill_price", "executed_price", "avg_price", "avg_fill_price", "limit_price"])
        price_expr = qident(pr_col) if pr_col else "NULL::numeric"

        # amount
        amt_col = col_size if (use_mappings and col_size in cols_tr) else \
            pick_first_available(cols_tr, ["qty_signed", "amount", "size", "executed_size", "filled_size", "base_amount", "remaining_size"])
        amt_expr = qident(amt_col) if amt_col else "NULL::numeric"

        # timestamp column
        ts_col = col_time if (use_mappings and col_time in cols_tr) else \
            pick_first_available(cols_tr, ["trade_time", "filled_at", "completed_at", "order_time", "ts", "created_at", "executed_at"])
        if not ts_col:
            raise RuntimeError(f"No time-like column on {table_name}")
        ts_expr = qident(ts_col)

        # ----- time window -----
        use_pt_day = os.getenv("REPORT_USE_PT_DAY", "0").strip() in {"1", "true", "TRUE", "yes", "Yes"}
        lookback_hours = int(os.getenv("REPORT_LOOKBACK_HOURS", "24"))

        if use_pt_day:
            # PT midnight → now (computed server-side in SQL)
            # DATE_TRUNC returns local PT midnight (timestamp without tz),
            # then AT TIME ZONE converts it back to a UTC timestamptz.
            time_window_sql = (
                f"{ts_expr} >= (DATE_TRUNC('day', (NOW() AT TIME ZONE 'America/Los_Angeles')) "
                f"AT TIME ZONE 'America/Los_Angeles')"
            )
        else:
            # Rolling lookback window in UTC
            time_window_sql = f"{ts_expr} >= (NOW() AT TIME ZONE 'UTC' - INTERVAL '{lookback_hours} hours')"

        # optional upper bound (defensive)
        upper_bound_sql = f"AND {ts_expr} < (NOW() AT TIME ZONE 'UTC')"

        # status filter only if present
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
        recent_trades = run_trades_for(tbl_trades, use_mappings=True)
        detect_notes.append(f"Trades source: {tbl_trades} (mapped where possible)")
    except Exception as e:
        errors.append(f"Trades query failed on {tbl_trades}: {e}")
        recent_trades = []

    if len(recent_trades) < 5 and tbl_trades != "public.trade_records":
        try:
            alt = run_trades_for("public.trade_records", use_mappings=False)
            if len(alt) > len(recent_trades):
                recent_trades = alt
                detect_notes.append("Trades fallback: public.trade_records (auto-detected columns)")
        except Exception as e:
            errors.append(f"Trades fallback query failed: {e}")

    return total_pnl, open_pos, recent_trades, errors, detect_notes

# ---------- Exposure Snapshot ----------

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

# ---------- Email / CSV ----------

def build_html(total_pnl, open_pos, recent_trades, errors, detect_notes, exposures=None):
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

    notes_html = ""
    if errors or detect_notes:
        items = "".join(f"<li>{e}</li>" for e in errors + detect_notes)
        notes_html = f"<h3>Notes</h3><ul>{items}</ul>"

    return f"""<html><body style="font-family:Arial,Helvetica,sans-serif">
    <h2>Daily Trading Bot Report</h2><p><b>As of:</b> {now_utc}</p>
    <h3>Key Metrics</h3>
    <table border="1" cellpadding="6" cellspacing="0"><tr><th>Total Realized PnL (USD)</th></tr>
      <tr><td>{round(float(total_pnl or 0), 2):,.2f}</td></tr>
    </table>
    {exposure_html}
    <h3>Open Positions</h3>
    <table border="1" cellpadding="6" cellspacing="0">
      <tr><th>Symbol</th><th>Qty</th><th>Avg Price</th></tr>{rows(open_pos)}
    </table>
    <h3>Trades (Last 24h)</h3>
    <table border="1" cellpadding="6" cellspacing="0">
      <tr><th>Symbol</th><th>Side</th><th>Price</th><th>Amount</th><th>Time</th></tr>{rows(recent_trades)}
    </table>
    {notes_html}
    <p style="color:#666">CSV attachment includes these tables.</p>
    </body></html>"""

def build_csv(total_pnl, open_pos, recent_trades, exposures=None):
    buf = io.StringIO(); w = csv.writer(buf)
    w.writerow(["Daily Trading Bot Report"])
    w.writerow(["Generated (UTC)", datetime.utcnow().isoformat()])
    w.writerow([])
    w.writerow(["Total Realized PnL (USD)"]); w.writerow([round(float(total_pnl or 0), 2)]); w.writerow([])
    if exposures and exposures.get("total_notional", 0) > 0:
        w.writerow(["Exposure Snapshot"])
        w.writerow(["Total Notional (USD)", f"{exposures['total_notional']:.2f}"])
        w.writerow(["Symbol","Notional","% of Total","Side","Qty","Avg Price"])
        for it in exposures["items"]:
            w.writerow([it["symbol"], f"{it['notional']:.2f}", f"{it['pct']:.1f}%", it["side"], it["qty"], it["price"]])
        w.writerow([])
    w.writerow(["Open Positions"]); w.writerow(["Symbol","Qty","Avg Price"])
    for r in open_pos:
        r = [c.isoformat() if hasattr(c, "isoformat") else c for c in r]
        w.writerow(r)
    w.writerow([])
    w.writerow(["Trades (Last 24h)"]); w.writerow(["Symbol","Side","Price","Amount","Time"])
    for r in recent_trades:
        r = [c.isoformat() if hasattr(c, "isoformat") else c for c in r]
        w.writerow(r)
    return buf.getvalue().encode("utf-8")

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
    try:
        total_pnl, open_pos, recent_trades, errors, detect_notes = run_queries(conn)
    finally:
        conn.close()
    exposures = compute_exposures(open_pos, top_n=3)
    html = build_html(total_pnl, open_pos, recent_trades, errors, detect_notes, exposures=exposures)
    csvb = build_csv(total_pnl, open_pos, recent_trades, exposures=exposures)
    save_report_copy(csvb)
    send_email(html, csvb)

if __name__ == "__main__":
    main()




# === Added: Strategy Breakdown (Matrix vs Passive vs Accumulation) ===

def _table_exists(conn, schema: str, table: str) -> bool:
    try:
        q = """
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = %s AND table_name = %s
        """
        return bool(conn.run(q, (schema, table)))
    except Exception:
        return False

def _first_ts_col(conn, schema: str, table: str):
    # prefer order_time, then trade_time, then created_at, then time
    cols = {c.lower() for c in table_columns(conn, f"{schema}.{table}")}
    for c in ["order_time","trade_time","event_time","created_at","time","ts"]:
        if c in cols:
            return c
    return None

def _first_col(conn, schema: str, table: str, options):
    cols = {c.lower() for c in table_columns(conn, f"{schema}.{table}")}
    for c in options:
        if c in cols:
            return c
    return None

def run_strategy_breakdown(conn):
    """
    Returns a list of dicts with strategy stats for the last 24h:
    [ {strategy, orders, fills, gross_cashflow, est_fees, pnl_cashflow, realized_pnl, win_trades, loss_trades} ]
    Strategy attribution heuristic:
      - type='tp_sl' -> excluded from strategy buckets (but available as exits)
      - passive: source or notes contains 'passive'
      - accumulation: source or notes contains 'accum'
      - otherwise: matrix
    """
    errors = []
    # Determine table
    # Try env override
    tbl = os.getenv("REPORT_TRADES_TABLE", "").strip() or "public.trade_records"
    if "." in tbl:
        schema, table = tbl.split(".", 1)
    else:
        schema, table = "public", tbl

    if not _table_exists(conn, schema, table):
        return {"rows": [], "errors": ["strategy: trades table not found: %s.%s" % (schema, table)]}

    # Columns
    ts_col = _first_ts_col(conn, schema, table) or "order_time"
    side_col = _first_col(conn, schema, table, ["side"]) or "side"
    price_col = _first_col(conn, schema, table, ["price","avg_price"]) or "price"
    amt_col = _first_col(conn, schema, table, ["amount","size","filled_size","qty","quantity","base_amount"]) or "amount"
    type_col = _first_col(conn, schema, table, ["type"]) or "type"
    source_col = _first_col(conn, schema, table, ["source"]) or "source"
    notes_col = _first_col(conn, schema, table, ["notes","comment","meta"]) or None
    pnl_cols = [c for c in ["realized_pnl","realized_profit","pnl_usd","pnl","profit"] if _first_col(conn, schema, table, [c])]
    fee_col = _first_col(conn, schema, table, ["fee","fees","fee_usd","commission","commission_usd"])  # optional

    # Build attribution expressions
    # CASE for strategy
    notes_expr = f"COALESCE({notes_col}, '')" if notes_col else "''"
    strat_case = f"""
    CASE
      WHEN LOWER(COALESCE({type_col},'')) = 'tp_sl' THEN 'exits'
      WHEN LOWER(COALESCE({source_col},'')) LIKE '%passive%' OR LOWER({notes_expr}) LIKE '%passive%' THEN 'passive'
      WHEN LOWER(COALESCE({source_col},'')) LIKE '%accum%'  OR LOWER({notes_expr}) LIKE '%accum%'  THEN 'accumulation'
      ELSE 'matrix'
    END AS strategy
    """

    # Time window: last 24h UTC
    win = f"({ts_col} >= (NOW() AT TIME ZONE 'UTC') - INTERVAL '24 hours')"

    # Cashflow math
    cash_expr = f"CASE WHEN LOWER({side_col})='sell' THEN {amt_col}*{price_col} ELSE -({amt_col}*{price_col}) END"
    fee_expr = f"COALESCE({fee_col},0)" if fee_col else "0"

    # Prefer realized pnl column if present for 'realized_pnl' metric; else NULL
    if pnl_cols:
        pnl_expr = "COALESCE(" + ",".join(pnl_cols) + ")"
    else:
        pnl_expr = "NULL"

    q = f"""
    WITH base AS (
      SELECT
        {strat_case},
        {cash_expr} AS cashflow,
        {fee_expr}   AS fee,
        {pnl_expr}   AS realized_pnl
      FROM {schema}.{table}
      WHERE {win}
        AND COALESCE(LOWER({type_col}),'limit') <> 'tp_sl'  -- exclude exits from strategy buckets
        AND {price_col} IS NOT NULL
        AND {amt_col}   IS NOT NULL
    )
    SELECT
      strategy,
      COUNT(*)                       AS orders,
      SUM(CASE WHEN cashflow <> 0 THEN 1 ELSE 0 END) AS fills, -- approximate
      SUM(cashflow)                  AS pnl_cashflow,
      SUM(fee)                       AS est_fees,
      SUM(realized_pnl)              AS realized_pnl
    FROM base
    GROUP BY strategy
    ORDER BY strategy;
    """
    try:
        rows = conn.run(q)
    except Exception as e:
        return {"rows": [], "errors": [f"strategy query failed: {e}"]}

    # Normalize rows into dicts
    out = []
    for r in rows:
        strategy, orders, fills, pnl_cash, est_fees, realized = r
        out.append({
            "strategy": strategy,
            "orders": int(orders or 0),
            "fills": int(fills or 0),
            "pnl_cashflow": float(pnl_cash or 0),
            "est_fees": float(est_fees or 0),
            "realized_pnl": (float(realized) if realized is not None else None),
        })
    return {"rows": out, "errors": errors}


# Override build_html to include Strategy section (keeps prior sections intact)
def build_html(total_pnl, open_pos, recent_trades, errors, detect_notes, exposures=None, strategies=None):
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

    exposure_html = ""
    if exposures:
        items = exposures.get("items", [])
        total_notional = exposures.get("total_notional", 0)
        if items:
            exp_rows = "".join(
                f"<tr><td>{it['symbol']}</td><td style='text-align:right'>{it['qty']}</td>"
                f"<td style='text-align:right'>{it['price']}</td>"
                f"<td style='text-align:right'>{it['notional']:.2f}</td>"
                f"<td style='text-align:right'>{it['pct']:.2f}%</td></tr>"
                for it in items
            )
            exposure_html = f"""
            <h3>Top Exposures</h3>
            <table border="1" cellpadding="4" cellspacing="0">
              <tr><th>Symbol</th><th>Qty</th><th>Avg Price</th><th>Notional</th><th>% of Total</th></tr>
              {exp_rows}
              <tr><td colspan="3"><b>Total</b></td><td colspan="2"><b>{total_notional:.2f}</b></td></tr>
            </table>
            """

    # New Strategy section
    strat_html = ""
    if strategies and strategies.get("rows"):
        rows = []
        for d in strategies["rows"]:
            rpnl = "" if d["realized_pnl"] is None else f"{d['realized_pnl']:.2f}"
            rows.append(
                f"<tr><td>{d['strategy']}</td>"
                f"<td style='text-align:right'>{d['orders']}</td>"
                f"<td style='text-align:right'>{d['fills']}</td>"
                f"<td style='text-align:right'>{d['pnl_cashflow']:.2f}</td>"
                f"<td style='text-align:right'>{d['est_fees']:.2f}</td>"
                f"<td style='text-align:right'>{rpnl}</td></tr>"
            )
        sh = "".join(rows)

        strat_errs = "".join(f"<li>{e}</li>" for e in strategies.get("errors", []))
        strat_html = f"""
        <h3>Strategy Performance (Last 24h)</h3>
        <table border="1" cellpadding="4" cellspacing="0">
          <tr><th>Strategy</th><th>Orders</th><th>Fills</th><th>PnL (Cashflow)</th><th>Est. Fees</th><th>Realized PnL</th></tr>
          {sh or "<tr><td colspan='6'>No strategy activity</td></tr>"}
        </table>
        {'<ul>'+strat_errs+'</ul>' if strat_errs else ''}
        """

    html = f"""
    <html><body>
    <h2>Daily Trading Bot Report</h2>
    <p><i>As of: {now_utc}</i></p>

    <h3>Key Metrics</h3>
    <table border="1" cellpadding="4" cellspacing="0">
      <tr><th>Total Realized PnL (USD)</th></tr>
      <tr><td>{total_pnl}</td></tr>
    </table>

    {exposure_html}
    {strat_html}

    <h3>Open Positions</h3>
    <table border="1" cellpadding="4" cellspacing="0">
      <tr><th>Symbol</th><th>Qty</th><th>Avg Price</th></tr>
      {rows(open_pos)}
    </table>

    <h3>Trades (Last 24h)</h3>
    <table border="1" cellpadding="4" cellspacing="0">
      <tr><th>Symbol</th><th>Side</th><th>Price</th><th>Amount</th><th>Time</th></tr>
      {rows(recent_trades)}
    </table>

    <h3>Diagnostics</h3>
    <ul>
      {"".join(f"<li>{e}</li>" for e in errors)}
      {"".join(f"<li>{n}</li>" for n in detect_notes)}
    </ul>
    </body></html>
    """
    return html

# Override build_csv to include strategy section
def build_csv(total_pnl, open_pos, recent_trades, exposures=None, strategies=None):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Daily Trading Bot Report"])
    from datetime import datetime as _dt
    w.writerow([f"As of: {_dt.utcnow().strftime('%Y-%m-%d %H:%M UTC')}"])
    w.writerow([])
    w.writerow(["Key Metrics"])
    w.writerow(["Total Realized PnL (USD)"])
    w.writerow([total_pnl])
    w.writerow([])

    if exposures:
        w.writerow(["Top Exposures"])
        w.writerow(["Symbol","Qty","Avg Price","Notional","% of Total"])
        for it in exposures.get("items", []):
            w.writerow([it["symbol"], it["qty"], it["price"], f"{it['notional']:.2f}", f"{it['pct']:.2f}%"])
        w.writerow(["Total Notional", exposures.get("total_notional", 0)])
        w.writerow([])

    if strategies and strategies.get("rows"):
        w.writerow(["Strategy Performance (Last 24h)"])
        w.writerow(["Strategy","Orders","Fills","PnL (Cashflow)","Est. Fees","Realized PnL"])
        for d in strategies["rows"]:
            w.writerow([d["strategy"], d["orders"], d["fills"], f"{d['pnl_cashflow']:.2f}", f"{d['est_fees']:.2f}", "" if d["realized_pnl"] is None else f"{d['realized_pnl']:.2f}"])
        w.writerow([])

    w.writerow(["Open Positions"]); w.writerow(["Symbol","Qty","Avg Price"])
    for r in open_pos:
        r = [c.isoformat() if hasattr(c, "isoformat") else c for c in r]
        w.writerow(r)
    w.writerow([])
    w.writerow(["Trades (Last 24h)"]); w.writerow(["Symbol","Side","Price","Amount","Time"])
    for r in recent_trades:
        r = [c.isoformat() if hasattr(c, "isoformat") else c for c in r]
        w.writerow(r)
    return buf.getvalue().encode("utf-8")


# Override main to compute strategies and pass into report builders
def main():
    conn = get_db_conn()
    try:
        total_pnl, open_pos, recent_trades, errors, detect_notes = run_queries(conn)
        strategies = run_strategy_breakdown(conn)
    finally:
        conn.close()
    exposures = compute_exposures(open_pos, top_n=3)
    html = build_html(total_pnl, open_pos, recent_trades, errors, detect_notes, exposures=exposures, strategies=strategies)
    csvb = build_csv(total_pnl, open_pos, recent_trades, exposures=exposures, strategies=strategies)
    save_report_copy(csvb)
    send_email(html, csvb)



