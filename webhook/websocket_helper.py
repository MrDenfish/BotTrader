import asyncio
import json
import time
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


    # @property
    # def market_data(self):
    #     return self.shared_data_manager.market_data
    #
    # @property
    # def order_management(self):
    #     return self.shared_data_manager.order_management
    #
    # @property
    # def passive_orders(self):
    #     return self.shared_data_manager.order_management.get('passive_orders') or {}
    #
    # @property
    # def coin_info(self):
    #     return self.shared_data_manager.market_data.get('filtered_vol', {})
    #
    # @property
    # def non_zero_balances(self):
    #     return self.shared_data_manager.order_management.get("non_zero_balances", {})
    #
    # @property
    # def open_orders(self):
    #     return self.shared_data_manager.order_management.get('order_tracker', {})
    #
    # @property
    # def ticker_cache(self):
    #     return self.shared_data_manager.market_data.get("ticker_cache", {})
    #
    # @property
    # def bid_ask_spread(self):
    #     return self.shared_data_manager.market_data.get('bid_ask_spread', {})
    #
    # @property
    # def spot_positions(self):
    #     return self.shared_data_manager.market_data.get("spot_positions", {})
    #
    # @property
    # def usd_pairs(self):
    #     return self.shared_data_manager.market_data.get("usd_pairs_cache", {})

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
            # print(json.dumps(data, indent=2)) # debug
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
                print(self.shared_utils_color.format(f" 💚 {self.listener.alerts.summarize_user_snapshot(data)}", self.shared_utils_color.BLUE))
                # print(f'💚  {self.listener.alerts.summarize_user_snapshot(data)}')
                await self.market_ws_manager.process_user_channel(data)
            elif channel == "heartbeats":
                self.last_heartbeat = time.time()
                self.count += 1
                if self.count >= 25:
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
        """Monitors activity on the user WebSocket channel and reconnects if inactive."""
        while True:
            try:
                now = time.time()
                for channel in self.user_channels:
                    last_seen = self.market_channel_activity.get(channel)
                    if not last_seen:
                        self.logger.warning(f"⚠️ No message ever received from user channel '{channel}'")
                    elif now - last_seen > timeout:
                        self.logger.warning(
                            f"⚠️ No message received from user channel '{channel}' in the last {int(now - last_seen)}s. Reconnecting..."
                        )
                        await self.websocket_manager.connect_user_stream()
                    else:
                        print(f"✅ User channel '{channel}' active within {int(now - last_seen)}s")

                await asyncio.sleep(timeout)

            except Exception as e:
                self.logger.error(f"❌ Error monitoring user channel activity: {e}", exc_info=True)


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
                            f"⚠️ No message received from market channel '{channel}' in the last {int(now - last_seen)}"
                            f" seconds reconnecting...."
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
                self.product_ids = market_data.get('usd_pairs_cache')['symbol'].str.replace('/', '-', regex=False).tolist()

                # ✅ Subscribe to each user channel separately
                for channel in new_channels:
                    subscription_message = {
                        "type": "subscribe",
                        "product_ids": self.product_ids,  # ✅ Ensure correct product ID format
                        "channel": channel,  # ✅ One channel per message
                        "jwt": jwt_token  # ✅ Include JWT for authentication
                    }
                    await self.user_ws.send(json.dumps(subscription_message))
                    self.logger.debug(f"Subscribed to user channel: {channel} with products: {self.product_ids}")

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
            # 🔁 Resynchronize open orders from REST to restore baseline state
            try:
                self.logger.info("🔄 Syncing open orders after reconnect...")
                await self.coinbase_api.fetch_open_orders()
            except Exception as e:
                self.logger.warning(f"⚠️ Open orders sync failed after reconnect: {e}")
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


    # async def process_event(self, event, profit_data_list, event_type):
    #     """Process specific events such as snapshots and updates."""
    #     print(f"Processing event: {event_type}")
    #     try:
    #         orders = event.get("orders", [])
    #
    #         if event_type == "snapshot":
    #             # Initialize tracker with the snapshot's orders
    #             for order in orders:
    #                 await self.market_ws_manager.process_order_for_tracker(order, profit_data_list, event_type)
    #             profit_df = self.profit_data_manager.consolidate_profit_data(profit_data_list)
    #         elif event_type == "update":
    #             # Apply updates to the order tracker
    #             # determine order type buy sell cancel
    #             for order in orders:
    #                 await self.market_ws_manager.handle_order_for_order_tracker(order, profit_data_list, event_type)
    #     except Exception as e:
    #         self.logger.error(f"Error processing {event_type} event: {e}", exc_info=True)

    # async def monitor_and_update_active_orders(self, market_data_snapshot, order_management_snapshot):
    #     """Monitor and manage active orders using modular handlers."""
    #     try:
    #         usd_avail = self._get_usd_available()
    #         profit_data_list = []
    #
    #         async with self.order_tracker_lock:
    #             order_tracker_snapshot = self._normalize_order_tracker_snapshot(order_management_snapshot)
    #             passive_tracker_snapshot = self._normalize_passive_tracker_snapshot(order_management_snapshot)
    #
    #             for order_id, raw_order in order_tracker_snapshot.items():
    #                 try:
    #                     order_data = OrderData.from_dict(raw_order)
    #                     symbol, asset = order_data.trading_pair, order_data.trading_pair.split('/')[0]
    #
    #                     if asset not in order_management_snapshot.get('non_zero_balances', {}):
    #                         continue
    #
    #                     precision = self.shared_utils_precision.fetch_precision(symbol)
    #                     order_data.base_decimal, order_data.quote_decimal = precision[:2]
    #                     order_data.product_id = symbol
    #                     order_data.avg_quote_volume = self.avg_quote_volume
    #                     order_duration = self._compute_order_duration(raw_order.get('datetime'))
    #
    #                     current_price = self.bid_ask_spread.get(symbol, Decimal("0"))
    #                     asset_balance, avg_price, cost_basis = self._get_asset_details(order_management_snapshot, asset, precision)
    #
    #                     # Dispatch based on type + side
    #                     if order_data.type == 'limit':
    #                         if order_data.side == 'buy':
    #                             await self._handle_limit_buy(order_data, symbol, asset, precision)
    #                         elif order_data.side == 'sell':
    #                             await self._handle_limit_sell(order_data, symbol, asset, precision, order_duration, avg_price, current_price)
    #
    #                     elif order_data.type == 'take_profit_stop_loss' and order_data.side == 'sell':
    #                         await self._handle_tp_sl_sell(order_data, order_management_snapshot, symbol, asset, precision, avg_price,
    #                                                       current_price)
    #
    #                     # Collect profitability
    #                     required_prices = {
    #                         'avg_price': avg_price,
    #                         'cost_basis': cost_basis,
    #                         'asset_balance': asset_balance,
    #                         'current_price': current_price,
    #                         'usd_avail': usd_avail,
    #                         'status_of_order': order_data.status
    #                     }
    #
    #                     profit = await self.profit_data_manager.calculate_profitability(
    #                         symbol, required_prices, self.bid_ask_spread, self.usd_pairs
    #                     )
    #
    #                     if profit:
    #                         profit_data_list.append(profit)
    #
    #                         if Decimal(profit.get('profit percent', '0').replace('%', '')) / 100 <= self.stop_loss:
    #                             await self.listener.handle_order_fill(order_data)
    #
    #                 except Exception as inner_ex:
    #                     self.logger.error(f"Error handling tracked order {order_id}: {inner_ex}", exc_info=True)
    #
    #         if profit_data_list:
    #             df = self.profit_data_manager.consolidate_profit_data(profit_data_list)
    #             print(f'Profit Data Open Orders:\n{df.to_string(index=True)}')
    #
    #         await self._run_monitor_untracked_assets()
    #
    #     except Exception as outer_e:
    #         self.logger.error(f"Error in monitor_and_update_active_orders: {outer_e}", exc_info=True)

    ## <><><><><><><><>><><>><> Handlers for monitor and update active orders  <><><><><><><><>><><>><>

    # def _get_usd_available(self):
    #     usd_data = self.usd_pairs.set_index('asset').to_dict(orient='index')
    #     return usd_data.get('USD', {}).get('free', Decimal('0'))

    # def _normalize_order_tracker_snapshot(self, snapshot):
    #     tracker = snapshot.get("order_tracker", {})
    #     if isinstance(tracker, list):
    #         return {o.get("order_id") or f"unknown_{i}": o for i, o in enumerate(tracker) if isinstance(o, dict)}
    #     return dict(tracker)

    def _normalize_passive_tracker_snapshot(self, snapshot):
        passive = snapshot.get("passive_tracker", {})
        if isinstance(passive, list):
            return {o["order_id"]: o for o in passive if isinstance(o, dict) and "order_id" in o}
        return dict(passive)

    # def _compute_order_duration(self, order_time_str):
    #     try:
    #         order_time = self.shared_utils_date_time.parse_iso_time(order_time_str)
    #         now = datetime.utcnow().replace(tzinfo=order_time.tzinfo)
    #         return int((now - order_time).total_seconds() // 60)
    #     except Exception:
    #         return 0

    # def _get_asset_details(self, snapshot, asset, precision):
    #     quote_deci = precision[1]
    #     quant = Decimal('1.' + '0' * quote_deci)
    #     balance_data = snapshot.get('non_zero_balances', {}).get(asset, {})
    #     avg_price = Decimal(balance_data.get('average_entry_price', {}).get('value', '0')).quantize(quant)
    #     cost_basis = Decimal(balance_data.get('cost_basis', {}).get('value', '0')).quantize(quant)
    #     asset_balance = Decimal(self.spot_positions.get(asset, {}).get('total_balance_crypto', 0)).quantize(quant)
    #     return asset_balance, avg_price, cost_basis

    # async def _handle_limit_sell(self, order_data, symbol, asset, precision, order_duration, avg_price, current_price):
    #     order_book = await self.order_book_manager.get_order_book(order_data, symbol)
    #     highest_bid = Decimal(max(order_book['order_book']['bids'], key=lambda x: x[0])[0])
    #
    #     # Adjust trailing stop logic
    #     if order_data.price < min(current_price, highest_bid) and order_duration > 5:
    #         await self.listener.order_manager.cancel_order(order_data.order_id, symbol)
    #         new_order_data = await self.trade_order_manager.build_order_data('websocket', 'trailing_stop', asset, symbol, order_data.price, None)
    #         await self.listener.handle_order_fill(new_order_data)
    #         return
    #
    #     # NEW: adjust limit sell if price is creeping upward from loss
    #     if order_data.price < avg_price and current_price > order_data.price and order_duration > 5:
    #         trigger = self.trade_order_manager.build_trigger(
    #             "limit_sell_adjusted",
    #             f"Recovering price: old={order_data.price}, current={current_price}, avg={avg_price}"
    #         )
    #         await self.listener.order_manager.cancel_order(order_data.order_id, symbol)
    #         new_order_data = await self.trade_order_manager.build_order_data('websocket', trigger, asset, symbol, None, 'limit', 'sell')
    #         if new_order_data:
    #             success, response = await self.trade_order_manager.place_order(new_order_data, precision)
    #             log = self.logger.info if success else self.logger.warning
    #             log(f"{'✅' if success else '⚠️'} Adjusted limit SELL for {symbol}: {response}")

    ## <><><><><><><><>><><>><> Handlers for monitor and update active orders  <><><><><><><><>><><>><>

    # async def _handle_limit_buy(self, order_data, symbol, asset, precision):
    #     await self._handle_active_limit_buy_adjustment(order_data, symbol, asset, precision)

    # async def _handle_tp_sl_sell(self, order_data, snapshot, symbol, asset, precision, avg_price, current_price):
    #     full_tracker = snapshot.get("order_tracker", {})
    #     full_order = full_tracker.get(order_data.order_id)
    #     if full_order:
    #         await self._handle_active_tp_sl_decision(
    #             order_data=order_data,
    #             full_order=full_order,
    #             symbol=symbol,
    #             asset=asset,
    #             current_price=current_price,
    #             avg_price=avg_price,
    #             precision_data=precision
    #         )

    # async def _run_monitor_untracked_assets(self):
    #     try:
    #         await asyncio.wait_for(self.asset_monitor.monitor(),timeout=30)
    #     except asyncio.TimeoutError:
    #         self.logger.warning("⚠️ monitor_untracked_assets timed out — skipping.")

    # async def monitor_and_update_active_orders(self, market_data_snapshot, order_management_snapshot):
    #     """Monitor active orders and update trailing stops or profitability."""
    #     try:
    #         usd_avail = self.usd_pairs.set_index('asset').to_dict(orient='index')
    #         usd_avail = usd_avail.get('USD',{}).get('free')
    #         profit_data_list = []
    #
    #         async with (self.order_tracker_lock):
    #             raw_tracker = order_management_snapshot.get("order_tracker", {})
    #             if isinstance(raw_tracker, list):
    #                 # Convert list of dicts to dict keyed by order_id
    #                 order_tracker_snapshot = {
    #                     (
    #                             o.get("order_id") or
    #                             o.get("info", {}).get("order_id") or
    #                             o.get("id") or
    #                             f"unknown_{i}"
    #                     ): o
    #                     for i, o in enumerate(raw_tracker)
    #                     if isinstance(o, dict)
    #                 }
    #             else:
    #                 # Assume it's already a dict
    #                 order_tracker_snapshot = dict(raw_tracker)
    #             raw_passive = order_management_snapshot.get("passive_tracker", {})
    #             if isinstance(raw_passive, list):
    #                 passive_tracker_snapshot = {
    #                     o["order_id"]: o for o in raw_passive if isinstance(o, dict) and "order_id" in o
    #                 }
    #             else:
    #                 passive_tracker_snapshot = dict(raw_passive)
    #
    #             for order_id, raw_order in order_tracker_snapshot.items():
    #                 # Skip passive orders — these are managed by PassiveMM via watchdog
    #                 # if raw_order.source.lower() == 'PassiveMM':
    #                 #     continue
    #                 raw_order['type'] = raw_order.get('type').lower()
    #                 raw_order['side'] = raw_order.get('side').lower()
    #                 order_id = raw_order.get('order_id', 'UNKNOWN')
    #                 order_data = OrderData.from_dict(raw_order)
    #                 order_data.avg_quote_volume = self.avg_quote_volume
    #                 try:
    #                     symbol = order_data.trading_pair
    #                     asset = symbol.split('/')[0]
    #                     # ✅ Fetch precision values for the asset
    #                     precision_data = self.shared_utils_precision.fetch_precision(symbol)
    #                     order_data.base_decimal = precision_data[0]
    #                     order_data.quote_decimal = precision_data[1]
    #                     if asset == 'RAD':
    #                        pass
    #                     order_duration = raw_order.get('order_duration')
    #
    #                     # If missing, compute duration from datetime
    #                     if order_duration is None:
    #                         order_time = raw_order.get('datetime')
    #                         if isinstance(order_time, str):
    #                             # Parse if it's a string
    #                             order_time = self.shared_utils_date_time.parse_iso_time(order_time)
    #                         if isinstance(order_time, datetime):
    #                             now = datetime.utcnow().replace(tzinfo=order_time.tzinfo)
    #                             elapsed = now - order_time
    #                             order_duration = int(elapsed.total_seconds() // 60)  # whole minutes
    #                         else:
    #                             order_duration = 0
    #                     base_deci, quote_deci, _, _ = precision_data
    #
    #                     # ✅ Add precision values to order_data
    #                     order_data.quote_decimal = quote_deci
    #                     order_data.base_decimal = base_deci
    #                     order_data.product_id = symbol
    #                     if asset not in order_management_snapshot.get('non_zero_balances', {}):
    #
    #                         continue
    #                     avg_price = order_management_snapshot.get('non_zero_balances', {})[asset]['average_entry_price'].get('value')
    #                     avg_price = Decimal(avg_price).quantize(Decimal('1.' + '0' * quote_deci))
    #                     asset_balance = Decimal(self.spot_positions.get(asset, {}).get('total_balance_crypto', 0))
    #                     try:
    #                         quantizer = Decimal(f'1e-{quote_deci}')
    #                         asset_balance = Decimal(asset_balance).quantize(quantizer)
    #                     except decimal.InvalidOperation:
    #                         self.logger.warning(f"⚠️ Could not quantize asset_balance={asset_balance} with precision={quote_deci}")
    #                         asset_balance = Decimal(asset_balance).scaleb(-quote_deci).quantize(quantizer)
    #                     current_price = self.bid_ask_spread.get(symbol, 0)
    #                     cost_basis = order_management_snapshot.get('non_zero_balances', {})[asset]['cost_basis'].get('value')
    #                     cost_basis = Decimal(cost_basis).quantize(Decimal('1.' + '0' * quote_deci))
    #
    #                     required_prices = {
    #                         'avg_price': avg_price,
    #                         'cost_basis': cost_basis,
    #                         'asset_balance': asset_balance,
    #                         'current_price': None,
    #                         'profit': None,
    #                         'profit_percentage': None,
    #                         'usd_avail': usd_avail,
    #                         'status_of_order': order_data.status
    #                     }
    #                     # if limit sell price < the min(current price, highest bid) and the order is > than 5 minutes old resubmit order
    #                     if order_data.type == 'limit' and order_data.side == 'sell':
    #                         order_book = await self.order_book_manager.get_order_book(order_data, symbol)
    #                         highest_bid = Decimal(max(order_book['order_book']['bids'], key=lambda x: x[0])[0])
    #                         if order_data.price < min(current_price,highest_bid) and order_duration > 5 :
    #                             # ✅ Update trailing stop with highest bid
    #                             old_ts_limit_price = order_data.price
    #                             await self.listener.order_manager.cancel_order(order_data.order_id, symbol)
    #                             new_order_data = await self.trade_order_manager.build_order_data('Websocket','trailing_stop',
    #                                                                                              asset, symbol, old_ts_limit_price, None)
    #                             await self.listener.handle_order_fill(new_order_data)
    #                             continue
    #                         else:  # for testing purposes
    #                             pass
    #                     elif order_data.type == 'limit' and order_data.side == 'buy':
    #                         await self._handle_active_limit_buy_adjustment(
    #                             order_data=order_data,
    #                             symbol=symbol,
    #                             asset=asset,
    #                             precision_data=precision_data
    #                         )
    #
    #                     elif order_data.type == 'take_profit_stop_loss' and order_data.side == 'sell':
    #                         full_tracker = order_management_snapshot.get("order_tracker", {})
    #                         full_order = full_tracker.get(order_data.order_id)
    #                         if full_order:
    #                             await self._handle_active_tp_sl_decision(
    #                                 order_data=order_data,
    #                                 full_order=full_order,
    #                                 symbol=symbol,
    #                                 asset=asset,
    #                                 current_price=current_price,
    #                                 avg_price=avg_price,
    #                                 precision_data=precision_data
    #                             )
    #
    #
    #                     profit = await self.profit_data_manager.calculate_profitability(
    #                         symbol, required_prices, self.bid_ask_spread, self.usd_pairs
    #                     )
    #
    #                     if profit and profit.get('profit', 0) != 0:
    #                         profit_data_list.append(profit)
    #                     if Decimal(profit.get('profit percent', '0').replace('%', '')) / 100 <= self.stop_loss:
    #                         await self.listener.handle_order_fill(order_data)
    #                 except Exception as inner_ex:
    #                     self.logger.error(f"Error handling tracked order {order_id}: {inner_ex}", exc_info=True)
    #
    #         if profit_data_list:
    #             profit_df = self.profit_data_manager.consolidate_profit_data(profit_data_list)
    #             print(f'Profit Data Open Orders:\n{profit_df.to_string(index=True)}')
    #
    #         self.logger.info(f"✅ monitor_untracked_assets is {type(self.monitor_untracked_assets)}")
    #         self.logger.info("🧪 About to await monitor_untracked_assets")
    #
    #         try:
    #             await asyncio.wait_for(
    #                 self.monitor_untracked_assets(market_data_snapshot, order_management_snapshot),
    #                 timeout=30  # seconds, or 60 if needed
    #             )
    #         except asyncio.TimeoutError:
    #             self.logger.warning("⚠️ monitor_untracked_assets timed out — skipping.")
    #
    #         self.logger.info("✅ monitor_untracked_assets completed")
    #
    #     except Exception as outer_e:
    #         self.logger.error(f"Error in monitor_and_update_active_orders: {outer_e}", exc_info=True)
    # async def _handle_active_limit_buy_adjustment(
    #     self,
    #     order_data: OrderData,
    #     symbol: str,
    #     asset: str,
    #     precision_data: tuple
    # ):
    #     try:
    #         # Fetch current order book
    #         order_book = await self.order_book_manager.get_order_book(order_data, symbol)
    #         best_ask = Decimal(min(order_book['order_book']['asks'], key=lambda x: x[0])[0])
    #
    #         quote_deci = precision_data[1]
    #         old_price = order_data.price.quantize(Decimal('1.' + '0' * quote_deci))
    #         best_ask = best_ask.quantize(Decimal('1.' + '0' * quote_deci))
    #
    #         # Compare to old price
    #         price_diff = (best_ask - old_price) / old_price
    #
    #         if price_diff <= Decimal("0.01"):
    #             return  # Skip if within 1%
    #
    #         await self.listener.order_manager.cancel_order(order_data.order_id, symbol)
    #
    #         # New limit price slightly below best ask
    #         new_price = (best_ask * Decimal("0.995")).quantize(Decimal('1.' + '0' * quote_deci))
    #
    #         trigger = self.trade_order_manager.build_trigger(
    #             "limit_buy_adjusted",
    #             f"best_ask={best_ask} > old_price={old_price}, new_price={new_price}"
    #         )
    #
    #         new_order_data = await self.trade_order_manager.build_order_data(
    #             source="websocket",
    #             trigger=trigger,
    #             asset=asset,
    #             product_id=symbol,
    #             stop_price=None,
    #             order_type="limit",
    #             side="buy"
    #         )
    #
    #         if new_order_data:
    #             success, response = await self.trade_order_manager.place_order(new_order_data, precision_data)
    #             log_method = self.logger.info if success else self.logger.warning
    #             log_method(f"{'✅' if success else '⚠️'} Replaced limit BUY for {symbol}: {response}")
    #
    #     except Exception as e:
    #         self.logger.error(f"❌ Error in _handle_active_limit_buy_adjustment for {symbol}: {e}", exc_info=True)
    #
    #
    # async def _handle_active_tp_sl_decision(self,
    #         order_data: OrderData,
    #         full_order: dict,
    #         symbol: str,
    #         asset: str,
    #         current_price: Decimal,
    #         avg_price: Decimal,
    #         precision_data: tuple,
    # ):
    #     try:
    #         quote_deci = precision_data[1]
    #         current_price = current_price.quantize(Decimal('1.' + '0' * quote_deci))
    #         avg_price = avg_price.quantize(Decimal('1.' + '0' * quote_deci))
    #
    #         profit_pct = (current_price - avg_price) / avg_price
    #
    #         trigger_config = full_order['info']['order_configuration']['trigger_bracket_gtc']
    #         old_limit_price = Decimal(trigger_config.get('limit_price', '0')).quantize(Decimal('1.' + '0' * quote_deci))
    #
    #         # Determine if price change justifies update
    #         if current_price > old_limit_price:
    #             trigger = self.trade_order_manager.build_trigger(
    #                 "TP",
    #                 f"profit_pct={profit_pct:.2%} → price rose above TP ({current_price} > {old_limit_price})"
    #             )
    #         elif current_price < old_limit_price:
    #             trigger = self.trade_order_manager.build_trigger(
    #                 "SL",
    #                 f"profit_pct={profit_pct:.2%} → price fell below SL ({current_price} < {old_limit_price})"
    #             )
    #         else:
    #             return  # No update needed
    #
    #         await self.listener.order_manager.cancel_order(order_data.order_id, symbol)
    #
    #         new_order_data = await self.trade_order_manager.build_order_data(
    #             source='websocket',
    #             trigger=trigger,
    #             asset=asset,
    #             product_id=symbol,
    #             side='sell',
    #         )
    #
    #         if new_order_data:
    #             success, response = await self.trade_order_manager.place_order(new_order_data, precision_data)
    #             log_method = self.logger.info if success else self.logger.warning
    #             log_method(f"{'✅' if success else '⚠️'} Updated SL/TP for {symbol} at {current_price}: {response}")
    #
    #     except Exception as e:
    #         self.logger.error(f"❌ Error in _handle_active_tp_sl_decision for {symbol}: {e}", exc_info=True)

    # <><><><><><><><><><><><><><><><><><><><><><><><><>
    # async def monitor_untracked_assets(self, market_data_snapshot, order_management_snapshot):
    #     self.logger.info("📱 Starting monitor_untracked_assets")
    #
    #     usd_prices = self._get_usd_prices()
    #     if not usd_prices or not self.non_zero_balances:
    #         self.logger.warning("⚠️ Skipping due to missing prices or balances")
    #         return
    #
    #     for asset, position in self.non_zero_balances.items():
    #         try:
    #             result = self._analyze_position(asset, position, usd_prices)
    #             if not result:
    #                 continue
    #
    #             symbol, asset, current_price, qty, avg_entry, profit, profit_pct, precision_data = result
    #
    #             if not await self._passes_holding_cooldown(symbol):
    #                 continue
    #
    #             await self._handle_tp_sl_decision(
    #                 symbol=symbol,
    #                 asset=asset,
    #                 current_price=current_price,
    #                 qty=qty,
    #                 avg_entry=avg_entry,
    #                 profit=profit,
    #                 profit_pct=profit_pct,
    #                 precision_data=precision_data,
    #                 snapshot=order_management_snapshot
    #             )
    #         except Exception as e:
    #             self.logger.error(f"❌ Error analyzing {asset}: {e}", exc_info=True)
    #
    #     self.logger.info("✅ monitor_untracked_assets completed")

    # def _get_usd_prices(self):
    #     if self.usd_pairs.empty:
    #         return {}
    #     return self.usd_pairs.set_index("symbol")["price"].to_dict()

    # def _analyze_position(self, asset, position, usd_prices):
    #     symbol = f"{asset}-USD"
    #     if symbol in ("USD-USD", "SHDW-USD") or symbol in self.passive_orders:
    #         return None
    #
    #     pos = position.to_dict() if hasattr(position, "to_dict") else position
    #     current_price = usd_prices.get(symbol)
    #     if not current_price:
    #         return None
    #
    #     precision_data = self.shared_utils_precision.fetch_precision(symbol)
    #     base_deci, quote_deci = precision_data[0], precision_data[1]
    #     base_q = Decimal("1").scaleb(-base_deci)
    #     quote_q = Decimal("1").scaleb(-quote_deci)
    #
    #     avg_entry = self.shared_utils_precision.safe_quantize(
    #         Decimal(pos.get("average_entry_price", {}).get("value", "0")), quote_q)
    #     cost_basis = self.shared_utils_precision.safe_quantize(
    #         Decimal(pos.get("cost_basis", {}).get("value", "0")), quote_q)
    #     qty = self.shared_utils_precision.safe_quantize(
    #         Decimal(pos.get("available_to_trade_crypto", "0")), base_q)
    #
    #     if qty <= Decimal("0.0001") or avg_entry <= 0:
    #         return None
    #
    #     profit = (Decimal(current_price) - avg_entry) * qty
    #     profit_pct = (Decimal(current_price) - avg_entry) / avg_entry
    #
    #     return symbol, asset, Decimal(current_price), qty, avg_entry, profit, profit_pct, precision_data

    # async def _passes_holding_cooldown(self, symbol: str) -> bool:
    #     try:
    #         order_id = await self.trade_recorder.find_latest_unlinked_buy(symbol)
    #         if not order_id:
    #             return True  # No recent buy
    #
    #         trade = await self.trade_recorder.fetch_trade_by_order_id(order_id)
    #         if not trade or not trade.order_time:
    #             return True
    #
    #         now = datetime.utcnow().replace(tzinfo=trade.order_time.tzinfo)
    #         held_for = now - trade.order_time
    #
    #         if held_for < timedelta(minutes=self.min_cooldown):
    #             self.logger.debug(f"⏳ Skipping {symbol} — held for {held_for}, below cooldown")
    #             return False
    #         return True
    #
    #     except Exception as e:
    #         self.logger.warning(f"⚠️ Could not evaluate cooldown for {symbol}: {e}", exc_info=True)
    #         return True

    # async def _handle_tp_sl_decision(self, symbol, asset, current_price, qty, avg_entry,
    #                                  profit, profit_pct, precision_data, snapshot):
    #     try:
    #         if asset in self.hodl:
    #             return
    #
    #         open_order = self._get_open_order_for_symbol(symbol)
    #         info = open_order.get('info', {}) if open_order else {}
    #         order_price = Decimal(info.get("average_filled_price", "0") or "0")
    #
    #         if profit_pct >= self.take_profit:
    #             trigger = self.trade_order_manager.build_trigger(
    #                 "TP", f"profit_pct={profit_pct:.2%} ≥ take_profit={self.take_profit:.2%}"
    #             )
    #             if open_order and current_price > order_price:
    #                 await self.order_manager.cancel_order(info.get("order_id"), symbol)
    #                 open_order = None
    #             if not open_order:
    #                 await self._place_tp_order("websocket", trigger, asset, symbol, current_price, precision_data)
    #
    #         elif profit_pct <= self.stop_loss:
    #             trigger = self.trade_order_manager.build_trigger(
    #                 "SL", f"profit_pct={profit_pct:.2%} < stop_loss={self.stop_loss:.2%}"
    #             )
    #             if open_order and current_price < order_price:
    #                 await self.order_manager.cancel_order(info.get("order_id"), symbol)
    #                 open_order = None
    #             if not open_order:
    #                 await self._place_sl_order(asset, symbol, current_price, trigger, precision_data)
    #
    #         self.logger.info(
    #             self.shared_utils_color.format(
    #                 f"{symbol} {trigger.get('trigger', '?')} → {trigger.get('trigger_note', profit_pct)}",
    #                 self.shared_utils_color.GREEN if profit_pct >= 0 else self.shared_utils_color.ORANGE
    #             )
    #         )
    #
    #     except Exception as e:
    #         self.logger.error(f"❌ Error handling TP/SL for {symbol}: {e}", exc_info=True)

    # def _get_open_order_for_symbol(self, symbol: str) -> Optional[dict]:
    #     return next((o for o in self.open_orders.values() if o.get("symbol") == symbol), None)

    # async def _place_tp_order(self, source, trigger, asset, symbol, price, precision_data):
    #     order_data = await self.trade_order_manager.build_order_data(
    #         source=source, trigger=trigger, asset=asset, product_id=symbol, side='sell'
    #     )
    #     if not order_data:
    #         return
    #     order_data.trigger = trigger
    #     success, response = await self.trade_order_manager.place_order(order_data, precision_data)
    #     log = self.logger.info if success else self.logger.warning
    #     log(f"{'✅' if success else '⚠️'} TP order for {symbol}: {response}")
    #
    # async def _place_sl_order(self, asset, symbol, current_price, trigger, precision_data):
    #     order_data = await self.trade_order_manager.build_order_data(
    #         source='websocket', trigger=trigger, asset=asset, product_id=symbol, side='sell'
    #     )
    #     if not order_data:
    #         return
    #     order_data.trigger = trigger
    #     success, response = await self.trade_order_manager.place_order(order_data, precision_data)
    #     log = self.logger.info if success else self.logger.warning
    #     log(f"{'✅' if success else '⚠️'} SL order for {symbol}: {response}")

    # def _extract_price(self, order: dict) -> Decimal:
    #     """
    #     Extracts the appropriate SL/limit price from various open order structures.
    #     """
    #     try:
    #         info = order.get("info", {}) or {}
    #         order_config = info.get("order_configuration", {})
    #
    #         if "trigger_bracket_gtc" in order_config:
    #             sl_price = order_config["trigger_bracket_gtc"].get("stop_loss_price") \
    #                        or order_config["trigger_bracket_gtc"].get("stop_trigger_price")
    #             return Decimal(sl_price)
    #
    #         if "limit_limit_gtc" in order_config:
    #             return Decimal(order_config["limit_limit_gtc"].get("limit_price"))
    #
    #         # Fallback to legacy formats or simplified views
    #         return Decimal(
    #             order.get("stop_loss_price")
    #             or order.get("stop_trigger_price")
    #             or order.get("limit_price")
    #             or order.get("price")
    #             or "0"
    #         )
    #     except Exception:
    #         self.logger.warning(f"⚠️ Failed to extract SL price from order: {order}")
    #         return Decimal("0")


