"""Tests for Milestone 5 — Risk + Persistence + Observability plugins.

Tests cover:
- BasicRiskManager: signal vetting, exposure limits, daily loss, position limits
- CircuitBreakerRiskManager: trip/reset, rolling losses, rejections, auto-reset
- PostgresStorage: registration, row converters (no live DB required)
- DailyReportObserver: stat accumulation, date rotation, report generation
- AlertingObserver: event handling, rate limiting, alert dispatch
"""

from __future__ import annotations

import time
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from v2.core import registry
from v2.core.event_bus import EventBus
from v2.core.types import (
    Direction,
    Fill,
    FillEvent,
    Order,
    OrderEvent,
    OrderStatus,
    OrderType,
    Portfolio,
    Position,
    RiskEvent,
    Side,
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
def bus():
    return EventBus()


@pytest.fixture
def now():
    return datetime.now(timezone.utc)


def _make_signal(
    direction=Direction.BUY,
    symbol="BTC-USD",
    price=97000.0,
    qty=0.001,
    notional=None,
    reason="test",
    **meta,
) -> Signal:
    return Signal(
        direction=direction,
        symbol=symbol,
        timestamp=datetime.now(timezone.utc),
        price=price,
        qty=qty,
        notional=notional,
        reason=reason,
        metadata=meta,
    )


def _make_fill(
    side=Side.BUY,
    symbol="BTC-USD",
    price=Decimal("97000"),
    qty=Decimal("0.001"),
    fee=Decimal("0.58"),
    fill_id="fill-1",
    order_id="order-1",
) -> Fill:
    return Fill(
        fill_id=fill_id,
        order_id=order_id,
        symbol=symbol,
        side=side,
        price=price,
        qty=qty,
        fee=fee,
        fee_currency="USD",
        is_maker=True,
        timestamp=datetime.now(timezone.utc),
    )


def _make_portfolio(
    cash=Decimal("10000"),
    positions=None,
) -> Portfolio:
    return Portfolio(
        cash_balance=cash,
        positions=positions or {},
        total_equity=cash,
        timestamp=datetime.now(timezone.utc),
    )


def _make_order(
    status=OrderStatus.REJECTED,
    symbol="BTC-USD",
    side=Side.BUY,
    order_id="order-1",
) -> Order:
    return Order(
        order_id=order_id,
        symbol=symbol,
        side=side,
        order_type=OrderType.LIMIT,
        price=Decimal("97000"),
        qty=Decimal("0.001"),
        status=status,
        timestamp=datetime.now(timezone.utc),
    )


# =====================================================================
# Plugin Registration
# =====================================================================


class TestPluginRegistration:
    """Verify all Milestone 5 plugins are discovered."""

    def test_basic_risk_registered(self):
        plugins = registry.list_plugins()
        assert "basic" in plugins.get("risk", [])

    def test_circuit_breaker_registered(self):
        plugins = registry.list_plugins()
        assert "circuit_breaker" in plugins.get("risk", [])

    def test_postgres_registered(self):
        plugins = registry.list_plugins()
        assert "postgres" in plugins.get("storage", [])

    def test_daily_report_registered(self):
        plugins = registry.list_plugins()
        assert "daily_report" in plugins.get("observer", [])

    def test_alerting_registered(self):
        plugins = registry.list_plugins()
        assert "alerting" in plugins.get("observer", [])


# =====================================================================
# BasicRiskManager
# =====================================================================


class TestBasicRiskManager:
    """Tests for the basic risk manager plugin."""

    def _create(self, bus=None, **kwargs):
        from v2.plugins.risk.basic import BasicRiskManager
        kwargs["event_bus"] = bus
        return BasicRiskManager(**kwargs)

    def test_hold_signals_pass_through(self):
        rm = self._create()
        signal = _make_signal(direction=Direction.HOLD)
        portfolio = _make_portfolio()
        result = rm.check_signal(signal, portfolio)
        assert result is signal

    def test_sell_signals_pass_through(self):
        rm = self._create()
        signal = _make_signal(direction=Direction.SELL)
        portfolio = _make_portfolio()
        result = rm.check_signal(signal, portfolio)
        assert result is signal

    def test_buy_signal_passes_within_limits(self):
        rm = self._create(
            max_exposure_per_symbol_usd="5000",
            max_daily_loss_usd="1000",
            max_open_positions=10,
        )
        signal = _make_signal(price=97000.0, qty=0.001)  # ~$97 notional
        portfolio = _make_portfolio()
        result = rm.check_signal(signal, portfolio)
        assert result is signal

    def test_buy_vetoed_max_positions(self, bus):
        rm = self._create(bus=bus, max_open_positions=1)
        # Portfolio with 1 open position
        positions = {
            "BTC-USD": Position(
                symbol="BTC-USD",
                qty=Decimal("0.01"),
                avg_entry_price=Decimal("97000"),
                cost_basis=Decimal("970"),
            ),
        }
        portfolio = _make_portfolio(positions=positions)
        signal = _make_signal(symbol="ETH-USD", price=3000.0, qty=0.1)
        result = rm.check_signal(signal, portfolio)
        assert result is None  # Vetoed

    def test_buy_vetoed_max_exposure(self, bus):
        rm = self._create(bus=bus, max_exposure_per_symbol_usd="100")
        # Position with $970 exposure already
        positions = {
            "BTC-USD": Position(
                symbol="BTC-USD",
                qty=Decimal("0.01"),
                avg_entry_price=Decimal("97000"),
                cost_basis=Decimal("970"),
            ),
        }
        portfolio = _make_portfolio(positions=positions)
        signal = _make_signal(price=97000.0, qty=0.001)  # Another ~$97
        result = rm.check_signal(signal, portfolio)
        assert result is None  # Vetoed

    def test_buy_vetoed_max_order_size(self, bus):
        rm = self._create(bus=bus, max_order_size_usd="50")
        signal = _make_signal(price=97000.0, qty=0.001)  # ~$97
        portfolio = _make_portfolio()
        result = rm.check_signal(signal, portfolio)
        assert result is None  # Vetoed

    def test_buy_vetoed_daily_loss_exceeded(self, bus):
        rm = self._create(bus=bus, max_daily_loss_usd="10")
        # Simulate a loss via on_fill
        position = Position(
            symbol="BTC-USD",
            qty=Decimal("0.001"),
            avg_entry_price=Decimal("98000"),
            cost_basis=Decimal("98"),
        )
        portfolio = _make_portfolio(positions={"BTC-USD": position})
        sell_fill = _make_fill(
            side=Side.SELL,
            price=Decimal("97000"),  # Loss of ~$1
            qty=Decimal("0.001"),
            fee=Decimal("10"),  # Large fee creates a big loss
        )
        rm.on_fill(sell_fill, portfolio)

        # Now try to buy — should be vetoed
        signal = _make_signal(price=97000.0, qty=0.001)
        result = rm.check_signal(signal, portfolio)
        assert result is None  # Vetoed

    def test_pass_through_mode(self):
        rm = self._create(pass_through=True, max_open_positions=0)
        signal = _make_signal()
        portfolio = _make_portfolio()
        result = rm.check_signal(signal, portfolio)
        assert result is signal

    def test_on_fill_tracks_exposure_buy(self):
        rm = self._create()
        portfolio = _make_portfolio()
        fill = _make_fill(side=Side.BUY, price=Decimal("97000"), qty=Decimal("0.001"))
        rm.on_fill(fill, portfolio)
        assert rm._symbol_exposure.get("BTC-USD", Decimal("0")) == Decimal("97")

    def test_on_fill_decreases_exposure_sell(self):
        rm = self._create()
        portfolio = _make_portfolio()
        # Buy first
        buy_fill = _make_fill(side=Side.BUY, price=Decimal("97000"), qty=Decimal("0.001"))
        rm.on_fill(buy_fill, portfolio)
        # Sell
        sell_fill = _make_fill(side=Side.SELL, price=Decimal("97000"), qty=Decimal("0.001"))
        position = Position(
            symbol="BTC-USD",
            qty=Decimal("0.001"),
            avg_entry_price=Decimal("97000"),
            cost_basis=Decimal("97"),
        )
        portfolio_with_pos = _make_portfolio(positions={"BTC-USD": position})
        rm.on_fill(sell_fill, portfolio_with_pos)
        assert rm._symbol_exposure.get("BTC-USD", Decimal("0")) == Decimal("0")

    def test_publishes_risk_event_on_veto(self, bus):
        rm = self._create(bus=bus, max_open_positions=0)
        events = []
        bus.subscribe(RiskEvent, lambda e: events.append(e))

        portfolio = _make_portfolio()
        signal = _make_signal()
        rm.check_signal(signal, portfolio)
        assert len(events) == 1
        assert events[0].event_type == "signal_vetoed"

    def test_state_persistence(self):
        rm = self._create()
        rm._daily_realized_pnl = Decimal("-50")
        rm._pnl_date = date(2026, 2, 7)
        rm._symbol_exposure = {"BTC-USD": Decimal("500")}

        state = rm.get_state()
        rm2 = self._create()
        rm2.load_state(state)

        assert rm2._daily_realized_pnl == Decimal("-50")
        assert rm2._pnl_date == date(2026, 2, 7)
        assert rm2._symbol_exposure == {"BTC-USD": Decimal("500")}

    def test_configure_updates_limits(self):
        rm = self._create()
        rm.configure({
            "max_exposure_per_symbol_usd": "200",
            "max_daily_loss_usd": "50",
            "max_open_positions": 3,
            "max_order_size_usd": "100",
        })
        assert rm._max_exposure_per_symbol == Decimal("200")
        assert rm._max_daily_loss == Decimal("50")
        assert rm._max_positions == 3
        assert rm._max_order_size == Decimal("100")

    def test_notional_based_signal(self, bus):
        """Signal with notional instead of qty*price."""
        rm = self._create(bus=bus, max_order_size_usd="50")
        signal = _make_signal(qty=None, price=None, notional=100.0)
        portfolio = _make_portfolio()
        result = rm.check_signal(signal, portfolio)
        assert result is None  # $100 > $50 limit

    def test_on_position_update_returns_none(self):
        rm = self._create()
        pos = Position(
            symbol="BTC-USD",
            qty=Decimal("0.01"),
            avg_entry_price=Decimal("97000"),
            cost_basis=Decimal("970"),
        )
        assert rm.on_position_update(pos) is None


# =====================================================================
# CircuitBreakerRiskManager
# =====================================================================


class TestCircuitBreaker:
    """Tests for the circuit breaker risk manager."""

    def _create(self, bus=None, **kwargs):
        from v2.plugins.risk.circuit_breaker import CircuitBreakerRiskManager
        kwargs["event_bus"] = bus
        return CircuitBreakerRiskManager(**kwargs)

    def test_signal_passes_when_not_tripped(self):
        cb = self._create()
        signal = _make_signal()
        portfolio = _make_portfolio()
        result = cb.check_signal(signal, portfolio)
        assert result is signal

    def test_signal_vetoed_when_tripped(self):
        cb = self._create()
        cb._trip("test reason")
        signal = _make_signal()
        portfolio = _make_portfolio()
        result = cb.check_signal(signal, portfolio)
        assert result is None

    def test_large_single_loss_trips(self, bus):
        cb = self._create(bus=bus, large_loss_threshold_usd="10")
        events = []
        bus.subscribe(RiskEvent, lambda e: events.append(e))

        position = Position(
            symbol="BTC-USD",
            qty=Decimal("0.01"),
            avg_entry_price=Decimal("98000"),
            cost_basis=Decimal("980"),
        )
        portfolio = _make_portfolio(positions={"BTC-USD": position})

        # Sell at big loss
        fill = _make_fill(
            side=Side.SELL,
            price=Decimal("96000"),  # $20 loss per unit * 0.01 = $200 loss
            qty=Decimal("0.01"),
            fee=Decimal("1"),
        )
        cb.on_fill(fill, portfolio)

        assert cb.is_tripped
        assert "large_single_loss" in cb._trip_reason
        assert len(events) == 1
        assert events[0].event_type == "circuit_breaker"

    def test_rapid_losses_trip(self, bus):
        cb = self._create(
            bus=bus,
            max_losses_in_window=3,
            loss_window_minutes=60,
            large_loss_threshold_usd="999999",  # Won't trip on single loss
        )

        position = Position(
            symbol="BTC-USD",
            qty=Decimal("1"),
            avg_entry_price=Decimal("98000"),
            cost_basis=Decimal("98000"),
        )
        portfolio = _make_portfolio(positions={"BTC-USD": position})

        # Create 3 small losses
        for i in range(3):
            fill = _make_fill(
                fill_id=f"fill-{i}",
                side=Side.SELL,
                price=Decimal("97999"),  # Small loss
                qty=Decimal("0.001"),
                fee=Decimal("0.01"),
            )
            cb.on_fill(fill, portfolio)

        assert cb.is_tripped
        assert "rapid_losses" in cb._trip_reason

    def test_consecutive_rejections_trip(self, bus):
        cb = self._create(bus=bus, max_consecutive_rejections=3)

        for _ in range(3):
            cb.on_rejection()

        assert cb.is_tripped
        assert "consecutive_rejections" in cb._trip_reason

    def test_fill_resets_rejection_counter(self):
        cb = self._create(max_consecutive_rejections=5)
        cb._consecutive_rejections = 4  # One away from tripping

        position = Position(
            symbol="BTC-USD",
            qty=Decimal("0.01"),
            avg_entry_price=Decimal("97000"),
            cost_basis=Decimal("970"),
        )
        portfolio = _make_portfolio(positions={"BTC-USD": position})

        # Successful buy fill resets counter
        fill = _make_fill(side=Side.BUY)
        cb.on_fill(fill, portfolio)
        assert cb._consecutive_rejections == 0

    def test_manual_reset(self):
        cb = self._create()
        cb._trip("test reason")
        assert cb.is_tripped

        cb.reset()
        assert not cb.is_tripped
        assert cb._trip_reason == ""
        assert cb._consecutive_rejections == 0

    def test_auto_reset_after_cooldown(self):
        cb = self._create(cooldown_minutes=1)
        cb._trip("test reason")
        # Pretend trip happened 2 minutes ago
        cb._trip_time = time.time() - 120

        signal = _make_signal()
        portfolio = _make_portfolio()
        result = cb.check_signal(signal, portfolio)
        assert result is signal  # Auto-reset allows signal
        assert not cb.is_tripped

    def test_no_auto_reset_before_cooldown(self):
        cb = self._create(cooldown_minutes=60)
        cb._trip("test reason")
        cb._trip_time = time.time() - 10  # 10 seconds ago

        signal = _make_signal()
        portfolio = _make_portfolio()
        result = cb.check_signal(signal, portfolio)
        assert result is None  # Still tripped

    def test_buy_fills_ignored(self):
        """Only sell fills are tracked for losses."""
        cb = self._create(large_loss_threshold_usd="1")
        position = Position(
            symbol="BTC-USD",
            qty=Decimal("0.01"),
            avg_entry_price=Decimal("97000"),
            cost_basis=Decimal("970"),
        )
        portfolio = _make_portfolio(positions={"BTC-USD": position})

        fill = _make_fill(side=Side.BUY)
        cb.on_fill(fill, portfolio)
        assert not cb.is_tripped

    def test_configure_updates_thresholds(self):
        cb = self._create()
        cb.configure({
            "max_losses_in_window": 10,
            "loss_window_minutes": 15,
            "max_consecutive_rejections": 20,
            "large_loss_threshold_usd": "1000",
            "cooldown_minutes": 120,
        })
        assert cb._max_losses_in_window == 10
        assert cb._loss_window_minutes == 15
        assert cb._max_consecutive_rejections == 20
        assert cb._large_loss_threshold == Decimal("1000")
        assert cb._cooldown_minutes == 120

    def test_state_persistence(self):
        cb = self._create()
        cb._trip("test reason")
        cb._consecutive_rejections = 5

        state = cb.get_state()
        cb2 = self._create()
        cb2.load_state(state)

        assert cb2._tripped
        assert cb2._trip_reason == "test reason"
        assert cb2._consecutive_rejections == 5

    def test_tripped_fills_ignored(self):
        """Fills are ignored when already tripped."""
        cb = self._create(large_loss_threshold_usd="1")
        cb._trip("already tripped")

        position = Position(
            symbol="BTC-USD",
            qty=Decimal("1"),
            avg_entry_price=Decimal("99000"),
            cost_basis=Decimal("99000"),
        )
        portfolio = _make_portfolio(positions={"BTC-USD": position})

        fill = _make_fill(
            side=Side.SELL,
            price=Decimal("90000"),
            qty=Decimal("1"),
        )
        # Should not raise or change state
        cb.on_fill(fill, portfolio)
        assert cb.is_tripped
        assert cb._trip_reason == "already tripped"

    def test_on_position_update_returns_none(self):
        cb = self._create()
        pos = Position(
            symbol="BTC-USD",
            qty=Decimal("0.01"),
            avg_entry_price=Decimal("97000"),
            cost_basis=Decimal("970"),
        )
        assert cb.on_position_update(pos) is None

    def test_loss_window_prunes_old(self):
        """Losses outside the window are pruned."""
        cb = self._create(
            max_losses_in_window=3,
            loss_window_minutes=1,
            large_loss_threshold_usd="999999",
        )
        # Add old losses (2 minutes ago)
        old_time = time.time() - 120
        cb._recent_losses.append((old_time, Decimal("-5")))
        cb._recent_losses.append((old_time, Decimal("-5")))

        position = Position(
            symbol="BTC-USD",
            qty=Decimal("1"),
            avg_entry_price=Decimal("98000"),
            cost_basis=Decimal("98000"),
        )
        portfolio = _make_portfolio(positions={"BTC-USD": position})

        # New loss should prune old ones
        fill = _make_fill(
            side=Side.SELL,
            price=Decimal("97999"),
            qty=Decimal("0.001"),
            fee=Decimal("0.01"),
        )
        cb.on_fill(fill, portfolio)

        # Old losses pruned, only 1 recent loss
        assert len(cb._recent_losses) == 1
        assert not cb.is_tripped


# =====================================================================
# PostgresStorage
# =====================================================================


class TestPostgresStorage:
    """Tests for PostgreSQL storage adapter (no live DB needed)."""

    def _create(self, **kwargs):
        from v2.plugins.persistence.postgres import PostgresStorage
        return PostgresStorage(dsn="postgresql://test:test@localhost/test", **kwargs)

    def test_instantiation(self):
        ps = self._create()
        assert ps.name == "postgres"
        assert ps._pool is None

    def test_dsn_from_env(self):
        with patch.dict("os.environ", {"DATABASE_URL": "postgresql://env:env@host/db"}):
            from v2.plugins.persistence.postgres import PostgresStorage
            ps = PostgresStorage()
            assert ps._dsn == "postgresql://env:env@host/db"

    def test_dsn_custom_env(self):
        with patch.dict("os.environ", {"MY_DB": "postgresql://custom@host/db"}):
            from v2.plugins.persistence.postgres import PostgresStorage
            ps = PostgresStorage(dsn_env="MY_DB")
            assert ps._dsn == "postgresql://custom@host/db"

    def test_row_to_fill(self):
        from v2.plugins.persistence.postgres import PostgresStorage

        row = {
            "fill_id": "fill-123",
            "order_id": "order-456",
            "symbol": "BTC-USD",
            "side": "buy",
            "price": 97000.0,
            "qty": 0.001,
            "fee": 0.58,
            "fee_currency": "USD",
            "is_maker": True,
            "timestamp": datetime(2026, 2, 7, 12, 0, 0, tzinfo=timezone.utc),
            "metadata": '{"source": "test"}',
        }
        fill = PostgresStorage._row_to_fill(row)
        assert fill.fill_id == "fill-123"
        assert fill.side == Side.BUY
        assert fill.price == Decimal("97000.0")
        assert fill.qty == Decimal("0.001")
        assert fill.metadata == {"source": "test"}

    def test_row_to_fill_json_metadata(self):
        from v2.plugins.persistence.postgres import PostgresStorage

        row = {
            "fill_id": "fill-1",
            "order_id": "order-1",
            "symbol": "ETH-USD",
            "side": "sell",
            "price": 3000.0,
            "qty": 0.5,
            "fee": 1.8,
            "fee_currency": "USD",
            "is_maker": False,
            "timestamp": datetime(2026, 2, 7, tzinfo=timezone.utc),
            "metadata": {"already": "parsed"},  # Already a dict (asyncpg JSONB)
        }
        fill = PostgresStorage._row_to_fill(row)
        assert fill.metadata == {"already": "parsed"}

    def test_row_to_fill_null_metadata(self):
        from v2.plugins.persistence.postgres import PostgresStorage

        row = {
            "fill_id": "fill-1",
            "order_id": "order-1",
            "symbol": "BTC-USD",
            "side": "buy",
            "price": 97000.0,
            "qty": 0.001,
            "fee": 0.0,
            "fee_currency": "USD",
            "is_maker": True,
            "timestamp": datetime(2026, 2, 7, tzinfo=timezone.utc),
            "metadata": None,
        }
        fill = PostgresStorage._row_to_fill(row)
        assert fill.metadata == {}

    def test_row_to_position(self):
        from v2.plugins.persistence.postgres import PostgresStorage

        row = {
            "symbol": "BTC-USD",
            "qty": 0.01,
            "avg_entry_price": 97000.0,
            "cost_basis": 970.0,
            "unrealized_pnl": 15.5,
            "realized_pnl": -3.2,
            "entry_time": datetime(2026, 2, 7, 10, 0, 0, tzinfo=timezone.utc),
        }
        pos = PostgresStorage._row_to_position(row)
        assert pos.symbol == "BTC-USD"
        assert pos.qty == Decimal("0.01")
        assert pos.avg_entry_price == Decimal("97000.0")
        assert pos.unrealized_pnl == Decimal("15.5")
        assert pos.realized_pnl == Decimal("-3.2")

    def test_row_to_position_null_pnl(self):
        from v2.plugins.persistence.postgres import PostgresStorage

        row = {
            "symbol": "BTC-USD",
            "qty": 0.01,
            "avg_entry_price": 97000.0,
            "cost_basis": 970.0,
            "unrealized_pnl": None,
            "realized_pnl": None,
            "entry_time": None,
        }
        pos = PostgresStorage._row_to_position(row)
        assert pos.unrealized_pnl == Decimal("0")
        assert pos.realized_pnl == Decimal("0")

    def test_pool_size_config(self):
        ps = self._create(pool_size=10)
        assert ps._pool_size == 10


# =====================================================================
# DailyReportObserver
# =====================================================================


class TestDailyReport:
    """Tests for the daily report observer."""

    def _create(self, **kwargs):
        from v2.plugins.observability.daily_report import DailyReportObserver
        defaults = {"enabled": False}  # Disable email sending in tests
        defaults.update(kwargs)
        return DailyReportObserver(**defaults)

    def test_instantiation(self):
        dr = self._create()
        assert dr.name == "daily_report"

    def test_fill_accumulation_buy(self, now):
        dr = self._create()
        fill = _make_fill(side=Side.BUY, price=Decimal("97000"), qty=Decimal("0.001"))
        event = FillEvent(fill=fill)
        dr.on_event(event)

        stats = dr.get_stats()
        assert stats["buy_count"] == 1
        assert stats["sell_count"] == 0
        assert Decimal(stats["buy_volume_usd"]) == Decimal("97")

    def test_fill_accumulation_sell(self, now):
        dr = self._create()
        fill = _make_fill(side=Side.SELL, price=Decimal("97000"), qty=Decimal("0.001"))
        event = FillEvent(fill=fill)
        dr.on_event(event)

        stats = dr.get_stats()
        assert stats["sell_count"] == 1
        assert stats["buy_count"] == 0
        assert Decimal(stats["sell_volume_usd"]) == Decimal("97")

    def test_fill_accumulation_fees(self):
        dr = self._create()
        fill = _make_fill(fee=Decimal("1.50"))
        event = FillEvent(fill=fill)
        dr.on_event(event)

        stats = dr.get_stats()
        assert Decimal(stats["total_fees"]) == Decimal("1.50")

    def test_signal_counting(self):
        dr = self._create()
        signal = _make_signal()
        event = SignalEvent(signal=signal, strategy_name="hybrid_4h_maker")
        dr.on_event(event)
        dr.on_event(event)

        event2 = SignalEvent(signal=signal, strategy_name="composite_scoring")
        dr.on_event(event2)

        stats = dr.get_stats()
        assert stats["signal_count"]["hybrid_4h_maker"] == 2
        assert stats["signal_count"]["composite_scoring"] == 1

    def test_risk_event_tracking(self):
        dr = self._create()
        event = RiskEvent(
            event_type="signal_vetoed",
            reason="max_positions_reached",
        )
        dr.on_event(event)

        stats = dr.get_stats()
        assert len(stats["risk_events"]) == 1
        assert "signal_vetoed" in stats["risk_events"][0]

    def test_order_rejection_counting(self):
        dr = self._create()
        order = _make_order(status=OrderStatus.REJECTED)
        event = OrderEvent(order=order, event_type="submitted")
        dr.on_event(event)

        stats = dr.get_stats()
        assert stats["rejection_count"] == 1

    def test_non_rejected_order_not_counted(self):
        dr = self._create()
        order = _make_order(status=OrderStatus.FILLED)
        event = OrderEvent(order=order, event_type="submitted")
        dr.on_event(event)

        stats = dr.get_stats()
        assert stats["rejection_count"] == 0

    def test_report_body_generation(self):
        dr = self._create()
        # Add some data
        fill = _make_fill(side=Side.BUY, price=Decimal("97000"), qty=Decimal("0.001"))
        dr.on_event(FillEvent(fill=fill))
        dr.on_event(SignalEvent(
            signal=_make_signal(),
            strategy_name="test_strategy",
        ))

        body = dr._generate_report_body()
        assert "TRADING ACTIVITY" in body
        assert "Buys:   1 trades" in body
        assert "SIGNALS" in body
        assert "test_strategy: 1 signals" in body
        assert "BotTrader v2" in body

    def test_report_body_no_activity(self):
        dr = self._create()
        body = dr._generate_report_body()
        assert "Buys:   0 trades" in body
        assert "No signals generated" in body
        assert "No risk events" in body

    def test_has_activity_true(self):
        dr = self._create()
        dr._buy_count = 1
        assert dr._has_activity()

    def test_has_activity_false(self):
        dr = self._create()
        assert not dr._has_activity()

    def test_send_report_disabled(self):
        dr = self._create(enabled=False)
        assert dr.send_report() is False

    def test_reset_stats(self):
        dr = self._create()
        dr._buy_count = 5
        dr._sell_count = 3
        dr._total_fees = Decimal("10")
        dr._signal_count = {"test": 5}

        dr._reset_stats()
        stats = dr.get_stats()
        assert stats["buy_count"] == 0
        assert stats["sell_count"] == 0
        assert Decimal(stats["total_fees"]) == Decimal("0")
        assert stats["signal_count"] == {}


# =====================================================================
# AlertingObserver
# =====================================================================


class TestAlerting:
    """Tests for the alerting observer."""

    def _create(self, **kwargs):
        from v2.plugins.observability.alerting import AlertingObserver
        return AlertingObserver(**kwargs)

    def test_instantiation(self):
        ao = self._create()
        assert ao.name == "alerting"

    def test_disabled_ignores_events(self):
        ao = self._create(enabled=False)
        event = RiskEvent(event_type="circuit_breaker", reason="test")
        ao.on_event(event)  # Should not raise

    def test_circuit_breaker_triggers_alert(self):
        ao = self._create(
            sender="test@test.com",
            password="secret",
            email_recipients="admin@test.com",
        )
        with patch.object(ao, "_send_email", return_value=True) as mock_send:
            event = RiskEvent(
                event_type="circuit_breaker",
                reason="rapid_losses",
                metadata={"cooldown_minutes": 60},
            )
            ao.on_event(event)
            mock_send.assert_called_once()
            call_args = mock_send.call_args
            assert "Circuit Breaker" in call_args[0][0]
            assert "rapid_losses" in call_args[0][1]

    def test_signal_vetoed_triggers_alert(self):
        ao = self._create(
            sender="test@test.com",
            password="secret",
            email_recipients="admin@test.com",
        )
        with patch.object(ao, "_send_email", return_value=True) as mock_send:
            event = RiskEvent(
                event_type="signal_vetoed",
                reason="max_positions",
                metadata={"symbol": "BTC-USD", "direction": "buy"},
            )
            ao.on_event(event)
            mock_send.assert_called_once()
            assert "Vetoed" in mock_send.call_args[0][0]

    def test_large_fill_triggers_alert(self):
        ao = self._create(
            sender="test@test.com",
            password="secret",
            email_recipients="admin@test.com",
            large_fill_threshold_usd=50,
        )
        with patch.object(ao, "_send_email", return_value=True) as mock_send:
            fill = _make_fill(
                price=Decimal("97000"),
                qty=Decimal("0.001"),  # $97 > $50 threshold
            )
            event = FillEvent(fill=fill)
            ao.on_event(event)
            mock_send.assert_called_once()
            assert "Large Fill" in mock_send.call_args[0][0]

    def test_small_fill_no_alert(self):
        ao = self._create(
            sender="test@test.com",
            password="secret",
            email_recipients="admin@test.com",
            large_fill_threshold_usd=1000,
        )
        with patch.object(ao, "_send_email", return_value=True) as mock_send:
            fill = _make_fill(
                price=Decimal("97000"),
                qty=Decimal("0.001"),  # $97 < $1000 threshold
            )
            event = FillEvent(fill=fill)
            ao.on_event(event)
            mock_send.assert_not_called()

    def test_order_rejection_triggers_alert(self):
        ao = self._create(
            sender="test@test.com",
            password="secret",
            email_recipients="admin@test.com",
        )
        with patch.object(ao, "_send_email", return_value=True) as mock_send:
            order = _make_order(status=OrderStatus.REJECTED)
            event = OrderEvent(order=order, event_type="submitted")
            ao.on_event(event)
            mock_send.assert_called_once()
            assert "Rejected" in mock_send.call_args[0][0]

    def test_filled_order_no_alert(self):
        ao = self._create(
            sender="test@test.com",
            password="secret",
            email_recipients="admin@test.com",
        )
        with patch.object(ao, "_send_email", return_value=True) as mock_send:
            order = _make_order(status=OrderStatus.FILLED)
            event = OrderEvent(order=order, event_type="submitted")
            ao.on_event(event)
            mock_send.assert_not_called()

    def test_rate_limiting(self):
        ao = self._create(
            sender="test@test.com",
            password="secret",
            email_recipients="admin@test.com",
            min_interval_seconds=300,
        )
        with patch.object(ao, "_send_email", return_value=True) as mock_send:
            event = RiskEvent(event_type="circuit_breaker", reason="test1")
            ao.on_event(event)
            assert mock_send.call_count == 1

            # Second alert within 5 minutes — should be rate-limited
            event2 = RiskEvent(event_type="circuit_breaker", reason="test2")
            ao.on_event(event2)
            assert mock_send.call_count == 1  # Still 1

    def test_rate_limiting_expires(self):
        ao = self._create(
            sender="test@test.com",
            password="secret",
            email_recipients="admin@test.com",
            min_interval_seconds=1,
        )
        with patch.object(ao, "_send_email", return_value=True) as mock_send:
            event = RiskEvent(event_type="circuit_breaker", reason="test1")
            ao.on_event(event)
            assert mock_send.call_count == 1

            # Pretend time passed
            ao._last_alert_time = time.time() - 2

            event2 = RiskEvent(event_type="circuit_breaker", reason="test2")
            ao.on_event(event2)
            assert mock_send.call_count == 2

    def test_sms_mode(self):
        ao = self._create(
            sender="test@test.com",
            password="secret",
            phone="5551234567",
            mode="sms",
        )
        with patch.object(ao, "_send_sms", return_value=True) as mock_sms:
            with patch.object(ao, "_send_email") as mock_email:
                event = RiskEvent(event_type="circuit_breaker", reason="test")
                ao.on_event(event)
                mock_sms.assert_called_once()
                mock_email.assert_not_called()

    def test_both_mode(self):
        ao = self._create(
            sender="test@test.com",
            password="secret",
            email_recipients="admin@test.com",
            phone="5551234567",
            mode="both",
        )
        with patch.object(ao, "_send_email", return_value=True) as mock_email:
            with patch.object(ao, "_send_sms", return_value=True) as mock_sms:
                event = RiskEvent(event_type="circuit_breaker", reason="test")
                ao.on_event(event)
                mock_email.assert_called_once()
                mock_sms.assert_called_once()

    def test_sms_recipient_format(self):
        ao = self._create(phone="5551234567", sms_gateway="txt.att.net")
        assert ao._sms_recipient == "5551234567@txt.att.net"

    def test_no_phone_no_sms_recipient(self):
        ao = self._create()
        assert ao._sms_recipient == ""

    def test_send_email_not_configured(self):
        ao = self._create()  # No sender/password/recipients
        result = ao._send_email("test", "body")
        assert result is False

    def test_send_sms_not_configured(self):
        ao = self._create()  # No sender/password/phone
        result = ao._send_sms("test", "body")
        assert result is False

    def test_manual_alert_bypasses_rate_limit(self):
        ao = self._create(
            sender="test@test.com",
            password="secret",
            email_recipients="admin@test.com",
            min_interval_seconds=300,
        )
        with patch.object(ao, "_send_email", return_value=True):
            # First regular alert
            event = RiskEvent(event_type="circuit_breaker", reason="test")
            ao.on_event(event)

            # Manual alert should bypass rate limit
            result = ao.send_manual_alert("Manual", "Alert body")
            assert result is True
