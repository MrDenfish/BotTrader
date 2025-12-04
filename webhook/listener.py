

import json
import time
import uuid
import random
import socket
import asyncio
import websockets
import contextlib
import datetime as dt

from aiohttp import web
from decimal import Decimal

from numpy.f2py.crackfortran import sourcecodeform
from sqlalchemy.sql import text
from sqlalchemy import case, func, or_
from sqlalchemy.exc import DBAPIError
from datetime import datetime, timezone
from typing import Optional, Any, Sequence
from ipaddress import ip_address, ip_network

from Shared_Utils.enum import ValidationCode
from Shared_Utils.print_data import PrintData
from Shared_Utils.print_data import ColorCodes
from Api_manager.api_manager import ApiManager
from Shared_Utils.utility import SharedUtility
from webhook.webhook_utils import TradeBotUtils
from sqlalchemy import  literal, literal_column
from TableModels.trade_record import TradeRecord
from Shared_Utils.precision import PrecisionUtils
from Shared_Utils.alert_system import AlertSystem
from webhook.webhook_manager import WebHookManager
from TestDebugMaintenance.debugger import Debugging
from webhook.websocket_helper import WebSocketHelper
from webhook.webhook_validate_orders import OrderData
from Shared_Utils.dates_and_times import DatesAndTimes
from webhook.webhook_order_types import OrderTypeManager
from MarketDataManager.ohlcv_manager import OHLCVManager
from MarketDataManager.ticker_manager import TickerManager
from webhook.webhook_validate_orders import ValidateOrders
from webhook.webhook_order_manager import TradeOrderManager
from Shared_Utils.snapshots_manager import SnapshotsManager
from sqlalchemy.dialects.postgresql import insert as pg_insert
from ProfitDataManager.profit_data_manager import ProfitDataManager
from webhook.websocket_market_manager import WebSocketMarketManager
from websockets.exceptions import ConnectionClosed, ConnectionClosedError, ConnectionClosedOK, InvalidStatusCode
from Shared_Utils.logger import get_logger



SYNC_INTERVAL = 60  # seconds
SYNC_LOOKBACK = 24

# Module-level logger for global handlers
_module_logger = get_logger('webhook', context={'component': 'listener_global'})



class WebSocketManager:
    def __init__(self, config, listener,coinbase_api, logger_manager, websocket_helper):
        self.config = config
        self.listener = listener
        self.coinbase_api = coinbase_api
        self.logger = logger_manager
        self.websocket_helper = websocket_helper

        # URLs for WebSocket connections
        self.user_ws_url = self.config.load_websocket_api_key().get("user_api_url")
        self.market_ws_url = self.config.load_websocket_api_key().get("market_api_url")

        # Active asyncio tasks for WebSocket streams
        self.market_ws_task = None
        self.user_ws_task = None

        # ✅ Phase 1: Per-stream reconnect attempts
        self.reconnect_attempts_user = 0
        self.reconnect_attempts_market = 0
        self.reconnect_limit = 5

        # ✅ Phase 1: Graceful shutdown event
        self.shutdown_event = asyncio.Event()

        # Single-flight reconnect locks
        self._market_reconnect_lock = asyncio.Lock()
        self._user_reconnect_lock = asyncio.Lock()

    # ===============================================================
    # PUBLIC METHODS
    # ===============================================================

    async def start_websockets(self):
        """Start both Market and User WebSockets."""
        try:
            await self.connect_market_stream()
            await self.connect_user_stream()

            # ✅ Phase 3: Replace periodic restart with smarter health checks
            asyncio.create_task(self.health_check_loop())

            # Keep your existing channel activity monitoring
            asyncio.create_task(self.websocket_helper.monitor_user_channel_activity())
            asyncio.create_task(self.websocket_helper.monitor_market_channel_activity())

        except Exception as e:
            self.logger.error(f"Error starting WebSockets: {e}", exc_info=True)

    async def stop(self):
        """Gracefully stop all WebSocket tasks."""
        self.logger.info("🛑 Stopping all WebSocket tasks...")
        self.shutdown_event.set()
        for task in [self.market_ws_task, self.user_ws_task]:
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    self.logger.info("✅ WebSocket task cancelled cleanly.")

    # ===============================================================
    # CONNECTION MANAGEMENT
    # ===============================================================

    async def connect_market_stream(self):
        """Reconnect the market WebSocket."""
        async with self._market_reconnect_lock:
            if self.market_ws_task and not self.market_ws_task.done():
                self.logger.warning("🔄 Cancelling old market_ws_task...")
                self.market_ws_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self.market_ws_task
                self.logger.info("🧹 Previous market_ws_task closed cleanly.")
            self.market_ws_task = asyncio.create_task(
                self.connect_websocket(self.market_ws_url, is_user_ws=False)
            )

    async def connect_user_stream(self):
        """Reconnect the user WebSocket."""
        async with self._user_reconnect_lock:
            if self.user_ws_task and not self.user_ws_task.done():
                self.logger.warning("🔄 Cancelling old user_ws_task...")
                self.user_ws_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self.user_ws_task
                self.logger.info("🧹 Previous user_ws_task closed cleanly.")
            self.user_ws_task = asyncio.create_task(
                self.connect_websocket(self.user_ws_url, is_user_ws=True)
            )

    async def connect_websocket(self, ws_url: str, is_user_ws: bool = False):
        """
        Establish and manage a WebSocket connection with:
          - DNS refresh before each attempt
          - quick first-frame/ACK gate (8s)
          - idle watchdog (90s) that forces reconnect
          - jittered exponential backoff
          - clean logging for ConnectionResetError and WS closes
        """
        stream = "USER" if is_user_ws else "MARKET"
        last_message_time = time.time()
        MAX_ALERT_ATTEMPTS = 10
        BACKOFF_CAP = 60  # seconds

        while not self.shutdown_event.is_set():
            ws = None
            try:
                # ---- DNS refresh (helps with LB changes) ----
                try:
                    host = ws_url.split("://", 1)[-1].split("/", 1)[0]
                    port = 443 if ws_url.startswith("wss://") else 80
                    await asyncio.get_running_loop().getaddrinfo(host, port)
                    self.logger.debug(f"🌐 {stream}: DNS OK for {host}")
                except socket.gaierror as e:
                    self.logger.warning(f"⚠️ {stream}: DNS resolution failed for {ws_url}: {e}")

                if not ws_url.startswith("wss://"):
                    self.logger.warning(f"⚠️ {stream} WS URL is not secure (wss): {ws_url}")

                async with websockets.connect(
                        ws_url,
                        ping_interval=20,
                        ping_timeout=20,
                        open_timeout=10,
                        close_timeout=5,
                        max_queue=2048,
                        max_size=2 ** 24,
                        compression=None,  # some feeds dislike permessage-deflate
                ) as ws:
                    # ---- mark ws + reset attempt counters ----
                    if is_user_ws:
                        self.websocket_helper.user_ws = ws
                        self.reconnect_attempts_user = 0
                    else:
                        self.websocket_helper.market_ws = ws
                        self.reconnect_attempts_market = 0

                    # ---- (re)subscribe cleanly ----
                    if hasattr(self.websocket_helper, "subscribed_channels"):
                        self.websocket_helper.subscribed_channels.clear()

                    if is_user_ws:
                        ok = await self.websocket_helper.subscribe_user()
                        if not ok:
                            self.logger.warning("⚠️ USER subscription failed; forcing reconnect.")
                            continue
                        self.logger.info(f"✅ Connected & subscribed to USER WebSocket: {ws_url}")
                    else:
                        ok = await self.websocket_helper.subscribe_market()
                        if not ok:
                            self.logger.warning("⚠️ MARKET subscription failed; forcing reconnect.")
                            continue
                        self.logger.info(f"✅ Connected & subscribed to MARKET WebSocket: {ws_url}")

                    # ---- quick handshake sanity: wait for first frame/ACK ----
                    try:
                        first = await asyncio.wait_for(ws.recv(), timeout=8)
                    except asyncio.TimeoutError:
                        self.logger.warning(f"⏱️ {stream}: no first frame within 8s; reconnecting…")
                        continue  # exit context and retry

                    last_message_time = time.time()
                    try:
                        if is_user_ws:
                            await self.websocket_helper._on_user_message_wrapper(first)
                        else:
                            await self.websocket_helper._on_market_message_wrapper(first)
                    except Exception as msg_err:
                        self.logger.error(f"❌ {stream}: error processing initial message: {msg_err}", exc_info=True)

                    self.logger.info(f"🎧 Listening on {stream} WebSocket…")

                    # ---- main receive loop with idle watchdog ----
                    while True:
                        try:
                            message = await asyncio.wait_for(ws.recv(), timeout=90)
                        except asyncio.TimeoutError:
                            self.logger.warning(f"⚠️ {stream}: no messages in 90s — forcing reconnect…")
                            break

                        last_message_time = time.time()
                        try:
                            if is_user_ws:
                                await self.websocket_helper._on_user_message_wrapper(message)
                            else:
                                await self.websocket_helper._on_market_message_wrapper(message)
                        except Exception as msg_err:
                            self.logger.error(f"❌ {stream}: error processing message: {msg_err}", exc_info=True)


            except asyncio.CancelledError:
                # Distinguish shutdown vs reconnect if you like:
                if self.shutdown_event.is_set():
                    self.logger.info(f"🛑 {stream} WS cancelled due to shutdown.")
                else:
                    self.logger.info(f"🔁 {stream} WS cancelled due to reconnect.")
                return
            except (ConnectionResetError, ConnectionClosed, ConnectionClosedError, ConnectionClosedOK, InvalidStatusCode) as e:
                self.logger.error(f"🔥 {stream} WebSocket connection error: {e}", exc_info=True)
            except asyncio.TimeoutError:
                self.logger.warning(f"⏱️ {stream}: no first frame within 8s; reconnecting…")
                continue  # exit context and retry
            except Exception as e:
                self.logger.error(f"🔥 Unexpected {stream} WebSocket error: {e}", exc_info=True)
            finally:
                # Clear stale pointers on exit from the 'with' block
                if is_user_ws and getattr(self.websocket_helper, "user_ws", None) is ws:
                    self.websocket_helper.user_ws = None
                if not is_user_ws and getattr(self.websocket_helper, "market_ws", None) is ws:
                    self.websocket_helper.market_ws = None

            # ---- backoff + alerting ----
            attempts = self.reconnect_attempts_user if is_user_ws else self.reconnect_attempts_market
            delay = min(2 ** attempts, BACKOFF_CAP) + random.uniform(0, 5)
            self.logger.warning(
                f"🔁 Reconnecting {stream} WebSocket in {delay:.1f}s "
                f"(Attempt {attempts + 1}, Total downtime ~{int(time.time() - last_message_time)}s)…"
            )

            if attempts + 1 >= MAX_ALERT_ATTEMPTS and getattr(getattr(self, "listener", None), "alert", None):
                try:
                    self.listener.alert.callhome(
                        f"{stream} WebSocket Down",
                        f"{stream} WebSocket failed to reconnect after {attempts + 1} attempts "
                        f"(~{int(time.time() - last_message_time)}s downtime).",
                        mode="email",
                    )
                    self.logger.error(f"🚨 {stream} WS alert sent (attempt {attempts + 1}).")
                except Exception:
                    self.logger.exception("🔥 Failed to send WS alert")

            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                self.logger.info(f"⚠️ {stream} WebSocket reconnect sleep cancelled.")
                return

            # bump attempt counter for this stream
            if is_user_ws:
                self.reconnect_attempts_user += 1
            else:
                self.reconnect_attempts_market += 1

            # optional: post-reconnect REST sync (run after a failed attempt; consider moving this to the success path if you prefer)
            if hasattr(self, "post_reconnect_sync") and asyncio.iscoroutinefunction(self.post_reconnect_sync):
                try:
                    await self.post_reconnect_sync()
                except Exception:
                    self.logger.exception(f"🔥 {stream}: post_reconnect_sync failed.")

    # ===============================================================
    # HEALTH CHECK & POST-RECONNECT SYNC
    # ===============================================================

    async def health_check_loop(self):
        """
        ✅ Phase 3: Smarter health check instead of blind periodic restart.
        Restarts streams only if they've been running too long or stale.
        """
        while not self.shutdown_event.is_set():
            await asyncio.sleep(600)  # check every 10 minutes
            # Example condition: restart if attempts > 3 or uptime > 4h (14400s)
            if self.reconnect_attempts_market > 3 or self.reconnect_attempts_user > 3:
                self.logger.warning("♻️ Restarting WebSockets due to high reconnect attempts...")
                await self.connect_market_stream()
                await self.connect_user_stream()

    async def post_reconnect_sync(self):
        """✅ Sync open orders and re-subscribe after reconnect."""
        try:
            self.logger.debug("🔄 Syncing open orders and subscriptions after reconnect...")
            await self.coinbase_api.fetch_open_orders()
            await self.websocket_helper.resubscribe_all_channels()
        except Exception as e:
            self.logger.warning(f"⚠️ Post-reconnect sync failed: {e}")

    async def force_reconnect(self):
        """
        ✅ Replacement for old reconnect().
        Immediately restarts both WebSocket streams and performs post-reconnect sync.
        """
        self.logger.info("🔁 Force reconnect requested (manual trigger)...")
        await self.connect_market_stream()
        await self.connect_user_stream()
        await self.post_reconnect_sync()

class WebhookListener:
    """The WebhookListener class is the central orchestrator of the bot,
    handling market data updates, order management, and webhooks."""

    _exchange_instance_count = 0

    def __init__(self, bot_config, shared_data_manager, shutdown_event: asyncio.Event, shared_utils_color, market_data_updater,
                 database_session_manager, logger_manager,coinbase_api, session, market_manager, exchange, alert, order_book_manager,
                 passive_order_manager=None, original_fees=None):

        self.bot_config = bot_config
        self.test_mode = self.bot_config.test_mode

        # Initialize structured logger early for initialization logging
        self._temp_logger = get_logger('webhook', context={'component': 'listener'})

        if not hasattr(self.bot_config, 'rest_client') or not self.bot_config.rest_client:
            self._temp_logger.info("REST client not initialized, initializing now")
            self.bot_config.initialize_rest_client()
        # Assign the REST client and portfolio UUID
        self.rest_client = self.bot_config.rest_client
        self.min_sell_value = float(self.bot_config.min_sell_value)
        self.portfolio_uuid = self.bot_config.portfolio_uuid
        self.session = session  # ✅ Store session passed from run_app
        # self.cb_api = self.bot_config.load_webhook_api_key() #moved to main.py
        self.exchange = exchange
        # self.order_management = {'order_tracker': {}}
        self.shared_data_manager = shared_data_manager
        self.shutdown_event = shutdown_event
        self.market_manager = market_manager
        self.market_data_updater = market_data_updater
        self.logger_manager = logger_manager  # 🙂
        if logger_manager:
            self.logger = logger_manager.loggers['webhook_logger']  # ✅ this is the actual logger you'll use

        # Structured logger for webhook operations
        self.structured_logger = get_logger('webhook', context={'component': 'listener'})

        self.webhook_manager = self.ticker_manager = self.utility = None  # Initialize webhook manager properly
        self.ohlcv_manager = None
        self.order_manager = None
        self.processed_uuids = set()
        self.original_fees = original_fees or {}

        # Core Utilities
        self.shared_utils_exchange = self.exchange
        self.shared_utils_precision = PrecisionUtils.get_instance(self.logger_manager, shared_data_manager)

        self.shared_utils_date_time = DatesAndTimes.get_instance(self.logger_manager)
        self.shared_utils_utility = SharedUtility.get_instance(self.logger_manager)
        self.shared_utils_print = PrintData.get_instance(self.logger_manager, self.shared_utils_utility)
        self.shared_utils_color = ColorCodes.get_instance()

        self.test_debug_maint = Debugging()


        self.alerts = AlertSystem(self.logger_manager)
        self.ccxt_api = ApiManager.get_instance(self.exchange, self.logger_manager, self.alerts)
        self.coinbase_api = coinbase_api
        #database related
        self.database_session_manager = database_session_manager

        self.lock = asyncio.Lock()
        # created without WebSocketHelper initially

        # ✅ Step 1: Create WebSocketHelper With Placeholders
        self.websocket_helper = WebSocketHelper(
            listener=self,
            websocket_manager=None,  # Placeholder
            logger_manager=self.logger_manager,
            coinbase_api=self.coinbase_api,
            profit_data_manager=None,  # Placeholder
            order_type_manager=None,  # Placeholder
            shared_utils_date_time=self.shared_utils_date_time,
            shared_utils_print=self.shared_utils_print, # Placeholder
            shared_utils_color=self.shared_utils_color,
            shared_utils_precision=self.shared_utils_precision,
            shared_utils_utility=self.shared_utils_utility, # Placeholder
            test_debug_maint=self.test_debug_maint, # Placeholder
            order_book_manager=None,  # Placeholder
            snapshot_manager=None,  # Placeholder
            trade_order_manager=None,
            shared_data_manager=self.shared_data_manager,
            market_ws_manager=None,
            database_session_manager=database_session_manager

        )

        self.passive_order_manager = passive_order_manager

        self.websocket_manager = WebSocketManager(self.bot_config, self, self.ccxt_api, self.logger,
                                                  self.websocket_helper)

        self.websocket_helper.websocket_manager = self.websocket_manager


        self.snapshot_manager = SnapshotsManager.get_instance(self.shared_data_manager, self.shared_utils_precision,
                                                              self.logger_manager)

        # Instantiation of ....
        self.utility = TradeBotUtils.get_instance(self.logger, self.coinbase_api, self.exchange,
                                                  self.ccxt_api, self.alerts, self.shared_data_manager)

        self.asset_monitor = None

        self.ticker_manager = None

        self.profit_data_manager = ProfitDataManager.get_instance(self.shared_utils_utility,
                                                                  self.shared_utils_precision,
                                                                  self.shared_utils_print,
                                                                  self.shared_data_manager,
                                                                  self.logger_manager)

        self.order_book_manager = order_book_manager

        self.validate = ValidateOrders.get_instance(self.logger, self.order_book_manager,
                                                    self.shared_utils_precision, self.shared_utils_utility,
                                                    self.shared_data_manager)

        self.order_type_manager = OrderTypeManager.get_instance(
            coinbase_api=self.coinbase_api,
            exchange_client=self.exchange,
            shared_utils_precision=self.shared_utils_precision,
            shared_utils_utility=self.shared_utils_utility,
            shared_utils_color=self.shared_utils_color,
            shared_data_manager=self.shared_data_manager,
            validate=self.validate,
            logger_manager=self.logger,
            alerts=self.alerts,
            ccxt_api=self.ccxt_api,
            order_book_manager=self.order_book_manager,
            websocket_helper=None, #Placeholder for self.websocket_helper,
            session=self.session
        )

        self.trade_order_manager = TradeOrderManager.get_instance(
            coinbase_api=self.coinbase_api,
            exchange_client=self.exchange,
            shared_utils_precision=self.shared_utils_precision,
            shared_utils_utility=self.shared_utils_utility,
            validate=self.validate,
            logger_manager=self.logger,
            alerts=self.alerts,
            ccxt_api=self.ccxt_api,
            market_data_updater=self.market_data_updater,
            order_book_manager=self.order_book_manager,
            order_types=self.order_type_manager,
            websocket_helper=self.websocket_helper,
            shared_data_manager=self.shared_data_manager,
            session=self.coinbase_api.session,
            profit_manager=self.profit_data_manager
        )

        #Assign WebSocketHelper to Other Managers
        self.trade_order_manager.websocket_helper = self.websocket_helper
        self.order_type_manager.websocket_helper = self.websocket_helper

        self.webhook_manager = WebHookManager.get_instance(
            logger_manager=self.logger,
            shared_utils_precision=self.shared_utils_precision,
            trade_order_manager=self.trade_order_manager,
            alerts=self.alerts,
            session=self.session
        )


        self.market_ws_manager = WebSocketMarketManager(
            self,  self.exchange, self.ccxt_api, self.logger, self.coinbase_api,
            self.profit_data_manager, self.order_type_manager, self.shared_utils_print,
            self.shared_utils_color, self.shared_utils_precision, self.shared_utils_utility,
            self.test_debug_maint, self.order_book_manager,
            self.snapshot_manager, self.trade_order_manager, self.ohlcv_manager,
            self.shared_data_manager, self.database_session_manager
        )

        self.websocket_helper = WebSocketHelper(
            self, self.websocket_manager, self.logger,
            self.coinbase_api, self.profit_data_manager, self.order_type_manager,
             self.shared_utils_date_time, self.shared_utils_print,
            self.shared_utils_color, self.shared_utils_precision, self.shared_utils_utility,
            self.test_debug_maint,  self.order_book_manager,
            self.snapshot_manager, self.trade_order_manager, self.shared_data_manager,
            self.market_ws_manager, None, None

        )

    async def async_init(self):
        """Initialize async components after __init__."""
        self.ohlcv_manager = await OHLCVManager.get_instance(self.exchange, self.coinbase_api, self.ccxt_api,
                                                             self.logger_manager,self.shared_utils_date_time,
                                                             self.market_manager,
                                                             self.database_session_manager)
        self.ticker_manager = await TickerManager.get_instance(self.bot_config, self.coinbase_api,
                                                               self.test_debug_maint,self.shared_utils_print,
                                                               self.shared_utils_color,self.logger_manager,
                                                               self.order_book_manager,self.rest_client,
                                                               self.portfolio_uuid, self.exchange,self.ccxt_api,
                                                               self.shared_data_manager,
                                                               self.shared_utils_precision
        )

    @property
    def market_data(self):
        return self.shared_data_manager.market_data

    @property
    def order_management(self):
        return self.shared_data_manager.order_management

    @property
    def fee_info(self):
        return self.shared_data_manager.market_data.get('fee_info', {})

    @property
    def ticker_cache(self):
        return self.shared_data_manager.market_data.get('ticker_cache', {})

    @property
    def bid_ask_spread(self):
        return self.shared_data_manager.market_data.get('bid_ask_spread', {})

    @property
    def filtered_balances(self):
        return self.shared_data_manager.order_management.get('non_zero_balances', {})

    async def refresh_market_data(self):
        """Refresh market_data and manage orders once (caller schedules periodically)."""
        try:
            # Fetch new market data
            t0 = time.monotonic()
            result = await self.market_data_updater.update_market_data(time.time())
            if result is None:
                self.logger.error("❌ update_market_data returned None; skipping this cycle.")
                return False
            new_market_data, new_order_management = result

            # Guard against None / wrong types before indexing
            if not isinstance(new_market_data, dict):
                self.logger.error("❌ new_market_data is not a dict; got %s", type(new_market_data).__name__)
                return False
            if not isinstance(new_order_management, dict):
                self.logger.error("❌ new_order_management is not a dict; got %s", type(new_order_management).__name__)
                new_order_management = {}

            # Merge passive orders
            try:
                new_order_management["passive_orders"] = await self.shared_data_manager.fetch_passive_orders()
            except Exception:
                self.logger.error("❌ Failed to fetch passive_orders", exc_info=True)

            self.logger.debug("⏱ update_market_data took %.2fs", time.monotonic() - t0)

            # Minimal validation
            if not new_market_data:
                self.logger.error("❌ new_market_data is empty; skipping save/monitor.")
                return False

            # Enrich + publish to shared state
            new_market_data["last_updated"] = datetime.now(timezone.utc)
            try:
                new_market_data["fee_info"] = await self.coinbase_api.get_fee_rates()
            except Exception:
                self.logger.warning("⚠️ fee_rates fetch failed; continuing", exc_info=True)

            t1 = time.monotonic()
            await self.shared_data_manager.update_shared_data(new_market_data, new_order_management)
            self.logger.debug("⏱ update_shared_data took %.2fs", time.monotonic() - t1)

            # Monitor/update orders
            t2 = time.monotonic()
            await self.asset_monitor.monitor_all_orders()
            self.logger.debug("⏱ monitor_all_orders took %.2fs", time.monotonic() - t2)

            self.logger.debug("✅ refresh_market_data completed")
            return True

        except asyncio.CancelledError:
            self.logger.info("⚠️ refresh_market_data was cancelled by the event loop")
            raise

        except Exception:
            self.logger.error("❌ refresh_market_data crashed", exc_info=True)
            return False

    async def handle_order_fill(self, websocket_order_data: OrderData):
        """Process existing orders that are Open or Active or have been filled"""

        try:
            symbol = websocket_order_data.trading_pair
            asset = symbol.split('/')[0]

            # Fetch precision
            base_deci, quote_deci, _, _ = self.shared_utils_precision.fetch_precision(symbol)
            websocket_order_data.base_decimal = base_deci
            websocket_order_data.quote_decimal = quote_deci

            # Adjust fields using correct precision
            if websocket_order_data.price:
                websocket_order_data.price = self.shared_utils_precision.adjust_precision(
                    base_deci, quote_deci, websocket_order_data.price, 'quote'
                )
            if websocket_order_data.order_amount_fiat:
                websocket_order_data.order_amount_fiat = self.shared_utils_precision.adjust_precision(
                    base_deci, quote_deci, websocket_order_data.order_amount_fiat, 'base'
                )
            if websocket_order_data.limit_price:
                websocket_order_data.limit_price = self.shared_utils_precision.adjust_precision(
                    base_deci, quote_deci, websocket_order_data.limit_price, 'quote'
                )
            if websocket_order_data.average_price:
                websocket_order_data.average_price = self.shared_utils_precision.adjust_precision(
                    base_deci, quote_deci, websocket_order_data.average_price, 'quote'
                )
            if websocket_order_data.stop_loss_price:
                websocket_order_data.stop_loss_price = self.shared_utils_precision.adjust_precision(
                    base_deci, quote_deci, websocket_order_data.stop_loss_price, 'quote'
                )

            # Fetch cached data if needed
            self.usd_pairs = self.market_data.get('usd_pairs_cache', {})
            self.spot_info = self.market_data.get('spot_positions', {})

            self.structured_logger.debug(
                "handle_order_fill - OrderData",
                extra={'order_data': websocket_order_data.debug_summary(verbose=True)}
            )

            # Hand off to processor
            await self._process_order_fill('WebSocket', websocket_order_data)

        except Exception as e:
            self.structured_logger.error(
                "Error in handle_order_fill",
                extra={'websocket_msg': str(websocket_order_data)},
                exc_info=True
            )
            self.logger.error(f"Error in handle_order_fill: {e} {websocket_order_data}", exc_info=True)

    async def _process_order_fill(self, source, order_data: OrderData):
        """
        Process an order fill and place a corresponding protective order.

        For BUY fills: The asset_monitor will detect the new position and place protective OCO orders.
        For SELL fills: Remove from tracking as position is closed.

        Args:
            order_data: Details of the filled order, including symbol, price, and size.
        """
        self.structured_logger.info(
            "Processing order fill",
            extra={'side': order_data.side, 'trading_pair': order_data.trading_pair, 'source': source}
        )
        try:
            if order_data.open_orders is not None:
                if order_data.open_orders.get('open_order'):
                    return

            order_data.source = source

            # Handle BUY fills - let asset_monitor place protective OCO
            if order_data.side.lower() == 'buy':
                self.structured_logger.info(
                    "BUY order filled - asset_monitor will place protective OCO",
                    extra={'trading_pair': order_data.trading_pair, 'order_id': order_data.order_id}
                )
                # Remove the buy order from order_tracker (it's now a position)
                if order_data.order_id in self.order_management.get('order_tracker', {}):
                    del self.order_management['order_tracker'][order_data.order_id]
                    self.structured_logger.info(
                        "Removed filled buy order from order_tracker",
                        extra={'order_id': order_data.order_id}
                    )
                # The asset_monitor's sweep_positions_for_exits will detect this new holding
                # and place protective OCO orders automatically
                return

            # Handle SELL fills - position closed, clean up tracking
            if order_data.side.lower() == 'sell':
                symbol = order_data.trading_pair
                self.structured_logger.info(
                    "SELL order filled - position closed",
                    extra={'trading_pair': symbol, 'order_id': order_data.order_id}
                )
                # Remove from order_tracker
                if order_data.order_id in self.order_management.get('order_tracker', {}):
                    del self.order_management['order_tracker'][order_data.order_id]
                    self.structured_logger.info(
                        "Removed filled sell order from order_tracker",
                        extra={'order_id': order_data.order_id}
                    )
                # Remove from positions if tracked there
                if symbol in self.order_management.get('positions', {}):
                    del self.order_management['positions'][symbol]
                    self.structured_logger.info(
                        "Removed closed position from positions",
                        extra={'symbol': symbol}
                    )

                # ✅ Task 5: Clean up bracket tracking when SELL fills
                bracket_orders = self.order_management.get('bracket_orders', {})
                if symbol in bracket_orders:
                    bracket = bracket_orders[symbol]

                    # Determine which part of bracket filled and log exit source
                    exit_source = 'UNKNOWN'
                    if bracket.get('stop_order_id') == order_data.order_id:
                        exit_source = 'EXCHANGE_BRACKET_STOP'
                    elif bracket.get('tp_order_id') == order_data.order_id:
                        exit_source = 'EXCHANGE_BRACKET_TP'
                    elif bracket.get('entry_order_id') == order_data.order_id:
                        # Entry order filled as SELL? Shouldn't happen, but handle it
                        exit_source = 'EXCHANGE_BRACKET_ENTRY'
                    else:
                        # Manual sell or position monitor sell
                        exit_source = 'MANUAL_OR_MONITOR'

                    # Log exit source for performance tracking
                    self.logger.info(
                        f"[EXIT_SOURCE] {symbol} | Reason: BRACKET_FILL | "
                        f"Source: {exit_source} | Order Type: LIMIT | "
                        f"Order ID: {order_data.order_id}"
                    )

                    # Clean up bracket tracking
                    del bracket_orders[symbol]
                    self.logger.debug(f"[BRACKET] Removed bracket tracking for {symbol} (exit via {exit_source})")
                else:
                    self.logger.debug(f"[BRACKET] No bracket found for {symbol} SELL fill (likely position monitor exit)")

                return

        except Exception as e:
            self.logger.error(f"Error in _process_order_fill: {e}", exc_info=True)

    async def handle_webhook(self, request: web.Request) -> web.Response:
        """Processes incoming webhook requests and delegates to WebHookManager."""
        try:
            ip_address = request.remote

            # print(f"� Request Headers: {dict(request.headers)}")  # Debug
            request_json = await request.json()
            self.structured_logger.info(
                "Receiving webhook",
                extra={'webhook_data': request_json, 'ip': ip_address}
            )

            symbol = request_json.get("pair")
            side = request_json.get("side")
            order_amount = request_json.get("order_amount_fiat")
            origin = request_json.get("origin")
            source = request_json.get("source")

            if origin == "TradingView":
                self.structured_logger.info(
                    "Handling webhook request from TradingView",
                    extra={'origin': origin, 'symbol': symbol, 'uuid': request_json.get('uuid')}
                )

            # Ensure UUID is present
            request_json["uuid"] = request_json.get("uuid", str(uuid.uuid4()))

            # � This already returns a fully prepared `web.Response`
            response = await self.process_webhook(request_json, ip_address)

            # ✅ Log and return
            try:
                body = json.loads(response.text)
                message = body.get("message")

                if body.get("success"):
                    self.logger.order_sent(
                        f"Webhook response: {message} {symbol} side:{side} size:{order_amount}. Order originated from {origin}"
                    )
                #print(json.dumps(body, indent=2))  # Optional debugging output

            except Exception as decode_error:
                self.logger.error(f"⚠️ Could not decode JSON response: {decode_error}", exc_info=True)

            return response

        except json.JSONDecodeError:
            self.logger.error("⚠️ JSON Decode Error: Invalid JSON received")
            return web.json_response(
                {"success": False, "message": f"Invalid JSON format"},
                status=int(ValidationCode.INVALID_JSON_FORMAT.value)
            )

        except Exception as e:
            self.logger.error(f"⚠️ Unhandled exception in handle_webhook: {str(e)}", exc_info=True)
            return web.json_response(
                {"success": False, "message": f"Internal error {e}"},
                status=int(ValidationCode.INTERNAL_SERVER_ERROR.value)
            )

    async def add_uuid_to_cache(self, check_uuid: str) -> None:
        """
        Add a UUID to the processed set and schedule its removal after 5 minutes.

        Args:
            check_uuid (str): The UUID to track temporarily to avoid duplicate processing.
        """
        async with self.lock:
            if check_uuid not in self.processed_uuids:
                self.processed_uuids.add(check_uuid)
                self.logger.debug(f"✅ UUID added to cache: {check_uuid}")

        def remove_uuid_later(uuid_to_remove: str):
            try:
                self.processed_uuids.remove(uuid_to_remove)
                self.logger.debug(f"� UUID automatically removed from cache: {uuid_to_remove}")
            except KeyError:
                self.logger.warning(f"⚠️ UUID not found in cache during removal: {uuid_to_remove}")

        # ⏱️ Schedule removal after 5 minutes (300 seconds)
        asyncio.get_event_loop().call_later(300, remove_uuid_later, check_uuid)

    # helper methods used in process_webhook()
    def is_ip_whitelisted(self, ip: str) -> bool:
        wl = self.bot_config.get_whitelist()
        if not wl:
            return False
        ip_obj = ip_address(ip)
        for entry in wl:
            e = (entry or "").strip()
            if not e:
                continue
            try:
                if "/" in e:
                    if ip_obj in ip_network(e, strict=False):
                        return True
                else:
                    if ip == e:
                        return True
            except ValueError:
                # ignore malformed entries
                pass
        return False

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
            # ✅ Validate UUID and deduplicate
            webhook_uuid = request_json.get("uuid")
            if not webhook_uuid:
                return web.json_response(
                    {"success": False, "message": "Missing 'uuid' in request"},
                    status=int(ValidationCode.MISSING_UUID.value)
                )

            if webhook_uuid in self.processed_uuids:
                self.logger.debug(f"Duplicate webhook detected: {webhook_uuid}")
                return web.json_response(
                    {"success": False, "message": "Duplicate 'uuid' detected"},
                    status=int(ValidationCode.DUPLICATE_UUID.value)
                )

            await self.add_uuid_to_cache(webhook_uuid)

            # ✅ Basic request validation
            if not request_json.get("action"):
                return web.json_response(
                    {"success": False, "message": "Missing action"},
                    status=int(ValidationCode.MISSING_ACTION.value)
                )

            if not self.is_ip_whitelisted(ip_address):
                return web.json_response(
                    {"success": False, "message": "Unauthorized"},
                    status=int(ValidationCode.UNAUTHORIZED.value)
                )

            if not WebhookListener.is_valid_origin(request_json.get("origin", "")):
                return web.json_response(
                    {"success": False, "message": "FORBIDDEN"},
                    status=int(ValidationCode.FORBIDDEN.value)
                )

            # ✅ Parse normalized trade data (now includes `test_mode`)
            trade_data = self.webhook_manager.parse_webhook_request(request_json)
            if trade_data is None:
                return web.json_response(
                    {"success": False, "message": "Failed to parse webhook request"},
                    status=int(ValidationCode.INVALID_JSON_FORMAT.value)
                )

            product_id = trade_data["trading_pair"]
            asset = trade_data["base_currency"]

            # ✅ Fetch market snapshots
            combined_snapshot = await self.snapshot_manager.get_market_data_snapshot()
            market_data_snapshot = combined_snapshot["market_data"]
            order_management_snapshot = combined_snapshot["order_management"]

            # ✅ Precision validation
            precision_data = self.shared_utils_precision.fetch_precision(trade_data["trading_pair"])
            if not self.is_valid_precision(precision_data):
                return web.json_response(
                    {"success": False, "message": "Failed to fetch precision data"},
                    status=int(ValidationCode.PRECISION_ERROR.value)
                )

            base_price_in_fiat, quote_price_in_fiat = await self.get_prices(trade_data, market_data_snapshot)
            asset_obj = order_management_snapshot.get("non_zero_balances", {}).get(asset)

            # ✅ Update fee cache if changed
            pom = self.passive_order_manager
            if pom is None:
                self.logger.warning("PassiveOrderManager not initialized yet")
                return web.json_response(
                    {"success": False, "message": "Service warming up"},
                    status=503
                )

            # Ensure fees are loaded once (if your POM supports it)
            if not getattr(pom, "fee", None) and hasattr(pom, "ensure_fees_loaded"):
                await pom.ensure_fees_loaded()

            try:
                new_rates = await self.coinbase_api.get_fee_rates()
                old_maker = (pom.fee or {}).get("maker")
                new_maker = Decimal(str(new_rates.get("maker"))) if "maker" in new_rates else None
                if new_maker is not None and (old_maker is None or new_maker != old_maker):
                    await pom.set_fee_cache(new_rates)
                    self.fee_rates = pom.fee
            except Exception as e:
                self.logger.debug(f"Fee refresh skipped: {e}")

            # ✅ Order size validation
            _, _, base_value = self.calculate_order_size_fiat(
                trade_data, base_price_in_fiat, quote_price_in_fiat, precision_data, self.fee_info
            )


            if self.test_mode:
                self.logger.warning(f"⚠️ [TEST MODE ENABLED] Building test order for {product_id}")

            # ✅ Normal balance validation unless in test mode
            if not self.test_mode and trade_data["side"] == "sell" and base_value < float(self.min_sell_value):
                return web.json_response(
                    {
                        "success": False,
                        "message": f"Insufficient balance to sell {asset} (requires {self.min_sell_value} USD)"
                    },
                    status=int(ValidationCode.INSUFFICIENT_BASE.value)
                )

            # ✅ Build order (pass test_mode directly)
            source = trade_data.get("source", "Webhook")
            trigger = trade_data.get("trigger", "strategy")
            trigger = {"trigger": f"{trigger}", "trigger_note": "from webhook"}

            order_details = await self.trade_order_manager.build_order_data(
                source, trigger, asset, product_id, stop_price=None, test_mode=self.test_mode
            )
            if order_details is None:
                msg = self.trade_order_manager.build_failure_reason or "Order build failed"
                return web.json_response(
                    {"success": False, "message": msg},
                    status=int(ValidationCode.ORDER_BUILD_FAILED.value)
                )

            order_details.trigger = trigger

            # ✅ Debug summary
            self.structured_logger.debug(
                "process_webhook - Order Data",
                extra={'order_data': order_details.debug_summary(verbose=True)}
            )

            # ✅ Delegate to action handler
            response = await self.webhook_manager.handle_action(order_details, precision_data)
            code = response.get("code", 200)

            return self.shared_utils_utility.safe_json_response(response, status=code)

        except Exception as e:
            self.logger.error(f"Error processing webhook: {e}", exc_info=True)
            return web.json_response(
                {"success": False, "message": f"Internal error {e}"},
                status=int(ValidationCode.INTERNAL_SERVER_ERROR.value)
            )

    async def get_prices(self, trade_data: dict, market_data_snapshot: dict) -> tuple:
        try:
            trading_pair = trade_data['trading_pair']
            asset = trade_data['base_currency']
            usd_pairs = market_data_snapshot.get('usd_pairs_cache', {})
            base_deci, quote_deci, _, _ = self.shared_utils_precision.fetch_precision(asset)

            bid_ask_spread = market_data_snapshot.get('bid_ask_spread', {})
            spread_data = bid_ask_spread.get(trading_pair, {})

            # ✅ Select the appropriate price
            if isinstance(spread_data, dict):
                base_price_raw = spread_data.get("ask") or spread_data.get("bid") or 0
            else:
                base_price_raw = spread_data  # fallback if it's already a float

            base_price_in_fiat = self.shared_utils_precision.float_to_decimal(base_price_raw, quote_deci)
            quote_price_in_fiat = Decimal(1.00)
            return base_price_in_fiat, quote_price_in_fiat

        except Exception as e:
            self.logger.error(f"Error fetching prices: {e}", exc_info=True)
            return Decimal(0), Decimal(0)

    def calculate_order_size_fiat(self, trade_data: dict, base_price: Decimal, quote_price: Decimal, precision_data: tuple, fee_info: dict):
        """
        Wrapper function to call webhook_manager's calculate_order_size_fiat with correct arguments.
        """
        base_deci, quote_deci, _, _ = precision_data  # Extract precision values
        return self.webhook_manager.calculate_order_size_fiat(
            trade_data.get("side"),
            trade_data.get("order_amount_fiat"),
            trade_data.get("quote_avail_balance"),  # This is USD balance for buying
            trade_data.get("base_avail_balance", 0),  # Base asset balance for selling
            quote_price,
            base_price,
            quote_deci,
            base_deci,
            fee_info
        )

    async def periodic_save(self, interval: int = 60):
        try:
            while True:
                try:
                    market_data_snapshot, order_management_snapshot = await self.shared_data_manager.get_snapshots()
                    await self.shared_data_manager.update_shared_data(
                        new_market_data=market_data_snapshot,
                        new_order_management=order_management_snapshot
                    )
                    await self.shared_data_manager.save_data()
                    self.logger.debug("✅ Periodic save completed.")
                except Exception as e:
                    self.logger.error(f"Error during periodic save: {e}", exc_info=True)

                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            self.logger.info("🛑 periodic_save cancelled cleanly.")

    def _infer_origin_from_client_order_id(self, coid: str | None) -> str | None:
        s = (coid or "").lower()
        if s.startswith("passivemm-"): return "passivemm"
        if s.startswith("webhook-"):   return "webhook"
        if s.startswith("websocket-"): return "websocket"
        return None

    async def reconcile_with_rest_api(self, limit: int = 500):
        logger = self.logger
        coinbase_api = self.coinbase_api
        shared_data_manager = self.shared_data_manager

        def _gross_and_fees_from_batch(order: dict) -> tuple[str, str]:
            gross = str(order.get("filled_value") or "")
            fees = str(order.get("total_fees") or "")
            return gross, fees

        def _norm_iso_utc(ts: str) -> str:
            # Accepts ISO-ish inputs, returns 'YYYY-MM-DDTHH:MM:SS.ffffff+00:00'
            from datetime import datetime, timezone
            if not ts:
                return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")
            try:
                ts = str(ts)
                # handle 'Z'
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                dt = dt.astimezone(timezone.utc)
            except Exception:
                dt = datetime.now(timezone.utc)
            return dt.strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")

        try:
            logger.info("🔁 Starting reconciliation via REST API...")

            # ✅ Refresh open orders into tracker for context
            open_orders = await coinbase_api.fetch_open_orders(limit=limit)
            logger.debug(f"📥 Retrieved {len(open_orders)} open orders")

            tracker = shared_data_manager.order_management.setdefault("order_tracker", {})
            for raw_order in open_orders:
                normalized = shared_data_manager.normalize_raw_order(raw_order)
                if not normalized:
                    continue
                oid = normalized.get("order_id")
                if oid and oid not in tracker:
                    tracker[oid] = normalized
                    logger.debug(f"📌 Added missing open order: {oid}")

            # ✅ Fetch recent FILLED orders
            params = {"limit": limit, "order_status": ["FILLED"]}
            filled_response = await coinbase_api.get_historical_orders_batch(params=params)
            orders = filled_response.get("orders", []) or []
            logger.debug(f"📘 Retrieved {len(orders)} filled orders")

            reconciled_trades = []

            for order in orders:
                if (order.get("status") or "").upper() != "FILLED":
                    continue

                order_id = order.get("order_id")
                if not order_id:
                    continue

                existing_trade = await shared_data_manager.trade_recorder.fetch_trade_by_order_id(order_id)
                # ✅ Only skip fully-complete trades
                if existing_trade and existing_trade.parent_id and existing_trade.parent_ids:
                    continue
                elif existing_trade:
                    logger.warning(f"⚠️ Trade {order_id} found in DB but incomplete — will reprocess.")

                side = (order.get("side") or "").lower()
                symbol = order.get("product_id")
                filled_size = order.get("filled_size") or order.get("order_size") or None
                avg_price = order.get("average_filled_price") or order.get("price") or None

                # Time normalization (UTC full ISO)
                order_time = (
                        order.get("last_fill_time")
                        or order.get("completed_time")
                        or order.get("created_time")
                        or ""
                )
                order_time = _norm_iso_utc(order_time)

                # Overrides from batch (strings expected by recorder)
                gross_override, fees_override = _gross_and_fees_from_batch(order)

                # Parent hints
                preferred_parent_id = order.get("originating_order_id") or None
                parent_id = None
                parent_ids = None
                if side == "sell":
                    # Prefer true origination if present, else FIFO hint
                    parent_id = preferred_parent_id or await shared_data_manager.trade_recorder.find_latest_unlinked_buy_id(symbol)
                    parent_ids = [parent_id] if parent_id else None
                else:
                    # For buys, let the recorder assign chain id; don't self-parent
                    parent_id = None
                    parent_ids = [order_id]

                # SELL safety checks — without these, PnL can explode due to zero/NaN basis
                if side == "sell":
                    if not filled_size or not gross_override:
                        logger.warning(f"⚠️ Skipping SELL {order_id}: missing filled_size/filled_value for cost-basis/PnL.")
                        continue
                # Preserve origin or infer for brand-new rows
                if existing_trade and existing_trade.source:
                    _source = existing_trade.source
                else:
                    _source = self._infer_origin_from_client_order_id(order.get("client_order_id")) or "unknown"

                trade_data = {
                    "order_id": order_id,
                    "parent_id": parent_id,
                    "parent_ids": parent_ids,
                    "preferred_parent_id": preferred_parent_id,
                    "symbol": symbol,
                    "side": side,
                    "price": str(avg_price) if avg_price is not None else "",
                    "amount": str(filled_size) if filled_size is not None else "",
                    "status": "filled",
                    "order_time": order_time,
                    "trigger": {"trigger": order.get("order_type", "market")},
                    "source": _source,
                    "total_fees": order.get("total_fees"),
                    "gross_override": gross_override,
                    "fees_override": fees_override,
                    # ingestion provenance
                    "ingest_via": "rest",
                    "last_reconciled_at": datetime.now(timezone.utc).isoformat(),
                    "last_reconciled_via": "rest_api",
                }

                reconciled_trades.append(trade_data)

            # 🔧 Sort once (by normalized UTC time) and enqueue once
            def _parse_utc(s: str):
                if not s:
                    raise ValueError("empty timestamp")
                s = s.strip().replace(" ", "T")
                # normalize common variants
                if s.endswith("+00"):
                    s = s + ":00"  # -> +00:00
                if s.endswith("Z"):
                    s = s[:-1] + "+00:00"  # -> +00:00
                # if there is a timezone without colon like +0000
                if len(s) >= 5 and (s[-5] in ["+", "-"]) and s[-3] != ":":
                    # e.g., 2025-09-21T23:30:22.516246+0000 -> +00:00
                    s = s[:-5] + s[-5:-2] + ":" + s[-2:]
                return datetime.fromisoformat(s)

            reconciled_trades.sort(key=lambda t: _parse_utc(t["order_time"]))

            for trade in reconciled_trades:
                await shared_data_manager.trade_recorder.enqueue_trade(trade)
                logger.debug(f"🧾 Reconciled and recorded trade: {trade['order_id']}")

            # Persist tracker
            await shared_data_manager.set_order_management({"order_tracker": tracker})
            await shared_data_manager.save_data()

            logger.debug("✅ Reconciliation complete.")
        except Exception as e:
            logger.error(f"❌ reconcile_with_rest_api() failed: {e}", exc_info=True)

    # helper method used in sync_open_orders()
    async def _exec_with_deadlock_retry(self, sess, stmt, max_retries=3):
        delay = 0.1
        for attempt in range(1, max_retries + 1):
            try:
                return await sess.execute(stmt)
            except DBAPIError as e:
                # asyncpg raises asyncpg.exceptions.DeadlockDetectedError under the hood
                if "deadlock detected" in str(e).lower() and attempt < max_retries:
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
                raise

    async def sync_open_orders(self, interval: int = 60 * 60) -> None:
        """
        Periodically fetch recent + open orders and upsert raw-exchange facts into trade_records
        WITHOUT touching derived/linkage fields (pnl_usd, remaining_size, realized_profit, parent_id(s)).

        Policy:
          - Insert new rows if missing.
          - Update only raw fields conservatively:
              * price/size only if > 0
              * order_time only if DB is NULL or incoming is EARLIER
              * source only if not 'unknown'
              * total_fees_usd only if incoming is non-null AND >= existing
          - Never update: pnl_usd, remaining_size, realized_profit, parent_id, parent_ids
          - Optional WHERE to avoid touching rows that are already finalized (pnl_usd IS NOT NULL).
        """
        LOOKBACK_FIRST_RUN_HOURS = SYNC_LOOKBACK  # keep your existing constant
        LOOKBACK_SUBSEQUENT_HOURS = 1
        GLOBAL_LOCK_KEY = 0x54524144 # same as what is used in trade_record_maintenance.py

        def _pick(d: dict, *paths, default=None):
            """Walks nested dicts by keys/indices; returns first value found."""
            for p in paths:
                try:
                    if isinstance(p, tuple):
                        cur = d
                        ok = True
                        for step in p:
                            if isinstance(step, int):
                                cur = cur[step]
                            else:
                                cur = cur.get(step)
                            if cur is None:
                                ok = False
                                break
                        if ok and cur is not None:
                            return cur
                    else:
                        v = d.get(p)
                        if v is not None:
                            return v
                except Exception:
                    continue
            return default

        def _iso_to_dt(s: str | None):
            if not s:
                return None
            try:
                # Coinbase usually gives Z or offset; let fromisoformat handle offsets
                # Replace 'Z' to be safe for Python versions that don't accept it
                s = s.replace("Z", "+00:00") if s.endswith("Z") else s
                return dt.datetime.fromisoformat(s)
            except Exception:
                return None

        def _infer_source(client_order_id: str, order_source: str) -> str:
            coid = (client_order_id or "").lower()
            if "passivemm" in coid:
                return "passivemm"
            if "webhook" in coid:
                return "webhook"
            if "websocket" in coid:
                return "websocket"
            return "unknown"

        def _derive_trigger(trigger_status: str, order_type: str) -> str | None:
            ts = (trigger_status or "").lower()
            ot = (order_type or "").lower()
            if not ts and not ot:
                return None
            return ts if ts and ts != "invalid_order_type" else (ot or None)

        first_run = True
        table_empty_known: bool | None = None

        try:
            while True:
                try:
                    self.logger.debug("🔄 Starting sync_open_orders cycle…")

                    # Only check emptiness once on first run
                    if first_run:
                        async with self.database_session_manager.async_session() as sess:
                            result = await sess.execute(text("SELECT COUNT(*) FROM trade_records"))
                            count = result.scalar_one()
                            table_empty_known = (count == 0)
                            self.logger.debug(
                                "📊 trade_records is %s (count=%s)",
                                "empty" if table_empty_known else "populated",
                                count,
                            )

                    lookback_hours = LOOKBACK_FIRST_RUN_HOURS if first_run else LOOKBACK_SUBSEQUENT_HOURS
                    since_iso = (
                            dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=lookback_hours)
                    ).isoformat(timespec="seconds").replace("+00:00", "Z")
                    first_run = False

                    # 1) Fetch orders
                    open_resp = await self.coinbase_api.list_historical_orders(
                        limit=250, order_status="OPEN"
                    )
                    open_orders = (open_resp or {}).get("orders", []) or []

                    recent_orders = []
                    if table_empty_known is False:
                        recent_resp = await self.coinbase_api.list_historical_orders(
                            limit=250, start_time=since_iso
                        )
                        recent_orders = (recent_resp or {}).get("orders", []) or []

                    self.logger.debug(
                        "Fetched %d OPEN and %d RECENT (since %s)",
                        len(open_orders), len(recent_orders), since_iso
                    )

                    # 2) Deduplicate by order_id
                    seen, orders_to_process = set(), []
                    for o in open_orders + recent_orders:
                        oid = o.get("order_id")
                        if oid and oid not in seen:
                            orders_to_process.append(o)
                            seen.add(oid)

                    # 3) Transform into rows (raw facts only)
                    rows: list[dict] = []

                    CHUNK = 50  # smaller batches reduce lock hold time
                    for o in orders_to_process:
                        try:
                            if (o.get("status") or "").lower() != "filled":
                                continue

                            symbol = o.get("product_id") or ""
                            side = (o.get("side") or "").lower() or None
                            status = (o.get("status") or "").lower() or None
                            order_id = o.get("order_id")
                            if not order_id:
                                continue

                            order_type = (o.get("order_type") or "").lower() or None
                            trigger = _derive_trigger(o.get("trigger_status"), order_type)
                            source = _infer_source(o.get("client_order_id", ""), o.get("source", ""))

                            # Prefer earliest credible time
                            order_time = _iso_to_dt(_pick(
                                o,
                                "created_time",
                                ("order_configuration", "created_time"),
                                ("edit_history", 0, "created_time"),
                                "last_fill_time",
                            ))

                            # Price/size with positive guards
                            price = Decimal(str(_pick(
                                o,
                                "average_filled_price",
                                ("order_configuration", "limit_limit_gtc", "limit_price"),
                                "price",
                            ) or "0"))
                            size = Decimal(str(_pick(
                                o,
                                "filled_size",
                                "size",
                                ("order_configuration", "limit_limit_gtc", "base_size"),
                                "order_size",
                            ) or "0"))
                            fee = Decimal(str(o.get("total_fees") or "0"))

                            row = {
                                "order_id": order_id,
                                "symbol": symbol or None,
                                "side": side,
                                "order_type": order_type,
                                "order_time": order_time,
                                "price": float(price) if price > 0 else None,
                                "size": float(size) if size > 0 else None,
                                "total_fees_usd": float(fee) if fee is not None else None,
                                "trigger": trigger,
                                "status": status,
                                "source": source,  # INSERT: best-guess origin
                                "ingest_via": "rest",  # ← how we saw it this time
                            }
                            # DO NOT include: parent_id, parent_ids, pnl_usd, remaining_size, realized_profit
                            rows.append(row)

                        except Exception as e:
                            self.logger.warning(
                                "⚠️ Skipping order_id=%s due to transform error: %s",
                                o.get("order_id"), e, exc_info=True
                            )
                    rows.sort(key=lambda r: (r["order_id"] or ""))
                    # 4) Upsert in chunks — non-destructive policy
                    if not rows:
                        self.logger.debug("ℹ️ sync_open_orders → nothing to upsert.")
                        await asyncio.sleep(interval)
                        continue

                    updated_total = 0
                    async with self.database_session_manager.async_session() as sess:
                        # 🧪 Try to grab the global lock; if not available, maintenance is running
                        got = (await sess.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": GLOBAL_LOCK_KEY})).scalar()
                        if not got:
                            self.logger.info("⏳ sync_open_orders skipped (maintenance running).")
                            await asyncio.sleep(interval)
                            continue
                        try:
                            for i in range(0, len(rows), CHUNK):
                                batch = rows[i:i + CHUNK]
                                # 🔐 Take xact-scoped advisory locks for each order_id in this batch.
                                # Use SQL's hashtext(order_id) to get a stable key across processes.
                                for r in batch:
                                    oid = r["order_id"]
                                    if not oid:
                                        continue
                                    await sess.execute(
                                        text("SELECT pg_advisory_xact_lock(hashtext(:oid))"),
                                        {"oid": oid}
                                    )
                                insert_stmt = pg_insert(TradeRecord).values(batch)

                                update_cols = {
                                    # NEVER touch derived/linkage fields here
                                    "pnl_usd": literal_column("trade_records.pnl_usd"),
                                    "remaining_size": literal_column("trade_records.remaining_size"),
                                    "realized_profit": literal_column("trade_records.realized_profit"),
                                    "parent_id": literal_column("trade_records.parent_id"),
                                    "parent_ids": literal_column("trade_records.parent_ids"),

                                    # Raw exchange fields (safe refresh)
                                    "status": insert_stmt.excluded.status,

                                    "price": case(
                                        (insert_stmt.excluded.price.isnot(None),
                                         case(
                                             (insert_stmt.excluded.price > 0, insert_stmt.excluded.price),
                                             else_=literal_column("trade_records.price"),
                                         )),
                                        else_=literal_column("trade_records.price"),
                                    ),
                                    "size": case(
                                        (insert_stmt.excluded.size.isnot(None),
                                         case(
                                             (insert_stmt.excluded.size > 0, insert_stmt.excluded.size),
                                             else_=literal_column("trade_records.size"),
                                         )),
                                        else_=literal_column("trade_records.size"),
                                    ),
                                    "order_time": case(
                                        (literal_column("trade_records.order_time").is_(None), insert_stmt.excluded.order_time),
                                        (insert_stmt.excluded.order_time.isnot(None),
                                         case(
                                             (insert_stmt.excluded.order_time < literal_column("trade_records.order_time"),
                                              insert_stmt.excluded.order_time),
                                             else_=literal_column("trade_records.order_time"),
                                         )),
                                        else_=literal_column("trade_records.order_time"),
                                    ),
                                    "side": case(
                                        (literal_column("trade_records.side").is_(None), insert_stmt.excluded.side),
                                        else_=literal_column("trade_records.side"),
                                    ),
                                    "order_type": case(
                                        (insert_stmt.excluded.order_type.isnot(None), insert_stmt.excluded.order_type),
                                        else_=literal_column("trade_records.order_type"),
                                    ),
                                    "trigger": case(
                                        (insert_stmt.excluded.trigger.isnot(None), insert_stmt.excluded.trigger),
                                        else_=literal_column("trade_records.trigger"),
                                    ),
                                    "source": case(
                                        (literal_column("trade_records.source").is_(None), insert_stmt.excluded.source),
                                                (literal_column("trade_records.source") == literal("unknown"), insert_stmt.excluded.source),
                                        else_=literal_column("trade_records.source"),
                                    ),
                                    "total_fees_usd": case(
                                        (insert_stmt.excluded.total_fees_usd.isnot(None),
                                         case(
                                             (insert_stmt.excluded.total_fees_usd >= literal_column("trade_records.total_fees_usd"),
                                              insert_stmt.excluded.total_fees_usd),
                                             else_=literal_column("trade_records.total_fees_usd"),
                                         )),
                                        else_=literal_column("trade_records.total_fees_usd"),
                                    ),
                                    # ✅ Always update ingestion provenance for this write
                                    "ingest_via": literal("rest"),
                                    "last_reconciled_at": func.now(),
                                    "last_reconciled_via": literal("rest_api"),
                                }

                                stmt = insert_stmt.on_conflict_do_update(
                                    index_elements=["order_id"],
                                    set_=update_cols,
                                    # Optional safety: skip rows that look "finalized"
                                    where=or_(
                                        literal_column("trade_records.pnl_usd").is_(None),
                                        literal_column("trade_records.source") == literal("unknown"),
                                    ),
                                )
                                result = await self._exec_with_deadlock_retry(sess, stmt)
                                updated_total += result.rowcount

                            await sess.commit()
                        finally:
                            await sess.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": GLOBAL_LOCK_KEY})

                    self.logger.info(
                        "✅ sync_open_orders → upserted %d rows (open=%d, recent=%d)",
                        updated_total, len(open_orders), len(recent_orders)
                    )

                except asyncio.CancelledError:
                    # Propagate cancellation cleanly (don’t log as an error)
                    self.logger.info("🛑 sync_open_orders cancelled during cycle.")
                    raise
                except Exception as exc:
                    # Real operational error
                    self.logger.error("❌ sync_open_orders failed: %s", exc, exc_info=True)
                    # Always sleep, even after failures, unless cancelled
                await asyncio.sleep(interval)

        except asyncio.CancelledError:
            self.logger.info("🛑 sync_open_orders cancelled cleanly.")
            return

    async def sync_order_tracker_from_exchange(self):
        """Fetch open orders from Coinbase and inject them into order_tracker + persist to DB."""
        try:
            resp = await self.coinbase_api.list_historical_orders(order_status="OPEN")
            orders = resp.get("orders", []) or []

            if not orders:
                self.logger.warning("⚠️ No open orders from exchange — skipping order_tracker sync.")
                return

            new_tracker = {}
            for order in orders:
                normalized = self.shared_data_manager.normalize_raw_order(order)
                if normalized:
                    new_tracker[normalized["order_id"]] = normalized

            await self.shared_data_manager.set_order_management({"order_tracker": new_tracker})
            await self.shared_data_manager.save_data()
            self.logger.debug(f"✅ Loaded {len(new_tracker)} open orders from REST and saved to DB.")
        except Exception as e:
            self.logger.error("❌ Failed to sync order_tracker from REST", exc_info=True)


    def pick(self, o: dict[str, Any], *paths: Sequence[str] | str, default=None):
        """
        Return the *first* non-empty value found in *o* for the given key-paths.

        Each *path* can be either
          • a plain string  ->  top-level key      ("price")
          • a tuple/list    ->  nested key-path    ("order_configuration", "limit_price")

        Empty strings, None, and empty lists are skipped.  If nothing is found,
        **default** is returned.
        """
        sentinel = object()
        for path in paths:
            cur = o
            if isinstance(path, (tuple, list)):
                for key in path:
                    cur = cur.get(key) if isinstance(cur, dict) else sentinel
                    if cur is sentinel:
                        break
            else:  # simple top-level key
                cur = o.get(path)
            if cur not in (None, "", []):
                return cur
        return default

    def iso_to_dt(self, ts: str | None):
        """Convert ISO-8601 strings like “…Z” into tz-aware datetimes (UTC)."""
        if not ts:
            return None
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        return datetime.fromisoformat(ts).astimezone(timezone.utc)



    async def create_app(self):
        """ Simplifies app creation by focusing on setting up routes only. """
        self.shared_utils_utility.log_event_loop("Webhook Server (create_app)")
        app = web.Application()
        app.router.add_post('/webhook', self.handle_webhook)
        app.router.add_get('/health', self.health)
        return app

    async def health(self, request: web.Request) -> web.Response:
        """
        Health check endpoint with WebSocket connection verification.

        Returns:
            200 OK if both user and market websockets are connected and healthy
            503 Service Unavailable if websockets are still connecting or have issues
        """
        # Check user websocket
        user_ws_healthy = (
            hasattr(self.websocket_helper, 'user_ws') and
            self.websocket_helper.user_ws is not None and
            not self.websocket_helper.user_ws.closed
        )

        # Check market websocket
        market_ws_healthy = (
            hasattr(self.websocket_helper, 'market_ws') and
            self.websocket_helper.market_ws is not None and
            not self.websocket_helper.market_ws.closed
        )

        # Both must be healthy for service to be fully operational
        if user_ws_healthy and market_ws_healthy:
            return web.json_response({
                "status": "ok",
                "websockets": {
                    "user": "connected",
                    "market": "connected"
                }
            })
        else:
            # Service is degraded - still starting up or reconnecting
            return web.json_response({
                "status": "degraded",
                "websockets": {
                    "user": "connected" if user_ws_healthy else "connecting",
                    "market": "connected" if market_ws_healthy else "connecting"
                }
            }, status=503)

    async def start(self, host: str = "127.0.0.1", port: int = 5003):
        """Start aiohttp server without blocking the event loop."""
        app = await self.create_app()
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, host, port)
        await self._site.start()

    async def stop(self):
        runner = getattr(self, "_runner", None)
        if runner:
            await runner.cleanup()


def handle_global_exception(loop, context):
    exception = context.get("exception")
    message = context.get("message", "Unhandled exception occurred")
    _module_logger.error(
        "Global exception handler caught",
        extra={'message': message, 'exception_type': type(exception).__name__ if exception else None},
        exc_info=exception
    )

    if hasattr(loop, 'log_manager'):
        loop.log_manager.error(f"Unhandled exception: {message}", exc_info=exception)
    else:
        _module_logger.error(f"Unhandled exception: {message}", exc_info=exception)

# def shutdown_handler(signal_received, frame):
#     """Gracefully shuts down the application by setting the shutdown event."""
#     print("\n� Shutting down gracefully...")
#     shutdown_event.set()  # ✅ Notify the event loop to stop

# async def initialize_market_data(listener, market_data_manager, shared_data_manager):
#     """Fetch and initialize market data safely after the event loop starts."""
#     await asyncio.sleep(1)  # Prevents race conditions
#     market_data_master, order_mgmnt_master = await market_data_manager.update_shared_data(time.time())
#     listener.initialize_listener_components(market_data_master, order_mgmnt_master, shared_data_manager)

async def supervised_task(task_coro, name):
    """Handles and logs errors in background tasks."""
    try:
        await task_coro
    except Exception as e:
        _module_logger.error(
            "Task encountered an error",
            extra={'task_name': name, 'error': str(e)},
            exc_info=True
        )



