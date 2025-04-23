
import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional, Dict, Any

import aiohttp
import pandas as pd
import websockets
from aiohttp import web
from coinbase import jwt_generator

from Api_manager.api_manager import ApiManager
from Config.config_manager import CentralConfig as Config
from MarketDataManager.ohlcv_manager import OHLCVManager
from MarketDataManager.ticker_manager import TickerManager
from ProfitDataManager.profit_data_manager import ProfitDataManager
from Shared_Utils.alert_system import AlertSystem
from Shared_Utils.dates_and_times import DatesAndTimes
from Shared_Utils.debugger import Debugging
from Shared_Utils.logging_manager import LoggerManager
from Shared_Utils.precision import PrecisionUtils
from Shared_Utils.print_data import PrintData
from Shared_Utils.snapshots_manager import SnapshotsManager
from Shared_Utils.utility import SharedUtility
from webhook.trailing_stop_manager import TrailingStopManager
from webhook.webhook_manager import WebHookManager
from webhook.webhook_order_book import OrderBookManager
from webhook.webhook_order_manager import TradeOrderManager
from webhook.webhook_order_types import OrderTypeManager
from webhook.webhook_utils import TradeBotUtils
from webhook.webhook_validate_orders import OrderData
from webhook.webhook_validate_orders import ValidateOrders
from webhook.websocket_helper import WebSocketMarketManager


class CoinbaseAPI:
    """This class is for REST API code and should nt be confused with the websocket code used in WebsocketHelper"""

    def __init__(self, session, shared_utils_utility, logger_manager):
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

        self.alerts = AlertSystem(logger_manager)
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

            # ✅ Always fetch the active loop
            current_loop = asyncio.get_running_loop()

            if self.session.closed:
                self.session = aiohttp.ClientSession()

            async with self.session.post(f'{self.rest_url}{request_path}', headers=headers, json=payload) as response:
                error_message = await response.text()

                if response.status == 200:
                    return await response.json()

                elif response.status == 401:
                    self.logger.error(f"� [401] Unauthorized Order Creation: {error_message}")
                    return {"error": "Unauthorized", "details": error_message}

                elif response.status == 400:
                    self.logger.error(f"⚠️ [400] Bad Request: {error_message}")
                    return {"error": "Bad Request", "details": error_message}

                elif response.status == 403:
                    self.logger.error(f"⛔ [403] Forbidden: {error_message} ⛔")
                    return {"error": "Forbidden", "details": error_message}

                elif response.status == 429:
                    self.logger.warning(f"⏳ [429] Rate Limit Exceeded: {error_message}")
                    return {"error": "Rate Limit Exceeded", "details": error_message}

                elif response.status == 500:
                    self.logger.error(f"� [500] Internal Server Error: {error_message}")
                    return {"error": "Internal Server Error", "details": error_message}

                else:
                    self.logger.error(f"❌ [{response.status}] Unexpected Error: {error_message}")
                    return {"error": f"Unexpected error {response.status}", "details": error_message}

        except aiohttp.ClientError as e:
            self.logger.error(f"� Network Error while creating order: {e}", exc_info=True)
            return {"error": "Network Error", "details": str(e)}

        except asyncio.TimeoutError:
            self.logger.error("⌛ Timeout while creating order")
            return {"error": "Timeout", "details": "Order request timed out"}

        except Exception as e:
            self.logger.error(f"❗ Unexpected Error in create_order: {e}", exc_info=True)
            return {"error": "Unexpected Error", "details": str(e)}

    async def get_fee_rates(self):
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
                    json_data = await response.json()
                    fee_info = json_data.get("fee_tier", {})

                    return {
                        "maker_fee": fee_info.get("maker_fee_rate"),
                        "taker_fee": fee_info.get("taker_fee_rate"),
                        "pricing_tier": fee_info.get("pricing_tier"),
                        "usd_volume": fee_info.get("usd_volume"),
                        "full_response": json_data  # Optional: for debugging or extended use
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

class WebSocketManager:
    def __init__(self, config, coinbase_api, logger_manager, websocket_helper):
        self.config = config
        self.coinbase_api = coinbase_api
        self.logger = logger_manager

        self.websocket_helper = websocket_helper

        self.user_ws_url = self.config.load_websocket_api_key().get('user_api_url')  # for websocket use not SDK
        self.market_ws_url = self.config.load_websocket_api_key().get('market_api_url')  # for websocket use not SDK

        self.market_ws_task = None
        self.user_ws_task = None

        self.reconnect_attempts = 0


    async def start_websockets(self):
        """Start both Market and User WebSockets."""
        try:
            self.market_ws_task = asyncio.create_task(
                self.connect_websocket(self.market_ws_url, is_user_ws=False)
            )
            self.user_ws_task = asyncio.create_task(
                self.connect_websocket(self.user_ws_url, is_user_ws=True)
            )

            asyncio.create_task(self.periodic_restart())
            asyncio.create_task(self.websocket_helper.monitor_market_channel_activity())

        except Exception as e:
            self.logger.error(f"Error starting WebSockets: {e}", exc_info=True)

    async def connect_market_stream(self):
        """Reconnect the market WebSocket."""
        await self.connect_websocket(self.market_ws_url, is_user_ws=False)

    async def connect_user_stream(self):
        """Reconnect the user WebSocket."""
        await self.connect_websocket(self.user_ws_url, is_user_ws=True)


    async def periodic_restart(self):
        """Restart WebSockets every 4 hours to ensure stability."""
        while True:
            await asyncio.sleep(14400)  # 4 hours
            self.logger.info("Restarting WebSockets to ensure stability...")
            await self.websocket_helper.reconnect()

    async def connect_websocket(self, ws_url, is_user_ws=False):
        """Establish and manage a WebSocket connection."""
        while True:
            try:
                async with websockets.connect(ws_url, max_size=2 ** 20) as ws:
                    self.logger.info(f"Connected to {ws_url}")

                    if is_user_ws:
                        self.websocket_helper.user_ws = ws
                        await self.websocket_helper.subscribe_user()
                    else:
                        self.websocket_helper.market_ws = ws
                        await asyncio.sleep(1)
                        self.logger.info("⚡ Subscribing to Market Channels...")
                        await self.websocket_helper.subscribe_market()

                    self.logger.info(f"Listening on {ws_url}")

                    # Setup dispatch map for known channel handlers
                    handlers = {
                        "user": self.websocket_helper._on_user_message_wrapper,
                        "ticker_batch": self.websocket_helper._on_market_message_wrapper,
                        "heartbeats": self.websocket_helper._on_market_message_wrapper,  # Use _on_market_message_wrapper for heartbeats
                        "subscriptions": self.websocket_helper._on_market_message_wrapper
                    }

                    async for message in ws:
                        try:
                            # print(f" ⚠️ Raw WebSocket message:\n{message} ⚠️")
                            data = json.loads(message)
                            channel = data.get("channel", "")

                            handler = handlers.get(channel)
                            if handler:
                                await handler(message)
                            else:
                                self.logger.warning(
                                    f"⚠️ Unknown or unsupported WebSocket channel: {channel}.\nFull message:\n{json.dumps(data, indent=2)}"
                                )

                        except Exception as msg_error:
                            self.logger.error(f"Error processing message: {msg_error}", exc_info=True)

            except websockets.exceptions.ConnectionClosedError as e:
                self.logger.warning(f"WebSocket closed unexpectedly: {e}. Reconnecting...")
                await asyncio.sleep(min(2 ** self.reconnect_attempts, 60))
                self.reconnect_attempts += 1

            except Exception as general_error:
                self.logger.error(f"Unexpected WebSocket error: {general_error}", exc_info=True)
                await asyncio.sleep(min(2 ** self.reconnect_attempts, 60))
                self.reconnect_attempts += 1


class WebSocketHelper:
    """
            WebSocketHelper is responsible for managing WebSocket connections and API integrations.
            """
    def __init__(
            self, listener, websocket_manager, exchange, ccxt_api, logger_manager, coinbase_api,
                 profit_data_manager, order_type_manager, shared_utils_print, shared_utils_precision, shared_utils_utility,
            shared_utils_debugger, trailing_stop_manager, order_book_manager, snapshot_manager, trade_order_manager, ohlcv_manager,
            shared_data_manager, market_ws_manager, order_manager, passive_order_manager=None
                 ):

        # Core configurations
        self.config = Config()
        self.listener = listener
        self.shared_data_manager = shared_data_manager
        self.websocket_manager = websocket_manager
        self.market_ws_manager = market_ws_manager

        self.exchange = exchange
        self.ccxt_api = ccxt_api
        self.coinbase_api = coinbase_api
        self.logger = logger_manager
        # self.alerts = self.listener.alerts  # ✅ Assign alerts from webhook
        # self.sequence_number = None  # Sequence number tracking

        # WebSocket variables
        self.market_ws = None
        self.user_ws = None

        # API credentials
        self.websocket_api_key = self.config.websocket_api.get('name')
        self.websocket_api_secret = self.config.websocket_api.get('signing_key')
        self.user_ws_url = self.config.load_websocket_api_key().get('user_api_url')  # for websocket use not SDK
        self.market_ws_url = self.config.load_websocket_api_key().get('market_api_url')  # for websocket use not SDK
        self.market_channels, self.user_channels = self.config.load_channels()
        self.jwt_token = self.jwt_expiry = None

        #self.api_algo = self.config.websocket_api.get('algorithm')

        # WebSocket tasks
        self.market_ws_task = None
        self.user_ws_task = None

        # Connection-related settings
        self.heartbeat_interval = 20
        self.heartbeat_timeout = 30
        self.reconnect_delay = 5
        self.connection_stable = True
        self.connection_lock = asyncio.Lock()
        self.subscription_lock = asyncio.Lock()

        self.reconnect_attempts = 0
        self.background_tasks = []


        self.market_client = None
        self.user_client = None
        self.latest_prices = {}
        self.order_tracker_lock = asyncio.Lock()
        self.price_history = {}  # Stores the last 5 minutes of prices per trading pair ROC calculation


        # Trading parameters
        self._stop_loss = Decimal(self.config.stop_loss)
        self._min_buy_value = Decimal(self.config.min_buy_value)
        self._take_profit = Decimal(self.config.take_profit)
        self._trailing_percentage = Decimal(self.config.trailing_percentage)
        self._trailing_stop = Decimal(self.config.trailing_stop)
        self._hodl = self.config.hodl
        self._order_size = Decimal(self.config.order_size)
        self._roc_5min = Decimal(self.config._roc_5min)


        # Snapshot and data managers
        self.passive_order_manager = passive_order_manager
        self.profit_data_manager = profit_data_manager
        self.trailing_stop_manager = trailing_stop_manager
        self.order_type_manager = order_type_manager
        self.trade_order_manager = trade_order_manager
        self.order_book_manager = order_book_manager
        self.snapshot_manager = snapshot_manager

        # Utility functions
        self.sharded_utils_print = shared_utils_print
        self.shared_utils_precision = shared_utils_precision
        self.shared_utils_utility = shared_utils_utility
        self.shared_utils_debugger = shared_utils_debugger

        # Subscription settings
        self.api_channels = self.config.load_channels()
        self.subscribed_channels = set()
        self.product_ids = set()
        self.pending_requests = {}  # Track pending requests for query-answer protocol
        self._currency_pairs_ignored = self.config.currency_pairs_ignored
        self.count = 0

        # Data managers
        # self.ohlcv_manager = ohlcv_manager
        self.received_channels = set()  # Track first-time messages per channel
        self.market_channel_activity = {}  # key = channel, value = last_received_timestamp

    @property
    def market_data(self):
        return self.shared_data_manager.market_data

    @property
    def ticker_cache(self):
        return self.market_data.get("ticker_cache", {})

    @property
    def current_prices(self):
        return self.market_data.get("current_prices", {})

    @property
    def currency_pairs_ignored(self):
        return self._currency_pairs_ignored

    @property
    def stop_loss(self):
        return self._stop_loss

    @property
    def take_profit(self):
        return self._take_profit

    @property
    def hodl(self):
        return self._hodl

    @property
    def min_buy_value(self):
        return self._min_buy_value

    @property
    def roc_5min(self):
        return self._roc_5min

    @property
    def order_size(self):
        return self._order_size

    @property
    def trailing_percentage(self):
        return Decimal(self._trailing_percentage)

    @property
    def trailing_stop(self):
        return self._trailing_stop

    def generate_ws_jwt(self):
        """Generate JWT for WebSocket authentication."""
        try:
            jwt_token = jwt_generator.build_rest_jwt(self.market_ws_url, self.websocket_api_key, self.websocket_api_secret)

            if not jwt_token:
                raise ValueError("JWT token is empty!")

            self.jwt_token = jwt_token
            self.jwt_expiry = datetime.utcnow() + timedelta(minutes=5)

            return jwt_token
        except Exception as e:
            self.logger.error(f"WebSocket JWT Generation Failed: {e}", exc_info=True)
            return None

    async def generate_jwt(self):
        """Generate and refresh JWT if expired."""
        if not self.jwt_token or datetime.utcnow() >= self.jwt_expiry - timedelta(seconds=60):
            return self.generate_ws_jwt()  # ✅ Use WebSocketHelper's method

        return self.jwt_token

    async def _on_user_message_wrapper(self, message):
        """Handle incoming user WebSocket messages and delegate to processor."""
        try:
            data = json.loads(message)
            if data.get("type") == "error":
                self.logger.warning(f"� User WebSocket error message: {data}")
                if "subscribe_market or unsubscribe required" in data.get("message", ""):
                    asyncio.create_task(self._handle_subscription_error())
                return

            await self.on_user_message(data)

        except json.JSONDecodeError:
            self.logger.error("❌ Failed to decode user WebSocket message.", exc_info=True)
        except Exception as e:
            self.logger.error(f"❌ Error in _on_user_message_wrapper: {e}", exc_info=True)

    async def on_user_message(self, data):
        """Process parsed user WebSocket message."""
        try:
            channel = data.get("channel")
            self.market_channel_activity[channel] = time.time()

            if channel not in self.received_channels:
                self.received_channels.add(channel)
                self.logger.info(f"✅ First message received from user channel: {channel}")

            if channel == "user":
                await self.market_ws_manager.process_user_channel(data)
            elif channel == "heartbeats":
                self.last_heartbeat = time.time()
                self.count += 1
                if self.count >= 25:
                    heartbeat_counter = data.get("events", [{}])[0].get("heartbeat_counter")
                    print(f"❤️ USER heartbeat: Counter={heartbeat_counter}")
                    self.count = 0
            elif channel == "subscriptions":
                self.logger.debug(f"� Received user channel subscription update: {json.dumps(data, indent=2)}")
            else:
                self.logger.warning(f"⚠️ Unhandled user WebSocket channel: {channel} | Message: {json.dumps(data)}")

        except Exception as e:
            self.logger.error(f"❌ Error processing user WebSocket message: {e}", exc_info=True)

    async def monitor_market_channel_activity(self, timeout: int = 60):
        """Monitors activity for all market channels and logs if any go silent."""
        while True:
            try:
                now = time.time()
                for channel in self.market_channels:
                    last_seen = self.market_channel_activity.get(channel)
                    if not last_seen:
                        self.logger.warning(f"⚠️ No message ever received from market channel '{channel}'")
                    elif now - last_seen > timeout:
                        await self.websocket_manager.connect_market_stream()
                        self.logger.warning(
                            f"⚠️ No message received from market channel '{channel}' in the last {int(now - last_seen)} seconds reconnecting...."
                        )
                    else:
                        print(f"✅ Market channel '{channel}' active within {int(now - last_seen)}s")

                await asyncio.sleep(timeout)

            except Exception as e:
                self.logger.error(f"❌ Error monitoring market channel activity: {e}", exc_info=True)

    async def _on_market_message_wrapper(self, message):
        """Handle raw market WebSocket message and dispatch to appropriate processor."""
        try:
            data = json.loads(message)

            if data.get("type") == "error":
                self.logger.error(f"❌ Market WebSocket Error: {data.get('message')} | Full message: {data}")
                await self.reconnect()
                return

            channel = data.get("channel")
            self.market_channel_activity[channel] = time.time()

            if channel not in self.received_channels:
                self.received_channels.add(channel)
                self.logger.info(f"✅ First message received from market channel: {channel}")

            await self.on_market_message(data)

        except json.JSONDecodeError:
            self.logger.error("❌ Failed to decode market WebSocket message.", exc_info=True)
        except Exception as e:
            self.logger.error(f"❌ Error in _on_market_message_wrapper: {e}", exc_info=True)

    async def on_market_message(self, data):
        """Process parsed market WebSocket message."""
        try:
            channel = data.get("channel")

            if channel == "ticker_batch":
                await self.market_ws_manager.process_ticker_batch_update(data)
            elif channel == "heartbeats":
                self.last_heartbeat = time.time()
                self.count += 1
                if self.count >= 25:
                    heartbeat_counter = data.get("events", [{}])[0].get("heartbeat_counter")
                    print(f"💙 MARKET heartbeat: Counter={heartbeat_counter}")
                    self.count = 0
            elif channel == "subscriptions":
                self.logger.info(f"� Confirmed Market Subscriptions: ")
            else:
                self.logger.warning(f"⚠️ Unhandled market WebSocket channel: {channel} | Message: {json.dumps(data)}")

        except Exception as e:
            self.logger.error(f"❌ Error processing market WebSocket message: {e}", exc_info=True)

    async def subscribe_market(self):
        """Subscribe to the ticker_batch market channel for all product IDs."""
        try:
            async with self.subscription_lock:
                if not self.market_ws:
                    self.logger.error("❌ Market WebSocket is None! Subscription aborted.")
                    return

                self.logger.info(f"� Subscribing to Market Channels: {list(self.market_channels)}")

                snapshot = await self.snapshot_manager.get_market_data_snapshot()
                market_data = snapshot.get("market_data", {})
                product_ids = [key.replace('/', '-') for key in market_data.get('current_prices', {}).keys()] or ["BTC-USD"]

                if not product_ids:
                    self.logger.warning("⚠️ No valid product IDs found. Subscription aborted.")
                    return

                for channel in self.market_channels:
                    subscription_message = {
                        "type": "subscribe",
                        "product_ids": product_ids,
                        "channel": channel
                    }
                    try:
                        await self.market_ws.send(json.dumps(subscription_message))
                        self.logger.debug(f"✅ Sent subscription for {channel} with: {product_ids}")
                    except Exception as e:
                        self.logger.error(f"❌ Failed to subscribe to {channel}: {e}", exc_info=True)

                self.product_ids.update(product_ids)
                self.subscribed_channels.update(self.market_channels)

        except Exception as e:
            self.logger.error(f"❌ Market subscription error: {e}", exc_info=True)

    async def subscribe_user(self):
        """Subscribe to User WebSocket channels with proper JWT authentication."""
        try:
            async with self.subscription_lock:
                self.coinbase_api.refresh_jwt_if_needed()

                new_channels = set(self.user_channels) - self.subscribed_channels
                if not new_channels:
                    self.logger.info("Already subscribed to all requested user channels. Skipping subscription.")
                    return

                # ✅ Ensure WebSocket is initialized before subscribing
                if not hasattr(self, "user_ws") or self.user_ws is None:
                    self.logger.error("User WebSocket is not initialized. Subscription aborted.")
                    return

                # ✅ Refresh JWT before subscribing
                jwt_token = await self.generate_jwt()

                # ✅ Fetch active product IDs
                snapshot = await self.snapshot_manager.get_market_data_snapshot()
                market_data = snapshot.get("market_data", {})
                # product_ids = [key.replace('/', '-') for key in market_data.get('current_prices', {}).keys()] or ["BTC-USD"]
                product_ids = ["BTC-USD", "ETH-USD", "SOL-USD"]

                # ✅ Subscribe to each user channel separately
                for channel in new_channels:
                    subscription_message = {
                        "type": "subscribe",
                        "product_ids": product_ids,  # ✅ Ensure correct product ID format
                        "channel": channel,  # ✅ One channel per message
                        "jwt": jwt_token  # ✅ Include JWT for authentication
                    }
                    await self.user_ws.send(json.dumps(subscription_message))
                    self.logger.debug(f"Subscribed to user channel: {channel} with products: {product_ids}")

                self.subscribed_channels.update(new_channels)

        except Exception as e:
            self.logger.error(f"User subscription error: {e}", exc_info=True)


    async def reconnect(self):
        """Reconnects both market and user WebSockets with exponential backoff."""
        if self.reconnect_attempts >= 5:
            self.logger.error("Max reconnect attempts reached. Manual intervention needed.")
            return

        delay = min(2 ** self.reconnect_attempts, 60)
        self.logger.warning(f"Reconnecting in {delay} seconds...")
        await asyncio.sleep(delay)

        try:
            # ✅ Use dedicated methods in WebSocketManager
            await self.websocket_manager.connect_market_stream()
            await self.websocket_manager.connect_user_stream()

            self.reconnect_attempts = 0
            self.logger.info("Reconnected successfully.")
        except Exception as e:
            self.reconnect_attempts += 1
            self.logger.error(f"Reconnection failed: {e}", exc_info=True)
            await self.reconnect()

    async def _handle_subscription_error(self):
        """Handles the WebSocket subscription error by resubscribing."""
        try:
            # Check if WebSocket is open

            if self.user_client.websocket and self.user_client.websocket.open:
                self.logger.info("Attempting to resubscribe after error.", exc_info=True)

                await self.user_client.unsubscribe_all_async()

                # Re-subscribe_market to channels
                await self.user_client.subscribe_async(
                    product_ids=self.product_ids,
                    channels=['user', 'heartbeats']
                )
                self.logger.info(f"Resubscribed to channels")
            else:
                self.logger.warning("WebSocket not open, attempting reconnection.", exc_info=True)
                await self.websocket_manager.connect_websocket()  # Reconnect if not open

        except Exception as e:
            self.logger.error(f"Error during re-subscription: {e}", exc_info=True)

    # async def handle_order_update(self, order, profit_data_list):
    #  do not delete this until orders can be updated...
    #     """
    #     Handle updates to individual orders.
    #
    #     Args:
    #         order (dict): The order data from the event.
    #     """
    #     try:
    #         order_tracker = self.listener.order_management.get('order_tracker', {})
    #
    #         # Handle open orders
    #         if order.get('status') == 'OPEN':
    #             # Delegate updating the order to the lower-level function
    #             await self.update_order_in_tracker(order, profit_data_list)
    #
    #         # Remove closed or canceled orders from tracker
    #         elif order.get('status') not in {"OPEN", None}:
    #             order_id = order.get('order_id')
    #             if order_id in order_tracker:
    #                 order_tracker.pop(order_id, None)
    #                 self.log_manager.info(f"Order {order_id} removed from tracker in real-time.")
    #
    #     except Exception as order_error:
    #         self.log_manager.error(f"Error handling order update: {order_error}", exc_info=True)



    async def process_event(self, event, profit_data_list, event_type):
        """Process specific events such as snapshots and updates."""
        print(f"Processing event: {event_type}")
        try:
            orders = event.get("orders", [])

            if event_type == "snapshot":
                # Initialize tracker with the snapshot's orders
                for order in orders:
                    await self.market_ws_manager.process_order_for_tracker(order, profit_data_list, event_type)
                profit_df = self.profit_data_manager.consolidate_profit_data(profit_data_list)
            elif event_type == "update":
                # Apply updates to the order tracker
                # determine order type buy sell cancel
                for order in orders:
                    await self.market_ws_manager.handle_order_for_order_tracker(order, profit_data_list, event_type)
        except Exception as e:
            self.logger.error(f"Error processing {event_type} event: {e}", exc_info=True)

    async def monitor_and_update_active_orders(self, market_data_snapshot, order_management_snapshot):
        """Monitor active orders and update trailing stops or profitability."""
        try:
            spot_positions = market_data_snapshot.get('spot_positions', {})
            coin_info = market_data_snapshot.get('filtered_vol', {})
            current_prices = market_data_snapshot.get('current_prices', {})
            usd_pairs = market_data_snapshot.get('usd_pairs_cache', {})
            usd_avail = order_management_snapshot.get('non_zero_balances',{})['USD']['available_to_trade_crypto'] # USD is custom
            profit_data_list = []
            profit_data_list_new = []

            async with self.order_tracker_lock:
                order_tracker_snapshot = dict(order_management_snapshot.get("order_tracker", {}))
                for order_id, raw_order in order_tracker_snapshot.items():
                    order_data = OrderData.from_dict(raw_order)

                    try:

                        symbol = order_data.trading_pair
                        asset = symbol.split('/')[0]
                        # ✅ Fetch precision values for the asset
                        precision_data = self.shared_utils_precision.fetch_precision(symbol)
                        if asset == 'FIS':
                            pass

                        base_deci, quote_deci, _, _ = precision_data

                        # ✅ Add precision values to order_data
                        order_data.quote_decimal = quote_deci
                        order_data.base_decimal = base_deci
                        order_data.product_id = symbol

                        avg_price = spot_positions.get(asset, {}).get('average_entry_price', {}).get('value', 0)
                        avg_price = Decimal(avg_price).quantize(Decimal('1.' + '0' * quote_deci))
                        asset_balance = Decimal(spot_positions.get(asset, {}).get('total_balance_crypto', 0))
                        asset_balance = Decimal(asset_balance).quantize(Decimal('1.' + '0' * quote_deci))
                        current_price = current_prices.get(symbol, 0)
                        cost_basis = spot_positions.get(asset, {}).get('cost_basis', {}).get('value', 0)
                        cost_basis = Decimal(cost_basis).quantize(Decimal('1.' + '0' * quote_deci))

                        required_prices = {
                            'avg_price': avg_price,
                            'cost_basis': cost_basis,
                            'asset_balance': asset_balance,
                            'current_price': None,
                            'profit': None,
                            'profit_percentage': None,
                            'usd_avail': usd_avail,
                            'status_of_order': order_data.status
                        }

                        if order_data.type == 'limit' and order_data.side == 'sell':
                            order_book = await self.order_book_manager.get_order_book(order_data, symbol)
                            highest_bid = Decimal(max(order_book['order_book']['bids'], key=lambda x: x[0])[0])
                            if order_data.price < current_price:
                                # ✅ Update trailing stop with highest bid
                                await self.listener.order_manager.cancel_order(order_data.order_id, symbol)
                                new_order_data = await self.trade_order_manager.build_order_data('Websocket', 'trailing_stop', asset, symbol,
                                                                                                 old_limit_price, None)


                                continue
                            else:  # for testing purposes

                                pass


                        elif order_data.type == 'limit' and order_data.side == 'buy':

                            # Fetch the current order book for the trading pair
                            order_book = await self.order_book_manager.get_order_book(order_data, symbol)
                            best_ask = Decimal(min(order_book['order_book']['asks'], key=lambda x: x[0])[0])

                            # Define a threshold for price difference (e.g., 1%)
                            price_difference = (best_ask - order_data.price) / order_data.price
                            if price_difference > Decimal('0.01'):

                                # Cancel the existing limit buy order
                                await self.listener.order_manager.cancel_order(order_data.order_id, symbol)

                                # Calculate a new limit price (e.g., slightly below the best ask)
                                new_limit_price = best_ask * Decimal('0.995')  # 0.5% below best ask
                                new_limit_price = new_limit_price.quantize(Decimal('1.' + '0' * quote_deci))

                                # Build and place a new limit buy order
                                new_order_data = await self.trade_order_manager.build_order_data(
                                    'Websocket', 'limit_buy_adjusted', asset, symbol, new_limit_price, None
                                )
                                if new_order_data:
                                    new_order_data.trigger = "limit_buy_adjusted"


                        elif order_data.type == 'take_profit_stop_loss' and order_data.side == 'sell':
                            full_tracker = order_management_snapshot.get("order_tracker", {})
                            full_order = full_tracker.get(order_data.order_id)
                            if full_order:
                                # Extract old limit price and current price
                                trigger_config = full_order['info']['order_configuration']['trigger_bracket_gtc']
                                old_limit_price = Decimal(trigger_config.get('limit_price', '0'))
                                current_price = Decimal(current_prices.get(symbol, Decimal('0')))
                                # If we're above the original limit price, reconfigure
                                if current_price > old_limit_price:
                                    print(f"🔆TP adjustment: Current price {current_price} > TP {old_limit_price}, reconfiguring TP/SL for {symbol} 🔆")
                                    # Cancel the stale TP/SL order
                                    await self.listener.order_manager.cancel_order(order_data.order_id, symbol)
                                    # Create new TP/SL order
                                    new_order_data = await self.trade_order_manager.build_order_data('Websocket', 'profit', asset, symbol,
                                                                                                     old_limit_price, None)
                                    continue

                        profit = await self.profit_data_manager.calculate_profitability(
                            symbol, required_prices, current_prices, usd_pairs
                        )

                        if profit and profit.get('profit', 0) != 0:
                            profit_data_list.append(profit)
                        if Decimal(profit.get('profit percent', '0').replace('%', '')) / 100 <= self.stop_loss:
                            await self.listener.handle_order_fill(order_data)

                    except Exception as e:
                        self.logger.error(f"Error handling tracked order {order_id}: {e}", exc_info=True)

            if profit_data_list:
                profit_df = self.profit_data_manager.consolidate_profit_data(profit_data_list)
                print(f'Profit Data Open Orders:\n{profit_df.to_string(index=True)}')

            if profit_data_list_new:
                profit_df_new = self.profit_data_manager.consolidate_profit_data(profit_data_list_new)
                print(f'Profit Data Open Orders:\n{profit_df_new.to_string(index=True)}')

            await self.monitor_untracked_assets(market_data_snapshot, order_management_snapshot)

        except Exception as e:
            self.logger.error(f"Error in monitor_and_update_active_orders: {e}", exc_info=True)

    async def monitor_untracked_assets(self, market_data_snapshot: Dict[str, Any], order_management_snapshot: Dict[str, Any]):
        """
        Monitors untracked assets (no active orders to buy or sell) and places sell orders if they are profitable.
        """
        try:
            order_tracker = order_management_snapshot.get('order_tracker', {})
            spot_positions = market_data_snapshot.get('spot_positions', {})
            non_zero_balances = order_management_snapshot.get('non_zero_balances', {})
            usd_pairs = market_data_snapshot.get('usd_pairs_cache', {})

            usd_prices = usd_pairs.set_index('symbol')['price'].to_dict()

            for asset, position in spot_positions.items():
                symbol = f"{asset}/USD"
                if asset == 'FIS':
                    pass
                if symbol in self.currency_pairs_ignored:
                    continue
                if any(order.get('symbol') == symbol for order in order_tracker.values()):
                    continue
                average_entry_price = Decimal(position.get('average_entry_price', {}).get('value', '0'))
                asset_balance = Decimal(position.get('available_to_trade_crypto', '0'))
                asset_value = asset_balance * average_entry_price

                if asset_value < self.min_buy_value:
                    continue

                precision_data = self.shared_utils_precision.fetch_precision(symbol)
                base_deci, quote_deci, min_trade_amount, _ = precision_data

                if asset_balance < min_trade_amount:
                    continue

                current_price = usd_prices.get(symbol)
                if current_price is None:
                    continue

                current_price = self.shared_utils_precision.adjust_precision(base_deci, quote_deci, current_price, convert='quote')

                cost_basis = Decimal(position.get('cost_basis', {}).get('value', '0'))

                profit_data = await self.profit_data_manager.calculate_profitability(
                    symbol,
                    {
                        'avg_price': average_entry_price,
                        'cost_basis': cost_basis,
                        'asset_balance': asset_balance,
                        'current_price': current_price
                    },
                    usd_prices,
                    usd_pairs
                )

                if not profit_data:
                    continue

                profit_percent = Decimal(profit_data.get('profit percent', '0').strip('%')) / Decimal('100')

                if profit_percent >= self.take_profit and asset not in self.hodl:

                    entry_price = (1 + (profit_percent - self.take_profit)) * average_entry_price
                    order_data = await self.trade_order_manager.build_order_data('Websocket', 'profit', asset, symbol, entry_price, None, 'tp_sl')
                    if order_data:
                        order_data.trigger = 'profit'
                        order_success, response_msg = await self.trade_order_manager.place_order(order_data, precision_data)
                        self.logger.info(f"Placed sell order for {symbol}: {response_msg}")
                elif profit_percent < self.stop_loss and asset not in self.hodl:
                    order_data = await self.trade_order_manager.build_order_data('Websocket', 'stop_loss', asset, symbol, None, None)
                    if order_data:
                        order_data.trigger = 'stop_loss'
                        order_success, response_msg = await self.trade_order_manager.place_order(order_data, precision_data)
                        self.logger.info(f"Placed stop-loss order for {symbol}: {response_msg}")

            await asyncio.sleep(15)

        except Exception as e:
            self.logger.error(f"Error in monitor_untracked_assets: {e}", exc_info=True)

    async def refresh_open_orders(self, trading_pair=None):
        """
        Refresh open orders using the REST API, cross-check them with order_tracker,
        and remove obsolete orders from the tracker.

        Args:
            trading_pair (str): Specific trading pair to check for open orders (e.g., 'BTC/USD').

        Returns:
            tuple: (DataFrame of all open orders, has_open_order (bool), updated order tracker)
        """
        try:
            print(f"  🟪   refresh_open_orders  🟪  ")  # debug
            # � Attempt to fetch open orders with retries
            endpoint = 'private'
            params = {'paginate': True, 'paginationCalls': 10}
            max_retries = 3
            all_open_orders = []

            for attempt in range(max_retries):
                all_open_orders = await self.ccxt_api.ccxt_api_call(
                    self.exchange.fetch_open_orders, endpoint, params=params
                )

                if all_open_orders:
                    break  # ✅ Stop retrying if orders are found

                print(f"⚠️ Attempt {attempt + 1}: No open orders found. Retrying...")
                await asyncio.sleep(2)  # Small delay before retrying

            # ✅ Retrieve the existing order tracker
            order_tracker_master = self.listener.order_management.get('order_tracker', {})

            # � If API fails to return orders, DO NOT remove everything—fallback to `order_tracker`
            if not all_open_orders:
                print("❌ No open orders found from API! Using cached order_tracker...")
                all_open_orders = list(order_tracker_master.values())

            # ✅ Cross-check API-fetched order IDs with existing order_tracker IDs
            fetched_order_ids = {order.get('id') for order in all_open_orders if order.get('id')}
            existing_order_ids = set(order_tracker_master.keys())

            # ✅ Identify obsolete orders to remove (only if API was successful)
            if all_open_orders:
                obsolete_order_ids = existing_order_ids - fetched_order_ids
                for obsolete_order_id in obsolete_order_ids:
                    #print(f"� Removing obsolete order: {obsolete_order_id}")# debug
                    del order_tracker_master[obsolete_order_id]

            # ✅ Update order tracker with new API data
            for order in all_open_orders:
                order_id = order.get('id')
                if order_id:
                    created_time_str = order.get('info', {}).get('created_time')
                    if created_time_str:
                        created_time = datetime.fromisoformat(created_time_str.replace("Z", "+00:00"))
                        now = datetime.now(timezone.utc)
                        order['order_duration'] = round((now - created_time).total_seconds() / 60, 2)

                    order_tracker_master[order_id] = order
            # ✅ Ensure latest data is stored

            # ✅ Save updated tracker
            self.listener.order_management['order_tracker'] = order_tracker_master

            # ✅ Check if there is an open order for the specific `trading_pair`
            has_open_order = any(order['symbol'] == trading_pair for order in all_open_orders) if trading_pair else bool(
                all_open_orders)

            return pd.DataFrame(all_open_orders), has_open_order, order_tracker_master

        except Exception as e:
            self.logger.error(f"Failed to refresh open orders: {e}", exc_info=True)
            return pd.DataFrame(), False, self.listener.order_management['order_tracker']

class WebhookListener:
    """The WebhookListener class is the central orchestrator of the bot,
    handling market data updates, order management, and webhooks."""

    _exchange_instance_count = 0

    def __init__(self, bot_config, shared_data_manager, database_session_manager, logger_manager, session, market_manager,
                 market_data_manager, exchange):
        self.bot_config = bot_config
        if not hasattr(self.bot_config, 'rest_client') or not self.bot_config.rest_client:
            print("REST client is not initialized. Initializing now...")
            self.bot_config.initialize_rest_client()
        # Assign the REST client and portfolio UUID
        self.rest_client = self.bot_config.rest_client
        self.min_sell_value = float(self.bot_config.min_sell_value)
        self.portfolio_uuid = self.bot_config.portfolio_uuid
        self.session = session  # ✅ Store session passed from run_app
        # self.cb_api = self.bot_config.load_webhook_api_key() #moved to main.py
        self.exchange = exchange
        # self.order_management = {'order_tracker': {}}
        self.shared_data_manager = shared_data_manager
        self.market_manager = market_manager
        self.market_data_manager = market_data_manager
        self.logger_manager = logger_manager  # 🙂
        self.logger = logger_manager.loggers['webhook_logger']  # ✅ this is the actual logger you’ll use

        self.webhook_manager = self.ticker_manager = self.utility = None  # Initialize webhook manager properly
        self.ohlcv_manager = None
        self.processed_uuids = set()

        # Core Utilities
        self.shared_utils_exchange = self.exchange
        self.shared_utils_precision = PrecisionUtils.get_instance(self.logger_manager, self.shared_data_manager)

        self.shared_utiles_data_time = DatesAndTimes.get_instance(self.logger_manager)
        self.shared_utils_utility = SharedUtility.get_instance(self.logger_manager)
        self.shared_utils_print = PrintData.get_instance(self.logger_manager, self.shared_utils_utility)
        self.shared_utils_debugger = Debugging()

        self.coinbase_api = CoinbaseAPI(self.session, self.shared_utils_utility, self.logger_manager)
        self.alerts = AlertSystem(self.logger_manager)
        self.ccxt_api = ApiManager.get_instance(self.exchange, self.logger_manager, self.alerts)

        #database related
        self.database_session_manager = database_session_manager

        self.lock = asyncio.Lock()
        # created without WebSocketHelper initially

        # ✅ Step 1: Create WebSocketHelper With Placeholders
        self.websocket_helper = WebSocketHelper(
            listener=self,
            websocket_manager=None,  # Placeholder
            exchange=self.exchange, # Placeholder
            ccxt_api=self.ccxt_api, # Placeholder
            logger_manager=self.logger_manager,
            coinbase_api=self.coinbase_api,
            profit_data_manager=None,  # Placeholder
            order_type_manager=None,  # Placeholder
            shared_utils_print=self.shared_utils_print, # Placeholder
            shared_utils_precision=self.shared_utils_precision,
            shared_utils_utility=self.shared_utils_utility, # Placeholder
            shared_utils_debugger=self.shared_utils_debugger, # Placeholder
            trailing_stop_manager=None,  # Placeholder
            order_book_manager=None,  # Placeholder
            snapshot_manager=None,  # Placeholder
            trade_order_manager=None,
            ohlcv_manager=None,
            shared_data_manager=self.shared_data_manager,
            market_ws_manager=None,
            order_manager=None  # Placeholder

        )

        self.websocket_manager = WebSocketManager(self.bot_config, self.ccxt_api, self.logger,
                                                  self.websocket_helper)

        self.websocket_helper.websocket_manager = self.websocket_manager

        # self.coinbase_api = CoinbaseAPI(self.session, self.shared_utils_utility, self.logger)

        self.snapshot_manager = SnapshotsManager.get_instance(self.shared_data_manager, self.logger_manager)

        # Instantiation of ....
        self.utility = TradeBotUtils.get_instance(self.logger, self.coinbase_api, self.exchange,
                                                  self.ccxt_api, self.alerts, self.shared_data_manager)


        self.ticker_manager = None

        self.profit_data_manager = ProfitDataManager.get_instance(self.shared_utils_precision, self.shared_utils_print,
                                                                  self.shared_data_manager, self.logger_manager)

        self.order_book_manager = OrderBookManager.get_instance(self.exchange, self.shared_utils_precision,
                                                                self.logger, self.ccxt_api)

        self.validate = ValidateOrders.get_instance(self.logger, self.order_book_manager,
                                                    self.shared_utils_precision)

        self.order_type_manager = OrderTypeManager.get_instance(
            coinbase_api=self.coinbase_api,
            exchange_client=self.exchange,
            shared_utils_precision=self.shared_utils_precision,
            shared_utils_utility=self.shared_utils_utility,
            validate=self.validate,
            logger_manager=self.logger,
            alerts=self.alerts,
            ccxt_api=self.ccxt_api,
            order_book_manager=self.order_book_manager,
            websocket_helper=None, #Placeholder for self.websocket_helper,
            session=self.session
        )

        # self.market_data_lock = asyncio.Lock()

        self.trailing_stop_manager = TrailingStopManager.get_instance(self.logger, self.shared_utils_precision,
                                                                      self.coinbase_api, self.shared_data_manager,
                                                                      self.order_type_manager)

        self.trade_order_manager = TradeOrderManager.get_instance(
            coinbase_api=self.coinbase_api,
            exchange_client=self.exchange,
            shared_utils_precision=self.shared_utils_precision,
            shared_utils_utility=self.shared_utils_utility,
            validate=self.validate,
            logger_manager=self.logger,
            alerts=self.alerts,
            ccxt_api=self.ccxt_api,
            order_book_manager=self.order_book_manager,
            order_types=self.order_type_manager,
            websocket_helper=self.websocket_helper,
            shared_data_manager=self.shared_data_manager,
            session=self.coinbase_api.session,
            profit_manager=self.profit_data_manager
        )

        #Assign WebSocketHelper to Other Managers
        self.trade_order_manager.websocket_helper = self.websocket_helper
        self.order_type_manager.websocket_helper = self.websocket_helper

        self.webhook_manager = WebHookManager.get_instance(
            logger_manager=self.logger,
            shared_utils_precision=self.shared_utils_precision,
            trade_order_manager=self.trade_order_manager,
            alerts=self.alerts,
            session=self.session
        )

        self.websocket_helper = WebSocketHelper(
            self, self.websocket_manager, self.exchange, self.ccxt_api, self.logger,
            self.coinbase_api, self.profit_data_manager, self.order_type_manager,
            self.shared_utils_print, self.shared_utils_precision, self.shared_utils_utility,
            self.shared_utils_debugger, self.trailing_stop_manager, self.order_book_manager,
            self.snapshot_manager, self.trade_order_manager, None,
            self.shared_data_manager, self.session, None

        )
        self.market_ws_manager = WebSocketMarketManager(
            self, self.exchange, self.ccxt_api, self.logger, self.coinbase_api,
            self.profit_data_manager, self.order_type_manager, self.shared_utils_print,
            self.shared_utils_precision, self.shared_utils_utility, self.shared_utils_debugger,
            self.trailing_stop_manager, self.order_book_manager, self.snapshot_manager,
            self.trade_order_manager, self.ohlcv_manager, self.shared_data_manager
        )
    async def async_init(self):
        """Initialize async components after __init__."""
        self.ohlcv_manager = await OHLCVManager.get_instance(self.exchange, self.ccxt_api, self.logger_manager,
                                                             self.shared_utiles_data_time, self.market_manager)
        self.ticker_manager = await TickerManager.get_instance(self.bot_config, self.shared_utils_debugger,
                                                               self.shared_utils_print, self.logger_manager,
                                                               self.rest_client, self.portfolio_uuid, self.exchange,
                                                               self.ccxt_api, self.shared_data_manager,
                                                               self.shared_utils_precision
        )

    @property
    def market_data(self):
        return self.shared_data_manager.market_data

    @property
    def order_management(self):
        return self.shared_data_manager.order_management

    @property
    def ticker_cache(self):
        return self.market_data.get('ticker_cache', {})

    @property
    def current_prices(self):
        return self.market_data.get('current_prices', {})

    @property
    def filtered_balances(self):
        return self.order_management.get('non_zero_balances', {})


    async def refresh_market_data(self):
        """Refresh market_data and manage orders periodically."""
        while True:
            try:
                # Fetch new market data
                new_market_data, new_order_management = await self.market_data_manager.update_market_data(time.time())

                # Ensure fetched data is valid before proceeding
                if not new_market_data:
                    self.logger.error("❌ new_market_data is empty! Skipping update.")
                    await asyncio.sleep(60)  # Wait before retrying
                    continue

                if not new_order_management:
                    self.logger.error("❌ new_order_management is empty! Skipping update.")
                    await asyncio.sleep(60)
                    continue

                # Update shared state via SharedDataManager
                await self.shared_data_manager.update_market_data(new_market_data, new_order_management)
                new_price = self.shared_data_manager.market_data.get("spot_positions", {}).get("USD", {}).get("available_to_trade_fiat", "N/A")
                print("⚠️ Market data and order management updated successfully. ⚠️")

                # Refresh open orders and get the updated order_tracker
                _, _, updated_order_tracker = await self.websocket_helper.refresh_open_orders()

                # Reflect the updated order_tracker in the shared state
                if updated_order_tracker:
                    new_order_management['order_tracker'] = updated_order_tracker
                    await self.shared_data_manager.update_market_data(new_market_data, new_order_management)


                # Monitor and update active orders
                await self.websocket_helper.monitor_and_update_active_orders(new_market_data, new_order_management)

            except Exception as e:
                self.logger.error(f"❌ Error refreshing market_data: {e}", exc_info=True)

            # Sleep before next update
            await asyncio.sleep(30)

    async def handle_order_fill(self, websocket_order_data: OrderData):
        """Process existing orders that are Open or Active or have beend filled"""

        try:
            base_deci = websocket_order_data.base_decimal
            quote_deci = websocket_order_data.quote_decimal

            if websocket_order_data.type == 'stop_limit':
                websocket_order_data.limit_price = self.shared_utils_precision.adjust_precision(
                    base_deci, quote_deci, websocket_order_data.limit_price, 'quote'
                )

                websocket_order_data.stop_loss_price = self.shared_utils_precision.adjust_precision(
                    base_deci, quote_deci, websocket_order_data.stop_loss_price, 'quote'
                )

                websocket_order_data.average_price = self.shared_utils_precision.adjust_precision(
                    base_deci, quote_deci, websocket_order_data.average_price, 'quote'
                )
            elif websocket_order_data.type.lower() == 'limit':
                websocket_order_data.stop_loss_price = self.shared_utils_precision.adjust_precision(
                    base_deci, quote_deci, websocket_order_data.limit_price, 'quote'
                )
            elif websocket_order_data.type == 'limit':
                websocket_order_data.price = (
                    self.shared_utils_precision.adjust_precision(base_deci, quote_deci, websocket_order_data.price, 'quote')
                )
            else:
                pass

            self.usd_pairs = self.market_data.get('usd_pairs_cache', {})
            self.spot_info = self.market_data.get('spot_positions', {})
            print(f" 🟠 handle_order_fill started order_tracker:  🟠 ")
            symbol = None
            order_id = None

            if websocket_order_data.status.lower() == 'filled':
                symbol = websocket_order_data.trading_pair
                print(f"Symbol: {symbol}")
                order_id = websocket_order_data.order_id
            elif websocket_order_data.status.lower() == 'open':
                symbol = websocket_order_data.trading_pair.replace('-', '/')
                print(f"Symbol: {symbol}")
                order_id = websocket_order_data.order_id
            else:
                pass
            asset = symbol.split('/')[0]
            base_deci, quote_deci, _, _ = self.shared_utils_precision.fetch_precision(symbol)


            if websocket_order_data.price:
                websocket_order_data.price = self.shared_utils_precision.adjust_precision(
                    base_deci, quote_deci, websocket_order_data.price, 'quote'
                )
            if websocket_order_data.order_amount:
                websocket_order_data.order_amount = self.shared_utils_precision.adjust_precision(
                    base_deci, quote_deci, websocket_order_data.order_amount, 'base'
                )
            product_id = symbol.replace('/', '-')
            order_data = await self.trade_order_manager.build_order_data('Websocket', websocket_order_data.trigger, asset, product_id, None, None)
            order_data.trigger = websocket_order_data.trigger
            if order_data:
                print(f'')
                print(f' 🟠️ handle_order_fill - Order Data: 🟠   {order_data.debug_summary(verbose=True)}  ')  # Debug
                print(f'')
                await self._process_order_fill('WebSocket', order_data)


        except Exception as e:
            print(f'websocket_msg:{websocket_order_data}')
            self.logger.error(f"Error in handle_order_fill: {e} {websocket_order_data}", exc_info=True)

    async def _process_order_fill(self, source, order_data: OrderData):
        """
        Process an order fill and place a corresponding trailing stop order.

        Args:
            order_data (dict): Details of the filled order, including symbol, price, and size.
        """
        print(f"Processing order fill: {order_data.side}:{order_data.trading_pair}")
        try:
            if order_data.open_orders.get('open_order'):
                return

            # Fetch the order book for price and size adjustments
            order_book = await self.order_book_manager.get_order_book(order_data)

            # Use take profit stop loss
            order_data.source = source
            order_success, response_msg = await self.trade_order_manager.place_order(order_data)
            if response_msg:
                response_data = response_msg
                if response_data.get('error') == 'OPEN_ORDER':
                    return
            else:
                return

            if response_data:
                if response_data.get('details', {}).get("Order_id"):
                    pass
                    print(f'REVIEW CODE FOR TRAILING STOP ORDER (1789)*********************************')
                    # Add the trailing stop order to the order_tracker
                    self.order_management['order_tracker'][response_data["order_id"]] = {
                        'symbol': order_data.trading_pair,
                        'take_profit_price': tp,
                        'purchase_price': order_data.average_price,
                        'amount': order_data.order_amount,
                        'stop_loss_price': sl,
                        'limit_price': order_data.limit_price * Decimal('1.002')  # Example limit price adjustment
                    }
                    order_id = response_data.get("order_id")
                    print(f"Order tracker updated with trailing stop order: {order_id}")

                    # Remove the associated buy order from the order_tracker
                    associated_buy_order_id = order_data.order_id
                    if associated_buy_order_id in self.order_management['order_tracker']:
                        del self.order_management['order_tracker'][associated_buy_order_id]
                        print(f"Removed associated buy order {associated_buy_order_id} from order_tracker")



            else:
                print("No response data received from order_type_manager.process_limit_and_tp_sl_orders")

        except Exception as e:
            self.logger.error(f"Error in _process_order_fill: {e}", exc_info=True)

    async def handle_webhook(self, request: web.Request) -> web.Response:
        """Processes incoming webhook requests and delegates to WebHookManager."""
        try:
            ip_address = request.remote

            # print(f"� Request Headers: {dict(request.headers)}")  # Debug
            request_json = await request.json()
            print(f"🔹 Receiving webhook: {request_json}")

            symbol = request_json.get("pair")
            side = request_json.get("side")
            order_amount = request_json.get("order_amount")
            origin = request_json.get("origin")

            if origin == "TradingView":
                print(f"Handling webhook request from: {origin} {symbol} uuid :{request_json.get('uuid')}")

            # Ensure UUID is present
            request_json["uuid"] = request_json.get("uuid", str(uuid.uuid4()))

            # � This already returns a fully prepared `web.Response`
            response = await self.process_webhook(request_json, ip_address)

            # ✅ Log and return
            try:
                body = json.loads(response.text)
                message = body.get("message")

                if body.get("success"):
                    self.logger.order_sent(
                        f"Webhook response: {message} {symbol} side:{side} size:{order_amount}. Order originated from {origin}"
                    )
                print(json.dumps(body, indent=2))  # Optional debugging output

            except Exception as decode_error:
                self.logger.error(f"⚠️ Could not decode JSON response: {decode_error}", exc_info=True)

            return response

        except json.JSONDecodeError:
            self.logger.error("⚠️ JSON Decode Error: Invalid JSON received")
            return web.json_response({"success": False, "message": "Invalid JSON format"}, status=400)

        except Exception as e:
            self.logger.error(f"⚠️ Unhandled exception in handle_webhook: {str(e)}", exc_info=True)
            return web.json_response({"success": False, "message": "Internal server error"}, status=500)

    async def add_uuid_to_cache(self, check_uuid: str) -> None:
        """
        Add a UUID to the processed set and schedule its removal after 5 minutes.

        Args:
            check_uuid (str): The UUID to track temporarily to avoid duplicate processing.
        """
        async with self.lock:
            if check_uuid not in self.processed_uuids:
                self.processed_uuids.add(check_uuid)
                self.logger.debug(f"✅ UUID added to cache: {check_uuid}")

        def remove_uuid_later(uuid_to_remove: str):
            try:
                self.processed_uuids.remove(uuid_to_remove)
                self.logger.debug(f"� UUID automatically removed from cache: {uuid_to_remove}")
            except KeyError:
                self.logger.warning(f"⚠️ UUID not found in cache during removal: {uuid_to_remove}")

        # ⏱️ Schedule removal after 5 minutes (300 seconds)
        asyncio.get_event_loop().call_later(300, remove_uuid_later, check_uuid)

    # helper methods used in process_webhook()
    def is_ip_whitelisted(self, ip_address: str) -> bool:
        return ip_address in self.bot_config.get_whitelist()

    @staticmethod
    def is_valid_origin(origin: Optional[str]) -> bool:
        if not origin:
            return False
        return 'SIGHOOK' in origin or 'TradingView' in origin

    @staticmethod
    def is_valid_precision(precision_data: tuple) -> bool:
        if not precision_data:
            return False
        return all(p is not None for p in precision_data)

    async def process_webhook(self, request_json, ip_address) -> web.Response:
        try:
            webhook_uuid = request_json.get('uuid')
            if not webhook_uuid:
                return web.json_response({"success": False, "message": "Missing 'uuid' in request"}, status=410)

            if webhook_uuid in self.processed_uuids:
                self.logger.info(f"Duplicate webhook detected: {webhook_uuid}")
                return web.json_response({"success": False, "message": "Duplicate webhook ignored"}, status=410)

            await self.add_uuid_to_cache(webhook_uuid)

            if not request_json.get('action'):
                return web.json_response({"success": False, "message": "Missing 'action' in request"}, status=410)

            if not self.is_ip_whitelisted(ip_address):
                return web.json_response({"success": False, "message": "Unauthorized"}, status=401)

            if not WebhookListener.is_valid_origin(request_json.get('origin', '')):
                return web.json_response({"success": False, "message": "Invalid origin"}, status=403)

            # Parse trade data and fetch market/order snapshots
            trade_data = self.webhook_manager.parse_webhook_request(request_json)
            product_id = trade_data.get('trading_pair')
            asset = product_id.split('/')[0]

            combined_snapshot = await self.snapshot_manager.get_market_data_snapshot()
            market_data_snapshot = combined_snapshot["market_data"]
            order_management_snapshot = combined_snapshot["order_management"]
            usd_pairs = market_data_snapshot.get("usd_pairs_cache", {})

            precision_data = self.shared_utils_precision.fetch_precision(trade_data["trading_pair"])


            if not self.is_valid_precision(precision_data):
                return web.json_response({"success": False, "message": "Failed to fetch precision data"}, status=422)

            base_price, quote_price = await self.get_prices(trade_data, market_data_snapshot)

            asset_obj = order_management_snapshot.get("non_zero_balances", {}).get(asset)
            base_balance = getattr(asset_obj, "total_balance_crypto", 0) if asset_obj else 0

            fee_info = await self.coinbase_api.get_fee_rates()
            _, _, base_value = self.calculate_order_size(trade_data, base_price, quote_price,
                                                         precision_data, fee_info)
            if trade_data["side"] == "sell" and base_value < float(self.min_sell_value):
                return web.json_response(
                    {
                        "success": False,
                        "message": f"Insufficient balance to sell {asset} (requires {self.min_sell_value} USD)"
                    }, status=400
                )

            # Build order and place it
            source = 'Webhook'
            trigger = trade_data.get('trigger')

            order_details = await self.trade_order_manager.build_order_data(source, trigger, asset, product_id, None, fee_info)
            if order_details is None:
                return web.json_response({"success": False, "message": "Failed to build order data"}, status=422)
            order_details.trigger = trigger
            print(f'')
            print(f' 🟠️ process_webhook - Order Data: 🟠   {order_details.debug_summary(verbose=True)}  ')  # Debug
            print(f'')
            response = await self.webhook_manager.handle_action(order_details, precision_data)
            code = response.get("code", 200)

            # ✅ Convert Decimals to JSON-safe format
            return self.shared_utils_utility.safe_json_response(response, status=code)


        except Exception as e:
            self.logger.error(f"Error processing webhook: {e}", exc_info=True)
            return web.json_response({"success": False, "message": f"Internal error: {e}"}, status=500)

    async def get_prices(self, trade_data: dict, market_data_snapshot: dict) -> tuple:
        try:
            trading_pair = trade_data['trading_pair']
            asset = trade_data['base_currency']
            usd_pairs = market_data_snapshot.get('usd_pairs_cache', {})
            base_deci, quote_deci, _, _ = self.shared_utils_precision.fetch_precision(asset)


            current_prices = market_data_snapshot.get('current_prices', {})
            base_price = self.shared_utils_precision.float_to_decimal(current_prices.get(trading_pair, 0), quote_deci)

            quote_price = Decimal(1.00)
            return base_price, quote_price
        except Exception as e:
            self.logger.error(f"Error fetching prices: {e}", exc_info=True)
            return Decimal(0), Decimal(0)

    def calculate_order_size(self, trade_data: dict, base_price: Decimal, quote_price: Decimal, precision_data: tuple, fee_info: dict):
        """
        Wrapper function to call webhook_manager's calculate_order_size with correct arguments.
        """
        base_deci, quote_deci, _, _ = precision_data  # Extract precision values
        return self.webhook_manager.calculate_order_size(
            trade_data.get("side"),
            trade_data.get("order_amount"),
            trade_data.get("quote_avail_balance"),  # This is USD balance for buying
            trade_data.get("base_avail_balance", 0),  # Base asset balance for selling
            quote_price,
            base_price,
            quote_deci,
            base_deci,
            fee_info
        )

    async def periodic_save(self, interval: int = 60):
        """Periodically save shared data every `interval` seconds."""
        while True:
            try:
                # Synchronize the latest market_data and order_management
                market_data_snapshot, order_management_snapshot = await self.shared_data_manager.get_snapshots()

                # Update shared data with the latest snapshots
                await self.shared_data_manager.update_market_data(
                    new_market_data=market_data_snapshot,
                    new_order_management=order_management_snapshot
                )
                # Save the updated data
                await self.shared_data_manager.save_data()
                self.logger.debug("Periodic save completed successfully.")
            except Exception as e:
                self.logger.error(f"Error during periodic save: {e}", exc_info=True)
            await asyncio.sleep(interval)
    # test code
    async def close_resources(self):
        # No need to close the ccxt exchange instance
        print("Closing resources...")
        if self.session:
            await self.session.close()
        print("Resources closed.")

    async def create_app(self):
        """ Simplifies app creation by focusing on setting up routes only. """
        self.shared_utils_utility.log_event_loop("Webhook Server (create_app)")
        app = web.Application()
        app.router.add_post('/webhook', self.handle_webhook)
        return app


shutdown_event = asyncio.Event()  # ✅ Define the event globally

def handle_global_exception(loop, context):
    exception = context.get("exception")
    message = context.get("message", "Unhandled exception occurred")
    print(f"Global exception handler caught: {message}")
    if exception:
        print(f"Exception: {exception}")

    if hasattr(loop, 'log_manager'):
        loop.log_manager.error(f"Unhandled exception: {message}", exc_info=exception)
    else:
        print(f"Unhandled exception: {message}")

# def shutdown_handler(signal_received, frame):
#     """Gracefully shuts down the application by setting the shutdown event."""
#     print("\n� Shutting down gracefully...")
#     shutdown_event.set()  # ✅ Notify the event loop to stop

# async def initialize_market_data(listener, market_data_manager, shared_data_manager):
#     """Fetch and initialize market data safely after the event loop starts."""
#     await asyncio.sleep(1)  # Prevents race conditions
#     market_data_master, order_mgmnt_master = await market_data_manager.update_market_data(time.time())
#     listener.initialize_listener_components(market_data_master, order_mgmnt_master, shared_data_manager)

async def supervised_task(task_coro, name):
    """Handles and logs errors in background tasks."""
    try:
        await task_coro
    except Exception as e:
        print(f"❌ Task {name} encountered an error: {e}")



