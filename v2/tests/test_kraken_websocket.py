"""Tests for v2.plugins.data.kraken_websocket."""

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from v2.core.types import CandleEvent, TickerEvent
from v2.plugins.data.kraken_websocket import KrakenWebSocketDataProvider
from v2.utils.symbol_mapper import KrakenSymbolMapper


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _make_provider(**kwargs) -> KrakenWebSocketDataProvider:
    mapper = KrakenSymbolMapper()
    return KrakenWebSocketDataProvider(
        event_bus=MagicMock(),
        mapper=mapper,
        **kwargs,
    )


# ------------------------------------------------------------------
# Message processing
# ------------------------------------------------------------------

class TestProcessMessage:
    def test_ticker_update(self):
        provider = _make_provider()
        msg = json.dumps({
            "channel": "ticker",
            "type": "update",
            "data": [
                {
                    "symbol": "XBT/USD",
                    "last": "50000.5",
                    "bid": "50000.0",
                    "ask": "50001.0",
                    "change_pct": "2.5",
                },
            ],
        })

        provider._process_message(msg)

        # Should publish TickerEvent
        assert provider._bus.publish.called
        event = provider._bus.publish.call_args[0][0]
        assert isinstance(event, TickerEvent)
        assert event.symbol == "BTC-USD"  # Mapped from XBT/USD
        assert event.price == 50000.5
        assert event.bid == 50000.0
        assert event.ask == 50001.0
        assert event.change_24h_pct == 2.5

    def test_ticker_snapshot(self):
        provider = _make_provider()
        msg = json.dumps({
            "channel": "ticker",
            "type": "snapshot",
            "data": [
                {
                    "symbol": "ETH/USD",
                    "last": "3000.0",
                },
            ],
        })

        provider._process_message(msg)
        assert provider._bus.publish.called
        event = provider._bus.publish.call_args[0][0]
        assert event.symbol == "ETH-USD"
        assert event.price == 3000.0

    def test_heartbeat_ignored(self):
        provider = _make_provider()
        msg = json.dumps({"channel": "heartbeat"})
        provider._process_message(msg)
        assert not provider._bus.publish.called

    def test_invalid_json(self):
        provider = _make_provider()
        provider._process_message("not json {{{")
        assert not provider._bus.publish.called

    def test_error_message(self):
        provider = _make_provider()
        msg = json.dumps({"type": "error", "error": "bad request"})
        provider._process_message(msg)
        assert not provider._bus.publish.called

    def test_missing_last_price_skipped(self):
        provider = _make_provider()
        msg = json.dumps({
            "channel": "ticker",
            "type": "update",
            "data": [{"symbol": "XBT/USD"}],  # No "last" field
        })
        provider._process_message(msg)
        assert not provider._bus.publish.called


class TestSymbolMapping:
    def test_xbt_mapped_to_btc(self):
        provider = _make_provider()
        msg = json.dumps({
            "channel": "ticker",
            "type": "update",
            "data": [{"symbol": "XBT/USD", "last": "50000"}],
        })
        provider._process_message(msg)
        event = provider._bus.publish.call_args[0][0]
        assert event.symbol == "BTC-USD"

    def test_xdg_mapped_to_doge(self):
        provider = _make_provider()
        msg = json.dumps({
            "channel": "ticker",
            "type": "update",
            "data": [{"symbol": "XDG/USD", "last": "0.15"}],
        })
        provider._process_message(msg)
        event = provider._bus.publish.call_args[0][0]
        assert event.symbol == "DOGE-USD"

    def test_standard_symbol_passthrough(self):
        provider = _make_provider()
        msg = json.dumps({
            "channel": "ticker",
            "type": "update",
            "data": [{"symbol": "SOL/USD", "last": "100"}],
        })
        provider._process_message(msg)
        event = provider._bus.publish.call_args[0][0]
        assert event.symbol == "SOL-USD"


# ------------------------------------------------------------------
# Candle aggregation
# ------------------------------------------------------------------

class TestCandleAggregation:
    def test_first_tick_starts_candle(self):
        provider = _make_provider()
        ts = datetime(2026, 2, 18, 12, 0, 30, tzinfo=timezone.utc)
        provider._update_candle("BTC-USD", 50000.0, ts)

        state = provider._candle_state["BTC-USD"]
        assert state["open"] == 50000.0
        assert state["high"] == 50000.0
        assert state["low"] == 50000.0
        assert state["close"] == 50000.0
        assert state["minute"] == datetime(2026, 2, 18, 12, 0, tzinfo=timezone.utc)

    def test_same_minute_updates_ohlc(self):
        provider = _make_provider()
        base = datetime(2026, 2, 18, 12, 0, tzinfo=timezone.utc)

        provider._update_candle("BTC-USD", 50000.0, base.replace(second=10))
        provider._update_candle("BTC-USD", 50500.0, base.replace(second=20))  # New high
        provider._update_candle("BTC-USD", 49500.0, base.replace(second=30))  # New low
        provider._update_candle("BTC-USD", 50100.0, base.replace(second=40))  # New close

        state = provider._candle_state["BTC-USD"]
        assert state["open"] == 50000.0
        assert state["high"] == 50500.0
        assert state["low"] == 49500.0
        assert state["close"] == 50100.0

    def test_new_minute_emits_candle(self):
        provider = _make_provider()

        # First minute
        ts1 = datetime(2026, 2, 18, 12, 0, 30, tzinfo=timezone.utc)
        provider._update_candle("BTC-USD", 50000.0, ts1)

        # Next minute triggers emit
        ts2 = datetime(2026, 2, 18, 12, 1, 5, tzinfo=timezone.utc)
        provider._update_candle("BTC-USD", 50100.0, ts2)

        # Should have published a CandleEvent
        assert provider._bus.publish.called
        event = provider._bus.publish.call_args[0][0]
        assert isinstance(event, CandleEvent)
        assert event.candle.symbol == "BTC-USD"
        assert event.candle.open == 50000.0
        assert event.candle.close == 50000.0
        assert event.candle.timeframe == "1m"

    def test_flush_all_candles(self):
        provider = _make_provider()
        ts = datetime(2026, 2, 18, 12, 0, 30, tzinfo=timezone.utc)
        provider._update_candle("BTC-USD", 50000.0, ts)
        provider._update_candle("ETH-USD", 3000.0, ts)

        provider._flush_all_candles()

        assert len(provider._candle_state) == 0
        # Two candles emitted
        assert provider._bus.publish.call_count == 2

    def test_volume_from_cache(self):
        provider = _make_provider()
        minute = datetime(2026, 2, 18, 12, 0, tzinfo=timezone.utc)
        provider._volume_cache["BTC-USD"] = {minute: 42.5}

        state = {
            "minute": minute,
            "open": 50000.0,
            "high": 50500.0,
            "low": 49500.0,
            "close": 50100.0,
        }
        provider._emit_candle("BTC-USD", state)

        event = provider._bus.publish.call_args[0][0]
        assert event.candle.volume == 42.5
        # Cache entry should be consumed
        assert minute not in provider._volume_cache["BTC-USD"]

    def test_volume_zero_when_no_cache(self):
        provider = _make_provider()
        state = {
            "minute": datetime(2026, 2, 18, 12, 0, tzinfo=timezone.utc),
            "open": 50000.0,
            "high": 50000.0,
            "low": 50000.0,
            "close": 50000.0,
        }
        provider._emit_candle("BTC-USD", state)

        event = provider._bus.publish.call_args[0][0]
        assert event.candle.volume == 0.0


# ------------------------------------------------------------------
# Lifecycle
# ------------------------------------------------------------------

class TestLifecycle:
    @pytest.mark.asyncio
    async def test_start_and_stop(self):
        provider = _make_provider()

        # start() will create tasks including _ws_loop which needs websockets.
        # We just verify that start sets running state, then stop cleans up.
        await provider.start(["BTC-USD", "ETH-USD"])

        assert provider._running is True
        assert provider._symbols == ["BTC-USD", "ETH-USD"]

        # stop() cancels all tasks gracefully
        await provider.stop()
        assert provider._running is False

    def test_symbols_updated_handler(self):
        provider = _make_provider()
        provider._symbols = ["BTC-USD"]

        event = MagicMock()
        event.symbols = ("BTC-USD", "ETH-USD")
        event.added = ("ETH-USD",)
        event.removed = ()

        provider._on_symbols_updated(event)
        assert "ETH-USD" in provider._symbols
        assert provider._symbols_changed.is_set()

    def test_symbols_updated_no_change(self):
        provider = _make_provider()
        provider._symbols = ["BTC-USD", "ETH-USD"]

        event = MagicMock()
        event.symbols = ("BTC-USD", "ETH-USD")
        event.added = ()
        event.removed = ()

        provider._on_symbols_updated(event)
        assert not provider._symbols_changed.is_set()


# ------------------------------------------------------------------
# Subscribe message format
# ------------------------------------------------------------------

class TestSubscribe:
    @pytest.mark.asyncio
    async def test_subscribe_sends_correct_format(self):
        provider = _make_provider()
        provider._symbols = ["BTC-USD", "ETH-USD"]

        ws = MagicMock()
        ws.send = MagicMock(return_value=None)
        # Make send awaitable
        import asyncio
        ws.send = AsyncMock = MagicMock()
        sent_messages = []

        async def capture_send(msg):
            sent_messages.append(json.loads(msg))

        ws.send = capture_send
        await provider._subscribe(ws)

        assert len(sent_messages) == 1  # One channel: "ticker"
        msg = sent_messages[0]
        assert msg["method"] == "subscribe"
        assert msg["params"]["channel"] == "ticker"
        assert "XBT/USD" in msg["params"]["symbol"]
        assert "ETH/USD" in msg["params"]["symbol"]
