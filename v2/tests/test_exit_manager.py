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
    OrderType,
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
    """Create an ExitManager with sensible test defaults.

    Fees default to 0 so existing stop-level tests use raw P&L.
    Fee-aware tests explicitly set maker_fee_pct/taker_fee_pct.
    """
    defaults = {
        "hard_stop_pct": 0.045,
        "soft_stop_pct": 0.03,
        "trailing_activation_pct": 0.03,
        "trailing_distance_pct": 0.03,
        "check_interval_sec": 0,  # No throttle for tests
        "maker_fee_pct": 0.0,     # Zero fees by default for deterministic stop tests
        "taker_fee_pct": 0.0,
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


# =====================================================================
# Fee-aware P&L
# =====================================================================


class TestFeeAwarePnl:
    """Test that fee-aware P&L shifts exit thresholds correctly.

    With Coinbase Intro 1 fees (0.6% maker, 1.2% taker), the fee drag is
    about 1.8%. So a raw -3% loss becomes ~-4.8% net, and a raw +3% gain
    becomes only ~+1.2% net.
    """

    def test_compute_pnl_zero_fees(self, bus):
        """With zero fees, net == raw."""
        em = _make_exit_manager(bus, maker_fee_pct=0.0, taker_fee_pct=0.0)
        pnl_net, pnl_raw = em._compute_pnl(103_000.0, 100_000.0)
        assert abs(pnl_net - 0.03) < 1e-9
        assert abs(pnl_raw - 0.03) < 1e-9

    def test_compute_pnl_with_fees(self, bus):
        """Fee-aware P&L is lower than raw."""
        em = _make_exit_manager(bus, maker_fee_pct=0.006, taker_fee_pct=0.012)
        pnl_net, pnl_raw = em._compute_pnl(103_000.0, 100_000.0)
        # raw = +3%
        assert abs(pnl_raw - 0.03) < 1e-9
        # net = (103000*0.988 - 100000*1.006) / (100000*1.006)
        #     = (101764 - 100600) / 100600 ≈ +1.157%
        assert pnl_net < pnl_raw
        assert 0.011 < pnl_net < 0.012

    def test_compute_pnl_loss_amplified_by_fees(self, bus):
        """A raw -3% loss becomes worse with fees."""
        em = _make_exit_manager(bus, maker_fee_pct=0.006, taker_fee_pct=0.012)
        pnl_net, pnl_raw = em._compute_pnl(97_000.0, 100_000.0)
        assert abs(pnl_raw - (-0.03)) < 1e-9
        # net ≈ -4.73% (loss amplified by fees)
        assert pnl_net < pnl_raw
        assert -0.048 < pnl_net < -0.047

    def test_hard_stop_shifted_by_fees(self, bus):
        """With fees, hard stop triggers at a higher raw price (less loss needed).

        Entry=100k, hard_stop=-4.5%.
        Net pnl = (price*0.988 - 100600) / 100600
        At price=$97,100: net = (95934.8 - 100600)/100600 = -4.64% → triggers
        At price=$97,300: net = (96132.4 - 100600)/100600 = -4.44% → no trigger
        """
        em = _make_exit_manager(bus, hard_stop_pct=0.045, soft_stop_pct=0.045,
                                maker_fee_pct=0.006, taker_fee_pct=0.012)
        portfolio = _make_portfolio(avg_entry=100_000.0)

        published = []
        bus.subscribe(SignalEvent, lambda e: published.append(e))

        # Price at $97,100 → raw pnl = -2.9%, net pnl ≈ -4.64% (< -4.5% threshold)
        em.on_ticker(_make_ticker(price=97_100.0), portfolio)
        assert len(published) == 1
        assert published[0].signal.reason == "hard_stop"
        assert "pnl_raw_pct" in published[0].signal.metadata

    def test_soft_stop_shifted_by_fees(self, bus):
        """With fees, soft stop triggers at less raw loss.

        Entry=100k, soft_stop=-3%.
        Net pnl = (price*0.988 - 100600) / 100600
        At price=$98,750: net = (97565 - 100600)/100600 = -3.02% → triggers
        At price=$98,800: net = (97614 - 100600)/100600 = -2.97% → no trigger
        """
        em = _make_exit_manager(bus, hard_stop_pct=0.10, soft_stop_pct=0.03,
                                maker_fee_pct=0.006, taker_fee_pct=0.012)
        portfolio = _make_portfolio(avg_entry=100_000.0)

        published = []
        bus.subscribe(SignalEvent, lambda e: published.append(e))

        # Price at $98,750 → raw pnl = -1.25%, net pnl ≈ -3.02%
        em.on_ticker(_make_ticker(price=98_750.0), portfolio)
        assert len(published) == 1
        assert published[0].signal.reason == "soft_stop"

    def test_trailing_activation_shifted_by_fees(self, bus):
        """With fees, trailing requires more raw gain to activate.

        Entry=100k, activation=+3%. With 1.8% fee drag, price must rise
        ~4.8% raw to reach +3% net. Price=$104,800 should NOT activate,
        but $105,100 should.
        """
        em = _make_exit_manager(bus, trailing_activation_pct=0.03,
                                maker_fee_pct=0.006, taker_fee_pct=0.012)
        portfolio = _make_portfolio(avg_entry=100_000.0)

        # $104,800 → raw +4.8%, net ≈ +2.96% → should NOT activate
        em.on_ticker(_make_ticker(price=104_800.0), portfolio)
        assert em._trailing_active.get("BTC-USD", False) is False

        # $105,100 → raw +5.1%, net ≈ +3.26% → SHOULD activate
        em.on_ticker(_make_ticker(price=105_100.0), portfolio)
        assert em._trailing_active.get("BTC-USD") is True

    def test_signal_exit_uses_fee_pnl(self, bus):
        """Signal-based exit P&L metadata includes both net and raw."""
        em = _make_exit_manager(bus, maker_fee_pct=0.006, taker_fee_pct=0.012,
                                signal_exit_enabled=True, signal_exit_min_pnl_pct=0.0)
        portfolio = _make_portfolio(avg_entry=100_000.0)

        signal = Signal(
            direction=Direction.SELL,
            symbol="BTC-USD",
            timestamp=datetime.now(timezone.utc),
            price=105_000.0,
            reason="test_sell",
        )
        result = em.check_signal(signal, portfolio)
        assert result is not None
        assert "pnl_pct" in result.metadata       # net P&L
        assert "pnl_raw_pct" in result.metadata    # raw P&L
        assert result.metadata["pnl_raw_pct"] > result.metadata["pnl_pct"]

    def test_signal_exit_blocked_when_net_pnl_negative(self, bus):
        """Signal exit with min_pnl=0 should block when net P&L < 0 due to fees,
        even if raw P&L is slightly positive."""
        em = _make_exit_manager(bus, maker_fee_pct=0.006, taker_fee_pct=0.012,
                                signal_exit_enabled=True, signal_exit_min_pnl_pct=0.0)
        portfolio = _make_portfolio(avg_entry=100_000.0)

        # Price at $101,000 → raw pnl = +1.0%, net pnl ≈ -0.8% (fees eat the profit)
        signal = Signal(
            direction=Direction.SELL,
            symbol="BTC-USD",
            timestamp=datetime.now(timezone.utc),
            price=101_000.0,
            reason="test_sell",
        )
        result = em.check_signal(signal, portfolio)
        # Signal should pass through but without exit_reason tag (net pnl < 0)
        assert result is not None
        assert "exit_reason" not in result.metadata

    def test_configure_updates_fees(self, bus):
        em = _make_exit_manager(bus)
        em.configure({"maker_fee_pct": 0.003, "taker_fee_pct": 0.005})
        assert em._maker_fee == 0.003
        assert em._taker_fee == 0.005

    def test_metadata_includes_raw_and_net_pnl(self, bus):
        """Exit signal metadata includes both pnl_pct (net) and pnl_raw_pct."""
        em = _make_exit_manager(bus, hard_stop_pct=0.05, maker_fee_pct=0.006, taker_fee_pct=0.012)
        portfolio = _make_portfolio(avg_entry=100_000.0)

        published = []
        bus.subscribe(SignalEvent, lambda e: published.append(e))

        em.on_ticker(_make_ticker(price=94_000.0), portfolio)  # -6% raw → much worse net
        assert len(published) == 1
        meta = published[0].signal.metadata
        assert "pnl_pct" in meta
        assert "pnl_raw_pct" in meta
        assert meta["pnl_pct"] < meta["pnl_raw_pct"]  # net worse than raw


class TestFeeRefresh:
    @pytest.mark.asyncio
    async def test_refresh_fees_from_exchange(self, bus):
        """Exchange fee rates override config defaults."""
        from unittest.mock import AsyncMock
        em = _make_exit_manager(bus, maker_fee_pct=0.006, taker_fee_pct=0.012)

        mock_exchange = MagicMock()
        mock_exchange.get_fee_rates = AsyncMock(return_value={
            "maker": Decimal("0.004"),
            "taker": Decimal("0.006"),
        })
        em._exchange = mock_exchange

        await em._refresh_fees()

        assert em._maker_fee == 0.004
        assert em._taker_fee == 0.006
        assert em._fee_fetch_time > 0

    @pytest.mark.asyncio
    async def test_refresh_fees_fallback_on_error(self, bus):
        """If exchange API fails, keep cached/config fees."""
        from unittest.mock import AsyncMock
        em = _make_exit_manager(bus, maker_fee_pct=0.006, taker_fee_pct=0.012)

        mock_exchange = MagicMock()
        mock_exchange.get_fee_rates = AsyncMock(side_effect=Exception("API error"))
        em._exchange = mock_exchange

        await em._refresh_fees()

        # Fees unchanged
        assert em._maker_fee == 0.006
        assert em._taker_fee == 0.012

    def test_maybe_refresh_skips_without_exchange(self, bus):
        """No exchange wired → no refresh attempt."""
        em = _make_exit_manager(bus)
        em._exchange = None
        em._fee_fetch_time = 0  # Stale
        em._maybe_refresh_fees()
        assert not em._fee_refresh_pending

    def test_maybe_refresh_skips_when_fresh(self, bus):
        """Cache not stale → no refresh."""
        em = _make_exit_manager(bus)
        em._exchange = MagicMock()
        em._fee_fetch_time = time.time()  # Just fetched
        em._maybe_refresh_fees()
        assert not em._fee_refresh_pending


# =====================================================================
# Market orders for hard/severe stops
# =====================================================================


class TestMarketOrders:
    def test_hard_stop_emits_market_order(self, bus):
        """Hard stop signals use OrderType.MARKET."""
        em = _make_exit_manager(bus, hard_stop_pct=0.045)
        portfolio = _make_portfolio(avg_entry=100_000.0)

        published = []
        bus.subscribe(SignalEvent, lambda e: published.append(e))

        em.on_ticker(_make_ticker(price=95_000.0), portfolio)  # -5%
        assert len(published) == 1
        assert published[0].signal.order_type == OrderType.MARKET
        assert published[0].signal.reason == "hard_stop"

    def test_soft_stop_emits_market_order(self, bus):
        """All soft stops use OrderType.MARKET — limit orders on illiquid
        pairs get cancelled as stale, leaving the position stuck."""
        em = _make_exit_manager(bus, hard_stop_pct=0.10, soft_stop_pct=0.03)
        portfolio = _make_portfolio(avg_entry=100_000.0)

        published = []
        bus.subscribe(SignalEvent, lambda e: published.append(e))

        em.on_ticker(_make_ticker(price=97_000.0), portfolio)  # -3%
        assert len(published) == 1
        assert published[0].signal.order_type == OrderType.MARKET
        assert published[0].signal.reason == "soft_stop"

    def test_trailing_stop_emits_limit_order(self, bus):
        """Trailing stop uses the default OrderType.LIMIT."""
        em = _make_exit_manager(bus, trailing_activation_pct=0.03, trailing_distance_pct=0.03)
        portfolio = _make_portfolio(avg_entry=100_000.0)

        published = []
        bus.subscribe(SignalEvent, lambda e: published.append(e))

        # Activate trailing at +5%
        em.on_ticker(_make_ticker(price=105_000.0), portfolio)
        # Drop 3% from peak
        em.on_ticker(_make_ticker(price=101_800.0), portfolio)

        assert len(published) == 1
        assert published[0].signal.order_type == OrderType.LIMIT
        assert published[0].signal.reason == "trailing_stop"


# =====================================================================
# ATR-based trailing stops
# =====================================================================


class TestATRTrailing:
    def _make_candle(self, symbol, high, low, close):
        """Create a minimal candle-like object."""
        from types import SimpleNamespace
        return SimpleNamespace(symbol=symbol, high=high, low=low, close=close)

    def _feed_candles(self, em, symbol, candles):
        """Feed a list of (high, low, close) tuples as candles."""
        for h, l, c in candles:
            em.on_candle(self._make_candle(symbol, h, l, c))

    def test_atr_computation_basic(self, bus):
        """ATR computed from candle history."""
        em = _make_exit_manager(bus, trailing_mode="atr", atr_period=3,
                                atr_mult=2.0, trailing_min_distance_pct=0.001,
                                trailing_max_distance_pct=0.10)
        # Feed 4 candles (need N+1 for N true ranges)
        candles = [
            (100, 90, 95),   # bar 0
            (105, 92, 100),  # bar 1: TR = max(13, 10, 3) = 13
            (103, 88, 90),   # bar 2: TR = max(15, 3, 12) = 15
            (98, 85, 88),    # bar 3: TR = max(13, 8, 5) = 13
        ]
        self._feed_candles(em, "BTC-USD", candles)

        dist = em._compute_atr_distance("BTC-USD", 88.0)
        assert dist is not None
        # ATR = (13+15+13)/3 = 13.67, atr_pct = 13.67/88 = 0.1553
        # distance = 0.1553 * 2.0 = 0.3106, clamped to max 0.10
        assert abs(dist - 0.10) < 0.001  # Clamped to max

    def test_atr_constrained_to_min(self, bus):
        """Low volatility ATR gets constrained to min distance."""
        em = _make_exit_manager(bus, trailing_mode="atr", atr_period=3,
                                atr_mult=2.0, trailing_min_distance_pct=0.02,
                                trailing_max_distance_pct=0.05)
        # Very tight candles → small ATR
        candles = [
            (100.0, 99.8, 99.9),
            (100.1, 99.9, 100.0),
            (100.0, 99.8, 99.9),
            (100.1, 99.9, 100.0),
        ]
        self._feed_candles(em, "BTC-USD", candles)

        dist = em._compute_atr_distance("BTC-USD", 100.0)
        assert dist is not None
        assert dist == 0.02  # Constrained to min

    def test_atr_returns_none_insufficient_data(self, bus):
        """Returns None when not enough candle history."""
        em = _make_exit_manager(bus, trailing_mode="atr", atr_period=14)
        # Only 1 candle — need at least 2
        em.on_candle(self._make_candle("BTC-USD", 100, 90, 95))
        assert em._compute_atr_distance("BTC-USD", 95.0) is None

    def test_on_candle_ignored_in_fixed_mode(self, bus):
        """Candles don't accumulate in fixed trailing mode."""
        em = _make_exit_manager(bus, trailing_mode="fixed")
        em.on_candle(self._make_candle("BTC-USD", 100, 90, 95))
        assert "BTC-USD" not in em._candle_history

    def test_atr_trailing_stop_ratchets_up(self, bus):
        """ATR stop price only ratchets up, never down."""
        em = _make_exit_manager(bus, trailing_mode="atr", atr_period=3,
                                atr_mult=1.0, trailing_activation_pct=0.01,
                                trailing_min_distance_pct=0.01,
                                trailing_max_distance_pct=0.05)
        portfolio = _make_portfolio(avg_entry=100.0)

        # Feed candles to build history
        candles = [(102, 98, 100), (103, 99, 101), (104, 100, 102), (105, 101, 103)]
        self._feed_candles(em, "BTC-USD", candles)

        # Activate trailing at +5%
        em.on_ticker(_make_ticker("BTC-USD", 105.0), portfolio)
        assert em._trailing_active.get("BTC-USD") is True
        stop1 = em._stop_price.get("BTC-USD")
        assert stop1 is not None

        # Price goes higher — stop should ratchet up
        em.on_ticker(_make_ticker("BTC-USD", 110.0), portfolio)
        stop2 = em._stop_price.get("BTC-USD")
        assert stop2 >= stop1  # Ratcheted up

        # Price drops back — stop should NOT decrease
        em.on_ticker(_make_ticker("BTC-USD", 107.0), portfolio)
        stop3 = em._stop_price.get("BTC-USD")
        assert stop3 >= stop2  # Never lower

    def test_atr_fallback_to_fixed_without_candles(self, bus):
        """In ATR mode, falls back to fixed distance when no candle data."""
        em = _make_exit_manager(bus, trailing_mode="atr",
                                trailing_activation_pct=0.03,
                                trailing_distance_pct=0.03)
        portfolio = _make_portfolio(avg_entry=100_000.0)

        published = []
        bus.subscribe(SignalEvent, lambda e: published.append(e))

        # No candles fed — should use fixed 3% distance
        em.on_ticker(_make_ticker(price=105_000.0), portfolio)  # activate
        assert em._trailing_active["BTC-USD"] is True

        em.on_ticker(_make_ticker(price=101_800.0), portfolio)  # 3% below 105k peak
        assert len(published) == 1
        assert published[0].signal.reason == "trailing_stop"

    def test_sell_fill_clears_stop_price(self, bus):
        """Sell fill clears ATR stop price state."""
        em = _make_exit_manager(bus, trailing_mode="atr")
        em._stop_price["BTC-USD"] = 95000.0

        fill = Fill(
            fill_id="f1", order_id="o1", symbol="BTC-USD",
            side=Side.SELL, price=Decimal("97000"), qty=Decimal("0.001"),
            fee=Decimal("0.58"), fee_currency="USD", is_maker=True,
            timestamp=datetime.now(timezone.utc),
        )
        em.on_fill(fill, Portfolio(cash_balance=Decimal("10000")))
        assert "BTC-USD" not in em._stop_price

    def test_state_persistence_includes_stop_prices(self, bus):
        """get_state/load_state round-trips stop_prices."""
        em = _make_exit_manager(bus, trailing_mode="atr")
        em._stop_price["BTC-USD"] = 95000.0
        em._stop_price["ETH-USD"] = 2800.0

        state = em.get_state()
        assert state["stop_prices"]["BTC-USD"] == 95000.0

        em2 = _make_exit_manager(bus, trailing_mode="atr")
        em2.load_state(state)
        assert em2._stop_price["BTC-USD"] == 95000.0
        assert em2._stop_price["ETH-USD"] == 2800.0


# =====================================================================
# Peak Tracking for ROC Momentum Trades
# =====================================================================


class TestPeakTracking:
    """Tests for SMA-smoothed peak tracking on momentum trades."""

    def _make_fill(self, symbol="BTC-USD", price=100.0, side=Side.BUY, trigger="roc_momo_20m"):
        return Fill(
            fill_id="f1", order_id="o1", symbol=symbol,
            side=side, price=Decimal(str(price)), qty=Decimal("0.1"),
            fee=Decimal("0"), fee_currency="USD", is_maker=True,
            timestamp=datetime.now(timezone.utc),
            metadata={"trigger": trigger, "signal_reason": trigger},
        )

    def test_init_on_roc_fill(self, bus):
        """Peak tracking initializes on BUY fill with a peak trigger."""
        em = _make_exit_manager(bus, peak_tracking_enabled=True,
                                peak_triggers=["roc_momo_20m", "roc_momo_24h"])
        fill = self._make_fill(price=100.0, trigger="roc_momo_20m")
        em.on_fill(fill, Portfolio(cash_balance=Decimal("10000")))
        assert "BTC-USD" in em._peak_state
        assert em._peak_state["BTC-USD"]["trigger_type"] == "roc_momo_20m"
        assert em._peak_state["BTC-USD"]["breakeven_activated"] is False

    def test_no_init_on_composite_fill(self, bus):
        """Peak tracking does NOT initialize on non-momentum triggers."""
        em = _make_exit_manager(bus, peak_tracking_enabled=True,
                                peak_triggers=["roc_momo_20m", "roc_momo_24h"])
        fill = self._make_fill(trigger="score")
        em.on_fill(fill, Portfolio(cash_balance=Decimal("10000")))
        assert "BTC-USD" not in em._peak_state

    def test_no_init_when_disabled(self, bus):
        """Peak tracking does NOT initialize when disabled."""
        em = _make_exit_manager(bus, peak_tracking_enabled=False,
                                peak_triggers=["roc_momo_20m"])
        fill = self._make_fill(trigger="roc_momo_20m")
        em.on_fill(fill, Portfolio(cash_balance=Decimal("10000")))
        assert "BTC-USD" not in em._peak_state

    def test_time_limit_exit(self, bus):
        """Exit after 24h hold regardless of P&L."""
        em = _make_exit_manager(bus, peak_tracking_enabled=True,
                                peak_triggers=["roc_momo_20m"],
                                peak_max_hold_minutes=0)  # Immediate timeout
        fill = self._make_fill(price=100.0)
        portfolio = _make_portfolio(avg_entry=100.0)
        em.on_fill(fill, Portfolio(cash_balance=Decimal("10000")))

        published = []
        bus.subscribe(SignalEvent, lambda e: published.append(e))

        em.on_ticker(_make_ticker(price=100.0), portfolio)
        assert len(published) == 1
        assert published[0].signal.reason == "peak_time_limit"

    def test_drawdown_exit(self, bus):
        """Exit when smoothed price drops >5% from SMA peak."""
        em = _make_exit_manager(bus, peak_tracking_enabled=True,
                                peak_triggers=["roc_momo_20m"],
                                peak_drawdown_pct=0.05,
                                peak_min_profit_pct=0.01,  # Low threshold for test
                                peak_smoothing_prices=1,   # No smoothing for predictability
                                peak_max_hold_minutes=9999)
        # Entry at 100, price will peak at 120 so 5% drawdown (to 114) still has +14% pnl
        portfolio = _make_portfolio(avg_entry=100.0)
        fill = self._make_fill(price=100.0)
        em.on_fill(fill, Portfolio(cash_balance=Decimal("10000")))

        published = []
        bus.subscribe(SignalEvent, lambda e: published.append(e))

        # Price rises to 120 (+20% pnl) — well above min_profit=1%
        em.on_ticker(_make_ticker(price=120.0), portfolio)
        assert len(published) == 0
        assert em._peak_state["BTC-USD"]["breakeven_activated"] is True

        # Peak is now 120. Price drops to 113.9 → 113.9/120 ≈ -5.08% drawdown
        # pnl from entry 100 = +13.9%, still above min_profit
        em.on_ticker(_make_ticker(price=113.9), portfolio)
        assert len(published) == 1
        assert published[0].signal.reason == "peak_drawdown"

    def test_breakeven_protection(self, bus):
        """Exit when P&L drops to fee level after activation.

        Once peak tracking activates (pnl >= min_profit), it remains active.
        If P&L then drops to -(fees), breakeven protection triggers.
        Set soft/hard stops very wide so they don't interfere.
        """
        em = _make_exit_manager(bus, peak_tracking_enabled=True,
                                peak_triggers=["roc_momo_20m"],
                                peak_min_profit_pct=0.01,
                                maker_fee_pct=0.006, taker_fee_pct=0.012,
                                peak_smoothing_prices=1,
                                peak_max_hold_minutes=9999,
                                hard_stop_pct=0.50,  # 50% — won't interfere
                                soft_stop_pct=0.50)   # 50% — won't interfere
        portfolio = _make_portfolio(avg_entry=100.0)
        fill = self._make_fill(price=100.0)
        em.on_fill(fill, Portfolio(cash_balance=Decimal("10000")))

        published = []
        bus.subscribe(SignalEvent, lambda e: published.append(e))

        # Price rises to activate peak tracking (pnl_net > 1%)
        # At price=103: pnl_net = (103*0.988 - 100*1.006) / (100*1.006) ≈ 1.12%
        em.on_ticker(_make_ticker(price=103.0), portfolio)
        assert em._peak_state["BTC-USD"]["breakeven_activated"] is True

        # Price tanks: pnl_net <= -(maker+taker) = -1.8%
        # At price=98: pnl_net = (98*0.988 - 100.6) / 100.6 = -3.75%
        em.on_ticker(_make_ticker(price=98.0), portfolio)
        assert len(published) == 1
        assert published[0].signal.reason == "peak_breakeven"

    def test_not_activated_below_min_profit(self, bus):
        """Peak tracking doesn't trigger when below min_profit threshold."""
        em = _make_exit_manager(bus, peak_tracking_enabled=True,
                                peak_triggers=["roc_momo_20m"],
                                peak_min_profit_pct=0.06,
                                peak_drawdown_pct=0.05,
                                peak_smoothing_prices=1,
                                peak_max_hold_minutes=9999)
        portfolio = _make_portfolio(avg_entry=100.0)
        fill = self._make_fill(price=100.0)
        em.on_fill(fill, Portfolio(cash_balance=Decimal("10000")))

        published = []
        bus.subscribe(SignalEvent, lambda e: published.append(e))

        # Price rises only +2% — below +6% activation
        em.on_ticker(_make_ticker(price=102.0), portfolio)
        assert em._peak_state["BTC-USD"]["breakeven_activated"] is False

        # Price drops significantly — peak tracking should NOT emit (not activated)
        # Normal stops might fire, but peak tracking returns False
        em.on_ticker(_make_ticker(price=95.0), portfolio)
        # Check that the peak_drawdown reason was NOT the exit reason
        peak_exits = [p for p in published if "peak_" in p.signal.reason]
        assert len(peak_exits) == 0

    def test_sma_smoothing(self, bus):
        """SMA smoothing averages multiple price observations for peak."""
        em = _make_exit_manager(bus, peak_tracking_enabled=True,
                                peak_triggers=["roc_momo_20m"],
                                peak_smoothing_prices=3,
                                peak_min_profit_pct=0.0,
                                peak_max_hold_minutes=9999)
        fill = self._make_fill(price=100.0)
        em.on_fill(fill, Portfolio(cash_balance=Decimal("10000")))

        state = em._peak_state["BTC-USD"]

        # Feed 3 prices via _check_peak_tracking
        em._check_peak_tracking("BTC-USD", 100.0, 0.0, 0.0)
        em._check_peak_tracking("BTC-USD", 110.0, 0.1, 0.1)
        em._check_peak_tracking("BTC-USD", 105.0, 0.05, 0.05)

        # SMA = (100 + 110 + 105) / 3 = 105
        assert len(state["price_history"]) == 3
        assert state["peak_price"] == pytest.approx(110.0 / 3 + 100.0 / 3 + 105.0 / 3, abs=0.01)

    def test_sell_fill_clears_peak_state(self, bus):
        """Sell fill clears peak tracking state."""
        em = _make_exit_manager(bus, peak_tracking_enabled=True,
                                peak_triggers=["roc_momo_20m"])
        fill = self._make_fill(price=100.0)
        em.on_fill(fill, Portfolio(cash_balance=Decimal("10000")))
        assert "BTC-USD" in em._peak_state

        sell_fill = Fill(
            fill_id="f2", order_id="o2", symbol="BTC-USD",
            side=Side.SELL, price=Decimal("110"), qty=Decimal("0.1"),
            fee=Decimal("0"), fee_currency="USD", is_maker=True,
            timestamp=datetime.now(timezone.utc),
        )
        em.on_fill(sell_fill, Portfolio(cash_balance=Decimal("10000")))
        assert "BTC-USD" not in em._peak_state

    def test_disabled_by_default(self, bus):
        """Peak tracking is disabled by default."""
        em = _make_exit_manager(bus)
        assert em._peak_tracking_enabled is False
        fill = self._make_fill(trigger="roc_momo_20m")
        em.on_fill(fill, Portfolio(cash_balance=Decimal("10000")))
        assert "BTC-USD" not in em._peak_state

    def test_state_persistence_round_trip(self, bus):
        """Peak tracking state survives get_state/load_state."""
        em = _make_exit_manager(bus, peak_tracking_enabled=True,
                                peak_triggers=["roc_momo_20m"])
        fill = self._make_fill(price=100.0)
        em.on_fill(fill, Portfolio(cash_balance=Decimal("10000")))

        state = em.get_state()
        assert "peak_tracking_states" in state
        assert "BTC-USD" in state["peak_tracking_states"]

        em2 = _make_exit_manager(bus, peak_tracking_enabled=True,
                                 peak_triggers=["roc_momo_20m"])
        em2.load_state(state)
        assert "BTC-USD" in em2._peak_state
        assert em2._peak_state["BTC-USD"]["trigger_type"] == "roc_momo_20m"

    def test_priority_hard_stop_over_peak(self, bus):
        """Hard stop fires even if peak tracking is active (higher priority)."""
        em = _make_exit_manager(bus, peak_tracking_enabled=True,
                                peak_triggers=["roc_momo_20m"],
                                hard_stop_pct=0.045,
                                peak_max_hold_minutes=9999)
        portfolio = _make_portfolio(avg_entry=100.0)
        fill = self._make_fill(price=100.0)
        em.on_fill(fill, Portfolio(cash_balance=Decimal("10000")))

        published = []
        bus.subscribe(SignalEvent, lambda e: published.append(e))

        # Price drops -5% → hard stop territory
        em.on_ticker(_make_ticker(price=95.0), portfolio)
        assert len(published) == 1
        assert published[0].signal.reason == "hard_stop"
