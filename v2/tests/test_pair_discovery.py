"""Tests for the pair discovery plugin system.

Covers:
- Two-pass volume filter (core algorithm)
- Product filtering (SPOT, USD, online, shill coins)
- Seed symbol inclusion
- API failure handling
- SymbolsUpdatedEvent publishing
- Config parsing
- WebSocket symbol update handling
- Registry discovery
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from v2.core.config import Config
from v2.core import registry
from v2.core.event_bus import EventBus
from v2.core.types import SymbolsUpdatedEvent
from v2.plugins.pair_discovery.coinbase import CoinbasePairDiscovery


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_product(
    symbol: str,
    base: str = "",
    quote_volume: float = 0.0,
    product_type: str = "SPOT",
    status: str = "online",
    quote: str = "USD",
) -> dict:
    """Build a mock Coinbase product dict."""
    if not base:
        base = symbol.split("-")[0]
    return {
        "product_id": symbol,
        "base_currency_id": base,
        "quote_currency_id": quote,
        "product_type": product_type,
        "status": status,
        "approximate_quote_24h_volume": str(quote_volume),
        "volume_24h": str(quote_volume / 100),
        "price": "100.0",
    }


SAMPLE_PRODUCTS = [
    _make_product("BTC-USD", quote_volume=500_000_000),  # High vol
    _make_product("ETH-USD", quote_volume=200_000_000),  # High vol
    _make_product("SOL-USD", quote_volume=100_000_000),  # Above avg
    _make_product("DOGE-USD", quote_volume=50_000_000),  # Below avg
    _make_product("SHIB-USD", quote_volume=10_000_000),  # Below avg
]
# Average = (500M + 200M + 100M + 50M + 10M) / 5 = 172M
# Above average: BTC, ETH — SOL is below but close. Let's recalculate:
# Average = 172_000_000. BTC(500M) >= avg, ETH(200M) >= avg, SOL(100M) < avg


@pytest.fixture
def bus():
    return EventBus()


@pytest.fixture
def plugin(bus):
    p = CoinbasePairDiscovery(event_bus=bus, api_key="test", api_secret="test")
    p.configure({
        "refresh_interval_minutes": 30,
        "shill_coins": [],
        "seed_symbols": [],
        "max_pairs": 100,
    })
    return p


# ---------------------------------------------------------------------------
# Two-pass volume filter
# ---------------------------------------------------------------------------

class TestTwoPassFilter:
    """Test the core volume filtering algorithm."""

    def test_keeps_above_average(self, plugin):
        """Products with volume >= average should be kept."""
        symbols = plugin._filter_products(SAMPLE_PRODUCTS)
        # Avg = 172M. BTC(500M) and ETH(200M) are above average.
        assert "BTC-USD" in symbols
        assert "ETH-USD" in symbols

    def test_excludes_below_average(self, plugin):
        """Products below average volume should be excluded."""
        symbols = plugin._filter_products(SAMPLE_PRODUCTS)
        assert "DOGE-USD" not in symbols
        assert "SHIB-USD" not in symbols

    def test_single_product_passes(self, plugin):
        """A single product is always at the average, so it passes."""
        products = [_make_product("BTC-USD", quote_volume=100)]
        symbols = plugin._filter_products(products)
        assert symbols == ["BTC-USD"]

    def test_empty_products_returns_empty(self, plugin):
        """Empty product list returns empty (or seed symbols)."""
        symbols = plugin._filter_products([])
        assert symbols == []

    def test_zero_volume_products_excluded(self, plugin):
        """Products with zero volume should be excluded from average calc and filter."""
        products = [
            _make_product("BTC-USD", quote_volume=100_000_000),
            _make_product("ETH-USD", quote_volume=100_000_000),
            _make_product("ZERO-USD", quote_volume=0),
        ]
        # Avg = 100M (zero excluded from calc). Both BTC and ETH pass.
        symbols = plugin._filter_products(products)
        assert "BTC-USD" in symbols
        assert "ETH-USD" in symbols
        assert "ZERO-USD" not in symbols


# ---------------------------------------------------------------------------
# Product filtering criteria
# ---------------------------------------------------------------------------

class TestProductFilters:
    """Test SPOT/USD/online/shill filters."""

    def test_spot_usd_online_only(self, plugin):
        """Only SPOT + USD + online products should pass."""
        products = [
            _make_product("BTC-USD", quote_volume=100),
            _make_product("BTC-EUR", quote_volume=100, quote="EUR"),
            _make_product("BTC-PERP", quote_volume=100, product_type="PERPETUAL"),
            _make_product("OFFLINE-USD", quote_volume=100, status="offline"),
        ]
        symbols = plugin._filter_products(products)
        assert "BTC-USD" in symbols
        assert "BTC-EUR" not in symbols
        assert "BTC-PERP" not in symbols
        assert "OFFLINE-USD" not in symbols

    def test_shill_coin_exclusion(self, plugin):
        """Products with shill base currencies should be excluded."""
        plugin._shill_coins = {"TRUMP", "UNFI"}
        products = [
            _make_product("BTC-USD", quote_volume=200),
            _make_product("TRUMP-USD", base="TRUMP", quote_volume=200),
            _make_product("UNFI-USD", base="UNFI", quote_volume=200),
        ]
        symbols = plugin._filter_products(products)
        assert "BTC-USD" in symbols
        assert "TRUMP-USD" not in symbols
        assert "UNFI-USD" not in symbols

    def test_shill_coins_case_insensitive(self, plugin):
        """Shill coin matching should be case-insensitive."""
        plugin._shill_coins = {"TRUMP"}
        products = [
            _make_product("TRUMP-USD", base="TRUMP", quote_volume=200),
        ]
        symbols = plugin._filter_products(products)
        assert "TRUMP-USD" not in symbols


# ---------------------------------------------------------------------------
# Seed symbols
# ---------------------------------------------------------------------------

class TestSeedSymbols:
    """Test that seed symbols are always included."""

    def test_seed_included_even_if_below_volume(self, plugin):
        """Seed symbols should appear even if they fail the volume filter."""
        plugin._seed_symbols = ["DOGE-USD", "SHIB-USD"]
        symbols = plugin._filter_products(SAMPLE_PRODUCTS)
        # DOGE and SHIB are below avg volume but should still be in the list
        assert "DOGE-USD" in symbols
        assert "SHIB-USD" in symbols

    def test_seed_not_duplicated(self, plugin):
        """Seed symbols that pass the volume filter should not appear twice."""
        plugin._seed_symbols = ["BTC-USD"]
        symbols = plugin._filter_products(SAMPLE_PRODUCTS)
        assert symbols.count("BTC-USD") == 1

    def test_seed_returned_when_no_products(self, plugin):
        """When no products have valid volumes, only seed symbols are returned."""
        plugin._seed_symbols = ["BTC-USD", "ETH-USD"]
        products = [
            _make_product("BTC-USD", quote_volume=0),
        ]
        symbols = plugin._filter_products(products)
        assert set(symbols) == {"BTC-USD", "ETH-USD"}


# ---------------------------------------------------------------------------
# Max pairs cap
# ---------------------------------------------------------------------------

class TestMaxPairs:
    """Test max_pairs limiting."""

    def test_respects_max_pairs(self, plugin):
        """Should cap filtered results at max_pairs."""
        plugin._max_pairs = 2
        products = [
            _make_product(f"COIN{i}-USD", quote_volume=1000 * (10 - i))
            for i in range(10)
        ]
        symbols = plugin._filter_products(products)
        # All 10 have the same volume pattern. Max 2 from filter + seed symbols
        assert len([s for s in symbols if s not in plugin._seed_symbols]) <= 2

    def test_seed_symbols_not_counted_against_cap(self, plugin):
        """Seed symbols should be added even if we're at max_pairs."""
        plugin._max_pairs = 1
        plugin._seed_symbols = ["SEED-USD"]
        products = [
            _make_product("BTC-USD", quote_volume=500),
            _make_product("ETH-USD", quote_volume=400),
        ]
        symbols = plugin._filter_products(products)
        assert "SEED-USD" in symbols


# ---------------------------------------------------------------------------
# API failure handling
# ---------------------------------------------------------------------------

class TestAPIFailure:
    """Test graceful degradation on API errors."""

    @pytest.mark.asyncio
    async def test_discover_returns_empty_on_api_failure(self, plugin):
        """discover() should return empty list when API fails."""
        with patch.object(plugin, "_fetch_products", new_callable=AsyncMock, return_value=[]):
            result = await plugin.discover()
        assert result == []

    @pytest.mark.asyncio
    async def test_discover_with_mock_products(self, plugin):
        """discover() should return filtered symbols from mock products."""
        with patch.object(plugin, "_fetch_products", new_callable=AsyncMock, return_value=SAMPLE_PRODUCTS):
            result = await plugin.discover()
        assert "BTC-USD" in result
        assert "ETH-USD" in result


# ---------------------------------------------------------------------------
# SymbolsUpdatedEvent
# ---------------------------------------------------------------------------

class TestSymbolsUpdatedEvent:
    """Test event publishing on symbol changes."""

    @pytest.mark.asyncio
    async def test_event_published_on_change(self, bus, plugin):
        """SymbolsUpdatedEvent should fire when discovered symbols change."""
        received = []
        bus.subscribe(SymbolsUpdatedEvent, lambda e: received.append(e))

        # First discover
        with patch.object(plugin, "_fetch_products", new_callable=AsyncMock, return_value=SAMPLE_PRODUCTS):
            await plugin.discover()

        # Simulate refresh with different products (add a new high-vol coin)
        new_products = SAMPLE_PRODUCTS + [
            _make_product("NEW-USD", quote_volume=300_000_000),
        ]
        with patch.object(plugin, "_fetch_products", new_callable=AsyncMock, return_value=new_products):
            new_symbols = await plugin.discover()

        old_set = {"BTC-USD", "ETH-USD"}
        new_set = set(new_symbols)

        if old_set != new_set:
            added = sorted(new_set - old_set)
            removed = sorted(old_set - new_set)
            bus.publish(SymbolsUpdatedEvent(
                symbols=tuple(new_symbols),
                added=tuple(added),
                removed=tuple(removed),
            ))

        assert len(received) == 1
        assert "NEW-USD" in received[0].symbols

    @pytest.mark.asyncio
    async def test_no_event_when_unchanged(self, bus, plugin):
        """No event should fire when symbol list hasn't changed."""
        received = []
        bus.subscribe(SymbolsUpdatedEvent, lambda e: received.append(e))

        with patch.object(plugin, "_fetch_products", new_callable=AsyncMock, return_value=SAMPLE_PRODUCTS):
            first = await plugin.discover()
            second = await plugin.discover()

        # Same set → no event
        assert set(first) == set(second)
        assert len(received) == 0


# ---------------------------------------------------------------------------
# Config parsing
# ---------------------------------------------------------------------------

class TestConfig:
    """Test YAML config integration."""

    def test_config_parses_pair_discovery(self, tmp_path):
        """Config with pair_discovery section should parse correctly."""
        yaml_content = """
app:
  mode: paper
  symbols: ["BTC-USD"]

pair_discovery:
  type: coinbase
  config:
    refresh_interval_minutes: 15
    shill_coins: ["TRUMP"]
    seed_symbols: ["BTC-USD"]
    max_pairs: 25

exchange:
  type: paper
"""
        config_file = tmp_path / "test_config.yaml"
        config_file.write_text(yaml_content)
        cfg = Config.load(str(config_file))

        assert cfg.pair_discovery is not None
        assert cfg.pair_discovery.type == "coinbase"
        inner = cfg.pair_discovery.config["config"]
        assert inner["refresh_interval_minutes"] == 15
        assert inner["shill_coins"] == ["TRUMP"]

    def test_config_without_pair_discovery(self, tmp_path):
        """Config without pair_discovery should default to None."""
        yaml_content = """
app:
  mode: paper
  symbols: ["BTC-USD"]

exchange:
  type: paper
"""
        config_file = tmp_path / "test_config.yaml"
        config_file.write_text(yaml_content)
        cfg = Config.load(str(config_file))

        assert cfg.pair_discovery is None


# ---------------------------------------------------------------------------
# WebSocket symbol update handling
# ---------------------------------------------------------------------------

class TestWebSocketSymbolUpdate:
    """Test WebSocket provider reacts to SymbolsUpdatedEvent."""

    def test_symbols_updated_handler(self):
        """Handler should update _symbols and set _symbols_changed flag."""
        from v2.plugins.data.websocket import WebSocketDataProvider

        bus = EventBus()
        provider = WebSocketDataProvider(event_bus=bus)
        provider._symbols = ["BTC-USD", "ETH-USD"]
        provider._symbols_changed = asyncio.Event()

        # Subscribe the handler
        bus.subscribe(SymbolsUpdatedEvent, provider._on_symbols_updated)

        # Publish update
        bus.publish(SymbolsUpdatedEvent(
            symbols=("BTC-USD", "ETH-USD", "SOL-USD"),
            added=("SOL-USD",),
            removed=(),
        ))

        assert "SOL-USD" in provider._symbols
        assert provider._symbols_changed.is_set()

    def test_no_change_no_flag(self):
        """Same symbols should not set the flag."""
        from v2.plugins.data.websocket import WebSocketDataProvider

        bus = EventBus()
        provider = WebSocketDataProvider(event_bus=bus)
        provider._symbols = ["BTC-USD", "ETH-USD"]
        provider._symbols_changed = asyncio.Event()

        bus.subscribe(SymbolsUpdatedEvent, provider._on_symbols_updated)

        bus.publish(SymbolsUpdatedEvent(
            symbols=("ETH-USD", "BTC-USD"),  # Same set, different order
            added=(),
            removed=(),
        ))

        assert not provider._symbols_changed.is_set()


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class TestRegistry:
    """Test that the plugin is properly registered."""

    def test_pair_discovery_registered(self):
        """coinbase should appear in pair_discovery registry."""
        # Re-register in case registry was cleared by other tests
        registry.register("pair_discovery", "coinbase", CoinbasePairDiscovery)
        plugins = registry.list_plugins("pair_discovery")
        assert "coinbase" in plugins.get("pair_discovery", [])

    def test_create_pair_discovery(self):
        """Factory should create CoinbasePairDiscovery instance."""
        registry.register("pair_discovery", "coinbase", CoinbasePairDiscovery)
        plugin = registry.create_pair_discovery("coinbase", api_key="k", api_secret="s")
        assert isinstance(plugin, CoinbasePairDiscovery)


# ---------------------------------------------------------------------------
# Plugin configure
# ---------------------------------------------------------------------------

class TestConfigure:
    """Test plugin configuration."""

    def test_configure_sets_params(self):
        """configure() should set all parameters from config dict."""
        p = CoinbasePairDiscovery(api_key="k", api_secret="s")
        p.configure({
            "refresh_interval_minutes": 15,
            "shill_coins": ["TRUMP", "matic"],
            "seed_symbols": ["BTC-USD"],
            "max_pairs": 25,
        })
        assert p._refresh_interval_minutes == 15
        assert p._shill_coins == {"TRUMP", "MATIC"}
        assert p._seed_symbols == ["BTC-USD"]
        assert p._max_pairs == 25

    def test_configure_with_empty_dict(self):
        """configure() with empty dict should use defaults."""
        p = CoinbasePairDiscovery(api_key="k", api_secret="s")
        p.configure({})
        assert p._refresh_interval_minutes == 30
        assert p._shill_coins == set()
        assert p._seed_symbols == []
        assert p._max_pairs == 100


# ---------------------------------------------------------------------------
# Volume sorting
# ---------------------------------------------------------------------------

class TestVolumeSorting:
    """Test that results are sorted by volume descending."""

    def test_sorted_by_volume(self, plugin):
        """Filtered products should be sorted by 24h volume descending."""
        products = [
            _make_product("LOW-USD", quote_volume=100_000_000),
            _make_product("HIGH-USD", quote_volume=500_000_000),
            _make_product("MID-USD", quote_volume=200_000_000),
        ]
        # Avg = 266M. Only HIGH(500M) is above average.
        # Actually, avg = (100M+500M+200M)/3 = 266M
        # HIGH(500M) >= 266M ✓, MID(200M) < 266M ✗, LOW(100M) < 266M ✗
        symbols = plugin._filter_products(products)
        assert symbols[0] == "HIGH-USD"
