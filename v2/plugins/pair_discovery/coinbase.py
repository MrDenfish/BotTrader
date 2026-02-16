"""Coinbase pair discovery plugin.

Fetches all products from the Coinbase Advanced Trade API, applies
v1-style two-pass volume filtering (average 24h USD quote volume),
and publishes SymbolsUpdatedEvent when the active pair set changes.

Ported from MarketDataManager/ticker_manager.py filter_volume_for_market_data().
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

import aiohttp

from v2.core import registry
from v2.core.event_bus import EventBus
from v2.core.interfaces import PairDiscovery
from v2.core.types import SymbolsUpdatedEvent

logger = logging.getLogger(__name__)

_API_URL = "https://api.coinbase.com"
_PRODUCTS_PATH = "/api/v3/brokerage/products"


@registry.plugin("pair_discovery", "coinbase")
class CoinbasePairDiscovery(PairDiscovery):
    """Discovers trading pairs from Coinbase filtered by 24h volume.

    Two-pass algorithm (matching v1):
      Pass 1 — Calculate average 24h USD quote volume across all USD SPOT pairs.
      Pass 2 — Keep pairs with volume >= average, excluding shill coins.

    Seed symbols are always included regardless of volume.
    """

    name = "coinbase"

    def __init__(
        self,
        event_bus: EventBus | None = None,
        api_key: str | None = None,
        api_secret: str | None = None,
        api_key_env: str = "COINBASE_API_KEY",
        api_secret_env: str = "COINBASE_API_SECRET",
        key_file: str | None = None,
        **kwargs: Any,
    ) -> None:
        self._bus = event_bus
        self._api_key = api_key or os.environ.get(api_key_env, "")
        self._api_secret = api_secret or os.environ.get(api_secret_env, "")

        if not self._api_key and not self._api_secret and key_file:
            self._load_key_file(key_file)

        self._session: aiohttp.ClientSession | None = None
        self._refresh_task: asyncio.Task | None = None
        self._current_symbols: list[str] = []

        # Defaults — overridden by configure()
        self._refresh_interval_minutes: int = 30
        self._shill_coins: set[str] = set()
        self._seed_symbols: list[str] = []
        self._max_pairs: int = 100
        self._min_quote_volume: float = 0.0  # hard floor for 24h USD volume
        self._configured = False

    # ------------------------------------------------------------------
    # Credential loading (same pattern as WebSocket provider)
    # ------------------------------------------------------------------

    def _load_key_file(self, path: str) -> None:
        """Load API credentials from a Coinbase CDP JSON key file."""
        try:
            with open(path) as f:
                data = json.load(f)
            self._api_key = data.get("name", "")
            self._api_secret = data.get("signing_key", "") or data.get("privateKey", "")
            logger.info("PairDiscovery: loaded credentials from %s", path)
        except (FileNotFoundError, json.JSONDecodeError, KeyError) as e:
            logger.warning("PairDiscovery: failed to load key file %s: %s", path, e)

    # ------------------------------------------------------------------
    # PairDiscovery ABC
    # ------------------------------------------------------------------

    def configure(self, config: Any) -> None:
        if not isinstance(config, dict):
            return
        self._refresh_interval_minutes = config.get("refresh_interval_minutes", 30)
        self._shill_coins = {
            s.upper() for s in config.get("shill_coins", [])
        }
        self._seed_symbols = list(config.get("seed_symbols", []))
        self._max_pairs = config.get("max_pairs", 100)
        self._min_quote_volume = float(config.get("min_quote_volume", 0))
        self._configured = True

    async def discover(self) -> list[str]:
        """Fetch products and apply two-pass volume filter.

        Returns list of symbol strings (e.g. ``["BTC-USD", "ETH-USD", ...]``).
        Returns empty list on API failure.
        """
        products = await self._fetch_products()
        if not products:
            return []

        symbols = self._filter_products(products)
        self._current_symbols = symbols
        return symbols

    async def start(self) -> None:
        """Start the periodic refresh background task."""
        self._refresh_task = asyncio.create_task(self._refresh_loop())
        logger.info(
            "Pair discovery started (refresh every %d min, %d seed symbols, %d shill coins)",
            self._refresh_interval_minutes,
            len(self._seed_symbols),
            len(self._shill_coins),
        )

    async def stop(self) -> None:
        """Cancel refresh task and close HTTP session."""
        if self._refresh_task:
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass
        if self._session and not self._session.closed:
            await self._session.close()
        logger.info("Pair discovery stopped")

    # ------------------------------------------------------------------
    # Two-pass volume filter (ported from v1 ticker_manager.py)
    # ------------------------------------------------------------------

    def _filter_products(self, products: list[dict]) -> list[str]:
        """Apply v1-style two-pass volume filter.

        Pass 1: Calculate average 24h USD quote volume.
        Pass 2: Keep SPOT+USD+online pairs above average, exclude shill coins.
        """
        # Pass 1: collect valid USD quote volumes
        valid_volumes: list[float] = []
        for item in products:
            if item.get("quote_currency_id") != "USD":
                continue
            vol = self._safe_float(item.get("approximate_quote_24h_volume"))
            if vol > 0:
                valid_volumes.append(vol)

        if not valid_volumes:
            logger.warning("Pair discovery: no valid USD quote volumes found")
            return list(self._seed_symbols)

        avg_volume = sum(valid_volumes) / len(valid_volumes)
        # Use the higher of avg_volume and min_quote_volume as the threshold
        volume_threshold = max(avg_volume, self._min_quote_volume)
        logger.info(
            "Pair discovery: %d USD pairs, avg 24h volume $%.0f, threshold $%.0f",
            len(valid_volumes), avg_volume, volume_threshold,
        )

        # Pass 2: filter by criteria
        filtered: list[tuple[str, float]] = []
        for item in products:
            if (
                item.get("quote_currency_id") != "USD"
                or item.get("product_type") != "SPOT"
                or item.get("status") != "online"
            ):
                continue

            base = item.get("base_currency_id", "")
            if base.upper() in self._shill_coins:
                continue

            vol = self._safe_float(item.get("approximate_quote_24h_volume"))
            if vol >= volume_threshold:
                symbol = item.get("product_id", "")
                if symbol:
                    filtered.append((symbol, vol))

        # Sort by volume descending
        filtered.sort(key=lambda x: x[1], reverse=True)

        # Cap at max_pairs
        symbols = [s for s, _ in filtered[: self._max_pairs]]

        # Union with seed symbols (always included)
        symbol_set = set(symbols)
        for seed in self._seed_symbols:
            if seed not in symbol_set:
                symbols.append(seed)

        return symbols

    @staticmethod
    def _safe_float(value: Any) -> float:
        """Convert to float, returning 0.0 on failure."""
        if value is None:
            return 0.0
        try:
            return float(value)
        except (ValueError, TypeError):
            return 0.0

    # ------------------------------------------------------------------
    # Periodic refresh
    # ------------------------------------------------------------------

    async def _refresh_loop(self) -> None:
        """Periodically re-discover pairs and publish changes."""
        while True:
            try:
                await asyncio.sleep(self._refresh_interval_minutes * 60)

                new_symbols = await self.discover()
                if not new_symbols:
                    logger.warning("Pair discovery refresh returned empty — keeping current symbols")
                    continue

                old_set = set(self._current_symbols)
                new_set = set(new_symbols)

                if old_set == new_set:
                    logger.debug("Pair discovery refresh: no changes (%d symbols)", len(new_symbols))
                    continue

                added = sorted(new_set - old_set)
                removed = sorted(old_set - new_set)
                self._current_symbols = new_symbols

                logger.info(
                    "Pair discovery: %d symbols (added=%s, removed=%s)",
                    len(new_symbols), added, removed,
                )

                if self._bus:
                    self._bus.publish(SymbolsUpdatedEvent(
                        symbols=tuple(new_symbols),
                        added=tuple(added),
                        removed=tuple(removed),
                    ))

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Pair discovery refresh failed — keeping current symbols")

    # ------------------------------------------------------------------
    # REST client
    # ------------------------------------------------------------------

    async def _fetch_products(self) -> list[dict]:
        """Fetch all products from Coinbase Advanced Trade REST API."""
        from coinbase import jwt_generator

        if not self._api_key or not self._api_secret:
            logger.error("Pair discovery: no API credentials configured")
            return []

        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()

        try:
            jwt_uri = jwt_generator.format_jwt_uri("GET", _PRODUCTS_PATH)
            token = jwt_generator.build_rest_jwt(jwt_uri, self._api_key, self._api_secret)

            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            }

            async with self._session.get(
                f"{_API_URL}{_PRODUCTS_PATH}",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    products = data.get("products", [])
                    logger.info("Pair discovery: fetched %d products from Coinbase", len(products))
                    return products
                text = await resp.text()
                logger.error("Pair discovery: HTTP %d — %s", resp.status, text[:200])
                return []

        except asyncio.TimeoutError:
            logger.error("Pair discovery: request timed out")
            return []
        except aiohttp.ClientError as e:
            logger.error("Pair discovery: network error — %s", e)
            return []
        except Exception:
            logger.exception("Pair discovery: unexpected error fetching products")
            return []
