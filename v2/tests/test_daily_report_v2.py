"""Tests for the daily_report_v2 observer plugin."""

from __future__ import annotations

import json
import tempfile
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from v2.plugins.observability.daily_report_v2.models import (
    ComparisonData,
    PnLSummary,
    PositionSnapshot,
    ReportData,
    RiskStats,
    SignalStats,
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
        assert "Trading Activity" in html
        assert "P&amp;L" in html or "P&L" in html
        assert "Signals" in html
        assert "Risk Events" in html
        assert "v1 vs v2 Comparison" in html

    def test_renders_without_comparison(self):
        from v2.plugins.observability.daily_report_v2.renderers.html import HtmlRenderer

        data = _make_report_data(comparison=None)
        html = HtmlRenderer().render(data)
        assert "Trading Activity" in html
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
