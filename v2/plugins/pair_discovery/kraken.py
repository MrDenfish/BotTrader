"""Kraken pair discovery plugin.

Fetches all tradeable pairs from the Kraken public API, applies
v1-style two-pass volume filtering (average 24h USD quote volume),
and publishes SymbolsUpdatedEvent when the active pair set changes.

Uses only public endpoints — no API key needed.
"""

from __future__ import annotations

import asyncio
import logging
import time
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
_OHLC_PATH = "/0/public/OHLC"


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
        self._max_spread_bps: float = 0.0  # 0 = disabled
        self._min_listing_age_days: int = 0  # 0 = disabled
        # Listing dates never change — cache for the process lifetime
        self._listing_ts_cache: dict[str, float] = {}
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
        self._max_spread_bps = float(config.get("max_spread_bps", 0))
        self._min_listing_age_days = int(config.get("min_listing_age_days", 0))
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

        # Fetch 24h volumes and bid-ask spreads for all USD pairs
        volumes, spreads = await self._fetch_volumes(usd_pairs)

        # Apply two-pass filter (volume + spread)
        symbols = self._filter_by_volume(usd_pairs, volumes, spreads)

        # Listing-age gate — drop recently listed pairs
        if self._min_listing_age_days > 0:
            symbols = await self._filter_by_listing_age(symbols)

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
        spreads: dict[str, float] | None = None,
    ) -> list[str]:
        """Apply v1-style two-pass volume filter with optional spread gate.

        Pass 1: Calculate average 24h USD quote volume.
        Pass 2: Keep pairs above threshold, exclude shill coins and
                 pairs with bid-ask spread exceeding ``max_spread_bps``.
        """
        if spreads is None:
            spreads = {}

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

        # Pass 2: filter by volume, spread, and shill coins
        filtered: list[tuple[str, float]] = []
        spread_rejected = 0
        for rest_name, internal in usd_pairs.items():
            base = internal.split("-")[0].upper()
            if base in self._shill_coins:
                continue

            vol = volumes.get(rest_name, 0.0)
            if vol < volume_threshold:
                continue

            # Spread gate: reject pairs with wide bid-ask spread
            if self._max_spread_bps > 0:
                spread = spreads.get(rest_name, 0.0)
                if spread > self._max_spread_bps:
                    spread_rejected += 1
                    logger.debug(
                        "Spread gate: %s rejected (%.0f bps > %.0f bps)",
                        internal, spread, self._max_spread_bps,
                    )
                    continue

            filtered.append((internal, vol))

        if spread_rejected:
            logger.info(
                "Spread gate: %d pairs rejected (> %.0f bps)",
                spread_rejected, self._max_spread_bps,
            )

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
    # Listing-age gate
    # ------------------------------------------------------------------

    async def _filter_by_listing_age(
        self, symbols: list[str], now: float | None = None,
    ) -> list[str]:
        """Drop pairs listed on Kraken less than ``min_listing_age_days`` ago.

        Newly listed pairs trade on thin, market-maker-driven books that
        gap through stop levels; the gate keeps the universe to seasoned
        pairs. Seed symbols bypass the gate. Pairs whose listing date
        cannot be determined are kept (graceful degradation).
        """
        if now is None:
            now = time.time()
        min_age_secs = self._min_listing_age_days * 86_400
        seeds = set(self._seed_symbols)

        kept: list[str] = []
        rejected: list[str] = []
        for symbol in symbols:
            if symbol in seeds:
                kept.append(symbol)
                continue

            ts = self._listing_ts_cache.get(symbol)
            if ts is None:
                rest_name = self.mapper.to_kraken_rest(symbol)
                ts = await self._fetch_first_bar_ts(rest_name)
                if ts is not None:
                    self._listing_ts_cache[symbol] = ts

            if ts is None:
                logger.warning(
                    "Listing-age gate: no listing date for %s — keeping", symbol,
                )
                kept.append(symbol)
            elif now - ts >= min_age_secs:
                kept.append(symbol)
            else:
                rejected.append(symbol)

        if rejected:
            logger.info(
                "Listing-age gate: %d pairs rejected (< %d days): %s",
                len(rejected), self._min_listing_age_days, rejected,
            )
        return kept

    async def _fetch_first_bar_ts(self, rest_name: str) -> float | None:
        """First weekly OHLC bar timestamp — approximates the listing date.

        The OHLC endpoint caps at 720 bars, so pairs older than ~13.8
        years report a later date; irrelevant for any sane age gate.
        """
        session = await self._ensure_session()
        try:
            async with session.get(
                f"{_API_URL}{_OHLC_PATH}",
                params={"pair": rest_name, "interval": 10080},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    logger.warning(
                        "Kraken OHLC HTTP %d for %s", resp.status, rest_name,
                    )
                    return None
                data = await resp.json()
                if data.get("error"):
                    logger.warning(
                        "Kraken OHLC error for %s: %s", rest_name, data["error"],
                    )
                    return None
                for key, bars in data.get("result", {}).items():
                    if key != "last" and bars:
                        return float(bars[0][0])
                return None
        except asyncio.TimeoutError:
            logger.warning("Kraken OHLC timeout for %s", rest_name)
            return None
        except aiohttp.ClientError as e:
            logger.warning("Kraken OHLC network error for %s: %s", rest_name, e)
            return None
        finally:
            # Public-endpoint rate limiting between per-pair lookups
            await asyncio.sleep(0.25)

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

                added = sorted(new_set - old_set)
                removed = sorted(old_set - new_set)

                if old_set == new_set:
                    # Still publish so data providers can merge position symbols
                    # (zombie position prevention). The WS handler deduplicates
                    # and won't reconnect if the final symbol set is unchanged.
                    if self._bus:
                        self._bus.publish(SymbolsUpdatedEvent(
                            symbols=tuple(new_symbols),
                            added=(),
                            removed=(),
                        ))
                    continue

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

    async def _fetch_volumes(
        self, usd_pairs: dict[str, str],
    ) -> tuple[dict[str, float], dict[str, float]]:
        """Fetch 24h volumes and bid-ask spreads via ``/0/public/Ticker``.

        Returns ``(volumes, spreads)`` where:
        - ``volumes``: ``{rest_pair_name: 24h_usd_volume}``
        - ``spreads``: ``{rest_pair_name: spread_in_bps}``

        Volume is approximated as ``volume * vwap`` (volume in base * avg price).
        Spread is ``(ask - bid) / midpoint * 10000`` in basis points.
        """
        session = await self._ensure_session()

        # Kraken accepts comma-separated pair names
        pair_names = list(usd_pairs.keys())
        volumes: dict[str, float] = {}
        spreads: dict[str, float] = {}

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

                            # a = [price, ...], b = [price, ...]
                            ask = float(ticker["a"][0])
                            bid = float(ticker["b"][0])
                            mid = (ask + bid) / 2
                            if mid > 0:
                                spreads[pair_name] = (ask - bid) / mid * 10_000
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
        return volumes, spreads
