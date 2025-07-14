import asyncio
import json
import time
import pandas as pd
from datetime import datetime, timedelta
from decimal import Decimal
from coinbase import jwt_generator

BATCH_SIZE = 10
TASK_TIMEOUT = 10  # per asset
TOTAL_TIMEOUT = 180  # total for the full monitor_untracked_assets cycle


class WebSocketHelper:
    """
            WebSocketHelper is responsible for managing WebSocket connections and API integrations.
            """
    def __init__(
            self, listener, websocket_manager, logger_manager, coinbase_api, profit_data_manager,
            order_type_manager, shared_utils_date_time, shared_utils_print, shared_utils_color, shared_utils_precision, shared_utils_utility,
            shared_utils_debugger, trailing_stop_manager, order_book_manager, snapshot_manager, trade_order_manager, shared_data_manager,
            market_ws_manager, passive_order_manager=None, asset_monitor=None
            ):

        # Core configurations
        self.config = listener.bot_config
        self.listener = listener
        self.shared_data_manager = shared_data_manager
        self.websocket_manager = websocket_manager
        self.market_ws_manager = market_ws_manager
        self.coinbase_api = coinbase_api
        self.logger = logger_manager

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



        # WebSocket tasks
        self.market_ws_task = None
        self.user_ws_task = None

        # Connection-related settings

        self.connection_stable = True
        self.connection_lock = asyncio.Lock()
        self.subscription_lock = asyncio.Lock()
        self.asset_semaphore = asyncio.Semaphore(5)

        self.reconnect_attempts = 0
        self.background_tasks = []

        self.market_client = None
        self.user_client = None


        # Trading parameters
        self._stop_loss = Decimal(self.config.stop_loss)
        self._min_buy_value = Decimal(self.config.min_buy_value)
        self._take_profit = Decimal(self.config.take_profit)
        self._trailing_percentage = Decimal(self.config.trailing_percentage)
        self._trailing_stop = Decimal(self.config.trailing_stop)
        self._hodl = self.config.hodl
        self._order_size_fiat = Decimal(self.config.order_size_fiat)
        self._roc_5min = Decimal(self.config._roc_5min)
        self._min_cooldown = float(self.config._min_cooldown)

        # Snapshot and data managers
        self.passive_order_manager = passive_order_manager
        self.asset_monitor = asset_monitor
        self.profit_data_manager = profit_data_manager
        self.trailing_stop_manager = trailing_stop_manager
        self.order_type_manager = order_type_manager
        self.trade_order_manager = trade_order_manager
        self.order_book_manager = order_book_manager
        self.snapshot_manager = snapshot_manager
        self.trade_recorder = self.shared_data_manager.trade_recorder

        # Utility functions
        self.shared_utils_date_time = shared_utils_date_time
        self.sharded_utils_print = shared_utils_print
        self.shared_utils_color = shared_utils_color
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
        self.user_channel_activity = {}

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
    def min_cooldown(self):
        return self._min_cooldown

    @property
    def hodl(self):
        return self._hodl

    @property
    def min_buy_value(self):
        return self._min_buy_value

    @property
    def avg_quote_volume(self):
        return Decimal(self.shared_data_manager.market_data['avg_quote_volume'])

    @property
    def roc_5min(self):
        return self._roc_5min

    @property
    def order_size(self):
        return self._order_size_fiat

    @property
    def trailing_percentage(self):
        return Decimal(self._trailing_percentage)

    @property
    def trailing_stop(self):
        return self._trailing_stop

    async def debug_websocket_health(self, timeout: int = 5):
        """Performs a deep health check on the user WebSocket connection."""
        try:
            if not self.user_ws:
                self.logger.warning("❌ user_ws is None — WebSocket never connected.")
                return

            if self.user_ws.closed:
                self.logger.warning("🔌 user_ws is closed — WebSocket may have died.")
            else:
                self.logger.info("✅ user_ws is open.")

            try:
                pong = await self.user_ws.ping()
                await asyncio.wait_for(pong, timeout=timeout)
                self.logger.info("✅ Ping to user_ws successful.")
            except Exception as e:
                self.logger.warning(f"❌ Ping to user_ws failed: {e}")

            now = time.time()
            for channel in self.user_channels:
                last_seen = self.market_channel_activity.get(channel)
                if last_seen:
                    delta = int(now - last_seen)
                    self.logger.info(f"🕒 Channel '{channel}' last active {delta}s ago ({datetime.fromtimestamp(last_seen)})")
                else:
                    self.logger.warning(f"⚠️ No activity recorded for channel '{channel}'")

        except Exception as e:
            self.logger.error(f"❌ Exception in debug_websocket_health: {e}", exc_info=True)

    def generate_ws_jwt(self):
        """Generate JWT for WebSocket authentication."""
        try:
            jwt_token = jwt_generator.build_rest_jwt(self.market_ws_url, self.websocket_api_key,
                                                     self.websocket_api_secret)

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

            # 📥 Log raw message for diagnostics
            #print(f"💚💚 Raw user WS message: {json.dumps(data, indent=2)} 💚💚 DEBUG")  # debug

            if "subscriptions" in data:
                self.logger.info(f"💚💚 User WebSocket subscription confirmed: {data['subscriptions']} 💚💚")

            if data.get("type") == "error":
                self.logger.warning(f"🚨 User WebSocket error message: {data}")
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
            channel = data.get("channel", "unknown")
            if channel:
                self.user_channel_activity[channel] = time.time()

            if channel not in self.received_channels:
                self.received_channels.add(channel)
                self.logger.info(f"✅ First message received from user channel: {channel}")

            if channel == "user":
                print(self.shared_utils_color.format(f" 💚 {self.listener.alerts.summarize_user_snapshot(data)}", self.shared_utils_color.BLUE))
                # print(f'💚  {self.listener.alerts.summarize_user_snapshot(data)}')
                await self.market_ws_manager.process_user_channel(data)
            elif channel == "heartbeats":
                self.last_heartbeat = time.time()
                self.count += 1
                if self.count >= 20:
                    heartbeat_counter = data.get("events", [{}])[0].get("heartbeat_counter")
                    print(f"💚 USER heartbeat: Counter={heartbeat_counter}")
                    self.count = 0
            elif channel == "subscriptions":
                self.logger.debug(f"� Received user channel subscription update: {json.dumps(data, indent=2)}")
            else:
                self.logger.warning(f"⚠️ Unhandled user WebSocket channel: {channel} | Message: {json.dumps(data)}")

        except Exception as e:
            self.logger.error(f"❌ Error processing user WebSocket message: {e}", exc_info=True)

    async def monitor_user_channel_activity(self, timeout: int = 60):
        """Monitor user WebSocket activity and trigger full reconnect on silence."""
        while True:
            try:
                now = time.time()
                inactivity_detected = False

                for channel in self.user_channels:
                    last_seen = self.market_channel_activity.get(channel)
                    if not last_seen:
                        self.logger.warning(f"⚠️ No message ever received from user channel '{channel}'")
                        inactivity_detected = True
                    elif now - last_seen > timeout:
                        self.logger.warning(
                            f"⏳ No message from user channel '{channel}' in {int(now - last_seen)}s — triggering reconnect"
                        )
                        inactivity_detected = True
                    else:
                        self.logger.info(
                            f"✅ User channel '{channel}' active {int(now - last_seen)}s ago"
                        )

                if inactivity_detected:
                    self.logger.warning("🔍 Running websocket health diagnostics before reconnect...")
                    await self.debug_websocket_health()  # 🔍 Run the new diagnostic
                    await self.websocket_manager.reconnect()

                await asyncio.sleep(timeout)

            except Exception as e:
                self.logger.error(f"❌ Error in monitor_user_channel_activity: {e}", exc_info=True)

    async def monitor_market_channel_activity(self, timeout: int = 60):
        """Monitor market WebSocket activity and trigger full reconnect on silence."""
        while True:
            try:
                now = time.time()
                inactivity_detected = False

                for channel in self.market_channels:
                    last_seen = self.market_channel_activity.get(channel)
                    if not last_seen:
                        self.logger.warning(f"⚠️ No message ever received from market channel '{channel}'")
                        inactivity_detected = True
                    elif now - last_seen > timeout:
                        self.logger.warning(
                            f"⏳ No message from market channel '{channel}' in {int(now - last_seen)}s — triggering reconnect"
                        )
                        inactivity_detected = True
                    else:
                        self.logger.info(
                            f"✅ Market channel '{channel}' active {int(now - last_seen)}s ago"
                        )

                if inactivity_detected:
                    await self.websocket_manager.reconnect()

                await asyncio.sleep(timeout)

            except Exception as e:
                self.logger.error(f"❌❌ Error in monitor_market_channel_activity: {e}", exc_info=True)

    async def _on_market_message_wrapper(self, message):
        """Handle raw market WebSocket message and dispatch to appropriate processor."""
        try:
            data = json.loads(message)

            if data.get("type") == "error":
                self.logger.error(f"❌ ❌ Market WebSocket Error: {data.get('message')} | Full message: {data} ❌❌")
                await self.websocket_manager.reconnect()
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
            print(f"💙 MARKET channel: {channel}")

            if channel == "ticker_batch":
                await self.market_ws_manager.process_ticker_batch_update(data)
            elif channel == "level2":
                print(f"💙 MARKET level2: {data}") #debug
                pass # debug
            elif channel == "heartbeats":
                self.last_heartbeat = time.time()
                self.count += 1
                if self.count >= 25:
                    heartbeat_counter = data.get("events", [{}])[0].get("heartbeat_counter")
                    print(f"💙 MARKET heartbeat: Counter={heartbeat_counter}")
                    self.count = 0
            elif channel == "subscriptions":
                self.logger.info(f"💙 Confirmed Market Subscriptions: {data}")
            else:
                self.logger.warning(f"⚠️ Unhandled market WebSocket channel: {channel} | Message: {json.dumps(data)}")

        except Exception as e:
            self.logger.error(f"❌ Error processing market WebSocket message: {e}", exc_info=True)

    async def subscribe_market(self):
        """Subscribe to the ticker_batch market channel for all product IDs."""
        try:
            async with (self.subscription_lock):
                self.coinbase_api.refresh_jwt_if_needed()
                if not self.market_ws:
                    self.logger.error("❌ Market WebSocket is None! Subscription aborted.")
                    return

                self.logger.info(f"� Subscribing to Market Channels: {list(self.market_channels)}")

                if not self.product_ids:
                    self.logger.warning("⚠️ No valid product IDs found. Subscription aborted.")
                    return


                for channel in self.market_channels:
                    subscription_message = {
                        "type": "subscribe",
                        "product_ids": self.product_ids,
                        "channel": channel
                    }
                    # Inject JWT ONLY for level2
                    if channel == "level2_batch":
                        # ✅ Refresh JWT before subscribing
                        jwt_token = await self.generate_jwt()
                        subscription_message["jwt"] = jwt_token
                    try:
                        await self.market_ws.send(json.dumps(subscription_message))
                        self.logger.debug(f"✅ Sent subscription for {channel} with: {self.product_ids}")
                    except Exception as e:
                        self.logger.error(f"❌ Failed to subscribe to {channel}: {e}", exc_info=True)

                #self.product_ids.update(product_ids)
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
                    self.logger.info("⚠💚💚 No new channels to subscribe. Ensure subscribed_channels was cleared after reconnect. 💚💚")
                    return

                if not hasattr(self, "user_ws") or self.user_ws is None:
                    self.logger.error("🚫 User WebSocket is not initialized. Subscription aborted.")
                    return

                # 🔐 Generate JWT token
                jwt_token = await self.generate_jwt()
                ##print(f"💚💚 JWT token generated. Length: {len(jwt_token)}💚💚 DEBUG") #debug

                # 📦 Load product IDs from cached market data
                snapshot = await self.snapshot_manager.get_market_data_snapshot()
                market_data = snapshot.get("market_data", {})
                self.product_ids = (
                    market_data.get("usd_pairs_cache", {}).get("symbol", pd.Series())
                    .str.replace("/", "-", regex=False)
                    .tolist()
                )

                if not self.product_ids:
                    self.logger.error("🚫 Subscription aborted — product_ids list is empty.")
                    return

                # print(f"📦 Subscribing with product_ids: {self.product_ids} DEBUG") #debug

                for channel in new_channels:
                    subscription_message = {
                        "type": "subscribe",
                        "product_ids": self.product_ids,
                        "channel": channel,
                        "jwt": jwt_token
                    }
                    await self.user_ws.send(json.dumps(subscription_message))
                    self.logger.info(f"💚💚 Subscribed to user channel: {channel} 💚💚")

                self.subscribed_channels.update(new_channels)

        except Exception as e:
            self.logger.error(f"❌ User subscription error: {e}", exc_info=True)

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

    def _normalize_passive_tracker_snapshot(self, snapshot):
        passive = snapshot.get("passive_tracker", {})
        if isinstance(passive, list):
            return {o["order_id"]: o for o in passive if isinstance(o, dict) and "order_id" in o}
        return dict(passive)



