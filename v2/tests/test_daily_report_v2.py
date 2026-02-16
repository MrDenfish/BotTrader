"""Tests for the daily_report_v2 observer plugin."""

from __future__ import annotations

import json
import tempfile
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from v2.core.types import Side
from v2.plugins.observability.daily_report_v2.models import (
    ComparisonData,
    ExitEventDetail,
    ExitManagerStats,
    PnLSummary,
    PortfolioSnapshot,
    PositionSnapshot,
    ReportData,
    RiskStats,
    SignalStats,
    TradeLogEntry,
    TradeStats,
)


# ============================================================
# Helpers
# ============================================================


def _make_report_data(**overrides) -> ReportData:
    """Build a ReportData with sensible defaults for testing."""
    defaults = dict(
        report_date=date(2026, 2, 10),
        period_hours=24,
        trade_stats=TradeStats(
            buy_count=5,
            sell_count=3,
            buy_volume_usd=Decimal("1500.00"),
            sell_volume_usd=Decimal("900.00"),
            total_fees=Decimal("12.50"),
            symbols_traded=["BTC-USD", "ETH-USD", "SOL-USD"],
        ),
        pnl=PnLSummary(
            realized_pnl=Decimal("42.50"),
            total_fees=Decimal("12.50"),
            net_pnl=Decimal("42.50"),
            win_count=2,
            loss_count=1,
            win_rate=0.667,
            avg_win=Decimal("30.00"),
            avg_loss=Decimal("-17.50"),
            best_trade=("BTC-USD", Decimal("35.00")),
            worst_trade=("SOL-USD", Decimal("-17.50")),
            by_symbol={"BTC-USD": Decimal("35.00"), "ETH-USD": Decimal("25.00"), "SOL-USD": Decimal("-17.50")},
        ),
        positions=[
            PositionSnapshot(symbol="ETH-USD", qty=0.5, avg_entry=2500.0, cost_basis=1250.0),
        ],
        signals=SignalStats(total=20, buy_count=12, sell_count=8, by_symbol={"BTC-USD": {"BUY": 5, "SELL": 3}}),
        risk=RiskStats(vetoes=2, circuit_breaker_trips=0, rejections=1, events=["veto: low score"]),
        comparison=ComparisonData(
            v1_signal_count=18,
            v2_signal_count=20,
            agreement_count=15,
            v1_only_count=3,
            v2_only_count=5,
            agreement_rate=0.75,
            v1_symbols={"BTC-USD", "ETH-USD", "NKN-USD"},
            v2_symbols={"BTC-USD", "ETH-USD", "SOL-USD"},
            symbols_only_v1={"NKN-USD"},
            symbols_only_v2={"SOL-USD"},
            divergences=[{"source": "v1_only", "symbol": "NKN-USD", "action": "BUY", "timestamp": 0}],
        ),
        generated_at=datetime(2026, 2, 11, 8, 0, tzinfo=timezone.utc),
    )
    defaults.update(overrides)
    return ReportData(**defaults)


def _write_jsonl(path: Path, entries: list[dict]) -> None:
    with open(path, "w") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")


# ============================================================
# FIFO P&L Tests
# ============================================================


class TestFifoPnL:
    """Test the FIFO P&L computation."""

    def test_basic_buy_sell(self):
        from v2.plugins.observability.daily_report_v2.collectors.pnl import compute_fifo_pnl

        rows = [
            {"symbol": "BTC-USD", "side": "BUY", "price": 100.0, "qty": 1.0, "fee": 0.6},
            {"symbol": "BTC-USD", "side": "SELL", "price": 110.0, "qty": 1.0, "fee": 0.66},
        ]
        result = compute_fifo_pnl(rows)
        # Gross: (110 - 100) * 1 = 10.  Fees: 0.6 + 0.66 = 1.26
        assert result.win_count == 1
        assert result.loss_count == 0
        expected_pnl = Decimal("10") - Decimal("0.6") - Decimal("0.66")
        assert result.realized_pnl == pytest.approx(expected_pnl, abs=Decimal("0.01"))

    def test_partial_fill(self):
        from v2.plugins.observability.daily_report_v2.collectors.pnl import compute_fifo_pnl

        rows = [
            {"symbol": "ETH-USD", "side": "BUY", "price": 2000.0, "qty": 2.0, "fee": 2.4},
            {"symbol": "ETH-USD", "side": "SELL", "price": 2100.0, "qty": 1.0, "fee": 1.26},
        ]
        result = compute_fifo_pnl(rows)
        # Sell 1 of 2. Buy fee per unit = 2.4/2 = 1.2
        # Gross: (2100 - 2000) * 1 = 100.  Buy fee: 1.2, Sell fee: 1.26
        expected = Decimal("100") - Decimal("1.2") - Decimal("1.26")
        assert result.realized_pnl == pytest.approx(expected, abs=Decimal("0.01"))
        assert result.win_count == 1

    def test_multi_consume(self):
        from v2.plugins.observability.daily_report_v2.collectors.pnl import compute_fifo_pnl

        rows = [
            {"symbol": "SOL-USD", "side": "BUY", "price": 50.0, "qty": 1.0, "fee": 0.3},
            {"symbol": "SOL-USD", "side": "BUY", "price": 55.0, "qty": 1.0, "fee": 0.33},
            {"symbol": "SOL-USD", "side": "SELL", "price": 60.0, "qty": 2.0, "fee": 0.72},
        ]
        result = compute_fifo_pnl(rows)
        # FIFO: sell consumes both buys
        # Trade 1: (60-50)*1 - 0.3 - 0.36 = 10 - 0.66 = 9.34
        # But sell fee per unit = 0.72/2 = 0.36
        # Trade 1: (60-50)*1 = 10 - buy_fee(0.3) - sell_fee(0.36) = 9.34
        # Trade 2: (60-55)*1 = 5 - buy_fee(0.33) - sell_fee(0.36) = 4.31
        # Total: 13.65
        assert result.realized_pnl == pytest.approx(Decimal("9.34") + Decimal("4.31"), abs=Decimal("0.02"))
        assert result.win_count == 1  # Single sell = 1 trade

    def test_loss_trade(self):
        from v2.plugins.observability.daily_report_v2.collectors.pnl import compute_fifo_pnl

        rows = [
            {"symbol": "BTC-USD", "side": "BUY", "price": 100.0, "qty": 1.0, "fee": 0.0},
            {"symbol": "BTC-USD", "side": "SELL", "price": 90.0, "qty": 1.0, "fee": 0.0},
        ]
        result = compute_fifo_pnl(rows)
        assert result.realized_pnl == Decimal("-10")
        assert result.loss_count == 1
        assert result.win_count == 0
        assert result.win_rate == 0.0

    def test_empty_rows(self):
        from v2.plugins.observability.daily_report_v2.collectors.pnl import compute_fifo_pnl

        result = compute_fifo_pnl([])
        assert result.realized_pnl == Decimal("0")
        assert result.win_count == 0
        assert result.loss_count == 0

    def test_multiple_symbols(self):
        from v2.plugins.observability.daily_report_v2.collectors.pnl import compute_fifo_pnl

        rows = [
            {"symbol": "BTC-USD", "side": "BUY", "price": 100.0, "qty": 1.0, "fee": 0.0},
            {"symbol": "ETH-USD", "side": "BUY", "price": 200.0, "qty": 1.0, "fee": 0.0},
            {"symbol": "BTC-USD", "side": "SELL", "price": 120.0, "qty": 1.0, "fee": 0.0},
            {"symbol": "ETH-USD", "side": "SELL", "price": 190.0, "qty": 1.0, "fee": 0.0},
        ]
        result = compute_fifo_pnl(rows)
        assert result.by_symbol["BTC-USD"] == Decimal("20")
        assert result.by_symbol["ETH-USD"] == Decimal("-10")
        assert result.realized_pnl == Decimal("10")


# ============================================================
# Signal Collector Tests
# ============================================================


class TestSignalCollector:
    def test_parse_jsonl(self):
        from v2.plugins.observability.daily_report_v2.collectors.signals import collect_signals

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            now = datetime.now(timezone.utc)
            entries = [
                {"symbol": "BTC-USD", "action": "BUY", "timestamp": now.timestamp()},
                {"symbol": "BTC-USD", "action": "SELL", "timestamp": now.timestamp()},
                {"symbol": "ETH-USD", "action": "BUY", "timestamp": now.timestamp()},
                # Out of range
                {"symbol": "SOL-USD", "action": "BUY", "timestamp": (now - timedelta(days=2)).timestamp()},
            ]
            for e in entries:
                f.write(json.dumps(e) + "\n")
            f.flush()

            start = now - timedelta(hours=1)
            end = now + timedelta(hours=1)
            stats = collect_signals(f.name, start, end)

            assert stats.total == 3
            assert stats.buy_count == 2
            assert stats.sell_count == 1
            assert stats.by_symbol["BTC-USD"]["BUY"] == 1
            assert stats.by_symbol["BTC-USD"]["SELL"] == 1

    def test_missing_file(self):
        from v2.plugins.observability.daily_report_v2.collectors.signals import collect_signals

        stats = collect_signals("/nonexistent/path.jsonl", datetime.now(timezone.utc), datetime.now(timezone.utc))
        assert stats.total == 0


# ============================================================
# Comparison Collector Tests
# ============================================================


class TestComparisonCollector:
    def test_signal_matching(self):
        from v2.plugins.observability.daily_report_v2.collectors.comparison import collect_comparison

        with tempfile.TemporaryDirectory() as tmpdir:
            now = datetime.now(timezone.utc)
            v1_path = str(Path(tmpdir) / "v1.jsonl")
            v2_path = str(Path(tmpdir) / "v2.jsonl")

            # v1: 3 signals
            _write_jsonl(Path(v1_path), [
                {"symbol": "BTC-USD", "action": "BUY", "timestamp": now.timestamp()},
                {"symbol": "ETH-USD", "action": "SELL", "timestamp": now.timestamp()},
                {"symbol": "NKN-USD", "action": "BUY", "timestamp": now.timestamp()},
            ])
            # v2: 2 matching + 1 unique
            _write_jsonl(Path(v2_path), [
                {"symbol": "BTC-USD", "action": "BUY", "timestamp": (now + timedelta(seconds=5)).timestamp()},
                {"symbol": "ETH-USD", "action": "SELL", "timestamp": (now + timedelta(seconds=10)).timestamp()},
                {"symbol": "SOL-USD", "action": "BUY", "timestamp": now.timestamp()},
            ])

            start = now - timedelta(hours=1)
            end = now + timedelta(hours=1)
            result = collect_comparison(v1_path, v2_path, start, end, match_window_seconds=60)

            assert result.agreement_count == 2
            assert result.v1_only_count == 1
            assert result.v2_only_count == 1
            assert "NKN-USD" in result.symbols_only_v1
            assert "SOL-USD" in result.symbols_only_v2

    def test_no_signals(self):
        from v2.plugins.observability.daily_report_v2.collectors.comparison import collect_comparison

        with tempfile.TemporaryDirectory() as tmpdir:
            v1_path = str(Path(tmpdir) / "v1.jsonl")
            v2_path = str(Path(tmpdir) / "v2.jsonl")
            Path(v1_path).touch()
            Path(v2_path).touch()

            now = datetime.now(timezone.utc)
            result = collect_comparison(v1_path, v2_path, now - timedelta(hours=1), now)
            assert result.agreement_count == 0
            assert result.agreement_rate == 0.0


# ============================================================
# Risk Event Accumulator Tests
# ============================================================


class TestRiskEventAccumulator:
    def test_accumulation(self):
        from v2.plugins.observability.daily_report_v2.collectors.risk_events import RiskEventAccumulator

        acc = RiskEventAccumulator()
        acc.record_veto("low score")
        acc.record_veto("red day")
        acc.record_circuit_breaker("5 losses in 30min")
        acc.record_rejection("post_only")

        snap = acc.snapshot()
        assert snap.vetoes == 2
        assert snap.circuit_breaker_trips == 1
        assert snap.rejections == 1
        assert len(snap.events) == 4

    def test_reset(self):
        from v2.plugins.observability.daily_report_v2.collectors.risk_events import RiskEventAccumulator

        acc = RiskEventAccumulator()
        acc.record_veto("test")
        acc.reset()
        snap = acc.snapshot()
        assert snap.vetoes == 0
        assert len(snap.events) == 0


# ============================================================
# HTML Renderer Tests
# ============================================================


class TestHtmlRenderer:
    def test_renders_without_error(self):
        from v2.plugins.observability.daily_report_v2.renderers.html import HtmlRenderer

        data = _make_report_data()
        html = HtmlRenderer().render(data)
        assert isinstance(html, str)
        assert len(html) > 100

    def test_contains_key_sections(self):
        from v2.plugins.observability.daily_report_v2.renderers.html import HtmlRenderer

        data = _make_report_data()
        html = HtmlRenderer().render(data)
        assert "hero-pnl" in html
        assert "P&amp;L by Symbol" in html
        assert "System Health" in html
        assert "v1 vs v2 Comparison" in html

    def test_renders_without_comparison(self):
        from v2.plugins.observability.daily_report_v2.renderers.html import HtmlRenderer

        data = _make_report_data(comparison=None)
        html = HtmlRenderer().render(data)
        assert "hero-pnl" in html
        assert "v1 vs v2 Comparison" not in html


# ============================================================
# Slack Renderer Tests
# ============================================================


class TestSlackRenderer:
    def test_block_structure(self):
        from v2.plugins.observability.daily_report_v2.renderers.slack import SlackRenderer

        data = _make_report_data()
        blocks = SlackRenderer().render(data)
        assert isinstance(blocks, list)
        assert len(blocks) > 0
        assert blocks[0]["type"] == "header"

    def test_without_comparison(self):
        from v2.plugins.observability.daily_report_v2.renderers.slack import SlackRenderer

        data = _make_report_data(comparison=None)
        blocks = SlackRenderer().render(data)
        # Should still render without error
        assert any(b["type"] == "header" for b in blocks)


# ============================================================
# Email Delivery Tests
# ============================================================


class TestEmailDelivery:
    def test_ses_called(self):
        from v2.plugins.observability.daily_report_v2.delivery.email import EmailConfig, send_email

        config = EmailConfig(backend="ses", sender="test@x.com", recipients=["user@x.com"])
        mock_ses = MagicMock()
        mock_boto3 = MagicMock()
        mock_boto3.client.return_value = mock_ses

        with patch.dict("sys.modules", {"boto3": mock_boto3}):
            ok = send_email("Subject", "<h1>Test</h1>", config)

        assert ok is True
        mock_ses.send_raw_email.assert_called_once()

    def test_smtp_called(self):
        from v2.plugins.observability.daily_report_v2.delivery.email import EmailConfig, send_email

        config = EmailConfig(
            backend="smtp", sender="test@x.com", recipients=["user@x.com"],
            smtp_host="smtp.test.com", smtp_port=587, smtp_password_env="TEST_SMTP_PW",
        )
        mock_smtp = MagicMock()
        with patch("v2.plugins.observability.daily_report_v2.delivery.email.smtplib.SMTP", return_value=mock_smtp):
            mock_smtp.__enter__ = MagicMock(return_value=mock_smtp)
            mock_smtp.__exit__ = MagicMock(return_value=False)
            with patch.dict("os.environ", {"TEST_SMTP_PW": "password123"}):
                ok = send_email("Subject", "<h1>Test</h1>", config)

        assert ok is True
        mock_smtp.sendmail.assert_called_once()

    def test_smtp_user_env(self):
        from v2.plugins.observability.daily_report_v2.delivery.email import EmailConfig, send_email

        config = EmailConfig(
            backend="smtp", sender="test@x.com", recipients=["user@x.com"],
            smtp_host="smtp.test.com", smtp_port=587,
            smtp_user_env="TEST_SMTP_USER", smtp_password_env="TEST_SMTP_PW",
        )
        mock_smtp = MagicMock()
        with patch("v2.plugins.observability.daily_report_v2.delivery.email.smtplib.SMTP", return_value=mock_smtp):
            mock_smtp.__enter__ = MagicMock(return_value=mock_smtp)
            mock_smtp.__exit__ = MagicMock(return_value=False)
            with patch.dict("os.environ", {"TEST_SMTP_USER": "envuser", "TEST_SMTP_PW": "password123"}):
                ok = send_email("Subject", "<h1>Test</h1>", config)

        assert ok is True
        mock_smtp.login.assert_called_once_with("envuser", "password123")

    def test_no_config_returns_false(self):
        from v2.plugins.observability.daily_report_v2.delivery.email import EmailConfig, send_email

        config = EmailConfig(sender="", recipients=[])
        ok = send_email("Subject", "<h1>Test</h1>", config)
        assert ok is False


# ============================================================
# Slack Delivery Tests
# ============================================================


class TestSlackDelivery:
    @pytest.mark.asyncio
    async def test_webhook_post(self):
        from v2.plugins.observability.daily_report_v2.delivery.slack import send_slack

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_session = AsyncMock()
        mock_session.post.return_value.__aenter__ = AsyncMock(return_value=mock_resp)

        with patch("v2.plugins.observability.daily_report_v2.delivery.slack.aiohttp.ClientSession") as mock_cls:
            mock_instance = AsyncMock()
            mock_instance.post.return_value = mock_resp
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_instance

            ok = await send_slack([{"type": "header", "text": {"type": "plain_text", "text": "Test"}}], webhook_url="https://hooks.slack.com/test")

        assert ok is True

    @pytest.mark.asyncio
    async def test_no_url_returns_false(self):
        from v2.plugins.observability.daily_report_v2.delivery.slack import send_slack

        with patch.dict("os.environ", {}, clear=True):
            ok = await send_slack([{"type": "header"}], webhook_url_env="NONEXISTENT_ENV")
        assert ok is False


# ============================================================
# Observer Plugin Tests
# ============================================================


class TestDailyReportV2Observer:
    def test_plugin_registered(self):
        from v2.core import registry
        # Force re-registration in case registry.clear() was called by earlier tests
        from v2.plugins.observability.daily_report_v2.observer import DailyReportV2Observer  # noqa: F811
        registry.register("observer", "daily_report_v2", DailyReportV2Observer)
        plugins = registry.list_plugins()
        assert "daily_report_v2" in plugins.get("observer", [])

    def test_risk_event_accumulation(self):
        from v2.core.types import RiskEvent
        from v2.plugins.observability.daily_report_v2 import DailyReportV2Observer

        obs = DailyReportV2Observer()
        obs.on_event(RiskEvent(event_type="veto", reason="low score"))
        obs.on_event(RiskEvent(event_type="veto", reason="red day"))

        stats = obs._risk_acc.snapshot()
        assert stats.vetoes == 2

    def test_no_report_without_activity(self):
        from v2.plugins.observability.daily_report_v2 import DailyReportV2Observer

        obs = DailyReportV2Observer()
        assert obs._has_activity is False

    def test_period_slot_alignment(self):
        from v2.plugins.observability.daily_report_v2 import DailyReportV2Observer

        obs = DailyReportV2Observer(report_interval_hours=4)
        slot = obs._current_period_slot()
        # Slot should be aligned to a 4-hour boundary
        assert slot.hour % 4 == 0
        assert slot.minute == 0
        assert slot.second == 0
        assert slot.microsecond == 0

    def test_ticker_updates_portfolio_tracker(self):
        from v2.core.types import TickerEvent
        from v2.plugins.observability.daily_report_v2 import DailyReportV2Observer

        obs = DailyReportV2Observer(report_interval_hours=4)
        now = datetime.now(timezone.utc)
        obs.on_event(TickerEvent(symbol="BTC-USD", price=50000.0, timestamp=now))
        assert obs._portfolio_tracker._last_prices.get("BTC-USD") == 50000.0

    def test_fill_updates_portfolio_tracker(self):
        from v2.core.types import Fill, FillEvent, Side
        from v2.plugins.observability.daily_report_v2 import DailyReportV2Observer

        obs = DailyReportV2Observer(report_interval_hours=4, initial_balance_usd=10000)
        now = datetime.now(timezone.utc)
        fill = Fill(
            fill_id="test-1", order_id="ord-1",
            symbol="ETH-USD", side=Side.BUY,
            price=Decimal("2000"), qty=Decimal("0.5"),
            fee=Decimal("1.2"), fee_currency="USD", is_maker=True,
            timestamp=now,
        )
        obs.on_event(FillEvent(fill=fill))
        assert obs._has_activity is True
        # Portfolio should reflect the buy
        assert Decimal("0.5") == obs._portfolio_tracker._positions.get("ETH-USD", Decimal("0"))

    def test_exit_event_routed_to_accumulator(self):
        from v2.core.types import RiskEvent
        from v2.plugins.observability.daily_report_v2 import DailyReportV2Observer

        obs = DailyReportV2Observer(report_interval_hours=4)
        obs.on_event(RiskEvent(
            event_type="exit_triggered",
            reason="hard_stop",
            metadata={"symbol": "BTC-USD", "price": 45000, "pnl_pct": -4.5},
        ))
        snap = obs._exit_acc.snapshot()
        assert snap.hard_stops == 1
        assert snap.total_exits == 1
        assert len(snap.events) == 1

    def test_trailing_activation_routed(self):
        from v2.core.types import RiskEvent
        from v2.plugins.observability.daily_report_v2 import DailyReportV2Observer

        obs = DailyReportV2Observer(report_interval_hours=4)
        obs.on_event(RiskEvent(
            event_type="trailing_activated",
            reason="trailing_stop",
            metadata={"symbol": "SOL-USD", "price": 120, "pnl_pct": 3.5},
        ))
        snap = obs._exit_acc.snapshot()
        assert snap.trailing_activations == 1
        assert snap.total_exits == 0  # activation != exit


# ============================================================
# Exit Event Accumulator Tests
# ============================================================


class TestExitEventAccumulator:
    def test_record_exit_types(self):
        from v2.core.types import RiskEvent
        from v2.plugins.observability.daily_report_v2.collectors.exit_events import ExitEventAccumulator

        acc = ExitEventAccumulator()
        acc.record_exit(RiskEvent(event_type="exit_triggered", reason="hard_stop",
                                   metadata={"symbol": "BTC-USD", "price": 45000, "pnl_pct": -4.5}))
        acc.record_exit(RiskEvent(event_type="exit_triggered", reason="soft_stop",
                                   metadata={"symbol": "ETH-USD", "price": 2000, "pnl_pct": -3.0}))
        acc.record_exit(RiskEvent(event_type="exit_triggered", reason="trailing_stop",
                                   metadata={"symbol": "SOL-USD", "price": 115, "pnl_pct": 2.5, "peak_price": 120}))

        snap = acc.snapshot()
        assert snap.hard_stops == 1
        assert snap.soft_stops == 1
        assert snap.trailing_stops == 1
        assert snap.total_exits == 3
        assert len(snap.events) == 3

    def test_trailing_activation(self):
        from v2.core.types import RiskEvent
        from v2.plugins.observability.daily_report_v2.collectors.exit_events import ExitEventAccumulator

        acc = ExitEventAccumulator()
        acc.record_trailing_activation(RiskEvent(
            event_type="trailing_activated", reason="trailing_stop",
            metadata={"symbol": "BTC-USD", "price": 52000, "pnl_pct": 3.5},
        ))
        snap = acc.snapshot()
        assert snap.trailing_activations == 1
        assert snap.total_exits == 0

    def test_reset(self):
        from v2.core.types import RiskEvent
        from v2.plugins.observability.daily_report_v2.collectors.exit_events import ExitEventAccumulator

        acc = ExitEventAccumulator()
        acc.record_exit(RiskEvent(event_type="exit_triggered", reason="hard_stop",
                                   metadata={"symbol": "X", "price": 1, "pnl_pct": -5}))
        acc.record_trailing_activation(RiskEvent(
            event_type="trailing_activated", reason="trailing_stop", metadata={},
        ))
        acc.reset()
        snap = acc.snapshot()
        assert snap.hard_stops == 0
        assert snap.trailing_activations == 0
        assert len(snap.events) == 0

    def test_max_events_cap(self):
        from v2.plugins.observability.daily_report_v2.collectors.exit_events import ExitEventAccumulator, MAX_EVENTS
        from v2.core.types import RiskEvent

        acc = ExitEventAccumulator()
        for i in range(MAX_EVENTS + 10):
            acc.record_exit(RiskEvent(
                event_type="exit_triggered", reason="soft_stop",
                metadata={"symbol": f"SYM-{i}", "price": 100, "pnl_pct": -3.0},
            ))
        snap = acc.snapshot()
        assert len(snap.events) == MAX_EVENTS
        assert snap.soft_stops == MAX_EVENTS + 10  # counter still increments


# ============================================================
# Trade Log Collector Tests
# ============================================================


class TestTradeLogCollector:
    def test_fifo_pnl_annotation(self):
        from v2.plugins.observability.daily_report_v2.collectors.trade_log import _annotate_with_pnl

        now = datetime.now(timezone.utc)
        rows = [
            {"symbol": "BTC-USD", "side": "BUY", "price": 50000.0, "qty": 0.1, "fee": 3.0, "is_maker": True, "timestamp": now},
            {"symbol": "BTC-USD", "side": "SELL", "price": 51000.0, "qty": 0.1, "fee": 3.06, "is_maker": True, "timestamp": now},
        ]
        entries = _annotate_with_pnl(rows)
        assert len(entries) == 2
        assert entries[0].realized_pnl is None  # buy has no P&L
        assert entries[1].realized_pnl is not None
        # Gross: (51000 - 50000) * 0.1 = 100. Fees: 3.0 + 3.06 = 6.06. Net: 93.94
        assert abs(entries[1].realized_pnl - 93.94) < 0.01

    def test_multiple_buys_single_sell(self):
        from v2.plugins.observability.daily_report_v2.collectors.trade_log import _annotate_with_pnl

        now = datetime.now(timezone.utc)
        rows = [
            {"symbol": "ETH-USD", "side": "BUY", "price": 2000.0, "qty": 0.5, "fee": 0.6, "is_maker": True, "timestamp": now},
            {"symbol": "ETH-USD", "side": "BUY", "price": 2100.0, "qty": 0.5, "fee": 0.63, "is_maker": True, "timestamp": now},
            {"symbol": "ETH-USD", "side": "SELL", "price": 2200.0, "qty": 1.0, "fee": 1.32, "is_maker": True, "timestamp": now},
        ]
        entries = _annotate_with_pnl(rows)
        assert entries[2].realized_pnl is not None
        # FIFO: first 0.5 bought at 2000, second 0.5 at 2100
        # Gross: (2200-2000)*0.5 + (2200-2100)*0.5 = 100 + 50 = 150
        # Fees: 0.6 + 0.63 + 1.32 = 2.55
        # Net: 147.45
        assert abs(entries[2].realized_pnl - 147.45) < 0.01

    def test_empty_rows(self):
        from v2.plugins.observability.daily_report_v2.collectors.trade_log import _annotate_with_pnl

        entries = _annotate_with_pnl([])
        assert entries == []


# ============================================================
# Portfolio Tracker Tests
# ============================================================


class TestPortfolioTracker:
    def test_initial_snapshot(self):
        from v2.plugins.observability.daily_report_v2.collectors.portfolio_tracker import PortfolioTracker

        pt = PortfolioTracker(initial_balance_usd=10000)
        snap = pt.snapshot()
        assert snap.starting_value == 10000.0
        assert snap.ending_value == 10000.0
        assert snap.cash_balance == 10000.0
        assert snap.positions_value == 0.0

    def test_buy_reduces_cash(self):
        from v2.plugins.observability.daily_report_v2.collectors.portfolio_tracker import PortfolioTracker

        pt = PortfolioTracker(initial_balance_usd=10000)
        pt.on_fill("BTC-USD", Side.BUY, Decimal("50000"), Decimal("0.1"), Decimal("3"))
        snap = pt.snapshot()
        # Cash: 10000 - 5000 - 3 = 4997
        assert abs(snap.cash_balance - 4997.0) < 0.01
        # Position value at fill price: 0.1 * 50000 = 5000
        assert abs(snap.positions_value - 5000.0) < 0.01

    def test_sell_increases_cash(self):
        from v2.plugins.observability.daily_report_v2.collectors.portfolio_tracker import PortfolioTracker

        pt = PortfolioTracker(initial_balance_usd=10000)
        pt.on_fill("BTC-USD", Side.BUY, Decimal("50000"), Decimal("0.1"), Decimal("3"))
        pt.on_fill("BTC-USD", Side.SELL, Decimal("51000"), Decimal("0.1"), Decimal("3.06"))
        snap = pt.snapshot()
        # After sell: position cleared, cash = 10000 - 5000 - 3 + 5100 - 3.06 = 10093.94
        assert abs(snap.cash_balance - 10093.94) < 0.01
        assert snap.positions_value == 0.0

    def test_ticker_updates_mtm(self):
        from v2.plugins.observability.daily_report_v2.collectors.portfolio_tracker import PortfolioTracker

        pt = PortfolioTracker(initial_balance_usd=10000)
        pt.on_fill("BTC-USD", Side.BUY, Decimal("50000"), Decimal("0.1"), Decimal("0"))
        pt.on_ticker("BTC-USD", 55000.0)
        snap = pt.snapshot()
        # Position: 0.1 * 55000 = 5500.  Cash: 10000 - 5000 = 5000.  Total: 10500
        assert abs(snap.ending_value - 10500.0) < 0.01

    def test_watermarks(self):
        from v2.plugins.observability.daily_report_v2.collectors.portfolio_tracker import PortfolioTracker

        pt = PortfolioTracker(initial_balance_usd=10000)
        pt.on_fill("BTC-USD", Side.BUY, Decimal("50000"), Decimal("0.1"), Decimal("0"))
        # High: ticker goes up
        pt.on_ticker("BTC-USD", 55000.0)
        pt._update_watermarks()
        # Low: ticker drops
        pt.on_ticker("BTC-USD", 45000.0)
        pt._update_watermarks()

        snap = pt.snapshot()
        # High: 5000 cash + 0.1*55000 = 10500
        assert abs(snap.high_watermark - 10500.0) < 0.01
        # Low: 5000 cash + 0.1*45000 = 9500
        assert abs(snap.low_watermark - 9500.0) < 0.01

    def test_start_new_period_resets_watermarks(self):
        from v2.plugins.observability.daily_report_v2.collectors.portfolio_tracker import PortfolioTracker

        pt = PortfolioTracker(initial_balance_usd=10000)
        pt.on_fill("BTC-USD", Side.BUY, Decimal("50000"), Decimal("0.1"), Decimal("0"))
        pt.on_ticker("BTC-USD", 55000.0)
        pt._update_watermarks()
        pt.start_new_period()

        snap = pt.snapshot()
        # After reset, high/low/start should equal current value
        assert snap.starting_value == snap.ending_value
        assert snap.high_watermark == snap.ending_value
        assert snap.low_watermark == snap.ending_value


# ============================================================
# New HTML Renderer Section Tests
# ============================================================


class TestHtmlRendererNewSections:
    def _make_data_with_new_sections(self):
        return _make_report_data(
            period_start=datetime(2026, 2, 10, 4, 0, tzinfo=timezone.utc),
            period_end=datetime(2026, 2, 10, 8, 0, tzinfo=timezone.utc),
            period_hours=4,
            portfolio=PortfolioSnapshot(
                starting_value=10000, ending_value=10150,
                high_watermark=10200, low_watermark=9950,
                cash_balance=5000, positions_value=5150,
            ),
            trade_log=[
                TradeLogEntry(
                    timestamp=datetime(2026, 2, 10, 5, 30, tzinfo=timezone.utc),
                    symbol="BTC-USD", side="BUY", price=50000.0, qty=0.1,
                    notional=5000.0, fee=3.0, is_maker=True,
                ),
                TradeLogEntry(
                    timestamp=datetime(2026, 2, 10, 6, 15, tzinfo=timezone.utc),
                    symbol="BTC-USD", side="SELL", price=51000.0, qty=0.1,
                    notional=5100.0, fee=3.06, is_maker=True, realized_pnl=93.94,
                ),
            ],
            exit_manager=ExitManagerStats(
                hard_stops=1, soft_stops=0, trailing_stops=1,
                trailing_activations=2, total_exits=2,
                events=[
                    ExitEventDetail(
                        timestamp=datetime(2026, 2, 10, 7, 0, tzinfo=timezone.utc),
                        symbol="BTC-USD", reason="trailing_stop",
                        price=51000.0, pnl_pct=2.0, peak_price=52000.0,
                    ),
                ],
            ),
        )

    def test_portfolio_section_rendered(self):
        from v2.plugins.observability.daily_report_v2.renderers.html import HtmlRenderer

        data = self._make_data_with_new_sections()
        html = HtmlRenderer().render(data)
        assert "Portfolio" in html
        assert "Starting" in html
        assert "Ending" in html
        assert "High / Low" in html

    def test_trade_log_section_rendered(self):
        from v2.plugins.observability.daily_report_v2.renderers.html import HtmlRenderer

        data = self._make_data_with_new_sections()
        html = HtmlRenderer().render(data)
        assert "Trade Log" in html
        assert "side-buy" in html
        assert "side-sell" in html

    def test_exit_manager_section_rendered(self):
        from v2.plugins.observability.daily_report_v2.renderers.html import HtmlRenderer

        data = self._make_data_with_new_sections()
        html = HtmlRenderer().render(data)
        assert "Exit Manager" in html
        assert "Hard stops" in html
        assert "Trailing stops" in html
        assert "trailing_stop" in html

    def test_period_aware_header(self):
        from v2.plugins.observability.daily_report_v2.renderers.html import HtmlRenderer

        data = self._make_data_with_new_sections()
        html = HtmlRenderer().render(data)
        assert "4h Report" in html

    def test_daily_header_for_24h(self):
        from v2.plugins.observability.daily_report_v2.renderers.html import HtmlRenderer

        data = _make_report_data(period_hours=24)
        html = HtmlRenderer().render(data)
        assert "Daily Report" in html

    def test_sections_hidden_when_none(self):
        from v2.plugins.observability.daily_report_v2.renderers.html import HtmlRenderer

        data = _make_report_data(portfolio=None, trade_log=None, exit_manager=None)
        html = HtmlRenderer().render(data)
        assert "Portfolio" not in html
        assert "Trade Log" not in html
        assert "Exit Manager" not in html


# ============================================================
# New Slack Renderer Section Tests
# ============================================================


class TestSlackRendererNewSections:
    def _make_data_with_new_sections(self):
        return _make_report_data(
            period_start=datetime(2026, 2, 10, 4, 0, tzinfo=timezone.utc),
            period_end=datetime(2026, 2, 10, 8, 0, tzinfo=timezone.utc),
            period_hours=4,
            portfolio=PortfolioSnapshot(
                starting_value=10000, ending_value=10150,
                high_watermark=10200, low_watermark=9950,
                cash_balance=5000, positions_value=5150,
            ),
            trade_log=[
                TradeLogEntry(
                    timestamp=datetime(2026, 2, 10, 5, 30, tzinfo=timezone.utc),
                    symbol="BTC-USD", side="BUY", price=50000.0, qty=0.1,
                    notional=5000.0, fee=3.0, is_maker=True,
                ),
            ],
            exit_manager=ExitManagerStats(
                hard_stops=1, soft_stops=0, trailing_stops=0,
                trailing_activations=1, total_exits=1,
                events=[
                    ExitEventDetail(
                        timestamp=datetime(2026, 2, 10, 7, 0, tzinfo=timezone.utc),
                        symbol="BTC-USD", reason="hard_stop",
                        price=48000.0, pnl_pct=-4.5,
                    ),
                ],
            ),
        )

    def test_period_aware_header(self):
        from v2.plugins.observability.daily_report_v2.renderers.slack import SlackRenderer

        data = self._make_data_with_new_sections()
        blocks = SlackRenderer().render(data)
        header_text = blocks[0]["text"]["text"]
        assert "4h Report" in header_text
        assert "04:00" in header_text
        assert "08:00" in header_text

    def test_portfolio_block(self):
        from v2.plugins.observability.daily_report_v2.renderers.slack import SlackRenderer

        data = self._make_data_with_new_sections()
        blocks = SlackRenderer().render(data)
        all_text = " ".join(b.get("text", {}).get("text", "") for b in blocks if b.get("type") == "section")
        assert "Portfolio" in all_text
        assert "Cash" in all_text

    def test_trade_log_block(self):
        from v2.plugins.observability.daily_report_v2.renderers.slack import SlackRenderer

        data = self._make_data_with_new_sections()
        blocks = SlackRenderer().render(data)
        all_text = " ".join(b.get("text", {}).get("text", "") for b in blocks if b.get("type") == "section")
        assert "Trade Log" in all_text
        assert "BTC-USD" in all_text

    def test_exit_manager_block(self):
        from v2.plugins.observability.daily_report_v2.renderers.slack import SlackRenderer

        data = self._make_data_with_new_sections()
        blocks = SlackRenderer().render(data)
        all_text = " ".join(b.get("text", {}).get("text", "") for b in blocks if b.get("type") == "section")
        assert "Exit Manager" in all_text
        assert "Hard stops" in all_text

    def test_no_new_sections_when_none(self):
        from v2.plugins.observability.daily_report_v2.renderers.slack import SlackRenderer

        data = _make_report_data(portfolio=None, trade_log=None, exit_manager=None)
        blocks = SlackRenderer().render(data)
        all_text = " ".join(b.get("text", {}).get("text", "") for b in blocks if b.get("type") == "section")
        assert "Portfolio" not in all_text
        assert "Trade Log" not in all_text
        assert "Exit Manager" not in all_text
