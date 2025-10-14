# botreport/emailer.py
from __future__ import annotations
import os, smtplib
import boto3
from typing import Optional, Sequence
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from .config import load_email_config

def _build_mime(subject: str, sender: str, recipients: Sequence[str],
                text_body: str, html_body: Optional[str], csv_bytes: Optional[bytes]) -> MIMEMultipart:
    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(text_body or "", "plain"))
    if html_body:
        alt.attach(MIMEText(html_body, "html"))
    msg.attach(alt)
    if csv_bytes:
        part = MIMEApplication(csv_bytes, Name="trading_report.csv")
        part.add_header("Content-Disposition", 'attachment; filename="trading_report.csv"')
        msg.attach(part)
    return msg

def _send_via_ses(subject: str, text_body: str, *, html_body: Optional[str],
                  csv_bytes: Optional[bytes], sender: str, recipients: Sequence[str], region: str) -> None:
    ses = boto3.client("ses", region_name=region)
    msg = _build_mime(subject, sender, recipients, text_body, html_body, csv_bytes)
    ses.send_raw_email(Source=sender, Destinations=list(recipients),
                       RawMessage={"Data": msg.as_string().encode("utf-8")})

def _send_via_smtp(subject: str, text_body: str, *, html_body: Optional[str],
                   csv_bytes: Optional[bytes], sender: str, recipients: Sequence[str]) -> None:
    host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER", "")
    password = os.getenv("SMTP_PASSWORD", "")
    use_tls = os.getenv("SMTP_USE_TLS", "true").lower() in {"1","true","yes"}
    use_ssl = os.getenv("SMTP_USE_SSL", "false").lower() in {"1","true","yes"}
    msg = _build_mime(subject, sender, recipients, text_body, html_body, csv_bytes)
    if use_ssl:
        with smtplib.SMTP_SSL(host, port) as s:
            if user and password: s.login(user, password)
            s.sendmail(sender, recipients, msg.as_string())
    else:
        with smtplib.SMTP(host, port) as s:
            if use_tls: s.starttls()
            if user and password: s.login(user, password)
            s.sendmail(sender, recipients, msg.as_string())

def send_email(subject: str,
               text_body: str,
               *,
               html_body: Optional[str] = None,
               csv_bytes: Optional[bytes] = None,
               sender: Optional[str] = None,
               recipients: Optional[Sequence[str]] = None,
               ) -> None:
    """
    Uses legacy env keys:
      REPORT_SENDER / REPORT_RECIPIENTS / AWS_REGION (or SES_REGION),
    falling back to EMAIL_SENDER / EMAIL_TO, and EMAIL_BACKEND=ses|smtp.
    """
    ecfg = load_email_config()
    _sender = sender or ecfg.sender
    _recips = list(recipients) if recipients else ecfg.recipients

    if ecfg.backend == "ses":
        _send_via_ses(subject, text_body, html_body=html_body, csv_bytes=csv_bytes,
                      sender=_sender, recipients=_recips, region=ecfg.region)
    else:
        _send_via_smtp(subject, text_body, html_body=html_body, csv_bytes=csv_bytes,
                       sender=_sender, recipients=_recips)
