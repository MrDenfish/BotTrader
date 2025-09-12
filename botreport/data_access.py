from __future__ import annotations

import os, ssl
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timezone, timedelta
from typing import Tuple, Dict, Any, Optional
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from botreport.models import PositionRow, ExposureBlock, ReportBundle, MetricsBlock
from botreport.metrics_compute import compute_windowed_metrics

# ---------- SSM / SSL helpers (back-compat with your original code) ----------

def get_param(name: str) -> str:
    import boto3
    region = os.getenv("AWS_REGION") or os.getenv("SES_REGION") or "us-west-2"
    ssm = boto3.client("ssm", region_name=region)
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
            # non-fatal; leave default roots
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

# ---------- Engine factory honoring DATABASE_URL or env/SSM ----------

def get_engine() -> Engine:
    """
    SQLAlchemy Engine using pg8000 driver.
    Precedence:
      1) DATABASE_URL (e.g., postgresql+pg8000://user:pass@host:5432/db?sslmode=require)
      2) Env/SSM: DB_HOST/PORT/NAME/USER/PASSWORD (+ DB_*_SSM), defaults to host=db in Docker else 127.0.0.1
    """
    url = os.getenv("DATABASE_URL")
    in_docker = os.getenv("IN_DOCKER", "false").lower() == "true"

    if url:
        # Respect sslmode in the URL if present
        u = urlparse(url)
        qs = parse_qs(u.query or "")
        sslmode = (qs.get("sslmode", [""])[0] or "").lower()
        require_ssl = sslmode in {"require", "verify-ca", "verify-full"} or "+ssl" in (u.scheme or "")
        connect_args = {}
        ctx = _maybe_ssl_context(require_ssl)
        if ctx:
            connect_args["ssl_context"] = ctx
        return create_engine(url, pool_pre_ping=True, connect_args=connect_args)

    # Fallback to discrete env/SSM vars
    default_host = "db" if in_docker else "localhost"
    host = _env_or_ssm("DB_HOST", os.getenv("DB_HOST_SSM"), default_host).strip()
    port = int(_env_or_ssm("DB_PORT", os.getenv("DB_PORT_SSM"), "5432"))
    name = _env_or_ssm("DB_NAME", os.getenv("DB_NAME_SSM"))
    default_user = "DB_USER" if in_docker else "Manny"
    user = _env_or_ssm("DB_USER", os.getenv("DB_USER_SSM"), ("DB_USER" if in_docker else default_user or ""))
    pwd  = _env_or_ssm("DB_PASSWORD", os.getenv("DB_PASSWORD_SSM"))
    db_ssl = (os.getenv("DB_SSL", "disable").lower() in {"require", "true", "1"})

    dsn = f"postgresql+pg8000://{user}:{pwd}@{host}:{port}/{name}"
    connect_args = {}
    ctx = _maybe_ssl_context(db_ssl)
    if ctx:
        connect_args["ssl_context"] = ctx
    return create_engine(dsn, pool_pre_ping=True, connect_args=connect_args)

# Back-compat alias if other code expects this name
def get_sa_engine() -> Engine:
    return get_engine()

# ---------- The rest stays the same (window + loader + bundle) ----------

def resolve_time_window(hours: int = 24) -> Tuple[datetime, datetime]:
    now = datetime.now(tz=timezone.utc)
    start = now - timedelta(hours=hours)
    return start, now

def load_windowed_data(start: datetime, end: datetime, source: str) -> Dict[str, Any]:
    engine = get_engine()
    with engine.connect() as conn:
        return compute_windowed_metrics(conn, start, end, source)

def assemble_bundle(raw: Dict[str, Any],
                    window_label: str,
                    source_label: str,
                    starting_equity_usd: float,
                    csv_note: Optional[str]) -> ReportBundle:

    positions = [
        PositionRow(
            symbol=r["symbol"],
            side=r["side"],
            qty=float(r["qty"]),
            avg_price=float(r["avg_price"]),
            notional=float(r["notional"]),
            pct_total=float(r.get("pct_total", 0.0)),
        )
        for r in (raw.get("open_positions") or [])
    ] or None

    exp_tot = raw.get("exposure_totals", {}) or {}
    exposure = ExposureBlock(
        total_notional=float(exp_tot.get("total_notional", 0.0)),
        invested_pct_of_equity=exp_tot.get("invested_pct_of_equity"),
        leverage_used=exp_tot.get("leverage_used"),
        long_notional=exp_tot.get("long_notional"),
        short_notional=exp_tot.get("short_notional"),
        net_exposure_abs=exp_tot.get("net_abs"),
        net_exposure_pct=exp_tot.get("net_pct"),
        positions=positions,
    )

    metrics = MetricsBlock(
        as_of_iso=raw.get("as_of_iso") or datetime.now(tz=timezone.utc).isoformat(),
        window_label=window_label,
        source_label=source_label,
        realized_pnl=raw.get("realized_pnl"),
        unrealized_pnl=raw.get("unrealized_pnl"),
    )

    notes = ("Notes: Win rate includes breakevens in the denominator. "
             "Profit Factor = gross profits / gross losses. "
             f"Starting equity for drawdown: ${starting_equity_usd:,.2f}.")

    return ReportBundle(metrics=metrics, exposure=exposure, notes=notes, csv_note=csv_note)