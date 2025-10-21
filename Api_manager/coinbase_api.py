

import asyncio
import logging
import random
import aiohttp
import re, os
import pandas as pd
import hmac, hashlib, base64, time

from typing import Optional, List
from coinbase import jwt_generator
from Shared_Utils.enum import ValidationCode
from Shared_Utils.alert_system import AlertSystem
from datetime import datetime, timedelta, timezone
from Shared_Utils.logging_manager import LoggerManager
from Config.config_manager import CentralConfig as Config


class CoinbaseAPI:
    """This class is for REST API code and should not be confused with the websocket code used in WebsocketHelper."""

    # ✅ Global OHLCV semaphore & counters (shared across all instances)
    _ohlcv_semaphore = asyncio.Semaphore(5)  # limit concurrent OHLCV calls
    _active_ohlcv_tasks = 0  # tracking active tasks

    def __init__(self, session, shared_utils_utility, logger_manager, shared_utils_precision):
        self.session: aiohttp.ClientSession | None = None
        self.session = session
        self.config = Config()
        self._reload_credentials_from_config()
        self._valid_products_cache: set[str] = set()
        self._valid_cache_expiry: datetime | None = None
        self._blocklist_bases: set[str] = self._load_blocklist_bases_from_env()

        log_config = {"log_level": logging.INFO}
        self.webhook_logger = LoggerManager(log_config)
        self.logger = logger_manager.loggers['shared_logger']

        self.logger.info("🔹 CoinBaseAPI initialized debug.")

        self.default_maker_fee = self.config.maker_fee
        self.default_taker_fee = self.config.taker_fee


        self.alerts = AlertSystem(logger_manager)
        self.shared_utils_precision = shared_utils_precision
        self.shared_utils_utility = shared_utils_utility


        self.api_algo = self.config.load_websocket_api_key().get('algorithm')

        self.empty_ohlcv_cache = set()
        self.last_cache_clear_time = datetime.now(timezone.utc)

        self.jwt_token = None
        self.jwt_expiry = None

    def _load_blocklist_bases_from_env(self) -> set[str]:
        """
        Read SHILL_COINS from .env and return a set of BASE tickers (uppercased).
        Example: SHILL_COINS=UNFI,TRUMP,MATIC
        """
        raw = os.getenv("SHILL_COINS", "")
        if not raw:
            return set()
        tokens = [t.strip().upper() for t in raw.replace(";", ",").replace("|", ",").split(",") if t.strip()]
        return set(tokens)

    def _reload_credentials_from_config(self):
        """Refresh API creds and base URLs from Config (idempotent)."""
        w = self.config.load_websocket_api_key() or {}
        self.api_key = w.get('name') or None
        self.api_secret = w.get('signing_key') or None
        self.user_url = w.get('user_api_url') or None
        self.market_url = w.get('market_api_url') or None
        self.base_url = w.get('base_url') or None
        self.rest_url = w.get('rest_api_url') or None
        try:
            klen = len(self.api_key or "")
            self.logger.debug(f"🔐 Coinbase creds refreshed (key_len={klen}, rest_url={self.rest_url})")
        except Exception:
            pass

    def _ensure_creds(self):
        """Make sure creds/urls exist before signing; reload once if missing."""
        if not self.api_key or not self.api_secret or not self.rest_url:
            self.logger.warning("⚠️ Missing Coinbase creds/urls at call time — reloading.")
            self._reload_credentials_from_config()

    # def _get_auth_headers(self, method: str, request_path: str, body: str = "") -> dict:
    #     timestamp = str(time.time())
    #     message = f'{timestamp}{method}{request_path}{body}'
    #     hmac_key = base64.b64decode(self.api_secret)
    #     signature = hmac.new(hmac_key, message.encode(), hashlib.sha256)
    #     signature_b64 = base64.b64encode(signature.digest()).decode()
    #
    #     return {
    #         "CB-ACCESS-KEY": self.api_key,
    #         "CB-ACCESS-SIGN": signature_b64,
    #         "CB-ACCESS-TIMESTAMP": timestamp,
    #         "CB-ACCESS-PASSPHRASE": self.passphrase,
    #         "Content-Type": "application/json"
    #     }

    def _normalize_symbol(self, product_id: str) -> str:
        """Apply local alias mapping, e.g. 'MATIC-USD' -> 'POL-USD'."""
        return self._symbol_alias_map.get(product_id, product_id)

    async def _filter_valid_product_ids(self, product_ids: list[str]) -> list[str]:
        """
        Drop blocklisted bases and keep only product_ids present in Coinbase /products.
        """
        valid = await self._refresh_valid_products()
        out, dropped = [], []

        for pid in product_ids or []:
            base = (pid or "").split("-")[0].upper()
            if base in self._blocklist_bases:
                dropped.append(f"{pid} (blocked:{base})")
                continue
            if pid in valid:
                out.append(pid)
            else:
                dropped.append(pid)

        if dropped:
            self.logger.info(f"🧹 Dropping unsupported/blocked symbols: {dropped}")
        return out

    async def _refresh_valid_products(self, *, force: bool = False) -> set[str]:
        try:
            now = datetime.now(timezone.utc)
            if not force and self._valid_products_cache and self._valid_cache_expiry and now < self._valid_cache_expiry:
                return self._valid_products_cache
            products = await self.fetch_all_products()
            valid = {p.get("product_id") for p in products if p.get("product_id")}
            self._valid_products_cache = valid
            self._valid_cache_expiry = now + timedelta(minutes=10)
            return valid
        except Exception as e:
            self.logger.warning(f"Could not refresh valid products: {e}", exc_info=True)
            return self._valid_products_cache or set()

    def clear_ohlcv_cache_if_stale(self):
        now = datetime.now(timezone.utc)
        if now - self.last_cache_clear_time >= timedelta(hours=1):
            self.empty_ohlcv_cache.clear()
            self.last_cache_clear_time = now
            self.logger.debug("🧹 Cleared empty OHLCV cache after 1 hour.")

    def generate_rest_jwt(self, method='GET', request_path='/api/v3/brokerage/orders'):
        try:
            # NEW guard to avoid early-empty creds
            self._ensure_creds()
            jwt_uri = jwt_generator.format_jwt_uri(method, request_path)
            jwt_token = jwt_generator.build_rest_jwt(jwt_uri, self.api_key, self.api_secret)

            if not jwt_token:
                raise ValueError("JWT token is empty!")

            self.jwt_token = jwt_token
            self.jwt_expiry = datetime.now(timezone.utc) + timedelta(minutes=5)
            return jwt_token
        except Exception as e:
            self.logger.error(f"JWT Generation Failed: {e}", exc_info=True)
            return None

    def refresh_jwt_if_needed(self):
        """Refresh JWT only if it is close to expiration."""
        if not self.jwt_token or datetime.now(timezone.utc) >= self.jwt_expiry - timedelta(seconds=60):
            self.logger.info("Refreshing JWT token...")
            self.jwt_token = self.generate_rest_jwt()  # ✅ Only refresh if expired

    async def create_order(self, payload):
        try:
            request_path = '/api/v3/brokerage/orders'
            jwt_token = self.generate_rest_jwt('POST', request_path)
            headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {jwt_token}'}

            if self.session.closed:
                self.session = aiohttp.ClientSession()

            async with self.session.post(f'{self.rest_url}{request_path}', headers=headers, json=payload) as response:
                error_message = await response.text()
                status = response.status

                if status == 200:
                    return await response.json()

                elif status == 401:
                    self.logger.error(f"� [401] Unauthorized Order Creation: {error_message}")
                    return {
                        "error": "Unauthorized",
                        "details": error_message,
                        "code": ValidationCode.UNAUTHORIZED.value
                    }

                elif status == 400:
                    self.logger.error(f"⚠️ [400] Bad Request: {error_message}")
                    return {
                        "error": "Bad Request",
                        "details": error_message,
                        "code": ValidationCode.BAD_REQUEST.value
                    }

                elif status == 403:
                    self.logger.error(f"⛔ [403] Forbidden: {error_message} ⛔")
                    return {
                        "error": "Forbidden",
                        "details": error_message,
                        "code": ValidationCode.FORBIDDEN.value
                    }

                elif status == 429:
                    self.logger.warning(f"⏳ [429] Rate Limit Exceeded: {error_message}")
                    return {
                        "error": "Rate Limit",
                        "details": error_message,
                        "code": ValidationCode.RATE_LIMIT.value
                    }

                elif status == 500:
                    self.logger.error(f"� [500] Internal Server Error: {error_message}")
                    return {
                        "error": "Server Error",
                        "details": error_message,
                        "code": ValidationCode.INTERNAL_SERVER_ERROR.value
                    }

                else:
                    self.logger.error(f"❌ [{status}] Unexpected Error: {error_message}")
                    return {
                        "error": f"Unexpected error {status}",
                        "details": error_message,
                        "code": ValidationCode.UNKNOWN_ERROR.value
                    }

        except aiohttp.ClientError as e:
            self.logger.error(f"� Network Error while creating order: {e}", exc_info=True)
            return {
                "error": "Network Error",
                "details": str(e),
                "code": ValidationCode.NETWORK_ERROR.value
            }

        except asyncio.TimeoutError:
            self.logger.error("⌛ Timeout while creating order")
            return {
                "error": "Timeout",
                "details": "Order request timed out",
                "code": ValidationCode.TIMEOUT.value
            }

        except Exception as e:
            self.logger.error(f"❗ Unexpected Error in create_order: {e}", exc_info=True)
            return {
                "error": "Unexpected Error",
                "details": str(e),
                "code": ValidationCode.UNHANDLED_EXCEPTION.value
            }

    async def get_fee_rates(self):
        """
        Retrieves maker and taker fee rates from Coinbase.
        Returns:
            dict: {'maker': float, 'taker': float, 'pricing_tier': str, 'usd_volume': ...} or {'error':..., 'details':...}
        """
        try:
            request_path = '/api/v3/brokerage/transaction_summary'
            jwt_token = self.generate_rest_jwt('GET', request_path)
            headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {jwt_token}'}
            url = f'{self.rest_url}{request_path}'

            if self.session.closed:
                self.session = aiohttp.ClientSession()

            async with self.session.get(url, headers=headers) as response:
                text = await response.text()
                if response.status == 200:
                    js = await response.json()
                    tier = js.get("fee_tier", {})
                    usd_volume = js.get("advanced_trade_only_volume") or js.get("total_volume")
                    if usd_volume is None:
                        self.logger.warning("⚠️ USD volume data not found in fee summary response.")
                    return {
                        "maker": self.shared_utils_precision.safe_convert(tier.get("maker_fee_rate", self.default_maker_fee), 4),
                        "taker": self.shared_utils_precision.safe_convert(tier.get("taker_fee_rate", self.default_taker_fee), 4),
                        "pricing_tier": tier.get("pricing_tier"),
                        "usd_volume": usd_volume
                    }

                if response.status == 401:
                    self.logger.warning(f"401 on get_fee_rates — reloading creds and retrying once. Body: {response}: {text}")
                    self._reload_credentials_from_config()
                    jwt_token = self.generate_rest_jwt('GET', request_path)
                    headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {jwt_token}'}
                    async with self.session.get(url, headers=headers) as r2:
                        text2 = await r2.text()
                        if r2.status == 200:
                            js = await r2.json()
                            tier = js.get("fee_tier", {})
                            usd_volume = js.get("advanced_trade_only_volume") or js.get("total_volume")
                            if usd_volume is None:
                                self.logger.warning("⚠️ USD volume data not found in fee summary response (retry).")
                            return {
                                "maker": self.shared_utils_precision.convert(tier.get("maker_fee_rate", self.default_maker_fee), 4),
                                "taker": self.shared_utils_precision.convert(tier.get("taker_fee_rate", self.default_taker_fee), 4),
                                "pricing_tier": tier.get("pricing_tier"),
                                "usd_volume": usd_volume
                            }
                        self.logger.error(f"❌ Error fetching fee rates after retry: HTTP {r2.status} → {text2}")
                        return {"error": f"HTTP {r2.status}", "details": text2}

                self.logger.error(f"❌ Error fetching fee rates: HTTP {response.status} → {text}")
                return {"error": f"HTTP {response.status}", "details": text}

        except Exception as e:
            self.logger.error(f"❌ Exception in get_fee_rates: {e}", exc_info=True)
            return {"error": "exception", "details": str(e)}

    async def update_order(self, payload, max_retries=3):
        request_path = '/api/v3/brokerage/orders/edit'
        jwt_token = self.generate_rest_jwt('POST', request_path)
        headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {jwt_token}'}

        for attempt in range(max_retries):
            async with self.session.post(f'{self.rest_url}{request_path}', headers=headers, json=payload) as response:
                if response.status == 200:
                    return await response.json()
                elif response.status == 401:
                    self.logger.error(f"Unauthorized request during order update: {await response.text()}")
                    return {"error": "Unauthorized"}
                else:
                    error_message = await response.text()
                    self.logger.error(f"Attempt {attempt + 1} failed with status {response.status}: {error_message}")
                    await asyncio.sleep(2 ** attempt)

        return {"error": "Max retries exceeded"}

    async def cancel_order(self, order_ids: list[str]) -> dict:
        """
        Cancel multiple orders using Coinbase Advanced Trade API.

        Args:
            order_ids (list[str]): List of order UUIDs to cancel.

        Returns:
            dict: API response containing details of cancelled and failed orders.
        """
        try:
            if not order_ids:
                self.logger.warning("⚠️ batch_cancel called with empty order_ids list.")
                return {"success": [], "failure": []}

            request_path = "/api/v3/brokerage/orders/batch_cancel"
            jwt_token = self.generate_rest_jwt("POST", request_path)

            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {jwt_token}"
            }

            payload = {"order_ids": order_ids}

            if self.session.closed:
                self.session = aiohttp.ClientSession()

            async with self.session.post(f"{self.rest_url}{request_path}", headers=headers, json=payload) as response:
                text = await response.text()

                if response.status != 200:
                    self.logger.error(f"❌ batch_cancel failed: {response.status} - {text}")
                    return {"success": [], "failure": order_ids}

                data = await response.json()
                self.logger.info(f"✅ batch_cancel succeeded: {data}")
                return data

        except Exception as e:
            self.logger.error(f"❌ Exception in batch_cancel: {e}", exc_info=True)
            return {"success": [], "failure": order_ids}

    async def get_historical_orders_batch(self, params: dict) -> dict:
        request_path = '/api/v3/brokerage/orders/historical/batch'
        jwt_token = self.generate_rest_jwt('GET', request_path)
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {jwt_token}',
        }
        timeout_seconds = 15  # ⏱ To catch long stalls
        async with aiohttp.ClientSession() as session:
            resp = await asyncio.wait_for(
            session.get(f"{self.rest_url}{request_path}", params=params, headers=headers),
                timeout=timeout_seconds
            )
            async with resp:
                if resp.status == 200:
                    return await resp.json()
                else:
                    text = await resp.text()
                    raise Exception(f"Error {resp.status}: {text}")

    async def list_historical_orders(self, *,
            limit: int | None = None,
            cursor: str | None = None,
            start_time: str | None = None,  # ISO-8601 “2025-04-27T00:00:00Z”
            end_time: str | None = None,
            product_id: str | None = None,  # e.g. "BTC-USD"
            order_status: str | None = None,  # e.g. "FILLED", "OPEN"
        ) -> dict:
        """
        Fetch a page of historical orders (parent + children).

        Returns the *raw* Coinbase JSON, typically:
        {
            "orders": [ {...}, {...}, ... ],
            "cursor": "opaque_cursor_string",
            "has_next": true/false
        }

        Params mirror the official API:
        https://docs.cloud.coinbase.com/advanced-trade-api/reference/retailerapi_gethistoricalorders

        All params are optional; pass only the ones you need.
        """
        try:
            request_path = '/api/v3/brokerage/orders/historical/batch'
            jwt_token = self.generate_rest_jwt("GET", request_path)
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {jwt_token}",
            }

            # --- build query string --------------------------------------
            qs = []
            if limit is not None:
                qs.append(f"limit={limit}")
            if cursor:
                qs.append(f"cursor={cursor}")
            if start_time:
                qs.append(f"start_time={start_time}")
            if end_time:
                qs.append(f"end_time={end_time}")
            if product_id:
                qs.append(f"product_id={product_id}")
            if order_status:
                qs.append(f"order_status={order_status}")

            query = ("?" + "&".join(qs)) if qs else ""

            # NEW:
            url = f'{self.rest_url}{request_path}{query}'

            # --- do request ---------------------------------------------
            if self.session.closed:
                self.session = aiohttp.ClientSession()

            async with self.session.get(url, headers=headers) as resp:
                text = await resp.text()

                if resp.status == 200:
                    return await resp.json()

                # ----- map common HTTP errors to structured dict ---------
                self.logger.error(f"[{resp.status}] list_historical_orders → {text}", exc_info=True)
                return {"error": f"HTTP {resp.status}", "details": text}

        except Exception as exc:
            self.logger.error("list_historical_orders exception", exc_info=True)
            return {"error": "Exception", "details": str(exc)}

    # ---------- L1/L2 Product Book (price + size) ----------

    async def get_product_book(
            self,
            product_id: str,
            limit: int = 1,
            aggregation_price_increment: str | None = None,
    ) -> dict:
        """
        Coinbase Advanced: /api/v3/brokerage/product_book
        Returns a normalized dict with Decimal prices/sizes:
          {
            "product_id": "BTC-USD",
            "bids": [{"price": Decimal, "size": Decimal}, ...],
            "asks": [{"price": Decimal, "size": Decimal}, ...],
            "time": "RFC3339",
            "last": Decimal|None,
            "mid_market": Decimal|None,
            "spread_bps": Decimal|None,
            "spread_absolute": Decimal|None,
          }
        """
        try:
            path = "/api/v3/brokerage/product_book"
            jwt_token = self.generate_rest_jwt("GET", path)
            headers = {"Content-Type": "application/json", "Authorization": f"Bearer {jwt_token}"}
            params = {"product_id": product_id, "limit": int(limit)}
            if aggregation_price_increment:
                params["aggregation_price_increment"] = aggregation_price_increment

            if self.session is None or self.session.closed:
                self.session = aiohttp.ClientSession()

            async with self.session.get(f"{self.rest_url}{path}", params=params, headers=headers) as resp:
                text = await resp.text()
                if resp.status != 200:
                    self.logger.warning(f"[product_book {product_id}] {resp.status} {text}")
                    return {}

                data = await resp.json()
                pb = data.get("pricebook") or {}
                bids = pb.get("bids") or []
                asks = pb.get("asks") or []

                from decimal import Decimal
                def _D(x):
                    try:
                        return Decimal(str(x))
                    except Exception:
                        return None

                def _levels(raw):
                    out = []
                    for lvl in raw[:limit]:
                        p = _D(lvl.get("price"))
                        s = _D(lvl.get("size"))
                        if p is not None and s is not None:
                            out.append({"price": p, "size": s})
                    return out

                return {
                    "product_id": pb.get("product_id") or product_id,
                    "bids": _levels(bids),
                    "asks": _levels(asks),
                    "time": pb.get("time"),
                    "last": _D(data.get("last")),
                    "mid_market": _D(data.get("mid_market")),
                    "spread_bps": _D(data.get("spread_bps")),
                    "spread_absolute": _D(data.get("spread_absolute")),
                }

        except Exception as e:
            self.logger.error(f"get_product_book({product_id}) error: {e}", exc_info=True)
            return {}

    async def get_product_books(
            self,
            product_ids: list[str],
            limit: int = 1,
            max_concurrency: int = 8,
    ) -> dict[str, dict]:
        """
        Convenience batch fetcher for a small set of active products.
        Returns { product_id: normalized_book_dict, ... }
        """
        import asyncio
        sem = asyncio.Semaphore(max_concurrency)
        out: dict[str, dict] = {}

        async def _one(pid: str):
            async with sem:
                out[pid] = await self.get_product_book(pid, limit=limit)

        try:
            await asyncio.gather(*[_one(pid) for pid in product_ids])
        except Exception as e:
            self.logger.error(f"get_product_books error: {e}", exc_info=True)
        return out

    async def get_best_bid_ask(self, product_ids: list[str]) -> dict:
        try:
            ids = await self._filter_valid_product_ids(product_ids)
            if not ids:
                self.logger.warning("best_bid_ask called with no valid product_ids after filtering.")
                return {}

            request_path = "/api/v3/brokerage/best_bid_ask"
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.generate_rest_jwt('GET', request_path)}",
            }

            async def _one_call(batch: list[str]) -> tuple[int, str, dict]:
                params = {"product_ids": batch}
                if self.session.closed:
                    self.session = aiohttp.ClientSession()
                async with self.session.get(f"{self.rest_url}{request_path}", params=params, headers=headers) as resp:
                    text = await resp.text()
                    if resp.status == 200:
                        return 200, text, await resp.json()
                    return resp.status, text, {}

            CHUNK = 50
            aggregate = {"pricebooks": []}

            for i in range(0, len(ids), CHUNK):
                batch = ids[i:i + CHUNK]
                for _attempt in range(5):
                    status, text, payload = await _one_call(batch)

                    if status == 200:
                        pbs = payload.get("pricebooks") or payload.get("pricebook") or []
                        if isinstance(pbs, dict):
                            pbs = [pbs]
                        aggregate["pricebooks"].extend(pbs)
                        break

                    elif status == 400:
                        # peel off offending id if error mentions one
                        m = re.search(r'invalid product_id provided:\s*"?([A-Z0-9\-_/]+)"?', text)
                        bad = m.group(1) if m else None
                        if bad and bad in batch:
                            self.logger.info(f"🧹 best_bid_ask dropping invalid id from batch: {bad}")
                            batch = [x for x in batch if x != bad]
                            if not batch:
                                break
                            continue
                        # refresh valid set and hard-filter the batch once
                        await self._refresh_valid_products(force=True)
                        fresh_valid = await self._refresh_valid_products()
                        batch = [x for x in batch if x in fresh_valid]
                        if not batch:
                            break
                        continue

                    elif status == 401:
                        self.logger.error(f"[401] Unauthorized Best Bid/Ask: {text}")
                        return {}

                    elif status == 429:
                        self.logger.warning(f"[429] Rate Limited on Best Bid/Ask: {text}")
                        await asyncio.sleep(1.0)
                        continue

                    elif status >= 500:
                        self.logger.error(f"[{status}] Server error fetching best bid/ask: {text}")
                        break

                    else:
                        self.logger.error(f"[{status}] Unexpected response from best_bid_ask: {text}")
                        break

            return aggregate

        except Exception as e:
            self.logger.error(f"Exception in get_best_bid_ask: {e}", exc_info=True)
            return {}

    async def fetch_all_products(self) -> list[dict]:
        """
        Fetch full product list from Coinbase (not just USD pairs).
        """
        try:
            request_path = '/api/v3/brokerage/products'
            jwt_token = self.generate_rest_jwt('GET', request_path)
            headers = {
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {jwt_token}'
            }

            if self.session.closed:
                self.session = aiohttp.ClientSession()

            async with self.session.get(f"{self.rest_url}{request_path}", headers=headers) as response:
                text = await response.text()
                status = response.status

                if status == 200:
                    data = await response.json()
                    return data.get('products', [])
                self.logger.error(f"❌ Failed to fetch products: {status} {text}")
                return []
        except Exception as e:
            self.logger.error(f"❗ Exception in fetch_all_products: {e}", exc_info=True)
            return []

    async def get_all_usd_pairs(self) -> list[str]:
        """
        Fetch all trading pairs from Coinbase and return only USD pairs.
        """
        try:
            request_path = '/api/v3/brokerage/products'
            jwt_token = self.generate_rest_jwt('GET', request_path)
            headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {jwt_token}'}
            url = f"{self.rest_url}{request_path}"

            if self.session.closed:
                self.session = aiohttp.ClientSession()

            async with self.session.get(url, headers=headers) as response:
                text = await response.text()
                if response.status == 200:
                    data = await response.json()
                    products = data.get('products', [])
                    usd_pairs = [p['product_id'] for p in products if p.get('quote_currency_id') == 'USD']
                    return usd_pairs

                if response.status == 401:
                    self.logger.warning(f"401 in get_all_usd_pairs — reloading creds and retrying once. Body: {text}")
                    self._reload_credentials_from_config()
                    jwt_token = self.generate_rest_jwt('GET', request_path)
                    headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {jwt_token}'}
                    async with self.session.get(url, headers=headers) as r2:
                        text2 = await r2.text()
                        if r2.status == 200:
                            data = await r2.json()
                            products = data.get('products', [])
                            usd_pairs = [p['product_id'] for p in products if p.get('quote_currency') == 'USD']
                            return usd_pairs
                        self.logger.error(f"❌ Failed to fetch products after retry: {r2.status} {text2}")
                        return []

                self.logger.error(f"❌ Failed to fetch products: {response.status} {text}")
                return []

        except Exception as e:
            self.logger.error(f"❗ Exception in get_all_usd_pairs: {e}", exc_info=True)
            return []

    async def fetch_ohlcv(self, symbol: str, params: dict, max_retries: int = 5):
        """
        Fetch OHLCV candles from Coinbase Advanced Trade API (JWT-authenticated).
        Includes global throttling and exponential backoff retries.

        Args:
            symbol (str): Market symbol like "BTC-USD"
            params (dict): Dictionary with keys: start, end, granularity, limit
            max_retries (int): Maximum number of retries for transient errors.

        Returns:
            dict: { 'symbol': str, 'data': pd.DataFrame }
        """
        async with self._ohlcv_semaphore:
            type(self)._active_ohlcv_tasks += 1
            try:
                self.logger.debug(
                    f"📊 OHLCV active={self._active_ohlcv_tasks} "
                    f"(max={self._ohlcv_semaphore._value + self._active_ohlcv_tasks}) | Fetching: {symbol}"
                )

                product_id = symbol.replace("/", "-")
                request_path = f"/api/v3/brokerage/products/{product_id}/candles"

                query_params = {
                    "start": str(params.get("start")),
                    "end": str(params.get("end")),
                    "granularity": params.get("granularity", "ONE_MINUTE"),
                    "limit": str(params.get("limit", 300))
                }

                jwt_token = self.generate_rest_jwt("GET", request_path)
                headers = {
                    "Authorization": f"Bearer {jwt_token}",
                    "Content-Type": "application/json"
                }
                url = f'{self.rest_url}{request_path}'

                retries = 0
                delay = 1

                while retries <= max_retries:
                    try:
                        async with aiohttp.ClientSession() as session:
                            async with session.get(url, headers=headers, params=query_params) as response:
                                if response.status == 200:
                                    result = await response.json()
                                    candles = result.get("candles", [])

                                    if not candles:
                                        self.logger.debug(f"🟡 No OHLCV data for {symbol}")
                                        return {"symbol": symbol, "data": pd.DataFrame()}

                                    df = pd.DataFrame(candles).rename(columns={
                                        "start": "time",
                                        "low": "low",
                                        "high": "high",
                                        "open": "open",
                                        "close": "close",
                                        "volume": "volume"
                                    })
                                    df["time"] = pd.to_datetime(df["time"].astype(int), unit="s", utc=True)
                                    df[["open", "high", "low", "close", "volume"]] = df[
                                        ["open", "high", "low", "close", "volume"]
                                    ].astype(float)
                                    df = df.sort_values("time")
                                    return {"symbol": symbol, "data": df}

                                elif response.status in (429, 500, 503):
                                    # Retry on rate limits or server errors
                                    self.logger.warning(
                                        f"⚠️ OHLCV fetch {symbol} → HTTP {response.status}. "
                                        f"Retrying in {delay}s (Attempt {retries + 1}/{max_retries})"
                                    )
                                else:
                                    text = await response.text()
                                    self.logger.error(
                                        f"❌ OHLCV fetch {symbol} failed → HTTP {response.status}: {text}"
                                    )
                                    return {"symbol": symbol, "data": pd.DataFrame()}

                        # Exponential backoff before retry
                        retries += 1
                        await asyncio.sleep(delay + random.uniform(0, 0.5))
                        delay = min(delay * 2, 30)

                    except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
                        retries += 1
                        self.logger.warning(
                            f"🌐 Network error on OHLCV fetch for {symbol}: {e}. "
                            f"Retrying in {delay}s (Attempt {retries}/{max_retries})"
                        )
                        await asyncio.sleep(delay + random.uniform(0, 0.5))
                        delay = min(delay * 2, 30)

                self.logger.error(f"❌ Max retries exceeded for OHLCV {symbol}")
                return {"symbol": symbol, "data": pd.DataFrame()}

            except Exception as e:
                self.logger.error(f"❌ Error fetching OHLCV for {symbol}: {e}", exc_info=True)
                return {"symbol": symbol, "data": pd.DataFrame()}

            finally:
                type(self)._active_ohlcv_tasks -= 1

    async def fetch_open_orders(self, product_id: Optional[str] = None, limit: int = 100) -> List[dict]:
        """
        Fetch all open orders from Coinbase Advanced Trade API.

        Args:
            product_id (Optional[str]): Filter orders by trading pair (e.g., 'BTC-USD').
            limit (int): Max number of orders per page (max: 100).

        Returns:
            List[dict]: Filtered and normalized list of open orders.
        """
        request_path = '/api/v3/brokerage/orders/historical/batch'
        jwt_token = self.generate_rest_jwt('GET', request_path)
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {jwt_token}',
        }

        params = {
            "limit": str(limit),
            "order_status": "OPEN"
        }

        all_orders = []
        retries = 0
        cursor = None

        timeout_seconds = 15  # ⏱ To catch long stalls

        try:
            async with aiohttp.ClientSession() as session:
                while True:
                    if cursor:
                        params["cursor"] = cursor

                    try:
                        resp = await asyncio.wait_for(
                            session.get(f"{self.rest_url}{request_path}", params=params, headers=headers),
                            timeout=timeout_seconds
                        )
                        async with resp:
                            if resp.status == 429:
                                retries += 1
                                if retries > 3:
                                    self.logger.error("❌ Max retries exceeded due to rate limits.")
                                    return []
                                wait_time = 2 ** retries
                                self.logger.warning(f"⚠️ Rate limited (429). Retrying in {wait_time}s...")
                                await asyncio.sleep(wait_time)
                                continue

                            if resp.status != 200:
                                text = await resp.text()
                                self.logger.error(f"❌ Failed to fetch orders: HTTP {resp.status}")
                                self.logger.debug(f"↩️ Response: {text}")
                                return []

                            try:
                                data = await resp.json()
                            except aiohttp.ContentTypeError:
                                self.logger.error("❌ Invalid content-type. Could not parse JSON.")
                                return []

                            orders = data.get("orders", [])
                            all_orders.extend(orders)

                            if not data.get("has_next"):
                                break

                            cursor = data.get("cursor")

                    except asyncio.TimeoutError:
                        self.logger.error(f"⏰ Timeout: fetch_open_orders() exceeded {timeout_seconds}s.")
                        return []

                    except asyncio.CancelledError:
                        self.logger.error("❌ fetch_open_orders() was cancelled during aiohttp request.", exc_info=True)
                        raise  # Required to allow shutdowns and task cancellation

                    except Exception as e:
                        self.logger.error(f"❌ Exception during open orders fetch: {e}", exc_info=True)
                        return []

            # Final filtering and formatting
            formatted_orders = []
            for order in all_orders:
                status = order.get("status", "").upper()
                completion_pct = order.get("completion_percentage", "0")
                product = order.get("product_id", "")

                if product_id and product != product_id:
                    continue  # Filter by symbol

                if status in {"FILLED", "CANCELLED", "FAILED", "EXPIRED"} or completion_pct == "100.00":
                    continue  # Not open anymore

                formatted_orders.append({
                    "id": order.get("order_id"),
                    "symbol": product,
                    "side": order.get("side", "").upper(),
                    "type": order.get("order_type", "").upper(),
                    "status": status,
                    "filled": float(order.get("filled_size", 0)),
                    "remaining": float(order.get("size", 0)) - float(order.get("filled_size", 0)),
                    "amount": float(order.get("size", 0)),
                    "price": float(order.get("price", 0)),
                    "triggerPrice": None,
                    "stopPrice": None,
                    "datetime": order.get("created_time"),
                    "info": order,
                    "clientOrderId": order.get("client_order_id"),
                })

            return formatted_orders

        except asyncio.CancelledError:
            self.logger.error("❌ fetch_open_orders() was cancelled (outer scope).", exc_info=True)
            raise

        except Exception as e:
            self.logger.error(f"❌ Error fetching open orders: {e}", exc_info=True)
            return []