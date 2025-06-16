

import asyncio

import logging
import pandas as pd
import aiohttp

from typing import Optional, List
from coinbase import jwt_generator
from datetime import datetime, timedelta
from Shared_Utils.enum import ValidationCode
from Shared_Utils.alert_system import AlertSystem
from Shared_Utils.logging_manager import LoggerManager
from Config.config_manager import CentralConfig as Config


class CoinbaseAPI:
    """This class is for REST API code and should nt be confused with the websocket code used in WebsocketHelper"""

    def __init__(self, session, shared_utils_utility, logger_manager, shared_utils_precision,):
        self.config = Config()
        self.api_key = self.config.load_websocket_api_key().get('name')
        self.api_secret = self.config.load_websocket_api_key().get('signing_key')
        self.user_url = self.config.load_websocket_api_key().get('user_api_url')
        self.market_url = self.config.load_websocket_api_key().get('market_api_url')
        self.base_url = self.config.load_websocket_api_key().get('base_url')
        self.rest_url = self.config.load_websocket_api_key().get('rest_api_url')

        log_config = {"log_level": logging.INFO}
        self.webhook_logger = LoggerManager(log_config)
        self.logger = logger_manager.loggers['shared_logger']

        self.logger.info("🔹 CoinBaseAPI  initialzed debug.")

        # default fees
        self.default_maker_fee = self.config.maker_fee
        self.default_taker_fee = self.config.taker_fee

        self.alerts = AlertSystem(logger_manager)
        self.shared_utils_precision = shared_utils_precision
        self.shared_utils_utility = shared_utils_utility

        self.session = session

        self.api_algo = self.config.load_websocket_api_key().get('algorithm')

        # low volume coins that produce no candles get cached
        self.empty_ohlcv_cache = set()
        self.last_cache_clear_time = datetime.utcnow()

        self.jwt_token = None
        self.jwt_expiry = None

    def clear_ohlcv_cache_if_stale(self):
        now = datetime.utcnow()
        if now - self.last_cache_clear_time >= timedelta(hours=1):
            self.empty_ohlcv_cache.clear()
            self.last_cache_clear_time = now
            self.logger.debug("🧹 Cleared empty OHLCV cache after 1 hour.")

    def generate_rest_jwt(self, method='GET', request_path='/api/v3/brokerage/orders'):
        try:
            jwt_uri = jwt_generator.format_jwt_uri(method, request_path)
            jwt_token = jwt_generator.build_rest_jwt(jwt_uri, self.api_key, self.api_secret)

            if not jwt_token:
                raise ValueError("JWT token is empty!")

            self.jwt_token = jwt_token
            self.jwt_expiry = datetime.utcnow() + timedelta(minutes=5)

            return jwt_token
        except Exception as e:
            self.logger.error(f"JWT Generation Failed: {e}", exc_info=True)
            return None

    def refresh_jwt_if_needed(self):
        """Refresh JWT only if it is close to expiration."""
        if not self.jwt_token or datetime.utcnow() >= self.jwt_expiry - timedelta(seconds=60):
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

    async def get_fee_rates(self,):
        """
        Retrieves maker and taker fee rates from Coinbase.
        Returns:
            dict: Dictionary containing maker and taker fee rates, or error details.
        """
        try:
            request_path = '/api/v3/brokerage/transaction_summary'
            jwt_token = self.generate_rest_jwt('GET', request_path)
            headers = {
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {jwt_token}',
            }

            url = f'https://api.coinbase.com{request_path}'

            if self.session.closed:
                self.session = aiohttp.ClientSession()

            async with self.session.get(url, headers=headers) as response:
                response_text = await response.text()
                if response.status == 200:
                    js = await response.json()
                    tier = js.get("fee_tier", {})
                    return {
                        "maker": self.shared_utils_precision.safe_convert(tier.get("maker_fee_rate", self.default_maker_fee ), 4),
                        "taker": self.shared_utils_precision.safe_convert(tier.get("taker_fee_rate", self.default_taker_fee ), 4),
                        "pricing_tier": tier.get("pricing_tier"),
                        "usd_volume": tier.get("usd_volume"),
                    }
                else:
                    self.logger.error(f"❌ Error fetching fee rates: HTTP {response.status} → {response_text}")
                    return {"error": f"HTTP {response.status}", "details": response_text}

        except Exception as e:
            self.logger.error(f"❌ Exception in get_fee_rates(): {e}", exc_info=True)
            return {"error": "Exception", "details": str(e)}

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

            url = f"https://api.coinbase.com{request_path}{query}"

            # --- do request ---------------------------------------------
            if self.session.closed:
                self.session = aiohttp.ClientSession()

            async with self.session.get(url, headers=headers) as resp:
                text = await resp.text()

                if resp.status == 200:
                    return await resp.json()

                # ----- map common HTTP errors to structured dict ---------
                self.logger.error(f"[{resp.status}] list_historical_orders → {text}", exc_inf=True)
                return {"error": f"HTTP {resp.status}", "details": text}

        except Exception as exc:
            self.logger.error("list_historical_orders exception", exc_info=True)
            return {"error": "Exception", "details": str(exc)}

    async def get_best_bid_ask(self, product_ids: list[str]) -> dict:
        """
        Fetch best bid/ask for a list of products using Coinbase Advanced Trade API.
        """
        try:
            request_path = '/api/v3/brokerage/best_bid_ask'
            jwt_token = self.generate_rest_jwt('GET', request_path)
            payload = {'product_ids': product_ids}
            headers = {
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {jwt_token}'
            }

            if self.session.closed:
                self.session = aiohttp.ClientSession()
            async with self.session.get(f"{self.rest_url}{request_path}",params=payload,headers=headers) as response:
                text = await response.text()
                status = response.status

                if status == 200:
                    return await response.json()
                elif status == 401:
                    self.logger.error(f"❌ [401] Unauthorized Best Bid/Ask: {text}")
                elif status == 429:
                    self.logger.warning(f"⏳ [429] Rate Limited on Best Bid/Ask: {text}")
                elif status >= 500:
                    self.logger.error(f"❌ [{status}] Server error fetching best bid/ask: {text}")
                else:
                    self.logger.error(f"❌ [{status}] Unexpected response from best_bid_ask: {text}")
                return {}
        except Exception as e:
            self.logger.error(f"❗ Exception in get_best_bid_ask: {e}", exc_info=True)
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
                    usd_pairs = [
                        product['product_id']
                        for product in data.get('products', [])
                        if product.get('quote_currency_id') == 'USD'
                    ]
                    return usd_pairs

                self.logger.error(f"❌ Failed to fetch product list: {status} {text}")
                return []

        except Exception as e:
            self.logger.error(f"❗ Exception in get_all_usd_pairs: {e}", exc_info=True)
            return []

    async def fetch_ohlcv(self, symbol: str, params):
        """
        Fetch OHLCV data from Coinbase REST API for a given product_id (symbol).
        """
        self.clear_ohlcv_cache_if_stale()  # clears the cache once per hour.
        try:
            start = params.get('start')
            end = params.get('end')
            timeframe = params.get('granularity')
            limit = params.get('limit', 5)

            product_id = symbol.replace('/', '-')
            url = f"https://api.coinbase.com/api/v3/brokerage/market/products/{product_id}/candles"
            query_params = {
                "start": str(start),
                "end": str(end),
                "granularity": timeframe,
                "limit": limit
            }

            headers = {
                "Content-Type": "application/json"
            }

            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=query_params, headers=headers) as response:
                    if response.status == 200:
                        response_json = await response.json()
                        candles = response_json.get("candles", [])

                        if not candles:
                            if symbol not in self.empty_ohlcv_cache:
                                self.logger.info(f"🟡 No OHLCV data for {symbol} (market inactivity)")
                                self.empty_ohlcv_cache.add(symbol)
                            return {'symbol': symbol, 'data': pd.DataFrame()}

                        # Process valid data
                        all_ohlcv = [
                            [
                                int(candle['start']) * 1000,  # to milliseconds
                                float(candle['open']),
                                float(candle['high']),
                                float(candle['low']),
                                float(candle['close']),
                                float(candle['volume']),
                            ]
                            for candle in candles
                        ]

                        df = pd.DataFrame(all_ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'volume'])
                        df['time'] = pd.to_datetime(df['time'], unit='ms', utc=True)
                        df = df.sort_values(by='time')

                        return {'symbol': symbol, 'data': df}

                    else:
                        self.logger.warning(
                            f"⚠️ Coinbase returned {response.status} for {symbol}. Possibly no trade history or invalid request.")
                        return {'symbol': symbol, 'data': pd.DataFrame()}  # Also return empty DataFrame

        except Exception as e:
            self.logger.error(f"❌ Unexpected error fetching OHLCV for {symbol}: {e}", exc_info=True)
            return None

    async def fetch_open_orders(self, product_id: Optional[str] = None, limit: int = 100) -> List[dict]:
        """
        Fetch all open orders from Coinbase Advanced Trade API.

        Args:
            product_id (Optional[str]): Filter orders by this trading pair (e.g., 'BTC-USD').
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
        }

        all_orders = []
        retries = 0
        cursor = None

        try:
            async with aiohttp.ClientSession() as session:
                while True:
                    if cursor:
                        params["cursor"] = cursor

                    try:
                        async with self.session.get(f"{self.rest_url}{request_path}", params=params, headers=headers) as resp:
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
                                self.logger.error(f"❌ Failed to fetch orders: HTTP {resp.status}")
                                text = await resp.text()
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

                            cursor = data.get("cursor")  # continue pagination

                    except Exception as e:
                        self.logger.error(f"❌ Exception during open orders fetch: {e}", exc_info=True)
                        return []

            # Final filtering in Python
            formatted_orders = []
            for order in all_orders:
                status = order.get("status", "").upper()
                completion_pct = order.get("completion_percentage", "0")
                product = order.get("product_id", "")

                if product_id and product != product_id:
                    continue  # Client-side filtering

                if status in {"FILLED", "CANCELLED"} or completion_pct == "100.00":
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

        except Exception as e:
            self.logger.error(f"❌ Error fetching open orders: {e}", exc_info=True)
            return []