
import asyncio
import ccxt
import aiohttp
import os
import pandas as pd
import uuid
import time
from aiohttp import web
#from inspect import stack
import json
from datetime import datetime, timedelta
from decimal import Decimal
import logging
from Shared_Utils.logging_manager import LoggerManager
from Shared_Utils.print_data import PrintData
from Shared_Utils.debugger import Debugging
from Shared_Utils.config_manager import CentralConfig as Config
from SharedDataManager.shared_data_manager import SharedDataManager
from MarketDataManager.market_data_manager import MarketDataUpdater
from MarketDataManager.ticker_manager import TickerManager
from sighook.database_session_manager import DatabaseSessionManager
from trailing_stop_manager import TrailingStopManager
from ProfitDataManager.profit_data_manager import ProfitDataManager
from Shared_Utils.precision import PrecisionUtils
from Shared_Utils.snapshots_manager import SnapshotsManager
from alert_system import AlertSystem
from coinbase import jwt_generator
from coinbase.websocket import (WSClient, WSUserClient)
from Api_manager.api_manager import ApiManager
from webhook_utils import TradeBotUtils
from webhook_validate_orders import ValidateOrders
from webhook_order_book import OrderBookManager
from webhook_order_manager import TradeOrderManager
from webhook_order_types import OrderTypeManager
from webhook_manager import WebHookManager
from inspect import stack # debugging
#from sighook.database_table_models import Base



class CoinbaseAPI:
    def __init__(self, session):
        self.config = Config()
        self.api_key = self.config.load_websocket_api_key().get('name')
        self.api_secret = self.config.load_websocket_api_key().get('signing_key')
        self.user_url = self.config.load_websocket_api_key().get('user_api_url')  # for manual use not SDK
        self.market_url = self.config.load_websocket_api_key().get('market_api_url') # for manual use not SDK
        self.base_url = self.config.load_websocket_api_key().get('base_url')
        self.rest_url = self.config.load_websocket_api_key().get('rest_api_url')
        log_config = {"log_level": logging.INFO}
        self.webhook_logger = LoggerManager(log_config)  # Assign the logger
        self.log_manager = self.webhook_logger.get_logger('webhook_logger')
        self.alerts = AlertSystem(self.log_manager)
        self.session = session  # Store the session as an attribute
        self.api_algo = self.config.load_websocket_api_key().get('algorithm')

        # Track JWT expiry
        self.jwt_token = None
        self.jwt_expiry = None

    def generate_jwt(self, method='GET', request_path='/api/v3/brokerage/orders'):
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
            self.jwt_token = self.generate_jwt()  # ✅ Only refresh if expired

    async def create_order(self, payload):
        try:
            request_path = '/api/v3/brokerage/orders'
            jwt_token = self.generate_jwt('POST', request_path)
            headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {jwt_token}'}

            async with self.session.post(f'{self.rest_url}{request_path}', headers=headers, json=payload) as response:
                if response.status == 401:
                    error_message = await response.text()
                    self.log_manager.error(f"[{response.status}] Order creation unauthorized: {error_message}")
                    return {"error": "Unauthorized"}
                elif response.status != 200:
                    error_message = await response.text()
                    self.log_manager.error(f"[{response.status}] Error: {error_message}")
                    return {"error": "Order error", "details": error_message}
                return await response.json()
        except Exception as e:
            self.log_manager.error(f"Error creating order: {e}", exc_info=True)
            return {"error": "Order creation failed", "details": error_message}

    async def update_order(self, payload, max_retries=3):
        request_path = '/api/v3/brokerage/orders/edit'
        jwt_token = self.generate_jwt('POST', request_path)
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

class WebSocketHelper:
    def __init__(self, listener, exchange, ccxt_api, log_manager, coinbase_api,
                 profit_data_manager, order_type_manager, sharded_utils_print, shared_utils_precision,
                 shared_utils_debugger, trailing_stop_manager, order_book_manager, snapshot_manager):
        """
        WebSocketHelper is responsible for managing WebSocket connections and API integrations.
        """
        # Core configurations
        self.config = Config()
        self.listener = listener
        self.exchange = exchange
        self.ccxt_api = ccxt_api
        self.coinbase_api = coinbase_api
        self.log_manager = log_manager
        self.order_type_manager = order_type_manager
        self.order_book_manager = order_book_manager  # ✅ Defined before being used
        self.shared_utils_precision = shared_utils_precision  # ✅ Defined before use
        self.validate = self.listener.validate  # ✅ Make sure it's properly assigned
        self.alerts = self.listener.alerts  # ✅ Assign alerts from listener

        # ✅ Now that everything is assigned, we can safely initialize TradeOrderManager
        self.trade_order_manager = self.listener.trade_order_manager  # ✅ Use existing instance


        self.sequence_number = None  # Sequence number tracking

        # API credentials
        self.websocket_api_key = self.config.websocket_api.get('name')
        self.websocket_api_secret = self.config.websocket_api.get('signing_key')
        self.user_url = self.config.load_websocket_api_key().get('user_api_url')  # for manual use not SDK
        self.market_url = self.config.load_websocket_api_key().get('market_api_url')  # for manual use not SDK
        self.user_channels = self.config.load_websocket_api_key().get('user_channels',{})
        self.market_channels = self.config.load_websocket_api_key().get('market_channels',{})
        self.jwt_token = self.coinbase_api.generate_jwt()
        #self.api_algo = self.config.websocket_api.get('algorithm')

        # Connection-related settings
        self.heartbeat_interval = 20
        self.heartbeat_timeout = 30
        self.reconnect_delay = 5
        self.connection_stable = True
        self.connection_lock = asyncio.Lock()
        self.subscription_lock = asyncio.Lock()

        self.reconnect_attempts = 0
        self.background_tasks = []

        # Data management
        self.market_client = None
        self.user_client = None
        self.latest_prices = {}
        self.order_tracker_lock = asyncio.Lock()
        self.previous_prices = {}  # Store last known prices for ROC calculation


        # Trading parameters
        self._stop_loss = Decimal(self.config.stop_loss)
        self._take_profit = Decimal(self.config.take_profit)
        self._trailing_percentage = Decimal(self.config.trailing_percentage)
        self._trailing_stop = Decimal(self.config.trailing_stop)
        self._hodl = self.config.hodl
        self._order_size = Decimal(self.config.order_size)
        self.roc_threshold = 1.0  # Threshold for triggering an alert

        # Snapshot and data managers
        self.profit_data_manager = profit_data_manager
        self.trailing_stop_manager = trailing_stop_manager
        self.order_book_manager = order_book_manager
        self.snapshot_manager = snapshot_manager

        # Utility functions
        self.sharded_utils_print = sharded_utils_print
        self.shared_utils_precision = shared_utils_precision
        self.shared_utils_debugger = shared_utils_debugger

        # Subscription settings
        self.api_channels = self.config.load_channels()
        self.subscribed_channels = set()
        self.product_ids = set()
        self.pending_requests = {}  # Track pending requests for query-answer protocol
        self._currency_pairs_ignored = self.config.currency_pairs_ignored
        self.count = 0

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
    def order_size(self):
        return self._order_size

    @property
    def trailing_percentage(self):
        return Decimal(self._trailing_percentage)

    @property
    def trailing_stop(self):
        return self._trailing_stop

    async def _run_market_client(self):
        """Runs WSClient in a blocking manner within an executor."""
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self.market_client.run_forever_with_exception_check)
        except Exception as e:
            self.log_manager.error(f"Market WebSocket failed: {e}", exc_info=True)

    async def connect_and_subscribe_market(self):
        try:
            if not self.market_client: #
                self.market_client = self.initialize_market_client()

            if not self.market_client.websocket or not self.market_client.websocket.open:
                self.market_client.open()  # ✅ Market client uses sync method
                self.log_manager.info("Market WebSocket connection opened.")

            snapshot = await self.snapshot_manager.get_market_data_snapshot()
            market_data = snapshot.get("market_data", {})
            product_ids = [key.replace('/', '-') for key in market_data.get('current_prices', {}).keys()] or ['BTC-USD']

            await self.subscribe(product_ids)

            # ✅ Ensure market WebSocket runs in a separate thread/task
            if not hasattr(self, "market_client_task") or self.market_client_task.done():
                self.market_client_task = asyncio.create_task(self._run_market_client())
                self.log_manager.info("Market WebSocket listener task started.")

        except Exception as e:
            self.log_manager.error(f"Error in connect_and_subscribe_market: {e}", exc_info=True)

    async def connect_and_subscribe_user(self):
        try:
            if not self.user_client: #✅
                self.user_client = self.initialize_user_client()

            if not self.user_client.websocket or not self.user_client.websocket.open:
                await self.user_client.open_async()
                self.log_manager.info("User WebSocket connection opened.")

            await self.subscribe_user(['user', 'level2', 'heartbeats'])


            #  Keep WebSocket Running
            if not hasattr(self, "user_client_task") or self.user_client_task.done():
                self.user_client_task = asyncio.create_task(self.user_client.run_forever_with_exception_check_async())
                self.log_manager.info("User WebSocket listener task started.")

        except Exception as e:
            self.log_manager.error(f"Error in connect_and_subscribe_user: {e}", exc_info=True)

    def initialize_user_client(self):
        """Using the SDK to initialize the user client."""
        self.log_manager.info('Initializing user client')

        def sync_message_handler(message):
            asyncio.create_task(self._on_user_message_wrapper(message))

        return WSUserClient(
            api_key=self.websocket_api_key,
            api_secret=self.websocket_api_secret,
            on_message=sync_message_handler,
            retry=True,
            verbose=False
        )

    def initialize_market_client(self):
        """Using the SDK to initialize the market client."""
        self.log_manager.info(f"Initializing market client:")

        def sync_message_handler(message):

            asyncio.create_task(self._on_market_message_wrapper(message))

        client = WSClient(on_message=sync_message_handler)

        print(f"Initializing market client:")

        return client

    async def _on_user_message_wrapper(self, message):
        try:
            data = json.loads(message)
            if data.get("type") == "error" and "subscribe or unsubscribe required" in data.get("message", ""):
                self.log_manager.warning(f"Subscription error: {message}")
                asyncio.create_task(self._handle_subscription_error())
            else:
                asyncio.create_task(self.on_user_message(message))
        except Exception as e:
            self.log_manager.error(f"Error in user message wrapper: {e}", exc_info=True)

    async def _on_market_message_wrapper(self, message):
        try:
            caller_function_name = stack()[1].function
            data = json.loads(message)
            if data.get("type") == "error" and "subscribe or unsubscribe required" in data.get("message", ""):
                self.log_manager.warning(f"Subscription error: {message}")
                asyncio.create_task(self._handle_subscription_error())
            else:
                asyncio.create_task(self.on_market_message(message))
        except Exception as e:
            self.log_manager.error(f"Error in market message wrapper: {e}", exc_info=True)

    async def subscribe(self, product_ids):
        try:
            async with self.subscription_lock:
                self.log_manager.debug(f"Subscribing to market with product_ids: {product_ids}")

                new_product_ids = set(product_ids) - self.product_ids
                if not new_product_ids:
                    self.log_manager.info("Already subscribed to all requested product IDs. Skipping subscription.")
                    return

                await self.market_client.subscribe_async(product_ids=product_ids, channels=["ticker_batch", "heartbeats"]) #✅
                # # Run WebSocket listener in an async task instead of blocking execution
                # asyncio.create_task(self.market_client.run_forever_with_exception_check())

                # Update tracking sets
                self.product_ids.update(new_product_ids)
                self.subscribed_channels.update(self.market_channels.keys())
                self.log_manager.info(f"Subscribed to new market channels: {new_product_ids}")

        except Exception as e:
            self.log_manager.error(f"Subscription error: {e}", exc_info=True)

    async def subscribe_user(self, channels):
        try:
            self.coinbase_api.refresh_jwt_if_needed()
            new_channels = set(channels) - self.subscribed_channels
            if not new_channels:
                self.log_manager.info("Already subscribed to all requested user channels. Skipping subscription.")
                return
            snapshot = await self.snapshot_manager.get_market_data_snapshot()
            market_data = snapshot.get("market_data", {})
            product_ids = [key.replace('/', '-') for key in market_data.get('current_prices', {}).keys()] or ["USDT-USD",
                                                                                                              "ETH-USD"]
            subscription_message = json.dumps({
                "type": "subscribe",
                'product_ids': list(self.product_ids),
                "channels": list(new_channels),  # Allow multiple channels
                "jwt": self.coinbase_api.jwt_token
            })

            self.log_manager.debug(f"User subscription message (with JWT): {subscription_message}")

            await self.user_client.subscribe_async(product_ids=product_ids, channels=list(new_channels))

            #print(f"DEBUG: WSUserClient attributes: {dir(self.user_client)}")# debug
            self.subscribed_channels.update(new_channels)
            self.log_manager.info(f"Subscribed to new user channels: {new_channels}")

            # ✅ Add heartbeats subscription
            heartbeat_message = json.dumps({"type": "subscribe", "channel": "heartbeats"})
            await self.user_client.websocket.send(heartbeat_message)
            self.log_manager.info("User WebSocket subscribed to heartbeats.")

        except Exception as e:
            self.log_manager.error(f"User subscription error: {e}", exc_info=True)

    async def resubscribe(self):
        try:
            self.subscribed_channels.clear()
            self.product_ids.clear()

            snapshot = await self.snapshot_manager.get_market_data_snapshot()
            market_data = snapshot.get("market_data", {})
            product_ids = [key.replace('/', '-') for key in market_data.get('current_prices', {}).keys()] or ['USDT-USD']

            await self.subscribe(product_ids)
            await self.subscribe_user(self.user_channels)

            self.log_manager.info("Re-subscribed after sequence mismatch or authentication failure.")
        except Exception as e:
            self.log_manager.error(f"Error during re-subscription: {e}", exc_info=True)

    async def reconnect(self):
        if self.reconnect_attempts >= 5:
            self.log_manager.error("Max reconnect attempts reached. Manual intervention needed.")
            return  # ✅ Prevent infinite recursion

        delay = min(2 ** self.reconnect_attempts, 60)  # ✅ Exponential backoff up to 60s
        self.log_manager.warning(f"Reconnecting in {delay} seconds...")
        await asyncio.sleep(delay)

        try:
            await self.connect_and_subscribe_market()
            await self.connect_and_subscribe_user()
            self.reconnect_attempts = 0  # ✅ Reset counter on success
            self.log_manager.info("Reconnected successfully.")
        except Exception as e:
            self.reconnect_attempts += 1  # ✅ Increment counter
            self.log_manager.error(f"Reconnection failed: {e}", exc_info=True)
            await self.reconnect()


    async def subscribe_with_validation(self, client, subscription):
        product_ids = subscription.get('product_ids', [])
        channels = subscription.get('channel', [])

        if not channels:
            self.log_manager.warning("Subscription attempt with empty channels. Subscription aborted.")
            return  # Prevent subscription with empty channels

        # Allow empty product_ids for 'user' channel, but require for others
        if not product_ids and not any(channel in ['user'] for channel in channels):
            self.log_manager.warning(
                "Subscription attempt with empty product_ids for non-user/non-heartbeats channel. Subscription aborted.")
            return


        for channel in channels:
            try:
                subscription_message = {
                    "type": "subscribe",
                    "channel": [channel],
                    "product_ids": product_ids
                }

                if channel == 'heartbeats':
                    # Use market_client for heartbeats without JWT
                    if self.market_client and self.market_client.websocket.open:
                        await self.market_client.websocket.send(json.dumps(subscription_message))
                        self.log_manager.info(
                            "Successfully subscribed to heartbeats channel using market_client.")
                    else:
                        self.log_manager.warning("Market client WebSocket is not open. Cannot subscribe to heartbeats.")

                elif channel == 'user':
                    # User channel requires authentication (JWT)
                    if self.user_client and self.user_client.websocket.open:
                        await self.user_client.websocket.send(json.dumps(subscription_message))
                        self.log_manager.info(
                            f"Successfully subscribed to user channel: {channel} for products: {product_ids}")
                    else:
                        self.log_manager.warning("User client WebSocket is not open. Cannot subscribe to user channel.")

                else:
                    # General market data subscriptions (no JWT required)
                    if self.market_client and self.market_client.websocket.open:
                        await self.market_client.websocket.send(json.dumps(subscription_message))
                        self.log_manager.info(
                            f"Successfully subscribed to market channel: {channel} for products: {product_ids}")
                    else:
                        self.log_manager.warning(
                            f"Market client WebSocket is not open. Cannot subscribe to market channel: {channel}")

            except Exception as e:
                self.log_manager.error(f"Error during subscription to channel {channel}: {e}", exc_info=True)
                await self.retry_connection(
                    lambda: self.connect_and_subscribe_user() if client == self.user_client else self.connect_and_subscribe_market
                )

    async def monitor_user_heartbeat(self):
        """Monitor heartbeats from the user WebSocket and reconnect if missing."""
        while True:
            await asyncio.sleep(30)  # ✅ Check every 30 seconds
            if time.time() - self.last_heartbeat > 60:  # ✅ If no heartbeat for 60+ seconds...
                self.log_manager.warning("No heartbeat detected from user WebSocket. Reconnecting...")
                await self.reconnect()

    async def monitor_heartbeat(self):
        while True:
            await asyncio.sleep(30)
            if self.sequence_number is None:
                self.log_manager.warning("No heartbeat detected. Initiating reconnection...")
                await self.reconnect()

    async def _handle_subscription_error(self):
        """Handles the WebSocket subscription error by resubscribing."""
        try:
            # Check if WebSocket is open

            if self.user_client.websocket and self.user_client.websocket.open:
                self.log_manager.info("Attempting to resubscribe after error.", exc_info=True)

                await self.user_client.unsubscribe_all_async()

                # Re-subscribe to channels
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
                            await self.listener.handle_order_fill(order)
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
                            btc_order_data = await self.trade_order_manager.build_order_data('BTC/USD', symbol)
                            response = await self.order_type_manager.process_limit_and_tp_sl_orders("WebSocket", btc_order_data)
                            print(f"DEBUG: BTC Order Response: {response}")

                        elif profit_value > Decimal(2.0):
                            eth_order_data = await self.trade_order_manager.build_order_data('ETH/USD', symbol)
                            response = await self.order_type_manager.process_limit_and_tp_sl_orders("WebSocket", eth_order_data)
                            print(f"DEBUG: ETH Order Response: {response}")

        except Exception as channel_error:
            self.log_manager.error(f"Error processing user channel data: {channel_error}", exc_info=True)

    async def handle_order_update(self, order, profit_data_list):
        """
        Handle updates to individual orders.

        Args:
            order (dict): The order data from the event.
        """
        try:
            order_tracker = self.listener.order_management.get('order_tracker', {})

            # Handle open orders
            if order.get('status') == 'OPEN':
                # Delegate updating the order to the lower-level function
                await self.update_order_in_tracker(order, profit_data_list)

            # Remove closed or canceled orders from tracker
            elif order.get('status') not in {"OPEN", None}:
                order_id = order.get('order_id')
                if order_id in order_tracker:
                    order_tracker.pop(order_id, None)
                    self.log_manager.info(f"Order {order_id} removed from tracker in real-time.")

        except Exception as order_error:
            self.log_manager.error(f"Error handling order update: {order_error}", exc_info=True)

    async def process_ticker_batch_update(self, data):
        try:
            events = data.get("events", [])
            for event in events:
                if event.get("type") == "update":
                    tickers = event.get("tickers", [])
                    for ticker in tickers:
                        product_id = ticker.get("product_id")
                        if not product_id:
                            self.log_manager.warning("Missing product_id in ticker data.")
                            continue

                        price = Decimal(ticker.get("price", "0"))

                        # Calculate ROC if we have a previous price
                        if product_id in self.previous_prices:
                            prev_price = self.previous_prices[product_id]
                            if prev_price > 0:  # Avoid division by zero
                                roc = ((price - prev_price) / prev_price) * 100

                                # Check if ROC exceeds threshold
                                if roc >= self.roc_threshold:
                                    asset = product_id.split('/')[0]
                                    roc_order_data = await self.trade_order_manager.build_order_data(asset,product_id)
                                    print(f"DEBUG: ROC Order Data: {roc_order_data}")
                                    if roc_order_data.get('usd_available') > self.order_size and roc_order_data.get(
                                            'side') == 'buy':
                                        response = await self.order_type_manager.place_limit_order(roc_order_data)
                                        if response:
                                            print(f"‼️ ROC ALERT: {product_id} changed {roc:.2f}%  a buy order was placed")
                        # Update previous price
                        self.previous_prices[product_id] = price

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
                for order_id, order_data in order_management_snapshot.get("order_tracker", {}).items():
                    try:
                        symbol = order_data["symbol"]
                        asset = symbol.split('/')[0]
                        if asset == 'ADA':
                            pass
                        # ✅ Fetch precision values for the asset
                        precision_data = self.shared_utils_precision.fetch_precision(symbol, usd_pairs)
                        base_deci, quote_deci, _, _ = precision_data



                        # ✅ Add precision values to order_data
                        order_data["quote_decimal"] = quote_deci
                        order_data["base_decimal"] = base_deci
                        order_data["product_id"] = symbol


                        avg_price = Decimal(spot_positions.get(asset, {}).get('average_entry_price', {}).get('value', 0))
                        balance = Decimal(spot_positions.get(asset, {}).get('total_balance_crypto', 0))
                        current_price = current_prices.get(symbol, 0)
                        cost_basis = Decimal(spot_positions.get(asset, {}).get('cost_basis', {}).get('value', 0))

                        required_prices = {
                            'avg_price': avg_price,
                            'cost_basis': cost_basis,
                            'balance': balance,
                            'usd_avail': usd_avail,
                            'status': order_data.get('status', 'UNKNOWN')
                        }

                        if order_data.get("type") == 'limit' and order_data.get('side') == 'sell':
                            order_book_details = await self.order_book_manager.get_order_book(order_data, symbol)

                            if order_data.get("price") < current_price:
                                highest_bid = Decimal(max(order_book_details['order_book']['bids'], key=lambda x: x[0])[0])

                                # ✅ Update trailing stop with highest bid
                                await self.trailing_stop_manager.update_trailing_stop(
                                    order_id, symbol, highest_bid,
                                    order_management_snapshot["order_tracker"], required_prices, order_data
                                )
                                continue
                        elif order_data.get("type") == 'limit' and order_data.get('side') == 'buy':
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

            await self.monitor_untracked_assets(current_prices, market_data_snapshot, order_management_snapshot)

        except Exception as e:
            self.log_manager.error(f"Error in monitor_and_update_active_orders: {e}", exc_info=True)

    async def monitor_untracked_assets(self, current_prices, market_data_snapshot, order_management_snapshot):
        """Monitors untracked assets and places sell orders if they are profitable."""
        try:
            order_tracker = order_management_snapshot.get('order_tracker', {})
            spot_position = market_data_snapshot.get('spot_positions', {})
            non_zero_balances = order_management_snapshot.get('non_zero_balances', {})
            usd_pairs = market_data_snapshot.get('usd_pairs_cache', {})

            df = pd.DataFrame(usd_pairs)
            profit_data_list = []
            profit_df = pd.DataFrame()

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
                profit = await self.profit_data_manager._calculate_profitability(symbol, required_prices, usd_dict,
                                                                                 usd_pairs)
                order_data_updated = await self.trade_order_manager.build_order_data(asset, symbol)
                if profit:
                    profit_value = self.shared_utils_precision.adjust_precision(base_deci, quote_deci, profit.get('profit'),
                                                                                'quote')
                    if profit_value != 0.0:
                        profit_data_list.append(profit)

                    profit_percent_str = profit.get('   profit percent', '0%').strip().replace('%', '')
                    profit_percent_decimal = Decimal(profit_percent_str) / Decimal(100)  # Convert to decimal

                    if profit_percent_decimal >= self.take_profit and asset not in self.hodl:
                        if profit_percent_decimal > self.trailing_percentage:
                            order_book = await self.order_book_manager.get_order_book(order_data_updated)
                            await self.order_type_manager.place_trailing_stop_order(order_book,order_data_updated, current_price)
                    elif profit_percent_decimal >= Decimal(0.0) and asset not in self.hodl:
                        pass

                    elif current_price * profit_percent_decimal < ((1 + self.stop_loss ) *  avg_price) and asset not in self.hodl:
                        response = await self.order_type_manager.process_limit_and_tp_sl_orders("WebSocket", order_data_updated)

                else:
                    print(f"Placing limit order for untracked asset {asset}")
                    self.sharded_utils_print.print_order_tracker(order_tracker)
                    if order_data_updated.get('usd_available') > self.order_size and order_data_updated.get('side') == 'buy':
                        response = await self.order_type_manager.place_limit_order(order_data_updated)
                        print(f"DEBUG: Untracked Asset Order Response: {response}")

            profit_df = self.profit_data_manager.consolidate_profit_data(profit_data_list)
            print(f'Profit Data Portfolio:')
            print(profit_df.to_string(index=True))

            await asyncio.sleep(15)  # Run every 15 seconds

        except Exception as e:
            self.log_manager.error(f"Error monitoring untracked assets: {e}", exc_info=True)

    async def refresh_open_orders(self, order_type=None, trading_pair=None, order_data=None):
        """
        Refresh open orders using the REST API, cross-check them with order_tracker,
        and remove obsolete orders from the tracker.

        Args:
            order_type (str): Order type to filter (e.g., 'limit'). If None, fetch all orders.
            trading_pair (str): Specific trading pair to check for open orders (e.g., 'BTC/USD').
            order_data (dict): Additional order data (not used in this function, but passed from caller).

        Returns:
            tuple: (DataFrame of all open orders, has_open_order (bool), updated order tracker)
        """
        try:
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
    def __init__(self, bot_config, shared_data_manager, database_session_manager, logger_manager):
        self.bot_config = bot_config
        if not hasattr(self.bot_config, 'rest_client') or not self.bot_config.rest_client:
            print("REST client is not initialized. Initializing now...")
            self.bot_config.initialize_rest_client()
        # Assign the REST client and portfolio UUID
        self.rest_client = self.bot_config.rest_client
        self.min_sell_value = float(self.bot_config.min_sell_value)
        self.portfolio_uuid = self.bot_config.portfolio_uuid
        self.session = aiohttp.ClientSession()  # Only needed for webhooks
        self.cb_api = self.bot_config.load_webhook_api_key()

        self.order_management = {'order_tracker': {}}
        self.shared_data_manager = shared_data_manager
        self.log_manager = logger_manager
        self.coinbase_api = CoinbaseAPI(self.session)
        self.webhook_manager, self.ticker_manager, self.utility = None, None, None  # Initialize webhook manager properly
        self.processed_uuids = set()
        self.shared_utils_precision = PrecisionUtils.get_instance(self.log_manager)
        MAX_CACHE_DURATION = 60 * 5  # 5 minutes
        self.lock = asyncio.Lock()



        def setup_exchange():
            self.exchange = getattr(ccxt, 'coinbase')
            WebhookListener._exchange_instance_count += 1
            print(f"Exchange instance created. Total instances: {WebhookListener._exchange_instance_count}")  # debug
            return self.exchange({
                'apiKey': self.cb_api.get('name'),
                'secret': self.cb_api.get('privateKey'),
                'enableRateLimit': True,
                'verbose': False
            })

        # Initialize ccxt exchange
        self.exchange = setup_exchange()
        #print(f'{self.exchange.features}')# debug
        self.coinbase_api = CoinbaseAPI(self.session)
        self.alerts = AlertSystem(self.log_manager)
        self.ccxt_api = ApiManager.get_instance(self.exchange, self.log_manager, self.alerts)

        self.database_session_manager = database_session_manager


        self.snapshot_manager = SnapshotsManager.get_instance(shared_data_manager, self.log_manager)

        self.shared_utils_print = PrintData.get_instance(self.log_manager)
        self.shared_utils_debugger = Debugging()

        # Instantiation of ....
        self.utility = TradeBotUtils.get_instance(self.log_manager, self.coinbase_api, self.exchange,
                                                  self.ccxt_api, self.alerts)

        self.ticker_manager = TickerManager(self.shared_utils_debugger, self.shared_utils_print, self.log_manager,
                                            self.rest_client, self.portfolio_uuid, self.exchange, self.ccxt_api)

        self.profit_data_manager = ProfitDataManager.get_instance(self.shared_utils_precision, self.shared_utils_print,
                                                                  self.log_manager)

        self.order_book_manager = OrderBookManager.get_instance(self.exchange, self.shared_utils_precision, self.log_manager,
                                                   self.ccxt_api)

        self.validate = ValidateOrders.get_instance(self.log_manager, self.order_book_manager, self.shared_utils_precision)

        self.order_type_manager = OrderTypeManager.get_instance(
            coinbase_api=self.coinbase_api,
            exchange_client=self.exchange,
            shared_utils_precision=self.shared_utils_precision,
            validate=self.validate,
            logmanager=self.log_manager,
            alerts=self.alerts,
            ccxt_api=self.ccxt_api,
            order_book_manager=self.order_book_manager,
            websocket_helper=self,
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
            validate=self.validate,
            logmanager=self.log_manager,
            alerts=self.alerts,
            ccxt_api=self.ccxt_api,
            order_book_manager=self.order_book_manager,
            order_types=self.order_type_manager,
            websocket_helper=self,
            session=self.coinbase_api.session,
            market_data=self.market_data
        )

        self.websocket_helper = WebSocketHelper(self, self.exchange, self.ccxt_api, self.log_manager,
                                                self.coinbase_api, self.profit_data_manager, self.order_type_manager,
                                                self.shared_utils_print, self.shared_utils_precision,
                                                self.shared_utils_debugger, self.trailing_stop_manager,
                                                self.order_book_manager, self.snapshot_manager)

        self.webhook_manager = WebHookManager.get_instance(
            logmanager=self.log_manager,
            shared_utils_precision=self.shared_utils_precision,
            trade_order_manager=self.trade_order_manager,
            alerts=self.alerts,
            session=self.session
        )

        self.trade_order_manager.websocket_helper = self.websocket_helper
        self.order_type_manager.websocket_helper = self.websocket_helper



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

    async def initialize(self):
        """Initialize WebSocketHelper with market data and WebSocket subscriptions."""
        self.log_manager.info('Initializing WebhookListener...  ')
        #  Start monitoring user heartbeats in the background
        asyncio.create_task(self.websocket_helper.monitor_user_heartbeat())

        try:
            self.start_time = time.time()
            await asyncio.gather(
                self.websocket_helper.connect_and_subscribe_market(),
                self.websocket_helper.connect_and_subscribe_user()
            )
            self.log_manager.info("WebSocketHelper successfully initialized.")
        except Exception as e:
            self.log_manager.error(f"Error during WebSocketHelper initialization: {e}", exc_info=True)

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

    async def handle_order_fill(self, websocket_msg):
        try:
            base_deci =websocket_msg.get('base_decimal')
            quote_deci = websocket_msg.get('quote_decimal')

            if websocket_msg.get('order_type') == 'stop_limit':
                websocket_msg['limit_price'] = self.shared_utils_precision.adjust_precision(
                    base_deci, quote_deci, websocket_msg.get('limit_price'),'quote')

                websocket_msg['stop_price'] = self.shared_utils_precision.adjust_precision(
                    base_deci, quote_deci, websocket_msg.get('stop_price'), 'quote')

                websocket_msg['avg_price'] = self.shared_utils_precision.adjust_precision(
                    base_deci, quote_deci, websocket_msg.get('avg_price'), 'quote')
            elif  websocket_msg.get('order_type') == 'Limit':
                websocket_msg['stop_price'] = self.shared_utils_precision.adjust_precision(
                    base_deci, quote_deci, websocket_msg.get('limit_price'), 'quote')
            elif websocket_msg.get('type') == 'limit':
                websocket_msg['price'] = self.shared_utils_precision.adjust_precision(base_deci, quote_deci, websocket_msg.get('price'), 'quote')
            else:
                pass


            self.usd_pairs = self.market_data.get('usd_pairs_cache', {})
            self.spot_info = self.market_data.get('spot_positions',{})
            print(f"handle_order_fill started order_tracker:")
            symbol = None
            order_id = None

            if websocket_msg.get('status') == 'FILLED':
                symbol = websocket_msg['symbol']
                print(f"Symbol: {symbol}")
                order_id = websocket_msg['order_id']
            elif websocket_msg.get('status') == 'open':
                symbol = websocket_msg['symbol'].replace('-', '/')
                print(f"Symbol: {symbol}")
                order_id = websocket_msg['id']
            else:
                pass
            asset = symbol.split('/')[0]
            base_deci, quote_deci, _, _ = self.shared_utils_precision.fetch_precision(symbol, self.usd_pairs)
            websocket_msg['price'] = self.shared_utils_precision.adjust_precision(base_deci, quote_deci, websocket_msg.get('price'), 'quote')

            websocket_msg['amount'] = self.shared_utils_precision.adjust_precision(base_deci, quote_deci, websocket_msg.get('amount'), 'base')
            order_data = {
                'initial_order_id': order_id,
                'side': 'sell',  # Creating a sell order after the buy is filled
                'base_increment': Decimal('1E-8'),
                'base_decimal': 8,
                'quote_decimal': 2,
                'base_currency': symbol.split('/')[0],
                'quote_currency': symbol.split('/')[1],
                'trading_pair': symbol,
                'formatted_time': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                'quote_price': Decimal(websocket_msg['price']),
                'quote_amount': Decimal(websocket_msg['amount']),
                'available_to_trade_crypto':self.spot_info.get(asset,{}).get('available_to_trade_crypto',Decimal(0.0)),
                'base_balance': Decimal(websocket_msg['remaining']),
                'base_price': Decimal(websocket_msg['price'])
            }

            await self._process_order_fill(order_data)
        except Exception as e:
            print(f'websocket_msg:{websocket_msg}')
            self.log_manager.error(f"Error in handle_order_fill: {e} {websocket_msg}", exc_info=True)

    async def _process_order_fill(self, order_data):
        """
        Process an order fill and place a corresponding trailing stop order.

        Args:
            order_data (dict): Details of the filled order, including symbol, price, and size.
        """
        print(f"Processing order fill: {order_data}")
        try:
            # Fetch the order book for price and size adjustments
            order_book = await self.order_book_manager.get_order_book(order_data)

            # Use TrailingStopManager to place a trailing stop order
            trailing_stop_order_id, trailing_stop_price = await self.trailing_stop_manager.place_trailing_stop(
                order_data, order_book
            )

            if trailing_stop_order_id:
                # Add the trailing stop order to the order_tracker
                self.order_management['order_tracker'][trailing_stop_order_id] = {
                    'symbol': order_data['trading_pair'],
                    'initial_price': trailing_stop_price,
                    'purchase_price': order_data['base_price'],
                    'amount': order_data['base_balance'],
                    'trailing_stop_price': trailing_stop_price,
                    'limit_price': order_data['base_price'] * Decimal('1.002')  # Example limit price adjustment
                }

                print(f"Order tracker updated with trailing stop order: {trailing_stop_order_id}")

                # Remove the associated buy order from the order_tracker
                associated_buy_order_id = order_data['initial_order_id']
                if associated_buy_order_id in self.order_management['order_tracker']:
                    del self.order_management['order_tracker'][associated_buy_order_id]
                    print(f"Removed associated buy order {associated_buy_order_id} from order_tracker")

            else:
                print(f"Failed to place trailing stop order for {order_data['trading_pair']}")

        except Exception as e:            self.log_manager.error(f"Error in _process_order_fill: {e}", exc_info=True)

    async def handle_webhook(self, request: web.Request) -> web.Response:
        """ Processes incoming webhook requests and delegates to WebHookManager. """
        try:
            ip_address = request.remote
            request_json = await request.json()
            symbol = request_json.get('pair')
            side = request_json.get('side')
            order_size = request_json.get('order_size')
            price = request_json.get('limit_price')
            origin = request_json.get('origin')
            if origin == 'TradingView':
                print(f"Handling webhook request from: {origin} {symbol} uuid :{request_json.get('uuid')}") # debug

            # Add UUID if missing from TradingView webhook
            request_json['uuid'] = request_json.get('uuid', str(uuid.uuid4()))

            response = await self.process_webhook(request_json, ip_address)

            # Convert response to JSON manually
            response_text = response.text  # Get raw response text
            response_json = json.loads(response_text)  # Parse JSON
            message = response_json.get('message')
            if response_json.get('success'):
                self.log_manager.order_sent(f"Webhook response: {message} {symbol} side:{side} size:{order_size}. Order originated from "
                                            f"{origin}")

            return response

        except Exception as e:
            self.log_manager.error(f"Unhandled exception in handle_webhook: {str(e)}", exc_info=True)
            return web.json_response({"success": False, "message": "Internal server error"}, status=500)

    async def add_uuid_to_cache(self, uuid):
        print(f"Adding uuid to cache: {uuid}")
        async with self.lock:
            if uuid not in self.processed_uuids:
                self.processed_uuids.add(uuid)

        asyncio.get_event_loop().call_later(60*5, lambda: self.processed_uuids.remove(uuid))

    # helper methods used in process_webhook()
    def is_ip_whitelisted(self, ip_address: str) -> bool:
        return ip_address in self.bot_config.get_whitelist()

    def is_valid_origin(self, origin: str) -> bool:
        return 'SIGHOOK' in origin or 'TradingView' in origin

    def is_valid_precision(self, precision_data: tuple) -> bool:
        return all(p is not None for p in precision_data)


    async def process_webhook(self, request_json, ip_address) -> web.Response:
        try:
            # Validate basic webhook structure
            webhook_uuid = request_json.get('uuid')
            if not webhook_uuid:
                return web.json_response({"success": False, "message": "Missing 'uuid' in request"}, status=400)

            if webhook_uuid in self.processed_uuids:
                self.log_manager.info(f"Duplicate webhook detected: {webhook_uuid}")
                return web.json_response({"success": True, "message": "Duplicate webhook ignored"}, status=200)

            await self.add_uuid_to_cache(webhook_uuid)

            if not request_json.get('action'):
                return web.json_response({"success": False, "message": "Missing 'action' in request"}, status=400)

            # Validate IP whitelist
            if not self.is_ip_whitelisted(ip_address):
                return web.json_response({"success": False, "message": "Unauthorized"}, status=401)

            # Validate origin
            if not self.is_valid_origin(request_json.get('origin', '')):
                return web.json_response({"success": False, "message": "Invalid content type"}, status=415)

            # Extract trade data and fetch market data snapshot
            trade_data = self.webhook_manager.parse_webhook_data(request_json)
            asset = trade_data.get('trading_pair').split('/')[0]
            combined_snapshot = await self.snapshot_manager.get_market_data_snapshot()
            market_data_snapshot = combined_snapshot["market_data"]
            order_management_snapshot = combined_snapshot["order_management"]
            usd_pairs = market_data_snapshot.get('usd_pairs_cache', {})

            # Fetch precision and prices
            precision_data = self.shared_utils_precision.fetch_precision(trade_data['trading_pair'], usd_pairs)
            if not self.is_valid_precision(precision_data):
                self.log_manager.error(f"Failed to fetch precision data for {trade_data['trading_pair']}")
                return web.json_response({"success": False, "message": 'Failed to fetch precision data'}, status=500)

            base_price, quote_price =  await self.get_prices(trade_data, market_data_snapshot)

            # fetch usd balance
            usd_balance = order_management_snapshot.get('non_zero_balances', {}).get('USD')['total_balance_crypto']
            # never owned may have a zero balance need to check all cryptos

            asset_obj = order_management_snapshot.get('non_zero_balances', {}).get(asset, None)
            base_balance = getattr(asset_obj, 'total_balance_crypto', 0) if asset_obj else 0

            # Calculate order size
            base_order_size, quote_amount = self.calculate_order_size(trade_data, base_price, quote_price,
                                                                          precision_data)
            if trade_data["side"] == 'buy' and (not base_order_size or not quote_amount):
                return web.json_response({"success": False, "message": "Invalid order size"}, status=400)
            if trade_data["side"] == 'sell' and base_balance < float(self.min_sell_value):
                return web.json_response({"success": False, "message": "Insufficient balance to sell"}, status=400)
            # Build order details
            order_details = self.build_order_details(trade_data, base_balance, base_price, quote_price, base_order_size,
                                                     quote_amount, usd_balance, precision_data)

            # Delegate action to WebHookManager
            json_response = await self.webhook_manager.handle_action(order_details, precision_data)
            await self.websocket_helper.refresh_open_orders()  # Refresh order tracker
            return web.json_response({"success": True, "message": "Action processed successfully"}, status=200)

        except Exception as e:
            self.log_manager.error(f"Error processing webhook: {e}", exc_info=True)
            return web.json_response({"success": False, "message": "Internal server error"}, status=500)


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
        base_deci, _, _, _ = precision_data
        return self.webhook_manager.calculate_order_size(
            trade_data["side"], trade_data["quote_amount"], trade_data.get("base_amount", 0), quote_price, base_price,
            base_deci
        )

    def build_order_details(self, trade_data: dict, base_balance: str, base_price: Decimal, quote_price: Decimal,
                            base_order_size: Decimal, quote_amount: Decimal, usd_balance:Decimal, precision_data: tuple) \
                            -> dict:
        base_deci, quote_deci, base_increment, quote_increment = precision_data
        return {
            'side': trade_data['side'],
            'base_increment': self.shared_utils_precision.float_to_decimal(base_increment, base_deci),
            'base_decimal': base_deci,
            'quote_decimal': quote_deci,
            'base_currency': trade_data['base_currency'],
            'quote_currency': trade_data['quote_currency'],
            'trading_pair': trade_data['trading_pair'],
            'formatted_time': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'quote_price': quote_price,
            'quote_amount': quote_amount,
            'base_balance': base_balance,
            'quote_balance': usd_balance,
            'base_price': base_price,
            'order_size': base_order_size
        }

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

        app = web.Application()
        app.router.add_post('/webhook', self.handle_webhook)
        return app

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


async def run_app(config):
    # Create the log manager
    log_config = {"log_level": logging.INFO}
    webhook_logger = LoggerManager(log_config)
    log_manager = webhook_logger.get_logger('webhook_logger')

    # Initialize the database session manager
    database_session_manager = DatabaseSessionManager(None, log_manager)

    # Create the shared data manager
    shared_data_manager = SharedDataManager.get_instance(log_manager, database_session_manager)
    shared_data_manager.market_data = {}
    shared_data_manager.order_management = {}
    await shared_data_manager.initialize()

    # Create the WebhookListener
    listener = WebhookListener(config, shared_data_manager, database_session_manager, log_manager)

    # ✅ Debugging: Confirm shared_data_manager is assigned
    assert listener.shared_data_manager is not None, "shared_data_manager was not assigned!"

    app = await listener.create_app()

    # ✅ Ensure correct event loop
    loop = asyncio.get_running_loop()
    loop.set_exception_handler(handle_global_exception)

    # Initialize MarketDataUpdater
    market_data_manager = MarketDataUpdater.get_instance(
        ticker_manager=listener.ticker_manager,
        log_manager=listener.log_manager,
    )
    listener.market_data_manager = market_data_manager

    # ✅ Run market data initialization safely
    asyncio.create_task(initialize_market_data(listener, market_data_manager, shared_data_manager))

    try:
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', config.webhook_port)
        await site.start()
        print(f'Webhook {config.program_version} is Listening on port {config.webhook_port}...')

        # ✅ Run background tasks together
        asyncio.create_task(supervised_task(listener.refresh_market_data(), "refresh_market_data"))
        asyncio.create_task(supervised_task(listener.periodic_save(), "periodic_save"))
        asyncio.create_task(
            supervised_task(listener.websocket_helper.connect_and_subscribe_user(), "connect_and_subscribe_user"))

        while True:
            await asyncio.sleep(3600)

    except Exception as e:
        print(f"run_app: Exception caught - {e}")

    finally:
        await listener.close_resources()


if __name__ == '__main__':
    os.environ['PYTHONASYNCIODEBUG'] = '0'
    logger = logging.getLogger('asyncio')
    logger.setLevel(logging.ERROR)

    get_config = Config()
    asyncio.run(run_app(get_config))


