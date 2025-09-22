# botreport/config.py
from __future__ import annotations
import os, ssl
from dataclasses import dataclass
from email.utils import getaddresses
from pathlib import Path
from typing import Optional

try:
    from dotenv import load_dotenv  # type: ignore
except Exception:
    load_dotenv = None

@dataclass(frozen=True)
class EmailConfig:
    backend: str                 # "ses" or "smtp"
    region: str                  # AWS SES region
    sender: str
    recipients: list[str]

@dataclass(frozen=True)
class DBConfig:
    url: Optional[str]
    host: str
    port: int
    name: str
    user: str
    password: str
    ssl_required: bool

@dataclass(frozen=True)
class ReportConfig:
    in_docker: bool
    lookback_hours: int
    use_pt_day: bool
    show_details: bool
    taker_fee: float
    maker_fee: float
    starting_equity: float

def load_report_dotenv_if_needed() -> None:
    """
    Local-only env loader for the email report.
    - If IN_DOCKER=true: do nothing (Compose/entrypoint env wins).
    - Else: load project-root/.env_runtime if present; else fall back to .env_tradebot.
    - Does NOT override already-set env vars (override=False).
    """
    if os.getenv("IN_DOCKER", "false").lower() == "true":
        return
    if load_dotenv is None:
        return
    here = Path(__file__).resolve()
    # botreport/ -> project root at parents[1]
    project_root = here.parents[1]
    preferred = project_root / ".env_runtime"
    fallback  = project_root / ".env_tradebot"
    if preferred.exists():
        load_dotenv(dotenv_path=preferred, override=False)
    elif fallback.exists():
        load_dotenv(dotenv_path=fallback, override=False)

def _env_list_csv(name: str) -> list[str]:
    raw = os.getenv(name, "")
    if raw.strip():
        return [s.strip() for s in raw.split(",") if s.strip()]
    return []

def load_email_config() -> EmailConfig:
    backend = os.getenv("EMAIL_BACKEND", "").strip().lower()
    # Backward-compat: if not set, prefer SES when REPORT_SENDER/RECIPIENTS/AWS_REGION are present
    if not backend:
        backend = "ses" if os.getenv("AWS_REGION") else "smtp"

    region = os.getenv("SES_REGION") or os.getenv("AWS_REGION", "us-west-2")

    # Backward-compat names from your original code:
    sender = (os.getenv("REPORT_SENDER") or os.getenv("EMAIL_SENDER") or "").strip()
    if not sender:
        raise ValueError("Missing REPORT_SENDER/EMAIL_SENDER")

    recips_env = os.getenv("REPORT_RECIPIENTS") or os.getenv("EMAIL_TO") or sender
    recipients = [addr for _, addr in getaddresses([recips_env]) if addr] or [sender]
    return EmailConfig(backend=backend, region=region, sender=sender, recipients=recipients)

def load_db_config() -> DBConfig:
    url  = os.getenv("DATABASE_URL") or None
    host = (os.getenv("DB_HOST") or ("db" if os.getenv("IN_DOCKER","false").lower()=="true" else "127.0.0.1")).strip()
    port = int((os.getenv("DB_PORT") or "5432").strip())
    name = (os.getenv("DB_NAME") or "").strip()
    user = (os.getenv("DB_USER") or "").strip()
    pwd  = (os.getenv("DB_PASSWORD") or "").strip()

    ssl_required = (os.getenv("DB_SSL", "disable").lower() in {"require", "true", "1"})
    return DBConfig(url=url, host=host, port=port, name=name, user=user, password=pwd, ssl_required=ssl_required)

def load_report_config() -> ReportConfig:
    in_docker = os.getenv("IN_DOCKER", "false").lower() == "true"
    lookback = int(os.getenv("REPORT_LOOKBACK_HOURS", os.getenv("LOOKBACK_HOURS", "24")))
    use_pt   = os.getenv("REPORT_USE_PT_DAY", "0").lower() in {"1","true","yes"}
    details  = os.getenv("REPORT_SHOW_DETAILS", "0").lower() in {"1","true","yes"}
    taker    = float(os.getenv("TAKER_FEE", "0.0040"))
    maker    = float(os.getenv("MAKER_FEE", "0.0025"))
    equity   = float(os.getenv("STARTING_EQUITY_USD", "3000"))
    return ReportConfig(
        in_docker=in_docker,
        lookback_hours=lookback,
        use_pt_day=use_pt,
        show_details=details,
        taker_fee=taker,
        maker_fee=maker,
        starting_equity=equity,
    )
