import asyncio
import json
import math
import time
from datetime import datetime
from decimal import Decimal, getcontext

from Config.config_manager import CentralConfig as Config
from webhook.webhook_validate_orders import OrderData

getcontext().prec = 10


class WebSocketMarketManager:
    def __init__(self, listener, exchange, ccxt_api, logger_manager, coinbase_api, profit_data_manager,
                 order_type_manager, shared_utils_print, shared_utils_color, shared_utils_precision, shared_utils_utility,
                 shared_utils_debugger, trailing_stop_manager, order_book_manager, snapshot_manager,
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
        self._order_size_fiat = Decimal(self.config.order_size_fiat)
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
        self.shared_utils_print = shared_utils_print
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
        """
        Handle Coinbase *user*-channel events.

        ▸ Replaces order_tracker on snapshot
        ▸ Merges order changes on updates (create, cancel, fill)
        ▸ Tracks SL/TP child orders
        ▸ Triggers handle_order_fill on filled orders
        """
        try:
            events = data.get("events", [])
            if not isinstance(events, list):
                self.logger.error("user-payload missing events list")
                return

            mkt_snap, _ = await self.snapshot_manager.get_snapshots()
            spot_pos = mkt_snap.get("spot_positions", {})
            cur_prices = mkt_snap.get("bid_ask_spread", {})
            usd_pairs = mkt_snap.get("usd_pairs_cache", {})

            order_tracker = await self.shared_data_manager.get_order_tracker()

            for ev in events:
                ev_type = ev.get("type", "").lower()
                orders = ev.get("orders", [])
                if not isinstance(orders, list) or not orders:
                    continue

                if ev_type == "snapshot":
                    new_tracker = {}
                    for order in orders:
                        order_id = order.get("order_id")
                        symbol = order.get("product_id")
                        status = (order.get("status") or "").upper()
                        if not order_id or not symbol:
                            continue

                        normalized = self.shared_data_manager.normalize_raw_order(order)
                        if normalized and status in {"PENDING", "OPEN", "ACTIVE"}:
                            new_tracker[order_id] = normalized
                            self.logger.info(f"📥 Snapshot tracked: {order_id} | {symbol} | {status}")

                    await self.shared_data_manager.set_order_management({"order_tracker": new_tracker})
                    await self.shared_data_manager.save_data()
                    print(f"📸 Snapshot processed and persisted: {len(new_tracker)} open orders") # debug
                    return

                for order in orders:
                    order_id = order.get("order_id")
                    parent_id = order.get("parent_order_id") or order.get("parent_id")
                    symbol = order.get("product_id")
                    side = (order.get("order_side") or "").lower()
                    status = (order.get("status") or "").upper()

                    if not order_id or not symbol:
                        continue

                    if status in {"CANCELLED", "CANCEL_QUEUED"}:
                        try:
                            await self.shared_data_manager.trade_recorder.delete_trade(order_id)
                            self.logger.info(f"❎ {order_id} {status} → deleted from DB")
                        except Exception:
                            self.logger.error("❌ delete_trade failed", exc_info=True)

                        order_tracker.pop(order_id, None)
                        continue

                    if parent_id and side == "sell" and ev_type in {"order_created", "order_activated", "order_filled"}:
                        try:
                            trade = {
                                "order_id": order_id,
                                "parent_id": parent_id,
                                "symbol": symbol,
                                "side": "sell",
                                "price": order.get("limit_price") or order.get("price"),
                                "amount": order.get("size") or order.get("filled_size") or order.get("order_size") or 0,
                                "status": status.lower(),
                                "order_time": order.get("event_time") or order.get("created_time") or datetime.utcnow().isoformat(),
                                "trigger": {"trigger": "tp" if order.get("order_type") == "TAKE_PROFIT" else "sl"},
                                "source": "websocket",
                                "total_fees": order.get("total_fees")
                            }
                            await self.shared_data_manager.trade_recorder.record_trade(trade)
                            print(f"TP/SL child stored → {order_id}") #debug
                        except Exception:
                            self.logger.error("record_trade failed", exc_info=True)

                    order['source'] = 'websocket'
                    normalized = self.shared_data_manager.normalize_raw_order(order)
                    if normalized:
                        if status in {"PENDING", "OPEN", "ACTIVE"}:
                            order_tracker[order_id] = normalized
                        elif status == "FILLED":
                            order_tracker.pop(order_id, None)

                    if status == "FILLED":
                        try:
                            fills = order.get("fills", [])
                            symbol = order.get("product_id")
                            parent_id = order.get("order_id")
                            side = (order.get("order_side") or "").lower()

                            # Fallback single-record if no fills provided
                            if not fills:
                                unique_fill_id = f"{parent_id}-FALLBACK"
                                self.logger.warning(f"⚠️ No fills found for {parent_id} — using fallback record")

                                await self.shared_data_manager.trade_recorder.record_trade({
                                    "order_id": unique_fill_id,
                                    "parent_id": parent_id if side == "buy" else await self.shared_data_manager.trade_recorder.find_latest_unlinked_buy(
                                        symbol),
                                    "symbol": symbol,
                                    "side": side,
                                    "price": order.get("avg_price") or order.get("price"),
                                    "amount": order.get("filled_size") or order.get("order_size") or order.get("cumulative_quantity") or 0,
                                    "status": "filled",
                                    "order_time": order.get("event_time") or order.get("created_time") or datetime.utcnow().isoformat(),
                                    "trigger": {"trigger": order.get("order_type") or "market"},
                                    "source": "websocket",
                                    "total_fees": order.get("total_fees")
                                })
                            else:
                                for i, fill in enumerate(fills):
                                    unique_fill_id = f"{parent_id}-FILL-{i + 1}"
                                    fill_price = fill.get("price")
                                    fill_size = fill.get("size")
                                    fill_fee = fill.get("fee") or 0
                                    fill_time = fill.get("trade_time") or fill.get("event_time") or fill.get("created_time")

                                    await self.shared_data_manager.trade_recorder.record_trade({
                                        "order_id": unique_fill_id,
                                        "parent_id": parent_id if side == "buy" else await self.shared_data_manager.trade_recorder.find_latest_unlinked_buy(
                                            symbol),
                                        "symbol": symbol,
                                        "side": side,
                                        "price": fill_price,
                                        "amount": fill_size,
                                        "status": "filled",
                                        "order_time": fill_time or datetime.utcnow().isoformat(),
                                        "trigger": {"trigger": order.get("order_type") or "market"},
                                        "source": "websocket",
                                        "total_fees": fill_fee
                                    })
                                    self.logger.info(f"🧾 Fill recorded: {unique_fill_id} | {symbol} {side.upper()} {fill_size}@{fill_price}")

                            # Optional: profitability logging for SELLs
                            if side == "sell":
                                asset = symbol.split("-")[0]
                                base_d, quote_d, *_ = self.shared_utils_precision.fetch_precision(symbol)
                                mkt_snap, _ = await self.snapshot_manager.get_snapshots()
                                spot_pos = mkt_snap.get("spot_positions", {})
                                cur_prices = mkt_snap.get("bid_ask_spread", {})
                                usd_pairs = mkt_snap.get("usd_pairs_cache", {})

                                avg_p = Decimal(order.get("avg_price") or 0)
                                cost_bs = Decimal(spot_pos.get(asset, {}).get("cost_basis", {}).get("value", 0))
                                bal = Decimal(spot_pos.get(asset, {}).get("total_balance_crypto", 0))

                                req = {
                                    "avg_price": avg_p,
                                    "cost_basis": cost_bs,
                                    "asset_balance": bal,
                                    "current_price": None,
                                    "profit": None,
                                    "profit_percentage": None,
                                    "status_of_order": status,
                                }
                                p = await self.profit_data_manager.calculate_profitability(asset, req, cur_prices, usd_pairs)
                                if p and p.get("profit"):
                                    pf = self.shared_utils_precision.adjust_precision(base_d, quote_d, p["profit"], "quote")
                                    self.logger.info(f"💰 {symbol} SELL profit {pf:.2f} USD")

                            order_data = OrderData.from_dict(order)
                            await self.listener.handle_order_fill(order_data)

                        except Exception:
                            self.logger.error("❌ Error processing filled order (incremental fills)", exc_info=True)

            await self.shared_data_manager.set_order_management({"order_tracker": order_tracker})
            await self.shared_data_manager.save_data()
            print(f"🪲 Final tracker updated and persisted → {len(order_tracker)} orders DEBUG 🪲") # debug

        except Exception:
            self.logger.error("process_user_channel error", exc_info=True)

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
            now = time.time()
            if not self.passive_order_manager or not hasattr(self.passive_order_manager, "passive_order_tracker"):
                self.logger.warning(f"⚠️ passive_order_manager or tracker not available for {ticker.get('product_id')}")
                return
            last = self.passive_order_manager.passive_order_tracker.get(product_id, {}).get("timestamp", 0)
            symbol = product_id.split("-")[0]
            current_price = self.shared_utils_precision.safe_decimal(ticker.get("price", 0)) or Decimal("0")
            base_volume = self.shared_utils_precision.safe_decimal((ticker.get("volume_24_h", 0)) or  Decimal("0"))
            usd_volume = base_volume * current_price

            # call manager at most once every 5 s per symbol
            if now - last > 30:
                asyncio.create_task(  # don’t block the ticker loop
                    self.passive_order_manager.place_passive_orders(
                        asset=symbol,
                        product_id=product_id,
                    )
                )
            # Fetch historical data
            oldest_close, latest_close, avg_close = await self.ohlcv_manager.fetch_last_5min_ohlcv(product_id)
            volatility, adaptive_threshold = await self.ohlcv_manager.fetch_volatility_5min(product_id)

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
                trigger = {"trigger": f"roc", "trigger_note": f"ROC:{log_roc} % "}
                roc_order_data = await self.trade_order_manager.build_order_data(
                    source='websocket',
                    trigger=trigger,
                    asset=symbol,
                    trading_pair=trading_pair,
                    limit_price=None,
                    stop_price=None
                )

                if roc_order_data:
                    print(f'\n� Order Data:\n{roc_order_data.debug_summary(verbose=True)}\n')
                    if roc_order_data.side =='buy':
                        pass
                    order_success, response_msg = await self.trade_order_manager.place_order(roc_order_data)
                    print(f"‼️ ROC ALERT: {product_id} increased by {log_roc:.2f}% 5 minutes. A buy order was placed!")
            else:
                print(f"⛔ Skipped {product_id}: ROC={log_roc}%, Passed ROC={log_roc >= self._roc_5min}, "
                      f"Vol={volatility}, Adaptive={adaptive_threshold}, "
                      f"Passed Vol={volatility >= adaptive_threshold} ⛔")

        except Exception as e:
            self.logger.error(f"Error in _process_single_ticker for {ticker.get('product_id')}: {e}", exc_info=True)

    async def _handle_received(self, message):
        # Received = order accepted by engine, not on book yet
        client_oid = message.get("client_oid")
        if client_oid:
            print(f" 🪲Order received: {client_oid} 🪲")#debug

    async def _handle_open(self, message):
        # Order now open on the order book
        order_id = message.get("order_id")
        remaining = message.get("remaining_size")
        price = message.get("price")
        side = message.get("side")
        print(f" 🪲 Order open: {order_id} at {price} ({remaining}) [{side}] 🪲") #debug

    async def _handle_done(self, message):
        order_id = message.get("order_id")
        reason = message.get("reason")
        print(f" 🪲 Order done: {order_id}, reason: {reason} 🪲") #debug

    async def _handle_match(self, message):
        price = message.get("price")
        size = message.get("size")
        maker_id = message.get("maker_order_id")
        taker_id = message.get("taker_order_id")
        print(f" 🪲 Match: {size} at {price} between {maker_id} and {taker_id} 🪲") #debug

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
