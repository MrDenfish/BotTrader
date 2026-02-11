"""DailyReportV2Observer — the main observer plugin class.

Separated from __init__.py so the registry's ``discover_plugins()`` can
import it (the discovery loop skips ``__init__`` modules).
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any

from v2.core import registry
from v2.core.interfaces import Observer
from v2.core.types import FillEvent, OrderEvent, RiskEvent, SignalEvent, Side

from .collectors.comparison import collect_comparison
from .collectors.positions import collect_positions
from .collectors.pnl import collect_pnl
from .collectors.risk_events import RiskEventAccumulator
from .collectors.signals import collect_signals
from .collectors.trade_stats import collect_trade_stats
from .delivery.email import EmailConfig, send_email
from .delivery.slack import send_slack
from .models import ReportData, RiskStats, SignalStats
from .renderers.html import HtmlRenderer
from .renderers.slack import SlackRenderer

logger = logging.getLogger(__name__)


@registry.plugin("observer", "daily_report_v2")
class DailyReportV2Observer(Observer):
    """Full-featured daily report observer with v1/v2 comparison."""

    name = "daily_report_v2"

    def __init__(self, event_bus=None, **kwargs: Any) -> None:
        self._bus = event_bus

        # DB config (lazy pool)
        self._dsn_env = kwargs.get("dsn_env", "DATABASE_URL")
        self._pool = None  # asyncpg.Pool, created lazily

        # Report timing
        self._report_hour_utc = int(kwargs.get("report_hour_utc", 8))
        self._last_report_date: date | None = None
        self._stats_date = datetime.now(timezone.utc).date()

        # Email config
        email_cfg = kwargs.get("email", {})
        self._email_config = EmailConfig.from_dict(email_cfg) if email_cfg else EmailConfig()

        # Slack config
        slack_cfg = kwargs.get("slack", {})
        self._slack_webhook_url_env = slack_cfg.get("webhook_url_env", "SLACK_WEBHOOK_URL")

        # Comparison config
        comp_cfg = kwargs.get("comparison", {})
        self._comparison_enabled = comp_cfg.get("enabled", False)
        self._v1_signal_log = comp_cfg.get("v1_signal_log", "/app/logs/scores.jsonl")
        self._v2_signal_log = comp_cfg.get("v2_signal_log", "/app/logs/v2_score_log.jsonl")
        self._match_window = float(comp_cfg.get("match_window_seconds", 60))

        # In-memory accumulators
        self._risk_acc = RiskEventAccumulator()
        self._has_activity = False

        # Renderers
        self._html_renderer = HtmlRenderer()
        self._slack_renderer = SlackRenderer()

    # ------------------------------------------------------------------
    # Observer interface
    # ------------------------------------------------------------------

    def on_event(self, event: Any) -> None:
        """Process events: accumulate risk stats + check date rotation."""
        self._check_date_rotation()

        if isinstance(event, FillEvent):
            self._has_activity = True
        elif isinstance(event, SignalEvent):
            self._has_activity = True
        elif isinstance(event, RiskEvent):
            self._has_activity = True
            self._on_risk(event)
        elif isinstance(event, OrderEvent):
            from v2.core.types import OrderStatus
            if event.order.status == OrderStatus.REJECTED:
                reason = event.order.metadata.get("reject_reason", "unknown")
                self._risk_acc.record_rejection(reason)

    def _on_risk(self, event: RiskEvent) -> None:
        etype = event.event_type
        reason = event.reason
        if etype == "veto":
            self._risk_acc.record_veto(reason)
        elif etype == "circuit_breaker":
            self._risk_acc.record_circuit_breaker(reason)
        else:
            self._risk_acc.record_veto(f"{etype}: {reason}")

    # ------------------------------------------------------------------
    # Date rotation
    # ------------------------------------------------------------------

    def _check_date_rotation(self) -> None:
        now = datetime.now(timezone.utc)
        today = now.date()

        if self._stats_date != today and now.hour >= self._report_hour_utc:
            if self._last_report_date != self._stats_date and self._has_activity:
                # Fire report in background (non-blocking)
                report_date = self._stats_date
                asyncio.ensure_future(self._generate_and_send(report_date))

            self._stats_date = today
            self._risk_acc.reset()
            self._has_activity = False

    # ------------------------------------------------------------------
    # Report generation
    # ------------------------------------------------------------------

    async def _ensure_pool(self):
        """Lazy DB pool creation."""
        if self._pool is None:
            import asyncpg
            dsn = os.environ.get(self._dsn_env, "")
            if not dsn:
                logger.warning("DATABASE_URL not set (env: %s) — DB collectors will be empty", self._dsn_env)
                return
            self._pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3)

    async def generate_report(
        self,
        report_date: date | None = None,
        period_hours: int = 24,
    ) -> ReportData:
        """Collect all metrics and assemble a ReportData."""
        await self._ensure_pool()

        if report_date is None:
            report_date = (datetime.now(timezone.utc) - timedelta(hours=1)).date()

        start = datetime.combine(report_date, datetime.min.time(), tzinfo=timezone.utc)
        end = start + timedelta(hours=period_hours)

        # Collectors that need the DB
        from .models import TradeStats, PnLSummary

        if self._pool:
            trade_stats = await collect_trade_stats(self._pool, start, end)
            pnl = await collect_pnl(self._pool, start, end)
            positions = await collect_positions(self._pool)
        else:
            trade_stats = TradeStats()
            pnl = PnLSummary()
            positions = []

        # Signal stats (JSONL)
        signals = collect_signals(self._v2_signal_log, start, end)

        # Risk stats (in-memory snapshot)
        risk = self._risk_acc.snapshot()

        # Comparison (optional)
        comparison = None
        if self._comparison_enabled:
            try:
                comparison = collect_comparison(
                    self._v1_signal_log,
                    self._v2_signal_log,
                    start,
                    end,
                    self._match_window,
                )
            except Exception:
                logger.exception("Comparison collector failed")

        return ReportData(
            report_date=report_date,
            period_hours=period_hours,
            trade_stats=trade_stats,
            pnl=pnl,
            positions=positions,
            signals=signals,
            risk=risk,
            comparison=comparison,
            generated_at=datetime.now(timezone.utc),
        )

    async def _generate_and_send(self, report_date: date) -> None:
        """Generate report and deliver via all configured channels."""
        try:
            data = await self.generate_report(report_date=report_date)
            self._last_report_date = report_date

            # HTML render + email
            html = self._html_renderer.render(data)
            subject = f"BotTrader v2 Daily Report — {report_date}"

            if self._email_config.sender and self._email_config.recipients:
                send_email(subject, html, self._email_config)

            # Slack render + webhook
            blocks = self._slack_renderer.render(data)
            await send_slack(blocks, webhook_url_env=self._slack_webhook_url_env)

            logger.info("Daily report generated and sent for %s", report_date)

        except Exception:
            logger.exception("Failed to generate/send daily report for %s", report_date)
