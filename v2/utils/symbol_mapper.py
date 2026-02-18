"""Bidirectional symbol translation between v2 internal format and Kraken formats.

Kraken uses three different symbol representations:
- REST API:   ``XBTUSD`` or ``XXBTZUSD`` (legacy prefixes)
- WebSocket v2: ``BTC/USD`` (slash-separated)
- Balance keys:  ``XXBT``, ``ZUSD`` (legacy-prefixed asset names)

Internal v2 format is ``BTC-USD`` (dash-separated, standard ticker names).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Special cases where Kraken uses a non-standard ticker.
# Maps internal asset name -> Kraken asset name.
_ASSET_TO_KRAKEN = {
    "BTC": "XBT",
    "DOGE": "XDG",
}
_KRAKEN_TO_ASSET = {v: k for k, v in _ASSET_TO_KRAKEN.items()}

# Legacy prefixed asset names used in balance endpoints.
# X prefix = crypto, Z prefix = fiat.
_LEGACY_ASSET_MAP: dict[str, str] = {
    "XXBT": "BTC",
    "XETH": "ETH",
    "XLTC": "LTC",
    "XXRP": "XRP",
    "XXLM": "XLM",
    "XXDG": "DOGE",
    "XZEC": "ZEC",
    "XETC": "ETC",
    "XREP": "REP",
    "XXMR": "XMR",
    "ZUSD": "USD",
    "ZEUR": "EUR",
    "ZGBP": "GBP",
    "ZCAD": "CAD",
    "ZJPY": "JPY",
    "ZAUD": "AUD",
}


class KrakenSymbolMapper:
    """Bidirectional symbol mapper for Kraken.

    Can be populated dynamically from the ``/0/public/AssetPairs`` response
    via :meth:`load_from_asset_pairs`, or used with the static hardcoded
    mappings for common pairs.
    """

    def __init__(self) -> None:
        # Dynamic lookup tables built from AssetPairs API response.
        # internal (BTC-USD) -> REST pair name (XBTUSD)
        self._to_rest: dict[str, str] = {}
        # REST pair name -> internal
        self._from_rest: dict[str, str] = {}
        # internal -> WS v2 name (BTC/USD)
        self._to_ws: dict[str, str] = {}
        # WS v2 name -> internal
        self._from_ws: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Dynamic loading from AssetPairs API
    # ------------------------------------------------------------------

    def load_from_asset_pairs(self, pairs: dict[str, Any]) -> int:
        """Populate lookup tables from a Kraken ``AssetPairs`` response.

        *pairs* is the ``"result"`` dict from ``GET /0/public/AssetPairs``.
        Each value has ``wsname``, ``altname``, ``base``, ``quote`` fields.

        Returns the number of pairs loaded.
        """
        count = 0
        for rest_name, info in pairs.items():
            ws_name = info.get("wsname", "")  # e.g. "XBT/USD"
            if not ws_name or "/" not in ws_name:
                continue

            # Parse WS name into base/quote
            ws_base, ws_quote = ws_name.split("/", 1)

            # Normalize to internal format: map XBT->BTC, XDG->DOGE
            int_base = _KRAKEN_TO_ASSET.get(ws_base, ws_base)
            int_quote = _KRAKEN_TO_ASSET.get(ws_quote, ws_quote)
            internal = f"{int_base}-{int_quote}"

            self._to_rest[internal] = rest_name
            self._from_rest[rest_name] = internal
            self._to_ws[internal] = ws_name
            self._from_ws[ws_name] = internal

            # Also map altname (e.g. "XBTUSD") -> internal
            altname = info.get("altname", "")
            if altname and altname != rest_name:
                self._from_rest[altname] = internal

            count += 1

        logger.info("Symbol mapper loaded %d pairs from AssetPairs", count)
        return count

    # ------------------------------------------------------------------
    # Conversion methods
    # ------------------------------------------------------------------

    def to_kraken_ws(self, symbol: str) -> str:
        """Internal (``BTC-USD``) -> Kraken WS v2 (``BTC/USD``).

        Falls back to simple dash->slash replacement with XBT/XDG mapping.
        """
        if symbol in self._to_ws:
            return self._to_ws[symbol]
        # Fallback: split on dash, apply special cases
        parts = symbol.split("-", 1)
        if len(parts) != 2:
            return symbol
        base, quote = parts
        base = _ASSET_TO_KRAKEN.get(base, base)
        quote = _ASSET_TO_KRAKEN.get(quote, quote)
        return f"{base}/{quote}"

    def to_kraken_rest(self, symbol: str) -> str:
        """Internal (``BTC-USD``) -> Kraken REST pair name (``XBTUSD``).

        Falls back to concatenating with XBT/XDG mapping.
        """
        if symbol in self._to_rest:
            return self._to_rest[symbol]
        # Fallback: concatenate base + quote with special cases
        parts = symbol.split("-", 1)
        if len(parts) != 2:
            return symbol
        base, quote = parts
        base = _ASSET_TO_KRAKEN.get(base, base)
        quote = _ASSET_TO_KRAKEN.get(quote, quote)
        return f"{base}{quote}"

    def from_kraken_ws(self, symbol: str) -> str:
        """Kraken WS v2 (``BTC/USD``) -> Internal (``BTC-USD``)."""
        if symbol in self._from_ws:
            return self._from_ws[symbol]
        # Fallback: split on slash, reverse special cases
        parts = symbol.split("/", 1)
        if len(parts) != 2:
            return symbol
        base, quote = parts
        base = _KRAKEN_TO_ASSET.get(base, base)
        quote = _KRAKEN_TO_ASSET.get(quote, quote)
        return f"{base}-{quote}"

    def from_kraken_rest(self, symbol: str) -> str:
        """Kraken REST pair name -> Internal (``BTC-USD``)."""
        if symbol in self._from_rest:
            return self._from_rest[symbol]
        return symbol

    @staticmethod
    def normalize_asset(asset: str) -> str:
        """Normalize a Kraken balance key to standard asset name.

        E.g. ``XXBT`` -> ``BTC``, ``ZUSD`` -> ``USD``, ``SOL`` -> ``SOL``.
        """
        # Check explicit legacy map first
        if asset in _LEGACY_ASSET_MAP:
            return _LEGACY_ASSET_MAP[asset]
        # Try stripping X/Z prefix for 4-char assets
        if len(asset) == 4 and asset[0] in ("X", "Z"):
            stripped = asset[1:]
            # Reverse XBT->BTC, XDG->DOGE
            return _KRAKEN_TO_ASSET.get(stripped, stripped)
        # Already standard (e.g. "SOL", "DOT", "ADA")
        return _KRAKEN_TO_ASSET.get(asset, asset)
