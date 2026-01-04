#!/usr/bin/env python3

"""
AWS Daily Trading Report Emailer

Generates and delivers daily trading performance reports via email.
Queries windowed trade/position data, calculates metrics, builds HTML/CSV,
and sends via AWS SES.

Scheduling: Managed externally (cron runs every 6 hours)

================================================================================
TABLE OF CONTENTS
================================================================================

SECTION 1: Configuration & Imports .......................... Lines 30-100
    - Environment variables
    - Constants and configuration
    - Library imports

SECTION 2: Database Connections ............................. Lines 101-200
    - Connection factory (get_db_conn)
    - SSL context handling
    - SQLAlchemy engine setup
    - SSM parameter retrieval

SECTION 3: Schema Helpers & Query Builders .................. Lines 201-580
    - Table/column detection (table_columns, pick_first_available)
    - SQL identifiers (qident, qualify)
    - Time window SQL generation (_time_window_sql)

SECTION 4: Core Queries ..................................... Lines 581-680
    - run_queries() - Main query orchestrator
    - Position calculation (windowed)
    - PnL calculation (windowed)
    - Trade fetching

SECTION 5: Metric Calculations .............................. Lines 681-900
    - compute_exposures() - Position concentration
    - compute_unrealized_pnl() - Mark-to-market
    - compute_win_rate() - Win/loss statistics
    - compute_trade_stats_windowed() - Avg win/loss, profit factor
    - compute_max_drawdown() - Peak-to-trough analysis
    - compute_cash_vs_invested() - Capital allocation

SECTION 6: Fast Roundtrip Analysis .......................... Lines 901-930
    - build_fast_rt_sql() - SQL for ≤60s trades
    - fetch_fast_roundtrips() - Execution and CSV export

SECTION 7: HTML Generation .................................. Lines 931-1060
    - build_html() - Main HTML builder
    - render_score_section_html() - Signal scores
    - render_fees_section_html() - Break-even thresholds

SECTION 8: CSV & File I/O ................................... Lines 1061-1210
    - build_csv() - CSV export
    - save_report_copy() - Local file storage

SECTION 9: Email Delivery ................................... Lines 1211-1220
    - send_email() - SES wrapper

SECTION 10: Main Entry Point ................................ Lines 1221-END
    - main() - Orchestrates entire report flow
    - Error handling and output routing

================================================================================
QUICK NAVIGATION TIPS
================================================================================

To find specific functionality:
    - Database connection issues? → Section 2
    - Query errors? → Section 4
    - Position calculation? → Section 4, line ~599
    - Metric wrong? → Section 5
    - Email formatting? → Section 7
    - CSV export? → Section 8

Use Ctrl+G (Windows/Linux) or Cmd+L (Mac) to jump to line number.
Use Ctrl+F12 to see structure outline.

================================================================================
"""
# ============================================================================
# SECTION 1: Configuration & Imports
# ============================================================================
# Environment variables, constants, and library imports

import os
import re
import io
import csv
import ssl
import pandas as pd
import numpy as np
import pg8000.native as pg

from pathlib import Path
from html import unescape
from decimal import Decimal
from email.utils import getaddresses
from statistics import mean, pstdev
from datetime import datetime, timezone, timedelta
from sqlalchemy import text, create_engine
from urllib.parse import urlparse, parse_qs
from Config.config_manager import CentralConfig as Config
from collections import defaultdict, Counter
from typing import Optional, List, Dict, Tuple
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from botreport.metrics_compute import (load_score_jsonl, score_snapshot_metrics_from_jsonl, render_score_section_jsonl,
                              load_tpsl_jsonl, aggregate_tpsl, render_tpsl_section, render_tpsl_suggestions)
from botreport.analysis_symbol_performance import (
    compute_symbol_performance,
    render_symbol_performance_html,
    generate_symbol_suggestions
)
from botreport.emailer import send_email as send_email_via_ses  # uses lazy boto3
from botreport.email_report_print_format import build_console_report

# Structured logging
from Shared_Utils.logger import get_logger

# ============================================================================
# NEW: Import from Config package
# ============================================================================
from Config.environment import Environment as EnvConfig
from Config.constants_core import (
    POSITION_DUST_THRESHOLD,
    FAST_ROUNDTRIP_MAX_SECONDS,
    TRADE_QUERY_HARD_LIMIT,
    ABSOLUTE_MAX_LOOKBACK_HOURS,
    DEFAULT_TOP_POSITIONS as CORE_DEFAULT_TOP_POSITIONS,
)
from Config.constants_report import (
    DEFAULT_TOP_POSITIONS,
    DEFAULT_LOOKBACK_HOURS,
    MAX_LOOKBACK_HOURS,
    MIN_LOOKBACK_HOURS,
    REPORT_PNL_TABLE,
    REPORT_EXECUTIONS_TABLE,
    REPORT_PRICE_TABLE,
    REPORT_POSITIONS_TABLE,
    REPORT_TRADES_TABLE,
    REPORT_WINRATE_TABLE,
    REPORT_BALANCES_TABLE,
    REPORT_COL_SYMBOL,
    REPORT_COL_SIDE,
    REPORT_COL_PRICE,
    REPORT_COL_SIZE,
    REPORT_COL_TIME,
    REPORT_COL_POS_QTY,
    REPORT_COL_PNL,
    REPORT_PRICE_COL,
    REPORT_PRICE_TIME_COL,
    REPORT_PRICE_SYM_COL,
    REPORT_CASH_SYM_COL,
    REPORT_CASH_AMT_COL,
    REPORT_CASH_SYMBOLS,
    REPORT_SHOW_DETAILS,
    REPORT_DEBUG,
    REPORT_USE_PT_DAY,
    STARTING_EQUITY_USD,
    TAKER_FEE,
    USE_FIFO_ALLOCATIONS,
    FIFO_ALLOCATION_VERSION,
    FIFO_ALLOCATIONS_TABLE,
    FIFO_HEALTH_VIEW,
    MAKER_FEE,
)

# ============================================================================
# Environment-aware configuration
# ============================================================================

# Keep CentralConfig for backward compatibility if needed
try:
    config = Config()
except:
    config = None
try:
    env_config = EnvConfig()
except:
    env_config = None

# Use new Config system
IN_DOCKER = env_config.is_docker
REGION = os.getenv('AWS_REGION', 'us-west-2')
SENDER = os.getenv('REPORT_SENDER', '').strip()
RECIPIENTS = [os.getenv('REPORT_RECIPIENTS', SENDER)]

# Use environment-aware paths
SCORE_JSONL_PATH = str(env_config.score_jsonl_path)


try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None

def _html_to_text(s: str) -> str:
    if not s:
        return ""
    # super basic fallback: strip tags + collapse whitespace
    s = unescape(re.sub(r"<[^>]+>", " ", s))
    return re.sub(r"\s+", " ", s).strip()


def load_report_dotenv():
    """
    Load environment configuration.

    Now handled by Config.environment module automatically.
    This function kept for backward compatibility.
    """
    # Config.environment.env already loaded the appropriate .env file
    # Nothing to do here, but keep function for compatibility
    pass
# Ensure env is loaded BEFORE we snapshot env variables below.
load_report_dotenv()

if not SENDER or not RECIPIENTS:
    raise ValueError(f"Bad email config. REPORT_SENDER={SENDER!r}, REPORT_RECIPIENTS={os.getenv('REPORT_RECIPIENTS')!r}")

# ============================================================================
# Report Feature Flags
# ============================================================================
REPORT_INCLUDE_SYMBOL_PERFORMANCE = os.getenv('REPORT_INCLUDE_SYMBOL_PERFORMANCE', 'true').lower() == 'true'

# Initialize structured logger for botreport
logger = get_logger('botreport', context={'component': 'daily_report'})

ssm = None
ses = None

def _get_ssm():
    global ssm
    if ssm is None:
        try:
            import boto3
        except Exception as e:
            raise RuntimeError("SSM requested but boto3 is not available") from e
        ssm = boto3.client("ssm", region_name=REGION)
    return ssm

def _get_ses():
    global ses
    if ses is None:
        try:
            import boto3
        except Exception as e:
            raise RuntimeError("SES requested but boto3 is not available") from e
        ses = boto3.client("ses", region_name=REGION)
    return ses

# ============================================================================
# SECTION 2: Database Connections
# ============================================================================
# Connection factory, SSL handling, SSM parameter retrieval

def get_param(name: str) -> str:
    return _get_ssm().get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]

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

        def _log_conn_plan(source, _host, _port, _user, _name, _ssl):
            logger.info(
                "Database connection initialized",
                extra={
                    'source': source,
                    'host': _host,
                    'port': _port,
                    'user': _user,
                    'database': _name,
                    'ssl': 'on' if _ssl else 'off'
                }
            )

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

# ============================================================================
# SECTION 3: Schema Helpers & Query Builders
# ============================================================================
# Table/column detection, SQL identifiers, time window generation

def split_schema_table(qualified: str):
    q = qualified.strip().strip('"')
    if "." in q:
        s, t = q.split(".", 1)
        return s.strip('"') or "public", t.strip('"')
    return "public", q

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
        time_window_sql = f"{ts_expr} >= (NOW() AT TIME ZONE 'UTC' - INTERVAL '{DEFAULT_LOOKBACK_HOURS} hours')"

    upper_bound_sql = f"AND {ts_expr} < (NOW() AT TIME ZONE 'UTC')"
    return time_window_sql, upper_bound_sql


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


# ============================================================================
# FIFO Allocations Helpers
# ============================================================================

def query_fifo_pnl(conn, version: int):
    """
    Query total PnL from FIFO allocations table for a given version.

    Returns:
        (total_pnl, note) - Total PnL and descriptive note
    """
    try:
        # Check if FIFO tables exist
        cols = table_columns(conn, FIFO_ALLOCATIONS_TABLE)
        if not cols:
            return 0, f"FIFO table {FIFO_ALLOCATIONS_TABLE} not found"

        # Get time window
        ts_col = 'sell_time'  # FIFO allocations use sell_time
        if ts_col in cols:
            low_sql, high_sql = _time_window_sql(qident(ts_col))
            q = f"""
                SELECT COALESCE(SUM(pnl_usd), 0)
                FROM {qualify(FIFO_ALLOCATIONS_TABLE)}
                WHERE allocation_version = {version}
                  AND {low_sql} {high_sql}
            """
            note = f"FIFO v{version} PnL (windowed by {ts_col})"
        else:
            q = f"""
                SELECT COALESCE(SUM(pnl_usd), 0)
                FROM {qualify(FIFO_ALLOCATIONS_TABLE)}
                WHERE allocation_version = {version}
            """
            note = f"FIFO v{version} PnL (all-time, no time window)"

        total_pnl = conn.run(q)[0][0]
        return total_pnl, note

    except Exception as e:
        return 0, f"FIFO PnL query failed: {e}"


def query_fifo_health(conn, version: int):
    """
    Query FIFO allocation health metrics for a given version.

    Returns:
        dict with health metrics or None if unavailable
    """
    try:
        # Check if health view exists
        q = f"""
            SELECT
                total_allocations,
                sells_matched,
                buys_used,
                unmatched_sells,
                total_pnl
            FROM {qualify(FIFO_HEALTH_VIEW)}
            WHERE allocation_version = {version}
        """
        rows = conn.run(q)
        if not rows:
            return None

        row = rows[0]
        return {
            'version': version,
            'total_allocations': row[0],
            'sells_matched': row[1],
            'buys_used': row[2],
            'unmatched_sells': row[3],
            'total_pnl': row[4],
        }

    except Exception as e:
        return None


def query_trigger_breakdown(conn):
    """
    Query PnL and order count grouped by trigger type.

    Infers trigger type based on buy order size since trigger field is not reliably populated:
    - ~$15 (13-17) → Signal Matrix (technical indicators)
    - ~$20 (18-23) → ROC Momentum
    - ~$25 (24-30) → Webhook/External
    - ~$32 (31-34) → Passive Market Making
    - Other sizes → Websocket (manual/external)

    Returns:
        list of dicts: [{'trigger': str, 'order_count': int, 'total_pnl': float, 'win_count': int, 'loss_count': int}, ...]
    """
    try:
        # Check if required columns exist
        cols = table_columns(conn, REPORT_TRADES_TABLE)
        if 'trigger' not in cols:
            return []

        # Get time column for windowing
        ts_col = pick_first_available(cols, ["order_time", "trade_time", "filled_at", "ts", "timestamp", "created_at"])
        if not ts_col:
            return []

        time_window_sql, upper_bound_sql = _time_window_sql(qident(ts_col))

        # Query trigger breakdown using FIFO allocations and inferring trigger from buy order size
        q = f"""
            WITH buy_orders AS (
                -- Get buy orders with notional value for trigger inference
                SELECT
                    order_id,
                    (size * price) AS notional_usd,
                    CASE
                        -- TP/SL exits (these are legitimate trigger values)
                        WHEN UPPER(COALESCE(trigger->>'trigger', TRIM(BOTH '"' FROM trigger::text)))
                             IN ('TAKE_PROFIT_STOP_LOSS', 'STOP_TRIGGERED', 'BRACKET')
                        THEN 'TP/SL Exit'

                        -- Infer from order size based on ORDER_SIZE_* configuration
                        WHEN (size * price) BETWEEN 13 AND 17 THEN 'Signal Matrix'
                        WHEN (size * price) BETWEEN 18 AND 23 THEN 'ROC Momentum'
                        WHEN (size * price) BETWEEN 24 AND 30 THEN 'Webhook'
                        WHEN (size * price) BETWEEN 31 AND 34 THEN 'Passive MM'

                        -- Fallback for other sizes
                        ELSE 'Websocket'
                    END AS inferred_trigger
                FROM {qualify(REPORT_TRADES_TABLE)}
                WHERE side = 'buy'
                  AND status IN ('filled', 'done')
                  AND {time_window_sql}
                  {upper_bound_sql}
            ),
            trigger_pnl AS (
                SELECT
                    COALESCE(b.inferred_trigger, 'Unknown') AS trigger_type,
                    s.order_id,
                    COALESCE(SUM(fa.pnl_usd), 0) AS pnl
                FROM {qualify(REPORT_TRADES_TABLE)} s
                LEFT JOIN fifo_allocations fa
                    ON fa.sell_order_id = s.order_id
                    AND fa.allocation_version = 2
                LEFT JOIN buy_orders b
                    ON fa.buy_order_id = b.order_id
                WHERE s.side = 'sell'
                  AND s.status IN ('filled', 'done')
                  AND {time_window_sql}
                  {upper_bound_sql}
                GROUP BY trigger_type, s.order_id
            )
            SELECT
                trigger_type,
                COUNT(*) AS order_count,
                COALESCE(SUM(pnl), 0) AS total_pnl,
                COUNT(*) FILTER (WHERE pnl > 0) AS win_count,
                COUNT(*) FILTER (WHERE pnl < 0) AS loss_count,
                COUNT(*) FILTER (WHERE pnl = 0) AS breakeven_count
            FROM trigger_pnl
            GROUP BY trigger_type
            ORDER BY total_pnl DESC
        """

        rows = conn.run(q)
        results = []
        for row in rows:
            results.append({
                'trigger': row[0],
                'order_count': row[1],
                'total_pnl': float(row[2]),
                'win_count': row[3],
                'loss_count': row[4],
                'breakeven_count': row[5]
            })
        return results

    except Exception as e:
        return []


def query_source_statistics(conn):
    """
    Query order count and PnL grouped by source (webhook, sighook, manual, etc).

    Returns:
        list of dicts: [{'source': str, 'buy_count': int, 'sell_count': int, 'total_pnl': float}, ...]
    """
    try:
        cols = table_columns(conn, REPORT_TRADES_TABLE)
        if 'source' not in cols:
            return []

        ts_col = pick_first_available(cols, ["order_time", "trade_time", "filled_at", "ts", "timestamp", "created_at"])
        if not ts_col:
            return []

        time_window_sql, upper_bound_sql = _time_window_sql(qident(ts_col))

        q = f"""
            SELECT
                COALESCE(tr.source, 'unknown') AS source_type,
                COUNT(*) FILTER (WHERE tr.side = 'buy') AS buy_count,
                COUNT(*) FILTER (WHERE tr.side = 'sell') AS sell_count,
                COALESCE(SUM(fa.pnl_usd) FILTER (WHERE tr.side = 'sell'), 0) AS total_pnl
            FROM {qualify(REPORT_TRADES_TABLE)} tr
            LEFT JOIN fifo_allocations fa
                ON fa.sell_order_id = tr.order_id
                AND fa.allocation_version = 2
            WHERE {time_window_sql}
              {upper_bound_sql}
              AND tr.status IN ('filled', 'done')
            GROUP BY source_type
            ORDER BY (buy_count + sell_count) DESC
        """

        rows = conn.run(q)
        results = []
        for row in rows:
            results.append({
                'source': row[0],
                'buy_count': row[1],
                'sell_count': row[2],
                'total_pnl': float(row[3])
            })
        return results

    except Exception as e:
        return []


def query_hodl_positions(conn):
    """
    Query current positions for HODL assets (ETH, ATOM) with unrealized P&L.

    Returns:
        list of dicts: [{'symbol': str, 'qty': float, 'avg_price': float, 'current_value': float}, ...]
    """
    try:
        # Get HODL assets from environment
        hodl_str = os.getenv('HODL', 'ETH,ATOM')
        hodl_assets = [asset.strip() for asset in hodl_str.split(',') if asset.strip()]

        if not hodl_assets:
            return []

        # Build symbol list (e.g., ETH-USD, ATOM-USD)
        hodl_symbols = [f"{asset}-USD" for asset in hodl_assets]
        symbols_quoted = ', '.join([f"'{sym}'" for sym in hodl_symbols])

        cols = table_columns(conn, REPORT_TRADES_TABLE)
        ts_col = pick_first_available(cols, ["order_time", "trade_time", "filled_at", "ts", "timestamp", "created_at"])
        if not ts_col:
            return []

        # Calculate positions from all-time trades (not windowed - HODL is long-term)
        has_qty_signed = 'qty_signed' in cols

        if has_qty_signed:
            q = f"""
                SELECT
                    symbol,
                    SUM(qty_signed) AS qty,
                    AVG(price) AS avg_price
                FROM {qualify(REPORT_TRADES_TABLE)}
                WHERE symbol IN ({symbols_quoted})
                  AND status IN ('filled', 'done')
                GROUP BY symbol
                HAVING ABS(SUM(qty_signed)) > 0.0001
                ORDER BY symbol
            """
        else:
            q = f"""
                SELECT
                    symbol,
                    SUM(CASE
                        WHEN LOWER(side::text) = 'buy' THEN size
                        WHEN LOWER(side::text) = 'sell' THEN -size
                        ELSE 0
                    END) AS qty,
                    AVG(price) AS avg_price
                FROM {qualify(REPORT_TRADES_TABLE)}
                WHERE symbol IN ({symbols_quoted})
                  AND status IN ('filled', 'done')
                GROUP BY symbol
                HAVING ABS(SUM(CASE
                    WHEN LOWER(side::text) = 'buy' THEN size
                    ELSE -size
                END)) > 0.0001
                ORDER BY symbol
            """

        rows = conn.run(q)
        results = []
        for row in rows:
            symbol = row[0]
            qty = float(row[1])
            avg_price = float(row[2])
            current_value = abs(qty) * avg_price  # Will get updated with current price later

            results.append({
                'symbol': symbol,
                'qty': qty,
                'avg_price': avg_price,
                'cost_basis': abs(qty) * avg_price,
                'current_value': current_value,  # Placeholder, will be updated with live price
                'unrealized_pnl': 0.0  # Will be calculated with live price
            })

        return results

    except Exception as e:
        return []


# ============================================================================
# SECTION 4: Core Queries
# ============================================================================
# Main query orchestrator, position/PnL/trade fetching

def run_queries(conn):
    errors = []
    detect_notes = ["Build:v11"]  # Increment version
    fifo_health = None  # Store FIFO health metrics for HTML report

    # Realized PnL (windowed if timestamp exists)
    try:
        if USE_FIFO_ALLOCATIONS:
            # Use FIFO allocations for PnL
            total_pnl, fifo_note = query_fifo_pnl(conn, FIFO_ALLOCATION_VERSION)
            detect_notes.append(fifo_note)

            # Query FIFO health metrics
            fifo_health = query_fifo_health(conn, FIFO_ALLOCATION_VERSION)
            if fifo_health:
                detect_notes.append(
                    f"FIFO health: {fifo_health['total_allocations']} allocations, "
                    f"{fifo_health['unmatched_sells']} unmatched sells"
                )
        else:
            # Use existing trade_records.pnl_usd logic
            tbl_pnl = REPORT_PNL_TABLE
            cols_present = table_columns(conn, tbl_pnl)
            if REPORT_DEBUG:
                detect_notes.append(f"Columns({tbl_pnl}): {sorted(cols_present)}")

            pnl_candidates = ["pnl_usd", "realized_pnl_usd", "realized_pnl", "pnl", "profit", "realized_profit"]
            pnl_col = REPORT_COL_PNL if REPORT_COL_PNL else pick_first_available(cols_present, pnl_candidates)

            if not pnl_col:
                total_pnl = 0
                detect_notes.append(f"No pnl-like column found on {tbl_pnl}")
            else:
                ts_candidates = ["order_time", "exec_time", "time", "ts", "timestamp", "created_at", "updated_at"]
                ts_col = pick_first_available(cols_present, ["order_time", "trade_time", "ts", "timestamp", "created_at", "updated_at"])

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

    # Positions - FIXED: Calculate from windowed trades, not cumulative view
    open_pos = []
    try:
        tbl_trades = REPORT_TRADES_TABLE
        cols_trades = table_columns(conn, tbl_trades)

        if REPORT_DEBUG:
            detect_notes.append(f"Columns({tbl_trades}): {sorted(cols_trades)}")

        if not cols_trades:
            raise RuntimeError(f"Table not found: {tbl_trades}")

        # Verify required columns exist
        if 'symbol' not in cols_trades:
            raise RuntimeError(f"Missing 'symbol' column in {tbl_trades}")
        ts_col = pick_first_available(cols_trades, ["order_time", "trade_time", "filled_at", "ts", "timestamp", "created_at"])
        if not ts_col:
            raise RuntimeError(f"Missing time column in {tbl_trades}. Tried: order_time, trade_time, filled_at, ts, timestamp, created_at")

        has_qty_signed = 'qty_signed' in cols_trades

        # Build time window filter
        time_window_sql, upper_bound_sql = _time_window_sql(ts_col)

        if has_qty_signed:
            # qty_signed already has the sign (positive for buy, negative for sell)
            q = f"""
                    SELECT 
                        symbol,
                        SUM(qty_signed) AS qty,
                        AVG(price) AS avg_price
                    FROM {qualify(tbl_trades)}
                    WHERE {time_window_sql}
                      {upper_bound_sql}
                    GROUP BY symbol
                    HAVING ABS(SUM(qty_signed)) > 0.0001  -- POSITION_DUST_THRESHOLD (Python: {POSITION_DUST_THRESHOLD})
                    ORDER BY symbol
                """
            detect_notes.append(f"Positions: WINDOWED from {tbl_trades} using qty_signed (pre-signed)")
        else:
            # Fallback: use size + side columns
            q = f"""
                    SELECT 
                        symbol,
                        SUM(CASE 
                            WHEN LOWER(side::text) = 'buy' THEN size 
                            WHEN LOWER(side::text) = 'sell' THEN -size
                            ELSE 0
                        END) AS qty,
                        AVG(price) AS avg_price
                    FROM {qualify(tbl_trades)}
                    WHERE {time_window_sql}
                      {upper_bound_sql}
                      AND status = 'filled'
                    GROUP BY symbol
                    HAVING ABS(SUM(CASE 
                        WHEN LOWER(side::text) = 'buy' THEN size 
                        ELSE -size 
                    END)) > 0.0001  -- POSITION_DUST_THRESHOLD (Python: {POSITION_DUST_THRESHOLD})
                    ORDER BY symbol
                """
            detect_notes.append(f"Positions: WINDOWED from {tbl_trades} using size+side")

        open_pos = conn.run(q)
        detect_notes.append(f"Positions calculated: {len(open_pos)} symbols with non-zero qty")

    except Exception as e1:
        errors.append(f"Positions query failed: {e1}")

    # Trades (window)
    def run_trades_for(table_name: str, use_mappings: bool = True):
        cols_tr = table_columns(conn, table_name)
        if REPORT_DEBUG:
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

        _ts_col = REPORT_COL_TIME if (use_mappings and REPORT_COL_TIME in cols_tr) else \
                 pick_first_available(cols_tr, ["trade_time","filled_at","completed_at","order_time","ts","created_at","executed_at"])
        if not _ts_col:
            raise RuntimeError(f"No time-like column on {table_name}")
        ts_expr = qident(_ts_col)

        time_window_sql, upper_bound_sql = _time_window_sql(ts_expr)

        status_filter = ""
        if "status" in cols_tr:
            status_filter = "AND COALESCE(status,'filled') IN ('filled','done')"

        _q = f"""
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
        return conn.run(_q)

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

    # Query trigger breakdown
    trigger_breakdown = []
    try:
        trigger_breakdown = query_trigger_breakdown(conn)
        if trigger_breakdown:
            detect_notes.append(f"Trigger breakdown: {len(trigger_breakdown)} trigger types")
    except Exception as e:
        errors.append(f"Trigger breakdown query failed: {e}")

    # Query source statistics
    source_stats = []
    try:
        source_stats = query_source_statistics(conn)
        if source_stats:
            detect_notes.append(f"Source statistics: {len(source_stats)} sources")
    except Exception as e:
        errors.append(f"Source statistics query failed: {e}")

    # Query HODL positions
    hodl_positions = []
    try:
        hodl_positions = query_hodl_positions(conn)
        if hodl_positions:
            detect_notes.append(f"HODL positions: {len(hodl_positions)} assets")
    except Exception as e:
        errors.append(f"HODL positions query failed: {e}")

    return total_pnl, open_pos, recent_trades, errors, detect_notes, fifo_health, trigger_breakdown, source_stats, hodl_positions

# ============================================================================
# SECTION 5: Metric Calculations
# ============================================================================
# Exposure, unrealized PnL, win rate, drawdown, capital allocation

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


# ============================================================================
# Section 4: Metric Calculations & Prices
# ============================================================================


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

def compute_fee_break_even():
    """
    Returns a dict with approximate round-trip break-even moves for taker vs maker,
    using env TAKER_FEE / MAKER_FEE (already loaded as Decimals).
    """
    taker = float(TAKER_FEE or 0.0)
    maker = float(MAKER_FEE or 0.0)
    # round-trip (enter+exit). This is a simple, readable approximation.
    rt_taker = (taker * 2.0) * 100.0
    rt_maker = (maker * 2.0) * 100.0
    return {
        "rt_taker_pct": rt_taker,   # e.g. 1.10 for 0.55% taker fee
        "rt_maker_pct": rt_maker,   # e.g. 0.60 for 0.30% maker fee
    }

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
    """
    Compute win rate using FIFO allocations table.
    Returns: (win_rate_pct, wins_count, decisive_trades_count, notes)
    Note: Win rate is calculated as wins / (wins + losses), excluding break-even trades
    """
    notes = []
    tbl = REPORT_WINRATE_TABLE or REPORT_TRADES_TABLE
    cols = table_columns(conn, tbl)
    if not cols:
        return 0.0, 0, 0, [f"WinRate: table not found: {tbl}"]

    # Check if we have required columns for FIFO join
    ts_col = pick_first_available(cols, ["trade_time","filled_at","completed_at","order_time","ts","created_at","executed_at"])
    if not ts_col:
        return 0.0, 0, 0, [f"WinRate: no time-like column on {tbl}"]

    # Verify side column exists
    if 'side' not in cols:
        return 0.0, 0, 0, [f"WinRate: 'side' column not found on {tbl}"]

    ts_expr = qident(ts_col)
    time_window_sql, upper_bound_sql = _time_window_sql(ts_expr)

    # Use FIFO allocations for P&L calculation
    q = f"""
        WITH trade_pnl AS (
            SELECT
                tr.order_id,
                COALESCE(SUM(fa.pnl_usd), 0) AS pnl
            FROM {qualify(tbl)} tr
            LEFT JOIN fifo_allocations fa
                ON fa.sell_order_id = tr.order_id
                AND fa.allocation_version = {FIFO_ALLOCATION_VERSION}
            WHERE {time_window_sql}
              {upper_bound_sql}
              AND tr.side = 'sell'
              AND tr.status IN ('filled', 'done')
            GROUP BY tr.order_id
        )
        SELECT
            COUNT(*) FILTER (WHERE pnl > 0) AS wins,
            COUNT(*) FILTER (WHERE pnl < 0) AS losses,
            COUNT(*) AS total
        FROM trade_pnl
    """
    wins, losses, total = conn.run(q)[0]
    total = int(total or 0)
    wins = int(wins or 0)
    losses = int(losses or 0)

    # Calculate win rate excluding break-even trades
    decisive_trades = wins + losses
    win_rate = (wins / decisive_trades * 100.0) if decisive_trades > 0 else 0.0

    breakeven_count = total - decisive_trades
    notes.append(f"WinRate source: {tbl} using FIFO v{FIFO_ALLOCATION_VERSION} ts_col={ts_col} (denominator excludes {breakeven_count} breakeven trades)")
    return win_rate, wins, decisive_trades, notes

def compute_trade_stats_windowed(conn):
    """
    Compute trade statistics using FIFO allocations table.
    Returns: (avg_win, avg_loss_neg, profit_factor, notes)
    """
    notes = []
    tbl = REPORT_WINRATE_TABLE or REPORT_TRADES_TABLE
    cols = table_columns(conn, tbl)
    if not cols:
        return 0.0, 0.0, None, [f"TradeStats: table not found: {tbl}"]

    # Check if we have required columns for FIFO join
    ts_col = pick_first_available(cols, ["trade_time","filled_at","completed_at","order_time","ts","created_at","executed_at"])
    if not ts_col:
        return 0.0, 0.0, None, [f"TradeStats: no time-like column on {tbl}"]

    # Verify side column exists
    if 'side' not in cols:
        return 0.0, 0.0, None, [f"TradeStats: 'side' column not found on {tbl}"]

    ts_expr = qident(ts_col)
    time_window_sql, upper_bound_sql = _time_window_sql(ts_expr)

    # Use FIFO allocations for P&L calculation
    # NOTE: No ROUND() with double+int; compute raw aggregates and format at the edge
    q = f"""
        WITH trade_pnl AS (
            SELECT
                tr.order_id,
                COALESCE(SUM(fa.pnl_usd), 0) AS pnl
            FROM {qualify(tbl)} tr
            LEFT JOIN fifo_allocations fa
                ON fa.sell_order_id = tr.order_id
                AND fa.allocation_version = {FIFO_ALLOCATION_VERSION}
            WHERE {time_window_sql}
              {upper_bound_sql}
              AND tr.side = 'sell'
              AND tr.status IN ('filled', 'done')
            GROUP BY tr.order_id
        )
        SELECT
          AVG(CASE WHEN pnl > 0 THEN pnl END) AS avg_win,
          AVG(CASE WHEN pnl < 0 THEN pnl END) AS avg_loss_neg,
          SUM(CASE WHEN pnl > 0 THEN pnl ELSE 0 END) AS gross_profit,
          SUM(CASE WHEN pnl < 0 THEN -pnl ELSE 0 END) AS gross_loss_abs
        FROM trade_pnl
    """
    avg_win, avg_loss_neg, gross_profit, gross_loss_abs = conn.run(q)[0]
    avg_win = float(avg_win or 0.0)
    avg_loss_neg = float(avg_loss_neg or 0.0)
    pf = None
    try:
        pf = (float(gross_profit or 0.0) / float(gross_loss_abs or 0.0)) if float(gross_loss_abs or 0.0) > 0 else None
    except Exception:
        pf = None

    notes.append(f"TradeStats source: {tbl} using FIFO v{FIFO_ALLOCATION_VERSION} ts_col={ts_col}")
    return avg_win, avg_loss_neg, pf, notes

def compute_strategy_linkage_stats(conn):
    """
    Compute strategy linkage statistics for trade-strategy optimization.
    Returns: dict with linkage_rate, linked_count, total_count, missing_trades, sample_trades
    """
    try:
        tbl = REPORT_TRADES_TABLE
        cols = table_columns(conn, tbl)
        if not cols:
            return {"error": f"Table not found: {tbl}"}

        ts_col = pick_first_available(cols, ["order_time", "trade_time", "filled_at", "completed_at"])
        if not ts_col:
            return {"error": f"No time column found on {tbl}"}

        ts_expr = qident(ts_col)
        time_window_sql, upper_bound_sql = _time_window_sql(ts_expr)

        # Check if trade_strategy_link table exists
        link_tbl_check = conn.run("SELECT to_regclass('trade_strategy_link')")
        if not link_tbl_check or not link_tbl_check[0][0]:
            return {"error": "trade_strategy_link table not found - linkage not enabled"}

        # Query for linkage stats
        q = f"""
            SELECT
                COUNT(*) AS total_trades,
                COUNT(tsl.order_id) AS linked_trades,
                COUNT(*) FILTER (WHERE tsl.order_id IS NULL) AS missing_linkage
            FROM {qualify(tbl)} tr
            LEFT JOIN trade_strategy_link tsl ON tr.order_id = tsl.order_id
            WHERE {time_window_sql}
              {upper_bound_sql}
              AND tr.status IN ('filled', 'done')
        """
        total, linked, missing = conn.run(q)[0]
        total = int(total or 0)
        linked = int(linked or 0)
        missing = int(missing or 0)

        linkage_rate = (linked / total * 100) if total > 0 else 0.0

        # Get sample of linked trades (last 5)
        sample_q = f"""
            SELECT
                tr.symbol,
                tr.side,
                tr.{ts_expr},
                tsl.buy_score,
                tsl.sell_score,
                tsl.trigger_type,
                SUBSTRING(tsl.snapshot_id::text, 1, 8) AS snapshot_id_short
            FROM {qualify(tbl)} tr
            INNER JOIN trade_strategy_link tsl ON tr.order_id = tsl.order_id
            WHERE {time_window_sql}
              {upper_bound_sql}
              AND tr.status IN ('filled', 'done')
            ORDER BY tr.{ts_expr} DESC
            LIMIT 5
        """
        sample_trades = conn.run(sample_q)

        # Get missing trades (for debugging)
        missing_q = f"""
            SELECT
                tr.symbol,
                tr.side,
                tr.{ts_expr},
                tr.order_id
            FROM {qualify(tbl)} tr
            LEFT JOIN trade_strategy_link tsl ON tr.order_id = tsl.order_id
            WHERE {time_window_sql}
              {upper_bound_sql}
              AND tr.status IN ('filled', 'done')
              AND tsl.order_id IS NULL
            ORDER BY tr.{ts_expr} DESC
            LIMIT 10
        """
        missing_trades = conn.run(missing_q)

        return {
            "total_count": total,
            "linked_count": linked,
            "missing_count": missing,
            "linkage_rate": linkage_rate,
            "sample_trades": sample_trades,
            "missing_trades": missing_trades
        }

    except Exception as e:
        return {"error": f"Error computing linkage stats: {e}"}

def compute_max_drawdown(conn):
    """
    Compute max drawdown using FIFO allocations + starting cash balance.
    Returns: (dd_pct, dd_abs, peak_equity, trough_equity, notes)
    """
    notes = []
    tbl = REPORT_PNL_TABLE
    cols = table_columns(conn, tbl)
    if not cols:
        return 0.0, 0.0, 0.0, 0.0, [f"Drawdown: table not found: {tbl}"]

    # Check if we have required columns for FIFO join
    ts_col = pick_first_available(cols, ["trade_time","filled_at","completed_at","order_time","ts","created_at","executed_at"])
    if not ts_col:
        return 0.0, 0.0, 0.0, 0.0, [f"Drawdown: no time-like column on {tbl}"]

    # Verify side column exists
    if 'side' not in cols:
        return 0.0, 0.0, 0.0, 0.0, [f"Drawdown: 'side' column not found on {tbl}"]

    # Get starting cash balance from cash_transactions
    starting_cash_query = f"""
        SELECT COALESCE(SUM(
            CASE
                WHEN normalized_type = 'deposit' THEN amount_usd
                WHEN normalized_type = 'withdrawal' THEN -amount_usd
                ELSE 0
            END
        ), 0) as starting_cash
        FROM public.cash_transactions
        WHERE transaction_date <= (
            SELECT MIN({qident(ts_col)})
            FROM {qualify(tbl)}
            WHERE side = 'sell' AND status IN ('filled', 'done')
        )
    """

    try:
        starting_cash_result = conn.run(starting_cash_query)[0]
        starting_cash = float(starting_cash_result[0] if starting_cash_result else 0.0)
    except Exception as e:
        notes.append(f"Drawdown: error querying cash_transactions: {e}")
        starting_cash = 0.0

    # If no cash transactions or no trades yet, use fallback
    if starting_cash == 0:
        try:
            starting_cash = float(os.getenv("STARTING_EQUITY_USD", "1906.54"))
        except Exception:
            starting_cash = 1906.54

    # Build equity curve: starting_cash + cumulative_pnl
    q = f"""
        WITH trade_pnl AS (
          SELECT
              tr.{qident(ts_col)}::timestamptz AS ts,
              tr.order_id,
              COALESCE(SUM(fa.pnl_usd), 0) AS pnl
          FROM {qualify(tbl)} tr
          LEFT JOIN fifo_allocations fa
              ON fa.sell_order_id = tr.order_id
              AND fa.allocation_version = {FIFO_ALLOCATION_VERSION}
          WHERE tr.side = 'sell'
            AND tr.status IN ('filled', 'done')
          GROUP BY tr.{qident(ts_col)}, tr.order_id
        ),
        t AS (
          SELECT ts, pnl::numeric AS pnl
          FROM trade_pnl
        ),
        c AS (  -- equity curve with starting cash
          SELECT ts,
                 {starting_cash} + SUM(pnl) OVER (ORDER BY ts ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS equity
          FROM t
        ),
        d AS (  -- running peak and drawdowns
          SELECT ts, equity,
                 MAX(equity) OVER (ORDER BY ts ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS run_max
          FROM c
        )
        SELECT
          MIN( (equity - run_max) / NULLIF(run_max, 0.0) ) AS min_frac,
          MIN( equity - run_max )                          AS min_abs,
          MAX(run_max)                                     AS peak_eq,
          MIN(equity)                                      AS trough_eq
        FROM d
    """
    min_frac, min_abs, peak_eq, trough_eq = conn.run(q)[0]

    if min_frac is None or peak_eq in (None, 0):
        dd_pct = 0.0
    else:
        dd_pct = abs(float(min_frac) * 100.0)

    dd_abs = abs(float(min_abs or 0.0))
    notes.append(
        f"Drawdown source: {tbl} using FIFO v{FIFO_ALLOCATION_VERSION} ts_col={ts_col} "
        f"starting_cash=${starting_cash:.2f}"
    )
    return dd_pct, dd_abs, float(peak_eq or 0.0), float(trough_eq or 0.0), notes


def compute_cash_vs_invested(conn, exposures):
    """
    Calculate cash balance and invested percentage.

    Primary source: order_management_snapshots table (stored by webhook/sighook)
    Fallback: Coinbase REST API (if snapshot not available)
    """
    invested = float(exposures.get("total_notional", 0.0) if exposures else 0.0)
    notes = []
    cash = 0.0

    # PRIMARY: Get USD balance from order_management_snapshots (most reliable)
    try:
        snapshot_query = """
            SELECT
                (data::jsonb->'non_zero_balances'->'USD'->>'total_balance_fiat')::numeric as usd_balance,
                snapshot_time
            FROM order_management_snapshots
            WHERE data::jsonb->'non_zero_balances'->'USD' IS NOT NULL
            ORDER BY snapshot_time DESC
            LIMIT 1
        """
        snapshot_result = conn.run(snapshot_query)
        if snapshot_result and snapshot_result[0][0] is not None:
            cash = float(snapshot_result[0][0])
            snapshot_time = snapshot_result[0][1]
            notes.append(f"Cash source: DB snapshot ({snapshot_time})")
        else:
            raise ValueError("No USD balance in order_management_snapshots")

    except Exception as db_error:
        # FALLBACK: Try Coinbase API
        notes.append(f"DB snapshot failed ({db_error}), trying Coinbase API")

        try:
            from coinbase.rest import RESTClient
            import os
            import json

            # Load credentials from JSON file
            api_key = None
            api_secret = None
            api_key_paths = [
                "/app/Config/webhook_api_key.json",
                os.path.join(os.path.dirname(__file__), "..", "Config", "webhook_api_key.json"),
            ]
            for path in api_key_paths:
                if os.path.exists(path):
                    with open(path, 'r') as f:
                        creds = json.load(f)
                        api_key = creds.get("name")
                        api_secret = creds.get("privateKey")
                        break

            if not api_key or not api_secret:
                raise ValueError("Missing Coinbase API credentials")

            client = RESTClient(api_key=api_key, api_secret=api_secret)

            # Use get_portfolio_breakdown for fiat balances (get_accounts only returns crypto)
            portfolio_uuid = os.getenv("COINBASE_PORTFOLIO_UUID", "c5a8f238-d2ef-5bb4-93cd-0dfd7a773be7")
            breakdown = client.get_portfolio_breakdown(portfolio_uuid=portfolio_uuid)

            if breakdown and hasattr(breakdown, 'breakdown'):
                # Find USD in portfolio breakdown
                for position in getattr(breakdown.breakdown, 'spot_positions', []):
                    if position.asset == 'USD':
                        cash = float(position.total_balance_fiat)
                        notes.append(f"Cash source: Coinbase API (portfolio breakdown)")
                        break
            else:
                raise ValueError("Could not get portfolio breakdown")

        except Exception as api_error:
            notes.append(f"Coinbase API failed ({api_error})")
            cash = 0.0

    # Calculate invested percentage
    total_equity = cash + invested
    invested_pct = (invested / total_equity * 100.0) if total_equity > 0 else 0.0

    return cash, invested, invested_pct, notes

# ============================================================================
# SECTION 6: Fast Roundtrip Analysis
# ============================================================================
# Near-instant (≤60s) trade detection and analysis

def build_fast_rt_sql(table_name: str, lookback_minutes: int = None):
    """
    ≤60s roundtrips with realized, fees-aware PnL.
    - Time-windowed (default 24h via FAST_RT_LOOKBACK_MINUTES)
    - Prefer parent_id link when available; fall back if missing on either side
    - Deterministic pairing when timestamps tie (order_id tie-break)
    """
    if lookback_minutes is None:
        try:
            lookback_minutes = int(os.getenv("FAST_RT_LOOKBACK_MINUTES", "1440"))
        except Exception:
            lookback_minutes = 1440

    time_window_clause = f"AND e.order_time >= (now() AT TIME ZONE 'UTC') - interval '{lookback_minutes} minutes'"

    return text(f"""
WITH execs AS (
  SELECT
      e.order_id                                     AS id,
      e.symbol                                       AS symbol,
      LOWER(e.side)                                  AS side,
      e.size::numeric(38,18)                         AS qty,
      e.price::numeric(38,18)                        AS price,
      COALESCE(e.total_fees_usd, 0)::numeric(38,18)  AS fee_usd,
      e.order_time::timestamptz                      AS exec_time,
      e.parent_id                                    AS link_key,
      e.status                                       AS status
  FROM {table_name} e
  WHERE e.status = 'filled'
    {time_window_clause}
),
paired AS (
  SELECT
      a.id               AS entry_id,
      a.symbol,
      a.link_key,
      a.side             AS entry_side,
      a.qty              AS entry_qty,
      a.price            AS entry_price,
      a.fee_usd          AS entry_fee_usd,
      a.exec_time        AS entry_time,

      b.id               AS exit_id,
      b.side             AS exit_side,
      b.qty              AS exit_qty,
      b.price            AS exit_price,
      b.fee_usd          AS exit_fee_usd,
      b.exec_time        AS exit_time,

      EXTRACT(EPOCH FROM (b.exec_time - a.exec_time))::int AS hold_seconds
  FROM execs a
  JOIN LATERAL (
      SELECT *
      FROM execs b
      WHERE b.symbol = a.symbol
        AND b.side <> a.side
        -- strictly later, with tie-break when same second
        AND (b.exec_time > a.exec_time OR (b.exec_time = a.exec_time AND b.id > a.id))
        -- Pairing preference:
        AND (
             (a.link_key IS NOT NULL AND b.link_key = a.link_key)
             OR (a.link_key IS NULL OR b.link_key IS NULL)
        )
      ORDER BY b.exec_time, b.id
      LIMIT 1
  ) b ON TRUE
),
realized AS (
  SELECT
      entry_id, exit_id, symbol,
      entry_side, entry_qty, entry_price, entry_fee_usd, entry_time,
      exit_side,  exit_qty,  exit_price,  exit_fee_usd,  exit_time,
      hold_seconds,

      LEAST(entry_qty, exit_qty) AS matched_qty,

      -- scale each leg's fee by its matched fraction
      CASE
        WHEN entry_qty IS NULL OR entry_qty = 0 THEN 0
        ELSE entry_fee_usd * (LEAST(entry_qty, exit_qty) / NULLIF(entry_qty,0))
      END AS entry_fee_used,

      CASE
        WHEN exit_qty IS NULL OR exit_qty = 0 THEN 0
        ELSE exit_fee_usd * (LEAST(entry_qty, exit_qty) / NULLIF(exit_qty,0))
      END AS exit_fee_used
  FROM paired
),
pnl AS (
  SELECT
      *,
      (entry_fee_used + exit_fee_used)                                         AS fees_used_usd,
      -- Sign convention: positive when the roundtrip closes profitably
      (matched_qty * (exit_price - entry_price) *
          CASE WHEN entry_side='buy' THEN 1 ELSE -1 END
      ) - (entry_fee_used + exit_fee_used)                                     AS pnl_usd,

      -- net % relative to entry notional
      CASE
        WHEN entry_price = 0 OR matched_qty = 0 THEN NULL
        ELSE 100.0 * (
          ( (matched_qty * (exit_price - entry_price) *
              CASE WHEN entry_side='buy' THEN 1 ELSE -1 END
            ) - (entry_fee_used + exit_fee_used)
          ) / (matched_qty * entry_price)
        )
      END                                                                      AS pnl_pct
  FROM realized
)
SELECT
    entry_id, exit_id, symbol,
    entry_side, entry_qty, entry_price, entry_fee_usd, entry_time,
    exit_side,  exit_qty,  exit_price,  exit_fee_usd,  exit_time,
    hold_seconds,
    matched_qty,
    fees_used_usd,
    -- keep notional entry for reference/debugging
    (matched_qty * entry_price)                                                AS notional_entry,
    pnl_usd,
    pnl_pct
FROM pnl
WHERE hold_seconds BETWEEN 0 AND 60
ORDER BY entry_time DESC
LIMIT 200;
""")

def fetch_fast_roundtrips(engine, csv_path="/app/logs/fast_roundtrips.csv"):
    try:
        lookback = int(config.report_lookback_minutes)
        with engine.begin() as conn:
            df = pd.read_sql(
                build_fast_rt_sql(
                    os.getenv("REPORT_EXECUTIONS_TABLE", REPORT_EXECUTIONS_TABLE),
                    lookback_minutes=lookback
                ),
                con=conn
            )
    except Exception as e:
        msg = f"{type(e).__name__}: {str(e)}"
        if os.getenv("REPORT_DEBUG") == "1":
            logger.error("Fast roundtrips query failed", extra={'error_type': type(e).__name__, 'error_msg': str(e)})
        html = (
            "<h3>Near-Instant Roundtrips (≤60s)</h3>"
            "<p style='color:#b00;'>Could not compute fast roundtrips. Ensure REPORT_EXECUTIONS_TABLE points to your fills table and column names match: "
            "<code>order_time, price, size, total_fees_usd, side, status, parent_id, symbol, order_id</code>.</p>"
            f"<small>{msg}</small>"
        )
        return html.strip(), None, None

    if df.empty:
        html = (
            "<h3>Near-Instant Roundtrips (≤60s)</h3>"
            "<p>No roundtrips ≤60 seconds in this window.</p>"
        )
        return html.strip(), None, df

    # --- Normalize types & sanitize ---
    df["entry_time"] = pd.to_datetime(df["entry_time"], utc=True, errors="coerce")
    df["exit_time"]  = pd.to_datetime(df["exit_time"],  utc=True, errors="coerce")

    df = df.dropna(subset=["entry_time", "exit_time", "hold_seconds", "entry_price", "exit_price"])
    df = df[df["entry_time"] < df["exit_time"]]
    df = df[df["hold_seconds"].astype(int) >= 0]

    # Optional cosmetic clamp to avoid “0s” when exchange timestamps share the same second
    if os.getenv("FAST_RT_CLAMP_ONESEC", "1") == "1":
        df["hold_seconds"] = df["hold_seconds"].clip(lower=1)

    # --- Metrics ---
    n = int(len(df))
    median_hold = int(float(df["hold_seconds"].median())) if n else 0
    total_pnl = float(np.nansum(df["pnl_usd"].astype(float)))

    # --- Helpers (define BEFORE using) ---
    def _fmt_money(x):
        try:
            xv = float(x)
            return f"${xv:,.2f}"
        except Exception:
            return ""

    def _fmt_pct(x):
        try:
            xv = float(x)
            return f"{xv:,.2f}%"
        except Exception:
            return ""

    def _fmt_price(x):
        try:
            s = f"{float(x):.8f}".rstrip('0').rstrip('.')
            return s if s else "0"
        except Exception:
            return ""

    # --- Build the preview table (top 8) ---
    view_cols = [
        "symbol", "entry_time", "exit_time",
        "hold_seconds", "entry_price", "exit_price",
        "pnl_usd", "pnl_pct"
    ]
    show = df.loc[:, view_cols].copy()

    show["entry_time"]  = show["entry_time"].dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    show["exit_time"]   = show["exit_time"].dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    show["entry_price"] = show["entry_price"].map(_fmt_price)
    show["exit_price"]  = show["exit_price"].map(_fmt_price)
    show["pnl_usd"]     = show["pnl_usd"].map(_fmt_money)
    show["pnl_pct"]     = show["pnl_pct"].map(_fmt_pct)

    html_table = show.head(8).to_html(index=False, border=1, justify="center", escape=False)

    # --- Freshness note if latest exit is old ---
    try:
        latest_ts = pd.to_datetime(df["exit_time"], utc=True).max()
        now_utc = pd.Timestamp.utcnow().tz_localize("UTC")
        age = now_utc - latest_ts
        if age.total_seconds() > 6 * 3600:
            hours = int(age.total_seconds() // 3600)
            mins = int((age.total_seconds() % 3600) // 60)
            stale_note = f"<p style='color:#b00;'>Note: newest ≤60s roundtrip is {hours}h {mins}m old.</p>"
            html_table = stale_note + html_table
    except Exception:
        pass

    html = f"""
    <h3>Near-Instant Roundtrips (≤60s)</h3>
    <p>Count: <b>{n}</b> &nbsp; Median hold: <b>{median_hold}s</b> &nbsp; Total PnL: <b>${total_pnl:,.2f}</b></p>
    {html_table}
    <p style="color:#666;margin-top:6px">Full list (up to 200) saved as CSV on the server.</p>
    """.strip()

    # --- Write CSV safely ---
    csv_out = None
    try:
        out_path = Path(csv_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(out_path, index=False)
        csv_out = str(out_path)
    except Exception as e:
        if os.getenv("REPORT_DEBUG") == "1":
            logger.error("Fast roundtrips CSV write failed", extra={'error': str(e)}, exc_info=True)

    return html, csv_out, df

# ============================================================================
# SECTION 7: HTML Generation
# ============================================================================
# Email body construction, section builders, formatting
def render_fees_section_html(break_even: dict) -> str:
    if not break_even:
        return ""
    t = break_even.get("rt_taker_pct", 0.0)
    m = break_even.get("rt_maker_pct", 0.0)
    return f"""
    <h3>Costs &amp; Break-Even Move</h3>
    <table border="1" cellpadding="6" cellspacing="0">
      <tr><th>Round-trip taker break-even</th><th>Round-trip maker break-even</th></tr>
      <tr><td>{t:.2f}%</td><td>{m:.2f}%</td></tr>
    </table>
    <p style="color:#666">Your average trade must exceed these percentages (before slippage) to be profitable.</p>
    """

def render_linkage_section_html(linkage_stats: dict) -> str:
    """Render strategy linkage statistics for testing/debugging"""
    if not linkage_stats or "error" in linkage_stats:
        error_msg = linkage_stats.get("error", "Unknown error") if linkage_stats else "No data"
        return f"""
        <h3>Strategy Linkage Status (Testing)</h3>
        <p style="color:#999;font-style:italic;">{error_msg}</p>
        """

    total = linkage_stats.get("total_count", 0)
    linked = linkage_stats.get("linked_count", 0)
    missing = linkage_stats.get("missing_count", 0)
    rate = linkage_stats.get("linkage_rate", 0.0)

    # Status indicator
    if rate >= 90:
        status_color = "#28a745"
        status_icon = "✅"
    elif rate >= 50:
        status_color = "#ffc107"
        status_icon = "⚠️"
    else:
        status_color = "#dc3545"
        status_icon = "❌"

    html = f"""
    <h3>📊 Strategy Linkage Status (Testing)</h3>
    <p><strong style="color:{status_color};">{status_icon} Coverage: {linked}/{total} trades ({rate:.1f}%)</strong> | Target: &gt;90%</p>
    """

    # Sample linked trades
    sample_trades = linkage_stats.get("sample_trades", [])
    if sample_trades:
        html += """
        <h4>Recent Linked Trades</h4>
        <table border="1" cellpadding="4" cellspacing="0" style="font-size:12px;">
          <tr>
            <th>Symbol</th>
            <th>Side</th>
            <th>Time</th>
            <th>Buy Score</th>
            <th>Sell Score</th>
            <th>Trigger</th>
            <th>Snapshot ID</th>
          </tr>
        """
        for row in sample_trades[:5]:
            symbol, side, ts, buy_score, sell_score, trigger, snap_id = row
            buy_score_str = f"{buy_score:.2f}" if buy_score is not None else "-"
            sell_score_str = f"{sell_score:.2f}" if sell_score is not None else "-"
            trigger_str = trigger or "-"
            ts_str = str(ts)[:19] if ts else "-"
            html += f"""
          <tr>
            <td>{symbol}</td>
            <td>{side.upper()}</td>
            <td>{ts_str}</td>
            <td>{buy_score_str}</td>
            <td>{sell_score_str}</td>
            <td>{trigger_str}</td>
            <td>{snap_id}...</td>
          </tr>
            """
        html += "</table>"

    # Missing trades (for debugging)
    missing_trades = linkage_stats.get("missing_trades", [])
    if missing_trades and missing > 0:
        html += f"""
        <h4 style="color:#dc3545;">⚠️ Missing Linkage ({missing} trades)</h4>
        <table border="1" cellpadding="4" cellspacing="0" style="font-size:11px;">
          <tr><th>Symbol</th><th>Side</th><th>Time</th><th>Order ID</th></tr>
        """
        for row in missing_trades[:10]:
            symbol, side, ts, order_id = row
            ts_str = str(ts)[:19] if ts else "-"
            order_id_short = order_id[:12] + "..." if len(order_id) > 12 else order_id
            html += f"<tr><td>{symbol}</td><td>{side.upper()}</td><td>{ts_str}</td><td>{order_id_short}</td></tr>"
        html += "</table>"
        html += "<p style='color:#666;font-size:11px;'><em>Debug: Check logs for cache misses or webhook issues</em></p>"

    return html

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
               fees_html: str = "",
               tpsl_html: str = "",
               score_section_html: str = "",
               fifo_health: Optional[dict] = None,
               trigger_breakdown: Optional[list] = None,
               source_stats: Optional[list] = None,
               hodl_positions: Optional[list] = None
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

    fifo_health_html = ""
    if fifo_health and USE_FIFO_ALLOCATIONS:
        status_color = "#0a0" if fifo_health['unmatched_sells'] == 0 else "#b80"
        status_text = "✓ Healthy" if fifo_health['unmatched_sells'] == 0 else f"⚠ {fifo_health['unmatched_sells']} unmatched sells"
        fifo_health_html = f"""
        <h3>FIFO Allocation Health</h3>
        <table border="1" cellpadding="6" cellspacing="0">
          <tr><th>Version</th><th>Total Allocations</th><th>Sells Matched</th><th>Buys Used</th><th>Status</th><th>Total PnL</th></tr>
          <tr>
            <td>{fifo_health['version']}</td>
            <td>{fifo_health['total_allocations']:,}</td>
            <td>{fifo_health['sells_matched']:,}</td>
            <td>{fifo_health['buys_used']:,}</td>
            <td style="color:{status_color};font-weight:bold">{status_text}</td>
            <td>${fifo_health['total_pnl']:,.2f}</td>
          </tr>
        </table>
        <p style="color:#666">PnL calculated using FIFO (First-In-First-Out) allocation method for accurate cost basis.</p>
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

    # Trigger Breakdown Section
    trigger_html = ""
    if trigger_breakdown:
        total_trigger_pnl = sum(t['total_pnl'] for t in trigger_breakdown)
        trigger_rows = ""
        for t in trigger_breakdown:
            win_rate_pct = (t['win_count'] / t['order_count'] * 100) if t['order_count'] > 0 else 0
            pnl_color = "#0a0" if t['total_pnl'] > 0 else "#c00" if t['total_pnl'] < 0 else "#666"
            trigger_rows += f"""
            <tr>
                <td>{t['trigger']}</td>
                <td>{t['order_count']}</td>
                <td>{t['win_count']}</td>
                <td>{t['loss_count']}</td>
                <td>{win_rate_pct:.1f}%</td>
                <td style="color:{pnl_color};font-weight:bold">${t['total_pnl']:,.2f}</td>
            </tr>
            """
        trigger_html = f"""
        <h3>Trigger Breakdown (Windowed)</h3>
        <p><b>Total PnL by Trigger:</b> ${total_trigger_pnl:,.2f}</p>
        <table border="1" cellpadding="6" cellspacing="0">
          <tr><th>Trigger</th><th>Orders</th><th>Wins</th><th>Losses</th><th>Win Rate</th><th>Total PnL</th></tr>
          {trigger_rows}
        </table>
        <p style="color:#666;font-size:12px">Shows performance by exit trigger type (POSITION_MONITOR, REARM_OCO_MISSING, etc.)</p>
        """

    # Source Statistics Section
    source_html = ""
    if source_stats:
        total_orders = sum(s['buy_count'] + s['sell_count'] for s in source_stats)
        source_rows = ""
        for s in source_stats:
            total_count = s['buy_count'] + s['sell_count']
            pct_of_total = (total_count / total_orders * 100) if total_orders > 0 else 0
            pnl_color = "#0a0" if s['total_pnl'] > 0 else "#c00" if s['total_pnl'] < 0 else "#666"
            source_rows += f"""
            <tr>
                <td>{s['source']}</td>
                <td>{s['buy_count']}</td>
                <td>{s['sell_count']}</td>
                <td>{total_count}</td>
                <td>{pct_of_total:.1f}%</td>
                <td style="color:{pnl_color};font-weight:bold">${s['total_pnl']:,.2f}</td>
            </tr>
            """
        source_html = f"""
        <h3>Source Statistics (Windowed)</h3>
        <p><b>Total Orders:</b> {total_orders}</p>
        <table border="1" cellpadding="6" cellspacing="0">
          <tr><th>Source</th><th>Buys</th><th>Sells</th><th>Total</th><th>% of Total</th><th>PnL</th></tr>
          {source_rows}
        </table>
        <p style="color:#666;font-size:12px">Shows activity by entry point (websocket, sighook, manual, reconciled)</p>
        """

    # HODL Positions Section
    hodl_html = ""
    if hodl_positions:
        total_hodl_value = sum(abs(h['qty']) * h['avg_price'] for h in hodl_positions)
        hodl_rows = ""
        for h in hodl_positions:
            side = "long" if h['qty'] > 0 else "short"
            notional = abs(h['qty']) * h['avg_price']
            # Note: unrealized_pnl will be 0 unless we fetch live prices
            hodl_rows += f"""
            <tr>
                <td>{h['symbol']}</td>
                <td>{side}</td>
                <td>{h['qty']:.4f}</td>
                <td>${h['avg_price']:.4f}</td>
                <td>${notional:,.2f}</td>
            </tr>
            """
        hodl_html = f"""
        <h3>HODL Positions (Long-term Holdings)</h3>
        <p><b>Total HODL Value:</b> ${total_hodl_value:,.2f}</p>
        <table border="1" cellpadding="6" cellspacing="0">
          <tr><th>Symbol</th><th>Side</th><th>Quantity</th><th>Avg Cost</th><th>Notional Value</th></tr>
          {hodl_rows}
        </table>
        <p style="color:#666;font-size:12px">These assets are marked as HODL and will not trigger automatic sells.</p>
        """

    return f"""<html><body style="font-family:Arial,Helvetica,sans-serif">
        <h2>Daily Trading Bot Report</h2><p><b>As of:</b> {now_utc}</p>
        {key_metrics_html}
        {fees_html}
        {trade_stats_html}
        {risk_cap_html}
        {score_section_html}
        {trigger_html}
        {source_html}
        {exposure_html}
        {hodl_html}
        {strat_html}
        {fifo_health_html}
        {details_html}
        {notes_html}
        <p style="color:#666">CSV attachment includes these tables.</p>
        </body></html>"""

# ============================================================================
# SECTION 8: CSV & File I/O
# ============================================================================
# CSV export and local file storage
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
    """
    Compute strategy breakdown using FIFO allocations table.
    Returns: (breakdown_list, notes)
    """
    tbl = REPORT_TRADES_TABLE
    cols = table_columns(conn, tbl)
    out = []
    notes = []
    if not cols:
        return out, ["Strategy: table not found"]

    strat_col = pick_first_available(cols, ["strategy","module","algo","tag"])
    ts_col = pick_first_available(cols, ["trade_time","filled_at","completed_at","order_time","ts","created_at","executed_at"])

    if not strat_col or not ts_col:
        miss = []
        if not strat_col: miss.append("strategy-like column")
        if not ts_col: miss.append("time-like column")
        notes.append("Strategy: missing " + ", ".join(miss))
        return out, notes

    # Verify side column exists
    if 'side' not in cols:
        notes.append("Strategy: 'side' column not found")
        return out, notes

    ts_expr = qident(ts_col)
    time_window_sql, upper_bound_sql = _time_window_sql(ts_expr)

    # Use FIFO allocations for P&L calculation
    q = f"""
        WITH trade_pnl AS (
            SELECT
                tr.{qident(strat_col)} AS strat,
                tr.order_id,
                COALESCE(SUM(fa.pnl_usd), 0) AS pnl
            FROM {qualify(tbl)} tr
            LEFT JOIN fifo_allocations fa
                ON fa.sell_order_id = tr.order_id
                AND fa.allocation_version = {FIFO_ALLOCATION_VERSION}
            WHERE {time_window_sql}
              {upper_bound_sql}
              AND tr.side = 'sell'
              AND tr.status IN ('filled', 'done')
            GROUP BY tr.{qident(strat_col)}, tr.order_id
        )
        SELECT strat,
               COUNT(*) AS total,
               COUNT(*) FILTER (WHERE pnl > 0) AS wins,
               COALESCE(SUM(pnl), 0) AS pnl
        FROM trade_pnl
        WHERE strat IS NOT NULL
        GROUP BY strat
        ORDER BY total DESC
        LIMIT 20
    """
    for strat, total, wins, pnl in conn.run(q):
        total = int(total or 0)
        wins = int(wins or 0)
        wr = (wins / total * 100.0) if total > 0 else 0.0
        out.append({"strategy": strat, "total": total, "wins": wins, "win_rate": wr, "pnl": float(pnl or 0.0)})
    notes.append(f"Strategy source: {tbl} using FIFO v{FIFO_ALLOCATION_VERSION} strat_col={strat_col} ts_col={ts_col}")
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

# ============================================================================
# SECTION 9: Email Delivery
# ============================================================================
# SES email sending wrapper
def send_email(html, csv_bytes):
    send_email_via_ses(
        subject="Daily Trading Bot Report",
        text_body=_html_to_text(html),
        html_body=html,
        csv_bytes=csv_bytes,
        sender=SENDER,
        recipients=RECIPIENTS,
    )


# ============================================================================
# SECTION 10: Main Entry Point
# ============================================================================
# Report orchestration, error handling, output routing

def main():
    conn = get_db_conn()
    detect_notes = []
    fast_html = ""
    score_section_html = ""


    try:
        # Core queries (windowed where possible)
        total_pnl, open_pos, recent_trades, errors, detect_notes, fifo_health, trigger_breakdown, source_stats, hodl_positions = run_queries(conn)

        # Derived metrics
        unreal_pnl, unreal_notes = compute_unrealized_pnl(conn, open_pos)
        detect_notes.extend(unreal_notes)

        win_rate, wins, total_trades, wr_notes = compute_win_rate(conn)
        detect_notes.extend(wr_notes)

        avg_win, avg_loss, profit_factor, ts_notes = compute_trade_stats_windowed(conn)
        detect_notes.extend(ts_notes)

        max_dd_pct, max_dd_abs, peak_eq, trough_eq, dd_notes = compute_max_drawdown(conn)
        detect_notes.extend(dd_notes)

        exposures = compute_exposures(open_pos, top_n=DEFAULT_TOP_POSITIONS)
        cash_usd, invested_usd, invested_pct, cash_notes = compute_cash_vs_invested(conn, exposures)
        detect_notes.extend(cash_notes)

        strat_rows, strat_notes = compute_strategy_breakdown_windowed(conn)
        detect_notes.extend(strat_notes)

        # Strategy linkage stats (for testing/optimization)
        linkage_stats = compute_strategy_linkage_stats(conn)

        # Symbol performance analysis (if enabled)
        symbol_perf_html = ""
        if REPORT_INCLUDE_SYMBOL_PERFORMANCE:
            try:
                symbol_perf_data = compute_symbol_performance(
                    conn=get_sa_engine().connect(),
                    hours_back=DEFAULT_LOOKBACK_HOURS,
                    top_n=15,
                    min_trades=3
                )
                symbol_perf_html = render_symbol_performance_html(symbol_perf_data, include_header=True)
                symbol_suggestions = generate_symbol_suggestions(symbol_perf_data)
                if symbol_suggestions:
                    detect_notes.extend(symbol_suggestions)
            except Exception as e:
                symbol_perf_html = f"<!-- Symbol performance unavailable: {e} -->"
                detect_notes.append(f"Symbol performance error: {e}")

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
    try:
        score_path = SCORE_JSONL_PATH
        df = load_score_jsonl(score_path, since_hours=24)
        metrics = score_snapshot_metrics_from_jsonl(df)
        score_section_html = render_score_section_jsonl(metrics)
        tpsl_path = str(env_config.tp_sl_log_path)
        tpsl_rows = load_tpsl_jsonl(tpsl_path, since_hours=24)
        tpsl_per, tpsl_gsum = aggregate_tpsl(tpsl_rows)
        tpsl_table_html = render_tpsl_section(tpsl_per, tpsl_gsum)
        tpsl_suggest_html = render_tpsl_suggestions(tpsl_per, tpsl_gsum)
        tpsl_html = tpsl_table_html + (tpsl_suggest_html or "")
        detect_notes.append(f"Score section: {len(df)} rows from {score_path}")
    except Exception as e:
        score_section_html = f"<!-- score section unavailable: {e} -->"
        tpsl_html = f"<!-- tp/sl section unavailable: {e} -->"
        detect_notes.append(f"Score section error: {e}")

    # fees break-even block
    fee_info = compute_fee_break_even()
    fees_html = render_fees_section_html(fee_info)

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
        show_details=False,
        fees_html=fees_html,
        tpsl_html=tpsl_html,
        score_section_html=score_section_html,
        fifo_health=fifo_health,
        trigger_breakdown=trigger_breakdown,
        source_stats=source_stats,
        hodl_positions=hodl_positions,
    )

    # Near-instant roundtrips (≤60s) + CSV saved server-side when available
    try:
        sa_engine = get_sa_engine()
        fast_html, fast_csv_path, fast_df = fetch_fast_roundtrips(sa_engine)
    except Exception as e:
        if REPORT_DEBUG:
            logger.error("Fast roundtrips processing error", extra={'error': str(e)}, exc_info=True)
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
        if "</body></html>" in html:
            html = html.replace("</body></html>", tpsl_html + "\n</body></html>")
        else:
            html += "\n" + tpsl_html

    # Strategy linkage section (testing/optimization)
    linkage_html = render_linkage_section_html(linkage_stats)
    if linkage_html:
        if "</body></html>" in html:
            html = html.replace("</body></html>", linkage_html + "\n</body></html>")
        else:
            html += "\n" + linkage_html

    # Add symbol performance section (if enabled)
    if symbol_perf_html:
        if "</body></html>" in html:
            html = html.replace("</body></html>", symbol_perf_html + "\n</body></html>")
        else:
            html += "\n" + symbol_perf_html

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
        send_email(html, csvb)
        # Local dev: pretty console output + local CSV, no email
        as_of_utc = datetime.now(timezone.utc)
        window_label = "last 24h"  # or derive from REPORT_USE_PT_DAY/LOOKBACK
        source_label = "report_trades"

        console_text = build_console_report(
            as_of_utc=str(as_of_utc),
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
    main()

