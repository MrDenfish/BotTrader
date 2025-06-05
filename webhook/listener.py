
import asyncio
import json
import time
import uuid
from sqlalchemy.dialects.postgresql import insert as pg_insert
from datetime import datetime, timezone
import datetime as dt
from decimal import Decimal
from typing import Optional, Any, Sequence
import websockets
from aiohttp import web

from Api_manager.api_manager import ApiManager
from Api_manager.coinbase_api import CoinbaseAPI
from MarketDataManager.ohlcv_manager import OHLCVManager
from MarketDataManager.ticker_manager import TickerManager
from MarketDataManager.passive_order_manager import PassiveOrderManager
from ProfitDataManager.profit_data_manager import ProfitDataManager
from Shared_Utils.alert_system import AlertSystem
from Shared_Utils.dates_and_times import DatesAndTimes
from Shared_Utils.debugger import Debugging
from Shared_Utils.enum import ValidationCode
from Shared_Utils.precision import PrecisionUtils
from Shared_Utils.print_data import PrintData
from Shared_Utils.snapshots_manager import SnapshotsManager
from Shared_Utils.utility import SharedUtility
from webhook.trailing_stop_manager import TrailingStopManager
from webhook.webhook_manager import WebHookManager
from webhook.webhook_order_book import OrderBookManager
from webhook.webhook_order_manager import TradeOrderManager
from webhook.webhook_order_types import OrderTypeManager
from webhook.webhook_utils import TradeBotUtils
from webhook.webhook_validate_orders import OrderData
from webhook.webhook_validate_orders import ValidateOrders
from webhook.websocket_helper import WebSocketHelper
from webhook.websocket_market_manager import WebSocketMarketManager
from TableModels.trade_record import TradeRecord


SYNC_INTERVAL = 60  # seconds
SYNC_LOOKBACK = 24


class WebSocketManager:
    def __init__(self, config, coinbase_api, logger_manager, websocket_helper):
        self.config = config
        self.coinbase_api = coinbase_api
        self.logger = logger_manager

        self.websocket_helper = websocket_helper

        self.user_ws_url = self.config.load_websocket_api_key().get('user_api_url')  # for websocket use not SDK
        self.market_ws_url = self.config.load_websocket_api_key().get('market_api_url')  # for websocket use not SDK

        self.market_ws_task = None
        self.user_ws_task = None

        self.reconnect_attempts = 0

    async def start_websockets(self):
        """Start both Market and User WebSockets."""
        try:
            self.market_ws_task = asyncio.create_task(
                self.connect_websocket(self.market_ws_url, is_user_ws=False)
            )
            self.user_ws_task = asyncio.create_task(
                self.connect_websocket(self.user_ws_url, is_user_ws=True)
            )

            asyncio.create_task(self.periodic_restart())
            asyncio.create_task(self.websocket_helper.monitor_market_channel_activity())

        except Exception as e:
            self.logger.error(f"Error starting WebSockets: {e}", exc_info=True)

    async def connect_market_stream(self):
        """Reconnect the market WebSocket."""
        await self.connect_websocket(self.market_ws_url, is_user_ws=False)

    async def connect_user_stream(self):
        """Reconnect the user WebSocket."""
        await self.connect_websocket(self.user_ws_url, is_user_ws=True)

    async def periodic_restart(self):
        """Restart WebSockets every 4 hours to ensure stability."""
        while True:
            await asyncio.sleep(14400)  # 4 hours
            self.logger.info("Restarting WebSockets to ensure stability...")
            await self.websocket_helper.reconnect()

    async def connect_websocket(self, ws_url, is_user_ws=False):
        """Establish and manage a WebSocket connection."""
        while True:
            try:
                async with websockets.connect(ws_url, max_size=2 ** 20) as ws:
                    self.logger.info(f"Connected to {ws_url}")
                    self.reconnect_attempts = 0

                    if is_user_ws:
                        self.websocket_helper.user_ws = ws
                        await self.websocket_helper.subscribe_user()
                    else:
                        self.websocket_helper.market_ws = ws
                        await asyncio.sleep(1)
                        self.logger.info("⚡ Subscribing to Market Channels...")
                        await self.websocket_helper.subscribe_market()

                    self.logger.info(f"Listening on {ws_url}")

                    # Setup dispatch map for known channel handlers
                    handlers = {
                        "user": self.websocket_helper._on_user_message_wrapper,
                        "ticker_batch": self.websocket_helper._on_market_message_wrapper,
                        "heartbeats": self.websocket_helper._on_market_message_wrapper,
                        "subscriptions": self.websocket_helper._on_market_message_wrapper
                    }

                    async for message in ws:
                        try:
                            # print(f" ⚠️ Raw WebSocket message:\n{message} ⚠️")
                            data = json.loads(message)
                            channel = data.get("channel", "")

                            handler = handlers.get(channel)
                            if handler:
                                await handler(message)
                            else:
                                self.logger.warning(
                                    f"⚠️ Unknown or unsupported WebSocket channel: "
                                    f"{channel}.\nFull message:\n{json.dumps(data, indent=2)}"
                                )

                        except Exception as msg_error:
                            self.logger.error(f"Error processing message: {msg_error}", exc_info=True)
            except asyncio.CancelledError:
                self.logger.warning("⚠️ WebSocket connection task was cancelled.")
                raise  # re-raise to allow upstream shutdown handling
            except websockets.exceptions.ConnectionClosedError as e:
                self.logger.warning(f"WebSocket closed unexpectedly: {e}. Reconnecting...")
                await asyncio.sleep(min(2 ** self.reconnect_attempts, 60))
                self.reconnect_attempts += 1
            except Exception as general_error:
                self.logger.error(f"Unexpected WebSocket error, check NGROK connection: {general_error}", exc_info=True)
                await asyncio.sleep(min(2 ** self.reconnect_attempts, 60))
                self.reconnect_attempts += 1


class WebhookListener:
    """The WebhookListener class is the central orchestrator of the bot,
    handling market data updates, order management, and webhooks."""

    _exchange_instance_count = 0

    def __init__(self, bot_config, shared_data_manager, database_session_manager, logger_manager, session, market_manager,
                 market_data_updater, exchange):
        self.bot_config = bot_config
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
        self.market_manager = market_manager
        self.market_data_updater = market_data_updater
        self.logger_manager = logger_manager  # 🙂
        self.logger = logger_manager.loggers['webhook_logger']  # ✅ this is the actual logger you’ll use

        self.webhook_manager = self.ticker_manager = self.utility = None  # Initialize webhook manager properly
        self.ohlcv_manager = None
        self.processed_uuids = set()
        self.fee_rates={}

        # Core Utilities
        self.shared_utils_exchange = self.exchange
        self.shared_utils_precision = PrecisionUtils.get_instance(self.logger_manager, self.shared_data_manager)

        self.shared_utiles_data_time = DatesAndTimes.get_instance(self.logger_manager)
        self.shared_utils_utility = SharedUtility.get_instance(self.logger_manager)
        self.shared_utils_print = PrintData.get_instance(self.logger_manager, self.shared_utils_utility)
        self.shared_utils_debugger = Debugging()

        self.coinbase_api = CoinbaseAPI(self.session, self.shared_utils_utility, self.logger_manager,
                                        self.shared_utils_precision )
        self.alerts = AlertSystem(self.logger_manager)
        self.ccxt_api = ApiManager.get_instance(self.exchange, self.logger_manager, self.alerts)

        #database related
        self.database_session_manager = database_session_manager

        self.lock = asyncio.Lock()
        # created without WebSocketHelper initially

        # ✅ Step 1: Create WebSocketHelper With Placeholders
        self.websocket_helper = WebSocketHelper(
            listener=self,
            websocket_manager=None,  # Placeholder
            exchange=self.exchange, # Placeholder
            ccxt_api=self.ccxt_api, # Placeholder
            logger_manager=self.logger_manager,
            coinbase_api=self.coinbase_api,
            profit_data_manager=None,  # Placeholder
            order_type_manager=None,  # Placeholder
            shared_utils_print=self.shared_utils_print, # Placeholder
            shared_utils_precision=self.shared_utils_precision,
            shared_utils_utility=self.shared_utils_utility, # Placeholder
            shared_utils_debugger=self.shared_utils_debugger, # Placeholder
            trailing_stop_manager=None,  # Placeholder
            order_book_manager=None,  # Placeholder
            snapshot_manager=None,  # Placeholder
            trade_order_manager=None,
            ohlcv_manager=None,
            shared_data_manager=self.shared_data_manager,
            market_ws_manager=None,
            order_manager=None  # Placeholder

        )

        self.passive_order_manager = PassiveOrderManager(
            config=self.bot_config,
            ccxt_api=None,
            coinbase_api=None,
            exchange=None,
            ohlcv_manager=None,
            shared_data_manager=None,
            shared_utils_utility=None,
            shared_utils_precision=None,
            trade_order_manager=None,
            order_manager = None,
            logger=None,
            fee_cache=self.fee_rates,
            min_spread_pct=self.bot_config.min_spread_pct,  # 0.15 %, overrides default 0.20 %
            # optional knobs ↓
            max_lifetime=90,  # cancel / refresh after 90 s
        )
        self.websocket_manager = WebSocketManager(self.bot_config, self.ccxt_api, self.logger,
                                                  self.websocket_helper)

        self.websocket_helper.websocket_manager = self.websocket_manager

        # self.coinbase_api = CoinbaseAPI(self.session, self.shared_utils_utility, self.logger)

        self.snapshot_manager = SnapshotsManager.get_instance(self.shared_data_manager, self.shared_utils_precision,
                                                              self.logger_manager)

        # Instantiation of ....
        self.utility = TradeBotUtils.get_instance(self.logger, self.coinbase_api, self.exchange,
                                                  self.ccxt_api, self.alerts, self.shared_data_manager)


        self.ticker_manager = None

        self.profit_data_manager = ProfitDataManager.get_instance(self.shared_utils_precision, self.shared_utils_print,
                                                                  self.shared_data_manager, self.logger_manager)

        self.order_book_manager = OrderBookManager.get_instance(self.exchange, self.shared_utils_precision,
                                                                self.logger, self.ccxt_api)

        self.validate = ValidateOrders.get_instance(self.logger, self.order_book_manager,
                                                    self.shared_utils_precision)

        self.order_type_manager = OrderTypeManager.get_instance(
            coinbase_api=self.coinbase_api,
            exchange_client=self.exchange,
            shared_utils_precision=self.shared_utils_precision,
            shared_utils_utility=self.shared_utils_utility,
            shared_data_manager=self.shared_data_manager,
            validate=self.validate,
            logger_manager=self.logger,
            alerts=self.alerts,
            ccxt_api=self.ccxt_api,
            order_book_manager=self.order_book_manager,
            websocket_helper=None, #Placeholder for self.websocket_helper,
            session=self.session
        )

        # self.market_data_lock = asyncio.Lock()

        self.trailing_stop_manager = TrailingStopManager.get_instance(self.logger, self.shared_utils_precision,
                                                                      self.coinbase_api, self.shared_data_manager,
                                                                      self.order_type_manager)

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

        self.websocket_helper = WebSocketHelper(
            self, self.websocket_manager, self.exchange, self.ccxt_api, self.logger,
            self.coinbase_api, self.profit_data_manager, self.order_type_manager,
            self.shared_utils_print, self.shared_utils_precision, self.shared_utils_utility,
            self.shared_utils_debugger, self.trailing_stop_manager, self.order_book_manager,
            self.snapshot_manager, self.trade_order_manager, None,
            self.shared_data_manager, self.session, None

        )
        self.market_ws_manager = WebSocketMarketManager(
            self, self.exchange, self.ccxt_api, self.logger, self.coinbase_api,
            self.profit_data_manager, self.order_type_manager, self.shared_utils_print,
            self.shared_utils_precision, self.shared_utils_utility, self.shared_utils_debugger,
            self.trailing_stop_manager, self.order_book_manager, self.snapshot_manager,
            self.trade_order_manager, self.ohlcv_manager, self.shared_data_manager
        )


    async def async_init(self):
        """Initialize async components after __init__."""
        self.ohlcv_manager = await OHLCVManager.get_instance(self.exchange, self.ccxt_api, self.logger_manager,
                                                             self.shared_utiles_data_time, self.market_manager)
        self.ticker_manager = await TickerManager.get_instance(self.bot_config, self.shared_utils_debugger,
                                                               self.shared_utils_print, self.logger_manager,
                                                               self.rest_client, self.portfolio_uuid, self.exchange,
                                                               self.ccxt_api, self.shared_data_manager,
                                                               self.shared_utils_precision
        )

    @property
    def market_data(self):
        return self.shared_data_manager.market_data

    @property
    def order_management(self):
        return self.shared_data_manager.order_management

    @property
    def ticker_cache(self):
        return self.market_data.get('ticker_cache', {})

    @property
    def current_prices(self):
        return self.market_data.get('current_prices', {})

    @property
    def filtered_balances(self):
        return self.order_management.get('non_zero_balances', {})


    async def refresh_market_data(self):
        """Refresh market_data and manage orders periodically."""
        try:

            try:
                # Fetch new market data
                start = time.monotonic()
                new_market_data, new_order_management = await self.market_data_updater.update_market_data(time.time())
                self.logger.info(f"⏱ update_market_data took {time.monotonic() - start:.2f}s")

                # Ensure fetched data is valid before proceeding
                if not new_market_data:
                    self.logger.error("❌ new_market_data is empty! Skipping update.")

                if not new_order_management:
                    self.logger.error("❌ new_order_management is empty! Skipping update.")

                # Refresh open orders and get the updated order_tracker
                start = time.monotonic()
                _, _, updated_order_tracker = await self.websocket_helper.refresh_open_orders()
                self.logger.info(f"⏱ refresh_open_orders took {time.monotonic() - start:.2f}s")

                # Reflect the updated order_tracker in the shared state
                if updated_order_tracker:
                    new_order_management['order_tracker'] = updated_order_tracker
                # Update shared state via SharedDataManager

                start = time.monotonic()
                await self.shared_data_manager.update_shared_data(new_market_data, new_order_management)
                self.logger.info(f"⏱ update_market_data (shared_data_manager) took {time.monotonic() - start:.2f}s")

                print("⚠️ Market data and order management updated successfully. ⚠️")
                # Monitor and update active orders

                start = time.monotonic()
                await self.websocket_helper.monitor_and_update_active_orders(new_market_data, new_order_management)
                self.logger.info(f"⏱ monitor_and_update_active_orders took {time.monotonic() - start:.2f}s")
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


            # Fetch the order book for price and size adjustments
            order_book = await self.order_book_manager.get_order_book(order_data)

            # Use take profit stop loss
            order_data.source = source

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
            webhook_uuid = request_json.get('uuid')
            if not webhook_uuid:
                return web.json_response(
                    {"success": False, "message": "Missing 'uuid' in request"},
                    status=int(ValidationCode.MISSING_UUID.value)
                )

            if webhook_uuid in self.processed_uuids:
                self.logger.info(f"Duplicate webhook detected: {webhook_uuid}")
                return web.json_response(
                    {"success": False, "message": "Duplicate 'uuid' detected"},
                    status=int(ValidationCode.DUPLICATE_UUID.value)
                )

            await self.add_uuid_to_cache(webhook_uuid)

            if not request_json.get('action'):
                return web.json_response(
                    {"success": False, "message": "Missing action"},
                    status=int(ValidationCode.MISSING_ACTION.value)
                )

            if not self.is_ip_whitelisted(ip_address):
                return web.json_response(
                    {"success": False, "message": "Unauthorized"},
                    status=int(ValidationCode.UNAUTHORIZED.value)
                )

            if not WebhookListener.is_valid_origin(request_json.get('origin', '')):
                return web.json_response(
                    {"success": False, "message": "FORBIDDEN"},
                    status=int(ValidationCode.FORBIDDEN.value)
                )

            # Parse trade data and fetch market/order snapshots
            trade_data = self.webhook_manager.parse_webhook_request(request_json)
            product_id = trade_data.get('trading_pair')
            asset = product_id.split('/')[0]

            combined_snapshot = await self.snapshot_manager.get_market_data_snapshot()
            market_data_snapshot = combined_snapshot["market_data"]
            order_management_snapshot = combined_snapshot["order_management"]
            usd_pairs = market_data_snapshot.get("usd_pairs_cache", {})

            precision_data = self.shared_utils_precision.fetch_precision(trade_data["trading_pair"])


            if not self.is_valid_precision(precision_data):
                return web.json_response(
                    {"success": False, "message": "Failed to fetch precision data"},
                    status=int(ValidationCode.PRECISION_ERROR.value)
                )

            base_price_in_fiat, quote_price_in_fiat = await self.get_prices(trade_data, market_data_snapshot)

            asset_obj = order_management_snapshot.get("non_zero_balances", {}).get(asset)


            fee_info = await self.coinbase_api.get_fee_rates()
            new_maker = fee_info.get("maker")
            old_maker = self.passive_order_manager.fee.get("maker")

            if new_maker is not None and new_maker != old_maker:
                await self.passive_order_manager.update_fee_cache(fee_info)
            _, _, base_value = self.calculate_order_size_fiat(trade_data, base_price_in_fiat, quote_price_in_fiat,
                                                         precision_data, fee_info)
            if trade_data["side"] == "sell" and base_value < float(self.min_sell_value):
                return web.json_response(
                    {"success": False, "message": f"Insufficient balance to sell {asset} (requires {self.min_sell_value} USD)"},
                    status=int(ValidationCode.INSUFFICIENT_BASE.value)
                )


            # Build order and place it
            source = 'Webhook'
            trigger = trade_data.get('trigger','strategy')
            trigger = {"trigger": f"{trigger}", "trigger_note": f"from webhook"}
            order_details = await self.trade_order_manager.build_order_data(source, trigger, asset, product_id, None, fee_info)
            if order_details is None:
                return web.json_response(
                    {"success": False, "message": f"Order build failed"},
                    status=int(ValidationCode.ORDER_BUILD_FAILED.value)
                )
            order_details.trigger = trigger
            print(f'')
            print(f' 🟠️ process_webhook - Order Data: 🟠   {order_details.debug_summary(verbose=True)}  ')  # Debug
            print(f'')
            response = await self.webhook_manager.handle_action(order_details, precision_data)
            code = response.get("code", 200)

            # ✅ Convert Decimals to JSON-safe format
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
            current_prices = market_data_snapshot.get('current_prices', {})
            base_price_in_fiat = self.shared_utils_precision.float_to_decimal(current_prices.get(trading_pair, 0), quote_deci)
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
        """Periodically save shared data every `interval` seconds."""
        while True:
            try:
                # Synchronize the latest market_data and order_management
                market_data_snapshot, order_management_snapshot = await self.shared_data_manager.get_snapshots()

                # Update shared data with the latest snapshots
                await self.shared_data_manager.update_shared_data(
                    new_market_data=market_data_snapshot,
                    new_order_management=order_management_snapshot
                )
                # Save the updated data
                await self.shared_data_manager.save_data()
                self.logger.debug("Periodic save completed successfully.")
            except Exception as e:
                self.logger.error(f"Error during periodic save: {e}", exc_info=True)
            await asyncio.sleep(interval)

    async def sync_open_orders(self, interval: int = 60 * 60) -> None:
        """
        Periodically sync Coinbase open orders and recently-updated orders with the
        trade_records table.  Runs forever: immediately once, then every *interval*
        seconds (default = 60 min).

        * First run looks back SYNC_LOOKBACK h to catch anything missed during
          downtime; subsequent runs only look back 1 h.
        * UPSERTs each order (ON CONFLICT … DO UPDATE) so we never attempt to pass
          SQLAlchemy SQL-objects as bound parameters.
        """
        first_run = True

        while True:
            try:
                self.logger.info("🔄 Starting sync_open_orders cycle…")

                # ------------------------------------------------------------------
                # 1) Decide look-back window
                # ------------------------------------------------------------------
                lookback_hours = SYNC_LOOKBACK if first_run else 1
                since_iso = (
                                    dt.datetime.utcnow() - dt.timedelta(hours=lookback_hours)
                            ).isoformat(timespec="seconds") + "Z"
                first_run = False

                # ------------------------------------------------------------------
                # 2) Collect orders from Coinbase
                # ------------------------------------------------------------------
                open_resp = await self.coinbase_api.list_historical_orders(
                    limit=250, order_status="OPEN"
                )
                recent_resp = await self.coinbase_api.list_historical_orders(
                    limit=250, start_time=since_iso
                )
                open_orders = open_resp.get("orders", []) or []
                recent_orders = recent_resp.get("orders", []) or []

                self.logger.debug(
                    f"Fetched {len(open_orders)} open orders "
                    f"and {len(recent_orders)} recent orders (since {since_iso})."
                )

                # de-dup
                seen, orders_to_process = set(), []
                for o in open_orders + recent_orders:
                    oid = o.get("order_id")
                    if oid and oid not in seen:
                        orders_to_process.append(o)
                        seen.add(oid)
                # ------------------------------------------------------------------
                # 3) Transform into TradeRecord-shaped dicts
                # ------------------------------------------------------------------
                rows: list[dict] = []
                for o in orders_to_process:
                    rows.append(
                        {
                            "order_id": o["order_id"],
                            "parent_id": None if o.get("side", "").lower() == "buy" else o.get("originating_order_id"),
                            "symbol": o["product_id"],
                            "side": o["side"].lower(),
                            "order_type": o.get("order_type", "unknown").lower(),
                            "order_time": self.iso_to_dt(
                                self.pick(
                                    o,
                                    "created_time",
                                    ("order_configuration", "created_time"),
                                    ("edit_history", 0, "created_time"),
                                )
                            ),
                            "price": float(
                                Decimal(
                                    self.pick(
                                        o,
                                        "price",
                                        ("order_configuration", "price"),
                                        "average_filled_price",
                                        ("order_configuration", "trigger_bracket_gtc", "limit_price"),
                                    ) or 0
                                )
                            ),
                            "size": float(
                                Decimal(
                                    self.pick(
                                        o,
                                        "size",
                                        "filled_size",
                                        "order_size",
                                        ("order_configuration", "base_size"),
                                    ) or 0
                                )
                            ),
                            "pnl_usd": None,
                            "total_fees_usd": float(
                                Decimal(
                                    self.pick(
                                        o,
                                        "total_fees",
                                    ) or 0
                                )
                            ),
                            "trigger": o.get("order_type", "").lower(),
                            "status": o["status"].lower(),
                        }
                    )

                # ------------------------------------------------------------------
                # 4) UPSERT
                # ------------------------------------------------------------------
                if not rows:
                    self.logger.info("ℹ️ sync_open_orders → nothing to upsert.")
                    await asyncio.sleep(interval)
                    continue

                CHUNK = 200
                inserted_total = updated_total = 0
                async with self.database_session_manager.async_session_factory() as sess:
                    for i in range(0, len(rows), CHUNK):
                        batch = rows[i: i + CHUNK]
                        insert_stmt = pg_insert(TradeRecord).values(batch)
                        # Columns we want to update if the order_id already exists
                        update_cols = {
                            c: insert_stmt.excluded[c]
                            for c in (
                                "parent_id",
                                "status",
                                "price",
                                "size",
                                "order_time",
                                "side",
                                "trigger",
                            )
                        }
                        stmt = insert_stmt.on_conflict_do_update(
                            index_elements=["order_id"],
                            set_=update_cols,
                        )
                        result = await sess.execute(stmt)
                        # rowcount = # rows inserted **or** updated in this chunk
                        # We cannot distinguish easily without RETURNING, so we add them all
                        updated_total += result.rowcount
                    await sess.commit()
                self.logger.info(
                    f"✅ sync_open_orders → upserted {updated_total} rows "
                    f"({len(open_orders)} open / {len(recent_orders)} recent)"
                )
            except Exception as exc:
                self.logger.error("❌ sync_open_orders failed: %s", exc, exc_info=True)

            await asyncio.sleep(interval)

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


shutdown_event = asyncio.Event()  # ✅ Define the event globally

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



