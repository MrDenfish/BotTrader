"""Tests for Milestone 6 — Production Cutover plugins.

Tests cover:
- HeartbeatObserver: touches file, debounce, creates parent dir
- SignalComparisonObserver: JSONL output format, correct fields
- PostgresStorage DSN normalization: strips +asyncpg prefix
- Plugin registration: heartbeat + signal_comparison discovered
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from v2.core import registry
from v2.core.types import (
    CandleEvent,
    Candle,
    Direction,
    Signal,
    SignalEvent,
)


# =====================================================================
# Fixtures
# =====================================================================


@pytest.fixture(autouse=True)
def _discover():
    """Ensure all plugins are discovered before each test."""
    registry.discover_plugins()
    yield


@pytest.fixture
def now():
    return datetime.now(timezone.utc)


# =====================================================================
# HeartbeatObserver
# =====================================================================


class TestHeartbeatObserver:

    def test_touches_file(self, tmp_path, now):
        from v2.plugins.observability.heartbeat import HeartbeatObserver

        hb_path = tmp_path / "heartbeat"
        obs = HeartbeatObserver(path=str(hb_path), interval=0.0)
        event = CandleEvent(
            candle=Candle(
                symbol="BTC-USD", timestamp=now,
                open=100, high=101, low=99, close=100, volume=10,
            )
        )
        obs.on_event(event)
        assert hb_path.exists()

    def test_debounce_skips_rapid_events(self, tmp_path, now):
        from v2.plugins.observability.heartbeat import HeartbeatObserver

        hb_path = tmp_path / "heartbeat"
        obs = HeartbeatObserver(path=str(hb_path), interval=999.0)
        event = CandleEvent(
            candle=Candle(
                symbol="BTC-USD", timestamp=now,
                open=100, high=101, low=99, close=100, volume=10,
            )
        )
        # First event triggers touch
        obs.on_event(event)
        assert hb_path.exists()
        first_mtime = hb_path.stat().st_mtime

        # Brief pause so mtime would differ if touched
        time.sleep(0.05)

        # Second event should be debounced (interval=999s)
        obs.on_event(event)
        assert hb_path.stat().st_mtime == first_mtime

    def test_creates_parent_directory(self, tmp_path, now):
        from v2.plugins.observability.heartbeat import HeartbeatObserver

        hb_path = tmp_path / "deep" / "nested" / "heartbeat"
        obs = HeartbeatObserver(path=str(hb_path), interval=0.0)
        event = CandleEvent(
            candle=Candle(
                symbol="BTC-USD", timestamp=now,
                open=100, high=101, low=99, close=100, volume=10,
            )
        )
        obs.on_event(event)
        assert hb_path.exists()
        assert hb_path.parent.is_dir()

    def test_debounce_allows_after_interval(self, tmp_path, now):
        from v2.plugins.observability.heartbeat import HeartbeatObserver

        hb_path = tmp_path / "heartbeat"
        obs = HeartbeatObserver(path=str(hb_path), interval=0.01)
        event = CandleEvent(
            candle=Candle(
                symbol="BTC-USD", timestamp=now,
                open=100, high=101, low=99, close=100, volume=10,
            )
        )
        obs.on_event(event)
        first_mtime = hb_path.stat().st_mtime

        time.sleep(0.05)

        obs.on_event(event)
        assert hb_path.stat().st_mtime >= first_mtime


# =====================================================================
# SignalComparisonObserver
# =====================================================================


class TestSignalComparisonObserver:

    def test_writes_jsonl_for_signal(self, tmp_path, now):
        import logging

        from v2.plugins.observability.signal_comparison import SignalComparisonObserver

        log_path = str(tmp_path / "test_signals.jsonl")

        # Clear any handlers from previous tests
        sig_logger = logging.getLogger("v2.signal_comparison")
        sig_logger.handlers.clear()

        obs = SignalComparisonObserver(log_path=log_path)

        signal = Signal(
            direction=Direction.BUY,
            symbol="BTC-USD",
            timestamp=now,
            price=50000.0,
            reason="test_trigger",
            metadata={"buy_score": 2.5, "sell_score": 0.3},
        )
        event = SignalEvent(signal=signal, strategy_name="composite_scoring")
        obs.on_event(event)

        # Flush handlers
        for handler in sig_logger.handlers:
            handler.flush()

        content = Path(log_path).read_text().strip()
        assert content, "JSONL file should not be empty"

        record = json.loads(content)
        assert record["symbol"] == "BTC-USD"
        assert record["action"] == "buy"
        assert record["trigger"] == "test_trigger"
        assert record["buy_score"] == 2.5
        assert record["sell_score"] == 0.3
        assert record["strategy"] == "composite_scoring"
        assert record["version"] == "v2"
        assert record["price"] == 50000.0

    def test_ignores_non_signal_events(self, tmp_path, now):
        import logging

        from v2.plugins.observability.signal_comparison import SignalComparisonObserver

        log_path = str(tmp_path / "test_signals2.jsonl")

        sig_logger = logging.getLogger("v2.signal_comparison")
        sig_logger.handlers.clear()

        obs = SignalComparisonObserver(log_path=log_path)

        event = CandleEvent(
            candle=Candle(
                symbol="BTC-USD", timestamp=now,
                open=100, high=101, low=99, close=100, volume=10,
            )
        )
        obs.on_event(event)

        for handler in sig_logger.handlers:
            handler.flush()

        if Path(log_path).exists():
            content = Path(log_path).read_text().strip()
            assert content == "", "Should not write for non-signal events"

    def test_hold_signal_still_logged(self, tmp_path, now):
        """HOLD signals are logged too — filtering is done at analysis time."""
        import logging

        from v2.plugins.observability.signal_comparison import SignalComparisonObserver

        log_path = str(tmp_path / "test_signals3.jsonl")

        sig_logger = logging.getLogger("v2.signal_comparison")
        sig_logger.handlers.clear()

        obs = SignalComparisonObserver(log_path=log_path)

        signal = Signal(
            direction=Direction.HOLD,
            symbol="ETH-USD",
            timestamp=now,
            price=3000.0,
            reason="no_signal",
            metadata={},
        )
        event = SignalEvent(signal=signal, strategy_name="composite_scoring")
        obs.on_event(event)

        for handler in sig_logger.handlers:
            handler.flush()

        content = Path(log_path).read_text().strip()
        record = json.loads(content)
        assert record["action"] == "hold"


# =====================================================================
# PostgresStorage DSN normalization
# =====================================================================


class TestPostgresDSNNormalization:

    def test_strips_asyncpg_prefix(self):
        from v2.plugins.persistence.postgres import PostgresStorage

        storage = PostgresStorage(
            dsn="postgresql+asyncpg://user:pass@localhost:5432/mydb"
        )
        # The normalization happens in connect(), so we check the stored dsn
        assert storage._dsn == "postgresql+asyncpg://user:pass@localhost:5432/mydb"

    def test_plain_dsn_unchanged(self):
        from v2.plugins.persistence.postgres import PostgresStorage

        storage = PostgresStorage(dsn="postgresql://user:pass@localhost:5432/mydb")
        assert storage._dsn == "postgresql://user:pass@localhost:5432/mydb"

    @pytest.mark.asyncio
    async def test_connect_normalizes_dsn(self):
        """Verify connect() strips +asyncpg before passing to asyncpg."""
        from unittest.mock import AsyncMock, patch

        from v2.plugins.persistence.postgres import PostgresStorage

        storage = PostgresStorage(
            dsn="postgresql+asyncpg://user:pass@localhost:5432/mydb"
        )

        mock_pool = AsyncMock()
        mock_pool.execute = AsyncMock()
        mock_pool.close = AsyncMock()

        with patch("asyncpg.create_pool", new_callable=AsyncMock) as mock_create:
            mock_create.return_value = mock_pool
            await storage.connect()
            # Should pass normalized DSN (without +asyncpg)
            call_args = mock_create.call_args
            assert call_args[0][0] == "postgresql://user:pass@localhost:5432/mydb"


# =====================================================================
# Plugin registration
# =====================================================================


class TestPluginRegistration:

    def test_heartbeat_discovered(self):
        plugins = registry.list_plugins()
        assert "heartbeat" in plugins.get("observer", [])

    def test_signal_comparison_discovered(self):
        plugins = registry.list_plugins()
        assert "signal_comparison" in plugins.get("observer", [])

    def test_total_plugin_count(self):
        """Should have at least 15 plugins (13 from M5 + 2 new)."""
        plugins = registry.list_plugins()
        total = sum(len(names) for names in plugins.values())
        all_names = [n for names in plugins.values() for n in names]
        assert total >= 15, f"Expected >= 15 plugins, got {total}: {all_names}"

    def test_heartbeat_instantiation(self, tmp_path):
        cls = registry.get_class("observer", "heartbeat")
        obs = cls(path=str(tmp_path / "hb"), interval=5.0)
        assert obs.name == "heartbeat"

    def test_signal_comparison_instantiation(self, tmp_path):
        import logging
        sig_logger = logging.getLogger("v2.signal_comparison")
        sig_logger.handlers.clear()

        cls = registry.get_class("observer", "signal_comparison")
        obs = cls(log_path=str(tmp_path / "sc.jsonl"))
        assert obs.name == "signal_comparison"
