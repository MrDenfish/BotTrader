import asyncio
import decimal
import json
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Dict, Any

import pandas as pd
from coinbase import jwt_generator

from Config.config_manager import CentralConfig as Config
from webhook.webhook_validate_orders import OrderData

BATCH_SIZE = 10
TASK_TIMEOUT = 10  # per asset
TOTAL_TIMEOUT = 180  # total for the full monitor_untracked_assets cycle

class WebSocketHelper:
    """
            WebSocketHelper is responsible for managing WebSocket connections and API integrations.
            """
    def __init__(
            self, listener, websocket_manager, exchange, ccxt_api, logger_manager, coinbase_api, profit_data_manager,
            order_type_manager, shared_utils_date_time, shared_utils_print, shared_utils_precision, shared_utils_utility, shared_utils_debugger,
            trailing_stop_manager, order_book_manager, snapshot_manager, trade_order_manager, ohlcv_manager,
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

        # self.api_algo = self.config.websocket_api.get('algorithm')

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
        self.asset_semaphore = asyncio.Semaphore(5)

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
        self._order_size_fiat = Decimal(self.config.order_size_fiat)
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
        self.shared_utils_date_time = shared_utils_date_time
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
        return self.shared_data_manager.market_data.get("ticker_cache", {})

    @property
    def bid_ask_spread(self):
        return self.shared_data_manager.market_data.get("bid_ask_spread", {})

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
                        "product_ids": ['BTC-USD'],  #self.product_ids,
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
                await self.refresh_open_orders()
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
            bid_ask_spread = market_data_snapshot.get('bid_ask_spread', {})
            usd_pairs = market_data_snapshot.get('usd_pairs_cache', {})
            usd_avail = order_management_snapshot.get('non_zero_balances', {})['USD']['available_to_trade_crypto']
            profit_data_list = []

            async with (self.order_tracker_lock):
                order_tracker_snapshot = dict(order_management_snapshot.get("order_tracker", {}))
                for order_id, raw_order in order_tracker_snapshot.items():

                    raw_order['type'] = raw_order.get('type').lower()
                    raw_order['side'] = raw_order.get('side').lower()
                    order_id = raw_order.get('order_id', 'UNKNOWN')
                    order_data = OrderData.from_dict(raw_order)

                    try:
                        symbol = order_data.trading_pair
                        asset = symbol.split('/')[0]
                        # ✅ Fetch precision values for the asset
                        precision_data = self.shared_utils_precision.fetch_precision(symbol)
                        order_data.base_decimal = precision_data[0]
                        order_data.quote_decimal = precision_data[1]
                        if asset == 'IDEX':
                           pass
                        order_duration = raw_order.get('order_duration')

                        # If missing, compute duration from datetime
                        if order_duration is None:
                            order_time = raw_order.get('datetime')
                            if isinstance(order_time, str):
                                # Parse if it's a string
                                order_time = self.shared_utils_date_time.parse_iso_time(order_time)
                            if isinstance(order_time, datetime):
                                now = datetime.utcnow().replace(tzinfo=order_time.tzinfo)
                                elapsed = now - order_time
                                order_duration = int(elapsed.total_seconds() // 60)  # whole minutes
                            else:
                                order_duration = 0
                        base_deci, quote_deci, _, _ = precision_data

                        # ✅ Add precision values to order_data
                        order_data.quote_decimal = quote_deci
                        order_data.base_decimal = base_deci
                        order_data.product_id = symbol
                        if asset not in order_management_snapshot.get('non_zero_balances', {}):

                            continue
                        avg_price = order_management_snapshot.get('non_zero_balances', {})[asset]['average_entry_price'].get('value')
                        avg_price = Decimal(avg_price).quantize(Decimal('1.' + '0' * quote_deci))
                        asset_balance = Decimal(spot_positions.get(asset, {}).get('total_balance_crypto', 0))
                        try:
                            quantizer = Decimal(f'1e-{quote_deci}')
                            asset_balance = Decimal(asset_balance).quantize(quantizer)
                        except decimal.InvalidOperation:
                            self.logger.warning(f"⚠️ Could not quantize asset_balance={asset_balance} with precision={quote_deci}")
                            asset_balance = Decimal(asset_balance).scaleb(-quote_deci).quantize(quantizer)
                        current_price = bid_ask_spread.get(symbol, 0)
                        cost_basis = order_management_snapshot.get('non_zero_balances', {})[asset]['cost_basis'].get('value')
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
                        # if limit sell price < the min(current price, highest bid) and the order is > than 5 minutes old resubmit order
                        if order_data.type == 'limit' and order_data.side == 'sell':
                            order_book = await self.order_book_manager.get_order_book(order_data, symbol)
                            highest_bid = Decimal(max(order_book['order_book']['bids'], key=lambda x: x[0])[0])
                            if order_data.price < min(current_price,highest_bid) and order_duration > 5 :
                                # ✅ Update trailing stop with highest bid
                                old_ts_limit_price = order_data.price
                                await self.listener.order_manager.cancel_order(order_data.order_id, symbol)
                                new_order_data = await self.trade_order_manager.build_order_data('Websocket','trailing_stop',
                                                                                                 asset, symbol, old_ts_limit_price, None)
                                await self.listener.handle_order_fill(new_order_data)
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

                                # Cancel the existing limit buy order because price is out of range
                                await self.listener.order_manager.cancel_order(order_data.order_id, symbol)

                                # Calculate a new limit price (e.g., slightly below the best ask)
                                new_limit_price = best_ask * Decimal('0.995')  # 0.5% below best ask
                                new_limit_price = new_limit_price.quantize(Decimal('1.' + '0' * quote_deci))

                                # Build and place a new limit buy order
                                new_order_data = await self.trade_order_manager.build_order_data(
                                    'Websocket', 'limit_buy_adjusted', asset, symbol, new_limit_price, None
                                )
                                if new_order_data:
                                    trigger = {"trigger": f"limit_buy_adjusted", "trigger_note": f"new limit price:{new_limit_price}"}
                                    new_order_data.trigger = trigger

                        elif order_data.type == 'take_profit_stop_loss' and order_data.side == 'sell':
                            full_tracker = order_management_snapshot.get("order_tracker", {})
                            full_order = full_tracker.get(order_data.order_id)
                            if full_order:
                                # Extract old limit price and current price
                                trigger_config = full_order['info']['order_configuration']['trigger_bracket_gtc']
                                old_limit_price = Decimal(trigger_config.get('limit_price', '0'))
                                current_price = Decimal(bid_ask_spread.get(symbol, Decimal('0')))
                                # If we're above the original limit price, reconfigure
                                if current_price > old_limit_price:
                                    print(f"🔆TP adjustment: Current price {current_price} > TP {old_limit_price}, "
                                          f"reconfiguring TP/SL for {symbol} 🔆")
                                    # Cancel the stale TP/SL order
                                    await self.listener.order_manager.cancel_order(order_data.order_id, symbol)
                                    # Create new TP/SL order
                                    new_order_data = await self.trade_order_manager.build_order_data(
                                        'Websocket','profit', asset,symbol, old_limit_price, None)
                                    continue

                        profit = await self.profit_data_manager.calculate_profitability(
                            symbol, required_prices, bid_ask_spread, usd_pairs
                        )

                        if profit and profit.get('profit', 0) != 0:
                            profit_data_list.append(profit)
                        if Decimal(profit.get('profit percent', '0').replace('%', '')) / 100 <= self.stop_loss:
                            await self.listener.handle_order_fill(order_data)
                    except Exception as inner_ex:
                        self.logger.error(f"Error handling tracked order {order_id}: {inner_ex}", exc_info=True)

            if profit_data_list:
                profit_df = self.profit_data_manager.consolidate_profit_data(profit_data_list)
                print(f'Profit Data Open Orders:\n{profit_df.to_string(index=True)}')

            self.logger.info(f"✅ monitor_untracked_assets is {type(self.monitor_untracked_assets)}")
            self.logger.info("🧪 About to await monitor_untracked_assets")

            try:
                await asyncio.wait_for(
                    self.monitor_untracked_assets(market_data_snapshot, order_management_snapshot),
                    timeout=30  # seconds, or 60 if needed
                )
            except asyncio.TimeoutError:
                self.logger.warning("⚠️ monitor_untracked_assets timed out — skipping.")

            self.logger.info("✅ monitor_untracked_assets completed")

        except Exception as outer_e:
            self.logger.error(f"Error in monitor_and_update_active_orders: {outer_e}", exc_info=True)

    async def monitor_untracked_assets(self, market_data_snapshot: Dict[str, Any], order_management_snapshot: Dict[str, Any]):
        """
        Simplified and robust: evaluate held assets for TP/SL conditions and place orders.
        No per-asset timeout. No nested async logic. Clear logs for decision path.
        """
        try:
            self.logger.info("📱 Starting monitor_untracked_assets")

            spot_positions = market_data_snapshot.get("spot_positions", {})
            usd_pairs = market_data_snapshot.get("usd_pairs_cache", {})
            usd_prices = usd_pairs.set_index("symbol")["price"].to_dict() if not usd_pairs.empty else {}

            raw_balances = order_management_snapshot.get("non_zero_balances", {})
            if not usd_prices or not raw_balances:
                self.logger.warning("⚠️ Skipping due to missing prices or balances")
                return

            for asset, position in raw_balances.items():
                try:
                    precision = spot_positions.get(asset,{}).get('precision')
                    base_deci = precision.get('amount', 8)
                    quote_deci = precision.get('price', 8)

                    pos = position.to_dict() if hasattr(position, "to_dict") else position
                    if not isinstance(pos, dict):
                        raise TypeError(f"Invalid position type: {type(pos)}")

                    symbol = f"{asset}/USD"
                    if symbol =='USD/USD':
                        continue
                    current_price = usd_prices.get(symbol)
                    if not current_price:
                        self.logger.debug(f"🔍 Skipping {symbol}: price unavailable")
                        continue
                    quote_quantizer = Decimal("1").scaleb(-quote_deci)
                    base_quantizer = Decimal("1").scaleb(-base_deci)
                    average_entry = Decimal(pos.get("average_entry_price", {}).get("value"))
                    average_entry = self.shared_utils_precision.safe_quantize(average_entry, quote_quantizer)
                    cost_basis = Decimal(pos.get("cost_basis", {}).get("value"))
                    cost_basis = self.shared_utils_precision.safe_quantize(cost_basis, quote_quantizer)

                    available_qty = Decimal(pos.get("available_to_trade_crypto"))
                    available_qty = self.shared_utils_precision.safe_quantize(available_qty, base_quantizer)

                    if average_entry == 0 or available_qty == 0:
                        continue

                    # Evaluate PnL
                    profit = (Decimal(current_price) - average_entry) * available_qty
                    profit_pct = ((Decimal(current_price) - average_entry) / average_entry) if average_entry else Decimal("0")

                    self.logger.info(f"🔎 {symbol}: Entry={average_entry}, Now={current_price}, Qty={available_qty}, PnL%={profit_pct:.2%}")

                    if profit_pct >= self.take_profit and asset not in self.hodl:
                        self.logger.info(f"💰 TP trigger for {symbol}: {profit_pct:.2%}")
                        await self._place_tp_order('websocket','profit',asset, symbol, current_price)
                    elif profit_pct <= -self.stop_loss and asset not in self.hodl:
                        self.logger.info(f"🛑 SL trigger for {symbol}: {profit_pct:.2%}")
                        await self._place_sl_order(asset, symbol, current_price, profit, profit_pct)

                except Exception as e:
                    self.logger.error(f"❌ monitor_untracked_assets error for {asset}: {e}", exc_info=True)

        except Exception as e:
            self.logger.error(f"❌ monitor_untracked_assets crashed: {e}", exc_info=True)
        finally:
            self.logger.info("✅ monitor_untracked_assets completed")

    async def _place_tp_order(self, source, trigger, asset: str, symbol: str, price: Decimal):
        precision_data = self.shared_utils_precision.fetch_precision(symbol)

        order_data = await self.trade_order_manager.build_order_data(
            source='websocket', trigger='take_profit', asset=asset, product_id=symbol
        )
        if order_data:
            order_data.trigger = {"trigger": "TP", "trigger_note": f"price={price}"}

            # Check if there's already an open order
            open_orders = self.shared_data_manager.order_management.get("order_tracker", {})
            if any(o.get("symbol") == symbol for o in open_orders.values()):
                self.logger.info(f"⏸️ Skipping TP order for {symbol}: open order exists")
                return

            success, response = await self.trade_order_manager.place_order(order_data, precision_data)
            log_method = self.logger.info if success else self.logger.error
            log_method(f"{'✅' if success else '❌'} TP order for {symbol}: {response}")

    async def _place_sl_order(self, asset: str, symbol: str, current_price: Decimal, profit: Decimal, profit_pct: Decimal):
        precision_data = self.shared_utils_precision.fetch_precision(symbol)
        order_not_placed = True

        while order_not_placed:
            try:
                # Step 1: Build OrderData
                order_data = await self.trade_order_manager.build_order_data(
                    source='websocket', trigger='stop_loss', asset=asset, product_id=symbol
                )
                if not order_data:
                    return

                order_data.trigger = {"trigger": "SL", "trigger_note": f"stop_loss={self.stop_loss}%"}

                # Step 2: Check existing open orders
                open_orders = self.shared_data_manager.order_management.get("order_tracker", {})
                for o in open_orders.values():
                    if o.get("symbol") != symbol:
                        continue

                    order_id = o.get("order_id")
                    order_type = o.get("type", "").upper()
                    limit_price = self._extract_price(o)

                    if order_type == "TAKE_PROFIT_STOP_LOSS" and current_price <= limit_price:
                        await self.listener.order_manager.cancel_order(order_id, symbol)
                        self.logger.info(f"🛑 Canceled SL order {order_id} at limit {limit_price}")
                        order_not_placed = False
                        break  # Only cancel one matching SL order
                    else:
                        return  # Don't place a new order if one is active and valid

                # Step 3: Place SL order if no active one exists
                if not order_data.open_orders.get("open_order"):
                    success, response = await self.trade_order_manager.place_order(order_data, precision_data)
                    log_method = self.logger.info if success else self.logger.error
                    log_method(f"{'✅' if success else '❌'} SL order for {symbol}: {response}")
                    return

            except Exception as e:
                self.logger.error(f"Error building or submitting SL order for {symbol}: {e}", exc_info=True)
                return

    def _extract_price(self, order: dict) -> Decimal:
        """
        Extracts the appropriate SL/limit price from various open order structures.
        """
        try:
            info = order.get("info", {}) or {}
            order_config = info.get("order_configuration", {})

            if "trigger_bracket_gtc" in order_config:
                sl_price = order_config["trigger_bracket_gtc"].get("stop_loss_price") \
                           or order_config["trigger_bracket_gtc"].get("stop_trigger_price")
                return Decimal(sl_price)

            if "limit_limit_gtc" in order_config:
                return Decimal(order_config["limit_limit_gtc"].get("limit_price"))

            # Fallback to legacy formats or simplified views
            return Decimal(
                order.get("stop_loss_price")
                or order.get("stop_trigger_price")
                or order.get("limit_price")
                or order.get("price")
                or "0"
            )
        except Exception:
            self.logger.warning(f"⚠️ Failed to extract SL price from order: {order}")
            return Decimal("0")

    # async def reconcile_with_rest(self, trading_pair: str = None):
    #     rest_orders = await self.coinbase_api.fetch_open_orders(product_id=trading_pair.replace("/", "-") if trading_pair else None)
    #     tracker_orders = await self.get_open_orders_from_tracker(trading_pair)
    #
    #     rest_ids = {o["id"] for o in rest_orders if "id" in o}
    #     tracker_ids = {o["order_id"] for o in tracker_orders if "order_id" in o}
    #
    #     extra = tracker_ids - rest_ids
    #     missing = rest_ids - tracker_ids
    #
    #     self.logger.info(f"🧾 Reconciliation result: extra_in_tracker={extra}, missing_in_tracker={missing}")
    #
    # async def refresh_open_orders(self, trading_pair=None):
    #     """
    #     Retain the method but mark as a "manual reconciliation" tool (only use if desync suspected)
    #
    #     Refresh open orders using the REST API, cross-check them with order_tracker,
    #     and remove obsolete orders from the tracker.
    #     Responsibilities:
    #     -Fetches open orders via REST API
    #     -Compares API orders with order_tracker
    #     -Updates order_tracker with normalized data
    #     -Saves the updated order_tracker
    #     -Returns a DataFrame of open orders
    #
    #     Args:
    #         trading_pair (str): Specific trading pair to check for open orders (e.g., 'BTC/USD').
    #
    #     Returns:
    #         tuple: (DataFrame of all open orders, has_open_order (bool), updated order tracker)
    #     """
    #     try:
    #         max_retries = 3
    #         all_open_orders = []
    #
    #         for attempt in range(max_retries):
    #             all_open_orders = await self.coinbase_api.fetch_open_orders(
    #                 product_id=trading_pair.replace("/", "-") if trading_pair else None
    #             )
    #
    #             if all_open_orders or len(all_open_orders) == 0:
    #                 if len(all_open_orders) == 0:
    #                     print(f"⚠️ Attempt {attempt + 1}: No open orders found.")
    #                 break  # ✅ Stop retrying if orders are found or there are no open orders
    #             await asyncio.sleep(2)  # Small delay before retrying
    #
    #         # ✅ Retrieve the existing order tracker
    #
    #
    #         order_tracker_master = self.listener.order_management.get('order_tracker', {})
    #
    #
    #         # � If API fails to return orders, DO NOT remove everything—fallback to `order_tracker`
    #         if not all_open_orders:
    #             print("❌ No open orders found from API! Using cached order_tracker...")
    #             all_open_orders = list(order_tracker_master.values())
    #
    #         # ✅ Cross-check API-fetched order IDs with existing order_tracker IDs
    #         fetched_order_ids = {order.get('id') for order in all_open_orders if order.get('id')}
    #         existing_order_ids = set(order_tracker_master.keys())
    #
    #         # ✅ Identify obsolete orders to remove (only if API was successful)
    #         if all_open_orders:
    #             obsolete_order_ids = existing_order_ids - fetched_order_ids
    #             for obsolete_order_id in obsolete_order_ids:
    #                 #print(f"� Removing obsolete order: {obsolete_order_id}")# debug
    #                 del order_tracker_master[obsolete_order_id]
    #
    #         def parse_float(value, default=0.0):
    #             try:
    #                 return float(value) if value not in (None, '', 'null') else default
    #             except (ValueError, TypeError):
    #                 return default
    #
    #         # ✅ Update order tracker with new API data
    #         for order in order_tracker_master.values():
    #             order_id = order.get('order_id')
    #             if order_id:
    #                 created_time_str = order.get('datetime') or order.get('info', {}).get('created_time')
    #                 if created_time_str:
    #                     created_time = datetime.fromisoformat(created_time_str.replace("Z", "+00:00"))
    #                     now = datetime.now(timezone.utc)
    #                     order_duration = round((now - created_time).total_seconds() / 60, 2)
    #                 else:
    #                     order_duration = None
    #
    #                 # Updated structure for consistency
    #                 order_tracker_master[order_id] = {
    #                     'order_id': order_id,
    #                     'symbol': order.get('symbol'),
    #                     'side': order.get('side').upper(),
    #                     'type': order.get('type').upper() if order.get('type') else 'LIMIT',
    #                     'status': order.get('status').upper(),
    #                     'filled': parse_float(order.get('filled', 0)),
    #                     'remaining': parse_float(order.get('remaining')),
    #                     'amount': parse_float(order.get('amount', 0)),
    #                     'price': parse_float(order.get('price', 0)),
    #                     'triggerPrice': parse_float(order.get('triggerPrice')),
    #                     'stopPrice': parse_float(order.get('stopPrice')),
    #                     'datetime': order.get('datetime'),
    #                     'order_duration': order_duration,
    #                     'trigger_status': order.get('info', {}).get('trigger_status', 'Not Active'),
    #                     'clientOrderId': order.get('clientOrderId'),
    #                     'info': order.get('info', {}),
    #                     'limit_price': parse_float(order.get('info', {}).get('order_configuration', {})
    #                                                .get('limit_limit_gtc', {})
    #                                                .get('limit_price'))
    #                 }
    #
    #         # ✅ Ensure latest data is stored
    #
    #         # ✅ Save updated tracker
    #         self.listener.order_management['order_tracker'] = order_tracker_master
    #         # 🔄 Push updated order management into shared_data_manager
    #         await self.shared_data_manager.update_shared_data(self.listener.market_data, self.listener.order_management) # <--added to ensure update
    #
    #         # ✅ Check if there is an open order for the specific `trading_pair`
    #         has_open_order = any(order['symbol'] == trading_pair for order in all_open_orders) if trading_pair else bool(
    #             all_open_orders)
    #
    #         return pd.DataFrame(all_open_orders), has_open_order, order_tracker_master
    #
    #     except Exception as e:
    #         self.logger.error(f"Failed to refresh open orders: {e}", exc_info=True)
    #         return pd.DataFrame(), False, self.listener.order_management['order_tracker']

