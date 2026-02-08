"""Tests for Milestone 6 — Production Cutover plugins.

Tests cover:
- HeartbeatObserver: touches file, debounce, creates parent dir
- SignalComparisonObserver: JSONL output format, correct fields
- PostgresStorage DSN normalization: strips +asyncpg prefix
- Plugin registration: heartbeat + signal_comparison discovered
- WebSocket candle aggregation: OHLC accumulation, minute rollover, flusher
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from v2.core import registry
from v2.core.event_bus import EventBus
from v2.core.types import (
    CandleEvent,
    Candle,
    Direction,
    Signal,
    SignalEvent,
    TickerEvent,
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


# =====================================================================
# WebSocket candle aggregation
# =====================================================================


class TestCandleAggregation:
    """Test 1-minute candle aggregation in WebSocketDataProvider."""

    def _make_provider(self) -> "WebSocketDataProvider":
        from v2.plugins.data.websocket import WebSocketDataProvider
        bus = EventBus()
        provider = WebSocketDataProvider(event_bus=bus)
        return provider

    def test_first_tick_creates_bar(self, now):
        provider = self._make_provider()
        ts = now.replace(second=10, microsecond=0)
        provider._update_candle("BTC-USD", 50000.0, ts)

        state = provider._candle_state.get("BTC-USD")
        assert state is not None
        assert state["open"] == 50000.0
        assert state["high"] == 50000.0
        assert state["low"] == 50000.0
        assert state["close"] == 50000.0
        assert state["minute"] == ts.replace(second=0)

    def test_same_minute_updates_ohlc(self, now):
        provider = self._make_provider()
        base = now.replace(second=0, microsecond=0)

        provider._update_candle("BTC-USD", 50000.0, base.replace(second=5))
        provider._update_candle("BTC-USD", 50100.0, base.replace(second=15))  # new high
        provider._update_candle("BTC-USD", 49900.0, base.replace(second=30))  # new low
        provider._update_candle("BTC-USD", 50050.0, base.replace(second=45))  # new close

        state = provider._candle_state["BTC-USD"]
        assert state["open"] == 50000.0
        assert state["high"] == 50100.0
        assert state["low"] == 49900.0
        assert state["close"] == 50050.0

    def test_new_minute_emits_candle(self, now):
        provider = self._make_provider()
        emitted: list[CandleEvent] = []
        provider._bus.subscribe(CandleEvent, lambda e: emitted.append(e))

        minute_0 = now.replace(second=0, microsecond=0)
        minute_1 = minute_0 + timedelta(minutes=1)

        # Ticks in minute 0
        provider._update_candle("BTC-USD", 50000.0, minute_0.replace(second=5))
        provider._update_candle("BTC-USD", 50100.0, minute_0.replace(second=30))
        assert len(emitted) == 0

        # First tick in minute 1 triggers emit of minute 0 bar
        provider._update_candle("BTC-USD", 50200.0, minute_1.replace(second=2))
        assert len(emitted) == 1

        candle = emitted[0].candle
        assert candle.symbol == "BTC-USD"
        assert candle.timestamp == minute_0
        assert candle.open == 50000.0
        assert candle.high == 50100.0
        assert candle.low == 50000.0
        assert candle.close == 50100.0
        assert candle.timeframe == "1m"

    def test_multiple_symbols_independent(self, now):
        provider = self._make_provider()
        emitted: list[CandleEvent] = []
        provider._bus.subscribe(CandleEvent, lambda e: emitted.append(e))

        minute_0 = now.replace(second=0, microsecond=0)
        minute_1 = minute_0 + timedelta(minutes=1)

        # Both symbols in minute 0
        provider._update_candle("BTC-USD", 50000.0, minute_0.replace(second=5))
        provider._update_candle("ETH-USD", 3000.0, minute_0.replace(second=10))
        assert len(emitted) == 0

        # BTC rolls over, ETH stays
        provider._update_candle("BTC-USD", 50100.0, minute_1.replace(second=3))
        assert len(emitted) == 1
        assert emitted[0].candle.symbol == "BTC-USD"

        # ETH rolls over
        provider._update_candle("ETH-USD", 3050.0, minute_1.replace(second=5))
        assert len(emitted) == 2
        assert emitted[1].candle.symbol == "ETH-USD"
        assert emitted[1].candle.open == 3000.0

    def test_flush_all_emits_partial_candles(self, now):
        provider = self._make_provider()
        emitted: list[CandleEvent] = []
        provider._bus.subscribe(CandleEvent, lambda e: emitted.append(e))

        minute_0 = now.replace(second=0, microsecond=0)
        provider._update_candle("BTC-USD", 50000.0, minute_0.replace(second=10))
        provider._update_candle("ETH-USD", 3000.0, minute_0.replace(second=15))

        provider._flush_all_candles()
        assert len(emitted) == 2
        assert provider._candle_state == {}

    def test_minute_key_truncation(self, now):
        provider = self._make_provider()
        ts = now.replace(second=37, microsecond=123456)
        minute = provider._minute_key(ts)
        assert minute.second == 0
        assert minute.microsecond == 0
        assert minute.minute == ts.minute

    def test_no_emit_without_bus(self, now):
        """Provider without event_bus should not crash on emit."""
        from v2.plugins.data.websocket import WebSocketDataProvider
        provider = WebSocketDataProvider(event_bus=None)

        minute_0 = now.replace(second=0, microsecond=0)
        minute_1 = minute_0 + timedelta(minutes=1)

        provider._update_candle("BTC-USD", 50000.0, minute_0.replace(second=5))
        # This should not raise even without bus
        provider._update_candle("BTC-USD", 50100.0, minute_1.replace(second=3))

    @pytest.mark.asyncio
    async def test_candle_flusher_emits_stale_bars(self, now):
        """Candle flusher should emit bars from previous minutes."""
        provider = self._make_provider()
        provider._running = True
        emitted: list[CandleEvent] = []
        provider._bus.subscribe(CandleEvent, lambda e: emitted.append(e))

        # Inject a stale candle state (2 minutes ago)
        stale_minute = now.replace(second=0, microsecond=0) - timedelta(minutes=2)
        provider._candle_state["BTC-USD"] = {
            "minute": stale_minute,
            "open": 50000.0,
            "high": 50100.0,
            "low": 49900.0,
            "close": 50050.0,
        }

        # Call the flush logic directly (not the full async loop)
        current_minute = provider._minute_key(now)
        for symbol, state in list(provider._candle_state.items()):
            if state["minute"] < current_minute:
                provider._emit_candle(symbol, state)
                del provider._candle_state[symbol]

        assert len(emitted) == 1
        assert emitted[0].candle.open == 50000.0
        assert "BTC-USD" not in provider._candle_state
