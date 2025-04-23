import asyncio
import json
import math
from decimal import Decimal, getcontext

from Config.config_manager import CentralConfig as Config

getcontext().prec = 10


class WebSocketMarketManager:
    def __init__(self, listener, exchange, ccxt_api, logger_manager, coinbase_api, profit_data_manager, order_type_manager, shared_utils_print,
                 shared_utils_precision, shared_utils_utility, shared_utils_debugger, trailing_stop_manager, order_book_manager, snapshot_manager,
                 trade_order_manager, ohlcv_manager, shared_data_manager):

        self.config = Config()
        self.listener = listener
        self.shared_data_manager = shared_data_manager
        self.exchange = exchange
        self.ccxt_api = ccxt_api
        self.coinbase_api = coinbase_api
        self.logger = logger_manager  # 🙂
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
        self.order_books = {}

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
        self.passive_order_manager = None
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
        # self.api_channels = self.config.load_channels()
        self.subscribed_channels = set()
        self.product_ids = set()
        self.pending_requests = {}  # Track pending requests for query-answer protocol
        self._currency_pairs_ignored = self.config.currency_pairs_ignored
        self.count = 0

        # Data managers
        self.ohlcv_manager = ohlcv_manager

    @property
    def hodl(self):
        return self._hodl

    def set_websocket_manager(self, manager):
        self.websocket_manager = manager

    async def process_user_channel(self, data):
        """Process real-time updates from the user channel."""
        try:
            events = data.get("events", [])
            if not isinstance(events, list):
                self.logger.error("Invalid structure for 'events'. Expected a list.")
                return

            profit_data_list = []
            print_order_tracker = {}
            market_data_snapshot, order_management_snapshot = await self.snapshot_manager.get_snapshots()
            spot_position = market_data_snapshot.get('spot_positions', {})
            usd_entry = spot_position.get('USD')
            if usd_entry:
                usd_balance = Decimal(usd_entry.get('available_to_trade_fiat', 0))
                if usd_balance:
                    self.shared_data_manager.order_management['usd_balance'] = usd_balance
                    self.logger.debug(f' USD Balance cached: ${usd_balance}')

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
                    if status == "UPDATE":
                        pass
                    # ✅ Handle order status changes
                    if status in {"PENDING", "OPEN"}:
                        print_order_tracker = await self.process_order_for_tracker(order, profit_data_list, event_type)
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
                    asset_balance = Decimal(spot_position.get(asset, {}).get('total_balance_crypto', 0))
                    status_of_order = order.get('status')

                    filled_value = Decimal(order.get("filled_value", 0))  # Ensure safe Decimal conversion
                    required_prices = {
                        'avg_price': avg_price,
                        'cost_basis': cost_basis,
                        'asset_balance': asset_balance,
                        'current_price': None,
                        'profit': None,
                        'profit_percentage': None,
                        'status_of_order': status_of_order
                    }

                    base_deci, quote_deci, _, _ = self.shared_utils_precision.fetch_precision(symbol)

                    profit = await self.profit_data_manager.calculate_profitability(asset, required_prices, current_prices, usd_pairs)
                    profit_value = self.shared_utils_precision.adjust_precision(base_deci, quote_deci,
                                                                                profit.get('profit'), 'quote')
                    print(f"� Order {status} {symbol} profit: {profit_value:.2f}")
                    # ✅ Buy BTC when profit is between $1.00 and $2.00
                    if status == "FILLED" and asset not in self.hodl:
                        if Decimal(1.0) < profit_value < Decimal(2.0):
                            btc_order_data = await self.trade_order_manager.build_order_data('Websocket', 'profit', 'BTC', 'BTC/USD', None, None)
                            btc_order_data.trigger = "profitable filled order"
                            print(f'')
                            print(f' 🟠️ process_user_channel - Order Data: 🟠   {btc_order_data.debug_summary(verbose=True)}  ')  # Debug
                            print(f'')
                            order_success, response_msg = await self.trade_order_manager.place_order(btc_order_data)

                            # response, tp, sl = await self.order_type_manager.process_limit_and_tp_sl_orders("WebSocket", btc_order_data)
                            print(f"DEBUG: BTC Order Response: {response_msg}")

                        elif profit_value > Decimal(2.0):
                            eth_order_data = await self.trade_order_manager.build_order_data('Websocket', 'profit', 'ETH', 'ETH/USD', None, None)
                            eth_order_data.trigger = "profitable filled order"
                            print(f'')
                            print(f' 🟠️ process_user_channel - Order Data: 🟠   {eth_order_data.debug_summary(verbose=True)}  ')  # Debug
                            print(f'')
                            order_success, response_msg = await self.trade_order_manager.place_order(eth_order_data)
                            print(f"DEBUG: ETH Order Response: {response_msg}")

        except Exception as channel_error:
            self.logger.error(f"Error processing user channel data: {channel_error}", exc_info=True)

    async def process_ticker_batch_update(self, data):
        try:
            for event in data.get("events", []):
                for ticker in event.get("tickers", []):
                    await self._process_single_ticker(ticker)
        except Exception as e:
            self.logger.error(f"Error processing ticker_batch data: {e}", exc_info=True)

    async def _process_single_ticker(self, ticker):
        try:
            product_id = ticker.get("product_id")
            symbol = product_id.split("-")[0]
            current_price = Decimal(ticker.get("price", "0"))
            base_volume = Decimal(ticker.get("volume_24_h", "0"))
            usd_volume = base_volume * current_price

            if self.passive_order_manager and usd_volume < Decimal(600000) and usd_volume > Decimal(100000):
                await self.passive_order_manager.place_passive_orders(asset=symbol, product_id=product_id)

            # Fetch historical data
            oldest_close, latest_close = await self.ohlcv_manager.fetch_last_5min_ohlcv(product_id, limit=5)
            volatility, adaptive_threshold = await self.ohlcv_manager.fetch_volatility_5min(product_id, limit=5)

            if not all([oldest_close, latest_close, volatility, adaptive_threshold]):
                return

            try:
                log_roc = Decimal(math.log(float(latest_close / oldest_close))) * 100
            except (ValueError, ZeroDivisionError) as e:
                self.logger.error(f"Log ROC calculation error for {product_id}: {e}")
                return

            temp_base_deci = len(ticker.get('low_24_h', '0').split('.')[-1])
            precision = Decimal(f'1.{"0" * temp_base_deci}')
            log_roc = log_roc.quantize(precision)
            volatility = Decimal(volatility).quantize(precision)
            adaptive_threshold = Decimal(adaptive_threshold).quantize(precision)

            if log_roc >= self._roc_5min and volatility >= adaptive_threshold:
                print(f"✅ ROC={log_roc:.2f}%, Vol={volatility:.2f} ≥ Adaptive={adaptive_threshold:.2f} — Execute trade")
                trading_pair = product_id.replace("-", "/")
                symbol = trading_pair.split("/")[0]
                trigger_note = f'ROC:{log_roc} %'

                roc_order_data = await self.trade_order_manager.build_order_data(
                    source='Websocket',
                    trigger=trigger_note,
                    asset=symbol,
                    trading_pair=trading_pair,
                    limit_price=None,
                    stop_price=None
                )

                if roc_order_data:
                    roc_order_data.trigger = "roc"
                    print(f'\n� Order Data:\n{roc_order_data.debug_summary(verbose=True)}\n')
                    order_success, response_msg = await self.trade_order_manager.place_order(roc_order_data)
                    print(f"‼️ ROC ALERT: {product_id} increased by {log_roc:.2f}% in 5 minutes. A buy order was placed!")
            else:
                print(f"⛔ Skipped {product_id}: ROC={log_roc}%, Passed ROC={log_roc >= self._roc_5min}, "
                      f"Vol={volatility}, Adaptive={adaptive_threshold}, Passed Vol={volatility >= adaptive_threshold} ⛔")

        except Exception as e:
            self.logger.error(f"Error in _process_single_ticker for {ticker.get('product_id')}: {e}", exc_info=True)

    async def process_order_for_tracker(self, order, profit_data_list, event_type):
        try:
            async with self.order_tracker_lock:
                # Step 1: Fetch a snapshot of the shared data

                market_data_snapshot, order_management_snapshot = await self.snapshot_manager.get_snapshots()

                # Step 2: Work with order_tracker inside the snapshot
                order_tracker = order_management_snapshot.get('order_tracker', {})
                spot_position = market_data_snapshot.get('spot_positions', {})
                current_prices = market_data_snapshot.get('current_prices', {})
                usd_pairs = market_data_snapshot.get('usd_pairs', {})

                normalized = self.shared_data_manager.normalize_raw_order(order)
                if not normalized:
                    sample = json.dumps(order, indent=2)[:500]
                    self.logger.warning(f"❌ Could not normalize order (truncated):\n{sample}")
                    return

                # Extract basic order info
                order_id = normalized["order_id"]
                type = normalized["type"]
                symbol = normalized["symbol"]
                asset = symbol.split("/")[0]
                status = normalized["status"]
                side = normalized.get("side", "UNKNOWN")
                # avg_price = normalized.get("average_price", Decimal(0))
                # amount = normalized.get("amount", Decimal(0))
                # limit_price = normalized.get("limit_price")
                # stop_price = normalized.get("stop_price")

                if not order_id or not symbol:
                    self.logger.warning(f"Invalid order data: {order}")
                    return

                # Precision
                base_deci, quote_deci, _, _ = self.shared_utils_precision.fetch_precision(asset)

                # Price info fallback logic
                # initial_price = Decimal(order.get('initial_price') or
                #                         spot_position.get(asset, {}).get('average_entry_price', {}).get('value', 0))
                avg_price = Decimal(order.get('avg_price') or
                                    spot_position.get(asset, {}).get('average_entry_price', {}).get('value', 0))
                cost_basis = Decimal(spot_position.get(asset, {}).get('cost_basis', {}).get('value', 0))
                asset_balance = Decimal(spot_position.get(asset, {}).get('total_balance_crypto', 0))

                # limit_price = Decimal(order.get('limit_price', 0)) if order.get('limit_price') else None
                # stop_price = Decimal(order.get('stop_price', 0)) if order.get('stop_price') else None
                # amount = Decimal(order.get('leaves_quantity', 0)) if order.get('leaves_quantity') else None

                # Profit calc
                status_of_order = f"{order.get('order_type', 'UNKNOWN')}/{side}/{status}"
                required_prices = {
                    'avg_price': avg_price,
                    'cost_basis': cost_basis,
                    'asset_balance': asset_balance,
                    'current_price': None,
                    'profit': None,
                    'profit_percentage': None,
                    'status_of_order': status_of_order
                }

                profit = await self.profit_data_manager.calculate_profitability(asset, required_prices, current_prices, usd_pairs)
                if profit and profit.get("profit"):
                    normalized["profit"] = profit
                    profit_data_list.append(profit)

                self.profit_data_manager.consolidate_profit_data(profit_data_list)

                # Manage tracker state
                if status in {"OPEN", "PENDING", "ACTIVE"}:
                    order_tracker[order_id] = normalized
                    self.logger.info(f"Order {order_id} added/updated in tracker.")
                elif status in {"FILLED", "CANCELED"}:
                    if order_id in order_tracker:
                        del order_tracker[order_id]
                        self.logger.info(f"Order {order_id} removed from tracker. Status: {status}")
                else:
                    self.logger.warning(f"Unhandled order status: {status}")

                print_order_tracker = order_tracker
                # ✅ Save updated tracker back to SharedDataManager
                order_management_snapshot['order_tracker'] = print_order_tracker
                await self.shared_data_manager.set_order_management(order_management_snapshot)
                return print_order_tracker
        except Exception as e:
            self.logger.error(f"Error processing order in process_order_for_tracker: {e}", exc_info=True)

    def get_best_bid_ask(self, symbol: str):
        """Return best bid and ask prices for a symbol."""
        book = self.order_books.get(symbol)
        if not book:
            return None, None

        try:
            best_bid = max(book["bids"].items(), key=lambda x: float(x[0]))
            best_ask = min(book["asks"].items(), key=lambda x: float(x[0]))
            return best_bid, best_ask
        except (ValueError, KeyError):
            return None, None

    async def _handle_received(self, message):
        # Received = order accepted by engine, not on book yet
        client_oid = message.get("client_oid")
        if client_oid:
            self.logger.debug(f"Order received: {client_oid}")

    async def _handle_open(self, message):
        # Order now open on the order book
        order_id = message.get("order_id")
        remaining = message.get("remaining_size")
        price = message.get("price")
        side = message.get("side")
        self.logger.debug(f"Order open: {order_id} at {price} ({remaining}) [{side}]")

    async def _handle_done(self, message):
        order_id = message.get("order_id")
        reason = message.get("reason")
        self.logger.debug(f"Order done: {order_id}, reason: {reason}")

    async def _handle_match(self, message):
        price = message.get("price")
        size = message.get("size")
        maker_id = message.get("maker_order_id")
        taker_id = message.get("taker_order_id")
        self.logger.debug(f"Match: {size} at {price} between {maker_id} and {taker_id}")

    async def _handle_change(self, message):
        order_id = message.get("order_id")
        old_size = message.get("old_size")
        new_size = message.get("new_size")
        reason = message.get("reason")
        self.logger.debug(f"Order change: {order_id} ({old_size} → {new_size}) Reason: {reason}")

    async def _handle_activate(self, message):
        order_id = message.get("order_id")
        stop_price = message.get("stop_price")
        self.logger.debug(f"Stop order activated: {order_id} at stop price {stop_price}")

    # async def add_to_order_tracker(self, items, order_management):
    #     """
    #     Adds or updates an active order in the order tracker, ensuring compatibility with trailing stop logic.
    #
    #     Args:
    #         items (dict): Order data to add or update in the tracker.
    #         items['order_id'] (str): Unique identifier for the order.
    #         items['symbol'] (str): Trading pair (e.g., 'BTC/USD').
    #         items['side'] (str): Order side ('buy or 'sell).
    #         items['stop_price'] (Decimal): Stop price for the order.
    #         items['avg_price'] (Decimal): Average price of the order.
    #         items['amount'] (Decimal): Order amount.
    #         items['limit_price'] (Decimal): Limit price for the order.
    #         order_management (dict): Reference to the master `order_management` structure.
    #     """
    #     try:
    #         order_id= items.get('order_id')
    #         symbol = items.get('symbol')
    #         side = items.get('side')
    #         limit_price = items.get('limit_price')
    #         amount = items.get('amount')
    #         stop_price = items.get('stop_price')
    #
    #         order_tracker = order_management.get('order_tracker', {})
    #
    #         if not order_id or not symbol:
    #             self.logger.warning(f"Invalid order data: order_id={items.get('order_id')}, symbol={symbol}")
    #             return
    #         initial_price = 0
    #         if side == 'buy':
    #             initial_price = limit_price
    #         if order_id not in order_tracker:
    #             # Add a new order to the tracker
    #             order_tracker[order_id] = {
    #                 'symbol': symbol,
    #                 'initial_price': initial_price,
    #                 'current_price': limit_price,
    #                 'amount': amount,
    #                 'trailing_stop_price': stop_price,
    #                 'limit_price': limit_price,
    #                 'profit': 0,
    #                 'trailing_stop_active': True  # Activate trailing stop logic
    #             }
    #             self.logger.info(f"Order {order_id} added to tracker: {order_tracker[order_id]}")
    #         else:
    #             # Update existing order if there are changes
    #             existing_order = order_tracker[order_id]
    #             updated_data = {}
    #             if (existing_order.get('info',{}).
    #                     get('order_configuration',{}).
    #                     get('stop_limit_stop_limit_gtc',{}).
    #                     get('stop_price') != stop_price):
    #                 updated_data['trailing_stop_price'] = stop_price
    #             if (existing_order.get('info',{}).
    #                     get('order_configuration',{}).
    #                     get('stop_limit_stop_limit_gtc',{}).
    #                     get('limit_price') != limit_price):
    #                 updated_data['limit_price'] = limit_price
    #
    #             if updated_data:
    #                 order_tracker[order_id].update(updated_data)
    #                 self.logger.info(f"Order {order_id} updated with: {updated_data}")
    #
    #         # Save updated tracker back to `order_management`
    #         order_management['order_tracker'] = order_tracker
    #
    #     except Exception as e:
    #         self.logger.error(f"Error adding order to tracker: {e}", exc_info=True)
