
import asyncio
import ccxt.async_support as ccxt  # import ccxt as ccxt
import pandas as pd
import os
from aiohttp import web
from datetime import datetime
from log_manager import LoggerManager
from config_manager import BotConfig
from alert_system import AlertSystem
from custom_exceptions import ApiExceptions
from webhook_utils import TradeBotUtils
from webhook_validate_orders import ValidateOrders
from webhook_order_book import OrderBookManager
# from test import TestOrderManager
from webhook_order_manager import TradeOrderManager
from webhook_manager import WebHookManager


class WebhookListener:
    def __init__(self, config):
        self.semaphore = asyncio.Semaphore(15)  # Control access to the session
        self.bot_config = config
        self.api_key = self.bot_config.api_key
        self.secret = self.bot_config.api_secret
        self.base_url = self.bot_config.api_url
        self.trade_order_manager, self.order_book_manager, self.utility, self.webhook_manager = None, None, None, None
        self.alerts, self.ccxt_exceptions, self.api401, self.custom_excep = None, None, None, None
        self.exchange_class, self.exchange, self.accessory_tools, self.validate = None, None, None, None
        self.log_manager, self.test_order_manager = None, None

    async def setup(self):
        # Set up the exchange initiate the exchange session.
        self.exchange = ccxt.coinbase({
            'apiKey': self.api_key,
            'secret': self.secret,
            'enableRateLimit': True,
            'verbose': False
        })

        # Asynchronously load components like databases, logging, etc.
        self.log_manager = LoggerManager(self.bot_config, log_dir=self.bot_config.log_dir)
        self.alerts = AlertSystem(self.log_manager)
        self.ccxt_exceptions = ApiExceptions(self.exchange, self.log_manager, self.alerts, self.semaphore)
        self.utility = TradeBotUtils(self.bot_config, self.log_manager, self.exchange, self.ccxt_exceptions, self.alerts)
        self.validate = ValidateOrders(self.log_manager, self.utility, self.bot_config)
        self.order_book_manager = OrderBookManager(self.exchange, self.utility, self.log_manager, self.ccxt_exceptions)
        self.trade_order_manager = TradeOrderManager(self.bot_config, self.exchange, self.utility, self.validate,
                                                     self.log_manager, self.alerts, self.ccxt_exceptions,
                                                     self.order_book_manager)

        # self.test_order_manager = TestOrderManager(self.bot_config, self.exchange, self.utility, self.validate,
        #                                            self.log_manager, self.alerts, self.ccxt_exceptions,
        #                                            self.order_book_manager)

        self.webhook_manager = WebHookManager(self.log_manager, self.utility, self.trade_order_manager, self.alerts)

    async def handle_webhook(self, request: web.Request) -> web.Response:
        """Respond to webhook requests from TradingView. Check for whitelist compatability, compile webhook
                signal into a trade order to be placed on Coinbase Pro, and handle errors with the webhook request."""
        async with self.semaphore:  # Centralized semaphore management
            try:
                ip_address = request.remote
                request_json = await request.json()
                print(f'request_json: {request_json}')
                response = await self.process_webhook(request_json, ip_address)
                return response
            except Exception as e:
                # General error handling, should log the error and return a 500 server error response
                self.log_manager.webhook_logger.error(f"Unhandled exception in handle_webhook: {str(e)}", exc_info=True)
                return web.json_response({"success": False, "message": "Internal server error"}, status=500)

    async def process_webhook(self, request_json, ip_address) -> web.Response:
        """Processes the webhook JSON data."""
        try:

            self.log_manager.webhook_logger.debug(f'Incoming webhook from IP: {ip_address} {request_json}')
            current_time = datetime.now()
            formatted_time = current_time.strftime("%Y-%m-%d %H:%M:%S")
            if not request_json.get('action'):
                return web.json_response({"success": False, "message": "Missing 'action' in request"}, status=400)
            else:

                #  Check whitelist
                tv_whitelist = self.bot_config.tv_whitelist
                coin_whitelist = self.bot_config.coin_whitelist
                pagekite_whitelist = self.bot_config.pagekite_whitelist
                docker_staticip = self.bot_config.docker_staticip
                whitelist = tv_whitelist + ',' + coin_whitelist + ',' + docker_staticip + ',' + pagekite_whitelist
                whitelist = whitelist.split(',')
                content = request_json
                origin = content.get('origin', None)
                if ip_address not in whitelist:
                    self.log_manager.webhook_logger.error(f'webhook: {ip_address} is not whitelisted')
                    return web.json_response({"success": False, "message": "Unauthorized"}, status=401)

                if 'SIGHOOK' not in origin and 'TradingView' not in origin:
                    if 'text' in str(request_json):
                        self.log_manager.webhook_logger.error(f'webhook: Invalid content type {request_json} check '
                                                              f'Tradingview Alert settings')
                    return web.json_response({"success": False, "message": "Invalid content type"}, status=415)

                if not request_json:
                    self.log_manager.webhook_logger.error(f'webhook: Missing JSON in request')
                    return web.json_response({"success": False, "message": "Missing JSON in request"}, status=400)

                trade_data = (self.webhook_manager.parse_webhook_data(request_json))
                precision_data = await self.utility.fetch_precision(trade_data['trading_pair'])
                if not any(value is None for value in precision_data):
                    base_deci, quote_deci, base_increment, quote_increment = precision_data
                else:
                    self.log_manager.webhook_logger.error(f'webhook: Failed to fetch precision data for '
                                                          f'{trade_data["trading_pair"]}, {precision_data}')
                    return web.json_response({"success": False, "message": 'Failed to fetch precision data'}, status=500)
                if base_deci and base_increment:
                    base_incri = self.utility.float_to_decimal(base_increment, base_deci)
                else:
                    base_incri = base_increment
                if quote_deci and quote_increment:
                    quote_incri = self.utility.float_to_decimal(quote_increment, quote_deci)
                else:
                    quote_incri = quote_increment

                balances = {}
                order_data = {}  # dictionary of data for placing orders

                self.log_manager.webhook_logger.debug(f'webhook: {trade_data["orig"]}  {trade_data["side"]}  signal '
                                                      f'generated for {trade_data["trading_pair"]}', exc_info=True)
                # get quote price and base price
                if trade_data["quote_currency"] != 'USD':
                    quote_price = await self.utility.fetch_spot(trade_data["quote_currency"] + '-USD')

                    quote_price = self.utility.float_to_decimal(quote_price, quote_deci)
                else:
                    quote_price = self.utility.float_to_decimal(1.00, quote_deci)

                base_price = await self.utility.fetch_spot(trade_data["base_currency"] + '-USD')

                if base_price:
                    base_price = self.utility.float_to_decimal(base_price, quote_deci)
                if trade_data["side"] == 'buy':
                    base_price = base_price / quote_price
                base_order_size, quote_amount = self.webhook_manager.calculate_order_size(trade_data["side"], trade_data[
                    "quote_amount"], quote_price, base_price, base_deci)
                # Process the webhook request check for bad buy order
                if trade_data["side"] == 'buy':
                    if base_order_size is None or base_order_size == 0.0 or quote_amount is None or quote_amount == 0.0:
                        if base_order_size is None:
                            base_order_size = 0.0
                            self.log_manager.webhook_logger.info(f'webhook: {trade_data["side"]} order is not valid. '
                                                                 f'{trade_data["trading_pair"]}  order size is'
                                                                 f' {base_order_size} ')
                        if quote_amount is None:
                            quote_amount = 0.0
                            self.log_manager.webhook_logger.info(f'webhook: {trade_data["side"]} order is not valid. '
                                                                 f'{trade_data["trading_pair"]}  balance is {quote_amount} ')
                            return web.json_response({"success": False, "message": "Invalid order size"}, status=400)
                        else:
                            quote_amount = self.utility.adjust_precision(base_deci, quote_deci, quote_amount,
                                                                         convert='quote')
                else:
                    quote_amount = 0.0

                try:
                    current_time = datetime.now()
                    formatted_time = current_time.strftime("%Y-%m-%d %H:%M:%S")
                    self.log_manager.webhook_logger.debug(f'webhook: payload: {request_json}')
                    if 'open' in trade_data["action"] or 'close' in trade_data["action"]:
                        #  place order with handle_action
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
                            'formatted_time': formatted_time,
                            'quote_price': quote_price,
                            'quote_amount': quote_amount,
                            'base_price': base_price
                        }
                    else:
                        print(f'respond to web_hook, 400 invalid {trade_data["action"]}')
                        self.log_manager.webhook_logger.error(f'webhook: Invalid action {trade_data["action"]}.')
                        return web.json_response({"success": False, "message": "Invalid action"}, status=400)
                except Exception as inner_e:
                    self.log_manager.webhook_logger.error(
                        f"handle_webhook: Exception caught in main try block - {inner_e}")  # Debug Statement
                    current_time = datetime.now()
                    formatted_time = current_time.strftime("%Y-%m-%d %H:%M:%S")
                    order_data = (trade_data['side'], trade_data['balances'], base_incri, base_deci, quote_deci,
                                  trade_data['base_currency'], trade_data['quote_currency'], trade_data['trading_pair'],
                                  formatted_time, quote_price, quote_amount, trade_data['quote_amount'], base_price)
                    await self.webhook_manager.handle_webhook_error(inner_e, order_data)
                    return web.json_response({"Internal Server Error": True}, status=500)
                await self.webhook_manager.handle_action(order_data)

                # await self.exchange.close()

            return web.json_response({"success": True}, status=200)

        except ValueError as e:

            print(f"handle_webhook: ValueError caught - {e}")  # Debug Statement

            # Handle the specific case where the symbol is not found

            if "not found in exchange markets" in str(e) or "Failed to fetch markets" in str(e):
                current_time = datetime.now()
                formatted_time = current_time.strftime("%Y-%m-%d %H:%M:%S")
                self.alerts.callhome('Not connecting to Coinbase', f'Time:{formatted_time}'

                                                                   f'check IP is whitelisted  {e}', exc_info=True)

            return web.json_response({"success": False, "Error": "Check ip address is whitelisted"}, status=400)

        except Exception as outer_e:

            print(f"handle_webhook: Exception caught in outer try block - {outer_e}")  # Debug Statement

            self.log_manager.webhook_logger.error(f'Error processing webhook: {outer_e}', exc_info=True)

            return web.json_response({"success": False, "Error": "Internal Server Error"}, status=500)

        finally:
            # Log before closing to make sure all operations are accounted for.
            print("handle_webhook: Finally block")
            # Ensure all operations are complete or properly wrapped up
            if self.exchange:
                await self.exchange.close()
            self.log_manager.webhook_logger.debug('webhook: End of webhook request')
            print(f'Webhook {self.bot_config.program_version} is Listening...')

    async def close_resources(self):
        # Make sure to close any open resources, like database connections or network sessions
        if self.exchange:
            await self.exchange.close()
        print("Resources closed.")

    async def create_app(self):
        await self.setup()  # Make sure to await the setup
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
        await asyncio.Event().wait()
        while True:
            await asyncio.sleep(1)
    except Exception as e:
        print(f"run_app: Exception caught - {e}")
        await listener.close_resources()


if __name__ == '__main__':
    os.environ['PYTHONASYNCIODEBUG'] = '1'  # Enable asyncio debug mode
    pd.set_option('display.max_columns', None)
    pd.set_option('display.width', None)
    pd.set_option('display.max_colwidth', None)
    pd.set_option('display.colheader_justify', 'center')
    bot_config = BotConfig()  # Load or define your bot configuration
    print(f'Webhook {bot_config.program_version} is Listening on port {bot_config.port}...')
    print(f'Coins actively held: {bot_config.hodl}')
    asyncio.run(run_app(bot_config))
