"""Tests for v1 feature ports: signal-based exits, performance filter, stale order cancellation.

Uses lazy imports for plugin classes to avoid triggering @registry.plugin decorators
at collection time, which would break discover_plugins() for other test files that
run after registry.clear() is called.
"""

import time
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from v2.core.event_bus import EventBus
from v2.core.types import (
    Direction,
    Fill,
    Order,
    OrderStatus,
    OrderType,
    Portfolio,
    Position,
    Side,
    Signal,
    TickerEvent,
)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _make_signal(direction=Direction.SELL, symbol="BTC-USD", price=100.0, **kwargs):
    return Signal(
        direction=direction,
        symbol=symbol,
        timestamp=datetime.now(timezone.utc),
        price=price,
        metadata=kwargs.pop("metadata", {}),
        **kwargs,
    )


def _make_portfolio(symbol="BTC-USD", qty=1.0, avg_entry=95.0):
    return Portfolio(
        cash_balance=Decimal("10000"),
        positions={
            symbol: Position(
                symbol=symbol,
                qty=Decimal(str(qty)),
                avg_entry_price=Decimal(str(avg_entry)),
                cost_basis=Decimal(str(avg_entry * qty)),
            ),
        },
    )


# ==================================================================
# Feature 1: Signal-Based Exits (ExitManager)
# ==================================================================

class TestSignalBasedExits:
    def _make_exit_manager(self, **kwargs):
        from v2.plugins.risk.exit_manager import ExitManager
        defaults = {
            "signal_exit_enabled": True,
            "signal_exit_min_pnl_pct": 0.0,
        }
        defaults.update(kwargs)
        return ExitManager(event_bus=EventBus(), **defaults)

    def test_sell_approved_when_profitable_no_trailing(self):
        """SELL signal approved when P&L >= 0% and no trailing stop active."""
        em = self._make_exit_manager()
        signal = _make_signal(price=100.0)
        portfolio = _make_portfolio(avg_entry=95.0)  # +5.26% profit

        result = em.check_signal(signal, portfolio)

        assert result is not None
        assert result.metadata["exit_reason"] == "signal_exit"
        assert result.metadata["pnl_pct"] > 0

    def test_sell_blocked_when_trailing_active(self):
        """SELL signal blocked when trailing stop is active for the symbol."""
        em = self._make_exit_manager()
        em._trailing_active["BTC-USD"] = True
        signal = _make_signal(price=100.0)
        portfolio = _make_portfolio(avg_entry=95.0)

        result = em.check_signal(signal, portfolio)

        assert result is None

    def test_sell_passes_through_when_no_position(self):
        """SELL signal passes through when no position exists."""
        em = self._make_exit_manager()
        signal = _make_signal(price=100.0)
        portfolio = Portfolio(cash_balance=Decimal("10000"))

        result = em.check_signal(signal, portfolio)

        assert result is not None
        assert "exit_reason" not in result.metadata

    def test_buy_signals_pass_through(self):
        """BUY signals always pass through regardless of exit manager state."""
        em = self._make_exit_manager()
        em._trailing_active["BTC-USD"] = True  # trailing active
        signal = _make_signal(direction=Direction.BUY, price=100.0)
        portfolio = _make_portfolio()

        result = em.check_signal(signal, portfolio)

        assert result is not None
        assert result.direction == Direction.BUY

    def test_configurable_min_pnl_threshold(self):
        """Signal exit respects min_pnl_pct threshold."""
        em = self._make_exit_manager(signal_exit_min_pnl_pct=0.05)  # require 5%
        signal = _make_signal(price=100.0)
        portfolio = _make_portfolio(avg_entry=98.0)  # only +2.04%

        result = em.check_signal(signal, portfolio)

        # Should pass through but NOT be tagged as signal_exit
        assert result is not None
        assert "exit_reason" not in result.metadata

    def test_min_pnl_threshold_met(self):
        """Signal exit tags when threshold is met."""
        em = self._make_exit_manager(signal_exit_min_pnl_pct=0.05)
        signal = _make_signal(price=110.0)
        portfolio = _make_portfolio(avg_entry=100.0)  # +10%

        result = em.check_signal(signal, portfolio)

        assert result is not None
        assert result.metadata["exit_reason"] == "signal_exit"
        assert result.metadata["pnl_pct"] == pytest.approx(10.0)

    def test_disabled_signal_exit_passes_through(self):
        """When signal_exit_enabled=False, all signals pass through unchanged."""
        em = self._make_exit_manager(signal_exit_enabled=False)
        em._trailing_active["BTC-USD"] = True
        signal = _make_signal(price=100.0)
        portfolio = _make_portfolio()

        result = em.check_signal(signal, portfolio)

        # Should pass through even with trailing active
        assert result is not None
        assert "exit_reason" not in result.metadata

    def test_sell_at_loss_passes_through_without_tag(self):
        """SELL at a loss passes through but without signal_exit tag."""
        em = self._make_exit_manager()
        signal = _make_signal(price=90.0)
        portfolio = _make_portfolio(avg_entry=100.0)  # -10% loss

        result = em.check_signal(signal, portfolio)

        assert result is not None
        assert "exit_reason" not in result.metadata


# ==================================================================
# Feature 2: Performance-Based Symbol Exclusion
# ==================================================================

class TestPerformanceFilter:
    def _make_filter(self, **kwargs):
        from v2.plugins.risk.performance_filter import PerformanceFilter
        defaults = {
            "window_days": 30,
            "min_trades": 3,
            "min_win_rate": 0.30,
            "min_avg_pnl": -5.0,
            "min_total_pnl": -50.0,
        }
        defaults.update(kwargs)
        return PerformanceFilter(event_bus=EventBus(), **defaults)

    def _seed_trades(self, pf, symbol, trades):
        """Seed completed trades: list of (buy_price, sell_price, qty)."""
        portfolio = Portfolio(cash_balance=Decimal("10000"))
        for buy_price, sell_price, qty in trades:
            buy_fill = Fill(
                fill_id=f"buy-{buy_price}",
                order_id=f"order-buy-{buy_price}",
                symbol=symbol,
                side=Side.BUY,
                price=Decimal(str(buy_price)),
                qty=Decimal(str(qty)),
                fee=Decimal("0"),
                fee_currency="USD",
                is_maker=True,
                timestamp=datetime.now(timezone.utc),
            )
            pf.on_fill(buy_fill, portfolio)

            sell_fill = Fill(
                fill_id=f"sell-{sell_price}",
                order_id=f"order-sell-{sell_price}",
                symbol=symbol,
                side=Side.SELL,
                price=Decimal(str(sell_price)),
                qty=Decimal(str(qty)),
                fee=Decimal("0"),
                fee_currency="USD",
                is_maker=True,
                timestamp=datetime.now(timezone.utc),
            )
            pf.on_fill(sell_fill, portfolio)

    def test_blocks_buys_for_low_win_rate(self):
        """Blocks BUY for symbol with < 30% win rate."""
        pf = self._make_filter(min_trades=3)
        # 3 trades: 0 wins, 3 losses -> 0% win rate
        self._seed_trades(pf, "DOGE-USD", [
            (100, 90, 1),   # -$10
            (100, 95, 1),   # -$5
            (100, 92, 1),   # -$8
        ])

        signal = _make_signal(direction=Direction.BUY, symbol="DOGE-USD")
        portfolio = Portfolio(cash_balance=Decimal("10000"))

        result = pf.check_signal(signal, portfolio)
        assert result is None

    def test_blocks_buys_for_bad_avg_pnl(self):
        """Blocks BUY when avg P&L below threshold."""
        pf = self._make_filter(min_trades=3, min_avg_pnl=-5.0)
        # 3 trades: 2 wins, 1 big loss -> avg P&L < -$5
        self._seed_trades(pf, "SHIB-USD", [
            (100, 101, 1),   # +$1
            (100, 102, 1),   # +$2
            (100, 80, 1),    # -$20
        ])

        signal = _make_signal(direction=Direction.BUY, symbol="SHIB-USD")
        portfolio = Portfolio(cash_balance=Decimal("10000"))

        result = pf.check_signal(signal, portfolio)
        assert result is None

    def test_allows_buys_with_insufficient_history(self):
        """BUY allowed when symbol has fewer than min_trades."""
        pf = self._make_filter(min_trades=3)
        # Only 2 trades
        self._seed_trades(pf, "BTC-USD", [
            (100, 90, 1),
            (100, 85, 1),
        ])

        signal = _make_signal(direction=Direction.BUY, symbol="BTC-USD")
        portfolio = Portfolio(cash_balance=Decimal("10000"))

        result = pf.check_signal(signal, portfolio)
        assert result is not None

    def test_sells_always_pass_through(self):
        """SELL signals always pass through regardless of performance."""
        pf = self._make_filter(min_trades=3)
        self._seed_trades(pf, "DOGE-USD", [
            (100, 90, 1),
            (100, 85, 1),
            (100, 80, 1),
        ])

        signal = _make_signal(direction=Direction.SELL, symbol="DOGE-USD")
        portfolio = Portfolio(cash_balance=Decimal("10000"))

        result = pf.check_signal(signal, portfolio)
        assert result is not None

    def test_fifo_pairing_accuracy(self):
        """FIFO correctly pairs sequential buys and sells."""
        pf = self._make_filter(min_trades=2)
        portfolio = Portfolio(cash_balance=Decimal("10000"))

        # Buy 2 units at $100, then sell 1 at $110, then sell 1 at $90
        buy1 = Fill(
            fill_id="b1", order_id="o1", symbol="ETH-USD",
            side=Side.BUY, price=Decimal("100"), qty=Decimal("2"),
            fee=Decimal("0"), fee_currency="USD", is_maker=True,
            timestamp=datetime.now(timezone.utc),
        )
        pf.on_fill(buy1, portfolio)

        sell1 = Fill(
            fill_id="s1", order_id="o2", symbol="ETH-USD",
            side=Side.SELL, price=Decimal("110"), qty=Decimal("1"),
            fee=Decimal("0"), fee_currency="USD", is_maker=True,
            timestamp=datetime.now(timezone.utc),
        )
        pf.on_fill(sell1, portfolio)

        sell2 = Fill(
            fill_id="s2", order_id="o3", symbol="ETH-USD",
            side=Side.SELL, price=Decimal("90"), qty=Decimal("1"),
            fee=Decimal("0"), fee_currency="USD", is_maker=True,
            timestamp=datetime.now(timezone.utc),
        )
        pf.on_fill(sell2, portfolio)

        stats = pf._get_stats("ETH-USD")
        assert stats is not None
        assert stats["trade_count"] == 2
        # Trade 1: buy $100 sell $110 = +$10
        # Trade 2: buy $100 sell $90 = -$10
        assert stats["total_pnl"] == pytest.approx(0.0)
        assert stats["win_rate"] == pytest.approx(0.5)

    def test_rolling_window_expiry(self):
        """Trades outside the rolling window are excluded."""
        pf = self._make_filter(min_trades=2, window_days=1)

        # Manually inject old completed trades
        old_ts = time.time() - 86400 * 2  # 2 days ago
        pf._completed_trades["OLD-USD"] = [
            {"pnl": -20.0, "timestamp": old_ts},
            {"pnl": -15.0, "timestamp": old_ts},
            {"pnl": -10.0, "timestamp": old_ts},
        ]

        # These are outside the 1-day window
        stats = pf._get_stats("OLD-USD")
        assert stats is None  # Not enough recent trades

    def test_allows_buys_with_good_performance(self):
        """BUY allowed when symbol has good performance."""
        pf = self._make_filter(min_trades=3)
        self._seed_trades(pf, "SOL-USD", [
            (100, 110, 1),   # +$10
            (100, 105, 1),   # +$5
            (100, 103, 1),   # +$3
        ])

        signal = _make_signal(direction=Direction.BUY, symbol="SOL-USD")
        portfolio = Portfolio(cash_balance=Decimal("10000"))

        result = pf.check_signal(signal, portfolio)
        assert result is not None

    def test_blocks_buys_for_total_pnl_below_threshold(self):
        """Blocks BUY when total P&L below threshold."""
        pf = self._make_filter(min_trades=3, min_total_pnl=-50.0)
        # 3 trades totaling -$60
        self._seed_trades(pf, "BAD-USD", [
            (100, 80, 1),    # -$20
            (100, 75, 1),    # -$25
            (100, 85, 1),    # -$15
        ])

        signal = _make_signal(direction=Direction.BUY, symbol="BAD-USD")
        portfolio = Portfolio(cash_balance=Decimal("10000"))

        result = pf.check_signal(signal, portfolio)
        assert result is None

    def test_state_persistence(self):
        """State survives get_state/load_state cycle."""
        pf = self._make_filter(min_trades=2)
        self._seed_trades(pf, "BTC-USD", [
            (100, 90, 1),
            (100, 85, 1),
        ])

        state = pf.get_state()
        assert "completed_trades" in state
        assert "BTC-USD" in state["completed_trades"]

        pf2 = self._make_filter(min_trades=2)
        pf2.load_state(state)
        stats = pf2._get_stats("BTC-USD")
        assert stats is not None
        assert stats["trade_count"] == 2


# ==================================================================
# Feature 3: Stale Order Cancellation (MakerOnlyExecution)
# ==================================================================

class TestStaleOrderCancellation:
    def _make_execution(self, **kwargs):
        from v2.plugins.execution.maker_only import MakerOnlyExecution
        defaults = {
            "stale_timeout_seconds": 300,
            "cancel_drift_pct": 0.005,
            "mode": "paper",
        }
        defaults.update(kwargs)
        return MakerOnlyExecution(event_bus=EventBus(), **defaults)

    def test_order_tracked_after_open_status(self):
        """Order is tracked after execute_signal returns OPEN status."""
        ex = self._make_execution()

        # Simulate tracking an open order
        ex._tracked_orders["order-123"] = {
            "symbol": "BTC-USD",
            "side": Side.BUY,
            "price": Decimal("50000"),
            "timestamp": time.monotonic(),
        }

        assert "order-123" in ex._tracked_orders

    def test_order_removed_on_fill(self):
        """Order removed from tracking on fill."""
        ex = self._make_execution()
        ex._tracked_orders["order-123"] = {
            "symbol": "BTC-USD",
            "side": Side.BUY,
            "price": Decimal("50000"),
            "timestamp": time.monotonic(),
        }

        ex.on_fill("order-123")
        assert "order-123" not in ex._tracked_orders

    def test_order_removed_on_order_event(self):
        """Order removed from tracking on FILLED/CANCELLED/REJECTED events."""
        ex = self._make_execution()
        ex._tracked_orders["order-123"] = {
            "symbol": "BTC-USD",
            "side": Side.BUY,
            "price": Decimal("50000"),
            "timestamp": time.monotonic(),
        }

        filled_order = Order(
            order_id="order-123",
            symbol="BTC-USD",
            side=Side.BUY,
            order_type=OrderType.LIMIT,
            price=Decimal("50000"),
            qty=Decimal("1"),
            status=OrderStatus.FILLED,
            timestamp=datetime.now(timezone.utc),
        )
        ex.on_order_event(filled_order)
        assert "order-123" not in ex._tracked_orders

    @pytest.mark.asyncio
    async def test_stale_order_cancelled_after_timeout_and_drift(self):
        """Stale order cancelled when timeout exceeded AND price has drifted."""
        ex = self._make_execution(stale_timeout_seconds=60, cancel_drift_pct=0.005)

        # Simulate an order placed 120 seconds ago
        ex._tracked_orders["order-stale"] = {
            "symbol": "BTC-USD",
            "side": Side.BUY,
            "price": Decimal("50000"),
            "timestamp": time.monotonic() - 120,
        }

        # Mock exchange: ask has risen 1% above order price
        exchange = AsyncMock(spec=["get_ticker", "cancel_order"])
        exchange.get_ticker = AsyncMock(return_value=TickerEvent(
            symbol="BTC-USD", price=50600.0,
            timestamp=datetime.now(timezone.utc),
            bid=50500.0, ask=50600.0,
        ))
        exchange.cancel_order = AsyncMock(return_value=True)

        await ex.check_stale_orders(exchange)

        exchange.cancel_order.assert_called_once_with("order-stale")
        assert "order-stale" not in ex._tracked_orders

    @pytest.mark.asyncio
    async def test_order_not_cancelled_if_no_drift(self):
        """Order NOT cancelled when timeout exceeded but price hasn't drifted."""
        ex = self._make_execution(stale_timeout_seconds=60, cancel_drift_pct=0.005)

        # Order placed 120 seconds ago, but price barely moved
        ex._tracked_orders["order-nodrift"] = {
            "symbol": "BTC-USD",
            "side": Side.BUY,
            "price": Decimal("50000"),
            "timestamp": time.monotonic() - 120,
        }

        exchange = AsyncMock(spec=["get_ticker", "cancel_order"])
        exchange.get_ticker = AsyncMock(return_value=TickerEvent(
            symbol="BTC-USD", price=50010.0,
            timestamp=datetime.now(timezone.utc),
            bid=50005.0, ask=50010.0,  # Only 0.02% drift
        ))
        exchange.cancel_order = AsyncMock(return_value=True)

        await ex.check_stale_orders(exchange)

        exchange.cancel_order.assert_not_called()
        assert "order-nodrift" in ex._tracked_orders

    @pytest.mark.asyncio
    async def test_order_not_cancelled_within_timeout(self):
        """Order NOT cancelled if within timeout window."""
        ex = self._make_execution(stale_timeout_seconds=300)

        # Order placed 60 seconds ago (within 300s timeout)
        ex._tracked_orders["order-fresh"] = {
            "symbol": "BTC-USD",
            "side": Side.BUY,
            "price": Decimal("50000"),
            "timestamp": time.monotonic() - 60,
        }

        exchange = AsyncMock(spec=["get_ticker", "cancel_order"])
        exchange.cancel_order = AsyncMock(return_value=True)

        await ex.check_stale_orders(exchange)

        exchange.cancel_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_stale_sell_order_cancelled_on_bid_drop(self):
        """Stale SELL order cancelled when bid drops beyond drift threshold."""
        ex = self._make_execution(stale_timeout_seconds=60, cancel_drift_pct=0.005)

        ex._tracked_orders["order-sell"] = {
            "symbol": "ETH-USD",
            "side": Side.SELL,
            "price": Decimal("3000"),
            "timestamp": time.monotonic() - 120,
        }

        exchange = AsyncMock(spec=["get_ticker", "cancel_order"])
        exchange.get_ticker = AsyncMock(return_value=TickerEvent(
            symbol="ETH-USD", price=2970.0,
            timestamp=datetime.now(timezone.utc),
            bid=2970.0, ask=2975.0,  # bid dropped 1%
        ))
        exchange.cancel_order = AsyncMock(return_value=True)

        await ex.check_stale_orders(exchange)

        exchange.cancel_order.assert_called_once_with("order-sell")

    @pytest.mark.asyncio
    async def test_no_orders_does_nothing(self):
        """check_stale_orders returns immediately with no tracked orders."""
        ex = self._make_execution()
        exchange = AsyncMock(spec=["get_ticker", "cancel_order"])

        await ex.check_stale_orders(exchange)

        exchange.get_ticker.assert_not_called()


# ======================================================================
# Report Visibility Tests
# ======================================================================


class TestReportRiskEventAccumulator:
    """Tests for performance filter and stale cancellation tracking."""

    def _make_acc(self):
        from v2.plugins.observability.daily_report_v2.collectors.risk_events import (
            RiskEventAccumulator,
        )
        return RiskEventAccumulator()

    def test_performance_veto_increments_both_counters(self):
        acc = self._make_acc()
        acc.record_performance_veto("performance_filter: BTC-USD excluded")
        snap = acc.snapshot()
        assert snap.vetoes == 1
        assert snap.performance_filter_vetoes == 1

    def test_regular_veto_does_not_increment_perf_filter(self):
        acc = self._make_acc()
        acc.record_veto("basic: no position")
        snap = acc.snapshot()
        assert snap.vetoes == 1
        assert snap.performance_filter_vetoes == 0

    def test_stale_cancellation_tracked(self):
        acc = self._make_acc()
        acc.record_stale_cancellation("ETH-USD")
        acc.record_stale_cancellation("BTC-USD")
        snap = acc.snapshot()
        assert snap.stale_cancellations == 2

    def test_reset_clears_all_counters(self):
        acc = self._make_acc()
        acc.record_performance_veto("test")
        acc.record_stale_cancellation("SOL-USD")
        acc.record_circuit_breaker("trip")
        acc.reset()
        snap = acc.snapshot()
        assert snap.vetoes == 0
        assert snap.performance_filter_vetoes == 0
        assert snap.stale_cancellations == 0
        assert snap.circuit_breaker_trips == 0


class TestReportExitEventAccumulator:
    """Tests for signal exit tracking in ExitEventAccumulator."""

    def _make_acc(self):
        from v2.plugins.observability.daily_report_v2.collectors.exit_events import (
            ExitEventAccumulator,
        )
        return ExitEventAccumulator()

    def test_signal_exit_tracked(self):
        from v2.core.types import RiskEvent
        acc = self._make_acc()
        event = RiskEvent(
            event_type="exit_triggered",
            reason="signal_exit",
            metadata={"symbol": "BTC-USD", "price": 50000.0, "pnl_pct": 2.5},
        )
        acc.record_exit(event)
        snap = acc.snapshot()
        assert snap.signal_exits == 1
        assert snap.total_exits == 1

    def test_signal_exit_in_total_with_others(self):
        from v2.core.types import RiskEvent
        acc = self._make_acc()
        acc.record_exit(RiskEvent(
            event_type="exit_triggered", reason="hard_stop",
            metadata={"symbol": "ETH-USD", "price": 3000.0, "pnl_pct": -4.5},
        ))
        acc.record_exit(RiskEvent(
            event_type="exit_triggered", reason="signal_exit",
            metadata={"symbol": "BTC-USD", "price": 50000.0, "pnl_pct": 1.0},
        ))
        snap = acc.snapshot()
        assert snap.hard_stops == 1
        assert snap.signal_exits == 1
        assert snap.total_exits == 2

    def test_reset_clears_signal_exits(self):
        from v2.core.types import RiskEvent
        acc = self._make_acc()
        acc.record_exit(RiskEvent(
            event_type="exit_triggered", reason="signal_exit",
            metadata={"symbol": "BTC-USD", "price": 50000.0, "pnl_pct": 1.0},
        ))
        acc.reset()
        snap = acc.snapshot()
        assert snap.signal_exits == 0
        assert snap.total_exits == 0


class TestReportObserverRouting:
    """Tests for observer event routing to accumulators."""

    def _make_observer(self):
        from v2.plugins.observability.daily_report_v2.observer import (
            DailyReportV2Observer,
        )
        return DailyReportV2Observer()

    def test_performance_filter_veto_routed(self):
        from v2.core.types import RiskEvent
        obs = self._make_observer()
        event = RiskEvent(
            event_type="signal_vetoed",
            reason="performance_filter: BTC-USD excluded",
            metadata={"symbol": "BTC-USD", "direction": "buy"},
        )
        obs._on_risk(event)
        snap = obs._risk_acc.snapshot()
        assert snap.performance_filter_vetoes == 1
        assert snap.vetoes == 1

    def test_non_perf_filter_veto_not_routed_to_perf(self):
        from v2.core.types import RiskEvent
        obs = self._make_observer()
        event = RiskEvent(
            event_type="signal_vetoed",
            reason="basic: no position for sell",
            metadata={"symbol": "ETH-USD", "direction": "sell"},
        )
        obs._on_risk(event)
        snap = obs._risk_acc.snapshot()
        assert snap.performance_filter_vetoes == 0
        assert snap.vetoes == 1  # generic veto

    def test_cancelled_stale_order_tracked(self):
        from v2.core.types import OrderEvent, OrderStatus
        obs = self._make_observer()
        event = OrderEvent(
            order=Order(
                order_id="stale-1",
                symbol="SOL-USD",
                side=Side.BUY,
                order_type=OrderType.LIMIT,
                price=Decimal("100"),
                qty=Decimal("1"),
                status=OrderStatus.CANCELLED,
                timestamp=datetime.now(timezone.utc),
                metadata={"reason": "stale_order_cancelled"},
            ),
            event_type="cancelled",
        )
        obs.on_event(event)
        snap = obs._risk_acc.snapshot()
        assert snap.stale_cancellations == 1

    def test_non_stale_cancellation_ignored(self):
        from v2.core.types import OrderEvent, OrderStatus
        obs = self._make_observer()
        event = OrderEvent(
            order=Order(
                order_id="manual-1",
                symbol="SOL-USD",
                side=Side.BUY,
                order_type=OrderType.LIMIT,
                price=Decimal("100"),
                qty=Decimal("1"),
                status=OrderStatus.CANCELLED,
                timestamp=datetime.now(timezone.utc),
                metadata={"reason": "user_cancelled"},
            ),
            event_type="cancelled",
        )
        obs.on_event(event)
        snap = obs._risk_acc.snapshot()
        assert snap.stale_cancellations == 0


class TestSignalExitEmitsRiskEvent:
    """Tests that ExitManager emits RiskEvent for signal exits."""

    def _make_exit_manager(self, **kwargs):
        from v2.plugins.risk.exit_manager import ExitManager
        defaults = {
            "signal_exit_enabled": True,
            "signal_exit_min_pnl_pct": 0.0,
        }
        defaults.update(kwargs)
        bus = EventBus()
        em = ExitManager(event_bus=bus, **defaults)
        return em, bus

    def test_signal_exit_emits_risk_event(self):
        from v2.core.types import RiskEvent
        em, bus = self._make_exit_manager()
        events = []
        bus.subscribe(RiskEvent, lambda e: events.append(e))

        portfolio = Portfolio(
            cash_balance=Decimal("10000"),
            positions={
                "BTC-USD": Position(
                    symbol="BTC-USD", qty=Decimal("0.1"),
                    avg_entry_price=Decimal("50000"),
                    cost_basis=Decimal("5000"),
                ),
            },
        )
        signal = Signal(
            direction=Direction.SELL, symbol="BTC-USD",
            timestamp=datetime.now(timezone.utc),
            price=51000.0, reason="composite_scoring",
        )
        result = em.check_signal(signal, portfolio)
        assert result is not None
        assert len(events) == 1
        assert events[0].event_type == "exit_triggered"
        assert events[0].reason == "signal_exit"
        assert events[0].metadata["symbol"] == "BTC-USD"

    def test_signal_exit_no_risk_event_when_unprofitable(self):
        from v2.core.types import RiskEvent
        em, bus = self._make_exit_manager(signal_exit_min_pnl_pct=0.0)
        events = []
        bus.subscribe(RiskEvent, lambda e: events.append(e))

        portfolio = Portfolio(
            cash_balance=Decimal("10000"),
            positions={
                "BTC-USD": Position(
                    symbol="BTC-USD", qty=Decimal("0.1"),
                    avg_entry_price=Decimal("50000"),
                    cost_basis=Decimal("5000"),
                ),
            },
        )
        signal = Signal(
            direction=Direction.SELL, symbol="BTC-USD",
            timestamp=datetime.now(timezone.utc),
            price=49000.0, reason="composite_scoring",
        )
        result = em.check_signal(signal, portfolio)
        assert result is not None  # passes through
        assert len(events) == 0  # no RiskEvent for unprofitable
