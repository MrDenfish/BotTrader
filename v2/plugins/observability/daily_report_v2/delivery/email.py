"""Email delivery — SES and SMTP backends."""

from __future__ import annotations

import logging
import os
import smtplib
from dataclasses import dataclass, field
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Sequence

logger = logging.getLogger(__name__)


@dataclass
class EmailConfig:
    """Email delivery configuration."""

    backend: str = "ses"
    sender: str = ""
    recipients: list[str] = field(default_factory=list)
    region: str = "us-east-1"
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_user_env: str = ""
    smtp_password_env: str = "SMTP_PASSWORD"

    @classmethod
    def from_dict(cls, d: dict) -> EmailConfig:
        return cls(
            backend=d.get("backend", "ses"),
            sender=d.get("sender", ""),
            recipients=d.get("recipients", []),
            region=d.get("region", "us-east-1"),
            smtp_host=d.get("smtp_host", "smtp.gmail.com"),
            smtp_port=int(d.get("smtp_port", 587)),
            smtp_user=d.get("smtp_user", ""),
            smtp_user_env=d.get("smtp_user_env", ""),
            smtp_password_env=d.get("smtp_password_env", "SMTP_PASSWORD"),
        )


def send_email(
    subject: str,
    html_body: str,
    config: EmailConfig,
    text_body: str | None = None,
) -> bool:
    """Send an email report. Returns True on success."""
    if not config.sender or not config.recipients:
        logger.warning("Email not configured (sender=%s, recipients=%d)", config.sender, len(config.recipients))
        return False

    msg = _build_mime(subject, config.sender, config.recipients, text_body or "", html_body)

    if config.backend == "ses":
        return _send_via_ses(msg, config)
    else:
        return _send_via_smtp(msg, config)


def _build_mime(
    subject: str,
    sender: str,
    recipients: Sequence[str],
    text_body: str,
    html_body: str,
) -> MIMEMultipart:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))
    return msg


def _send_via_ses(msg: MIMEMultipart, config: EmailConfig) -> bool:
    try:
        import boto3
        ses = boto3.client("ses", region_name=config.region)
        ses.send_raw_email(
            Source=config.sender,
            Destinations=config.recipients,
            RawMessage={"Data": msg.as_string().encode("utf-8")},
        )
        logger.info("Report email sent via SES to %d recipients", len(config.recipients))
        return True
    except Exception:
        logger.exception("Failed to send email via SES")
        return False


def _send_via_smtp(msg: MIMEMultipart, config: EmailConfig) -> bool:
    password = os.environ.get(config.smtp_password_env, "")
    if not password:
        logger.warning("SMTP password not set (env: %s)", config.smtp_password_env)
        return False

    user = config.smtp_user
    if not user and config.smtp_user_env:
        user = os.environ.get(config.smtp_user_env, "")
    if not user:
        user = config.sender

    try:
        with smtplib.SMTP(config.smtp_host, config.smtp_port) as server:
            server.starttls()
            server.login(user, password)
            server.sendmail(config.sender, config.recipients, msg.as_string())
        logger.info("Report email sent via SMTP to %d recipients", len(config.recipients))
        return True
    except Exception:
        logger.exception("Failed to send email via SMTP")
        return False
