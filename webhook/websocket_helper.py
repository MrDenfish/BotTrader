import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Dict, Any

import pandas as pd
from coinbase import jwt_generator

from Config.config_manager import CentralConfig as Config
from webhook.webhook_validate_orders import OrderData


class WebSocketHelper:
    """
            WebSocketHelper is responsible for managing WebSocket connections and API integrations.
            """
    def __init__(
            self, listener, websocket_manager, exchange, ccxt_api, logger_manager, coinbase_api, profit_data_manager,
            order_type_manager, shared_utils_print, shared_utils_precision, shared_utils_utility, shared_utils_debugger,
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
            print(json.dumps(data, indent=2)) # debug
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
            async with (self.subscription_lock):
                if not self.market_ws:
                    self.logger.error("❌ Market WebSocket is None! Subscription aborted.")
                    return

                self.logger.info(f"� Subscribing to Market Channels: {list(self.market_channels)}")

                # snapshot = await self.snapshot_manager.get_market_data_snapshot()
                # market_data = snapshot.get("market_data", {})
                # product_ids = [key.replace('/', '-') for key in
                #                market_data.get('current_prices', {}).keys()] or ["BTC-USD"]

                if not self.product_ids:
                    self.logger.warning("⚠️ No valid product IDs found. Subscription aborted.")
                    return

                for channel in self.market_channels:
                    subscription_message = {
                        "type": "subscribe",
                        "product_ids": self.product_ids,
                        "channel": channel
                    }
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
                snapshot = await self.snapshot_manager.get_market_data_snapshot()
                market_data = snapshot.get("market_data", {})
                self.product_ids = [key.replace('/', '-') for key in
                               market_data.get('current_prices', {}).keys()] or ["BTC-USD"]

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
            usd_avail = order_management_snapshot.get('non_zero_balances', {})['USD']['available_to_trade_crypto']
            profit_data_list = []
            profit_data_list_new = []

            async with (self.order_tracker_lock):
                order_tracker_snapshot = dict(order_management_snapshot.get("order_tracker", {}))
                for order_id, raw_order in order_tracker_snapshot.items():
                    order_id = raw_order.get('id', 'UNKNOWN')
                    order_data = OrderData.from_dict(raw_order)

                    try:
                        symbol = order_data.trading_pair
                        asset = symbol.split('/')[0]
                        # ✅ Fetch precision values for the asset
                        precision_data = self.shared_utils_precision.fetch_precision(symbol)
                        order_data.base_decimal = precision_data[0]
                        order_data.quote_decimal = precision_data[1]
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
                                new_order_data = await self.trade_order_manager.build_order_data('Websocket',
                                                                                                 'trailing_stop',
                                                                                                 asset, symbol,
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
                                    print(f"🔆TP adjustment: Current price {current_price} > TP {old_limit_price}, "
                                          f"reconfiguring TP/SL for {symbol} 🔆")
                                    # Cancel the stale TP/SL order
                                    await self.listener.order_manager.cancel_order(order_data.order_id, symbol)
                                    # Create new TP/SL order
                                    new_order_data = await self.trade_order_manager.build_order_data(
                                        'Websocket','profit', asset,symbol, old_limit_price, None)
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

    async def monitor_untracked_assets(self, market_data_snapshot: Dict[str, Any],
                                       order_management_snapshot: Dict[str,Any]):
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

            await asyncio.sleep(30)

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

            def parse_float(value, default=0.0):
                try:
                    return float(value) if value not in (None, '', 'null') else default
                except (ValueError, TypeError):
                    return default

            # ✅ Update order tracker with new API data
            for order in all_open_orders:
                order_id = order.get('id')
                if order_id:
                    created_time_str = order.get('info', {}).get('created_time')
                    if created_time_str:
                        created_time = datetime.fromisoformat(created_time_str.replace("Z", "+00:00"))
                        now = datetime.now(timezone.utc)
                        order_duration = round((now - created_time).total_seconds() / 60, 2)
                    else:
                        order_duration = None

                    # Updated structure for consistency
                    order_tracker_master[order_id] = {
                        'order_id': order_id,
                        'symbol': order.get('symbol'),
                        'side': order.get('side').upper(),
                        'type': order.get('type').upper() if order.get('type') else 'LIMIT',
                        'status': order.get('status').upper(),
                        'filled': parse_float(order.get('filled', 0)),
                        'remaining': parse_float(order.get('remaining')),
                        'amount': parse_float(order.get('amount', 0)),
                        'price': parse_float(order.get('price', 0)),
                        'triggerPrice': parse_float(order.get('triggerPrice')),
                        'stopPrice': parse_float(order.get('stopPrice')),
                        'datetime': order.get('datetime'),
                        'order_duration': order_duration,
                        'trigger_status': order.get('info', {}).get('trigger_status', 'Not Active'),
                        'clientOrderId': order.get('clientOrderId'),
                        'info': order.get('info', {}),
                        'limit_price': parse_float(order.get('info', {}).get('order_configuration', {})
                                                   .get('limit_limit_gtc', {})
                                                   .get('limit_price'))
                    }

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
