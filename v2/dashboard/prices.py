"""Kraken public REST price fetcher for live unrealized P&L.

Fetches last-traded prices from the Kraken public Ticker API (no auth required).
Uses the AssetPairs endpoint to build a reliable symbol mapping (cached 1 hour).
Degrades gracefully — returns empty dict on any API error.
"""

import logging

import requests
import streamlit as st

logger = logging.getLogger(__name__)

# Same special-case mappings as v2/utils/symbol_mapper.py
_KRAKEN_TO_ASSET = {"XBT": "BTC", "XDG": "DOGE"}

_BASE_URL = "https://api.kraken.com/0/public"


@st.cache_data(ttl=3600)
def _get_pair_mapping() -> dict[str, str]:
    """Build Kraken REST pair name -> internal symbol mapping.

    Queries the AssetPairs endpoint once and caches for 1 hour.
    Returns: {"XXBTZUSD": "BTC-USD", "XETHZUSD": "ETH-USD", ...}
    """
    try:
        resp = requests.get(f"{_BASE_URL}/AssetPairs", timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data.get("error"):
            logger.warning("Kraken AssetPairs error: %s", data["error"])
            return {}

        mapping = {}
        for rest_name, info in data.get("result", {}).items():
            ws_name = info.get("wsname", "")
            if not ws_name or "/" not in ws_name:
                continue
            ws_base, ws_quote = ws_name.split("/", 1)
            int_base = _KRAKEN_TO_ASSET.get(ws_base, ws_base)
            int_quote = _KRAKEN_TO_ASSET.get(ws_quote, ws_quote)
            mapping[rest_name] = f"{int_base}-{int_quote}"
        return mapping
    except Exception:
        logger.warning("Failed to fetch Kraken AssetPairs", exc_info=True)
        return {}


def fetch_live_prices(symbols: list[str]) -> dict[str, float]:
    """Fetch last-traded prices from Kraken public Ticker API.

    Args:
        symbols: Internal symbols, e.g. ["BTC-USD", "ETH-USD"]

    Returns:
        {internal_symbol: last_price} dict. Empty dict on any error.
    """
    if not symbols:
        return {}

    pair_mapping = _get_pair_mapping()  # rest_name -> internal
    if not pair_mapping:
        return {}

    # Reverse: internal -> rest_name
    internal_to_rest = {v: k for k, v in pair_mapping.items()}

    # Find Kraken pair names for our symbols
    pairs_to_fetch = []
    fetchable_symbols = set()
    for sym in symbols:
        rest_name = internal_to_rest.get(sym)
        if rest_name:
            pairs_to_fetch.append(rest_name)
            fetchable_symbols.add(sym)

    if not pairs_to_fetch:
        logger.warning(
            "No Kraken pair mapping found for symbols: %s", symbols
        )
        return {}

    try:
        resp = requests.get(
            f"{_BASE_URL}/Ticker",
            params={"pair": ",".join(pairs_to_fetch)},
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("error"):
            logger.warning("Kraken Ticker error: %s", data["error"])
            return {}

        prices = {}
        for rest_name, ticker in data.get("result", {}).items():
            internal = pair_mapping.get(rest_name)
            if internal and internal in fetchable_symbols:
                # "c" field = [last_trade_price, lot_volume]
                prices[internal] = float(ticker["c"][0])
        return prices
    except Exception:
        logger.warning("Failed to fetch Kraken ticker prices", exc_info=True)
        return {}
