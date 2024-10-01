import asyncio
import ccxt
import websockets
import aiohttp
import os
import hashlib
import uuid
import time
from datetime import datetime, timedelta
from aiohttp import web
import json
from datetime import datetime
from decimal import Decimal
import jwt
import logging
from Utils.logging_manager import LoggerManager
from alert_system import AlertSystem
from coinbase import jwt_generator
from config_manager import BotConfig
from custom_exceptions import ApiExceptions
from webhook_utils import TradeBotUtils
from webhook_validate_orders import ValidateOrders
from webhook_order_book import OrderBookManager
from webhook_order_manager import TradeOrderManager
from webhook_order_types import OrderTypeManager
from webhook_manager import WebHookManager


class CoinbaseAPI:
    def __init__(self, config, session):
        self.cb_api = config.load_tb_api_key()
        self.api_key = self.cb_api.get('name')
        self.api_secret = self.cb_api.get('privateKey')
        self.base_url = self.cb_api.get('api_url')
        self.client = config.setup_rest_client(self.api_key, self.api_secret)
        log_config = {"log_level": logging.INFO}
        self.webhook_logger = LoggerManager(log_config)  # Assign the logger
        self.log_manager = self.webhook_logger.get_logger('webhook_logger')
        self.alerts = AlertSystem(self.log_manager)
        self.session = session  # Store the session as an attribute
        self.api_algo = config.websocket_api.get('algorithm')

    def generate_jwt(self, method='POST', request_path='/api/v3/brokerage/orders'):
        # Build JWT for Coinbase using method and path
        jwt_uri = jwt_generator.format_jwt_uri(method, request_path)  # This formats the URI with method and path
        jwt_token = jwt_generator.build_rest_jwt(jwt_uri, self.api_key, self.api_secret)
        return jwt_token

    async def create_order(self, payload):
        """Create a new order with authentication using JWT"""
        request_path = '/api/v3/brokerage/orders'
        jwt_token = self.generate_jwt('POST', request_path)  # Pass method and path to generate the JWT

        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {jwt_token}'  # Use the JWT in the Authorization header
        }

        async with self.session.post(f'https://{self.base_url}{request_path}', headers=headers, json=payload) as response:
            if response.status == 401:
                # Log the response for debugging in case of unauthorized error
                text_response = await response.text()
                print(f"401 Unauthorized error: {text_response}")  # Debug unauthorized issue
                return {"error": "Unauthorized", "details": text_response}
            elif response.status != 200 and response.status != 201:
                # Handle other non-200 status codes
                text_response = await response.text()
                print(f"Error response [{response.status}]: {text_response}")  # Debug for other errors
                return {"error": f"Error {response.status}", "details": text_response}
            return await response.json()

    async def update_order(self, payload):
        request_path = '/api/v3/brokerage/orders/edit'
        jwt_token = self.generate_jwt()
        #jwt_token = self.generate_jwt('POST', request_path)
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {jwt_token}'
        }
        async with self.session.post(f'https://{self.base_url}{request_path}', headers=headers, json=payload) as response:
            return await response.json()

    async def fetch_open_orders(self):
        request_path = "/api/v3/brokerage/orders?status=OPEN"
        jwt_token = self.generate_jwt()
        # jwt_token = self.generate_jwt('GET', request_path)
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {jwt_token}'
        }
        async with self.session.get(f'https://{self.base_url}{request_path}', headers=headers) as res:
            response = await res.json()
            print(f"Fetched open orders: {response}")  # Debugging
            return response.get('orders', [])

class WebSocketHelper:
    def __init__(self, config, listener, order_tracker, exchange, ccxt_api, log_manager, coinbase_api):
        """
        WebSocketHelper is now responsible for WebSocket connections and API integration.
        """
        self.bot_config = config
        self.listener = listener  # Injected WebhookListener, for monitoring updates
        self.exchange = exchange
        self.ccxt_api = ccxt_api
        self.coinbase_api = coinbase_api
        self.order_tracker = order_tracker
        self.log_manager = log_manager
        # Initialize API key, secret, and algorithm from config
        self.api_key = config.websocket_api.get('name')
        self.api_secret = config.websocket_api.get('signing_key')
        self.api_algo = config.websocket_api.get('algorithm')
        # Initialize CoinbaseAPI only once, here in the WebSocketHelper
        self.coinbase_api = CoinbaseAPI(config, self.listener.session)

        self.api_channels = config.websocket_api.get('channel_names')
        self.base_url = config.websocket_api.get('api_url')
        self.keepalive_interval = 20  # Ping interval for WebSocket connection
        self.reconnect_delay = 5  # Delay before attempting reconnection
        self.connection_stable = True  # Track connection status
        self.order_tracker_lock = asyncio.Lock()

    async def start_websocket(self):
        uri = self.base_url
        while True:
            await asyncio.sleep(0.1)  # Yield back to the event loop
            try:
                async with websockets.connect(uri, ping_interval=self.keepalive_interval) as websocket:
                    print(f"WebSocket connected: {type(websocket)}")
                    await self.subscribe_to_products(websocket)
                    self.connection_stable = True  # Connection is stable after successful connect

                    async for message in websocket:
                        await self.on_message(websocket, message)

            except websockets.exceptions.ConnectionClosedError as e:
                print(f"WebSocket connection closed: {e}. Reconnecting in {self.reconnect_delay} seconds...")
                self.connection_stable = False  # Mark connection as unstable during reconnection
                await asyncio.sleep(self.reconnect_delay)

            except AttributeError as e:
                print(
                    f"AttributeError: {e}. Type of websocket: {type(websocket)}. Reconnecting in {self.reconnect_delay} seconds...")
                self.connection_stable = False
                await asyncio.sleep(self.reconnect_delay)

            except asyncio.CancelledError:
                print("WebSocket connection was cancelled. Exiting...")
                raise  # Re-raise the exception to properly handle cancellation

            except Exception as e:
                print(f"Unexpected error with WebSocket: {e}. Reconnecting in {self.reconnect_delay} seconds...")
                self.connection_stable = False  # Mark connection as unstable during reconnection
                await asyncio.sleep(self.reconnect_delay)


    async def subscribe_to_products(self, websocket):
        products = await self.fetch_usd_pairs()

        # Include the heartbeats channel in your subscription
        message = {
            "type": "subscribe",
            "channel": self.api_channels["user"],
            "product_ids": products,
            "jwt": self.coinbase_api.generate_jwt()  # Use CoinbaseAPI for JWT
        }

        # Subscribe to heartbeats
        heartbeat_message = {
            "type": "subscribe",
            "channel": "heartbeats",
            "jwt": self.coinbase_api.generate_jwt()  # Use CoinbaseAPI for JWT
        }

        # Send subscription messages
        signed_message = self.sign_with_jwt(message)
        await websocket.send(json.dumps(signed_message))

        await websocket.send(json.dumps(heartbeat_message))

    def sign_with_jwt(self, message):
        payload = {
            "iss": "coinbase-cloud",
            "nbf": int(time.time()),
            "exp": int(time.time()) + 120,
            "sub": self.api_key,
        }
        headers = {
            "kid": self.api_key,
            "nonce": hashlib.sha256(os.urandom(16)).hexdigest()
        }
        token = jwt.encode(payload, self.api_secret, algorithm=self.api_algo, headers=headers)
        message['jwt'] = token
        return message

    async def on_message(self, websocket, message):
        try:
            data = json.loads(message)

            if data.get('channel') == 'heartbeats':
                pass
                # Handle heartbeat message

                # print(f"Received heartbeat: {data}")
            elif 'events' in data:
                for event in data['events']:
                    if event.get('type') == 'snapshot':
                        if self.connection_stable:
                            print("snapshot event received, processing...")
                            await self.process_snapshot_event(event)
                        else:
                            print("Ignoring snapshot event during reconnection")
                    elif event.get('type') == 'update':
                        await self.process_update_event(event)
        except Exception as e:
            self.log_manager.error(f"Error processing message: {e}", exc_info=True)


    async def fetch_usd_pairs(self):
        try:
            endpoint = 'public'  # for rate limiting
            params = {
                'paginate': True,  # Enable automatic pagination
                'paginationCalls': 10,  # Set the max number of pagination calls if necessary
                'limit': 1000  # Set the max number of items to return
            }
            markets = await self.ccxt_api.ccxt_api_call(self.exchange.fetch_markets, endpoint, params=params)  # Fetch
            # markets using CCXT
            usd_pairs = [market['symbol'] for market in markets if market['quote'] == 'USD' and market['active']]
            return usd_pairs
        except Exception as e:
            print(f"Error fetching markets from CCXT: {e}")
            return []

    async def process_snapshot_event(self, event):
        """
        Handles 'snapshot' events received from WebSocket.
        """
        await self.process_event(event, event_type='snapshot')

    async def process_update_event(self, event):
        """
        Handles 'update' events received from WebSocket.
        """
        await self.process_event(event, event_type='update')

    async def process_event(self, event, event_type):
        """
        Processes both 'snapshot' and 'update' events in a generalized way.
        - Extracts order details and updates `order_tracker`.
        - Initiates or resumes order monitoring.
        """
        async with self.order_tracker_lock:
            try:
                print(f"Processing {event_type} event:", event)

                # Ensure there are orders to process
                if not event.get('orders'):
                    print(f"No orders in the {event_type} event, skipping processing.")
                    return

                # Loop through orders and handle accordingly
                for order in event.get('orders', []):
                    await self.handle_order(order, event_type)

            except Exception as e:
                self.log_manager.error(f"Error processing {event_type} event: {e}", exc_info=True)

    async def handle_order(self, order, event_type):
        """
        Processes a single order within an event (snapshot/update).
        - Determines if the order should be added to the `order_tracker`.
        - Handles OPEN and FILLED statuses.
        """
        order_id = order.get('order_id')
        symbol = order.get('product_id').replace('-', '/')
        status = order.get('status')
        avg_price = Decimal(order.get('avg_price')) if order.get('avg_price') else None
        limit_price = Decimal(order.get('limit_price')) if order.get('limit_price') else None
        stop_price = Decimal(order.get('stop_price')) if order.get('stop_price') else None
        amount = Decimal(order.get('leaves_quantity')) if order.get('leaves_quantity') else None

        # Handling OPEN orders: add to order tracker and monitor
        if status == 'OPEN':
            await self.add_to_order_tracker(order_id, symbol, stop_price, avg_price, amount, limit_price)

        # Handle FILLED orders differently depending on event type
        if status == 'FILLED' and order.get('order_side') == 'BUY':
            await self.store_filled_order(order_id, symbol, avg_price, event_type)

        print(f"Order {order_id} with status {status} processed in {event_type} event.")

    async def add_to_order_tracker(self, order_id, symbol, stop_price, avg_price, amount, limit_price):
        """
        Adds an active order to the order tracker and starts/resumes monitoring it.
        """
        if order_id not in self.order_tracker:
            print(f"Loading active order {order_id} into order_tracker.")
            self.order_tracker[order_id] = {
                'symbol': symbol,
                'initial_price': stop_price,  # Assuming the stop price is the initial price
                'purchase_price': avg_price,
                'amount': amount,
                'trailing_stop_price': stop_price,
                'limit_price': limit_price
            }

            # Start monitoring this order
            trailing_percentage = self.bot_config.trailing_percentage
            await self.listener.monitor_and_update_trailing_stop(symbol, stop_price, trailing_percentage,
                                                                 self.order_tracker[order_id])
        else:
            print(f"Order {order_id} already in tracker, resuming monitoring.")
            initial_trailing_price = self.order_tracker[order_id]['initial_price']
            trailing_percentage = self.bot_config.trailing_percentage
            await self.listener.monitor_and_update_trailing_stop(symbol, initial_trailing_price, trailing_percentage,
                                                                 self.order_tracker[order_id])

    async def store_filled_order(self, order_id, symbol, avg_price, event_type):
        """
        Stores a FILLED buy order in the order tracker for further processing.
        """
        print(f"Storing FILLED order {order_id} in {event_type} event.")
        self.order_tracker[order_id] = {
            'symbol': symbol,
            'purchase_price': avg_price,
            'initial_price': avg_price
        }
        await self.listener.handle_order_fill(self.order_tracker[order_id])


class WebhookListener:
    def __init__(self, config):
        self.bot_config = config
        self.session = aiohttp.ClientSession()  # Only needed for webhooks
        self.cb_api = self.bot_config.load_webhook_api_key()
        self.order_tracker = {}
        log_config = {"log_level": logging.INFO}
        self.webhook_logger = LoggerManager(log_config)  # Assign the logger
        self.log_manager = self.webhook_logger.get_logger('webhook_logger')
        self.coinbase_api = CoinbaseAPI(config, self.session)
        self.webhook_manager, self.utility = None, None  # Initialize webhook manager properly

        # Initialize ccxt exchange
        self.exchange = ccxt.coinbase({
            'apiKey': self.cb_api.get('name'),
            'secret': self.cb_api.get('privateKey'),
            'enableRateLimit': True,
            'verbose': False
        })


        self.coinbase_api = CoinbaseAPI(self.bot_config, self.session)
        self.alerts = AlertSystem(self.log_manager)
        self.ccxt_exceptions = ApiExceptions(self.exchange, self.log_manager, self.alerts)
        self.websocket_helper = WebSocketHelper(self.bot_config, self, self.order_tracker, self.exchange,
                                                self.ccxt_exceptions, self.log_manager, self.coinbase_api)
        self.utility = TradeBotUtils.get_instance(self.bot_config, self.log_manager, self.coinbase_api, self.exchange,
                                                  self.ccxt_exceptions, self.alerts, self.order_tracker)
        self.validate = ValidateOrders(self.log_manager, self.utility, self.bot_config)
        self.order_book_manager = OrderBookManager(self.exchange, self.utility, self.log_manager, self.ccxt_exceptions)
        self.order_type_manager = OrderTypeManager(self.bot_config, self.coinbase_api, self.exchange, self.utility,
                                                   self.validate, self.log_manager, self.alerts, self.ccxt_exceptions,
                                                   self.order_book_manager, self.session)
        self.trade_order_manager = TradeOrderManager(self.bot_config, self.coinbase_api, self.exchange, self.utility,
                                                     self.validate, self.log_manager, self.alerts, self.ccxt_exceptions,
                                                     self.order_book_manager, self.order_type_manager, self.session)
        self.webhook_manager = WebHookManager(self.log_manager, self.utility, self.trade_order_manager, self.alerts,
                                              self.session)

    async def handle_order_fill(self, websocket_msg):
        try:
            print(f"handle_order_fill started order_tracker:, {self.order_tracker}")
            symbol = websocket_msg['product_id'].replace('-', '/')
            print(f"Symbol: {symbol}")

            order_data = {
                'initial_order_id': websocket_msg['order_id'],
                'side': 'sell',  # Creating a sell order after the buy is filled
                'base_increment': Decimal('1E-8'),
                'base_decimal': 8,
                'quote_decimal': 2,
                'base_currency': symbol.split('/')[0],
                'quote_currency': symbol.split('/')[1],
                'trading_pair': symbol,
                'formatted_time': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                'quote_price': Decimal(websocket_msg['filled_value']),
                'quote_amount': Decimal(websocket_msg['filled_value']),
                'base_balance': Decimal(websocket_msg['cumulative_quantity']),
                'base_price': Decimal(websocket_msg['limit_price'])
            }

            await self._process_order_fill(order_data)
        except Exception as e:
            print(f"Error in handle_order_fill: {e}")

    async def _process_order_fill(self, order_data):
        try:
            trailing_stop_price = None
            response = False

            # Fetch order book and get adjusted price and size
            order_book = await self.order_book_manager.get_order_book(order_data)
            adjusted_price, adjusted_size = self.utility.adjust_price_and_size(order_data, order_book)
            print(self.order_tracker)

            # Place trailing stop order
            print("Placing trailing stop order")
            max_retries = 3
            for attempt in range(max_retries):
                response, market_price, trailing_stop_price = await self.order_type_manager.place_trailing_stop_order(
                    order_data, adjusted_price)
                if response.get('success', False):
                    print(f"Trailing stop order placed successfully on attempt {attempt + 1}")

                    # Load the trailing stop order into the order_tracker
                    trailing_stop_order_id = response.get('order_id')
                    self.order_tracker[trailing_stop_order_id] = {
                        'symbol': order_data['trading_pair'],
                        'initial_price': trailing_stop_price,
                        'purchase_price': order_data['base_price'],
                        'amount': order_data['base_balance'],
                        'trailing_stop_price': trailing_stop_price,
                        'limit_price': adjusted_price
                    }

                    print(f"Order tracker updated with trailing stop order: {trailing_stop_order_id}")

                    # Remove the associated buy order from the order_tracker
                    associated_buy_order_id = order_data['initial_order_id']
                    if associated_buy_order_id in self.order_tracker:
                        del self.order_tracker[associated_buy_order_id]
                        print(f"Removed associated buy order {associated_buy_order_id} from order_tracker")

                    break
                else:
                    print(f"Retry {attempt + 1}: Failed to place trailing stop order, reason: {response['failure_reason']}")
            print(f"Trailing stop order placed, response: {response}")

            # Start monitoring and updating the trailing stop order
            if response.get('success', False):
                trailing_percentage = self.bot_config.trailing_percentage
                initial_trailing_price = trailing_stop_price
                asyncio.create_task(self.monitor_and_update_trailing_stop(
                    order_data['trading_pair'], initial_trailing_price, trailing_percentage, order_data))
            else:
                print(
                    f"Failed to place trailing stop order after {max_retries} attempts. Reason: {response['failure_reason']}")
        except Exception as e:
            print(f"Error in _process_order_fill: {e}")

    async def monitor_and_update_trailing_stop(self, symbol, initial_trailing_price, trailing_percentage, order_data):
        try:
            orders_to_remove = []

            for order_id, order_info in list(self.order_tracker.items()):  # Convert to a list for safe iteration
                if order_info['symbol'] == symbol:
                    print(f"Monitoring started for {symbol}. Initial stop price: {initial_trailing_price}")
                    highest_price = initial_trailing_price  # Track the highest price seen
                    trailing_stop_order_id = order_id

                    while True:
                        await asyncio.sleep(0.1)  # Yield back to the event loop
                        try:
                            # Fetch the current price
                            current_price = await self.utility.fetch_spot(symbol)
                            print(f"Checking current price for {symbol}: {current_price}")

                            # Check if the trailing stop order has been filled or closed
                            order_status = await self.utility.check_order_status(symbol, trailing_stop_order_id)
                            if order_status.upper() == 'FILLED':
                                print(f"Trailing stop order {trailing_stop_order_id} has been filled. Exiting monitoring.")
                                orders_to_remove.append(trailing_stop_order_id)
                                break
                            elif order_status.upper() != 'OPEN':
                                print(f"Order {trailing_stop_order_id} is no longer active. Exiting monitoring.")
                                orders_to_remove.append(trailing_stop_order_id)
                                break

                            # Update the trailing stop prices only if the current price exceeds the highest recorded price
                            if current_price > highest_price:
                                highest_price = Decimal(current_price)
                                new_stop_price = highest_price * (1 - trailing_percentage / 100)
                                new_limit_price = new_stop_price * Decimal('0.9998')  # Slightly lower than the stop price

                                # Ensure the prices are properly formatted with the right precision
                                new_stop_price = round(new_stop_price, 2)
                                new_limit_price = round(new_limit_price, 2)

                                print(f"New stop price: {new_stop_price}, New limit price: {new_limit_price}")

                                # Cancel the existing order
                                cancel_response = await self.ccxt_exceptions.ccxt_api_call(
                                    self.exchange.cancel_order, 'private', trailing_stop_order_id
                                )
                                if cancel_response.get('info', {}).get('success', False):
                                    print(f"Trailing stop order {trailing_stop_order_id} canceled successfully.")  # debug

                                    # Place a new order with the updated prices
                                    payload = {
                                        "client_order_id": str(uuid.uuid4()),
                                        "product_id": symbol.replace('/', '-'),
                                        "side": "SELL",
                                        "order_configuration": {
                                            "stop_limit_stop_limit_gtd": {
                                                "base_size": str(order_data['base_balance']),
                                                "stop_price": str(new_stop_price),
                                                "limit_price": str(new_limit_price),
                                                "end_time": (datetime.utcnow() + timedelta(hours=24)).strftime(
                                                    '%Y-%m-%dT%H:%M:%SZ'),
                                                "stop_direction": "STOP_DIRECTION_STOP_DOWN"
                                            }
                                        }
                                    }
                                    response = await self.coinbase_api.create_order(payload)
                                    if response.get('success', False):
                                        print(
                                            f"New trailing stop order placed successfully with stop: {new_stop_price}, limit: {new_limit_price}")
                                        trailing_stop_order_id = response['order_id']
                                    else:
                                        print(
                                            f"Failed to place new trailing stop order: {response.get('failure_reason', 'Unknown error')}")
                                        break  # Exit if the new order cannot be placed
                                else:
                                    print(
                                        f"Failed to cancel trailing stop order: {cancel_response.get('failure_reason', 'Unknown error')}")
                                    break  # Exit if the cancelation fails
                            else:
                                print(
                                    f"No update needed. Current price: {current_price} has not exceeded highest price: {highest_price}")

                            await asyncio.sleep(15)

                        except Exception as inner_exception:
                            print(f"Error during monitoring loop: {inner_exception}")
                            break

            # Remove orders after monitoring is done
            for order_id in orders_to_remove:
                if order_id in self.order_tracker:
                    del self.order_tracker[order_id]
                    print(f"Removed order {order_id} from order_tracker.")
                else:
                    print(f"Order {order_id} was already removed or does not exist.")

        except Exception as e:
            self.log_manager.error(f"Error monitoring and updating trailing stop: {e}", exc_info=True)

    async def handle_webhook(self, request: web.Request) -> web.Response:
        """ Processes incoming webhook requests and delegates to WebHookManager. """

        try:
            ip_address = request.remote
            request_json = await request.json()
            response = await self.process_webhook(request_json, ip_address)
            return response

        except Exception as e:

            self.log_manager.error(f"Unhandled exception in handle_webhook: {str(e)}", exc_info=True)
            return web.json_response({"success": False, "message": "Internal server error"}, status=500)

    async def process_webhook(self, request_json, ip_address) -> web.Response:
        try:
            # Ensure the request contains an 'action' field.
            if not request_json.get('action'):
                return web.json_response({"success": False, "message": "Missing 'action' in request"}, status=400)

            # Check if the IP address is in the allowed whitelist
            whitelist = self.bot_config.get_whitelist()
            if ip_address not in whitelist:
                self.log_manager.error(f'webhook: {ip_address} is not whitelisted')
                return web.json_response({"success": False, "message": "Unauthorized"}, status=401)

            # Validate the origin of the request
            if 'SIGHOOK' not in request_json.get('origin', '') and 'TradingView' not in request_json.get('origin', ''):
                return web.json_response({"success": False, "message": "Invalid content type"}, status=415)

            # Extract trade data from webhook payload
            trade_data = self.webhook_manager.parse_webhook_data(request_json)

            # Fetch precision data (base/quote decimals and increments)
            precision_data = await self.utility.fetch_precision(trade_data['trading_pair'])
            if not all(precision_data):
                self.log_manager.error(
                    f'webhook: Failed to fetch precision data for {trade_data["trading_pair"]}')
                return web.json_response({"success": False, "message": 'Failed to fetch precision data'}, status=500)

            base_deci, quote_deci, base_increment, quote_increment = precision_data
            base_incri = self.utility.float_to_decimal(base_increment, base_deci) if base_deci else base_increment
            quote_incri = self.utility.float_to_decimal(quote_increment, quote_deci) if quote_deci else quote_increment

            # Fetch spot prices for the quote and base currencies
            quote_price = await self.utility.fetch_spot(trade_data["quote_currency"] + '-USD') \
                if trade_data["quote_currency"] != 'USD' else 1.00
            quote_price = self.utility.float_to_decimal(quote_price, quote_deci)

            base_price = await self.utility.fetch_spot(trade_data["base_currency"] + '-USD')
            if base_price:
                base_price = self.utility.float_to_decimal(base_price, quote_deci)
                if trade_data["side"] == 'buy':
                    base_price /= quote_price

            # Calculate the base order size and quote amount for the order
            base_order_size, quote_amount = self.webhook_manager.calculate_order_size(
                trade_data["side"], trade_data["quote_amount"], quote_price, base_price, base_deci
            )

            if trade_data["side"] == 'buy' and (
                    base_order_size is None or base_order_size == 0.0 or quote_amount is None or quote_amount == 0.0):
                return web.json_response({"success": False, "message": "Invalid order size"}, status=400)

            # Log the action to provide a record of the signal
            if 'open' in trade_data["action"] or 'close' in trade_data["action"]:
                print(f'{trade_data["origin"]} {trade_data["side"]} signal generated for {trade_data["trading_pair"]} '
                      f'at {trade_data["time"]}')

            # Create the order data dictionary
            order_data = {
                'side': trade_data['side'],
                'base_increment': base_incri,
                'base_decimal': base_deci,
                'quote_decimal': quote_deci,
                'base_currency': trade_data['base_currency'],
                'quote_currency': trade_data['quote_currency'],
                'trading_pair': trade_data['trading_pair'],
                'formatted_time': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                'quote_price': quote_price,
                'quote_amount': quote_amount,
                'base_price': base_price
            }

            # Process the action with WebHookManager (delegating logic)
            action_response = await self.webhook_manager.handle_action(order_data, precision_data)
            return web.json_response({"success": True, "message": "Action processed successfully"}, status=200)

        except Exception as e:
            self.log_manager.error(f"Error processing webhook: {e}", exc_info=True)
            return web.json_response({"success": False, "message": "Internal server error"}, status=500)

    async def close_resources(self):
        # No need to close the ccxt exchange instance
        if self.session:
            await self.session.close()
        print("Resources closed.")

    async def create_app(self):
        """ Simplifies app creation by focusing on setting up routes only. """
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


async def run_app(config):
    listener = WebhookListener(config)
    app = await listener.create_app()
    # Create and configure the event loop
    loop = asyncio.get_event_loop()

    # Optionally attach your log_manager to the loop if needed for logging in the handler
    loop.log_manager = listener.log_manager

    # Set the custom exception handler
    loop.set_exception_handler(handle_global_exception)
    runner = web.AppRunner(app)
    await runner.setup()


    try:
        site = web.TCPSite(runner, '0.0.0.0', config.port)
        await site.start()
        print(f'Webhook {config.program_version} is Listening on port {config.port}...')

        await listener.websocket_helper.start_websocket()
    except Exception as e:
        print(f"run_app: Exception caught - {e}")
    finally:
        await listener.close_resources()


if __name__ == '__main__':
    os.environ['PYTHONASYNCIODEBUG'] = '0'
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger('asyncio')
    logger.setLevel(logging.ERROR)

    bot_config = BotConfig()
    asyncio.run(run_app(bot_config))
