"""DailyReportV2Observer — the main observer plugin class.

Separated from __init__.py so the registry's ``discover_plugins()`` can
import it (the discovery loop skips ``__init__`` modules).

Supports configurable reporting intervals (default: 4 hours).
Period boundaries are aligned to UTC slots (e.g., 00:00, 04:00, 08:00, ...).
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from v2.core import registry
from v2.core.interfaces import Observer
from v2.core.types import FillEvent, OrderEvent, RiskEvent, SignalEvent, Side, TickerEvent

from .collectors.comparison import collect_comparison
from .collectors.exit_events import ExitEventAccumulator
from .collectors.portfolio_tracker import PortfolioTracker
from .collectors.positions import collect_positions
from .collectors.pnl import collect_pnl
from .collectors.risk_events import RiskEventAccumulator
from .collectors.signals import collect_signals
from .collectors.trade_log import collect_trade_log
from .collectors.trade_stats import collect_trade_stats
from .delivery.email import EmailConfig, send_email
from .delivery.slack import send_slack
from .models import ReportData, RiskStats, SignalStats
from .renderers.html import HtmlRenderer
from .renderers.slack import SlackRenderer

logger = logging.getLogger(__name__)


@registry.plugin("observer", "daily_report_v2")
class DailyReportV2Observer(Observer):
    """Full-featured periodic report observer with v1/v2 comparison."""

    name = "daily_report_v2"

    def __init__(self, event_bus=None, **kwargs: Any) -> None:
        self._bus = event_bus

        # DB config (lazy pool)
        self._dsn_env = kwargs.get("dsn_env", "DATABASE_URL")
        self._pool = None  # asyncpg.Pool, created lazily

        # Report timing — period-based (default 4h)
        self._report_interval_hours = int(kwargs.get("report_interval_hours", 0))
        if not self._report_interval_hours:
            # Backward compat: report_hour_utc implies 24h interval
            if "report_hour_utc" in kwargs:
                self._report_interval_hours = 24
            else:
                self._report_interval_hours = 4

        self._current_period_start = self._current_period_slot()
        self._last_report_slot: datetime | None = None

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

        # Exchange name for DB filtering (auto-wired by app.py)
        self._exchange_name: str = kwargs.get("exchange_name", "")

        # In-memory accumulators
        self._risk_acc = RiskEventAccumulator()
        self._exit_acc = ExitEventAccumulator()
        self._portfolio_tracker = PortfolioTracker(
            initial_balance_usd=float(kwargs.get("initial_balance_usd", 10_000)),
        )
        self._portfolio_hydrated = False
        self._has_activity = False

        # Renderers
        self._html_renderer = HtmlRenderer()
        self._slack_renderer = SlackRenderer()

    # ------------------------------------------------------------------
    # Observer interface
    # ------------------------------------------------------------------

    def on_event(self, event: Any) -> None:
        """Process events: accumulate stats + check period boundaries."""
        self._check_period_boundary()

        if isinstance(event, FillEvent):
            self._has_activity = True
            fill = event.fill
            self._portfolio_tracker.on_fill(
                fill.symbol, fill.side, fill.price, fill.qty, fill.fee,
            )
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
            elif event.order.status == OrderStatus.CANCELLED:
                reason = event.order.metadata.get("reason", "")
                if reason == "stale_order_cancelled":
                    self._has_activity = True
                    self._risk_acc.record_stale_cancellation(event.order.symbol)
        elif isinstance(event, TickerEvent):
            self._portfolio_tracker.on_ticker(event.symbol, event.price)

    def _on_risk(self, event: RiskEvent) -> None:
        etype = event.event_type
        reason = event.reason
        if etype == "exit_triggered":
            self._exit_acc.record_exit(event)
        elif etype == "trailing_activated":
            self._exit_acc.record_trailing_activation(event)
        elif etype == "veto":
            self._risk_acc.record_veto(reason)
        elif etype == "circuit_breaker":
            self._risk_acc.record_circuit_breaker(reason)
        elif etype == "signal_vetoed" and "performance_filter" in reason:
            self._risk_acc.record_performance_veto(reason)
        else:
            self._risk_acc.record_veto(f"{etype}: {reason}")

    # ------------------------------------------------------------------
    # Period boundary detection
    # ------------------------------------------------------------------

    def _current_period_slot(self) -> datetime:
        """Return the start of the current period slot (floor to interval)."""
        now = datetime.now(timezone.utc)
        hour_slot = (now.hour // self._report_interval_hours) * self._report_interval_hours
        return now.replace(hour=hour_slot, minute=0, second=0, microsecond=0)

    def _check_period_boundary(self) -> None:
        """Check if we've crossed into a new period and fire report if so."""
        current_slot = self._current_period_slot()
        if current_slot != self._current_period_start:
            # Capture snapshots BEFORE resetting (async report runs later)
            portfolio_snap = self._portfolio_tracker.snapshot()
            risk_snap = self._risk_acc.snapshot()
            exit_snap = self._exit_acc.snapshot()

            if self._last_report_slot != self._current_period_start and self._has_activity:
                # Fire report for the period that just ended
                period_start = self._current_period_start
                asyncio.ensure_future(self._generate_and_send_period(
                    period_start,
                    portfolio_snapshot=portfolio_snap,
                    risk_snapshot=risk_snap,
                    exit_snapshot=exit_snap,
                ))

            # Advance to new period
            self._current_period_start = current_slot
            self._risk_acc.reset()
            self._exit_acc.reset()
            self._portfolio_tracker.start_new_period()
            self._has_activity = False

    # ------------------------------------------------------------------
    # Report generation
    # ------------------------------------------------------------------

    async def _ensure_pool(self):
        """Lazy DB pool creation + one-time portfolio hydration."""
        if self._pool is None:
            import asyncpg
            dsn = os.environ.get(self._dsn_env, "")
            if not dsn:
                logger.warning("DATABASE_URL not set (env: %s) — DB collectors will be empty", self._dsn_env)
                return
            self._pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3)

        if not self._portfolio_hydrated and self._pool:
            await self._hydrate_portfolio()

    async def _hydrate_portfolio(self) -> None:
        """Replay historical v2_fills to hydrate the portfolio tracker."""
        try:
            params: list = []
            exchange_clause = ""
            if self._exchange_name:
                params.append(self._exchange_name)
                exchange_clause = f" WHERE exchange = ${len(params)}"
            rows = await self._pool.fetch(
                f"SELECT symbol, side, price, qty, fee FROM v2_fills{exchange_clause} ORDER BY timestamp ASC",
                *params,
            )
            if rows:
                self._portfolio_tracker.replay_fills(rows)
                logger.info("Portfolio tracker hydrated from %d historical fills (exchange=%s)",
                            len(rows), self._exchange_name or "all")
            else:
                logger.info("Portfolio tracker: no historical fills to replay")
        except Exception:
            logger.exception("Failed to hydrate portfolio tracker from DB")
        self._portfolio_hydrated = True

    async def generate_report(
        self,
        report_date=None,
        period_hours: int | None = None,
        period_start: datetime | None = None,
        period_end: datetime | None = None,
        *,
        portfolio_snapshot=None,
        risk_snapshot=None,
        exit_snapshot=None,
    ) -> ReportData:
        """Collect all metrics and assemble a ReportData."""
        await self._ensure_pool()

        if period_hours is None:
            period_hours = self._report_interval_hours

        if period_start is not None and period_end is not None:
            start = period_start
            end = period_end
        else:
            # Fallback: date-based
            from datetime import date as date_type
            if report_date is None:
                report_date = (datetime.now(timezone.utc) - timedelta(hours=1)).date()
            start = datetime.combine(report_date, datetime.min.time(), tzinfo=timezone.utc)
            end = start + timedelta(hours=period_hours)

        if report_date is None:
            report_date = start.date()

        # Collectors that need the DB
        from .models import TradeStats, PnLSummary

        if self._pool:
            ex = self._exchange_name
            trade_stats = await collect_trade_stats(self._pool, start, end, exchange=ex)
            pnl = await collect_pnl(self._pool, start, end, exchange=ex)
            positions = await collect_positions(self._pool, exchange=ex)
            trade_log = await collect_trade_log(self._pool, start, end, exchange=ex)
        else:
            trade_stats = TradeStats()
            pnl = PnLSummary()
            positions = []
            trade_log = []

        # Signal stats (JSONL)
        signals = collect_signals(self._v2_signal_log, start, end)

        # Risk stats (use pre-captured snapshot if available)
        risk = risk_snapshot if risk_snapshot is not None else self._risk_acc.snapshot()

        # Exit manager stats
        exit_manager = exit_snapshot if exit_snapshot is not None else self._exit_acc.snapshot()

        # Portfolio snapshot
        portfolio = portfolio_snapshot if portfolio_snapshot is not None else self._portfolio_tracker.snapshot()

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
            period_start=start,
            period_end=end,
            trade_stats=trade_stats,
            pnl=pnl,
            positions=positions,
            signals=signals,
            risk=risk,
            exit_manager=exit_manager if exit_manager.total_exits > 0 or exit_manager.trailing_activations > 0 or exit_manager.signal_exits > 0 else None,
            trade_log=trade_log if trade_log else None,
            portfolio=portfolio,
            comparison=comparison,
            generated_at=datetime.now(timezone.utc),
        )

    async def _generate_and_send_period(
        self,
        period_start: datetime,
        *,
        portfolio_snapshot=None,
        risk_snapshot=None,
        exit_snapshot=None,
    ) -> None:
        """Generate and deliver report for a completed period."""
        period_end = period_start + timedelta(hours=self._report_interval_hours)
        try:
            data = await self.generate_report(
                period_hours=self._report_interval_hours,
                period_start=period_start,
                period_end=period_end,
                portfolio_snapshot=portfolio_snapshot,
                risk_snapshot=risk_snapshot,
                exit_snapshot=exit_snapshot,
            )
            self._last_report_slot = period_start

            # HTML render + email
            html = self._html_renderer.render(data)
            ex_label = f" [{self._exchange_name}]" if self._exchange_name else ""
            if self._report_interval_hours >= 24:
                subject = f"BotTrader v2{ex_label} Daily Report — {data.report_date}"
            else:
                subject = (
                    f"BotTrader v2{ex_label} {self._report_interval_hours}h Report — "
                    f"{data.report_date} {period_start.strftime('%H:%M')}-{period_end.strftime('%H:%M')} UTC"
                )

            if self._email_config.sender and self._email_config.recipients:
                send_email(subject, html, self._email_config)

            # Slack render + webhook
            blocks = self._slack_renderer.render(data)
            await send_slack(blocks, webhook_url_env=self._slack_webhook_url_env)

            logger.info(
                "Report generated and sent for %s (%s-%s UTC)",
                data.report_date,
                period_start.strftime("%H:%M"),
                period_end.strftime("%H:%M"),
            )

        except Exception:
            logger.exception(
                "Failed to generate/send report for %s-%s",
                period_start, period_end,
            )
