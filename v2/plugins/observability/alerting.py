"""Alerting observer — sends critical notifications via email/SMS.

Ported from Shared_Utils/alert_system.py. Monitors events for critical
conditions and sends alerts:
- Circuit breaker trips
- Risk events (signal vetoed, limits reached)
- Order rejections
- Large fills (configurable threshold)

Supports email (SMTP/Gmail) and SMS (carrier gateway).
Includes rate limiting to prevent alert storms.
"""

from __future__ import annotations

import logging
import os
import smtplib
import time
from email.mime.text import MIMEText
from typing import Any

from v2.core import registry
from v2.core.interfaces import Observer
from v2.core.types import (
    FillEvent,
    OrderEvent,
    RiskEvent,
    OrderStatus,
)

logger = logging.getLogger(__name__)


@registry.plugin("observer", "alerting")
class AlertingObserver(Observer):
    """Sends critical alerts via email/SMS on important events.

    Rate-limited to prevent alert storms: at most one alert per
    ``min_interval_seconds`` (default: 300 = 5 minutes).
    """

    name = "alerting"

    def __init__(self, event_bus=None, **kwargs: Any) -> None:
        # Email/SMS config
        self._smtp_server = kwargs.get("smtp_server", "smtp.gmail.com")
        self._smtp_port = int(kwargs.get("smtp_port", 465))
        self._sender = kwargs.get("sender", "") or os.environ.get("REPORT_SENDER", "")
        self._password = kwargs.get("password", "") or os.environ.get(
            "SMTP_PASSWORD", ""
        )

        # Recipients
        self._email_recipients = kwargs.get("email_recipients", "") or os.environ.get(
            "REPORT_RECIPIENTS", ""
        )
        phone = kwargs.get("phone", "") or os.environ.get("PHONE", "")
        self._sms_gateway = kwargs.get("sms_gateway", "txt.att.net")
        self._sms_recipient = f"{phone}@{self._sms_gateway}" if phone else ""

        self._mode = kwargs.get("mode", "email")  # "email", "sms", "both"
        self._enabled = kwargs.get("enabled", True)

        # Rate limiting
        self._min_interval = float(kwargs.get("min_interval_seconds", 300))
        self._last_alert_time: float = 0

        # Alert thresholds
        self._large_fill_usd = float(kwargs.get("large_fill_threshold_usd", 1000))

    # ------------------------------------------------------------------
    # Observer interface
    # ------------------------------------------------------------------

    def on_event(self, event: Any) -> None:
        """Process events and send alerts for critical conditions."""
        if not self._enabled:
            return

        if isinstance(event, RiskEvent):
            self._on_risk(event)
        elif isinstance(event, FillEvent):
            self._on_fill(event)
        elif isinstance(event, OrderEvent):
            self._on_order(event)

    def _on_risk(self, event: RiskEvent) -> None:
        """Alert on all risk events (circuit breaker, vetoes)."""
        if event.event_type == "circuit_breaker":
            self._send_alert(
                subject="CRITICAL: Circuit Breaker Tripped",
                body=(
                    f"Circuit breaker has been activated.\n\n"
                    f"Reason: {event.reason}\n"
                    f"Metadata: {event.metadata}\n\n"
                    f"All trading is halted until manual reset or cooldown expires."
                ),
            )
        elif event.event_type == "signal_vetoed":
            symbol = event.metadata.get("symbol", "unknown")
            self._send_alert(
                subject=f"Risk: Signal Vetoed ({symbol})",
                body=(
                    f"A trading signal was vetoed by the risk manager.\n\n"
                    f"Reason: {event.reason}\n"
                    f"Symbol: {symbol}\n"
                    f"Direction: {event.metadata.get('direction', 'unknown')}"
                ),
            )

    def _on_fill(self, event: FillEvent) -> None:
        """Alert on large fills."""
        fill = event.fill
        notional = float(fill.price * fill.qty)
        if notional >= self._large_fill_usd:
            self._send_alert(
                subject=f"Large Fill: {fill.side.value.upper()} {fill.symbol}",
                body=(
                    f"Large trade executed.\n\n"
                    f"Side: {fill.side.value.upper()}\n"
                    f"Symbol: {fill.symbol}\n"
                    f"Price: {fill.price}\n"
                    f"Qty: {fill.qty}\n"
                    f"Notional: ${notional:,.2f}\n"
                    f"Fee: {fill.fee}"
                ),
            )

    def _on_order(self, event: OrderEvent) -> None:
        """Alert on order rejections."""
        if event.order.status == OrderStatus.REJECTED:
            order = event.order
            self._send_alert(
                subject=f"Order Rejected: {order.symbol}",
                body=(
                    f"An order was rejected.\n\n"
                    f"Order ID: {order.order_id}\n"
                    f"Symbol: {order.symbol}\n"
                    f"Side: {order.side.value.upper()}\n"
                    f"Price: {order.price}\n"
                    f"Qty: {order.qty}"
                ),
            )

    # ------------------------------------------------------------------
    # Alert sending
    # ------------------------------------------------------------------

    def _send_alert(self, subject: str, body: str) -> bool:
        """Send alert if rate limit allows. Returns True if sent."""
        now = time.time()
        if now - self._last_alert_time < self._min_interval:
            logger.debug("Alert rate-limited: %s", subject)
            return False

        sent = False
        mode = self._mode

        if mode in ("email", "both"):
            sent = self._send_email(subject, body) or sent
        if mode in ("sms", "both"):
            sent = self._send_sms(subject, body) or sent

        if sent:
            self._last_alert_time = now

        return sent

    def _send_email(self, subject: str, body: str) -> bool:
        """Send email alert via SMTP."""
        if not all([self._sender, self._password, self._email_recipients]):
            logger.debug("Email alerting not configured — skipping")
            return False

        recipients = [
            r.strip() for r in self._email_recipients.split(",") if r.strip()
        ]

        try:
            msg = MIMEText(body)
            msg["Subject"] = f"[BotTrader v2] {subject}"
            msg["From"] = self._sender
            msg["To"] = ", ".join(recipients)

            with smtplib.SMTP_SSL(self._smtp_server, self._smtp_port) as server:
                server.login(self._sender, self._password)
                server.sendmail(self._sender, recipients, msg.as_string())

            logger.info("Alert email sent: %s", subject)
            return True

        except Exception:
            logger.exception("Failed to send alert email: %s", subject)
            return False

    def _send_sms(self, subject: str, body: str) -> bool:
        """Send SMS alert via carrier email gateway."""
        if not all([self._sender, self._password, self._sms_recipient]):
            logger.debug("SMS alerting not configured — skipping")
            return False

        # SMS messages should be short
        short_body = f"{subject}\n{body[:140]}"

        try:
            msg = MIMEText(short_body)
            msg["Subject"] = ""
            msg["From"] = self._sender
            msg["To"] = self._sms_recipient

            with smtplib.SMTP_SSL(self._smtp_server, self._smtp_port) as server:
                server.login(self._sender, self._password)
                server.sendmail(self._sender, [self._sms_recipient], msg.as_string())

            logger.info("Alert SMS sent: %s", subject)
            return True

        except Exception:
            logger.exception("Failed to send alert SMS: %s", subject)
            return False

    # ------------------------------------------------------------------
    # Manual alert
    # ------------------------------------------------------------------

    def send_manual_alert(self, subject: str, body: str) -> bool:
        """Send an alert bypassing rate limiting."""
        old_time = self._last_alert_time
        self._last_alert_time = 0  # Reset to bypass rate limit
        result = self._send_alert(subject, body)
        if not result:
            self._last_alert_time = old_time  # Restore if not sent
        return result
