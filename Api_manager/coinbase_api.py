

import asyncio
import logging
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Union
import pandas as pd
import aiohttp
from coinbase import jwt_generator
from Shared_Utils.enum import ValidationCode
from Config.config_manager import CentralConfig as Config
from Shared_Utils.alert_system import AlertSystem
from Shared_Utils.logging_manager import LoggerManager


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

        self.jwt_token = None
        self.jwt_expiry = None

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
            request_path = "/api/v3/brokerage/orders/historical/batch"
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

    async def fetch_ohlcv(self, symbol: str, params ):
        """
        Fetch OHLCV data from Coinbase REST API for a given product_id (symbol).
        timeframe: str, since: int, until: int, limit: int = 300
        Args:
            symbol (str): e.g., 'BTC/USD' or 'BTC-USD'
            timeframe (str): Coinbase granularity (e.g., 'ONE_MINUTE')
            since (int): start timestamp (UNIX)
            until (int): end timestamp (UNIX)
            limit (int): number of candles (max 350)

        Returns:
            dict: {'symbol': symbol, 'data': DataFrame}
        """
        try:
            start =params.get('start')
            end = params.get('end')
            timeframe = params.get('granularity')
            limit = params.get('limit', 300)



            # Coinbase format is "BTC-USD"
            product_id = symbol.replace('/', '-')
            url = f"https://api.coinbase.com/api/v3/brokerage/market/products/{product_id}/candles"
            params = {
                "start": str(start),
                "end": str(end),
                "granularity": timeframe,
                "limit": limit
            }

            headers = {
                "Content-Type": "application/json"
            }

            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, headers=headers) as response:
                    if response.status != 200:
                        self.logger.error(f"❌ Failed to fetch OHLCV data: {response.status}")
                        return None

                    response_json = await response.json()
                    self.logger.debug(f"Raw OHLCV response for {product_id}: {response_json}")

                    candles = response_json.get("candles", [])

                    if not candles:
                        self.logger.warning(f"⚠️ No OHLCV data returned for {symbol}")
                        return None

                    # Normalize candle format: convert to list of lists
                    all_ohlcv = []
                    for candle in candles:
                        all_ohlcv.append([
                            int(candle['start']) * 1000,  # milliseconds
                            float(candle['open']),
                            float(candle['high']),
                            float(candle['low']),
                            float(candle['close']),
                            float(candle['volume']),
                        ])

                    df = pd.DataFrame(all_ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'volume'])

                    # Format timestamp
                    df['time'] = pd.to_datetime(df['time'], unit='ms', utc=True)
                    df = df.sort_values(by='time')
                    print(f'OHLCV data {len(df)} rows, have been downloaded for {symbol}')
                    return {'symbol': symbol, 'data': df}

        except Exception as e:
            self.logger.error(f"❌ Error fetching OHLCV data for {symbol}: {e}", exc_info=True)

        return None