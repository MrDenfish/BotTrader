import asyncio
import json
import time
import random
import pandas as pd

from decimal import Decimal
from coinbase import jwt_generator
from datetime import datetime, timedelta, timezone
from websockets.exceptions import ConnectionClosed, ConnectionClosedError, ConnectionClosedOK

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
            test_debug_maint, order_book_manager, snapshot_manager, trade_order_manager, shared_data_manager,
            market_ws_manager, database_session_manager, passive_order_manager=None, asset_monitor=None
            ):

        # Core configurations
        self.config = listener.bot_config
        self.listener = listener
        self.database_session_manager = database_session_manager
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
        self.test_debug_maint = test_debug_maint

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
            self.jwt_expiry = datetime.now(timezone.utc) + timedelta(minutes=5)

            return jwt_token
        except Exception as e:
            self.logger.error(f"WebSocket JWT Generation Failed: {e}", exc_info=True)
            return None

    async def generate_jwt(self):
        """Generate and refresh JWT if expired."""
        if not self.jwt_token or datetime.now(timezone.utc) >= self.jwt_expiry - timedelta(seconds=60):
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
                #print(self.shared_utils_color.format(f" 💚 {self.listener.alerts.summarize_user_snapshot(data)}", self.shared_utils_color.BLUE))
                # print(f'💚  {self.listener.alerts.summarize_user_snapshot(data)}')
                await self.market_ws_manager.process_user_channel(data)
            elif channel == "heartbeats":
                self.last_heartbeat = time.time()
                self.count += 1
                if self.count >= 50:
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
                        self.logger.debug(
                            f"✅ User channel '{channel}' active {int(now - last_seen)}s ago"
                        )

                if inactivity_detected:
                    self.logger.warning("🔍 Running websocket health diagnostics before reconnect...")
                    await self.debug_websocket_health()  # 🔍 Run the new diagnostic
                    await self.websocket_manager.force_reconnect()

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
                        self.logger.debug(
                            f"✅ Market channel '{channel}' active {int(now - last_seen)}s ago"
                        )

                if inactivity_detected:
                    await self.websocket_manager.force_reconnect()

                await asyncio.sleep(timeout)

            except Exception as e:
                self.logger.error(f"❌❌ Error in monitor_market_channel_activity: {e}", exc_info=True)

    async def _on_market_message_wrapper(self, message):
        """Handle raw market WebSocket message and dispatch to appropriate processor."""
        try:
            data = json.loads(message)

            if data.get("type") == "error":
                self.logger.error(f"❌ ❌ Market WebSocket Error: {data.get('message')} | Full message: {data} ❌❌")
                await self.websocket_manager.force_reconnect()
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

            # ✅ Maintain separate counters for channels
            if not hasattr(self, "market_channel_counters"):
                self.market_channel_counters = {}
            if channel not in self.market_channel_counters:
                self.market_channel_counters[channel] = 0

            # Increment counter for the channel
            self.market_channel_counters[channel] += 1

            # ✅ Periodic status print (every 25 messages by default)
            if self.market_channel_counters[channel] >= 25:
                print(f"💙 MARKET channel active: {channel} | Count={self.market_channel_counters[channel]}")
                self.market_channel_counters[channel] = 0

            # --- Process specific channels ---
            if channel == "ticker_batch":
                await self.market_ws_manager.process_ticker_batch_update(data)

            elif channel == "level2":
                # Debug level2 occasionally if needed
                if self.market_channel_counters[channel] == 0:
                    print(f"💙 MARKET level2 active")
                # process level2 here if implemented

            elif channel == "heartbeats":
                self.last_heartbeat = time.time()
                self.count += 1
                if self.count >= 25:
                    heartbeat_counter = data.get("events", [{}])[0].get("heartbeat_counter")
                    print(f"💙 MARKET heartbeat: Counter={heartbeat_counter}")
                    self.count = 0


            elif channel == "subscriptions":

                # Extract the subscriptions block safely

                events = data.get("events") or [{}]

                subs = (events[0].get("subscriptions") or {}) if events else {}

                ts = data.get("timestamp")

                seq = data.get("sequence_num")

                # Build a compact per-channel summary like: "heartbeats=1, ticker_batch=123"

                parts = []

                for name, value in subs.items():
                    count = len(value) if isinstance(value, list) else 1

                    parts.append(f"{name}={count}")

                summary = ", ".join(parts) if parts else "no-channels"

                # Optional: show a tiny preview of tickers (first few only)

                tickers = subs.get("ticker_batch") or []

                preview = ", ".join(tickers[:5])

                preview_str = f" · ticker_batch: {len(tickers)} symbols ({preview}{'…' if len(tickers) > 5 else ''})" if tickers else ""

                # Concise INFO line

                print(f"🟢🟢🟢 Subscriptions confirmed · {summary}{preview_str}  🟢🟢🟢")

                # Full payload only at DEBUG (for deep dives when needed)

                self.logger.debug("subscriptions payload: %r", data)

            else:
                self.logger.warning(f"⚠️ Unhandled market WebSocket channel: {channel} | Message: {json.dumps(data)}")

        except Exception as e:
            self.logger.error(f"❌ Error processing market WebSocket message: {e}", exc_info=True)

    async def subscribe_market(self) -> bool:
        """
        Subscribe to market channels. Returns True if at least one subscription
        was sent successfully; False if the WS wasn't open or every send failed.
        """
        try:
            async with self.subscription_lock:
                # ensure JWT freshness if you need it
                self.coinbase_api.refresh_jwt_if_needed()

                ws = getattr(self, "market_ws", None)
                if ws is None:
                    self.logger.error("❌ Market WebSocket is None! Subscription aborted.")
                    return False
                if getattr(ws, "closed", True):
                    self.logger.warning("⚠️ Market WebSocket is closed; deferring subscription.")
                    return False

                # build product_ids (or reuse cached if you keep it fresh elsewhere)
                if not self.product_ids:
                    snapshot = await self.snapshot_manager.get_market_data_snapshot()
                    md = snapshot.get("market_data", {})
                    self.product_ids = (
                        md.get("usd_pairs_cache", {}).get("symbol", pd.Series())
                        .str.replace("/", "-", regex=False)
                        .tolist()
                    )
                if not self.product_ids:
                    self.logger.warning("⚠️ No valid product IDs found. Subscription aborted.")
                    return False

                self.logger.info(f"📡 Subscribing to market channels: {list(self.market_channels)}")

                successes, failures = set(), set()

                for channel in self.market_channels:
                    msg = {"type": "subscribe", "product_ids": self.product_ids, "channel": channel}

                    # JWT only if your provider requires it for specific channels
                    if channel in {"level2_batch", "level2"}:
                        try:
                            jwt_token = await self.generate_jwt()
                            msg["jwt"] = jwt_token
                        except Exception as e:
                            self.logger.error(f"❌ JWT generation failed for {channel}: {e}", exc_info=True)
                            failures.add(channel)
                            continue

                    # re-check right before send to avoid TOCTOU on ws state
                    if getattr(ws, "closed", True):
                        self.logger.warning(f"⚠️ WS closed before subscribing to {channel}; will retry on reconnect.")
                        failures.add(channel)
                        continue

                    try:
                        await ws.send(json.dumps(msg))
                        self.logger.debug(f"✅ Sent subscription for {channel} ({len(self.product_ids)} products).")
                        successes.add(channel)
                    except asyncio.CancelledError:
                        self.logger.warning(f"⚠️ Subscription to {channel} cancelled (reconnect/shutdown).")
                        raise
                    except (ConnectionClosed, ConnectionClosedError, ConnectionClosedOK) as e:
                        # This is your exact "no close frame received or sent" class of error
                        self.logger.error(f"❌ Failed to subscribe to {channel}: {e}")
                        failures.add(channel)
                        # let the connect loop handle reconnection
                        return False
                    except Exception as e:
                        self.logger.error(f"❌ Failed to subscribe to {channel}: {e}", exc_info=True)
                        failures.add(channel)

                # only mark channels that actually sent OK
                if successes:
                    self.subscribed_channels.update(successes)

                self.logger.info(f"📣 Market subscribe summary → ok={sorted(successes)} fail={sorted(failures)}")
                return bool(successes)

        except Exception as e:
            self.logger.error(f"❌ Market subscription error: {e}", exc_info=True)
            return False

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


        except asyncio.CancelledError:
            self.logger.warning(f"⚠️ Subscription to {channel} cancelled (reconnect or shutdown).")
            raise
        except Exception as e:
            if "sent 1000 (OK)" in str(e):
                self.logger.warning(f"⚠️ Clean close during subscription to {channel} (normal reconnect).")
            else:
                self.logger.error(f"❌ Failed to subscribe to {channel}: {e}", exc_info=True)

    async def _handle_subscription_error(self):
        """
        Handles subscription errors by attempting resubscription first,
        then falling back to full reconnect with backoff.
        """
        try:
            if self.user_client and self.user_client.websocket and self.user_client.websocket.open:
                self.logger.warning("⚠️ Subscription error detected — trying to resubscribe.")
                try:
                    await self.user_client.unsubscribe_all_async()
                    await self.user_client.subscribe_async(
                        product_ids=self.product_ids,
                        channels=['user', 'heartbeats']
                    )
                    self.logger.info("✅ Resubscribed successfully after error.")
                    return
                except Exception as e:
                    self.logger.warning(f"⚠️ Resubscription failed, will attempt full reconnect: {e}")

            self.logger.warning("🔄 Falling back to full reconnect...")
            await self.reconnect_with_backoff()

        except Exception as e:
            self.logger.error(f"❌ Error during subscription error handling: {e}", exc_info=True)

    async def resubscribe_all_channels(self):
        """
        Resubscribes to all previously subscribed channels after a reconnect.
        Uses existing subscribe_user() and subscribe_market() methods.
        """
        try:
            self.logger.info("🔄 Resubscribing to all channels after reconnect...")
            if self.user_ws:
                await self.subscribe_user()
            if self.market_ws:
                await self.subscribe_market()
            self.logger.info("✅ Resubscription complete.")
        except Exception as e:
            self.logger.error(f"❌ Failed to resubscribe all channels: {e}", exc_info=True)

    def _normalize_passive_tracker_snapshot(self, snapshot):
        passive = snapshot.get("passive_tracker", {})
        if isinstance(passive, list):
            return {o["order_id"]: o for o in passive if isinstance(o, dict) and "order_id" in o}
        return dict(passive)

    async def reconnect_with_backoff(self, max_attempts: int = 10):
        """
        Attempts to reconnect to the WebSocket with exponential backoff.
        Resets subscription state before trying.
        """
        attempt = 0
        while attempt < max_attempts and not self.listener.shutdown_event.is_set():
            attempt += 1
            wait_time = min(2 ** attempt + random.uniform(0, 1), 60)  # Cap at 60s
            self.logger.warning(f"🔄 Reconnecting (attempt {attempt}) in {wait_time:.1f}s...")
            await asyncio.sleep(wait_time)

            try:
                # Clear previous subscription state
                self.subscribed_channels.clear()
                self.market_channel_activity.clear()
                self.user_channel_activity.clear()

                # Perform reconnect
                await self.websocket_manager.force_reconnect()
                await self.resubscribe_all_channels()

                self.logger.info("✅ Reconnected successfully.")
                return True
            except Exception as e:
                self.logger.error(f"❌ Reconnect attempt {attempt} failed: {e}", exc_info=True)

        self.logger.critical("🚨 Max reconnect attempts reached; giving up.")
        return False




