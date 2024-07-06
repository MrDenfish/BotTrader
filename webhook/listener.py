import ccxt
import asyncio
import pandas as pd
import os
import logging
from aiohttp import web
from datetime import datetime
from log_manager import LoggerManager
from config_manager import BotConfig
from alert_system import AlertSystem
from custom_exceptions import ApiExceptions
from webhook_utils import TradeBotUtils
from webhook_validate_orders import ValidateOrders
from webhook_order_book import OrderBookManager
from webhook_order_manager import TradeOrderManager
from webhook_order_types import OrderTypeManager
from webhook_manager import WebHookManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
tasks = set()


class WebhookListener:
    def __init__(self, config):
        self.bot_config = config
        self.cb_api = self.bot_config.load_webhook_api_key()
        self.base_url = self.bot_config.api_url
        self.trade_order_manager, self.order_book_manager, self.utility, self.webhook_manager = None, None, None, None
        self.alerts, self.ccxt_exceptions, self.api401, self.custom_excep = None, None, None, None
        self.exchange, self.accessory_tools, self.validate = None, None, None
        self.order_type_manager, self.log_manager = None, None
        self.setup_exchange()

    def setup_exchange(self):
        self.exchange = ccxt.coinbase({
            'apiKey': self.cb_api.get('name'),
            'secret': self.cb_api.get('privateKey'),
            'enableRateLimit': True,
            'verbose': False
        })

        self.log_manager = LoggerManager(self.bot_config, log_dir=self.bot_config.log_dir)
        self.alerts = AlertSystem(self.log_manager)
        self.ccxt_exceptions = ApiExceptions(self.exchange, self.log_manager, self.alerts)
        # Ensure TradeBotUtils is instantiated as a singleton
        self.utility = TradeBotUtils.get_instance(
            self.bot_config, self.log_manager, self.exchange, self.ccxt_exceptions, self.alerts
        )
        self.validate = ValidateOrders(self.log_manager, self.utility, self.bot_config)
        self.order_book_manager = OrderBookManager(self.exchange, self.utility, self.log_manager, self.ccxt_exceptions)
        self.order_type_manager = OrderTypeManager(self.bot_config, self.exchange, self.utility, self.validate,
                                                   self.log_manager, self.alerts, self.ccxt_exceptions,
                                                   self.order_book_manager)
        self.trade_order_manager = TradeOrderManager(self.bot_config, self.exchange, self.utility, self.validate,
                                                     self.log_manager, self.alerts, self.ccxt_exceptions,
                                                     self.order_book_manager, self.order_type_manager)
        self.webhook_manager = WebHookManager(self.log_manager, self.utility, self.trade_order_manager, self.alerts)

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
                    print(F'<><><><><><><><><  {trade_data["orig"]}  ><><><><> {trade_data["side"]} signal generated '
                          F'for {trade_data["trading_pair"]} at {trade_data["time"]} ')
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
                await self.webhook_manager.handle_action(order_data)
                return web.json_response({"success": True}, status=200)

        except Exception as e:
            self.log_manager.webhook_logger.error(f'Error processing webhook: {e}', exc_info=True)
            return web.json_response({"success": False, "Error": "Internal Server Error"}, status=500)
        finally:
            print("handle_webhook: Finally block")
            print(f"Tracking {len(tasks)} tasks")
            # if self.exchange:
            #     await self.exchange.close()
            self.log_manager.webhook_logger.debug('webhook: End of webhook request')
            print(f'Webhook {bot_config.program_version} is Listening on port {bot_config.port}...')

    async def close_resources(self):
        if self.exchange:
            await self.exchange.close()
        print("Resources closed.")

    async def create_app(self):
        self.setup_exchange()
        app = web.Application()
        app.router.add_post('/webhook', self.handle_webhook)
        return app


async def run_app(config):
    listener = WebhookListener(config)
    try:
        app = await listener.create_app()
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', config.port)
        await site.start()
        print(f'Webhook {config.program_version} is Listening on port {config.port}...')
        print(f'Coins actively held: {config.hodl}')
        while True:
            await asyncio.sleep(10)
            logger.debug(f"Tracking {len(tasks)} tasks")
    except Exception as e:
        print(f"run_app: Exception caught - {e}")
        await listener.close_resources()

if __name__ == '__main__':
    os.environ['PYTHONASYNCIODEBUG'] = '0'
    pd.set_option('display.max_columns', None)
    pd.set_option('display.width', None)
    pd.set_option('display.max_colwidth', None)
    pd.set_option('display.colheader_justify', 'center')
    bot_config = BotConfig()
    asyncio.run(run_app(bot_config))
