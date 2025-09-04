#!/usr/bin/env python3
import boto3
import pg8000.native as pg
import numpy as np, pandas as pd
import os, io, csv, datetime, ssl

from decimal import Decimal
from email.mime.text import MIMEText
from email.utils import getaddresses
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication


REGION = os.getenv("AWS_REGION", "us-west-2")
SENDER = os.getenv("REPORT_SENDER", "").strip()
RECIPIENTS = [addr for _, addr in getaddresses([os.getenv("REPORT_RECIPIENTS","")]) if addr]
if not SENDER or not RECIPIENTS:
    raise ValueError(f"Bad email config. REPORT_SENDER={SENDER!r}, REPORT_RECIPIENTS={os.getenv('REPORT_RECIPIENTS')!r}")
TAKER_FEE = Decimal(os.getenv("TAKER_FEE","0.0040"))
MAKER_FEE = Decimal(os.getenv("MAKER_FEE","0.0025"))

ssm = boto3.client("ssm", region_name=REGION)
ses = boto3.client("ses", region_name=REGION)

def get_param(name): return ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]

def _maybe_ssl_context(require_ssl: bool):
    if not require_ssl:
        return None
    ctx = ssl.create_default_context()
    # Allow override; falls back to system CA store (works for RDS or other CAs if present)
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
        return get_param(ssm_param_name)  # your existing SSM helper
    if default is not None:
        return default
    raise RuntimeError(f"Missing {env_key} and no SSM fallback provided.")

# --- REPLACE your get_db_conn() with this ---
def get_db_conn():
    """
    Connection priority:
      1) DATABASE_URL (postgres:// or postgresql://; honors ?sslmode=require/verify-*)
      2) DB_* environment variables (with optional DB_SSL=require|disable)
      3) SSM parameters (SSM_DB_HOST, SSM_DB_NAME, SSM_DB_USER, SSM_DB_PASSWORD)
    """
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
        require_ssl = sslmode in {"require", "verify-ca", "verify-full"} or "+ssl" in u.scheme
        return pg.Connection(
            user=user, password=pwd, host=host, port=port, database=name,
            ssl_context=_maybe_ssl_context(require_ssl)
        )

    # 2) ENV block
    host = _env_or_ssm("DB_HOST", None, "db")
    port = int(_env_or_ssm("DB_PORT", None, "5432"))
    name = _env_or_ssm("DB_NAME", None, None)
    user = _env_or_ssm("DB_USER", None, None)
    pwd  = _env_or_ssm("DB_PASSWORD", None, None)
    db_ssl = (os.getenv("DB_SSL", "disable").lower() in {"require", "true", "1"})
    return pg.Connection(
        user=user, password=pwd, host=host, port=port, database=name,
        ssl_context=_maybe_ssl_context(db_ssl)
    )

    # 3) (Falls back to SSM only if you actually set SSM_* names)
    # Uncomment below if you want pure-SSM fallback when none of the above are set:
    # host = _env_or_ssm("DB_HOST", os.getenv("SSM_DB_HOST"))
    # name = _env_or_ssm("DB_NAME", os.getenv("SSM_DB_NAME"))
    # user = _env_or_ssm("DB_USER", os.getenv("SSM_DB_USER"))
    # pwd  = _env_or_ssm("DB_PASSWORD", os.getenv("SSM_DB_PASSWORD"))
    # return pg.Connection(user=user, password=pwd, host=host, port=5432, database=name,
    #                      ssl_context=_maybe_ssl_context(True))

# ---------- Auto-detection helpers ----------
SYN_SYMBOL = {"symbol","ticker","asset","pair","instrument"}
SYN_SIDE   = {"side","direction","action","buy_sell"}
SYN_PRICE  = {"price","fill_price","avg_price","execution_price","entry_price"}
SYN_SIZE   = {"size","amount","qty","quantity","filled_size","trade_qty","position_size","position_qty","net_qty","open_qty","remaining_size"}
SYN_TIME   = {"order_time","timestamp","created_at","executed_at","filled_at","time","ts"}
SYN_PNL    = {"realized_profit","realized_pnl","pnl","profit"}

def load_catalog(conn):
    rows = conn.run("""
        SELECT table_schema, table_name, column_name, data_type
        FROM information_schema.columns
        WHERE table_schema NOT IN ('pg_catalog','information_schema')
        ORDER BY table_schema, table_name, ordinal_position;
    """)
    tables = {}
    for schema, tname, col, dtype in rows:
        key = (schema, tname)
        tables.setdefault(key, {})[col.lower()] = dtype
    return tables  # {(schema,table): {col: dtype, ...}}

def compute_kpis(trades_df: pd.DataFrame) -> dict:
    pnl = pd.to_numeric(trades_df["realized_pnl"], errors="coerce").fillna(0)
    wins = pnl[pnl>0]; losses = pnl[pnl<0]
    gross_win = wins.sum(); gross_loss = -losses.sum()
    p = (pnl>0).mean() if len(pnl) else 0
    avg_win = wins.mean() if len(wins) else 0
    avg_loss = losses.mean() if len(losses) else 0  # negative
    profit_factor = (gross_win / gross_loss) if gross_loss else np.inf
    expectancy = p*avg_win + (1-p)*avg_loss

    # simple realized-only “equity” curve from daily sums
    daily = trades_df.assign(day=pd.to_datetime(trades_df["ts"]).dt.date).groupby("day")["realized_pnl"].sum()
    cum = daily.cumsum()
    drawdown = cum - cum.cummax()
    max_dd = float(drawdown.min()) if len(drawdown) else 0.0
    sharpe = float(daily.mean()/daily.std()*np.sqrt(252)) if daily.std() else float("nan")

    return dict(
        trades=int(len(trades_df)),
        win_rate=float(p),
        profit_factor=float(profit_factor),
        expectancy=float(expectancy),
        max_drawdown=max_dd,
        sharpe=sharpe,
        fees_usd=float(trades_df.get("fee_usd", pd.Series(0)).sum()),
    )

def best_table_for(tables, needed_sets, optional_sets=()):
    """
    Pick the table with max matches across the provided synonym sets.
    needed_sets: [set_of_synonyms_for_required_field, ...]
    optional_sets: like needed, but lower weight
    Returns: (schema, table, mapping_dict) or None
    mapping_dict maps canonical keys ('symbol','side','price','size','time','pnl','pos_qty') to actual column names.
    """
    best = None
    best_score = -1
    for (schema, tname), cols in tables.items():
        colset = set(cols.keys())
        mapping = {}
        score = 0
        # required
        ok = True
        for canon, synset in needed_sets.items():
            match = next((c for c in synset if c in colset), None)
            if not match:
                ok = False; break
            mapping[canon] = match; score += 3
        if not ok: continue
        # optional
        for canon, synset in optional_sets.items():
            match = next((c for c in synset if c in colset), None)
            if match:
                mapping[canon] = match; score += 1
        if score > best_score:
            best = (schema, tname, mapping); best_score = score
    return best

def qident(name):
    # naive identifier quoting (safe for names from catalog)
    return '"' + name.replace('"','""') + '"'

def qualify(schema, table): return f'{qident(schema)}.{qident(table)}'

# ---------- Queries with detection ----------
def run_queries(conn):
    errors = []
    detect_notes = []

    # Allow manual overrides
    o_tbl_trades = os.getenv("REPORT_TRADES_TABLE")
    o_tbl_pos    = os.getenv("REPORT_POSITIONS_TABLE")
    o_tbl_pnl    = os.getenv("REPORT_PNL_TABLE")

    o_col_symbol = os.getenv("REPORT_COL_SYMBOL")
    o_col_side   = os.getenv("REPORT_COL_SIDE")
    o_col_price  = os.getenv("REPORT_COL_PRICE")
    o_col_size   = os.getenv("REPORT_COL_SIZE")
    o_col_time   = os.getenv("REPORT_COL_TIME")
    o_col_pnl    = os.getenv("REPORT_COL_PNL")
    o_col_posqty = os.getenv("REPORT_COL_POS_QTY")

    tables = load_catalog(conn)

    # 1) Detect trades table (needs symbol, side, price, size, time)
    trades = None
    trades_map = {}
    if o_tbl_trades:
        # Expect form schema.table or just table (assume search_path first schema)
        if "." in o_tbl_trades:
            sch, tb = o_tbl_trades.split(".",1)
        else:
            # find by table name in any non-system schema
            match = next(((s,t) for (s,t) in tables.keys() if t == o_tbl_trades), None)
            if match: sch, tb = match
            else: sch, tb = "public", o_tbl_trades
        trades = (sch, tb, {})
        trades_map = {
            "symbol": o_col_symbol or "symbol",
            "side":   o_col_side   or "side",
            "price":  o_col_price  or "price",
            "size":   o_col_size   or "size",
            "time":   o_col_time   or "order_time",
        }
        detect_notes.append(f"Trades override: {sch}.{tb} columns={trades_map}")
    else:
        needed = {"symbol": SYN_SYMBOL, "side": SYN_SIDE, "price": SYN_PRICE, "size": SYN_SIZE, "time": SYN_TIME}
        trades = best_table_for(tables, needed, optional_sets={})
        if trades:
            detect_notes.append(f"Trades detected: {trades[0]}.{trades[1]} columns={trades[2]}")
            trades_map = trades[2]
        else:
            errors.append("trades detection: no table with symbol/side/price/size/time found")

    # 2) Detect positions table (prefer explicit pos qty), else compute from trades
    pos = None
    pos_map = {}
    if o_tbl_pos:
        if "." in o_tbl_pos: sch, tb = o_tbl_pos.split(".",1)
        else:
            match = next(((s,t) for (s,t) in tables.keys() if t == o_tbl_pos), None)
            if match: sch, tb = match
            else: sch, tb = "public", o_tbl_pos
        pos = (sch, tb, {})
        pos_map = {
            "symbol":  o_col_symbol or "symbol",
            "pos_qty": o_col_posqty or "remaining_size",
            "price":   o_col_price  or "avg_price",
        }
        detect_notes.append(f"Positions override: {sch}.{tb} columns={pos_map}")
    else:
        needed_pos = {"symbol": SYN_SYMBOL, "pos_qty": SYN_SIZE}
        pos = best_table_for(tables, needed_pos, optional_sets={"price": SYN_PRICE})
        if pos:
            detect_notes.append(f"Positions detected: {pos[0]}.{pos[1]} columns={pos[2]}")
            pos_map = pos[2]

    # 3) Detect PnL table/column
    pnl_tbl = None
    pnl_col = None
    if o_tbl_pnl or o_col_pnl:
        if o_tbl_pnl:
            if "." in o_tbl_pnl: sch, tb = o_tbl_pnl.split(".",1)
            else:
                match = next(((s,t) for (s,t) in tables.keys() if t == o_tbl_pnl), None)
                if match: sch, tb = match
                else: sch, tb = "public", o_tbl_pnl
            pnl_tbl = (sch, tb)
        # find a usable pnl column on that table or any table
        if o_col_pnl:
            pnl_col = o_col_pnl
        else:
            # pick first synonym present
            cols = tables.get(pnl_tbl, {})
            pnl_col = next((c for c in SYN_PNL if c in cols), None)
        detect_notes.append(f"PnL override: table={pnl_tbl} col={pnl_col}")
    else:
        # scan for any table that has a pnl-like column
        for (sch,tb), cols in tables.items():
            match = next((c for c in SYN_PNL if c in cols), None)
            if match:
                pnl_tbl = (sch,tb); pnl_col = match
                detect_notes.append(f"PnL detected: {sch}.{tb}.{match}")
                break
        if not pnl_tbl:
            detect_notes.append("PnL detection: no table with realized_profit/realized_pnl/pnl/profit found")

    # ---------- Execute queries ----------
    total_pnl = 0.0
    open_pos = []
    recent_trades = []

    # PnL
    try:
        if pnl_tbl and pnl_col:
            sql = f"SELECT COALESCE(SUM({qident(pnl_col)}),0) FROM {qualify(*pnl_tbl)};"
            total_pnl = conn.run(sql)[0][0]
        else:
            total_pnl = 0.0
            errors.append("total_pnl: no PnL column/table detected; returning 0")
    except Exception as e:
        errors.append(f"total_pnl: {e}")

    # Recent trades (last 24h)
    try:
        if trades:
            sch, tb, m = trades
            sym = qident(trades_map.get("symbol","symbol"))
            side= qident(trades_map.get("side","side"))
            price=qident(trades_map.get("price","price"))
            size =qident(trades_map.get("size","size"))
            timec=qident(trades_map.get("time","order_time"))
            sql = f"""
                SELECT {sym} AS symbol,
                       {side} AS side,
                       {price} AS price,
                       {size} AS amount,
                       {timec} AS "time"
                FROM {qualify(sch,tb)}
                WHERE {timec} >= NOW() - INTERVAL '24 hours'
                ORDER BY {timec} DESC;
            """
            recent_trades = conn.run(sql)
        else:
            errors.append("recent_trades: no trades table detected")
    except Exception as e:
        errors.append(f"recent_trades: {e}")

    # Open positions
    try:
        if pos:
            sch, tb, m = pos
            sym = qident(pos_map.get("symbol","symbol"))
            qty = qident(pos_map.get("pos_qty","remaining_size"))
            price = qident(pos_map.get("price","avg_price"))
            sql = f"""
                SELECT {sym} AS symbol, {qty} AS qty, {price} AS avg_price
                FROM {qualify(sch,tb)}
                WHERE {qty} <> 0
                ORDER BY {sym};
            """
            open_pos = conn.run(sql)
        elif trades:
            # Derive from trades: net qty per symbol; weighted avg price on buys
            sch, tb, m = trades
            sym = qident(trades_map.get("symbol","symbol"))
            side= qident(trades_map.get("side","side"))
            price=qident(trades_map.get("price","price"))
            size =qident(trades_map.get("size","size"))
            sql = f"""
                WITH t AS (
                  SELECT {sym} AS symbol,
                         LOWER({side}) AS side,
                         {price}::numeric AS price,
                         {size}::numeric AS size
                  FROM {qualify(sch,tb)}
                ),
                agg AS (
                  SELECT symbol,
                    SUM(CASE WHEN side IN ('buy','b','long')  THEN size
                             WHEN side IN ('sell','s','short') THEN -size ELSE 0 END) AS qty,
                    SUM(CASE WHEN side IN ('buy','b','long') THEN price*size ELSE 0 END) AS cost_buy,
                    SUM(CASE WHEN side IN ('buy','b','long') THEN size ELSE 0 END) AS buy_size
                  FROM t
                  GROUP BY symbol
                )
                SELECT symbol,
                       qty,
                       CASE WHEN buy_size > 0 THEN ROUND((cost_buy / buy_size)::numeric, 8) ELSE NULL END AS avg_price
                FROM agg
                WHERE qty <> 0
                ORDER BY symbol;
            """
            open_pos = conn.run(sql)
            detect_notes.append("Positions computed from trades (no positions table found).")
        else:
            errors.append("open_positions: neither positions nor trades table detected")
    except Exception as e:
        errors.append(f"open_positions: {e}")

    return total_pnl, open_pos, recent_trades, errors, detect_notes

# ---------- Email building ----------
def build_html(total_pnl, open_pos, recent_trades, errors, detect_notes):
    def rows(rows):
        if not rows: return "<tr><td colspan='99'>None</td></tr>"
        out=[]
        for r in rows:
            # Convert datetimes for HTML
            rr=[]
            for c in r:
                if hasattr(c, "isoformat"): rr.append(c.isoformat())
                else: rr.append(c)
            out.append("<tr>" + "".join(f"<td>{c}</td>" for c in rr) + "</tr>")
        return "".join(out)

    now = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    notes_html = ""
    if errors or detect_notes:
        items = "".join(f"<li>{e}</li>" for e in errors + detect_notes)
        notes_html = f"<h3>Notes</h3><ul>{items}</ul>"

    return f"""<html><body style="font-family:Arial,Helvetica,sans-serif">
    <h2>Daily Trading Bot Report</h2><p><b>As of:</b> {now}</p>
    <h3>Key Metrics</h3>
    <table border="1" cellpadding="6" cellspacing="0"><tr><th>Total Realized PnL (USD)</th></tr>
      <tr><td>{round(float(total_pnl or 0),2)}</td></tr>
    </table>
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

def build_csv(total_pnl, open_pos, recent_trades):
    buf = io.StringIO(); w = csv.writer(buf)
    w.writerow(["Daily Trading Bot Report"])
    w.writerow(["Generated (UTC)", datetime.datetime.utcnow().isoformat()])
    w.writerow([])
    w.writerow(["Total Realized PnL (USD)"]); w.writerow([round(float(total_pnl or 0),2)]); w.writerow([])
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

    ses.send_raw_email(Source=SENDER, Destinations=RECIPIENTS or [SENDER],
                       RawMessage={"Data": msg.as_string().encode("utf-8")})

def main():
    conn = get_db_conn()
    try:
        total_pnl, open_pos, recent_trades, errors, detect_notes = run_queries(conn)
    finally:
        conn.close()
    html = build_html(total_pnl, open_pos, recent_trades, errors, detect_notes)
    csvb = build_csv(total_pnl, open_pos, recent_trades)
    save_report_copy(csvb)
    send_email(html, csvb)

if __name__ == "__main__":
    main()
