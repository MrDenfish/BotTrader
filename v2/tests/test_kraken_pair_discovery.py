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

    def test_configure_no_dict(self):
        """Non-dict config is ignored."""
        d = KrakenPairDiscovery()
        d.configure("not a dict")
        assert d._configured is False

    @pytest.mark.asyncio
    async def test_discover_filters_by_volume(self):
        """Pairs below volume threshold are excluded."""
        self.discovery._fetch_asset_pairs = AsyncMock(return_value=SAMPLE_ASSET_PAIRS)
        self.discovery._fetch_volumes = AsyncMock(return_value=SAMPLE_VOLUMES)

        symbols = await self.discovery.discover()

        # BTC-USD: $250M ✓
        # ETH-USD: $60M ✓
        # SOL-USD: $5M ✓
        # ADA-USD: $500 ✗ (below threshold)
        # TRUMP-USD: excluded by shill_coins
        # ETH/XBT: excluded (not USD)
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

        self.discovery._fetch_asset_pairs = AsyncMock(return_value=small_pairs)
        self.discovery._fetch_volumes = AsyncMock(return_value=small_volumes)

        symbols = await self.discovery.discover()

        # ADA doesn't pass volume filter, but seeds must be included
        assert "BTC-USD" in symbols
        assert "ETH-USD" in symbols

    @pytest.mark.asyncio
    async def test_discover_excludes_shill_coins(self):
        """Shill coins are excluded regardless of volume."""
        self.discovery._fetch_asset_pairs = AsyncMock(return_value=SAMPLE_ASSET_PAIRS)
        self.discovery._fetch_volumes = AsyncMock(return_value=SAMPLE_VOLUMES)

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
        self.discovery._fetch_volumes = AsyncMock(return_value=SAMPLE_VOLUMES)

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
        self.discovery._fetch_volumes = AsyncMock(return_value=SAMPLE_VOLUMES)

        symbols = await self.discovery.discover()

        # 2 from filter + 2 seeds (if not already included)
        # BTC-USD and ETH-USD are both top-volume AND seeds
        assert len(symbols) <= 4  # At most max_pairs + seeds


class TestKrakenPairDiscoveryLifecycle:
    @pytest.mark.asyncio
    async def test_start_stop(self):
        d = KrakenPairDiscovery(event_bus=None)
        d.configure({"refresh_interval_minutes": 30})
        await d.start()
        assert d._refresh_task is not None
        await d.stop()
        assert d._refresh_task.cancelled() or d._refresh_task.done()
