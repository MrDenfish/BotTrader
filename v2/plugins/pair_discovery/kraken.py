"""Kraken pair discovery plugin.

Fetches all tradeable pairs from the Kraken public API, applies
v1-style two-pass volume filtering (average 24h USD quote volume),
and publishes SymbolsUpdatedEvent when the active pair set changes.

Uses only public endpoints — no API key needed.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

from v2.core import registry
from v2.core.event_bus import EventBus
from v2.core.interfaces import PairDiscovery
from v2.core.types import SymbolsUpdatedEvent
from v2.utils.symbol_mapper import KrakenSymbolMapper

logger = logging.getLogger(__name__)

_API_URL = "https://api.kraken.com"
_ASSET_PAIRS_PATH = "/0/public/AssetPairs"
_TICKER_PATH = "/0/public/Ticker"


@registry.plugin("pair_discovery", "kraken")
class KrakenPairDiscovery(PairDiscovery):
    """Discovers trading pairs from Kraken filtered by 24h volume.

    Two-pass algorithm (matching v1 / Coinbase pattern):
      Pass 1 — Calculate average 24h USD quote volume across all USD spot pairs.
      Pass 2 — Keep pairs with volume >= max(average, min_quote_volume),
               excluding shill coins.

    Seed symbols are always included regardless of volume.

    Also builds a :class:`KrakenSymbolMapper` from the AssetPairs response
    so other Kraken plugins can translate symbols.
    """

    name = "kraken"

    def __init__(
        self,
        event_bus: EventBus | None = None,
        key_file: str | None = None,
        **kwargs: Any,
    ) -> None:
        self._bus = event_bus
        self._key_file = key_file

        self._session: aiohttp.ClientSession | None = None
        self._refresh_task: asyncio.Task | None = None
        self._current_symbols: list[str] = []

        # Symbol mapper — populated from AssetPairs response
        self.mapper = KrakenSymbolMapper()

        # Defaults — overridden by configure()
        self._refresh_interval_minutes: int = 30
        self._shill_coins: set[str] = set()
        self._seed_symbols: list[str] = []
        self._max_pairs: int = 100
        self._min_quote_volume: float = 0.0
        self._configured = False

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
        """Fetch pairs + ticker volumes and apply two-pass volume filter.

        Returns list of symbol strings in internal format
        (e.g. ``["BTC-USD", "ETH-USD", ...]``).
        """
        pairs = await self._fetch_asset_pairs()
        if not pairs:
            return []

        # Build symbol mapper from pairs response
        self.mapper.load_from_asset_pairs(pairs)

        # Collect USD spot pairs
        usd_pairs = self._collect_usd_pairs(pairs)
        if not usd_pairs:
            logger.warning("No USD pairs found on Kraken")
            return list(self._seed_symbols)

        # Fetch 24h volumes for all USD pairs
        volumes = await self._fetch_volumes(usd_pairs)

        # Apply two-pass filter
        symbols = self._filter_by_volume(usd_pairs, volumes)
        self._current_symbols = symbols
        return symbols

    async def start(self) -> None:
        """Start the periodic refresh background task."""
        self._refresh_task = asyncio.create_task(self._refresh_loop())
        logger.info(
            "Kraken pair discovery started (refresh every %d min, %d seed symbols, %d shill coins)",
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
        logger.info("Kraken pair discovery stopped")

    # ------------------------------------------------------------------
    # USD pair collection
    # ------------------------------------------------------------------

    def _collect_usd_pairs(self, pairs: dict) -> dict[str, str]:
        """Return {rest_pair_name: internal_symbol} for all USD spot pairs."""
        usd_pairs: dict[str, str] = {}
        for rest_name, info in pairs.items():
            ws_name = info.get("wsname", "")
            if not ws_name or "/" not in ws_name:
                continue
            # Only USD quote currency
            _, quote = ws_name.split("/", 1)
            if quote != "USD":
                continue
            internal = self.mapper.from_kraken_ws(ws_name)
            usd_pairs[rest_name] = internal
        return usd_pairs

    # ------------------------------------------------------------------
    # Two-pass volume filter
    # ------------------------------------------------------------------

    def _filter_by_volume(
        self,
        usd_pairs: dict[str, str],
        volumes: dict[str, float],
    ) -> list[str]:
        """Apply v1-style two-pass volume filter.

        Pass 1: Calculate average 24h USD quote volume.
        Pass 2: Keep pairs above threshold, exclude shill coins.
        """
        # Pass 1: collect valid volumes
        valid_volumes: list[float] = []
        for rest_name in usd_pairs:
            vol = volumes.get(rest_name, 0.0)
            if vol > 0:
                valid_volumes.append(vol)

        if not valid_volumes:
            logger.warning("Kraken pair discovery: no valid USD volumes found")
            return list(self._seed_symbols)

        avg_volume = sum(valid_volumes) / len(valid_volumes)
        volume_threshold = max(avg_volume, self._min_quote_volume)
        logger.info(
            "Kraken pair discovery: %d USD pairs, avg 24h volume $%.0f, threshold $%.0f",
            len(valid_volumes), avg_volume, volume_threshold,
        )

        # Pass 2: filter
        filtered: list[tuple[str, float]] = []
        for rest_name, internal in usd_pairs.items():
            base = internal.split("-")[0].upper()
            if base in self._shill_coins:
                continue

            vol = volumes.get(rest_name, 0.0)
            if vol >= volume_threshold:
                filtered.append((internal, vol))

        # Sort by volume descending
        filtered.sort(key=lambda x: x[1], reverse=True)

        # Cap at max_pairs
        symbols = [s for s, _ in filtered[:self._max_pairs]]

        # Union with seed symbols
        symbol_set = set(symbols)
        for seed in self._seed_symbols:
            if seed not in symbol_set:
                symbols.append(seed)

        return symbols

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
                    logger.warning("Kraken pair discovery refresh returned empty — keeping current symbols")
                    continue

                old_set = set(self._current_symbols)
                new_set = set(new_symbols)

                if old_set == new_set:
                    logger.debug("Kraken pair discovery refresh: no changes (%d symbols)", len(new_symbols))
                    continue

                added = sorted(new_set - old_set)
                removed = sorted(old_set - new_set)
                self._current_symbols = new_symbols

                logger.info(
                    "Kraken pair discovery: %d symbols (added=%s, removed=%s)",
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
                logger.exception("Kraken pair discovery refresh failed — keeping current symbols")

    # ------------------------------------------------------------------
    # REST client (public, no auth needed)
    # ------------------------------------------------------------------

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def _fetch_asset_pairs(self) -> dict:
        """Fetch all tradeable pairs from ``/0/public/AssetPairs``."""
        session = await self._ensure_session()
        try:
            async with session.get(
                f"{_API_URL}{_ASSET_PAIRS_PATH}",
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    errors = data.get("error", [])
                    if errors:
                        logger.error("Kraken AssetPairs error: %s", errors)
                        return {}
                    result = data.get("result", {})
                    logger.info("Kraken pair discovery: fetched %d pairs", len(result))
                    return result
                text = await resp.text()
                logger.error("Kraken AssetPairs HTTP %d: %s", resp.status, text[:200])
                return {}
        except asyncio.TimeoutError:
            logger.error("Kraken AssetPairs request timed out")
            return {}
        except aiohttp.ClientError as e:
            logger.error("Kraken AssetPairs network error: %s", e)
            return {}

    async def _fetch_volumes(self, usd_pairs: dict[str, str]) -> dict[str, float]:
        """Fetch 24h volumes for a set of pairs via ``/0/public/Ticker``.

        Returns ``{rest_pair_name: 24h_usd_volume}``.
        Volume is approximated as ``volume * vwap`` (volume in base * avg price).
        """
        session = await self._ensure_session()

        # Kraken accepts comma-separated pair names
        pair_names = list(usd_pairs.keys())
        volumes: dict[str, float] = {}

        # Batch in groups of 30 to avoid URL length limits
        batch_size = 30
        for i in range(0, len(pair_names), batch_size):
            batch = pair_names[i:i + batch_size]
            pair_str = ",".join(batch)

            try:
                async with session.get(
                    f"{_API_URL}{_TICKER_PATH}",
                    params={"pair": pair_str},
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status != 200:
                        logger.warning("Kraken Ticker HTTP %d for batch %d", resp.status, i)
                        continue

                    data = await resp.json()
                    errors = data.get("error", [])
                    if errors:
                        logger.warning("Kraken Ticker error: %s", errors)
                        continue

                    result = data.get("result", {})
                    for pair_name, ticker in result.items():
                        try:
                            # v = [today_vol, 24h_vol], p = [today_vwap, 24h_vwap]
                            vol_24h = float(ticker["v"][1])   # 24h volume in base
                            vwap_24h = float(ticker["p"][1])  # 24h VWAP
                            usd_volume = vol_24h * vwap_24h
                            volumes[pair_name] = usd_volume
                        except (KeyError, IndexError, ValueError, TypeError):
                            continue

            except asyncio.TimeoutError:
                logger.warning("Kraken Ticker timeout for batch %d", i)
            except aiohttp.ClientError as e:
                logger.warning("Kraken Ticker network error: %s", e)

            # Rate limiting between batches
            if i + batch_size < len(pair_names):
                await asyncio.sleep(0.5)

        logger.info("Kraken volumes fetched for %d/%d pairs", len(volumes), len(pair_names))
        return volumes
