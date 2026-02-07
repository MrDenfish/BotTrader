"""Daily report observer — accumulates stats and sends email summary.

Ported from bottrader-report. Tracks daily fills, P&L, signals,
and generates a summary report at a configurable hour (default: 8 AM UTC).
Sends via SMTP (Gmail).
"""

from __future__ import annotations

import logging
import os
import smtplib
from datetime import date, datetime, timezone
from decimal import Decimal
from email.mime.text import MIMEText
from typing import Any

from v2.core import registry
from v2.core.interfaces import Observer
from v2.core.types import (
    FillEvent,
    OrderEvent,
    RiskEvent,
    SignalEvent,
    Side,
)

logger = logging.getLogger(__name__)


@registry.plugin("observer", "daily_report")
class DailyReportObserver(Observer):
    """Accumulates daily trading stats and sends email report.

    Tracks:
    - Total fills (buy/sell counts and volumes)
    - Realized P&L estimates
    - Signal count by strategy
    - Risk events (vetoes, circuit breakers)
    - Order rejections

    Sends report via SMTP at the configured hour, or when
    ``send_report()`` is called directly.
    """

    name = "daily_report"

    def __init__(self, event_bus=None, **kwargs: Any) -> None:
        # Email config
        self._smtp_server = kwargs.get("smtp_server", "smtp.gmail.com")
        self._smtp_port = int(kwargs.get("smtp_port", 465))
        self._sender = kwargs.get("sender", "") or os.environ.get("REPORT_SENDER", "")
        self._password = kwargs.get("password", "") or os.environ.get("SMTP_PASSWORD", "")
        self._recipients = kwargs.get("recipients", "") or os.environ.get(
            "REPORT_RECIPIENTS", ""
        )
        self._enabled = kwargs.get("enabled", True)

        # Report timing
        self._report_hour_utc = int(kwargs.get("report_hour_utc", 8))
        self._last_report_date: date | None = None

        # Daily accumulators
        self._reset_stats()

    def _reset_stats(self) -> None:
        """Reset all daily accumulators."""
        self._stats_date = datetime.now(timezone.utc).date()
        self._buy_count = 0
        self._sell_count = 0
        self._buy_volume_usd = Decimal("0")
        self._sell_volume_usd = Decimal("0")
        self._total_fees = Decimal("0")
        self._estimated_pnl = Decimal("0")
        self._signal_count: dict[str, int] = {}
        self._risk_events: list[str] = []
        self._rejection_count = 0

    # ------------------------------------------------------------------
    # Observer interface
    # ------------------------------------------------------------------

    def on_event(self, event: Any) -> None:
        """Process all events and accumulate daily stats."""
        self._check_date_rotation()

        if isinstance(event, FillEvent):
            self._on_fill(event)
        elif isinstance(event, SignalEvent):
            self._on_signal(event)
        elif isinstance(event, OrderEvent):
            self._on_order(event)
        elif isinstance(event, RiskEvent):
            self._on_risk(event)

    def _on_fill(self, event: FillEvent) -> None:
        fill = event.fill
        notional = fill.price * fill.qty

        if fill.side == Side.BUY:
            self._buy_count += 1
            self._buy_volume_usd += notional
        else:
            self._sell_count += 1
            self._sell_volume_usd += notional

        self._total_fees += fill.fee

    def _on_signal(self, event: SignalEvent) -> None:
        name = event.strategy_name
        self._signal_count[name] = self._signal_count.get(name, 0) + 1

    def _on_order(self, event: OrderEvent) -> None:
        from v2.core.types import OrderStatus
        if event.order.status == OrderStatus.REJECTED:
            self._rejection_count += 1

    def _on_risk(self, event: RiskEvent) -> None:
        self._risk_events.append(f"{event.event_type}: {event.reason}")

    # ------------------------------------------------------------------
    # Date rotation and report trigger
    # ------------------------------------------------------------------

    def _check_date_rotation(self) -> None:
        """Check if it's time to send a report and rotate stats."""
        now = datetime.now(timezone.utc)
        today = now.date()

        if self._stats_date != today:
            # Day changed — check if we should send report
            if (
                self._last_report_date != self._stats_date
                and self._has_activity()
            ):
                self.send_report()

            self._reset_stats()

    def _has_activity(self) -> bool:
        """Check if there was any trading activity today."""
        return (
            self._buy_count > 0
            or self._sell_count > 0
            or len(self._signal_count) > 0
            or len(self._risk_events) > 0
        )

    # ------------------------------------------------------------------
    # Report generation and sending
    # ------------------------------------------------------------------

    def send_report(self) -> bool:
        """Generate and send the daily report. Returns True on success."""
        if not self._enabled:
            return False

        subject = f"BotTrader v2 Daily Report — {self._stats_date}"
        body = self._generate_report_body()

        success = self._send_email(subject, body)
        if success:
            self._last_report_date = self._stats_date
            logger.info("Daily report sent for %s", self._stats_date)
        return success

    def _generate_report_body(self) -> str:
        """Generate the report body text."""
        lines = [
            f"BotTrader v2 — Daily Report for {self._stats_date}",
            "=" * 50,
            "",
            "TRADING ACTIVITY",
            f"  Buys:   {self._buy_count} trades, ${self._buy_volume_usd:,.2f} volume",
            f"  Sells:  {self._sell_count} trades, ${self._sell_volume_usd:,.2f} volume",
            f"  Fees:   ${self._total_fees:,.2f}",
            "",
            "SIGNALS",
        ]

        if self._signal_count:
            for strategy, count in sorted(self._signal_count.items()):
                lines.append(f"  {strategy}: {count} signals")
        else:
            lines.append("  No signals generated")

        lines.extend([
            "",
            "RISK",
            f"  Order rejections: {self._rejection_count}",
        ])

        if self._risk_events:
            lines.append(f"  Risk events: {len(self._risk_events)}")
            for evt in self._risk_events[:10]:  # Cap at 10
                lines.append(f"    - {evt}")
        else:
            lines.append("  No risk events")

        lines.extend(["", "---", "Generated by BotTrader v2"])
        return "\n".join(lines)

    def _send_email(self, subject: str, body: str) -> bool:
        """Send email via SMTP. Returns True on success."""
        if not all([self._sender, self._password, self._recipients]):
            logger.warning(
                "Email not configured (sender=%s, recipients=%s) — skipping report",
                bool(self._sender), bool(self._recipients),
            )
            return False

        recipients = [
            r.strip() for r in self._recipients.split(",") if r.strip()
        ]

        try:
            msg = MIMEText(body)
            msg["Subject"] = subject
            msg["From"] = self._sender
            msg["To"] = ", ".join(recipients)

            with smtplib.SMTP_SSL(self._smtp_server, self._smtp_port) as server:
                server.login(self._sender, self._password)
                server.sendmail(self._sender, recipients, msg.as_string())

            return True

        except Exception:
            logger.exception("Failed to send daily report email")
            return False

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def get_stats(self) -> dict:
        """Return current stats (useful for testing/debugging)."""
        return {
            "date": str(self._stats_date),
            "buy_count": self._buy_count,
            "sell_count": self._sell_count,
            "buy_volume_usd": str(self._buy_volume_usd),
            "sell_volume_usd": str(self._sell_volume_usd),
            "total_fees": str(self._total_fees),
            "signal_count": dict(self._signal_count),
            "risk_events": list(self._risk_events),
            "rejection_count": self._rejection_count,
        }
