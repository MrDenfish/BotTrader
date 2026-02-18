"""Tests for v2.utils.symbol_mapper."""

import pytest

from v2.utils.symbol_mapper import KrakenSymbolMapper


# ------------------------------------------------------------------
# Static fallback conversions (no AssetPairs loaded)
# ------------------------------------------------------------------

class TestStaticConversions:
    def setup_method(self):
        self.mapper = KrakenSymbolMapper()

    def test_to_kraken_ws_standard(self):
        assert self.mapper.to_kraken_ws("ETH-USD") == "ETH/USD"

    def test_to_kraken_ws_btc_maps_to_xbt(self):
        assert self.mapper.to_kraken_ws("BTC-USD") == "XBT/USD"

    def test_to_kraken_ws_doge_maps_to_xdg(self):
        assert self.mapper.to_kraken_ws("DOGE-USD") == "XDG/USD"

    def test_to_kraken_rest_standard(self):
        assert self.mapper.to_kraken_rest("ETH-USD") == "ETHUSD"

    def test_to_kraken_rest_btc(self):
        assert self.mapper.to_kraken_rest("BTC-USD") == "XBTUSD"

    def test_to_kraken_rest_doge(self):
        assert self.mapper.to_kraken_rest("DOGE-USD") == "XDGUSD"

    def test_from_kraken_ws_standard(self):
        assert self.mapper.from_kraken_ws("ETH/USD") == "ETH-USD"

    def test_from_kraken_ws_xbt(self):
        assert self.mapper.from_kraken_ws("XBT/USD") == "BTC-USD"

    def test_from_kraken_ws_xdg(self):
        assert self.mapper.from_kraken_ws("XDG/USD") == "DOGE-USD"

    def test_from_kraken_rest_unknown_passthrough(self):
        """Unknown REST pair names pass through unchanged."""
        assert self.mapper.from_kraken_rest("FOOBARBAZ") == "FOOBARBAZ"

    def test_roundtrip_ws(self):
        """Internal -> WS -> Internal should roundtrip."""
        for symbol in ["BTC-USD", "ETH-USD", "SOL-USD", "DOGE-USD", "XRP-USD"]:
            ws = self.mapper.to_kraken_ws(symbol)
            back = self.mapper.from_kraken_ws(ws)
            assert back == symbol, f"Roundtrip failed for {symbol}: {ws} -> {back}"

    def test_invalid_format_passthrough(self):
        assert self.mapper.to_kraken_ws("NODASH") == "NODASH"
        assert self.mapper.from_kraken_ws("NOSLASH") == "NOSLASH"


# ------------------------------------------------------------------
# Dynamic loading from AssetPairs response
# ------------------------------------------------------------------

SAMPLE_ASSET_PAIRS = {
    "XXBTZUSD": {
        "altname": "XBTUSD",
        "wsname": "XBT/USD",
        "base": "XXBT",
        "quote": "ZUSD",
    },
    "XETHZUSD": {
        "altname": "ETHUSD",
        "wsname": "ETH/USD",
        "base": "XETH",
        "quote": "ZUSD",
    },
    "SOLUSD": {
        "altname": "SOLUSD",
        "wsname": "SOL/USD",
        "base": "SOL",
        "quote": "ZUSD",
    },
    "XXDGZUSD": {
        "altname": "XDGUSD",
        "wsname": "XDG/USD",
        "base": "XXDG",
        "quote": "ZUSD",
    },
    "XETHXXBT": {
        "altname": "ETHXBT",
        "wsname": "ETH/XBT",
        "base": "XETH",
        "quote": "XXBT",
    },
}


class TestDynamicLoading:
    def setup_method(self):
        self.mapper = KrakenSymbolMapper()
        self.mapper.load_from_asset_pairs(SAMPLE_ASSET_PAIRS)

    def test_load_count(self):
        mapper = KrakenSymbolMapper()
        count = mapper.load_from_asset_pairs(SAMPLE_ASSET_PAIRS)
        assert count == 5

    def test_to_kraken_rest_dynamic(self):
        assert self.mapper.to_kraken_rest("BTC-USD") == "XXBTZUSD"
        assert self.mapper.to_kraken_rest("ETH-USD") == "XETHZUSD"
        assert self.mapper.to_kraken_rest("SOL-USD") == "SOLUSD"

    def test_from_kraken_rest_dynamic(self):
        assert self.mapper.from_kraken_rest("XXBTZUSD") == "BTC-USD"
        assert self.mapper.from_kraken_rest("XETHZUSD") == "ETH-USD"

    def test_from_kraken_rest_altname(self):
        """Altnames also map back to internal format."""
        assert self.mapper.from_kraken_rest("XBTUSD") == "BTC-USD"
        assert self.mapper.from_kraken_rest("ETHUSD") == "ETH-USD"

    def test_to_kraken_ws_dynamic(self):
        assert self.mapper.to_kraken_ws("BTC-USD") == "XBT/USD"
        assert self.mapper.to_kraken_ws("DOGE-USD") == "XDG/USD"

    def test_from_kraken_ws_dynamic(self):
        assert self.mapper.from_kraken_ws("XBT/USD") == "BTC-USD"
        assert self.mapper.from_kraken_ws("XDG/USD") == "DOGE-USD"

    def test_cross_pair(self):
        """ETH/XBT maps to ETH-BTC (not ETH-XBT)."""
        assert self.mapper.from_kraken_ws("ETH/XBT") == "ETH-BTC"

    def test_roundtrip_rest_dynamic(self):
        for symbol in ["BTC-USD", "ETH-USD", "SOL-USD", "DOGE-USD"]:
            rest = self.mapper.to_kraken_rest(symbol)
            back = self.mapper.from_kraken_rest(rest)
            assert back == symbol

    def test_skip_invalid_pairs(self):
        """Pairs without wsname or without slash are skipped."""
        mapper = KrakenSymbolMapper()
        count = mapper.load_from_asset_pairs({
            "VALID": {"wsname": "BTC/USD", "altname": "BTCUSD"},
            "NOWS": {"altname": "FOO"},
            "NOSLASH": {"wsname": "FOOBAR", "altname": "FB"},
        })
        assert count == 1


# ------------------------------------------------------------------
# Asset name normalization
# ------------------------------------------------------------------

class TestNormalizeAsset:
    def test_legacy_crypto_prefixed(self):
        assert KrakenSymbolMapper.normalize_asset("XXBT") == "BTC"
        assert KrakenSymbolMapper.normalize_asset("XETH") == "ETH"
        assert KrakenSymbolMapper.normalize_asset("XXDG") == "DOGE"
        assert KrakenSymbolMapper.normalize_asset("XXRP") == "XRP"

    def test_legacy_fiat_prefixed(self):
        assert KrakenSymbolMapper.normalize_asset("ZUSD") == "USD"
        assert KrakenSymbolMapper.normalize_asset("ZEUR") == "EUR"
        assert KrakenSymbolMapper.normalize_asset("ZGBP") == "GBP"

    def test_standard_names_passthrough(self):
        assert KrakenSymbolMapper.normalize_asset("SOL") == "SOL"
        assert KrakenSymbolMapper.normalize_asset("ADA") == "ADA"
        assert KrakenSymbolMapper.normalize_asset("DOT") == "DOT"
        assert KrakenSymbolMapper.normalize_asset("LINK") == "LINK"

    def test_short_unknown_4char(self):
        """4-char assets with X/Z prefix but not in legacy map get stripped."""
        assert KrakenSymbolMapper.normalize_asset("XLTC") == "LTC"

    def test_xbt_direct(self):
        """XBT (without prefix) maps to BTC."""
        assert KrakenSymbolMapper.normalize_asset("XBT") == "BTC"
