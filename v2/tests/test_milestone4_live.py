"""Tests for Milestone 4 — Live Exchange plugins.

Tests cover:
- CoinbaseExchange: registration, JWT auth, order parsing, REST helpers
- PaperExchange: fills, balances, limit orders, post-only rejection
- WebSocketDataProvider: registration, ticker parsing
- MakerOnlyExecution: post-only price calculation, retry logic, qty determination
- BracketExecution: TP/SL calculation, order construction
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

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
    Side,
    Signal,
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
    reason="test",
    **meta,
) -> Signal:
    return Signal(
        direction=direction,
        symbol=symbol,
        timestamp=datetime.now(timezone.utc),
        price=price,
        qty=qty,
        reason=reason,
        metadata=meta,
    )


# =====================================================================
# Plugin Registration
# =====================================================================


class TestPluginRegistration:
    def test_coinbase_registered(self):
        plugins = registry.list_plugins("exchange")
        assert "coinbase" in plugins["exchange"]

    def test_paper_registered(self):
        plugins = registry.list_plugins("exchange")
        assert "paper" in plugins["exchange"]

    def test_websocket_registered(self):
        plugins = registry.list_plugins("data")
        assert "websocket" in plugins["data"]

    def test_maker_only_registered(self):
        plugins = registry.list_plugins("execution")
        assert "maker_only" in plugins["execution"]

    def test_bracket_registered(self):
        plugins = registry.list_plugins("execution")
        assert "bracket" in plugins["execution"]

    def test_all_milestone4_plugins(self):
        """All 5 new/updated plugins discoverable."""
        plugins = registry.list_plugins()
        assert "coinbase" in plugins["exchange"]
        assert "paper" in plugins["exchange"]
        assert "websocket" in plugins["data"]
        assert "maker_only" in plugins["execution"]
        assert "bracket" in plugins["execution"]


# =====================================================================
# CoinbaseExchange
# =====================================================================


class TestCoinbaseExchange:
    def test_instantiate(self, bus):
        from v2.plugins.exchanges.coinbase import CoinbaseExchange
        ex = CoinbaseExchange(
            event_bus=bus,
            api_key="test-key",
            api_secret="test-secret",
        )
        assert ex.name == "coinbase"

    def test_parse_order(self):
        from v2.plugins.exchanges.coinbase import CoinbaseExchange
        data = {
            "order_id": "abc-123",
            "product_id": "BTC-USD",
            "side": "BUY",
            "status": "FILLED",
            "order_configuration": {
                "limit_limit_gtc": {
                    "base_size": "0.001",
                    "limit_price": "97000.00",
                    "post_only": True,
                }
            },
            "client_order_id": "client-xyz",
        }
        order = CoinbaseExchange._parse_order(data)
        assert order.order_id == "abc-123"
        assert order.symbol == "BTC-USD"
        assert order.side == Side.BUY
        assert order.status == OrderStatus.FILLED
        assert order.price == Decimal("97000.00")
        assert order.qty == Decimal("0.001")

    def test_parse_order_sell(self):
        from v2.plugins.exchanges.coinbase import CoinbaseExchange
        data = {
            "order_id": "def-456",
            "product_id": "ETH-USD",
            "side": "SELL",
            "status": "OPEN",
            "order_configuration": {
                "limit_limit_gtc": {
                    "base_size": "0.5",
                    "limit_price": "3200.00",
                }
            },
        }
        order = CoinbaseExchange._parse_order(data)
        assert order.side == Side.SELL
        assert order.status == OrderStatus.OPEN

    def test_status_mapping(self):
        from v2.plugins.exchanges.coinbase import _STATUS_MAP
        assert _STATUS_MAP["FILLED"] == OrderStatus.FILLED
        assert _STATUS_MAP["CANCELLED"] == OrderStatus.CANCELLED
        assert _STATUS_MAP["CANCELED"] == OrderStatus.CANCELLED
        assert _STATUS_MAP["FAILED"] == OrderStatus.REJECTED

    def test_get_headers_generates_jwt(self):
        """Headers generation should set JWT token."""
        from v2.plugins.exchanges.coinbase import CoinbaseExchange
        ex = CoinbaseExchange(api_key="test", api_secret="test")
        # Mock jwt_generator
        with patch("v2.plugins.exchanges.coinbase.CoinbaseExchange._generate_jwt") as mock_jwt:
            mock_jwt.return_value = "mock-jwt-token"
            ex._jwt_token = "mock-jwt-token"
            ex._jwt_expiry = datetime(2099, 1, 1, tzinfo=timezone.utc)
            headers = ex._get_headers("GET", "/api/v3/brokerage/orders")
            assert "Authorization" in headers
            assert headers["Authorization"] == "Bearer mock-jwt-token"


# =====================================================================
# PaperExchange
# =====================================================================


class TestPaperExchange:
    @pytest.fixture
    def paper(self, bus):
        from v2.plugins.exchanges.paper import PaperExchange
        return PaperExchange(
            event_bus=bus,
            initial_balance_usd=10000.0,
            maker_fee=0.006,
            taker_fee=0.012,
            slippage_bps=0.0,  # No slippage for test determinism
        )

    @pytest.mark.asyncio
    async def test_connect_disconnect(self, paper):
        await paper.connect()
        await paper.disconnect()

    @pytest.mark.asyncio
    async def test_initial_balances(self, paper):
        await paper.connect()
        balances = await paper.get_balances()
        assert balances["USD"] == Decimal("10000")
        await paper.disconnect()

    @pytest.mark.asyncio
    async def test_fee_rates(self, paper):
        fees = await paper.get_fee_rates()
        assert fees["maker"] == Decimal("0.006")
        assert fees["taker"] == Decimal("0.012")

    @pytest.mark.asyncio
    async def test_market_order_buy_fill(self, paper, bus, now):
        """Market BUY fills immediately at ask price."""
        await paper.connect()

        # Set price via ticker event
        paper._on_ticker(TickerEvent(
            symbol="BTC-USD", price=97000.0, timestamp=now,
            bid=96999.0, ask=97001.0,
        ))

        order = Order(
            order_id="test-1",
            symbol="BTC-USD",
            side=Side.BUY,
            order_type=OrderType.MARKET,
            price=Decimal("97000"),
            qty=Decimal("0.1"),
            status=OrderStatus.PENDING,
            timestamp=now,
        )

        result = await paper.submit_order(order)
        assert result.status == OrderStatus.FILLED
        assert result.price == Decimal("97001")  # Filled at ask (no slippage)

        balances = await paper.get_balances()
        # Cost = 97001 * 0.1 + fee (0.012 * 97001 * 0.1)
        expected_cost = Decimal("97001") * Decimal("0.1") * (Decimal("1") + Decimal("0.012"))
        assert balances["USD"] == Decimal("10000") - expected_cost
        assert balances["BTC"] == Decimal("0.1")

        await paper.disconnect()

    @pytest.mark.asyncio
    async def test_market_order_no_price_rejected(self, paper, now):
        """Market order without price data is rejected."""
        await paper.connect()

        order = Order(
            order_id="test-2",
            symbol="BTC-USD",
            side=Side.BUY,
            order_type=OrderType.MARKET,
            price=Decimal("97000"),
            qty=Decimal("0.1"),
            status=OrderStatus.PENDING,
            timestamp=now,
        )

        result = await paper.submit_order(order)
        assert result.status == OrderStatus.REJECTED

        await paper.disconnect()

    @pytest.mark.asyncio
    async def test_limit_order_rests_when_not_fillable(self, paper, now):
        """Limit BUY below ask rests as open order."""
        await paper.connect()

        paper._on_ticker(TickerEvent(
            symbol="BTC-USD", price=97000.0, timestamp=now,
            bid=96999.0, ask=97001.0,
        ))

        # Buy limit at 96500 — well below ask, should rest
        order = Order(
            order_id="test-3",
            symbol="BTC-USD",
            side=Side.BUY,
            order_type=OrderType.LIMIT,
            price=Decimal("96500"),
            qty=Decimal("0.1"),
            status=OrderStatus.PENDING,
            timestamp=now,
        )

        result = await paper.submit_order(order)
        assert result.status == OrderStatus.OPEN

        open_orders = await paper.get_open_orders("BTC-USD")
        assert len(open_orders) == 1

        await paper.disconnect()

    @pytest.mark.asyncio
    async def test_limit_order_fills_on_ticker(self, paper, bus, now):
        """Resting limit order fills when price crosses."""
        await paper.connect()

        fills_received = []
        bus.subscribe(FillEvent, lambda e: fills_received.append(e))

        # Set initial price above our limit
        paper._on_ticker(TickerEvent(
            symbol="BTC-USD", price=97000.0, timestamp=now,
            bid=96999.0, ask=97001.0,
        ))

        # Place limit buy at 96500
        order = Order(
            order_id="test-4",
            symbol="BTC-USD",
            side=Side.BUY,
            order_type=OrderType.LIMIT,
            price=Decimal("96500"),
            qty=Decimal("0.1"),
            status=OrderStatus.PENDING,
            timestamp=now,
        )

        result = await paper.submit_order(order)
        assert result.status == OrderStatus.OPEN
        assert len(fills_received) == 0

        # Price drops — ask crosses our limit
        paper._on_ticker(TickerEvent(
            symbol="BTC-USD", price=96400.0, timestamp=now,
            bid=96399.0, ask=96401.0,
        ))

        assert len(fills_received) == 1
        assert fills_received[0].fill.symbol == "BTC-USD"
        assert fills_received[0].fill.side == Side.BUY

        # No more open orders
        open_orders = await paper.get_open_orders("BTC-USD")
        assert len(open_orders) == 0

        await paper.disconnect()

    @pytest.mark.asyncio
    async def test_post_only_rejection(self, paper, now):
        """Post-only limit that crosses spread is rejected."""
        await paper.connect()

        paper._on_ticker(TickerEvent(
            symbol="BTC-USD", price=97000.0, timestamp=now,
            bid=96999.0, ask=97001.0,
        ))

        # Buy at 97100 (above ask) with post_only — should reject
        order = Order(
            order_id="test-5",
            symbol="BTC-USD",
            side=Side.BUY,
            order_type=OrderType.LIMIT,
            price=Decimal("97100"),
            qty=Decimal("0.1"),
            status=OrderStatus.PENDING,
            timestamp=now,
            metadata={"post_only": True},
        )

        result = await paper.submit_order(order)
        assert result.status == OrderStatus.REJECTED
        assert "post_only" in result.metadata.get("reject_reason", "")

        await paper.disconnect()

    @pytest.mark.asyncio
    async def test_cancel_order(self, paper, now):
        await paper.connect()

        paper._on_ticker(TickerEvent(
            symbol="BTC-USD", price=97000.0, timestamp=now,
            bid=96999.0, ask=97001.0,
        ))

        order = Order(
            order_id="test-6",
            symbol="BTC-USD",
            side=Side.BUY,
            order_type=OrderType.LIMIT,
            price=Decimal("96000"),
            qty=Decimal("0.1"),
            status=OrderStatus.PENDING,
            timestamp=now,
        )

        await paper.submit_order(order)
        assert await paper.cancel_order("test-6")
        assert not await paper.cancel_order("nonexistent")

        await paper.disconnect()

    @pytest.mark.asyncio
    async def test_sell_order(self, paper, now):
        """SELL market order works correctly."""
        await paper.connect()

        # Give the paper exchange some BTC first
        paper._balances["BTC"] = Decimal("1.0")

        paper._on_ticker(TickerEvent(
            symbol="BTC-USD", price=97000.0, timestamp=now,
            bid=96999.0, ask=97001.0,
        ))

        order = Order(
            order_id="test-sell-1",
            symbol="BTC-USD",
            side=Side.SELL,
            order_type=OrderType.MARKET,
            price=Decimal("97000"),
            qty=Decimal("0.1"),
            status=OrderStatus.PENDING,
            timestamp=now,
        )

        result = await paper.submit_order(order)
        assert result.status == OrderStatus.FILLED

        balances = await paper.get_balances()
        assert balances["BTC"] == Decimal("0.9")
        # Proceeds = bid * qty - fee
        expected_proceeds = Decimal("96999") * Decimal("0.1") - Decimal("96999") * Decimal("0.1") * Decimal("0.012")
        assert balances["USD"] == Decimal("10000") + expected_proceeds

        await paper.disconnect()


# =====================================================================
# WebSocketDataProvider
# =====================================================================


class TestWebSocketDataProvider:
    def test_instantiate(self, bus):
        from v2.plugins.data.websocket import WebSocketDataProvider
        ws = WebSocketDataProvider(
            event_bus=bus,
            api_key="test",
            api_secret="test",
        )
        assert ws.name == "websocket"

    def test_process_ticker_batch(self, bus):
        """Ticker batch message should publish TickerEvent."""
        from v2.plugins.data.websocket import WebSocketDataProvider
        ws = WebSocketDataProvider(event_bus=bus)

        received = []
        bus.subscribe(TickerEvent, lambda e: received.append(e))

        # Simulate a ticker_batch message
        msg = json.dumps({
            "channel": "ticker_batch",
            "events": [{
                "tickers": [
                    {
                        "product_id": "BTC-USD",
                        "price": "97500.00",
                        "price_percent_chg_24_h": "2.5",
                        "best_bid": "97499.00",
                        "best_ask": "97501.00",
                    },
                    {
                        "product_id": "ETH-USD",
                        "price": "3200.00",
                    },
                ],
            }],
        })

        ws._process_message(msg)

        assert len(received) == 2
        btc = received[0]
        assert btc.symbol == "BTC-USD"
        assert btc.price == 97500.0
        assert btc.change_24h_pct == 2.5
        assert btc.bid == 97499.0
        assert btc.ask == 97501.0

        eth = received[1]
        assert eth.symbol == "ETH-USD"
        assert eth.price == 3200.0
        assert eth.change_24h_pct is None

    def test_process_heartbeat(self, bus):
        """Heartbeat message should not publish events."""
        from v2.plugins.data.websocket import WebSocketDataProvider
        ws = WebSocketDataProvider(event_bus=bus)

        received = []
        bus.subscribe(TickerEvent, lambda e: received.append(e))

        msg = json.dumps({"channel": "heartbeats", "events": [{}]})
        ws._process_message(msg)

        assert len(received) == 0

    def test_process_invalid_json(self, bus):
        """Invalid JSON should not crash."""
        from v2.plugins.data.websocket import WebSocketDataProvider
        ws = WebSocketDataProvider(event_bus=bus)
        ws._process_message("not valid json")  # Should not raise


# =====================================================================
# MakerOnlyExecution
# =====================================================================


class TestMakerOnlyExecution:
    @pytest.fixture
    def maker(self, bus):
        from v2.plugins.execution.maker_only import MakerOnlyExecution
        return MakerOnlyExecution(event_bus=bus, mode="live")

    def test_post_only_price_buy(self):
        from v2.plugins.execution.maker_only import MakerOnlyExecution
        price = MakerOnlyExecution._calculate_post_only_price(
            Side.BUY,
            Decimal("96999"),
            Decimal("97001"),
            Decimal("0.001"),
        )
        # Should be below ask: min(97001 * 0.999, 97001 - 0.0000001)
        expected = Decimal("97001") * Decimal("0.999")
        assert price == expected
        assert price < Decimal("97001")

    def test_post_only_price_sell(self):
        from v2.plugins.execution.maker_only import MakerOnlyExecution
        price = MakerOnlyExecution._calculate_post_only_price(
            Side.SELL,
            Decimal("96999"),
            Decimal("97001"),
            Decimal("0.001"),
        )
        # Should be above bid: max(96999 * 1.001, 96999 + 0.0000001)
        expected = Decimal("96999") * Decimal("1.001")
        assert price == expected
        assert price > Decimal("96999")

    def test_is_post_only_rejection(self):
        from v2.plugins.execution.maker_only import MakerOnlyExecution
        assert MakerOnlyExecution._is_post_only_rejection(
            "post-only order would cross", {}
        )
        assert MakerOnlyExecution._is_post_only_rejection(
            "INVALID_LIMIT_PRICE", {}
        )
        assert not MakerOnlyExecution._is_post_only_rejection(
            "insufficient_funds", {}
        )

    def test_determine_qty_from_signal(self, maker):
        sig = _make_signal(qty=0.005)
        qty = maker._determine_qty(sig)
        assert qty == Decimal("0.005")

    def test_determine_qty_from_notional(self, maker):
        sig = _make_signal(qty=None)
        sig = Signal(
            direction=Direction.BUY,
            symbol="BTC-USD",
            timestamp=datetime.now(timezone.utc),
            price=97000.0,
            notional=970.0,
            reason="test",
        )
        qty = maker._determine_qty(sig)
        assert qty == Decimal("970") / Decimal("97000")

    def test_determine_qty_none(self, maker):
        sig = Signal(
            direction=Direction.BUY,
            symbol="BTC-USD",
            timestamp=datetime.now(timezone.utc),
            reason="test",
        )
        qty = maker._determine_qty(sig)
        assert qty is None

    @pytest.mark.asyncio
    async def test_backtest_mode_returns_none(self, bus):
        from v2.plugins.execution.maker_only import MakerOnlyExecution
        maker = MakerOnlyExecution(event_bus=bus, mode="backtest")
        sig = _make_signal()
        mock_exchange = AsyncMock()
        result = await maker.execute_signal(sig, mock_exchange)
        assert result is None

    @pytest.mark.asyncio
    async def test_execute_signal_success(self, maker):
        """Successful order submission."""
        mock_exchange = AsyncMock()
        mock_exchange.get_best_bid_ask = AsyncMock(return_value={
            "bid": Decimal("96999"),
            "ask": Decimal("97001"),
        })
        mock_exchange.submit_order = AsyncMock(return_value=Order(
            order_id="test-order",
            symbol="BTC-USD",
            side=Side.BUY,
            order_type=OrderType.LIMIT,
            price=Decimal("96904"),
            qty=Decimal("0.001"),
            status=OrderStatus.OPEN,
            timestamp=datetime.now(timezone.utc),
        ))

        sig = _make_signal(qty=0.001)
        result = await maker.execute_signal(sig, mock_exchange)
        assert result is not None
        assert result.status == OrderStatus.OPEN

    @pytest.mark.asyncio
    async def test_execute_signal_retry_on_post_only_rejection(self, maker):
        """Retries on post-only rejection with increased buffer."""
        mock_exchange = AsyncMock()
        mock_exchange.get_best_bid_ask = AsyncMock(return_value={
            "bid": Decimal("96999"),
            "ask": Decimal("97001"),
        })

        # First call: rejected, second call: success
        mock_exchange.submit_order = AsyncMock(side_effect=[
            Order(
                order_id="rej-1", symbol="BTC-USD", side=Side.BUY,
                order_type=OrderType.LIMIT, price=Decimal("96904"),
                qty=Decimal("0.001"), status=OrderStatus.REJECTED,
                timestamp=datetime.now(timezone.utc),
                metadata={"reject_reason": "post-only would cross"},
            ),
            Order(
                order_id="ok-1", symbol="BTC-USD", side=Side.BUY,
                order_type=OrderType.LIMIT, price=Decimal("96850"),
                qty=Decimal("0.001"), status=OrderStatus.OPEN,
                timestamp=datetime.now(timezone.utc),
            ),
        ])

        sig = _make_signal(qty=0.001)
        result = await maker.execute_signal(sig, mock_exchange)
        assert result is not None
        assert result.status == OrderStatus.OPEN
        assert mock_exchange.submit_order.call_count == 2

    @pytest.mark.asyncio
    async def test_no_bid_ask_returns_none(self, maker):
        """Returns None if no bid/ask available."""
        mock_exchange = AsyncMock()
        mock_exchange.get_best_bid_ask = AsyncMock(return_value={
            "bid": Decimal("0"), "ask": Decimal("0"),
        })
        mock_exchange.get_ticker = AsyncMock(return_value=TickerEvent(
            symbol="BTC-USD", price=0.0, timestamp=datetime.now(timezone.utc),
        ))

        sig = _make_signal(qty=0.001)
        result = await maker.execute_signal(sig, mock_exchange)
        assert result is None


# =====================================================================
# BracketExecution
# =====================================================================


class TestBracketExecution:
    @pytest.fixture
    def bracket(self, bus):
        from v2.plugins.execution.bracket import BracketExecution
        return BracketExecution(
            event_bus=bus, mode="live", tp_pct="0.015", sl_pct="0.03",
        )

    def test_tp_sl_buy_from_pct(self, bracket):
        tp, sl = bracket._calculate_tp_sl(
            Side.BUY, Decimal("97000"), {},
        )
        # TP = 97000 * 1.015 = 98455
        assert tp == Decimal("97000") * Decimal("1.015")
        # SL = 97000 * 0.97 = 94090
        assert sl == Decimal("97000") * Decimal("0.97")

    def test_tp_sl_sell_from_pct(self, bracket):
        tp, sl = bracket._calculate_tp_sl(
            Side.SELL, Decimal("97000"), {},
        )
        # TP = 97000 * 0.985 = 95545
        assert tp == Decimal("97000") * Decimal("0.985")
        # SL = 97000 * 1.03 = 99910
        assert sl == Decimal("97000") * Decimal("1.03")

    def test_tp_sl_from_absolute_prices(self, bracket):
        tp, sl = bracket._calculate_tp_sl(
            Side.BUY,
            Decimal("97000"),
            {"tp_price": "98500", "sl_price": "95000"},
        )
        assert tp == Decimal("98500")
        assert sl == Decimal("95000")

    def test_tp_sl_custom_pct_in_metadata(self, bracket):
        tp, sl = bracket._calculate_tp_sl(
            Side.BUY,
            Decimal("100000"),
            {"tp_pct": "0.02", "sl_pct": "0.01"},
        )
        assert tp == Decimal("100000") * Decimal("1.02")
        assert sl == Decimal("100000") * Decimal("0.99")

    @pytest.mark.asyncio
    async def test_backtest_mode_returns_none(self, bus):
        from v2.plugins.execution.bracket import BracketExecution
        bracket = BracketExecution(event_bus=bus, mode="backtest")
        sig = _make_signal()
        result = await bracket.execute_signal(sig, AsyncMock())
        assert result is None

    @pytest.mark.asyncio
    async def test_execute_with_bracket(self, bracket):
        mock_exchange = AsyncMock()
        mock_exchange.submit_order = AsyncMock(return_value=Order(
            order_id="bracket-1",
            symbol="BTC-USD",
            side=Side.BUY,
            order_type=OrderType.LIMIT,
            price=Decimal("97000"),
            qty=Decimal("0.001"),
            status=OrderStatus.OPEN,
            timestamp=datetime.now(timezone.utc),
            metadata={"tp_price": Decimal("98455"), "sl_price": Decimal("94090")},
        ))

        sig = _make_signal(price=97000.0, qty=0.001)
        result = await bracket.execute_signal(sig, mock_exchange)
        assert result is not None
        assert result.status == OrderStatus.OPEN

        # Verify the order was submitted with TP/SL in metadata
        submitted_order = mock_exchange.submit_order.call_args[0][0]
        assert "tp_price" in submitted_order.metadata
        assert "sl_price" in submitted_order.metadata

    def test_configure(self, bracket):
        bracket.configure({"tp_pct": "0.025", "sl_pct": "0.05", "post_only": False})
        assert bracket._default_tp_pct == Decimal("0.025")
        assert bracket._default_sl_pct == Decimal("0.05")
        assert bracket._post_only is False


# =====================================================================
# Integration: Paper Exchange + Execution
# =====================================================================


class TestPaperWithExecution:
    @pytest.mark.asyncio
    async def test_maker_only_with_paper(self, bus):
        """MakerOnly execution places real order on paper exchange."""
        from v2.plugins.exchanges.paper import PaperExchange
        from v2.plugins.execution.maker_only import MakerOnlyExecution

        paper = PaperExchange(
            event_bus=bus,
            initial_balance_usd=10000.0,
            slippage_bps=0.0,
        )
        await paper.connect()

        # Set prices
        paper._on_ticker(TickerEvent(
            symbol="BTC-USD", price=97000.0,
            timestamp=datetime.now(timezone.utc),
            bid=96999.0, ask=97001.0,
        ))

        maker = MakerOnlyExecution(event_bus=bus, mode="live")
        sig = _make_signal(direction=Direction.BUY, qty=0.001)
        result = await maker.execute_signal(sig, paper)

        # Should have placed a limit order that rests (post-only below ask)
        assert result is not None
        assert result.status == OrderStatus.OPEN

        await paper.disconnect()
