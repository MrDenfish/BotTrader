import asyncio
import ccxt
import websockets
import aiohttp
import os
import hashlib
import time
from datetime import datetime, timedelta
from aiohttp import web
import json
from datetime import datetime
from decimal import Decimal
import jwt
import logging
from log_manager import LoggerManager
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
        self.log_manager = LoggerManager(config, log_dir=config.log_dir)
        self.alerts = AlertSystem(self.log_manager)
        self.session = session  # Store the session as an attribute

    def generate_jwt(self, request_method, request_path):
        jwt_uri = jwt_generator.format_jwt_uri(request_method, request_path)
        jwt_token = jwt_generator.build_rest_jwt(jwt_uri, self.api_key, self.api_secret)
        return jwt_token

    async def create_order(self, payload):
        request_path = '/api/v3/brokerage/orders'
        jwt_token = self.generate_jwt('POST', request_path)
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {jwt_token}'
        }
        async with self.session.post(f'https://{self.base_url}{request_path}', headers=headers, json=payload) as response:
            return await response.json()

    async def update_order(self, payload):
        request_path = '/api/v3/brokerage/orders'
        jwt_token = self.generate_jwt('PUT', request_path)
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {jwt_token}'
        }
        async with self.session.put(f'https://{self.base_url}{request_path}', headers=headers, json=payload) as response:
            return await response.json()

    async def fetch_order(self, order_id):
        request_path = f"/api/v3/brokerage/orders/{order_id}"
        jwt_token = self.generate_jwt('GET', request_path)
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {jwt_token}'
        }
        async with self.session.get(f'https://{self.base_url}{request_path}', headers=headers) as res:
            return await res.json()


class WebsocketHelper:
    def __init__(self, config, listener):
        self.bot_config = config
        self.listener = listener
        self.api_key = config.websocket_api.get('name')
        self.api_secret = config.websocket_api.get('signing_key')
        self.api_algo = config.websocket_api.get('algorithm')
        self.api_channels = config.websocket_api.get('channel_names')
        self.base_url = config.websocket_api.get('api_url')

    async def start_websocket(self):
        uri = self.base_url
        async with websockets.connect(uri) as websocket:
            await self.subscribe_to_products(websocket)
            async for message in websocket:
                await self.on_message(message)

    async def subscribe_to_products(self, websocket):
        products = ["BTC-USD", "SOL-USD"]
        message = {
            "type": "subscribe",
            "channel": self.api_channels["user"],
            "product_ids": products
        }
        signed_message = self.sign_with_jwt(message)
        await websocket.send(json.dumps(signed_message))

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

    async def on_message(self, message):
        data = json.loads(message)
        print(f"Received message: {data}")

        if 'events' in data:
            for event in data['events']:
                if event.get('type') == 'update':
                    for order in event.get('orders', []):
                        if order.get('status') == 'FILLED':
                            """ after a buy is filled, place a sell order """
                            print("Order status is FILLED, attempting to run handle_order_fill")
                            try:
                                await self.listener.handle_order_fill(order)
                            except Exception as e:
                                print(f"Error in handle_order_fill: {e}")
                        else:
                            print(f"Order status is {order.get('status')}")


class WebhookListener:
    def __init__(self, config):
        self.bot_config = config
        self.session = aiohttp.ClientSession()  # Create the session once in the constructor
        self.cb_api = self.bot_config.load_webhook_api_key()
        self.websocket_helper = WebsocketHelper(config, self)
        self.coinbase_api = CoinbaseAPI(config, self.session)
        self.log_manager = LoggerManager(config, log_dir=config.log_dir)
        self.webhook_manager, self.utility = None, None  # Initialize webhook manager properly

        # Initialize ccxt exchange
        self.exchange = ccxt.coinbase({
            'apiKey': self.cb_api.get('name'),
            'secret': self.cb_api.get('privateKey'),
            'enableRateLimit': True,
            'verbose': False
        })
        self.coinbase_api = CoinbaseAPI(self.bot_config, self.session)
        self.websocket_helper = WebsocketHelper(self.bot_config, self)
        self.log_manager = LoggerManager(self.bot_config, log_dir=self.bot_config.log_dir)
        self.alerts = AlertSystem(self.log_manager)
        self.ccxt_exceptions = ApiExceptions(self.exchange, self.log_manager, self.alerts)
        self.utility = TradeBotUtils.get_instance(
            self.bot_config, self.log_manager, self.exchange, self.ccxt_exceptions, self.alerts)
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
            print("handle_order_fill started")
            symbol = websocket_msg['product_id'].replace('-', '/')
            print(f"Symbol: {symbol}")

            order_data = {
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
            # Fetch order book and get adjusted price and size
            order_book = await self.order_book_manager.get_order_book(order_data)
            adjusted_price, adjusted_size = self.utility.adjust_price_and_size(order_data, order_book)

            # Place trailing stop order

            print("Placing trailing stop order")
            response = await self.order_type_manager.place_trailing_stop_order(order_data, order_book,
                                                                               order_data['base_price'])
            print(f"Trailing stop order placed, response: {response}")

            # Start monitoring and updating the trailing stop order
            initial_trailing_price = order_data['base_price']
            trailing_percentage = self.bot_config.trailing_percentage
            print("Starting monitor_and_update_trailing_stop")
            asyncio.create_task(
                self.monitor_and_update_trailing_stop(order_data['trading_pair'], initial_trailing_price,
                                                      trailing_percentage))
        except Exception as e:
            print(f"Error in _process_order_fill: {e}")

    async def monitor_and_update_trailing_stop(self, symbol, initial_trailing_price, trailing_percentage):
        """
        Continuously monitor the market and update the trailing stop order if necessary.
        """
        try:
            while True:
                current_price = await self.utility.fetch_spot(symbol)
                print(f"Checking Current price for {symbol}: {current_price}")
                if current_price > initial_trailing_price:
                    new_trailing_price = Decimal(current_price) * (1 - trailing_percentage / 100)
                    adjusted_trailing_price = self.utility.float_to_decimal(new_trailing_price, 2)

                    payload = {
                        "product_id": symbol.replace('/', '-'),
                        "side": "SELL",
                        "stop_price": str(adjusted_trailing_price),
                    }

                    response = await self.coinbase_api.update_order(payload)
                    if response:
                        print(f"Trailing stop updated to: {adjusted_trailing_price}")
                        initial_trailing_price = new_trailing_price

                await asyncio.sleep(15)
        except Exception as e:
            print(f"Error monitoring and updating trailing stop: {e}")

    async def handle_webhook(self, request: web.Request) -> web.Response:
        try:
            ip_address = request.remote
            request_json = await request.json()
            response = await self.process_webhook(request_json, ip_address)
            return response
        except Exception as e:
            self.log_manager.webhook_logger.error(f"Unhandled exception in handle_webhook: {str(e)}", exc_info=True)
            return web.json_response({"success": False, "message": "Internal server error"}, status=500)

    async def process_webhook(self, request_json, ip_address) -> web.Response:
        try:
            current_time = datetime.now()
            formatted_time = current_time.strftime("%Y-%m-%d %H:%M:%S")
            if not request_json.get('action'):
                return web.json_response({"success": False, "message": "Missing 'action' in request"}, status=400)
            else:
                whitelist = (self.bot_config.tv_whitelist + ',' +
                             self.bot_config.coin_whitelist + ',' +
                             self.bot_config.docker_staticip + ',' +
                             self.bot_config.pagekite_whitelist).split(',')

                if ip_address not in whitelist:
                    self.log_manager.webhook_logger.error(f'webhook: {ip_address} is not whitelisted')
                    return web.json_response({"success": False, "message": "Unauthorized"}, status=401)

                if 'SIGHOOK' not in request_json.get('origin', '') and 'TradingView' not in request_json.get('origin', ''):
                    return web.json_response({"success": False, "message": "Invalid content type"}, status=415)
                if 'SIGHOOK' in request_json.get('origin', ''):
                    pass
                trade_data = self.webhook_manager.parse_webhook_data(request_json)
                precision_data = await self.utility.fetch_precision(trade_data['trading_pair'])
                if not all(precision_data):
                    self.log_manager.webhook_logger.error(
                        f'webhook: Failed to fetch precision data for {trade_data["trading_pair"]}')
                    return web.json_response({"success": False, "message": 'Failed to fetch precision data'}, status=500)

                base_deci, quote_deci, base_increment, quote_increment = precision_data
                base_incri = self.utility.float_to_decimal(base_increment, base_deci) if base_deci else base_increment
                quote_incri = self.utility.float_to_decimal(quote_increment, quote_deci) if quote_deci else quote_increment
                quote_price = await self.utility.fetch_spot(trade_data["quote_currency"] + '-USD') \
                    if trade_data["quote_currency"] != 'USD' else 1.00

                quote_price = self.utility.float_to_decimal(quote_price, quote_deci)
                base_price = await self.utility.fetch_spot(trade_data["base_currency"] + '-USD')
                if base_price:
                    base_price = self.utility.float_to_decimal(base_price, quote_deci)
                    if trade_data["side"] == 'buy':
                        base_price /= quote_price

                base_order_size, quote_amount = self.webhook_manager.calculate_order_size(trade_data["side"],
                                                                                          trade_data["quote_amount"],
                                                                                          quote_price, base_price, base_deci)
                if trade_data["side"] == 'buy' and (
                        base_order_size is None or base_order_size == 0.0 or quote_amount is None or quote_amount == 0.0):
                    return web.json_response({"success": False, "message": "Invalid order size"}, status=400)
                if 'open' in trade_data["action"] or 'close' in trade_data["action"]:
                    print(f'{trade_data["orig"]} {trade_data["side"]} signal generated for {trade_data["trading_pair"]} '
                          f'at {trade_data["time"]}')
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

                await self.webhook_manager.handle_action(order_data, precision_data)
                return web.json_response({"success": True}, status=200)
        except Exception as e:
            self.log_manager.webhook_logger.error(f'Error processing webhook: {e}', exc_info=True)
            return web.json_response({"success": False, "Error": "Internal Server Error"}, status=500)
        finally:
            print("handle_webhook: Finally block")
            self.log_manager.webhook_logger.debug('webhook: End of webhook request')

    async def close_resources(self):
        # No need to close the ccxt exchange instance
        if self.session:
            await self.session.close()
        print("Resources closed.")

    async def create_app(self):
        app = web.Application()
        app.router.add_post('/webhook', self.handle_webhook)
        return app


async def run_app(config):
    listener = WebhookListener(config)
    app = await listener.create_app()

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
