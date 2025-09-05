#!/usr/bin/env python3
import os
import io
import csv
import ssl
import boto3
import pg8000.native as pg
import numpy as np
import pandas as pd

from decimal import Decimal
from email.mime.text import MIMEText
from email.utils import getaddresses
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timezone

# =========================
# Configuration & Clients
# =========================

REGION = os.getenv("AWS_REGION", "us-west-2")
SENDER = os.getenv("REPORT_SENDER", "").strip()
RECIPIENTS = [addr for _, addr in getaddresses([os.getenv("REPORT_RECIPIENTS", "")]) if addr]

if not SENDER or not RECIPIENTS:
    raise ValueError(
        f"Bad email config. REPORT_SENDER={SENDER!r}, REPORT_RECIPIENTS={os.getenv('REPORT_RECIPIENTS')!r}"
    )

# Fees only used if you later choose to display fee-adjusted stats
TAKER_FEE = Decimal(os.getenv("TAKER_FEE", "0.0040"))
MAKER_FEE = Decimal(os.getenv("MAKER_FEE", "0.0025"))

ssm = boto3.client("ssm", region_name=REGION)
ses = boto3.client("ses", region_name=REGION)


# =========================
# Utilities
# =========================

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
    """
    Connection priority:
      1) DATABASE_URL (postgres:// or postgresql://; honors ?sslmode=require/verify-*)
      2) DB_* environment variables (with optional DB_SSL=require|true|1)
      3) SSM (not used by default here, but function supports it via _env_or_ssm)
    """
    url = os.getenv("DATABASE_URL")
    if url:
        u = urlparse(url)
        host = u.hostname or "db"
        port = int(u.port or 5432)
        user = u.username
        pwd = u.password
        name = (u.path or "/").lstrip("/")
        qs = parse_qs(u.query or "")
        sslmode = (qs.get("sslmode", [""])[0] or "").lower()
        require_ssl = sslmode in {"require", "verify-ca", "verify-full"} or "+ssl" in (u.scheme or "")
        return pg.Connection(
            user=user,
            password=pwd,
            host=host,
            port=port,
            database=name,
            ssl_context=_maybe_ssl_context(require_ssl),
        )

    host = _env_or_ssm("DB_HOST", None, "db")
    port = int(_env_or_ssm("DB_PORT", None, "5432"))
    name = _env_or_ssm("DB_NAME", None, None)
    user = _env_or_ssm("DB_USER", None, None)
    pwd = _env_or_ssm("DB_PASSWORD", None, None)
    db_ssl = (os.getenv("DB_SSL", "disable").lower() in {"require", "true", "1"})

    return pg.Connection(
        user=user,
        password=pwd,
        host=host,
        port=port,
        database=name,
        ssl_context=_maybe_ssl_context(db_ssl),
    )


# =========================
# Core Queries
# =========================

def run_queries(conn):
    """
    Return (total_pnl, open_positions, recent_trades, errors, notes)

    - Trades: prefer REPORT_TRADES_TABLE w/ mapped columns; if sparse (<5 rows),
      auto-fallback to public.trade_records with auto-detected columns.
    - Positions: REPORT_POSITIONS_TABLE (fallback public.positions), only non-zero qty.
    - PnL: sum REPORT_COL_PNL if set; else coalesce common synonyms.
    """
    errors = []
    detect_notes = []

    # --- Env-driven table/column config ---
    tbl_trades = os.getenv("REPORT_TRADES_TABLE", "public.trade_records")
    tbl_pos = os.getenv("REPORT_POSITIONS_TABLE", "public.report_positions")
    tbl_pnl = os.getenv("REPORT_PNL_TABLE", "public.trade_records")

    col_symbol = os.getenv("REPORT_COL_SYMBOL")         # e.g., symbol
    col_side = os.getenv("REPORT_COL_SIDE")             # e.g., side
    col_price = os.getenv("REPORT_COL_PRICE")           # e.g., price
    col_size = os.getenv("REPORT_COL_SIZE")             # e.g., qty_signed
    col_time = os.getenv("REPORT_COL_TIME")             # e.g., ts
    col_posqty = os.getenv("REPORT_COL_POS_QTY")        # e.g., position_qty
    col_pnl = os.getenv("REPORT_COL_PNL")               # e.g., realized_profit

    # ---------- TOTAL REALIZED PNL ----------
    try:
        if col_pnl:
            total_pnl = conn.run(f"""
                SELECT COALESCE(SUM({col_pnl}), 0) FROM {tbl_pnl}
            """)[0][0]
            detect_notes.append(f"PnL override: table=({tbl_pnl}) col={col_pnl}")
        else:
            total_pnl = conn.run(f"""
                SELECT COALESCE(SUM(COALESCE(realized_profit, realized_pnl, pnl, profit)), 0)
                FROM {tbl_pnl}
            """)[0][0]
            detect_notes.append(
                f"PnL auto-detect: table=({tbl_pnl}) coalesce(realized_profit, realized_pnl, pnl, profit)"
            )
    except Exception as e:
        total_pnl = 0
        errors.append(f"PnL query failed: {e}")

    # ---------- OPEN POSITIONS (inventory snapshot, not open orders) ----------
    open_pos = []
    try:
        qty_expr = col_posqty or "COALESCE(position_qty, pos_qty, qty, size, amount)"
        price_expr = "COALESCE(avg_price, price)"
        q = f"""
            SELECT symbol,
                   {qty_expr} AS qty,
                   {price_expr} AS avg_price
            FROM {tbl_pos}
            WHERE COALESCE({qty_expr}, 0) <> 0
            ORDER BY symbol
        """
        open_pos = conn.run(q)
        detect_notes.append(
            f"Positions source: {tbl_pos} qty_col={col_posqty or 'auto'} price_col=auto"
        )
    except Exception as e1:
        # Fallback to plain positions if report_positions missing
        try:
            q2 = f"""
                SELECT symbol,
                       COALESCE(position_qty, pos_qty, qty, size, amount) AS qty,
                       COALESCE(avg_price, price) AS avg_price
                FROM public.positions
                WHERE COALESCE(COALESCE(position_qty, pos_qty, qty, size, amount), 0) <> 0
                ORDER BY symbol
            """
            open_pos = conn.run(q2)
            detect_notes.append("Positions fallback: public.positions")
        except Exception as e2:
            errors.append(f"Positions query failed: {e1} / {e2}")

    # ---------- TRADES (last 24h) ----------
    def _trades_query(table_name: str, use_mapped: bool = True) -> str:
        # Column expressions (allow mapping or auto-detect)
        sym = col_symbol if (use_mapped and col_symbol) else "COALESCE(symbol, product_id)"
        side = (
            col_side
            if (use_mapped and col_side)
            else "COALESCE(side, CASE WHEN COALESCE(qty_signed, amount, size, 0) < 0 THEN 'sell' ELSE 'buy' END)"
        )
        price = col_price if (use_mapped and col_price) else "COALESCE(price, fill_price, executed_price)"
        amt = col_size if (use_mapped and col_size) else "COALESCE(qty_signed, amount, size)"
        ts = col_time if (use_mapped and col_time) else "COALESCE(trade_time, order_time, ts, created_at)"
        return f"""
            SELECT {sym} AS symbol,
                   {side} AS side,
                   {price} AS price,
                   {amt} AS amount,
                   {ts} AS ts
            FROM {table_name}
            WHERE COALESCE(status, 'filled') IN ('filled','done')
              AND {ts} >= (NOW() AT TIME ZONE 'UTC' - INTERVAL '24 hours')
            ORDER BY {ts} DESC
            LIMIT 1000
        """

    recent_trades = []
    try:
        # First attempt: configured table with mapped columns
        q_tr = _trades_query(tbl_trades, use_mapped=True)
        recent_trades = conn.run(q_tr)
        detect_notes.append(
            f"Trades source: {tbl_trades} columns={{'symbol': '{col_symbol}', 'side': '{col_side}', 'price': '{col_price}', 'size': '{col_size}', 'time': '{col_time}'}}"
        )
    except Exception as e:
        errors.append(f"Trades query failed on {tbl_trades}: {e}")
        recent_trades = []

    # Auto-fallback if sparse and not already using public.trade_records
    if len(recent_trades) < 5 and tbl_trades != "public.trade_records":
        try:
            q_alt = _trades_query("public.trade_records", use_mapped=False)
            alt = conn.run(q_alt)
            if len(alt) > len(recent_trades):
                recent_trades = alt
                detect_notes.append("Trades fallback: public.trade_records (auto-detected columns)")
        except Exception as e:
            errors.append(f"Trades fallback query failed: {e}")

    return total_pnl, open_pos, recent_trades, errors, detect_notes


# =========================
# Exposure Snapshot
# =========================

def compute_exposures(open_pos, top_n: int = 3):
    """
    open_pos: iterable of rows like (symbol, qty, avg_price)
    Returns: dict with total_notional, items (top_n), all_items
    """
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
            "pct": 0.0,  # filled later
        })
    items.sort(key=lambda x: x["notional"], reverse=True)
    for it in items:
        it["pct"] = (it["notional"] / total * 100.0) if total > 0 else 0.0
    return {"total_notional": total, "items": items[:top_n], "all_items": items}


# =========================
# Email / CSV Builders
# =========================

def build_html(total_pnl, open_pos, recent_trades, errors, detect_notes, exposures=None):
    def rows(rows_):
        if not rows_:
            return "<tr><td colspan='99'>None</td></tr>"
        out = []
        for r in rows_:
            rr = []
            for c in r:
                if hasattr(c, "isoformat"):
                    rr.append(c.isoformat())
                else:
                    rr.append(c)
            out.append("<tr>" + "".join(f"<td>{c}</td>" for c in rr) + "</tr>")
        return "".join(out)

    now_utc = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    # Exposure snapshot HTML
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
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Daily Trading Bot Report"])
    w.writerow(["Generated (UTC)", datetime.utcnow().isoformat()])
    w.writerow([])
    w.writerow(["Total Realized PnL (USD)"])
    w.writerow([round(float(total_pnl or 0), 2)])
    w.writerow([])

    # Exposure Snapshot (optional)
    if exposures and exposures.get("total_notional", 0) > 0:
        w.writerow(["Exposure Snapshot"])
        w.writerow(["Total Notional (USD)", f"{exposures['total_notional']:.2f}"])
        w.writerow(["Symbol", "Notional", "% of Total", "Side", "Qty", "Avg Price"])
        for it in exposures["items"]:
            w.writerow([
                it["symbol"],
                f"{it['notional']:.2f}",
                f"{it['pct']:.1f}%",
                it["side"],
                it["qty"],
                it["price"],
            ])
        w.writerow([])

    # Positions
    w.writerow(["Open Positions"])
    w.writerow(["Symbol", "Qty", "Avg Price"])
    for r in open_pos:
        r = [c.isoformat() if hasattr(c, "isoformat") else c for c in r]
        w.writerow(r)
    w.writerow([])

    # Trades
    w.writerow(["Trades (Last 24h)"])
    w.writerow(["Symbol", "Side", "Price", "Amount", "Time"])
    for r in recent_trades:
        r = [c.isoformat() if hasattr(c, "isoformat") else c for c in r]
        w.writerow(r)

    return buf.getvalue().encode("utf-8")


# =========================
# IO & Email
# =========================

def save_report_copy(csv_bytes: bytes, out_dir="/app/logs"):
    os.makedirs(out_dir, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S_UTC")
    with open(os.path.join(out_dir, f"trading_report_{ts}.csv"), "wb") as f:
        f.write(csv_bytes)


def send_email(html, csv_bytes):
    msg = MIMEMultipart("mixed")
    msg["Subject"] = "Daily Trading Bot Report"
    msg["From"] = SENDER
    msg["To"] = ",".join(RECIPIENTS) if RECIPIENTS else SENDER

    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText("Your client does not support HTML.", "plain"))
    alt.attach(MIMEText(html, "html"))
    msg.attach(alt)

    part = MIMEApplication(csv_bytes, Name="trading_report.csv")
    part.add_header("Content-Disposition", 'attachment; filename="trading_report.csv"')
    msg.attach(part)

    ses.send_raw_email(
        Source=SENDER,
        Destinations=RECIPIENTS or [SENDER],
        RawMessage={"Data": msg.as_string().encode("utf-8")},
    )


# =========================
# Entry Point
# =========================

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

