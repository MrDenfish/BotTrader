import asyncio
import json
import math
import time
from datetime import datetime, timezone
from typing import Optional
from decimal import Decimal, getcontext
from TableModels.trade_record import TradeRecord
from Config.config_manager import CentralConfig as Config
from webhook.webhook_validate_orders import OrderData
from Shared_Utils.logger import get_logger

getcontext().prec = 10


class WebSocketMarketManager:
    def __init__(self, listener, exchange, ccxt_api, logger_manager, coinbase_api, profit_data_manager,
                 order_type_manager, shared_utils_print, shared_utils_color, shared_utils_precision, shared_utils_utility,
                 test_debug_maint, order_book_manager, snapshot_manager, trade_order_manager, ohlcv_manager, shared_data_manager,
                 database_session_manager=None):

        self.config = Config()
        self.db_session_manager = database_session_manager
        self.listener = listener
        self.shared_data_manager = shared_data_manager
        self.exchange = exchange
        self.ccxt_api = ccxt_api
        self.coinbase_api = coinbase_api
        self.logger = logger_manager  # 🙂
        self.structured_logger = get_logger('webhook', context={'component': 'websocket_market_manager'})
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
        self.order_type_manager = order_type_manager
        self.trade_order_manager = trade_order_manager
        self.order_book_manager = order_book_manager
        self.snapshot_manager = snapshot_manager

        # Utility functions
        self.shared_utils_print = shared_utils_print
        self.shared_utils_precision = shared_utils_precision
        self.shared_utils_utility = shared_utils_utility
        self.test_debug_maint = test_debug_maint

        # Subscription settings
        # self.api_channels = self.config.load_channels()
        self.subscribed_channels = set()
        self.product_ids = set()
        self.pending_requests = {}  # Track pending requests for query-answer protocol
        self._currency_pairs_ignored = self.config.currency_pairs_ignored
        self.count = 0

        # Data managers
        self.ohlcv_manager = ohlcv_manager

        self.passive_order_semaphore = asyncio.Semaphore(5)
        self._background_tasks = set()

    @property
    def hodl(self):
        return self._hodl

    def set_websocket_manager(self, manager):
        self.websocket_manager = manager

    async def process_user_channel(self, data):
        """Handle Coinbase *user*-channel events with deduplication and fallback improvements."""
        try:
            events = data.get("events", [])
            if not isinstance(events, list):
                self.logger.error("user-payload missing events list")
                return

            mkt_snap, _ = await self.snapshot_manager.get_snapshots()
            order_tracker = await self.shared_data_manager.get_order_tracker()

            queued_trades = []  # ✅ Collect trades here
            queued_ids = set()  # ✅ Prevent duplicate order_ids

            for ev in events:
                ev_type = ev.get("type", "").lower()
                orders = ev.get("orders", [])
                if not isinstance(orders, list) or not orders:
                    continue

                # ✅ SNAPSHOT HANDLING
                if ev_type == "snapshot":
                    new_tracker = {}
                    for order in orders:
                        symbol = order.get("product_id")
                        order_id = order.get("order_id")
                        status = (order.get("status") or "").upper()

                        normalized = self.shared_data_manager.normalize_raw_order(order)
                        if normalized and status in {"PENDING", "OPEN", "ACTIVE"}:
                            new_tracker[order_id] = normalized
                            self.logger.debug(f"📥 Snapshot tracked: {order_id} | {symbol} | {status}")

                    await self.shared_data_manager.set_order_management({"order_tracker": new_tracker})
                    await self.shared_data_manager.save_data()
                    self.logger.info(f"📸 Snapshot processed and persisted: {len(new_tracker)} open orders")
                    return

                # ✅ NON-SNAPSHOT ORDER EVENTS
                for order in orders:
                    order_id = order.get("order_id")
                    parent_id = order.get("parent_order_id") or order.get("parent_id")
                    symbol = order.get("product_id")
                    side = (order.get("order_side") or "").lower()
                    status = (order.get("status") or "").upper()

                    if not order_id or not symbol:
                        continue

                    # -------------------------------
                    # ❌ CANCELLED ORDER HANDLING
                    # -------------------------------
                    if status in {"CANCELLED", "CANCEL_QUEUED"}:
                        try:
                            await self.shared_data_manager.trade_recorder.delete_trade(order_id)
                            self.logger.info(f"❎ Deleted cancelled order: {order_id}")
                        except Exception:
                            self.logger.error("❌ delete_trade failed", exc_info=True)
                        order_tracker.pop(order_id, None)
                        continue

                    # -------------------------------
                    # ✅ ENQUEUE CHILD TP/SL ORDERS
                    # -------------------------------
                    if parent_id and side == "sell" and ev_type in {"order_created", "order_activated", "order_filled"}:
                        trade = await self._build_trade_dict(order, parent_id, side, source="websocket")
                        queued_trades.append(trade)
                        queued_ids.add(trade["order_id"])
                        self.logger.debug(f"TP/SL child stored → {order_id}")

                    # -------------------------------
                    # 🔄 TRACKER UPDATE
                    # -------------------------------
                    order["source"] = "websocket"
                    normalized = self.shared_data_manager.normalize_raw_order(order)
                    normalized["filled_size"] = float(order.get("filled_size") or 0.0)
                    if normalized:
                        if status in {"PENDING", "OPEN", "ACTIVE"}:
                            order_tracker[order_id] = normalized
                        elif status == "FILLED":
                            order_tracker.pop(order_id, None)

                    # -------------------------------
                    # ✅ PRIMARY FILLED ORDER HANDLING
                    # -------------------------------
                    if status == "FILLED":
                        fills = order.get("fills", [])
                        base_id = order_id


                        async with self.db_session_manager.async_session() as session:
                            existing = await session.get(TradeRecord, base_id)

                        if not fills:
                            if existing:
                                self.logger.debug(f"⏭️ Skipping fallback — primary exists: {base_id}")
                                continue

                            self.logger.warning(f"⚠️ No fills, fallback promoted → primary: {base_id}")
                            trade_data = await self._build_trade_dict(order, parent_id, side, fallback=True)
                            queued_trades.append(trade_data)
                            queued_ids.add(base_id)

                        for i, fill in enumerate(fills):
                            fill_size = float(fill.get("size", 0))
                            prev_filled = order_tracker.get(base_id, {}).get("filled_size", 0)
                            if fill_size <= prev_filled:
                                self.logger.debug(f"⏭️ Duplicate partial fill ignored: {base_id}")
                                continue

                            fill_data = await self._build_fill_dict(order, fill, base_id, i + 1, side)
                            if fill_data["order_id"] not in queued_ids:
                                queued_trades.append(fill_data)
                                queued_ids.add(fill_data["order_id"])
                                self.logger.info(f"🧾 Fill recorded: {fill_data['order_id']} | {symbol} {side.upper()} {fill_data['amount']}")

            # -------------------------------
            # ✅ FINALIZE: SORT AND ENQUEUE
            # -------------------------------
            queued_trades.sort(key=lambda x: self._parse_order_time(x["order_time"]))
            for trade in queued_trades:
                await self.shared_data_manager.trade_recorder.enqueue_trade(trade)

            await self.shared_data_manager.set_order_management({"order_tracker": order_tracker})
            await self.shared_data_manager.save_data()
            self.logger.debug(f"🪲 Final tracker updated → {len(order_tracker)} orders")
        except asyncio.CancelledError:
            self.logger.warning("🛑 process_user_channel was cancelled.", exc_info=True)
            raise

        except Exception:
            self.logger.error("process_user_channel error", exc_info=True)

    def _parse_order_time(self, time_value):
        """
        Normalize order_time for sorting queued trades.
        Accepts ISO string or datetime object, returns UTC datetime.
        """
        if isinstance(time_value, datetime):
            return time_value.astimezone(timezone.utc)
        try:
            return datetime.fromisoformat(time_value.replace("Z", "+00:00")).astimezone(timezone.utc)
        except Exception:
            self.logger.warning(f"⚠️ Unable to parse order_time: {time_value}")
            return datetime.min.replace(tzinfo=timezone.utc)

    async def _build_trade_dict(
            self,
            order: dict,
            parent_id: Optional[str],
            side: str,
            fallback: bool = False,
            source: str = "websocket",
    ) -> dict:
        """Builds a normalized trade dictionary from order data."""
        order_id = order.get("order_id")
        symbol = order.get("product_id")
        order_type = order.get("order_type") or "market"

        # Prefer avg/limit/price in that order, same as before
        price = order.get("avg_price") or order.get("limit_price") or order.get("price")
        amount = (
                order.get("filled_size")
                or order.get("size")
                or order.get("cumulative_quantity")
                or 0
        )

        # 🔄 Only fetch a parent if we need to (fallback SELL with no parent)
        if fallback and side == "sell" and not parent_id:
            try:
                parent_id = await self.shared_data_manager.trade_recorder.find_latest_unlinked_buy_id(symbol)
            except Exception as e:
                self.logger.exception("find_latest_unlinked_buy_id failed for %s: %s", symbol, e)
                parent_id = None

        return {
            "order_id": order_id,
            "parent_id": parent_id,
            "symbol": symbol,
            "side": side,
            "price": price,
            "amount": amount,
            "status": "filled",
            "order_time": self._normalize_order_time(
                order.get("event_time") or order.get("created_time")
            ),
            "trigger": {"trigger": order_type},
            "source": source,
            "total_fees": order.get("total_fees", 0),
        }

    async def _build_fill_dict(
            self,
            order: dict,
            fill: dict,
            base_id: str,
            index: int,
            side: str,
    ) -> dict:
        """Builds a fill trade dict from individual fill record."""
        symbol = order.get("product_id")
        fill_order_id = f"{base_id}-FILL-{index}"

        fill_time = fill.get("trade_time") or order.get("event_time")
        amount = float(fill.get("size") or 0)
        price = fill.get("price")
        fee = fill.get("fee", 0)

        if side == "sell":
            try:
                parent_id = await self.shared_data_manager.trade_recorder.find_unlinked_buy_id(symbol)
            except Exception as e:
                self.logger.exception("find_unlinked_buy_id failed for %s: %s", symbol, e)
                parent_id = None
        else:
            parent_id = base_id

        return {
            "order_id": fill_order_id,
            "parent_id": parent_id,
            "symbol": symbol,
            "side": side,
            "price": price,
            "amount": amount,
            "status": "filled",
            "order_time": self._normalize_order_time(fill_time),
            "trigger": {"trigger": order.get("order_type", "market")},
            "source": "websocket",
            "total_fees": fee,  # (optional) consider aligning with `total_fees_usd`
        }

    def _normalize_order_time(self, raw_time) -> str:
        """Converts time input into ISO-8601 string format in UTC."""
        if isinstance(raw_time, datetime):
            return raw_time.astimezone(timezone.utc).isoformat()
        try:
            dt = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
            return dt.astimezone(timezone.utc).isoformat()
        except Exception:
            self.logger.warning(f"⚠️ Failed to normalize order_time: {raw_time}")
            return datetime.now(timezone.utc).isoformat()


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
                task = asyncio.create_task(
                    self._safe_place_passive_order(symbol, product_id)
                )
                self._background_tasks.add(task)
                task.add_done_callback(self._background_tasks.discard)
            # Fetch historical data
            df, oldest_close, latest_close, avg_close = await self.ohlcv_manager.fetch_last_5min_ohlcv(product_id)
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
                self.structured_logger.info(
                    "ROC threshold met - executing trade",
                    extra={
                        'product_id': product_id,
                        'roc_pct': float(log_roc),
                        'volatility': float(volatility),
                        'adaptive_threshold': float(adaptive_threshold)
                    }
                )
                trading_pair = product_id.replace("-", "/")
                symbol = trading_pair.split("/")[0]
                trigger = {"trigger": f"roc", "trigger_note": f"ROC:{log_roc} % "}
                roc_order_data = await self.trade_order_manager.build_order_data(
                    source='websocket',
                    trigger=trigger,
                    asset=symbol,
                    product_id=trading_pair

                )

                if roc_order_data:
                    self.structured_logger.debug(
                        "ROC Order Data",
                        extra={'order_summary': roc_order_data.debug_summary(verbose=True)}
                    )
                    if roc_order_data.side =='buy':
                        pass
                    order_success, response_msg = await self.trade_order_manager.place_order(roc_order_data)
                    self.structured_logger.order_sent(
                        "ROC ALERT - Buy order placed",
                        extra={'product_id': product_id, 'roc_pct': float(log_roc)}
                    )
            else:
                return
                # print(f"⛔ Skipped {product_id}: ROC={log_roc}%, Passed ROC={log_roc >= self._roc_5min}, "
                #       f"Vol={volatility}, Adaptive={adaptive_threshold}, "
                #       f"Passed Vol={volatility >= adaptive_threshold} ⛔") # debug

        except Exception as e:
            self.logger.error(f"Error in _process_single_ticker for {ticker.get('product_id')}: {e}", exc_info=True)

    async def _safe_place_passive_order(self, symbol: str, product_id: str):
        try:
            async with self.passive_order_semaphore:
                await self.passive_order_manager.place_passive_orders(asset=symbol, product_id=product_id)
        except Exception as e:
            self.logger.error(f"🚨 Error placing passive order for {product_id}: {e}", exc_info=True)


    async def _handle_received(self, message):
        # Received = order accepted by engine, not on book yet
        client_oid = message.get("client_oid")
        if client_oid:
            self.structured_logger.debug("Order received", extra={'client_oid': client_oid})

    async def _handle_open(self, message):
        # Order now open on the order book
        order_id = message.get("order_id")
        remaining = message.get("remaining_size")
        price = message.get("price")
        side = message.get("side")
        self.structured_logger.debug(
            "Order open",
            extra={'order_id': order_id, 'price': price, 'remaining': remaining, 'side': side}
        )

    async def _handle_done(self, message):
        order_id = message.get("order_id")
        reason = message.get("reason")
        self.structured_logger.debug("Order done", extra={'order_id': order_id, 'reason': reason})

    async def _handle_match(self, message):
        price = message.get("price")
        size = message.get("size")
        maker_id = message.get("maker_order_id")
        taker_id = message.get("taker_order_id")
        self.structured_logger.debug(
            "Order match",
            extra={'size': size, 'price': price, 'maker_order_id': maker_id, 'taker_order_id': taker_id}
        )

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

    async def shutdown(self):
        """Cleanly cancel and await background passive order tasks."""
        if hasattr(self, "_background_tasks"):
            self.logger.info("🛑 Cancelling passive order tasks...")
            for task in self._background_tasks:
                task.cancel()
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
            self.logger.info(f"✅ {len(self._background_tasks)} passive order tasks cleaned up.")
            self._background_tasks.clear()
