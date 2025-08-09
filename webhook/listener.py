

import json
import time
import uuid
import random
import socket
import asyncio
import websockets
import datetime as dt

from aiohttp import web
from decimal import Decimal
from sqlalchemy.sql import text
from sqlalchemy import case, or_
from datetime import datetime, timezone
from typing import Optional, Any, Sequence

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
from MarketDataManager.passive_order_manager import PassiveOrderManager



SYNC_INTERVAL = 60  # seconds
SYNC_LOOKBACK = 24



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
        if self.market_ws_task:
            self.logger.warning("🔄 Cancelling old market_ws_task...")
            self.market_ws_task.cancel()
            try:
                await self.market_ws_task
            except asyncio.CancelledError:
                self.logger.info("🧹 Previous market_ws_task cancelled cleanly.")
        self.market_ws_task = asyncio.create_task(
            self.connect_websocket(self.market_ws_url, is_user_ws=False)
        )

    async def connect_user_stream(self):
        """Reconnect the user WebSocket."""
        if self.user_ws_task:
            self.logger.warning("🔄 Cancelling old user_ws_task...")
            self.user_ws_task.cancel()
            try:
                await self.user_ws_task
            except asyncio.CancelledError:
                self.logger.info("🧹 Previous user_ws_task cancelled cleanly.")
        self.user_ws_task = asyncio.create_task(
            self.connect_websocket(self.user_ws_url, is_user_ws=True)
        )

    async def connect_websocket(self, ws_url, is_user_ws=False):
        """
        Establish and manage a WebSocket connection.
        Includes heartbeat, DNS refresh, auto-reset subscriptions, and alerting after repeated failures.
        """
        last_message_time = time.time()
        stream_name = "USER" if is_user_ws else "MARKET"
        MAX_ALERT_ATTEMPTS = 10  # 🔔 After 10 failed reconnects, send an alert

        while not self.shutdown_event.is_set():
            try:
                # ✅ Phase 0: Force DNS refresh before each attempt
                try:
                    host = ws_url.replace("wss://", "").split("/")[0]
                    await asyncio.get_running_loop().getaddrinfo(host, 443)
                    self.logger.debug(f"🌐 Refreshed DNS for {stream_name} WebSocket → {host}")
                except socket.gaierror as dns_error:
                    self.logger.warning(f"⚠️ DNS refresh failed for {stream_name} WebSocket: {dns_error}")

                async with websockets.connect(
                        ws_url,
                        ping_interval=30,
                        ping_timeout=30,
                        open_timeout=30,
                        max_size=2 ** 24,
                ) as ws:
                    # ✅ Reset reconnect attempts for the specific stream
                    if is_user_ws:
                        self.reconnect_attempts_user = 0
                        self.websocket_helper.user_ws = ws

                        # ✅ A: Always clear & reset subscriptions after reconnect
                        self.websocket_helper.subscribed_channels.clear()
                        await self.websocket_helper.subscribe_user()
                        self.logger.info(f"✅ Connected & subscribed to USER WebSocket: {ws_url}")
                    else:
                        self.reconnect_attempts_market = 0
                        self.websocket_helper.market_ws = ws

                        # ✅ A: Always clear & reset subscriptions after reconnect
                        self.websocket_helper.subscribed_channels.clear()
                        await self.websocket_helper.subscribe_market()
                        self.logger.info(f"✅ Connected to MARKET WebSocket: {ws_url}")
                        self.logger.info("📡 Market WebSocket subscription complete.")

                    self.logger.info(f"🎧 Listening on {stream_name} WebSocket: {ws_url}")
                    last_message_time = time.time()

                    async for message in ws:
                        last_message_time = time.time()
                        try:
                            if is_user_ws:
                                await self.websocket_helper._on_user_message_wrapper(message)
                            else:
                                await self.websocket_helper._on_market_message_wrapper(message)
                        except Exception as msg_error:
                            self.logger.error(f"❌ Error processing {stream_name} message: {msg_error}", exc_info=True)

                        # ✅ Heartbeat: Force reconnect if no message in 90 seconds
                        if time.time() - last_message_time > 90:
                            self.logger.warning(f"⚠️ No {stream_name} messages in 90s — forcing reconnect...")
                            break

            except asyncio.CancelledError:
                self.logger.info(f"⚠️ {stream_name} WebSocket task cancelled (shutdown or restart).")
                return
            except websockets.exceptions.ConnectionClosed as e:
                self.logger.warning(
                    f"🔌 {stream_name} WebSocket closed unexpectedly: "
                    f"Code={getattr(e, 'code', 'N/A')}, Reason={getattr(e, 'reason', 'No close frame received')}"
                )
            except Exception as general_error:
                self.logger.error(f"🔥 Unexpected {stream_name} WebSocket error: {general_error}", exc_info=True)

            # ✅ Phase 1: Per-stream exponential backoff with jitter
            attempts = self.reconnect_attempts_user if is_user_ws else self.reconnect_attempts_market
            delay = min(2 ** attempts, 60)
            self.logger.warning(
                f"🔁 Reconnecting {stream_name} WebSocket in {delay}s (Attempt {attempts + 1}, Total downtime ~{int(time.time() - last_message_time)}s)..."
            )

            # ✅ B: Alert after repeated failures
            if attempts + 1 >= MAX_ALERT_ATTEMPTS:
                if hasattr(self.listener, "alert") and self.listener.alert:
                    self.listener.alert.callhome(
                        f"{stream_name} WebSocket Down",
                        f"{stream_name} WebSocket failed to reconnect after {attempts + 1} attempts (~{int(time.time() - last_message_time)}s downtime).",
                        mode="email"
                    )
                self.logger.error(
                    f"🚨 {stream_name} WebSocket has failed to reconnect after {attempts + 1} attempts — Alert sent!"
                )

            await asyncio.sleep(delay + random.uniform(0, 5))

            if is_user_ws:
                self.reconnect_attempts_user += 1
            else:
                self.reconnect_attempts_market += 1

            # ✅ Phase 4: Post-reconnect REST sync
            await self.post_reconnect_sync()

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
                 database_session_manager, logger_manager,coinbase_api, session, market_manager, exchange, alert, order_book_manager):

        self.bot_config = bot_config
        self.test_mode = self.bot_config.test_mode
        if not hasattr(self.bot_config, 'rest_client') or not self.bot_config.rest_client:
            print("REST client is not initialized. Initializing now...")
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
            self.logger = logger_manager.loggers['webhook_logger']  # ✅ this is the actual logger you’ll use

        self.webhook_manager = self.ticker_manager = self.utility = None  # Initialize webhook manager properly
        self.ohlcv_manager = None
        self.order_manager = None
        self.processed_uuids = set()
        self.fee_rates={}

        # Core Utilities
        self.shared_utils_exchange = self.exchange
        self.shared_utils_precision = PrecisionUtils.get_instance(self.logger_manager, shared_data_manager)

        self.shared_utils_date_time = DatesAndTimes.get_instance(self.logger_manager)
        self.shared_utils_utility = SharedUtility.get_instance(self.logger_manager)
        self.shared_utils_print = PrintData.get_instance(self.logger_manager, self.shared_utils_utility)
        self.shared_utils_color = ColorCodes.get_instance()

        self.shared_utils_debugger = Debugging()


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
            shared_utils_debugger=self.shared_utils_debugger, # Placeholder
            order_book_manager=None,  # Placeholder
            snapshot_manager=None,  # Placeholder
            trade_order_manager=None,
            shared_data_manager=self.shared_data_manager,
            market_ws_manager=None,
            database_session_manager=database_session_manager

        )

        self.passive_order_manager = PassiveOrderManager(
            config=self.bot_config,
            ccxt_api=None,
            coinbase_api=None,
            exchange=None,
            ohlcv_manager=None,
            shared_data_manager=self.shared_data_manager,
            shared_utils_color=self.shared_utils_color,
            shared_utils_utility=None,
            shared_utils_precision=None,
            trade_order_manager=None,
            order_manager = None,
            logger_manager=self.logger_manager,
            min_spread_pct=self.bot_config.min_spread_pct,  # 0.15 %, overrides default 0.20 %
            fee_cache=self.fee_rates,
            # optional knobs ↓
            max_lifetime=90,  # cancel / refresh after 90 s
        )
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

        self.profit_data_manager = ProfitDataManager.get_instance(self.shared_utils_precision, self.shared_utils_print,
                                                                  self.shared_data_manager, self.logger_manager)

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
            self.shared_utils_debugger, self.order_book_manager,
            self.snapshot_manager, self.trade_order_manager, self.ohlcv_manager,
            self.shared_data_manager, self.database_session_manager
        )

        self.websocket_helper = WebSocketHelper(
            self, self.websocket_manager, self.logger,
            self.coinbase_api, self.profit_data_manager, self.order_type_manager,
             self.shared_utils_date_time, self.shared_utils_print,
            self.shared_utils_color, self.shared_utils_precision, self.shared_utils_utility,
            self.shared_utils_debugger,  self.order_book_manager,
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
                                                               self.shared_utils_debugger,self.shared_utils_print,
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
        """Refresh market_data and manage orders periodically."""
        try:

            try:
                # Fetch new market data
                start = time.monotonic()
                new_market_data, new_order_management = await self.market_data_updater.update_market_data(time.time())
                new_order_management["passive_orders"] = await self.database_session_manager.fetch_passive_orders()
                print(f"⚠️⚠️⚠️  update_market_data took {time.monotonic() - start:.2f}s    ⚠️⚠️⚠️")

                # Ensure fetched data is valid before proceeding
                if not new_market_data:
                    self.logger.error("❌ ⚠️⚠️⚠️      new_market_data is empty! Skipping update.     ⚠️⚠️⚠️⚠️ ❌")

                if not new_order_management:
                    self.logger.error("❌ ⚠️⚠️⚠️    new_order_management is empty! Skipping update. ⚠️⚠️⚠️⚠️ ❌")

                start = time.monotonic()
                new_market_data["last_updated"] = datetime.now(timezone.utc)
                new_market_data["fee_info"] = await self.coinbase_api.get_fee_rates()

                await self.shared_data_manager.update_shared_data(new_market_data, new_order_management)
                self.logger.debug(f"⏱ update_market_data (shared_data_manager) took {time.monotonic() - start:.2f}s")

                print("⚠️ Market data and order management updated successfully. ⚠️")
                # Monitor and update active orders

                start = time.monotonic()
                await self.asset_monitor.monitor_all_orders()
                self.logger.debug(f"⏱ monitor_and_update_active_orders took {time.monotonic() - start:.2f}s")
                pass


            except Exception as e:
                self.logger.error(f"❌ refresh_market_data inner loop error: {e}", exc_info=True)


        except asyncio.CancelledError:
            self.logger.warning("⚠️ refresh_market_data was cancelled by the event loop.", exc_info=True)
            raise  # allow the task to be properly cancelled

        except Exception as e:
            self.logger.error(f"❌ refresh_market_data crashed: {e}", exc_info=True)

        finally:
            self.logger.error("🚨 refresh_market_data loop exited unexpectedly!", exc_info=True)


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

            print(f"\n🟠 handle_order_fill - OrderData: {websocket_order_data.debug_summary(verbose=True)}\n")

            # Hand off to processor
            await self._process_order_fill('WebSocket', websocket_order_data)

        except Exception as e:
            print(f'websocket_msg: {websocket_order_data}')
            self.logger.error(f"Error in handle_order_fill: {e} {websocket_order_data}", exc_info=True)

    async def _process_order_fill(self, source, order_data: OrderData):
        """
        Process an order fill and place a corresponding trailing stop order.

        Args:
            order_data (dict): Details of the filled order, including symbol, price, and size.
        """
        print(f"Processing order fill: {order_data.side}:{order_data.trading_pair}")
        try:
            if order_data.open_orders:
                if order_data.open_orders.get('open_order'):
                    return

            # Use take profit stop loss
            order_data.source = source
            if order_data.side == 'buy':
                pass
            order_success, response_msg = await self.trade_order_manager.place_order(order_data)
            if response_msg:
                response_data = response_msg
                if response_data.get('error') == 'OPEN_ORDER':
                    return
            else:
                return

            if response_data:
                if response_data.get('details', {}).get("Order_id"):
                    pass
                    print(f'REVIEW CODE FOR TRAILING STOP ORDER (1789)*********************************')
                    # Add the trailing stop order to the order_tracker
                    self.order_management['order_tracker'][response_data["order_id"]] = {
                        'symbol': order_data.trading_pair,
                        'take_profit_price': order_data.take_profit_price,
                        'purchase_price': order_data.average_price,
                        'amount': order_data.order_amount_fiat,
                        'stop_loss_price': order_data.stop_loss_price,
                        'limit_price': order_data.limit_price * Decimal('1.002')  # Example limit price adjustment
                    }
                    order_id = response_data.get("order_id")
                    print(f"Order tracker updated with trailing stop order: {order_id}")

                    # Remove the associated buy order from the order_tracker
                    associated_buy_order_id = order_data.order_id
                    if associated_buy_order_id in self.order_management['order_tracker']:
                        del self.order_management['order_tracker'][associated_buy_order_id]
                        print(f"Removed associated buy order {associated_buy_order_id} from order_tracker")
            else:
                print("No response data received from order_type_manager.process_limit_and_tp_sl_orders")

        except Exception as e:
            self.logger.error(f"Error in _process_order_fill: {e}", exc_info=True)

    async def handle_webhook(self, request: web.Request) -> web.Response:
        """Processes incoming webhook requests and delegates to WebHookManager."""
        try:
            ip_address = request.remote

            # print(f"� Request Headers: {dict(request.headers)}")  # Debug
            request_json = await request.json()
            print(f"🔹 Receiving webhook: {request_json}")

            symbol = request_json.get("pair")
            side = request_json.get("side")
            order_amount = request_json.get("order_amount_fiat")
            origin = request_json.get("origin")

            if origin == "TradingView":
                print(f"Handling webhook request from: {origin} {symbol} uuid :{request_json.get('uuid')}")

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
    def is_ip_whitelisted(self, ip_address: str) -> bool:
        return ip_address in self.bot_config.get_whitelist()

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
            new_maker = self.fee_info.get("maker")
            old_maker = self.passive_order_manager.fee.get("maker")
            if new_maker is not None and new_maker != old_maker:
                await self.passive_order_manager.update_fee_cache(self.fee_info)

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
            source = "Webhook"
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
            print(f"\n 🟠️ process_webhook - Order Data: 🟠\n{order_details.debug_summary(verbose=True)}\n")

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

    async def reconcile_with_rest_api(self, limit: int = 100):
        logger = self.logger
        coinbase_api = self.coinbase_api
        shared_data_manager = self.shared_data_manager

        def _gross_and_fees_from_batch(order: dict) -> tuple[str, str]:
            # Keep as strings (JSON-safe); record_trade will Decimal() them.
            gross = str(order.get("filled_value") or "")
            fees = str(order.get("total_fees") or "")
            return gross, fees

        try:
            logger.info("🔁 Starting reconciliation via REST API...")

            # ✅ Fetch open orders
            open_orders = await coinbase_api.fetch_open_orders(limit=limit)
            logger.debug(f"📥 Retrieved {len(open_orders)} open orders")

            tracker = shared_data_manager.order_management.setdefault("order_tracker", {})

            for raw_order in open_orders:
                normalized = shared_data_manager.normalize_raw_order(raw_order)
                if not normalized:
                    continue

                order_id = normalized.get("order_id")
                if order_id not in tracker:
                    tracker[order_id] = normalized
                    logger.debug(f"📌 Added missing open order: {order_id}")

            # ✅ Fetch recent FILLED orders
            params = {
                "limit": limit,
                "order_status": ["FILLED"],
            }
            filled_response = await coinbase_api.get_historical_orders_batch(params)
            orders = filled_response.get("orders", [])
            logger.debug(f"📘 Retrieved {len(orders)} filled orders")

            reconciled_trades = []

            for order in orders:
                order_id = order.get("order_id")
                if not order_id or (order.get("status") or "").upper() != "FILLED":
                    continue

                existing_trade = await shared_data_manager.trade_recorder.fetch_trade_by_order_id(order_id)
                # ✅ Only skip fully complete trades
                if existing_trade and existing_trade.parent_id and existing_trade.parent_ids:
                    continue
                elif existing_trade:
                    logger.warning(f"⚠️ Trade {order_id} found in DB but incomplete — will reprocess.")

                side = (order.get("side") or "").lower()
                symbol = order.get("product_id")
                price = order.get("average_filled_price") or order.get("price")
                size = order.get("filled_size") or order.get("order_size") or "0"

                # Parse/clean order_time
                order_time = (
                        order.get("last_fill_time")
                        or order.get("completed_time")
                        or order.get("created_time")
                        or datetime.now(timezone.utc).isoformat()
                )
                total_fees = order.get("total_fees")

                # Parent suggestions (just hints; record_trade will FIFO anyway)
                parent_id = None
                parent_ids = None
                preferred_parent_id =  None
                if side == "buy":
                    parent_id = order_id
                    parent_ids = [order_id]
                elif side == "sell":
                    parent_id = await shared_data_manager.trade_recorder.find_latest_unlinked_buy_id(symbol)
                    parent_ids = [parent_id] if parent_id else None
                    preferred_parent_id = order.get("originating_order_id") or None

                # 👇 Step 5: use batch values instead of per-fill calls
                gross_override, fees_override = _gross_and_fees_from_batch(order)

                trade_data = {
                    "order_id": order_id,
                    "parent_id": parent_id,
                    "parent_ids": parent_ids,
                    "preferred_parent_id": preferred_parent_id,  # new optional hint
                    "symbol": symbol,
                    "side": side,
                    "price": price,
                    "amount": size,
                    "status": "filled",
                    "order_time": order_time,
                    "trigger": {"trigger": order.get("order_type", "market")},
                    "source": "reconciled",
                    "total_fees": total_fees,
                    # No 'fills' key in step 5
                    "gross_override": gross_override,  # order['filled_value']
                    "fees_override": fees_override,  # order['total_fees']
                }

                reconciled_trades.append(trade_data)

                # 🔧 Helper for sorting
                def parse_order_time(trade_dict):
                    ot = trade_dict["order_time"]
                    if isinstance(ot, str):
                        return datetime.fromisoformat(ot.replace("Z", "+00:00"))
                    return ot

                reconciled_trades.sort(key=parse_order_time)

                for trade in reconciled_trades:
                    await shared_data_manager.trade_recorder.enqueue_trade(trade)
                    logger.debug(f"🧾 Reconciled and recorded trade: {trade['order_id']}")

                await shared_data_manager.set_order_management({"order_tracker": tracker})
                await shared_data_manager.save_data()

                logger.debug("✅ Reconciliation complete.")
        except Exception as e:
            logger.error(f"❌ reconcile_with_rest_api() failed: {e}", exc_info=True)

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
        CHUNK = 200
        LOOKBACK_FIRST_RUN_HOURS = SYNC_LOOKBACK  # keep your existing constant
        LOOKBACK_SUBSEQUENT_HOURS = 1

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
            src = (order_source or "").lower()
            if "passivemm" in coid:
                return "PassiveMM"
            if "webhook" in coid:
                return "webhook"
            if "websocket" in coid:
                return "websocket"
            # preserve reconciled if present; else unknown
            return "reconciled" if "reconciled" in src else "unknown"

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
                                "source": source,
                            }
                            # DO NOT include: parent_id, parent_ids, pnl_usd, remaining_size, realized_profit
                            rows.append(row)

                        except Exception as e:
                            self.logger.warning(
                                "⚠️ Skipping order_id=%s due to transform error: %s",
                                o.get("order_id"), e, exc_info=True
                            )

                    # 4) Upsert in chunks — non-destructive policy
                    if not rows:
                        self.logger.debug("ℹ️ sync_open_orders → nothing to upsert.")
                        await asyncio.sleep(interval)
                        continue

                    updated_total = 0
                    async with self.database_session_manager.async_session() as sess:
                        for i in range(0, len(rows), CHUNK):
                            batch = rows[i:i + CHUNK]
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
                                    (insert_stmt.excluded.source != literal("unknown"), insert_stmt.excluded.source),
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
                            result = await sess.execute(stmt)
                            updated_total += result.rowcount

                        await sess.commit()

                    self.logger.info(
                        "✅ sync_open_orders → upserted %d rows (open=%d, recent=%d)",
                        updated_total, len(open_orders), len(recent_orders)
                    )

                except Exception as exc:
                    self.logger.error("❌ sync_open_orders failed: %s", exc, exc_info=True)

                # Always sleep, even after failures, unless cancelled
                await asyncio.sleep(interval)

        except asyncio.CancelledError:
            self.logger.info("🛑 sync_open_orders cancelled cleanly.")

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
        print(f"❌ Task {name} encountered an error: {e}")



