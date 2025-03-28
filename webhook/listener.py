
import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from inspect import stack  # debugging
from typing import Optional

import aiohttp
import ccxt
import pandas as pd
import websockets
from aiohttp import web
from coinbase import jwt_generator

from Api_manager.api_manager import ApiManager
from Config.config_manager import CentralConfig as Config
from MarketDataManager.ohlcv_manager import OHLCVManager
from MarketDataManager.ticker_manager import TickerManager
from ProfitDataManager.profit_data_manager import ProfitDataManager
from Shared_Utils.dates_and_times import DatesAndTimes
from Shared_Utils.debugger import Debugging
from Shared_Utils.logging_manager import LoggerManager
from Shared_Utils.precision import PrecisionUtils
from Shared_Utils.print_data import PrintData
from Shared_Utils.snapshots_manager import SnapshotsManager
from Shared_Utils.utility import SharedUtility
from webhook.alert_system import AlertSystem
from webhook.trailing_stop_manager import TrailingStopManager
from webhook.webhook_manager import WebHookManager
from webhook.webhook_order_book import OrderBookManager
from webhook.webhook_order_manager import TradeOrderManager
from webhook.webhook_order_types import OrderTypeManager
from webhook.webhook_utils import TradeBotUtils
from webhook.webhook_validate_orders import OrderData
from webhook.webhook_validate_orders import ValidateOrders


class CoinbaseAPI:
    """This class is for REST API code and should nt be confused with the websocket code used in WebsocketHelper"""
    def __init__(self, session, shared_utils_utility, log_manager):
        self.config = Config()
        self.api_key = self.config.load_websocket_api_key().get('name')
        self.api_secret = self.config.load_websocket_api_key().get('signing_key')
        self.user_url = self.config.load_websocket_api_key().get('user_api_url')
        self.market_url = self.config.load_websocket_api_key().get('market_api_url')
        self.base_url = self.config.load_websocket_api_key().get('base_url')
        self.rest_url = self.config.load_websocket_api_key().get('rest_api_url')

        # fees
        self._taker_fee = Decimal(self.config.taker_fee)
        self._maker_fee = Decimal(self.config.maker_fee)

        log_config = {"log_level": logging.INFO}
        self.webhook_logger = LoggerManager(log_config)
        self.log_manager = log_manager

        self.alerts = AlertSystem(self.log_manager)
        self.shared_utils_utility = shared_utils_utility

        self.session = session

        self.api_algo = self.config.load_websocket_api_key().get('algorithm')

        self.jwt_token = None
        self.jwt_expiry = None

    @property
    def taker_fee(self):
        return self._taker_fee

    @property
    def maker_fee(self):
        return self._maker_fee

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
            self.log_manager.error(f"JWT Generation Failed: {e}", exc_info=True)
            return None

    def refresh_jwt_if_needed(self):
        """Refresh JWT only if it is close to expiration."""
        if not self.jwt_token or datetime.utcnow() >= self.jwt_expiry - timedelta(seconds=60):
            self.log_manager.info("Refreshing JWT token...")
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
                    self.log_manager.error(f"� [401] Unauthorized Order Creation: {error_message}")
                    return {"error": "Unauthorized", "details": error_message}

                elif response.status == 400:
                    self.log_manager.error(f"⚠️ [400] Bad Request: {error_message}")
                    return {"error": "Bad Request", "details": error_message}

                elif response.status == 403:
                    self.log_manager.error(f"⛔ [403] Forbidden: {error_message}")
                    return {"error": "Forbidden", "details": error_message}

                elif response.status == 429:
                    self.log_manager.warning(f"⏳ [429] Rate Limit Exceeded: {error_message}")
                    return {"error": "Rate Limit Exceeded", "details": error_message}

                elif response.status == 500:
                    self.log_manager.error(f"� [500] Internal Server Error: {error_message}")
                    return {"error": "Internal Server Error", "details": error_message}

                else:
                    self.log_manager.error(f"❌ [{response.status}] Unexpected Error: {error_message}")
                    return {"error": f"Unexpected error {response.status}", "details": error_message}

        except aiohttp.ClientError as e:
            self.log_manager.error(f"� Network Error while creating order: {e}", exc_info=True)
            return {"error": "Network Error", "details": str(e)}

        except asyncio.TimeoutError:
            self.log_manager.error("⌛ Timeout while creating order")
            return {"error": "Timeout", "details": "Order request timed out"}

        except Exception as e:
            self.log_manager.error(f"❗ Unexpected Error in create_order: {e}", exc_info=True)
            return {"error": "Unexpected Error", "details": str(e)}

    async def get_fee_rates(self):
        """
        Retrieves maker and taker fee rates from Coinbase.
        Returns:
            dict: Dictionary containing maker and taker fee rates, or error details.
        """
        try:
            request_path = '/fees'
            jwt_token = self.generate_rest_jwt('GET', request_path)
            headers = {
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {jwt_token}',
            }

            if self.session.closed:
                self.session = aiohttp.ClientSession()
            url = 'https://api.exchange.coinbase.com/fees'
            async with (self.session.get(f'{url}', headers=headers) as response):
                response_text = await response.text()

                if response.status == 200:
                    return await response.json()
                elif response.status == 500:
                    default_fees = {
                        "maker_fee": self.maker_fee,
                        "taker_fee": self.taker_fee}
                    self.log_manager.error(f"❌ Internal Server Error: default fees are being used")
                    return default_fees
                else:
                    self.log_manager.error(f"❌ Error fetching fees: {response.status} → {response_text}")
                    return {"error": f"HTTP {response.status}", "details": response_text}

        except Exception as e:
            self.log_manager.error(f"❌ Exception while retrieving fees: {e}", exc_info=True)
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
                    self.log_manager.error(f"Unauthorized request during order update: {await response.text()}")
                    return {"error": "Unauthorized"}
                else:
                    error_message = await response.text()
                    self.log_manager.error(f"Attempt {attempt + 1} failed with status {response.status}: {error_message}")
                    await asyncio.sleep(2 ** attempt)

        return {"error": "Max retries exceeded"}

class WebSocketManager:
    def __init__(self, config, coinbase_api, log_manager, websocket_helper):
        self.config = config
        self.coinbase_api = coinbase_api
        self.log_manager = log_manager
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
        except Exception as e:
            self.log_manager.error(f"Error starting WebSockets: {e}", exc_info=True)

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
            self.log_manager.info("Restarting WebSockets to ensure stability...")
            await self.websocket_helper.reconnect()

    async def connect_websocket(self, ws_url, is_user_ws=False):
        """Establish and manage a WebSocket connection."""
        while True:
            try:

                async with websockets.connect(ws_url) as ws:
                    self.log_manager.info(f"Connected to {ws_url}")
                    print(f'is_user_ws:{is_user_ws}')
                    # ✅ Assign WebSocket instance properly
                    if is_user_ws:
                        self.websocket_helper.user_ws = ws
                        await self.websocket_helper.subscribe_user()
                    else:
                        self.websocket_helper.market_ws = ws  # ✅ Assign market WebSocket
                        await asyncio.sleep(1)  # Ensure WebSocket is ready before subscribing
                        self.log_manager.info("⚡ Subscribing to Market Channels...")
                        await self.websocket_helper.subscribe_market()  # ✅ FIX: CALL HERE

                    self.log_manager.info(f"Listening on {ws_url}")

                    async for message in ws:
                        try:
                            data = json.loads(message)
                            channel = data.get("channel", "")

                            if channel == "user":
                                await self.websocket_helper._on_user_message_wrapper(message)
                            elif channel in self.websocket_helper.market_channels:
                                await self.websocket_helper._on_market_message_wrapper(message)
                            elif channel == "heartbeats":
                                await self.websocket_helper._on_heartbeat(message)
                            else:
                                self.log_manager.warning(f"Unknown message type: {message}")

                        except Exception as msg_error:
                            self.log_manager.error(f"Error processing message: {msg_error}", exc_info=True)

            except websockets.exceptions.ConnectionClosedError as e:
                self.log_manager.warning(f"WebSocket closed unexpectedly: {e}. Reconnecting...")
                await asyncio.sleep(min(2 ** self.reconnect_attempts, 60))  # Exponential backoff
                self.reconnect_attempts += 1  # Increment reconnection attempts

            except Exception as general_error:
                self.log_manager.error(f"Unexpected WebSocket error: {general_error}", exc_info=True)
                await asyncio.sleep(min(2 ** self.reconnect_attempts, 60))
                self.reconnect_attempts += 1  # Increment reconnection attempts


class WebSocketHelper:
    def __init__(
            self, listener, websocket_manager, exchange, ccxt_api, log_manager, coinbase_api,
                 profit_data_manager, order_type_manager, shared_utils_print, shared_utils_precision, shared_utils_utility,
                 shared_utils_debugger, trailing_stop_manager, order_book_manager, snapshot_manager, trade_order_manager, ohlcv_manager
                 ):
        """
        WebSocketHelper is responsible for managing WebSocket connections and API integrations.
        """
        # Core configurations
        self.config = Config()
        self.listener = listener
        self.websocket_manager = websocket_manager
        self.exchange = exchange
        self.ccxt_api = ccxt_api
        self.coinbase_api = coinbase_api
        self.log_manager = log_manager
        self.alerts = self.listener.alerts  # ✅ Assign alerts from webhook
        self.sequence_number = None  # Sequence number tracking

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
        self._take_profit = Decimal(self.config.take_profit)
        self._trailing_percentage = Decimal(self.config.trailing_percentage)
        self._trailing_stop = Decimal(self.config.trailing_stop)
        self._hodl = self.config.hodl
        self._order_size = Decimal(self.config.order_size)
        self._roc_5min = Decimal(self.config._roc_5min)


        # Snapshot and data managers
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
        self.ohlcv_manager = ohlcv_manager

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
            self.log_manager.error(f"WebSocket JWT Generation Failed: {e}", exc_info=True)
            return None

    async def generate_jwt(self):
        """Generate and refresh JWT if expired."""
        if not self.jwt_token or datetime.utcnow() >= self.jwt_expiry - timedelta(seconds=60):
            return self.generate_ws_jwt()  # ✅ Use WebSocketHelper's method

        return self.jwt_token

    # async def async_message_handler(self, message):
    #     """Example async function to process messages."""
    #     self.shared_utils_utility.log_event_loop("async_message_handler")  # debug
    #     await asyncio.sleep(1)  # Simulate async processing
    #     print(f"Processed message asynchronously: {message}")

    # async def async_initialize_market_client(self):
    #     """Wraps initialize_market_client() in an async-safe way using an executor."""
    #     self.shared_utils_utility.log_event_loop("async_initialize_market_client")  # debug
    #
    #     """Ensure that initialize_market_client runs inside the event loop."""
    #     self.shared_utils_utility.log_event_loop("async_initialize_market_client")  # debug
    #     return self.initialize_market_client()

    async def _on_user_message_wrapper(self, message):
        try:
            data = json.loads(message)
            if data.get("type") == "error" and "subscribe_market or unsubscribe required" in data.get("message", ""):
                self.log_manager.warning(f"Subscription error: {message}")
                asyncio.create_task(self._handle_subscription_error())
            else:
                asyncio.create_task(self.on_user_message(message))  # Call the existing handler
        except Exception as e:
            self.log_manager.error(f"Error in user message wrapper: {e}", exc_info=True)

    async def _on_market_message_wrapper(self, message):
        try:
            self.log_manager.debug(f"� Received market message: {message}")  # debug

            data = json.loads(message)
            if data.get("type") == "error":
                self.log_manager.error(f"❌ WebSocket Error: {data.get('message')} | Full message: {data}")
                await self.reconnect()

                return

            channel = data.get("channel")
            if channel == "ticker_batch":
                #self.log_manager.info(f"✅ Subscribed to ticker_batch: {data}")
                await self.process_ticker_batch_update(data)
            elif channel == "trades":
                await self.listener.process_trade_updates(data)
                self.log_manager.info(f"✅ Subscribed to trades: {data}")
            elif channel == "heartbeats":
                self.last_heartbeat = time.time()  # Update the heartbeat timestamp
                self.count = self.count + 1
                if self.count == 25:
                    # Extract heartbeat_counter
                    heartbeat_counter = data.get('events', [{}])[0].get('heartbeat_counter')
                    print(f"USER {channel} received : Counter:{heartbeat_counter}")  # debug
                    self.count = 0
            elif channel == "subscriptions":
                self.log_manager.info(f"✅ Confirmed Subscriptions: {data}")
            else:
                self.log_manager.warning(f"⚠️ Unhandled market message channel: {channel} ::: {data}")

        except Exception as e:
            self.log_manager.error(f"❌ Error processing market message: {e}", exc_info=True)

    async def subscribe_market(self):
        """Subscribe to Market WebSocket channels."""
        try:
            async with self.subscription_lock:
                if not self.market_ws:
                    self.log_manager.error("❌ Market WebSocket is None! Subscription aborted.")
                    return

                # Log available channels for debugging
                self.log_manager.info(f"� Market Channels Before Subscription: {self.market_channels}")

                # Fetch latest market snapshot
                snapshot = await self.snapshot_manager.get_market_data_snapshot()
                market_data = snapshot.get("market_data", {})

                product_ids = [key.replace('/', '-') for key in market_data.get('current_prices', {}).keys()] or ["BTC-USD"]

                if not product_ids:
                    self.log_manager.warning("⚠️ No valid product IDs found. Subscription aborted.")
                    return

                # Subscribe to each market channel separately
                for channel in self.market_channels:
                    subscription_message = {
                        "type": "subscribe",
                        "product_ids": list(product_ids),
                        "channel": channel
                    }

                    try:
                        await self.market_ws.send(json.dumps(subscription_message))
                        self.log_manager.info(f"✅ Subscribed to market channel: {channel} with products: {list(product_ids)}")
                    except Exception as e:
                        self.log_manager.error(f"❌ Failed to subscribe to channel {channel}: {e}", exc_info=True)

                self.product_ids.update(product_ids)
                self.subscribed_channels.update(self.market_channels)

        except Exception as e:
            self.log_manager.error(f"❌ Market subscription error: {e}", exc_info=True)

    async def subscribe_user(self):
        """Subscribe to User WebSocket channels with proper JWT authentication."""
        try:
            async with self.subscription_lock:
                self.coinbase_api.refresh_jwt_if_needed()

                new_channels = set(self.user_channels) - self.subscribed_channels
                if not new_channels:
                    self.log_manager.info("Already subscribed to all requested user channels. Skipping subscription.")
                    return

                # ✅ Ensure WebSocket is initialized before subscribing
                if not hasattr(self, "user_ws") or self.user_ws is None:
                    self.log_manager.error("User WebSocket is not initialized. Subscription aborted.")
                    return

                # ✅ Refresh JWT before subscribing
                jwt_token = await self.generate_jwt()

                # ✅ Fetch active product IDs
                snapshot = await self.snapshot_manager.get_market_data_snapshot()
                market_data = snapshot.get("market_data", {})
                product_ids = [key.replace('/', '-') for key in market_data.get('current_prices', {}).keys()] or ["BTC-USD"]

                # ✅ Subscribe to each user channel separately
                for channel in new_channels:
                    subscription_message = {
                        "type": "subscribe",
                        "product_ids": product_ids,  # ✅ Ensure correct product ID format
                        "channel": channel,  # ✅ One channel per message
                        "jwt": jwt_token  # ✅ Include JWT for authentication
                    }
                    await self.user_ws.send(json.dumps(subscription_message))
                    self.log_manager.info(f"Subscribed to user channel: {channel} with products: {product_ids}")

                self.subscribed_channels.update(new_channels)

        except Exception as e:
            self.log_manager.error(f"User subscription error: {e}", exc_info=True)

    # async def resubscribe(self):
    #     """Re-subscribe to WebSocket channels after disconnection or authentication failure."""
    #     try:
    #         self.subscribed_channels.clear()
    #         # Re-subscribe to both market and user channels
    #         await self.subscribe_market()
    #         await self.subscribe_user()
    #
    #         self.log_manager.info("Re-subscribed to WebSocket channels successfully.")
    #
    #     except Exception as e:
    #         self.log_manager.error(f"Error during re-subscription: {e}", exc_info=True)

    async def reconnect(self):
        """Reconnects both market and user WebSockets with exponential backoff."""
        if self.reconnect_attempts >= 5:
            self.log_manager.error("Max reconnect attempts reached. Manual intervention needed.")
            return

        delay = min(2 ** self.reconnect_attempts, 60)
        self.log_manager.warning(f"Reconnecting in {delay} seconds...")
        await asyncio.sleep(delay)

        try:
            # ✅ Use dedicated methods in WebSocketManager
            await self.websocket_manager.connect_market_stream()
            await self.websocket_manager.connect_user_stream()

            self.reconnect_attempts = 0
            self.log_manager.info("Reconnected successfully.")
        except Exception as e:
            self.reconnect_attempts += 1
            self.log_manager.error(f"Reconnection failed: {e}", exc_info=True)
            await self.reconnect()

    # async def subscribe_with_validation(self, client, subscription):
    #     product_ids = subscription.get('product_ids', [])
    #     channels = subscription.get('channel', [])
    #
    #     if not channels:
    #         self.log_manager.warning("Subscription attempt with empty channels. Subscription aborted.")
    #         return  # Prevent subscription with empty channels
    #
    #     # Allow empty product_ids for 'user' channel, but require for others
    #     if not product_ids and not any(channel in ['user'] for channel in channels):
    #         self.log_manager.warning(
    #             "Subscription attempt with empty product_ids for non-user/non-heartbeats channel. Subscription aborted.")
    #         return
    #
    #
    #     for channel in channels:
    #         try:
    #             subscription_message = {
    #                 "type": "subscribe_market",
    #                 "channel": [channel],
    #                 "product_ids": product_ids
    #             }
    #
    #             if channel == 'heartbeats':
    #                 # Use market_client for heartbeats without JWT
    #                 if self.market_client and self.market_client.websocket.open:
    #                     await self.market_client.websocket.send(json.dumps(subscription_message))
    #                     self.log_manager.info(
    #                         "Successfully subscribed to heartbeats channel using market_client.")
    #                 else:
    #                     self.log_manager.warning("Market client WebSocket is not open. Cannot subscribe_market to heartbeats.")
    #
    #             elif channel == 'user':
    #                 # User channel requires authentication (JWT)
    #                 if self.user_client and self.user_client.websocket.open:
    #                     await self.user_client.websocket.send(json.dumps(subscription_message))
    #                     self.log_manager.info(
    #                         f"Successfully subscribed to user channel: {channel} for products: {product_ids}")
    #                 else:
    #                     self.log_manager.warning("User client WebSocket is not open. Cannot subscribe_market to user channel.")
    #
    #             else:
    #                 # General market data subscriptions (no JWT required)
    #                 if self.market_client and self.market_client.websocket.open:
    #                     await self.market_client.websocket.send(json.dumps(subscription_message))
    #                     self.log_manager.info(
    #                         f"Successfully subscribed to market channel: {channel} for products: {product_ids}")
    #                 else:
    #                     self.log_manager.warning(
    #                         f"Market client WebSocket is not open. Cannot subscribe_market to market channel: {channel}")
    #
    #         except Exception as e:
    #             self.log_manager.error(f"Error during subscription to channel {channel}: {e}", exc_info=True)
    #             await self.retry_connection(
    #                 lambda: self.connect_and_subscribe_user() if client == self.user_client else self.connect_and_subscribe_market
    #             )
    #
    # async def monitor_heartbeat(self):
    #     """Monitor heartbeats from the WebSocket and reconnect if missing."""
    #     while True:
    #         await asyncio.sleep(30)  # ✅ Check every 30 seconds
    #
    #         if hasattr(self, "last_heartbeat") and time.time() - self.last_heartbeat > 60:
    #             self.log_manager.warning("No heartbeat detected in 60 seconds. Reconnecting...")
    #             await self.reconnect()

    async def _handle_subscription_error(self):
        """Handles the WebSocket subscription error by resubscribing."""
        try:
            # Check if WebSocket is open

            if self.user_client.websocket and self.user_client.websocket.open:
                self.log_manager.info("Attempting to resubscribe after error.", exc_info=True)

                await self.user_client.unsubscribe_all_async()

                # Re-subscribe_market to channels
                await self.user_client.subscribe_async(
                    product_ids=self.product_ids,
                    channels=['user', 'heartbeats']
                )
                self.log_manager.info(f"Resubscribed to channels")
            else:
                self.log_manager.warning("WebSocket not open, attempting reconnection.", exc_info=True)
                await self.connect_and_subscribe_user()  # Reconnect if not open

        except Exception as e:
            self.log_manager.error(f"Error during re-subscription: {e}", exc_info=True)

    async def on_user_message(self, message):
        """Process messages from the User WebSocket."""
        try:
            data = json.loads(message)

            channel = data.get("channel")

            if channel == "user":
                #print(f"DEBUG: WebSocket User Message: {json.dumps(data, indent=2)}")
                all_open_orders, has_open_order, _ = await self.refresh_open_orders()
                await self.process_user_channel(data)
            elif channel == "heartbeats":
                self.last_heartbeat = time.time()  # Update the heartbeat timestamp
                self.count = self.count + 1
                if self.count == 25:
                    # Extract heartbeat_counter
                    heartbeat_counter = data.get('events', [{}])[0].get('heartbeat_counter')
                    print(f"USER {channel} received : Counter:{heartbeat_counter}")  # debug
                    self.count = 0
            elif channel == "subscriptions":
                 self.log_manager.debug(f"Received subscriptions update :{data}")
        except Exception as e:
            self.log_manager.error(f"Error processing user message: {e}", exc_info=True)

    async def on_market_message(self, message):
        try:
            data = json.loads(message)
            if data.get("type") == "error":
                if data.get("message") == "authentication failure":
                    self.log_manager.error(
                        f"Authentication Failure Detected! | JWT: {self.jwt_token[:10]}... | Product IDs:"
                        f" {self.product_ids} | "
                        f"API Key (Last 4): {self.websocket_api_key[-4:]}"
                    )
                else:
                    self.log_manager.error(f"WebSocket Error: {data.get('message')} | Full message: {data}")
                await self.retry_connection(self.connect_and_subscribe_market)
                return  # Exit early since it's an error
            channel = data.get("channel")
            if channel == "ticker_batch":
                await self.process_ticker_batch_update(data)
            elif channel == "trades":
                await self.process_trade_updates(data)
            elif channel == "heartbeats":
                self.last_heartbeat = time.time()  # Update the heartbeat timestamp
                self.count = self.count + 1
                if self.count == 25:
                    # Extract heartbeat_counter
                    heartbeat_counter = data.get('events', [{}])[0].get('heartbeat_counter')
                    print(f"Market {channel} received : Counter:{heartbeat_counter }")  # debug
                    self.count = 0
            elif channel == "subscriptions":
                self.log_manager.debug(f"Received subscriptions update: {data}")
            else:
                self.log_manager.warning(f"Unhandled market message channel: {channel}::: {data}")
        except Exception as e:
            self.log_manager.error(f"Error processing market message: {e}", exc_info=True)

    async def process_user_channel(self, data):
        """Process real-time updates from the user channel."""
        try:
            events = data.get("events", [])
            if not isinstance(events, list):
                self.log_manager.error("Invalid structure for 'events'. Expected a list.")
                return

            profit_data_list = []
            market_data_snapshot, order_management_snapshot = await self.snapshot_manager.get_snapshots()
            spot_position = market_data_snapshot.get('spot_positions', {})
            current_prices = market_data_snapshot.get('current_prices', {})
            usd_pairs = market_data_snapshot.get('usd_pairs_cache', {})

            for event in events:
                event_type = event.get("type", "")
                orders = event.get("orders", [])

                for order in orders:
                    order_id = order.get("order_id")
                    status = order.get("status")
                    symbol = order.get("product_id")
                    order_side = order.get("order_side")

                    # ✅ Handle order status changes
                    if status == "PENDING":
                        print(f"⏳ Order {order_id} is pending...")
                    elif status == "OPEN":
                        print(f"✅ Order {order_id} is now open.")
                    elif status == "FILLED":
                        if order_side == 'sell':
                            print(f"� Order {order_id} has been FILLED! Calling handle_order_fill().")
                            response = await self.listener.handle_order_fill(order)
                            print(f"‼️ Order submitted from process_user_channel {response} webhook.py:616  ‼️")
                        print(f"� Order {order_id}")
                    elif status == "CANCELLED":
                        print(f"� Order {order_id} was cancelled.")

                    # ✅ Maintain existing logic for BTC buy orders after profitable sales
                    if order_side == 'buy':
                        continue  # Ignore buy orders, only act on sells

                    asset = symbol.split('-')[0]  # Extract asset symbol
                    avg_price = Decimal(order.get('avg_price', 0)) if order.get('avg_price') else None
                    cost_basis = Decimal(spot_position.get(asset, {}).get('cost_basis', {}).get('value', 0))
                    balance = Decimal(spot_position.get(asset, {}).get('total_balance_crypto', 0))
                    status_of_order = order.get('status')

                    filled_value = Decimal(order.get("filled_value", 0))  # Ensure safe Decimal conversion
                    required_prices = {
                        'avg_price': avg_price,
                        'cost_basis': cost_basis,
                        'balance': balance,
                        'status_of_order': status_of_order
                    }
                    base_deci,quote_deci,_,_ = self.shared_utils_precision.fetch_precision(symbol,usd_pairs)

                    profit = await self.profit_data_manager._calculate_profitability(asset, required_prices, current_prices,usd_pairs)
                    profit_value = self.shared_utils_precision.adjust_precision(base_deci, quote_deci, profit.get('profit'),'quote')
                    print(f"� Order {status} profit: {profit_value:.2f}")
                    # ✅ Buy BTC when profit is between $1.00 and $2.00
                    if status == "FILLED" and asset not in self.hodl:
                        if Decimal(1.0) < profit_value < Decimal(2.0):
                            btc_order_data = await self.trade_order_manager.build_order_data('Websocket', 'BTC/USD', symbol)
                            print(f' ⚠️ process_user_channel - Order Data: {btc_order_data.debug_summary(verbose=True)}')  # Debug
                            order_success, response_msg = await self.trade_order_manager.place_order(btc_order_data)

                            #response, tp, sl = await self.order_type_manager.process_limit_and_tp_sl_orders("WebSocket", btc_order_data)
                            print(f"DEBUG: BTC Order Response: {response_msg}")

                        elif profit_value > Decimal(2.0):
                            eth_order_data = await self.trade_order_manager.build_order_data('Websocket', 'ETH/USD', symbol)
                            print(f' ⚠️ process_user_channel - Order Data: {eth_order_data.debug_summary(verbose=True)}')  # Debug
                            order_success, response_msg = await self.trade_order_manager.place_order(eth_order_data)
                            print(f"DEBUG: ETH Order Response: {response_msg}")


        except Exception as channel_error:
            self.log_manager.error(f"Error processing user channel data: {channel_error}", exc_info=True)

    # async def handle_order_update(self, order, profit_data_list):
    #
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

    async def process_ticker_batch_update(self, data):
        """
        Process real-time ticker updates and calculate 5-minute ROC using OHLCV data.

        Args:
            data (dict): WebSocket ticker data.
        """
        try:
            events = data.get("events", [])
            timestamp = time.time()  # Current timestamp
            roc_5m = None
            for event in events:
                tickers = event.get("tickers", [])

                for ticker in tickers:
                    product_id = ticker.get("product_id")  # e.g., 'BTC-USD'
                    current_price = Decimal(ticker.get("price", "0"))
                    base_volume = Decimal(ticker.get("volume_24_h", "0"))
                    usd_volume = Decimal(base_volume * current_price)
                    if usd_volume > Decimal(1000000):
                        # ✅ Fetch last 5-minute OHLCV data from SharedDataManager
                        oldest_close_5m, latest_close_5m = await self.ohlcv_manager.fetch_last_5min_ohlcv(product_id, limit=5)
                        oldest_close_3m, latest_close_3m = await self.ohlcv_manager.fetch_last_5min_ohlcv(product_id, limit=3)

                        if oldest_close_5m and latest_close_5m:
                            # ✅ Calculate 5-minute Rate of Change (ROC)
                            roc_5m = Decimal(((latest_close_5m - oldest_close_5m) / oldest_close_5m) * 100)
                        if oldest_close_3m and latest_close_3m:

                            roc_3m = Decimal(((latest_close_3m - oldest_close_3m) / oldest_close_3m) * 100)
                            # ✅ Calculate 5-minute Rate of Change (ROC)
                            # ✅ Trigger trade if ROC exceeds threshold (e.g., 5% or 3%)
                            if roc_5m >= self.roc_5min or (roc_3m >= self.roc_5min):
                                trading_pair = product_id.replace("-", "/")
                                symbol = trading_pair.split("/")[0]
                                roc_order_data = await self.trade_order_manager.build_order_data('Websocket', symbol, trading_pair)
                                print(f' ⚠️ process_ticker_batch_update - Order Data: {roc_order_data.debug_summary(verbose=True)}')  # Debug
                                roc_order_data.source = 'Websocket'
                                order_success, response_msg = await self.trade_order_manager.place_order(roc_order_data)
                                print(f"‼️ ROC ALERT: {product_id} increased by {roc_5m:.2f}% in 5 minutes. A buy order was placed!")

        except Exception as e:
            self.log_manager.error(f"Error processing ticker_batch data: {e}", exc_info=True)

    async def process_event(self, event, profit_data_list, event_type):
        """Process specific events such as snapshots and updates."""
        print(f"Processing event: {event_type}")
        try:
            orders = event.get("orders", [])

            if event_type == "snapshot":
                # Initialize tracker with the snapshot's orders
                for order in orders:
                    await self.update_order_in_tracker(order, profit_data_list)
                profit_df = self.profit_data_manager.consolidate_profit_data(profit_data_list)
            elif event_type == "update":
                # Apply updates to the order tracker
                # determine order type buy sell cancel
                for order in orders:
                    await self.handle_order_for_order_tracker(order, profit_data_list, event_type)
        except Exception as e:
            self.log_manager.error(f"Error processing {event_type} event: {e}", exc_info=True)

    async def update_order_in_tracker(self, order, profit_data_list):
        """
        Add or update an order in the order tracker based on snapshot data.

        Args:
            order (dict): Order data to update in the tracker.
            profit_data_list (list): List to store profit data.
        """
        try:
            # ✅ Fetch necessary snapshots only once
            profit = None
            market_data_snapshot, order_management_snapshot = await self.snapshot_manager.get_snapshots()
            order_tracker = order_management_snapshot.get('order_tracker', {})
            spot_position = market_data_snapshot.get('spot_positions', {})
            current_prices = market_data_snapshot.get('current_prices', {})
            usd_pairs = market_data_snapshot.get('usd_pairs_cache', {})

            # ✅ Extract order details
            order_id = order.get('order_id')
            symbol = order.get('product_id', '').replace('-', '/')
            asset = symbol.split('/')[0]
            status = order.get('status')
            side = order.get('order_side')

            if not order_id or not symbol:
                self.log_manager.warning(f"Invalid order data: {order}")
                return

            # ✅ Fetch precision and balance details
            base_deci, quote_deci, _, _ = self.shared_utils_precision.fetch_precision(asset, usd_pairs)
            balance = Decimal(spot_position.get(asset, {}).get('total_balance_crypto', 0))
            initial_price = Decimal(order.get('initial_price', 0)) if order.get('initial_price') else None
            avg_price = Decimal(order.get('avg_price', 0)) if order.get('avg_price') else None
            limit_price = Decimal(order.get('limit_price', 0)) if order.get('limit_price') else None
            stop_price = Decimal(order.get('stop_price', 0)) if order.get('stop_price') else None
            amount = Decimal(order.get('leaves_quantity', 0)) if order.get('leaves_quantity') else None

            # ✅ Handle missing price for SELL orders
            if not initial_price and side == 'sell':
                initial_price = Decimal(spot_position.get(asset, {}).get('average_entry_price', {}).get('value', 0))
                cost_basis = Decimal(spot_position.get(asset, {}).get('cost_basis', {}).get('value', 0))
            else:
                cost_basis = None

            # ✅ Add status_of_order
            status_of_order = f"{order.get('order_type', 'UNKNOWN')}/{order.get('order_side', 'UNKNOWN')}/{order.get('status', 'UNKNOWN')}"

            # ✅ Profit Calculation for SELL Orders
            if initial_price and side == 'sell':
                required_prices = {
                    'avg_price': avg_price,
                    'cost_basis': cost_basis,
                    'balance': balance,
                    'status_of_order': status_of_order
                }
                profit = await self.profit_data_manager._calculate_profitability(asset, required_prices, current_prices,
                                                                                 usd_pairs)

                if profit:
                    profit_value = self.shared_utils_precision.adjust_precision(base_deci, quote_deci, profit.get('profit'),
                                                                                'quote')
                    if profit_value != 0.0:
                        profit_data_list.append(profit)
                        return profit_value
                    return Decimal(0.0)

            # ✅ Remove orders that are no longer active
            if status not in {"OPEN", "PENDING"}:
                order_tracker.pop(order_id, None)
                self.log_manager.info(f"Order {order_id} removed from tracker.")
                return

            # ✅ Add or update active orders
            if order_id not in order_tracker:
                order_tracker[order_id] = {
                    'symbol': symbol,
                    'initial_price': initial_price,
                    'current_price': limit_price,
                    'amount': amount,
                    'stopPrice': stop_price,
                    'average_price': avg_price,
                    'profit': profit if side == 'sell' else None,
                    'status_of_order': status_of_order
                }
                self.log_manager.info(f"New order {order_id} added to tracker.")
            else:
                existing_order = order_tracker[order_id]

                # ✅ Only update if values have changed
                if existing_order.get('stopPrice') != stop_price or existing_order.get('current_price') != limit_price:
                    order_tracker[order_id].update({
                        'stopPrice': stop_price,
                        'current_price': limit_price,
                        'status_of_order': status_of_order
                    })
                    self.log_manager.info(f"Order {order_id} updated in tracker.")

            # ✅ Debugging Output
            func_name = stack()[1].function
            self.sharded_utils_print.print_order_tracker(order_tracker, func_name)

        except asyncio.TimeoutError:
            self.log_manager.error("Timeout while waiting for market_data_lock in monitor_and_update_active_orders",
                                   exc_info=True)
            await self.handle_reconnection()
        except Exception as e:
            self.log_manager.error(f"Error updating order in tracker: {e}", exc_info=True)

    async def handle_order_for_order_tracker(self, order, profit_data_list, event_type):
        """
        Processes a single order event and adds or updates it in the order tracker as necessary.

        Args:
            order (dict): The order data to process.
            event_type (str): The type of event triggering this function (e.g., 'update').
            profit_data_list (list): List to store profit data.
        """
        try:
            # Get a reference to the master order_tracker
            market_data_snapshot, order_management_snapshot = await self.snapshot_manager.get_snapshots()

            order_tracker = order_management_snapshot.get('order_tracker', {})
            spot_position = market_data_snapshot.get('spot_positions', {})
            current_prices = market_data_snapshot.get('current_prices', {})
            usd_pairs = market_data_snapshot.get('usd_pairs', {})

            # Extract order details
            order_id = order.get('order_id')
            symbol = order.get('product_id', '').replace('-', '/')
            asset = symbol.split('/')[0]
            side = order.get('order_side')
            status = order.get('status')

            base_deci, quote_deci, _, _ = self.shared_utils_precision.fetch_precision(asset, usd_pairs)
            initial_price = Decimal(order.get('initial_price')) if order.get('initial_price') else None
            if not initial_price:
                initial_price = Decimal(spot_position.get(asset, {}).get('average_entry_price', {}).get('value', 0))

            avg_price = Decimal(order.get('avg_price')) if order.get('avg_price') else None
            if not avg_price:
                avg_price = Decimal(spot_position.get(asset, {}).get('average_entry_price', {}).get('value', 0))

            cost_basis = Decimal(spot_position.get(asset, {}).get('cost_basis', {}).get('value', 0))
            balance = Decimal(spot_position.get(asset, {}).get('total_balance_crypto', 0))

            limit_price = Decimal(order.get('limit_price', 0)) if order.get('limit_price') else None
            stop_price = Decimal(order.get('stop_price', 0)) if order.get('stop_price') else None
            amount = Decimal(order.get('leaves_quantity', 0)) if order.get('leaves_quantity') else None

            # Extracting status_of_order
            status_of_order = f"{order.get('order_type', 'UNKNOWN')}/{side}/{status}"

            required_prices = {
                'avg_price': avg_price,
                'cost_basis': cost_basis,
                'balance': balance,
                'status_of_order': status_of_order
            }

            profit = await self.profit_data_manager._calculate_profitability(asset, required_prices, current_prices, usd_pairs)
            if profit:
                profit_value = self.shared_utils_precision.adjust_precision(base_deci, quote_deci, profit.get('profit'), 'quote')
                if profit_value != 0.0:
                    profit_data_list.append(profit)
            else:
                return Decimal(0.0)

            self.profit_data_manager.consolidate_profit_data(profit_data_list)

            items_to_add = {
                order_id: {
                    'symbol': symbol,
                    'initial_price': initial_price,
                    'current_price': limit_price,
                    'amount': amount,
                    'stopPrice': stop_price,
                    'average_price': avg_price,
                    'profit': profit,
                    'status_of_order': status_of_order
                }
            }

            if not order_id or not symbol:
                self.log_manager.warning(f"Invalid order data: {order}")
                return

            if status in {"OPEN", "PENDING", "ACTIVE"}:
                # Add or update the order in the tracker
                await self.add_to_order_tracker(items_to_add, order_management_snapshot)
            elif status in {"FILLED", "CANCELED"}:
                # Remove the order from the tracker
                if order_id in order_tracker:
                    del order_tracker[order_id]
                    self.log_manager.info(f"Order {order_id} removed from tracker. Status: {status}")
                order_management_snapshot['order_tracker'] = order_tracker  # Save updated tracker
            else:
                # Handle unexpected statuses
                self.log_manager.warning(f"Unhandled order status: {status}")

        except Exception as e:
            self.log_manager.error(f"Error processing order in handle_order: {e}", exc_info=True)

    async def add_to_order_tracker(self, items, order_management):
        """
        Adds or updates an active order in the order tracker, ensuring compatibility with trailing stop logic.

        Args:
            items (dict): Order data to add or update in the tracker.
            items['order_id'] (str): Unique identifier for the order.
            items['symbol'] (str): Trading pair (e.g., 'BTC/USD').
            items['side'] (str): Order side ('buy or 'sell).
            items['stop_price'] (Decimal): Stop price for the order.
            items['avg_price'] (Decimal): Average price of the order.
            items['amount'] (Decimal): Order amount.
            items['limit_price'] (Decimal): Limit price for the order.
            order_management (dict): Reference to the master `order_management` structure.
        """
        try:
            order_id= items.get('order_id')
            symbol = items.get('symbol')
            side = items.get('side')
            limit_price = items.get('limit_price')
            amount = items.get('amount')
            stop_price = items.get('stop_price')

            order_tracker = order_management.get('order_tracker', {})

            if not order_id or not symbol:
                self.log_manager.warning(f"Invalid order data: order_id={items.get('order_id')}, symbol={symbol}")
                return
            initial_price = 0
            if side == 'buy':
                initial_price = limit_price
            if order_id not in order_tracker:
                # Add a new order to the tracker
                order_tracker[order_id] = {
                    'symbol': symbol,
                    'initial_price': initial_price,
                    'current_price': limit_price,
                    'amount': amount,
                    'trailing_stop_price': stop_price,
                    'limit_price': limit_price,
                    'profit': 0,
                    'trailing_stop_active': True  # Activate trailing stop logic
                }
                self.log_manager.info(f"Order {order_id} added to tracker: {order_tracker[order_id]}")


            else:
                # Update existing order if there are changes
                existing_order = order_tracker[order_id]
                updated_data = {}
                if (existing_order.get('info',{}).
                        get('order_configuration',{}).
                        get('stop_limit_stop_limit_gtc',{}).
                        get('stop_price') != stop_price):
                    updated_data['trailing_stop_price'] = stop_price
                if (existing_order.get('info',{}).
                        get('order_configuration',{}).
                        get('stop_limit_stop_limit_gtc',{}).
                        get('limit_price') != limit_price):
                    updated_data['limit_price'] = limit_price

                if updated_data:
                    order_tracker[order_id].update(updated_data)
                    self.log_manager.info(f"Order {order_id} updated with: {updated_data}")

            # Save updated tracker back to `order_management`
            order_management['order_tracker'] = order_tracker

        except Exception as e:
            self.log_manager.error(f"Error adding order to tracker: {e}", exc_info=True)

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
                        precision_data = self.shared_utils_precision.fetch_precision(symbol, usd_pairs)
                        base_deci, quote_deci, _, _ = precision_data

                        # ✅ Add precision values to order_data
                        order_data.quote_decimal = quote_deci
                        order_data.base_decimal = base_deci
                        order_data.product_id = symbol

                        avg_price = spot_positions.get(asset, {}).get('average_entry_price', {}).get('value', 0)
                        avg_price = Decimal(avg_price).quantize(Decimal('1.' + '0' * quote_deci))
                        balance = Decimal(spot_positions.get(asset, {}).get('total_balance_crypto', 0))
                        balance = Decimal(balance).quantize(Decimal('1.' + '0' * quote_deci))
                        current_price = current_prices.get(symbol, 0)
                        cost_basis = spot_positions.get(asset, {}).get('cost_basis', {}).get('value', 0)
                        cost_basis = Decimal(cost_basis).quantize(Decimal('1.' + '0' * quote_deci))

                        required_prices = {
                            'avg_price': avg_price,
                            'cost_basis': cost_basis,
                            'balance': balance,
                            'usd_avail': usd_avail,
                            'status': order_data.status
                        }

                        if order_data.type == 'limit' and order_data.side == 'sell':
                            order_book = await self.order_book_manager.get_order_book(order_data, symbol)

                            if order_data.price < current_price:
                                highest_bid = Decimal(max(order_book['order_book']['bids'], key=lambda x: x[0])[0])

                                # ✅ Update trailing stop with highest bid
                                await self.trailing_stop_manager.update_trailing_stop(
                                    order_id, symbol, highest_bid,
                                    order_management_snapshot["order_tracker"], required_prices, order_data
                                )
                                continue
                        elif order_data.type == 'limit' and order_data.side == 'buy':
                            pass # need to develope code that will amend limit orders and stop orders

                        profit = await self.profit_data_manager._calculate_profitability(
                            symbol, required_prices, current_prices, usd_pairs
                        )

                        if profit and profit.get('profit', 0) != 0:
                            profit_data_list.append(profit)

                        if Decimal(profit.get('   profit percent', '0').replace('%', '')) / 100 <= self.stop_loss:
                            await self.listener.handle_order_fill(order_data)

                    except Exception as e:
                        self.log_manager.error(f"Error handling tracked order {order_id}: {e}", exc_info=True)

            if profit_data_list:
                profit_df = self.profit_data_manager.consolidate_profit_data(profit_data_list)
                print(f'Profit Data Open Orders:\n{profit_df.to_string(index=True)}')

            if profit_data_list_new:
                profit_df_new = self.profit_data_manager.consolidate_profit_data(profit_data_list_new)
                print(f'Profit Data Open Orders:\n{profit_df_new.to_string(index=True)}')

            await self.monitor_untracked_assets(market_data_snapshot, order_management_snapshot)

        except Exception as e:
            self.log_manager.error(f"Error in monitor_and_update_active_orders: {e}", exc_info=True)

    async def monitor_untracked_assets(self, market_data_snapshot, order_management_snapshot):
        """Monitors untracked assets and places sell orders if they are profitable."""
        try:
            order_tracker = order_management_snapshot.get('order_tracker', {})
            spot_position = market_data_snapshot.get('spot_positions', {})
            non_zero_balances = order_management_snapshot.get('non_zero_balances', {})
            usd_pairs = market_data_snapshot.get('usd_pairs_cache', {})

            df = pd.DataFrame(usd_pairs)
            profit_data_list = []

            usd_dict = df.set_index('symbol')['price'].to_dict()

            for asset in spot_position:
                symbol = f"{asset}/USD"

                if symbol in self.currency_pairs_ignored:
                    continue
                if asset == 'ADA':
                    pass
                balance = Decimal(spot_position.get(asset, {}).get('total_balance_crypto', {}))

                # Skip tracked assets
                if ((symbol in [order_data['symbol'] for order_data in order_tracker.values()])
                        or balance < 0.03):
                    continue

                precision_data = self.shared_utils_precision.fetch_precision(symbol, usd_pairs)
                base_deci, quote_deci, _, _ = precision_data
                asset_data = non_zero_balances.get(asset)
                min_trade_amount = precision_data[2]
                if min_trade_amount > balance: # skip when balance is below exchange threshold for trading
                    continue
                if not asset_data:
                    continue  # Skip if no data is found for the asset

                initial_price = Decimal(spot_position.get(asset, {}).get('average_entry_price', {}).get('value', 0))
                avg_price = Decimal(asset_data['average_entry_price'].get('value'))
                current_price = Decimal(usd_dict.get(symbol, 0))
                current_price = self.shared_utils_precision.adjust_precision(base_deci, quote_deci, current_price,
                                                                             convert='quote')

                cost_basis = Decimal(spot_position.get(asset,{}).get('cost_basis', {}).get('value', 0))

                # ✅ Collect order status details
                status_of_order = "-"
                for order_id, order in order_tracker.items():
                    if order.get('symbol') == symbol:
                        order_info = order.get('info', {})
                        status_of_order = f"{order_info.get('order_type', 'UNKNOWN')}/" \
                                          f"{order_info.get('side', 'UNKNOWN')}/" \
                                          f"{order_info.get('status', 'UNKNOWN')}"
                        break  # Stop after finding the first matching order

                # Add `status_of_order` to required_prices
                required_prices = {
                    'avg_price': avg_price,
                    'cost_basis': cost_basis,
                    'balance': balance,
                    'status_of_order': status_of_order  # ✅ Added status_of_order
                }

                # ✅ Calculate profitability
                profit = await self.profit_data_manager._calculate_profitability(symbol, required_prices, usd_dict, usd_pairs)
                order_data_updated = await self.trade_order_manager.build_order_data('Websocket', asset, symbol)
                print(f' ⚠️ monitor_untracked_assets - Order Data: {order_data_updated.debug_summary(verbose=True)}')  # Debug
                if profit:
                    profit_value = self.shared_utils_precision.adjust_precision(base_deci, quote_deci, profit.get('profit'),'quote')
                    if profit_value != 0.0:
                        profit_data_list.append(profit)

                    profit_percent_str = profit.get('   profit percent', '0%').strip().replace('%', '')
                    profit_percent_decimal = Decimal(profit_percent_str) / Decimal(100)  # Convert to decimal

                    if profit_percent_decimal >= self.take_profit and asset not in self.hodl:
                        if profit_percent_decimal > self.trailing_percentage:
                            order_book = await self.order_book_manager.get_order_book(order_data_updated)
                            order_success, response_msg = await self.trade_order_manager.place_order(order_data_updated, precision_data)
                            print(f' ⚠️ monitor_untracked_assets - Order Data: {order_data_updated.debug_summary(verbose=True)}')  # Debug
                            #await self.order_type_manager.process_limit_and_tp_sl_orders("WebSocket", order_data_updated)
                    elif profit_percent_decimal >= Decimal(0.0) and asset not in self.hodl:
                        pass

                    elif current_price * profit_percent_decimal < ((1 + self.stop_loss ) *  avg_price) and asset not in self.hodl:
                        order_success, response_msg = await self.trade_order_manager.place_order(order_data_updated, precision_data)
                        #response, tp, sl = await self.order_type_manager.process_limit_and_tp_sl_orders("WebSocket", order_data_updated)

                else:
                    print(f"Placing limit order for untracked asset {asset}")
                    self.sharded_utils_print.print_order_tracker(order_tracker)
                    if order_data_updated.get('usd_available') > self.order_size and order_data_updated.get('side') == 'buy':
                        response_msg = await self.order_type_manager.place_limit_order(order_data_updated)
                        print(f"DEBUG: Untracked Asset Order Response: {response_msg}")

            profit_df = self.profit_data_manager.consolidate_profit_data(profit_data_list)
            print(f'Profit Data Portfolio:')
            print(profit_df.to_string(index=True))

            await asyncio.sleep(15)  # Run every 15 seconds

        except Exception as e:
            self.log_manager.error(f"Error monitoring untracked assets: {e}", exc_info=True)

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
            self.shared_utils_utility.log_event_loop("refresh_open_orders") #debug
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
                    print(f"� Removing obsolete order: {obsolete_order_id}")
                    del order_tracker_master[obsolete_order_id]

            # ✅ Update order tracker with new API data
            for order in all_open_orders:
                order_id = order.get('id')
                if order_id:
                    order_tracker_master[order_id] = order  # ✅ Ensure latest data is stored

            # ✅ Save updated tracker
            self.listener.order_management['order_tracker'] = order_tracker_master

            # ✅ Check if there is an open order for the specific `trading_pair`
            has_open_order = any(order['symbol'] == trading_pair for order in all_open_orders) if trading_pair else bool(
                all_open_orders)

            return pd.DataFrame(all_open_orders), has_open_order, order_tracker_master

        except Exception as e:
            self.log_manager.error(f"Failed to refresh open orders: {e}", exc_info=True)
            return pd.DataFrame(), False, self.listener.order_management['order_tracker']

class WebhookListener:
    """The WebhookListener class is the central orchestrator of the bot,
    handling market data updates, order management, and webhooks."""

    _exchange_instance_count = 0

    def __init__(self, bot_config, shared_data_manager, database_session_manager, logger_manager, session, market_manager, market_data_manager):
        self.bot_config = bot_config
        if not hasattr(self.bot_config, 'rest_client') or not self.bot_config.rest_client:
            print("REST client is not initialized. Initializing now...")
            self.bot_config.initialize_rest_client()
        # Assign the REST client and portfolio UUID
        self.rest_client = self.bot_config.rest_client
        self.min_sell_value = float(self.bot_config.min_sell_value)
        self.portfolio_uuid = self.bot_config.portfolio_uuid
        self.session = session  # ✅ Store session passed from run_app
        self.cb_api = self.bot_config.load_webhook_api_key()

        self.order_management = {'order_tracker': {}}
        self.shared_data_manager = shared_data_manager
        self.market_manager = market_manager
        self.market_data_manager = market_data_manager
        self.log_manager = logger_manager
        self.webhook_manager = self.ticker_manager = self.utility = None  # Initialize webhook manager properly
        self.ohlcv_manager = None
        self.processed_uuids = set()

        # Core Utilites
        self.shared_utils_precision = PrecisionUtils.get_instance(self.log_manager,None)
        self.shared_utils_print = PrintData.get_instance(self.log_manager)
        self.shared_utiles_data_time = DatesAndTimes.get_instance(self.log_manager)
        self.shared_utils_utility = SharedUtility.get_instance(self.log_manager)
        self.shared_utils_debugger = Debugging()

        #  Setup CCXT Exchange
        self.exchange = self.setup_exchange()
        self.coinbase_api = CoinbaseAPI(self.session, self.shared_utils_utility, self.log_manager)
        self.alerts = AlertSystem(self.log_manager)
        self.ccxt_api = ApiManager.get_instance(self.exchange, self.log_manager, self.alerts)

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
            log_manager=self.log_manager,
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
            ohlcv_manager=None

        )


        self.websocket_manager = WebSocketManager(self.bot_config, self.ccxt_api, self.log_manager, self.websocket_helper)

        self.websocket_helper.websocket_manager = self.websocket_manager

        self.coinbase_api = CoinbaseAPI(self.session, self.shared_utils_utility, self.log_manager)

        self.snapshot_manager = SnapshotsManager.get_instance(shared_data_manager, self.log_manager)

        # Instantiation of ....
        self.utility = TradeBotUtils.get_instance(self.log_manager, self.coinbase_api, self.exchange,
                                                  self.ccxt_api, self.alerts)


        self.ticker_manager = None

        self.profit_data_manager = ProfitDataManager.get_instance(self.shared_utils_precision, self.shared_utils_print,
                                                                  self.log_manager)

        self.order_book_manager = OrderBookManager.get_instance(self.exchange, self.shared_utils_precision, self.log_manager,
                                                   self.ccxt_api)

        self.validate = ValidateOrders.get_instance(self.log_manager, self.order_book_manager, self.shared_utils_precision)

        self.order_type_manager = OrderTypeManager.get_instance(
            coinbase_api=self.coinbase_api,
            exchange_client=self.exchange,
            shared_utils_precision=self.shared_utils_precision,
            shared_utils_utility=self.shared_utils_utility,
            validate=self.validate,
            logmanager=self.log_manager,
            alerts=self.alerts,
            ccxt_api=self.ccxt_api,
            order_book_manager=self.order_book_manager,
            websocket_helper=None, #Placeholder for self.websocket_helper,
            session=self.session
        )

        # place holder websocket_helper
        self.market_data = {}
        self.market_data_lock = asyncio.Lock()

        self.trailing_stop_manager = TrailingStopManager.get_instance(self.log_manager, self.order_type_manager,
                                                         self.shared_utils_precision, self.market_data, self.coinbase_api)

        self.trade_order_manager = TradeOrderManager.get_instance(
            coinbase_api=self.coinbase_api,
            exchange_client=self.exchange,
            shared_utils_precision=self.shared_utils_precision,
            shared_utils_utility=self.shared_utils_utility,
            validate=self.validate,
            logmanager=self.log_manager,
            alerts=self.alerts,
            ccxt_api=self.ccxt_api,
            order_book_manager=self.order_book_manager,
            order_types=self.order_type_manager,
            websocket_helper=self.websocket_helper,
            session=self.coinbase_api.session,
            market_data=self.market_data,
            profit_manager=self.profit_data_manager
        )

        #Assign WebSocketHelper to Other Managers
        self.trade_order_manager.websocket_helper = self.websocket_helper
        self.order_type_manager.websocket_helper = self.websocket_helper

        self.webhook_manager = WebHookManager.get_instance(
            logmanager=self.log_manager,
            shared_utils_precision=self.shared_utils_precision,
            trade_order_manager=self.trade_order_manager,
            alerts=self.alerts,
            session=self.session
        )

        self.websocket_helper = WebSocketHelper(
            self, self.websocket_manager, self.exchange, self.ccxt_api, self.log_manager,
            self.coinbase_api, self.profit_data_manager, self.order_type_manager,
            self.shared_utils_print, self.shared_utils_precision, self.shared_utils_utility, self.shared_utils_debugger,
            self.trailing_stop_manager, self.order_book_manager, self.snapshot_manager, self.trade_order_manager,None
        )

    async def async_init(self):
        """Initialize async components after __init__."""
        self.ohlcv_manager = await OHLCVManager.get_instance(self.exchange, self.ccxt_api, self.log_manager, self.shared_utiles_data_time,
                                                             self.market_manager)
        self.ticker_manager = await TickerManager.get_instance(self.shared_utils_debugger, self.shared_utils_print, self.log_manager,
            self.rest_client, self.portfolio_uuid, self.exchange, self.ccxt_api
            )

    def setup_exchange(self):
        self.exchange = getattr(ccxt, 'coinbase')
        WebhookListener._exchange_instance_count += 1
        print(f"Exchange instance created. Total instances: {WebhookListener._exchange_instance_count}")  # debug
        return self.exchange(
            {
                'apiKey': self.cb_api.get('name'),
                'secret': self.cb_api.get('privateKey'),
                'enableRateLimit': True,
                'verbose': False
            }
        )

    def initialize_components(self, market_data_master, order_mgmnt_master, shared_data_manager):
        self.shared_data_manager = shared_data_manager
        self.market_data = market_data_master  # Store updated market data
        self.order_management = order_mgmnt_master  # Store updated order management data
        self.shared_data_manager.market_data = market_data_master
        self.shared_data_manager.order_management = order_mgmnt_master
        self.websocket_helper.market_data = market_data_master
        self.websocket_helper.order_management = order_mgmnt_master
        self.ticker_manager.market_data = market_data_master
        self.ticker_manager.order_management = order_mgmnt_master
        self.profit_data_manager.market_data = market_data_master
        self.profit_data_manager.order_management = order_mgmnt_master
        self.utility.market_data = market_data_master
        self.utility.order_management = order_mgmnt_master
        self.webhook_manager.market_data = market_data_master
        self.webhook_manager.order_management = order_mgmnt_master
        self.trade_order_manager.market_data = market_data_master
        self.trade_order_manager.order_management = order_mgmnt_master
        self.order_type_manager.market_data = market_data_master
        self.order_type_manager.order_management = order_mgmnt_master
        self.order_book_manager.market_data = market_data_master
        self.order_book_manager.order_management = order_mgmnt_master
        self.validate.market_data = market_data_master
        self.validate.order_management = order_mgmnt_master
        self.shared_utils_precision.market_data = market_data_master
        self.shared_utils_precision.order_management = order_mgmnt_master
        self.shared_data_manager.market_data = market_data_master
        self.shared_data_manager.order_management = order_mgmnt_master


    async def refresh_market_data(self):
        """Refresh market_data and manage orders periodically."""
        while True:
            try:
                # Fetch new market data
                new_market_data, new_order_management = await self.market_data_manager.update_market_data(time.time())

                # Ensure fetched data is valid before proceeding
                if not new_market_data:
                    self.log_manager.error("❌ new_market_data is empty! Skipping update.")
                    await asyncio.sleep(60)  # Wait before retrying
                    continue

                if not new_order_management:
                    self.log_manager.error("❌ new_order_management is empty! Skipping update.")
                    await asyncio.sleep(60)
                    continue

                # Update shared state via SharedDataManager
                await self.shared_data_manager.update_market_data(new_market_data, new_order_management)

                # Refresh open orders and get the updated order_tracker
                _, _, updated_order_tracker = await self.websocket_helper.refresh_open_orders()

                # Reflect the updated order_tracker in the shared state
                if updated_order_tracker:
                    new_order_management['order_tracker'] = updated_order_tracker
                    await self.shared_data_manager.update_market_data(new_market_data, new_order_management)

                # Monitor and update active orders
                await self.websocket_helper.monitor_and_update_active_orders(new_market_data, new_order_management)

            except Exception as e:
                self.log_manager.error(f"❌ Error refreshing market_data: {e}", exc_info=True)

            # Sleep before next update
            await asyncio.sleep(60)

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
            elif websocket_order_data.type == 'Limit':
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
            self.spot_info = self.market_data.get('spot_positions',{})
            print(f"handle_order_fill started order_tracker:")
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
            base_deci, quote_deci, _, _ = self.shared_utils_precision.fetch_precision(symbol, self.usd_pairs)
            if websocket_order_data.price:
                websocket_order_data.price = self.shared_utils_precision.adjust_precision(
                    base_deci, quote_deci, websocket_order_data.price, 'quote'
                )
            if websocket_order_data.order_amount:
                websocket_order_data.order_amount = self.shared_utils_precision.adjust_precision(
                    base_deci, quote_deci, websocket_order_data.order_amount, 'base'
                )
            product_id = symbol.replace('/', '-')
            order_data = await self.trade_order_manager.build_order_data('Websocket', asset, product_id)
            print(f' ⚠️ handle_order_fill - Order Data: {order_data.debug_summary(verbose=True)}')  # Debug

            await self._process_order_fill('WebSocket', order_data)
        except Exception as e:
            print(f'websocket_msg:{websocket_order_data}')
            self.log_manager.error(f"Error in handle_order_fill: {e} {websocket_order_data}", exc_info=True)

    async def _process_order_fill(self, source, order_data: OrderData):
        """
        Process an order fill and place a corresponding trailing stop order.

        Args:
            order_data (dict): Details of the filled order, including symbol, price, and size.
        """
        print(f"Processing order fill: {order_data}")
        try:
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
                    associated_buy_order_id = order_data['initial_order_id']
                    if associated_buy_order_id in self.order_management['order_tracker']:
                        del self.order_management['order_tracker'][associated_buy_order_id]
                        print(f"Removed associated buy order {associated_buy_order_id} from order_tracker")



            else:
                print("No response data received from order_type_manager.process_limit_and_tp_sl_orders")

        except Exception as e:
            self.log_manager.error(f"Error in _process_order_fill: {e}", exc_info=True)

    async def handle_webhook(self, request: web.Request) -> web.Response:
        """ Processes incoming webhook requests and delegates to WebHookManager. """
        try:
            ip_address = request.remote

            # Print request headers for debugging
            print(f"� Request Headers: {dict(request.headers)}")  # Debugging line

            request_json = await request.json()
            print(f"✅ Receiving webhook: {request_json}")  # Debug

            symbol = request_json.get('pair')
            side = request_json.get('side')
            order_amount = request_json.get('order_amount')
            price = request_json.get('limit_price')
            origin = request_json.get('origin')

            if origin == 'TradingView':
                print(f"Handling webhook request from: {origin} {symbol} uuid :{request_json.get('uuid')}")  # Debug

            # Add UUID if missing from TradingView webhook
            request_json['uuid'] = request_json.get('uuid', str(uuid.uuid4()))

            response = await self.process_webhook(request_json, ip_address)

            # Convert response to JSON manually
            response_text = response.text  # Get raw response text
            response_json = json.loads(response_text)  # Parse JSON
            message = response_json.get('message')

            if response_json.get('success'):
                self.log_manager.order_sent(
                    f"Webhook response: {message} {symbol} side:{side} size:{order_amount}. Order originated from {origin}"
                )
            print(f'{response_text}')
            return response

        except json.JSONDecodeError:
            self.log_manager.error("� JSON Decode Error: Invalid JSON received")
            return web.json_response({"success": False, "message": "Invalid JSON format"}, status=400)

        except Exception as e:
            self.log_manager.error(f"� Unhandled exception in handle_webhook: {str(e)}", exc_info=True)
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
                self.log_manager.debug(f"✅ UUID added to cache: {check_uuid}")

        def remove_uuid_later(uuid_to_remove: str):
            try:
                self.processed_uuids.remove(uuid_to_remove)
                self.log_manager.debug(f"� UUID automatically removed from cache: {uuid_to_remove}")
            except KeyError:
                self.log_manager.warning(f"⚠️ UUID not found in cache during removal: {uuid_to_remove}")

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
                self.log_manager.info(f"Duplicate webhook detected: {webhook_uuid}")
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

            precision_data = self.shared_utils_precision.fetch_precision(trade_data["trading_pair"], usd_pairs)
            if not self.is_valid_precision(precision_data):
                return web.json_response({"success": False, "message": "Failed to fetch precision data"}, status=422)

            base_price, quote_price = await self.get_prices(trade_data, market_data_snapshot)

            asset_obj = order_management_snapshot.get("non_zero_balances", {}).get(asset)
            base_balance = getattr(asset_obj, "total_balance_crypto", 0) if asset_obj else 0

            _, _, base_value = self.calculate_order_size(trade_data, base_price, quote_price, precision_data)
            if trade_data["side"] == "sell" and base_value < float(self.min_sell_value):
                return web.json_response(
                    {
                        "success": False,
                        "message": f"Insufficient balance to sell {asset} (requires {self.min_sell_value} USD)"
                    }, status=400
                )

            # Build order and place it
            order_details = await self.trade_order_manager.build_order_data("Webhook", asset, product_id)
            print(f' ⚠️ process_webhook - Order Data: {order_details.debug_summary(verbose=True)}')

            response = await self.webhook_manager.handle_action(order_details, precision_data)
            code = response.get("code", 200)
            return web.json_response(response, status=code)

        except Exception as e:
            self.log_manager.error(f"Error processing webhook: {e}", exc_info=True)
            return web.json_response({"success": False, "message": f"Internal error: {e}"}, status=500)

    async def get_prices(self, trade_data: dict, market_data_snapshot: dict) -> tuple:
        try:
            trading_pair = trade_data['trading_pair']
            asset = trade_data['base_currency']
            usd_pairs = market_data_snapshot.get('usd_pairs_cache', {})
            base_deci, quote_deci, _, _ = self.shared_utils_precision.fetch_precision(asset, usd_pairs)
            current_prices = market_data_snapshot.get('current_prices', {})
            base_price = self.shared_utils_precision.float_to_decimal(current_prices.get(trading_pair, 0), quote_deci)

            quote_price = Decimal(1.00)
            return base_price, quote_price
        except Exception as e:
            self.log_manager.error(f"Error fetching prices: {e}", exc_info=True)
            return Decimal(0), Decimal(0)

    def calculate_order_size(self, trade_data: dict, base_price: Decimal, quote_price: Decimal, precision_data: tuple):
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
            base_deci
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

                self.log_manager.debug("Periodic save completed successfully.")
            except Exception as e:
                self.log_manager.error(f"Error during periodic save: {e}", exc_info=True)
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

async def initialize_market_data(listener, market_data_manager, shared_data_manager):
    """Fetch and initialize market data safely after the event loop starts."""
    await asyncio.sleep(1)  # Prevents race conditions
    market_data_master, order_mgmnt_master = await market_data_manager.update_market_data(time.time())
    listener.initialize_components(market_data_master, order_mgmnt_master, shared_data_manager)

async def supervised_task(task_coro, name):
    """Handles and logs errors in background tasks."""
    try:
        await task_coro
    except Exception as e:
        print(f"❌ Task {name} encountered an error: {e}")



