"""Tests for ExitManager risk plugin — dynamic exits via trailing/hard/soft stops."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from v2.core import registry
from v2.core.event_bus import EventBus
from v2.core.types import (
    Direction,
    Fill,
    Portfolio,
    Position,
    Signal,
    SignalEvent,
    Side,
    TickerEvent,
)


# =====================================================================
# Fixtures
# =====================================================================


@pytest.fixture(autouse=True)
def _discover():
    registry.discover_plugins()
    yield


@pytest.fixture
def bus():
    return EventBus()


@pytest.fixture
def now():
    return datetime.now(timezone.utc)


def _make_portfolio(symbol="BTC-USD", qty=0.001, avg_entry=100_000.0) -> Portfolio:
    """Create a portfolio with a single position."""
    return Portfolio(
        cash_balance=Decimal("10000"),
        positions={
            symbol: Position(
                symbol=symbol,
                qty=Decimal(str(qty)),
                avg_entry_price=Decimal(str(avg_entry)),
                cost_basis=Decimal(str(qty * avg_entry)),
                entry_time=datetime.now(timezone.utc),
            ),
        },
    )


def _make_ticker(symbol="BTC-USD", price=100_000.0) -> TickerEvent:
    return TickerEvent(
        symbol=symbol,
        price=price,
        timestamp=datetime.now(timezone.utc),
    )


def _make_exit_manager(bus, **overrides):
    """Create an ExitManager with sensible test defaults."""
    defaults = {
        "hard_stop_pct": 0.045,
        "soft_stop_pct": 0.03,
        "trailing_activation_pct": 0.03,
        "trailing_distance_pct": 0.03,
        "check_interval_sec": 0,  # No throttle for tests
    }
    defaults.update(overrides)
    return registry.create_risk("exit_manager", event_bus=bus, **defaults)


# =====================================================================
# check_signal — pass-through
# =====================================================================


class TestCheckSignalPassThrough:
    def test_buy_signal_passes(self, bus):
        em = _make_exit_manager(bus)
        signal = Signal(
            direction=Direction.BUY,
            symbol="BTC-USD",
            timestamp=datetime.now(timezone.utc),
            price=100_000.0,
            reason="test",
        )
        portfolio = Portfolio(cash_balance=Decimal("10000"))
        result = em.check_signal(signal, portfolio)
        assert result is signal

    def test_sell_signal_passes(self, bus):
        em = _make_exit_manager(bus)
        signal = Signal(
            direction=Direction.SELL,
            symbol="BTC-USD",
            timestamp=datetime.now(timezone.utc),
            price=100_000.0,
            reason="test",
        )
        portfolio = Portfolio(cash_balance=Decimal("10000"))
        result = em.check_signal(signal, portfolio)
        assert result is signal


# =====================================================================
# Hard stop
# =====================================================================


class TestHardStop:
    def test_triggers_at_threshold(self, bus):
        em = _make_exit_manager(bus, hard_stop_pct=0.045)
        portfolio = _make_portfolio(avg_entry=100_000.0)

        published = []
        bus.subscribe(SignalEvent, lambda e: published.append(e))

        # Price at exactly -4.5%
        ticker = _make_ticker(price=95_500.0)
        em.on_ticker(ticker, portfolio)

        assert len(published) == 1
        assert published[0].signal.direction == Direction.SELL
        assert published[0].signal.reason == "hard_stop"

    def test_does_not_trigger_above_threshold(self, bus):
        em = _make_exit_manager(bus, hard_stop_pct=0.045, soft_stop_pct=0.045)
        portfolio = _make_portfolio(avg_entry=100_000.0)

        published = []
        bus.subscribe(SignalEvent, lambda e: published.append(e))

        # Price at -4% (above -4.5% threshold, soft stop also set to 4.5%)
        ticker = _make_ticker(price=96_000.0)
        em.on_ticker(ticker, portfolio)

        assert len(published) == 0

    def test_hard_stop_takes_priority_over_soft(self, bus):
        """Hard stop fires at -4.5% even though soft stop threshold (-3%) is also breached."""
        em = _make_exit_manager(bus, hard_stop_pct=0.045, soft_stop_pct=0.03)
        portfolio = _make_portfolio(avg_entry=100_000.0)

        published = []
        bus.subscribe(SignalEvent, lambda e: published.append(e))

        ticker = _make_ticker(price=94_000.0)  # -6%
        em.on_ticker(ticker, portfolio)

        assert len(published) == 1
        assert published[0].signal.reason == "hard_stop"


# =====================================================================
# Soft stop
# =====================================================================


class TestSoftStop:
    def test_triggers_at_threshold(self, bus):
        em = _make_exit_manager(bus, soft_stop_pct=0.03, hard_stop_pct=0.045)
        portfolio = _make_portfolio(avg_entry=100_000.0)

        published = []
        bus.subscribe(SignalEvent, lambda e: published.append(e))

        # Price at -3%
        ticker = _make_ticker(price=97_000.0)
        em.on_ticker(ticker, portfolio)

        assert len(published) == 1
        assert published[0].signal.reason == "soft_stop"

    def test_does_not_trigger_above_threshold(self, bus):
        em = _make_exit_manager(bus, soft_stop_pct=0.03, hard_stop_pct=0.045)
        portfolio = _make_portfolio(avg_entry=100_000.0)

        published = []
        bus.subscribe(SignalEvent, lambda e: published.append(e))

        # Price at -2% (above -3% threshold)
        ticker = _make_ticker(price=98_000.0)
        em.on_ticker(ticker, portfolio)

        assert len(published) == 0


# =====================================================================
# Trailing stop
# =====================================================================


class TestTrailingStop:
    def test_activates_at_profit_threshold(self, bus):
        em = _make_exit_manager(bus, trailing_activation_pct=0.03, trailing_distance_pct=0.03)
        portfolio = _make_portfolio(avg_entry=100_000.0)

        # Price at +3% — should activate trailing
        ticker = _make_ticker(price=103_000.0)
        em.on_ticker(ticker, portfolio)

        assert em._trailing_active.get("BTC-USD") is True

    def test_does_not_activate_below_threshold(self, bus):
        em = _make_exit_manager(bus, trailing_activation_pct=0.03)
        portfolio = _make_portfolio(avg_entry=100_000.0)

        # Price at +2% — not enough to activate
        ticker = _make_ticker(price=102_000.0)
        em.on_ticker(ticker, portfolio)

        assert em._trailing_active.get("BTC-USD", False) is False

    def test_fires_on_drawdown_from_peak(self, bus):
        em = _make_exit_manager(bus, trailing_activation_pct=0.03, trailing_distance_pct=0.03)
        portfolio = _make_portfolio(avg_entry=100_000.0)

        published = []
        bus.subscribe(SignalEvent, lambda e: published.append(e))

        # Step 1: Price rises to +5% — activates trailing, peak=$105k
        em.on_ticker(_make_ticker(price=105_000.0), portfolio)
        assert em._trailing_active["BTC-USD"] is True
        assert em._peak_price["BTC-USD"] == 105_000.0

        # Step 2: Price drops 3% from peak (105k * 0.97 = 101,850)
        em.on_ticker(_make_ticker(price=101_800.0), portfolio)

        assert len(published) == 1
        assert published[0].signal.reason == "trailing_stop"

    def test_peak_only_ratchets_up(self, bus):
        em = _make_exit_manager(bus, trailing_activation_pct=0.03, trailing_distance_pct=0.03)
        portfolio = _make_portfolio(avg_entry=100_000.0)

        # Price goes up to 105k
        em.on_ticker(_make_ticker(price=105_000.0), portfolio)
        assert em._peak_price["BTC-USD"] == 105_000.0

        # Price drops to 103k — peak should NOT decrease
        em.on_ticker(_make_ticker(price=103_000.0), portfolio)
        assert em._peak_price["BTC-USD"] == 105_000.0

        # Price goes higher to 107k — peak should update
        em.on_ticker(_make_ticker(price=107_000.0), portfolio)
        assert em._peak_price["BTC-USD"] == 107_000.0

    def test_no_fire_without_activation(self, bus):
        """Trailing should not fire just because price dropped from a high,
        if trailing was never activated (profit threshold not reached)."""
        em = _make_exit_manager(bus, trailing_activation_pct=0.03, trailing_distance_pct=0.03)
        portfolio = _make_portfolio(avg_entry=100_000.0)

        published = []
        bus.subscribe(SignalEvent, lambda e: published.append(e))

        # Price at +2% (below activation threshold)
        em.on_ticker(_make_ticker(price=102_000.0), portfolio)

        # Price drops to entry price — should NOT fire trailing
        em.on_ticker(_make_ticker(price=100_000.0), portfolio)

        assert len(published) == 0


# =====================================================================
# Pending exits — no duplicate signals
# =====================================================================


class TestPendingExits:
    def test_no_duplicate_signals(self, bus):
        em = _make_exit_manager(bus, soft_stop_pct=0.03, hard_stop_pct=0.045)
        portfolio = _make_portfolio(avg_entry=100_000.0)

        published = []
        bus.subscribe(SignalEvent, lambda e: published.append(e))

        # First tick at -3% — should fire
        em.on_ticker(_make_ticker(price=97_000.0), portfolio)
        assert len(published) == 1

        # Second tick still at -3% — should NOT fire again (pending)
        em.on_ticker(_make_ticker(price=96_500.0), portfolio)
        assert len(published) == 1  # Still 1, not 2


# =====================================================================
# Fill tracking — state reset/clear
# =====================================================================


class TestFillTracking:
    def test_buy_fill_initializes_tracking(self, bus):
        em = _make_exit_manager(bus)

        fill = Fill(
            fill_id="f1",
            order_id="o1",
            symbol="BTC-USD",
            side=Side.BUY,
            price=Decimal("100000"),
            qty=Decimal("0.001"),
            fee=Decimal("0.60"),
            fee_currency="USD",
            is_maker=True,
            timestamp=datetime.now(timezone.utc),
        )
        portfolio = _make_portfolio()
        em.on_fill(fill, portfolio)

        assert em._peak_price["BTC-USD"] == 100_000.0
        assert em._trailing_active["BTC-USD"] is False

    def test_sell_fill_clears_tracking(self, bus):
        em = _make_exit_manager(bus)

        # First set up some state
        em._peak_price["BTC-USD"] = 105_000.0
        em._trailing_active["BTC-USD"] = True
        em._pending_exits.add("BTC-USD")
        em._last_check["BTC-USD"] = time.time()

        fill = Fill(
            fill_id="f2",
            order_id="o2",
            symbol="BTC-USD",
            side=Side.SELL,
            price=Decimal("103000"),
            qty=Decimal("0.001"),
            fee=Decimal("0.62"),
            fee_currency="USD",
            is_maker=True,
            timestamp=datetime.now(timezone.utc),
        )
        portfolio = Portfolio(cash_balance=Decimal("10000"))
        em.on_fill(fill, portfolio)

        assert "BTC-USD" not in em._peak_price
        assert "BTC-USD" not in em._trailing_active
        assert "BTC-USD" not in em._pending_exits
        assert "BTC-USD" not in em._last_check

    def test_sell_fill_clears_pending_allows_new_entry(self, bus):
        """After sell fill, new buy + subsequent tickers should work."""
        em = _make_exit_manager(bus)
        portfolio = _make_portfolio(avg_entry=100_000.0)

        published = []
        bus.subscribe(SignalEvent, lambda e: published.append(e))

        # Trigger soft stop
        em.on_ticker(_make_ticker(price=97_000.0), portfolio)
        assert len(published) == 1

        # Simulate sell fill
        sell_fill = Fill(
            fill_id="f3", order_id="o3", symbol="BTC-USD",
            side=Side.SELL, price=Decimal("97000"), qty=Decimal("0.001"),
            fee=Decimal("0.58"), fee_currency="USD", is_maker=True,
            timestamp=datetime.now(timezone.utc),
        )
        em.on_fill(sell_fill, Portfolio(cash_balance=Decimal("10000")))

        # New buy fill at different price
        buy_fill = Fill(
            fill_id="f4", order_id="o4", symbol="BTC-USD",
            side=Side.BUY, price=Decimal("95000"), qty=Decimal("0.001"),
            fee=Decimal("0.57"), fee_currency="USD", is_maker=True,
            timestamp=datetime.now(timezone.utc),
        )
        new_portfolio = _make_portfolio(avg_entry=95_000.0)
        em.on_fill(buy_fill, new_portfolio)

        # Should be able to trigger again on new position
        em.on_ticker(_make_ticker(price=92_000.0), new_portfolio)  # -3.16%
        assert len(published) == 2


# =====================================================================
# No position — skip
# =====================================================================


class TestNoPosition:
    def test_skip_when_no_position(self, bus):
        em = _make_exit_manager(bus)
        portfolio = Portfolio(cash_balance=Decimal("10000"))

        published = []
        bus.subscribe(SignalEvent, lambda e: published.append(e))

        em.on_ticker(_make_ticker(price=50_000.0), portfolio)
        assert len(published) == 0

    def test_skip_when_zero_qty(self, bus):
        em = _make_exit_manager(bus)
        portfolio = Portfolio(
            cash_balance=Decimal("10000"),
            positions={
                "BTC-USD": Position(
                    symbol="BTC-USD",
                    qty=Decimal("0"),
                    avg_entry_price=Decimal("100000"),
                    cost_basis=Decimal("0"),
                    entry_time=datetime.now(timezone.utc),
                ),
            },
        )

        published = []
        bus.subscribe(SignalEvent, lambda e: published.append(e))

        em.on_ticker(_make_ticker(price=50_000.0), portfolio)
        assert len(published) == 0


# =====================================================================
# Throttle
# =====================================================================


class TestThrottle:
    def test_throttle_skips_rapid_checks(self, bus):
        em = _make_exit_manager(bus, check_interval_sec=10)
        portfolio = _make_portfolio(avg_entry=100_000.0)

        published = []
        bus.subscribe(SignalEvent, lambda e: published.append(e))

        # First check at -3% — should fire
        em.on_ticker(_make_ticker(price=97_000.0), portfolio)
        assert len(published) == 1

        # Immediately after — should be throttled (pending_exits also blocks, but
        # clear it to test throttle specifically)
        em._pending_exits.clear()
        em._last_check["BTC-USD"] = time.time()  # Force recent check
        em.on_ticker(_make_ticker(price=96_000.0), portfolio)
        assert len(published) == 1  # Throttled


# =====================================================================
# State persistence
# =====================================================================


class TestState:
    def test_get_and_load_state(self, bus):
        em = _make_exit_manager(bus)
        em._peak_price = {"BTC-USD": 105_000.0, "ETH-USD": 3500.0}
        em._trailing_active = {"BTC-USD": True, "ETH-USD": False}
        em._pending_exits = {"BTC-USD"}

        state = em.get_state()
        assert state["peak_prices"]["BTC-USD"] == 105_000.0
        assert state["trailing_active"]["BTC-USD"] is True
        assert "BTC-USD" in state["pending_exits"]

        # Load into fresh instance
        em2 = _make_exit_manager(bus)
        em2.load_state(state)
        assert em2._peak_price["BTC-USD"] == 105_000.0
        assert em2._trailing_active["BTC-USD"] is True
        assert "BTC-USD" in em2._pending_exits


# =====================================================================
# Configure
# =====================================================================


class TestConfigure:
    def test_configure_overrides_defaults(self, bus):
        em = _make_exit_manager(bus)
        em.configure({
            "hard_stop_pct": 0.05,
            "soft_stop_pct": 0.025,
            "trailing_activation_pct": 0.04,
            "trailing_distance_pct": 0.02,
            "check_interval_sec": 10,
        })
        assert em._hard_stop_pct == 0.05
        assert em._soft_stop_pct == 0.025
        assert em._trailing_activation_pct == 0.04
        assert em._trailing_distance_pct == 0.02
        assert em._check_interval_sec == 10.0


# =====================================================================
# Multi-symbol
# =====================================================================


class TestMultiSymbol:
    def test_independent_tracking(self, bus):
        em = _make_exit_manager(bus, trailing_activation_pct=0.03, trailing_distance_pct=0.03)

        btc_portfolio = Portfolio(
            cash_balance=Decimal("10000"),
            positions={
                "BTC-USD": Position(
                    symbol="BTC-USD", qty=Decimal("0.001"),
                    avg_entry_price=Decimal("100000"),
                    cost_basis=Decimal("100"),
                    entry_time=datetime.now(timezone.utc),
                ),
                "ETH-USD": Position(
                    symbol="ETH-USD", qty=Decimal("0.1"),
                    avg_entry_price=Decimal("3000"),
                    cost_basis=Decimal("300"),
                    entry_time=datetime.now(timezone.utc),
                ),
            },
        )

        published = []
        bus.subscribe(SignalEvent, lambda e: published.append(e))

        # BTC activates trailing at +5%
        em.on_ticker(_make_ticker("BTC-USD", 105_000.0), btc_portfolio)
        assert em._trailing_active.get("BTC-USD") is True
        assert em._trailing_active.get("ETH-USD", False) is False

        # ETH still at entry — no activation
        em.on_ticker(_make_ticker("ETH-USD", 3000.0), btc_portfolio)
        assert em._trailing_active.get("ETH-USD", False) is False

        # BTC drops from peak — trailing fires
        em.on_ticker(_make_ticker("BTC-USD", 101_800.0), btc_portfolio)
        assert len(published) == 1
        assert published[0].signal.symbol == "BTC-USD"

        # ETH unaffected
        assert "ETH-USD" not in em._pending_exits
