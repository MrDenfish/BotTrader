"""Tests for v2.plugins.pair_discovery.kraken."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from v2.plugins.pair_discovery.kraken import KrakenPairDiscovery


# ------------------------------------------------------------------
# Sample API responses
# ------------------------------------------------------------------

SAMPLE_ASSET_PAIRS = {
    "XXBTZUSD": {
        "altname": "XBTUSD",
        "wsname": "XBT/USD",
        "base": "XXBT",
        "quote": "ZUSD",
        "status": "online",
    },
    "XETHZUSD": {
        "altname": "ETHUSD",
        "wsname": "ETH/USD",
        "base": "XETH",
        "quote": "ZUSD",
        "status": "online",
    },
    "SOLUSD": {
        "altname": "SOLUSD",
        "wsname": "SOL/USD",
        "base": "SOL",
        "quote": "ZUSD",
        "status": "online",
    },
    "ADAUSD": {
        "altname": "ADAUSD",
        "wsname": "ADA/USD",
        "base": "ADA",
        "quote": "ZUSD",
        "status": "online",
    },
    "XETHXXBT": {
        "altname": "ETHXBT",
        "wsname": "ETH/XBT",
        "base": "XETH",
        "quote": "XXBT",
        "status": "online",
    },
    "TRUMPUSD": {
        "altname": "TRUMPUSD",
        "wsname": "TRUMP/USD",
        "base": "TRUMP",
        "quote": "ZUSD",
        "status": "online",
    },
}

SAMPLE_VOLUMES = {
    "XXBTZUSD": 3_000_000,    # $3M — above threshold ✓
    "XETHZUSD": 2_000_000,    # $2M — above threshold ✓
    "SOLUSD": 2_000_000,      # $2M — above threshold ✓
    "ADAUSD": 100,            # $100 — well below threshold ✗
    "TRUMPUSD": 2_000_000,    # $2M — above threshold but shill ✗
}
# Average = (3M + 2M + 2M + 100 + 2M) / 5 = ~1.8M
# Threshold = max(1.8M, 500K) = 1.8M

# Tight spreads for all pairs (won't be filtered)
SAMPLE_SPREADS = {
    "XXBTZUSD": 1.0,     # 0.01% — very tight
    "XETHZUSD": 2.0,     # 0.02%
    "SOLUSD": 5.0,        # 0.05%
    "ADAUSD": 20.0,       # 0.20%
    "TRUMPUSD": 50.0,     # 0.50%
}


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------

class TestKrakenPairDiscovery:
    def setup_method(self):
        self.discovery = KrakenPairDiscovery(event_bus=None)
        self.discovery.configure({
            "refresh_interval_minutes": 30,
            "min_quote_volume": 500_000,
            "shill_coins": ["TRUMP"],
            "seed_symbols": ["BTC-USD", "ETH-USD"],
            "max_pairs": 50,
        })

    def test_configure(self):
        assert self.discovery._min_quote_volume == 500_000
        assert "TRUMP" in self.discovery._shill_coins
        assert self.discovery._seed_symbols == ["BTC-USD", "ETH-USD"]
        assert self.discovery._max_pairs == 50

    def test_configure_spread(self):
        """max_spread_bps is parsed from config."""
        d = KrakenPairDiscovery()
        d.configure({"max_spread_bps": 100})
        assert d._max_spread_bps == 100.0

    def test_configure_no_dict(self):
        """Non-dict config is ignored."""
        d = KrakenPairDiscovery()
        d.configure("not a dict")
        assert d._configured is False

    @pytest.mark.asyncio
    async def test_discover_filters_by_volume(self):
        """Pairs below volume threshold are excluded."""
        self.discovery._fetch_asset_pairs = AsyncMock(return_value=SAMPLE_ASSET_PAIRS)
        self.discovery._fetch_volumes = AsyncMock(return_value=(SAMPLE_VOLUMES, SAMPLE_SPREADS))

        symbols = await self.discovery.discover()

        assert "BTC-USD" in symbols
        assert "ETH-USD" in symbols
        assert "SOL-USD" in symbols
        assert "ADA-USD" not in symbols
        assert "TRUMP-USD" not in symbols

    @pytest.mark.asyncio
    async def test_discover_seed_symbols_always_included(self):
        """Seed symbols are included even if they fail volume filter."""
        small_pairs = {
            "ADAUSD": {
                "altname": "ADAUSD",
                "wsname": "ADA/USD",
                "base": "ADA",
                "quote": "ZUSD",
            },
        }
        small_volumes = {"ADAUSD": 50.0}
        small_spreads = {"ADAUSD": 10.0}

        self.discovery._fetch_asset_pairs = AsyncMock(return_value=small_pairs)
        self.discovery._fetch_volumes = AsyncMock(return_value=(small_volumes, small_spreads))

        symbols = await self.discovery.discover()

        # ADA doesn't pass volume filter, but seeds must be included
        assert "BTC-USD" in symbols
        assert "ETH-USD" in symbols

    @pytest.mark.asyncio
    async def test_discover_excludes_shill_coins(self):
        """Shill coins are excluded regardless of volume."""
        self.discovery._fetch_asset_pairs = AsyncMock(return_value=SAMPLE_ASSET_PAIRS)
        self.discovery._fetch_volumes = AsyncMock(return_value=(SAMPLE_VOLUMES, SAMPLE_SPREADS))

        symbols = await self.discovery.discover()
        assert "TRUMP-USD" not in symbols

    @pytest.mark.asyncio
    async def test_discover_empty_on_api_failure(self):
        """Returns empty list on API failure."""
        self.discovery._fetch_asset_pairs = AsyncMock(return_value={})

        symbols = await self.discovery.discover()
        assert symbols == []

    @pytest.mark.asyncio
    async def test_discover_populates_mapper(self):
        """discover() populates the symbol mapper."""
        self.discovery._fetch_asset_pairs = AsyncMock(return_value=SAMPLE_ASSET_PAIRS)
        self.discovery._fetch_volumes = AsyncMock(return_value=(SAMPLE_VOLUMES, SAMPLE_SPREADS))

        await self.discovery.discover()

        # Mapper should now have entries
        mapper = self.discovery.mapper
        assert mapper.to_kraken_ws("BTC-USD") == "XBT/USD"
        assert mapper.from_kraken_rest("XXBTZUSD") == "BTC-USD"

    def test_collect_usd_pairs(self):
        """Only USD pairs are collected."""
        self.discovery.mapper.load_from_asset_pairs(SAMPLE_ASSET_PAIRS)
        usd_pairs = self.discovery._collect_usd_pairs(SAMPLE_ASSET_PAIRS)

        assert "XXBTZUSD" in usd_pairs
        assert "XETHZUSD" in usd_pairs
        assert "XETHXXBT" not in usd_pairs  # ETH/XBT not USD

    def test_filter_by_volume_sorts_descending(self):
        """Filtered pairs are sorted by volume descending."""
        self.discovery.mapper.load_from_asset_pairs(SAMPLE_ASSET_PAIRS)
        usd_pairs = self.discovery._collect_usd_pairs(SAMPLE_ASSET_PAIRS)

        symbols = self.discovery._filter_by_volume(usd_pairs, SAMPLE_VOLUMES)

        # BTC should be first (highest volume), then ETH, then SOL
        if "BTC-USD" in symbols and "ETH-USD" in symbols:
            btc_idx = symbols.index("BTC-USD")
            eth_idx = symbols.index("ETH-USD")
            assert btc_idx < eth_idx

    @pytest.mark.asyncio
    async def test_discover_max_pairs_limit(self):
        """max_pairs caps the number of returned symbols."""
        self.discovery._max_pairs = 2
        self.discovery._fetch_asset_pairs = AsyncMock(return_value=SAMPLE_ASSET_PAIRS)
        self.discovery._fetch_volumes = AsyncMock(return_value=(SAMPLE_VOLUMES, SAMPLE_SPREADS))

        symbols = await self.discovery.discover()

        # 2 from filter + 2 seeds (if not already included)
        # BTC-USD and ETH-USD are both top-volume AND seeds
        assert len(symbols) <= 4  # At most max_pairs + seeds


# ------------------------------------------------------------------
# Spread filter tests
# ------------------------------------------------------------------

class TestSpreadFilter:
    def setup_method(self):
        self.discovery = KrakenPairDiscovery(event_bus=None)
        self.discovery.configure({
            "min_quote_volume": 500_000,
            "max_spread_bps": 100,        # 1.0% max spread
            "shill_coins": [],
            "seed_symbols": ["BTC-USD"],
            "max_pairs": 50,
        })
        self.discovery.mapper.load_from_asset_pairs(SAMPLE_ASSET_PAIRS)

    def test_spread_rejects_wide_spread_pair(self):
        """Pairs with spread > max_spread_bps are excluded."""
        usd_pairs = self.discovery._collect_usd_pairs(SAMPLE_ASSET_PAIRS)

        volumes = {
            "XXBTZUSD": 5_000_000,
            "XETHZUSD": 5_000_000,
            "SOLUSD": 5_000_000,
            "ADAUSD": 5_000_000,   # passes volume but has wide spread
        }
        spreads = {
            "XXBTZUSD": 2.0,      # 0.02% — tight
            "XETHZUSD": 5.0,      # 0.05% — tight
            "SOLUSD": 50.0,        # 0.50% — OK
            "ADAUSD": 200.0,       # 2.00% — TOO WIDE
        }

        symbols = self.discovery._filter_by_volume(usd_pairs, volumes, spreads)

        assert "BTC-USD" in symbols
        assert "ETH-USD" in symbols
        assert "SOL-USD" in symbols
        assert "ADA-USD" not in symbols   # rejected by spread gate

    def test_spread_filter_disabled_when_zero(self):
        """When max_spread_bps=0, no pairs are filtered by spread."""
        self.discovery._max_spread_bps = 0.0
        usd_pairs = self.discovery._collect_usd_pairs(SAMPLE_ASSET_PAIRS)

        volumes = {
            "XXBTZUSD": 5_000_000,
            "ADAUSD": 5_000_000,
        }
        spreads = {
            "XXBTZUSD": 2.0,
            "ADAUSD": 500.0,      # 5% spread — would be rejected if enabled
        }

        symbols = self.discovery._filter_by_volume(usd_pairs, volumes, spreads)

        assert "BTC-USD" in symbols
        assert "ADA-USD" in symbols  # NOT rejected because filter disabled

    def test_spread_missing_data_passes(self):
        """Pairs with no spread data are NOT rejected (graceful degradation)."""
        usd_pairs = self.discovery._collect_usd_pairs(SAMPLE_ASSET_PAIRS)

        volumes = {
            "XXBTZUSD": 5_000_000,
            "XETHZUSD": 5_000_000,
        }
        spreads = {
            "XXBTZUSD": 2.0,
            # XETHZUSD intentionally missing — should still pass
        }

        symbols = self.discovery._filter_by_volume(usd_pairs, volumes, spreads)

        assert "BTC-USD" in symbols
        assert "ETH-USD" in symbols  # passes because spread=0.0 < 100

    def test_spread_borderline_at_threshold(self):
        """Pair at exactly max_spread_bps passes; just above is rejected."""
        usd_pairs = self.discovery._collect_usd_pairs(SAMPLE_ASSET_PAIRS)

        volumes = {"XXBTZUSD": 5_000_000, "SOLUSD": 5_000_000, "ADAUSD": 5_000_000}
        spreads = {"XXBTZUSD": 2.0, "SOLUSD": 100.0, "ADAUSD": 100.1}

        symbols = self.discovery._filter_by_volume(usd_pairs, volumes, spreads)

        assert "BTC-USD" in symbols
        assert "SOL-USD" in symbols       # exactly 100 is NOT > 100 → passes
        assert "ADA-USD" not in symbols   # 100.1 > 100 → rejected

    @pytest.mark.asyncio
    async def test_spread_end_to_end_discover(self):
        """Full discover() flow with spread filtering."""
        self.discovery._fetch_asset_pairs = AsyncMock(return_value=SAMPLE_ASSET_PAIRS)

        volumes = {
            "XXBTZUSD": 5_000_000,
            "XETHZUSD": 5_000_000,
            "SOLUSD": 5_000_000,
            "ADAUSD": 5_000_000,
        }
        spreads = {
            "XXBTZUSD": 2.0,
            "XETHZUSD": 3.0,
            "SOLUSD": 50.0,
            "ADAUSD": 150.0,       # rejected: 1.5% > 1.0%
        }
        self.discovery._fetch_volumes = AsyncMock(return_value=(volumes, spreads))

        symbols = await self.discovery.discover()

        assert "BTC-USD" in symbols
        assert "ETH-USD" in symbols
        assert "SOL-USD" in symbols
        assert "ADA-USD" not in symbols


# ------------------------------------------------------------------
# Listing-age filter tests
# ------------------------------------------------------------------

DAY = 86_400.0


class TestListingAgeFilter:
    def setup_method(self):
        self.discovery = KrakenPairDiscovery(event_bus=None)
        self.discovery.configure({
            "min_quote_volume": 500_000,
            "min_listing_age_days": 365,
            "shill_coins": [],
            "seed_symbols": ["BTC-USD"],
            "max_pairs": 50,
        })
        self.discovery.mapper.load_from_asset_pairs(SAMPLE_ASSET_PAIRS)
        self.now = 1_785_500_000.0  # fixed "now" for deterministic ages

    def test_configure_min_listing_age(self):
        assert self.discovery._min_listing_age_days == 365

    def test_configure_min_listing_age_default_disabled(self):
        d = KrakenPairDiscovery()
        d.configure({})
        assert d._min_listing_age_days == 0

    @pytest.mark.asyncio
    async def test_young_listing_rejected(self):
        """Symbol first traded 100 days ago is dropped by a 365d gate."""
        self.discovery._fetch_first_bar_ts = AsyncMock(
            side_effect=lambda rest: {
                "SOLUSD": self.now - 100 * DAY,       # young
                "XETHZUSD": self.now - 2_000 * DAY,   # old
            }.get(rest)
        )
        symbols = await self.discovery._filter_by_listing_age(
            ["SOL-USD", "ETH-USD"], now=self.now,
        )
        assert "SOL-USD" not in symbols
        assert "ETH-USD" in symbols

    @pytest.mark.asyncio
    async def test_seed_symbols_bypass_age_gate(self):
        """Seeds are kept without any listing-date lookup."""
        fetch = AsyncMock(return_value=self.now - 1 * DAY)
        self.discovery._fetch_first_bar_ts = fetch

        symbols = await self.discovery._filter_by_listing_age(
            ["BTC-USD"], now=self.now,
        )
        assert "BTC-USD" in symbols
        fetch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unknown_listing_age_kept(self):
        """If the listing date cannot be fetched, keep the pair (graceful degradation)."""
        self.discovery._fetch_first_bar_ts = AsyncMock(return_value=None)
        symbols = await self.discovery._filter_by_listing_age(
            ["ETH-USD"], now=self.now,
        )
        assert "ETH-USD" in symbols

    @pytest.mark.asyncio
    async def test_listing_ts_cached_across_calls(self):
        """Listing dates are fetched at most once per pair."""
        fetch = AsyncMock(return_value=self.now - 2_000 * DAY)
        self.discovery._fetch_first_bar_ts = fetch

        await self.discovery._filter_by_listing_age(["ETH-USD"], now=self.now)
        await self.discovery._filter_by_listing_age(["ETH-USD"], now=self.now)

        assert fetch.await_count == 1

    @pytest.mark.asyncio
    async def test_age_gate_boundary(self):
        """Exactly min age passes; one day younger is rejected."""
        self.discovery._fetch_first_bar_ts = AsyncMock(
            side_effect=lambda rest: {
                "XETHZUSD": self.now - 365 * DAY,   # exactly at threshold
                "SOLUSD": self.now - 364 * DAY,     # one day short
            }.get(rest)
        )
        symbols = await self.discovery._filter_by_listing_age(
            ["ETH-USD", "SOL-USD"], now=self.now,
        )
        assert "ETH-USD" in symbols
        assert "SOL-USD" not in symbols

    @pytest.mark.asyncio
    async def test_discover_applies_age_gate(self):
        """Full discover() flow drops young listings."""
        self.discovery._fetch_asset_pairs = AsyncMock(return_value=SAMPLE_ASSET_PAIRS)
        volumes = {"XXBTZUSD": 5_000_000, "XETHZUSD": 5_000_000, "SOLUSD": 5_000_000}
        spreads = {"XXBTZUSD": 2.0, "XETHZUSD": 3.0, "SOLUSD": 5.0}
        self.discovery._fetch_volumes = AsyncMock(return_value=(volumes, spreads))
        self.discovery._fetch_first_bar_ts = AsyncMock(
            side_effect=lambda rest: {
                "XXBTZUSD": self.now - 4_000 * DAY,
                "XETHZUSD": self.now - 4_000 * DAY,
                "SOLUSD": self.now - 30 * DAY,      # fresh listing
            }.get(rest)
        )
        with patch("v2.plugins.pair_discovery.kraken.time.time", return_value=self.now):
            symbols = await self.discovery.discover()

        assert "BTC-USD" in symbols
        assert "ETH-USD" in symbols
        assert "SOL-USD" not in symbols

    @pytest.mark.asyncio
    async def test_discover_age_gate_disabled_by_default(self):
        """min_listing_age_days=0 → no listing-date lookups at all."""
        d = KrakenPairDiscovery(event_bus=None)
        d.configure({"min_quote_volume": 500_000, "seed_symbols": []})
        fetch = AsyncMock()
        d._fetch_first_bar_ts = fetch
        d._fetch_asset_pairs = AsyncMock(return_value=SAMPLE_ASSET_PAIRS)
        d._fetch_volumes = AsyncMock(
            return_value=(SAMPLE_VOLUMES, SAMPLE_SPREADS)
        )

        symbols = await d.discover()

        assert "SOL-USD" in symbols
        fetch.assert_not_awaited()


class TestKrakenPairDiscoveryLifecycle:
    @pytest.mark.asyncio
    async def test_start_stop(self):
        d = KrakenPairDiscovery(event_bus=None)
        d.configure({"refresh_interval_minutes": 30})
        await d.start()
        assert d._refresh_task is not None
        await d.stop()
        assert d._refresh_task.cancelled() or d._refresh_task.done()
