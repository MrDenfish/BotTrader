"""Tests for v2.plugins.exchanges.kraken."""

import json
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from v2.core.types import Order, OrderStatus, OrderType, Side
from v2.plugins.exchanges.kraken import KrakenExchange
from v2.utils.symbol_mapper import KrakenSymbolMapper


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _make_exchange(**kwargs) -> KrakenExchange:
    """Create a KrakenExchange with test credentials (no real API calls)."""
    return KrakenExchange(
        event_bus=None,
        api_key="test_key",
        api_secret="dGVzdF9zZWNyZXQ=",  # base64("test_secret")
        **kwargs,
    )


def _make_order(**kwargs) -> Order:
    defaults = {
        "order_id": "test-123",
        "symbol": "BTC-USD",
        "side": Side.BUY,
        "order_type": OrderType.LIMIT,
        "price": Decimal("50000"),
        "qty": Decimal("0.1"),
        "status": OrderStatus.PENDING,
        "timestamp": datetime.now(timezone.utc),
        "metadata": {},
    }
    defaults.update(kwargs)
    return Order(**defaults)


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------

class TestKrakenExchangeInit:
    def test_default_urls(self):
        ex = _make_exchange()
        assert "kraken.com" in ex._rest_url
        assert "ws-auth.kraken.com" in ex._ws_auth_url

    def test_credentials_loaded(self):
        ex = _make_exchange()
        assert ex._api_key == "test_key"
        assert ex._api_secret == "dGVzdF9zZWNyZXQ="

    def test_custom_mapper(self):
        mapper = KrakenSymbolMapper()
        ex = _make_exchange(mapper=mapper)
        assert ex.mapper is mapper

    def test_name(self):
        ex = _make_exchange()
        assert ex.name == "kraken"


class TestSubmitOrder:
    @pytest.mark.asyncio
    async def test_submit_limit_buy(self):
        ex = _make_exchange()
        order = _make_order()

        # Mock the private_request to return success
        ex._private_request = AsyncMock(return_value={
            "descr": {"order": "buy 0.1 XBTUSD @ limit 50000"},
            "txid": ["OABC-DEFG-HIJK"],
        })

        result = await ex.submit_order(order)

        # Verify request was made correctly
        ex._private_request.assert_called_once()
        call_args = ex._private_request.call_args
        assert call_args[0][0] == "/0/private/AddOrder"
        data = call_args[0][1]
        assert data["type"] == "buy"
        assert data["ordertype"] == "limit"
        assert data["price"] == "50000"
        assert data["volume"] == "0.1"
        assert data["oflags"] == "post"  # post_only default

        # Verify result
        assert result.order_id == "OABC-DEFG-HIJK"
        assert result.status == OrderStatus.OPEN

    @pytest.mark.asyncio
    async def test_submit_market_sell(self):
        ex = _make_exchange()
        order = _make_order(
            side=Side.SELL,
            order_type=OrderType.MARKET,
            metadata={},
        )

        ex._private_request = AsyncMock(return_value={
            "descr": {"order": "sell 0.1 XBTUSD @ market"},
            "txid": ["OSELL-123"],
        })

        result = await ex.submit_order(order)

        call_data = ex._private_request.call_args[0][1]
        assert call_data["type"] == "sell"
        assert call_data["ordertype"] == "market"
        assert "price" not in call_data

    @pytest.mark.asyncio
    async def test_submit_order_rejected(self):
        ex = _make_exchange()
        order = _make_order()

        ex._private_request = AsyncMock(return_value={
            "error": ["EOrder:Insufficient funds"],
        })

        result = await ex.submit_order(order)
        assert result.status == OrderStatus.REJECTED
        assert "Insufficient funds" in result.metadata.get("reject_reason", "")


class TestCancelOrder:
    @pytest.mark.asyncio
    async def test_cancel_success(self):
        ex = _make_exchange()
        ex._private_request = AsyncMock(return_value={"count": 1})

        result = await ex.cancel_order("OABC-123")
        assert result is True

    @pytest.mark.asyncio
    async def test_cancel_failure(self):
        ex = _make_exchange()
        ex._private_request = AsyncMock(return_value={
            "error": ["EOrder:Unknown order"],
        })

        result = await ex.cancel_order("FAKE-123")
        assert result is False


class TestGetOrder:
    @pytest.mark.asyncio
    async def test_get_from_cache(self):
        ex = _make_exchange()
        cached_order = _make_order(order_id="CACHED-1", status=OrderStatus.OPEN)
        ex._orders["CACHED-1"] = cached_order

        result = await ex.get_order("CACHED-1")
        assert result.order_id == "CACHED-1"

    @pytest.mark.asyncio
    async def test_get_from_rest(self):
        ex = _make_exchange()
        ex._private_request = AsyncMock(return_value={
            "OREST-1": {
                "status": "open",
                "vol": "0.5",
                "vol_exec": "0.0",
                "descr": {
                    "pair": "XBTUSD",
                    "type": "buy",
                    "ordertype": "limit",
                    "price": "45000",
                },
            },
        })

        result = await ex.get_order("OREST-1")
        assert result is not None
        assert result.side == Side.BUY
        assert result.status == OrderStatus.OPEN

    @pytest.mark.asyncio
    async def test_get_not_found(self):
        ex = _make_exchange()
        ex._private_request = AsyncMock(return_value={})

        result = await ex.get_order("NOPE-1")
        assert result is None


class TestGetBalances:
    @pytest.mark.asyncio
    async def test_balances_normalized(self):
        ex = _make_exchange()
        ex._private_request = AsyncMock(return_value={
            "XXBT": "1.5",
            "ZUSD": "10000.00",
            "XETH": "5.0",
            "SOL": "100",
        })

        balances = await ex.get_balances()
        assert balances["BTC"] == Decimal("1.5")
        assert balances["USD"] == Decimal("10000.00")
        assert balances["ETH"] == Decimal("5.0")
        assert balances["SOL"] == Decimal("100")

    @pytest.mark.asyncio
    async def test_balances_on_error(self):
        ex = _make_exchange()
        ex._private_request = AsyncMock(return_value={"error": ["EAPI:Invalid key"]})

        balances = await ex.get_balances()
        assert balances == {}


class TestGetFeeRates:
    @pytest.mark.asyncio
    async def test_fee_rates_from_api(self):
        ex = _make_exchange()
        ex._private_request = AsyncMock(return_value={
            "currency": "ZUSD",
            "volume": "1000",
            "fees": {
                "XXBTZUSD": {"fee": "0.2600", "minfee": "0.1000", "maxfee": "0.2600"},
            },
            "fees_maker": {
                "XXBTZUSD": {"fee": "0.1600", "minfee": "0.0000", "maxfee": "0.1600"},
            },
        })

        rates = await ex.get_fee_rates()
        assert "maker" in rates
        assert "taker" in rates

    @pytest.mark.asyncio
    async def test_fee_rates_default_on_error(self):
        ex = _make_exchange()
        ex._private_request = AsyncMock(return_value={"error": ["EAPI:error"]})

        rates = await ex.get_fee_rates()
        assert rates["maker"] == Decimal("0.0025")
        assert rates["taker"] == Decimal("0.004")


class TestGetTicker:
    @pytest.mark.asyncio
    async def test_ticker_parses_correctly(self):
        ex = _make_exchange()
        ex._public_request = AsyncMock(return_value={
            "XXBTZUSD": {
                "a": ["50100.0", "1", "1.000"],     # ask
                "b": ["50000.0", "1", "1.000"],     # bid
                "c": ["50050.0", "0.001"],           # last trade
            },
        })

        ticker = await ex.get_ticker("BTC-USD")
        assert ticker.symbol == "BTC-USD"
        assert ticker.price == 50050.0
        assert ticker.bid == 50000.0
        assert ticker.ask == 50100.0

    @pytest.mark.asyncio
    async def test_ticker_on_error(self):
        ex = _make_exchange()
        ex._public_request = AsyncMock(return_value={"error": ["bad"]})

        ticker = await ex.get_ticker("BTC-USD")
        assert ticker.price == 0.0


class TestGetBestBidAsk:
    @pytest.mark.asyncio
    async def test_bid_ask(self):
        ex = _make_exchange()
        ex._public_request = AsyncMock(return_value={
            "XXBTZUSD": {
                "a": ["50100.0", "1", "1.000"],
                "b": ["50000.0", "1", "1.000"],
                "c": ["50050.0", "0.001"],
            },
        })

        result = await ex.get_best_bid_ask("BTC-USD")
        assert result["bid"] == Decimal("50000.0")
        assert result["ask"] == Decimal("50100.0")


class TestParseOrder:
    def test_parse_buy_limit(self):
        ex = _make_exchange()
        data = {
            "status": "open",
            "vol": "0.5",
            "vol_exec": "0.0",
            "descr": {
                "pair": "XBTUSD",
                "type": "buy",
                "ordertype": "limit",
                "price": "45000",
            },
        }
        order = ex._parse_order("OTXID-123", data)
        assert order.order_id == "OTXID-123"
        assert order.side == Side.BUY
        assert order.status == OrderStatus.OPEN
        assert order.qty == Decimal("0.5")
        assert order.price == Decimal("45000")

    def test_parse_filled_sell(self):
        ex = _make_exchange()
        data = {
            "status": "closed",
            "vol": "1.0",
            "vol_exec": "1.0",
            "descr": {
                "pair": "XBTUSD",
                "type": "sell",
                "ordertype": "market",
                "price": "0",
            },
        }
        order = ex._parse_order("OSELL-456", data)
        assert order.side == Side.SELL
        assert order.status == OrderStatus.FILLED
        assert order.order_type == OrderType.MARKET


class TestProcessExecutions:
    def test_handle_fill_event(self):
        bus = MagicMock()
        ex = KrakenExchange(
            event_bus=bus,
            api_key="test",
            api_secret="dGVzdA==",
        )

        data = {
            "channel": "executions",
            "type": "update",
            "data": [
                {
                    "exec_type": "trade",
                    "exec_id": "TEXEC-001",
                    "order_id": "OABC-123",
                    "symbol": "XBT/USD",
                    "side": "buy",
                    "last_price": "50000",
                    "last_qty": "0.1",
                    "fee_paid": "1.25",
                    "fee_currency": "ZUSD",
                    "order_type": "limit",
                },
            ],
        }

        ex._process_executions(data)

        # Verify FillEvent was published
        assert bus.publish.called
        fill_event = bus.publish.call_args[0][0]
        assert fill_event.fill.fill_id == "TEXEC-001"
        assert fill_event.fill.symbol == "BTC-USD"
        assert fill_event.fill.side == Side.BUY
        assert fill_event.fill.price == Decimal("50000")
        assert fill_event.fill.qty == Decimal("0.1")
        assert fill_event.fill.fee == Decimal("1.25")
        assert fill_event.fill.fee_currency == "USD"  # Normalized

    def test_dedup_fills(self):
        bus = MagicMock()
        ex = KrakenExchange(event_bus=bus, api_key="t", api_secret="dA==")

        data = {
            "channel": "executions",
            "type": "update",
            "data": [
                {
                    "exec_type": "trade",
                    "exec_id": "TEXEC-DUP",
                    "order_id": "O1",
                    "symbol": "ETH/USD",
                    "side": "sell",
                    "last_price": "3000",
                    "last_qty": "1.0",
                    "fee_paid": "0.75",
                    "fee_currency": "ZUSD",
                    "order_type": "limit",
                },
            ],
        }

        ex._process_executions(data)
        ex._process_executions(data)  # Duplicate

        # Should only publish once
        assert bus.publish.call_count == 1
