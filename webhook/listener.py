# Define the WebhookListener class
"""
    The webhooklistener is designed to be run on a Digital Ocean droplet. The program uses flask to listen for incoming
     webhook requests and ccxt for api(Coinbase Cloud) coinbase  work. the program utilizes docker to run the program in
     containers"""

import time

from datetime import datetime
from decimal import ROUND_HALF_UP

import pandas as pd

import os

import ccxt as ccxt

from flask import Flask, request, jsonify

from config_manager import BotConfig

from log_manager import LoggerManager

from alert_system import AlertSystem

from accessories import AccessoryTools

from validate_orders import ValidateOrders

from webhook_utils import TradeBotUtils

from webhook_order_book import OrderBookManager

from webhook_order_manager import TradeOrderManager

from custom_exceptions import (ApiExceptions, UnauthorizedError, InsufficientFundsException,
                               SizeTooSmallException, ProductIDException, InternalServerErrorException,
                               MaintenanceException, RateLimitException, BadRequestException, NotFoundException,
                               UnknownException, CustomExceptions)


class WebhookListener:

    def __init__(self, host, port, botconfig):  # err_log, flask_err_log,
        """ Initialize the WebhookListener class. Configure the flask app and initialize the LoggerManager."""
        # self.log_dir = err_log

        # self.flask_log_dir = flask_err_log
        self.app = Flask(__name__)
        self.host = host
        self.port = port
        self.configure_routes()
        self.bot_config = botconfig  # This will  return the singleton instance
        self.trade_order_manager, self.order_book_manager, self.utility, self.log_manager = None, None, None, None
        self.alerts, self.ccxt_exceptions, self.api401, self.custom_excep = None, None, None, None
        self.exchange_class, self.exchange, self.accessory_tools, self.validate = None, None, None, None
        self.log_dir = bot_config.log_dir
        self.setup_exchange()
        self.load_bot_components()

    def setup_exchange(self):
        self.exchange_class = getattr(ccxt, 'coinbase')
        self.exchange = self.exchange_class({
            'apiKey': self.bot_config.api_key,
            'secret': self.bot_config.api_secret,
            'enableRateLimit': True,
            'verbose': False
        })

    def load_bot_components(self):
        self.bot_config = BotConfig()
        self.log_manager = LoggerManager(log_dir=self.log_dir)
        self.accessory_tools = AccessoryTools(self.log_manager)
        self.alerts = AlertSystem(self.log_manager)
        self.ccxt_exceptions = ApiExceptions(self.log_manager, self.alerts)
        self.api401 = UnauthorizedError(self.log_manager, self.alerts)
        self.custom_excep = CustomExceptions(self.log_manager, self.alerts)

        self.utility = TradeBotUtils(self.bot_config, self.log_manager, self.exchange, self.ccxt_exceptions,
                                     self.order_book_manager)
        self.validate = ValidateOrders(self.log_manager, self.utility)
        self.order_book_manager = OrderBookManager(self.exchange, self.utility, self.log_manager, self.ccxt_exceptions)
        self.trade_order_manager = TradeOrderManager(self.exchange, self.utility, self.validate, self.log_manager,
                                                     self.alerts, self.ccxt_exceptions, self.order_book_manager)

    def configure_routes(self):
        #  self.log_manager.flask_logger.info(f'configure_routes: webhook route configured')

        @self.app.route('/webhook', methods=['POST'])
        def webhook():
            """Respond to webhook requests from TradingView. Check for whitelist compatability, compile webhook
            signal into a trade order to be placed on Coinbase Pro, and handle errors with the webhook request."""

            try:
                if request.method == 'POST':
                    tv_whitelist = self.bot_config.tv_whitelist
                    coin_whitelist = self.bot_config.coin_whitelist
                    docker_staticip = self.bot_config.docker_staticip
                    whitelist = tv_whitelist + ',' + coin_whitelist + ',' + docker_staticip
                    whitelist = whitelist.split(',')
                    content_type = request.headers.get('Content-Type')
                    ip_address = request.headers.get('X-Forwarded-For', request.remote_addr)
                    if ip_address not in whitelist:
                        self.log_manager.webhook_logger.error(f'webhook: {ip_address} is not whitelisted')
                        return "Access denied ip not whitelisted", 403

                    if not content_type.startswith('application/json'):
                        if 'text' in str(content_type):
                            self.log_manager.webhook_logger.error(f'webhook: Invalid content type {content_type} check '
                                                                  f'Tradingview Alert settings')
                        return jsonify(success=False, message="Invalid content type"), 415

                    if not request.is_json:
                        self.log_manager.webhook_logger.error(f'webhook: Missing JSON in request')
                        print(f'respond to web_hook, 400 invalid Missing JSON in request')
                        return jsonify(success=False, message="Missing JSON in request"), 400

                    action, side, trading_pair, quote_currency, base_currency, usd_amount, orig = self.parse_webhook_data(
                        request.get_json())
                    base_decimal, quote_decimal, base_increment, quote_increment = self.utility.fetch_precision(trading_pair)
                    base_increment = self.utility.float_to_decimal(base_increment, base_decimal)
                    quote_increment = self.utility.float_to_decimal(quote_increment, quote_decimal)
                    balances = {}
                    self.validate.set_trade_parameters(trading_pair, base_currency, quote_currency, base_decimal,
                                                       quote_decimal, base_increment, quote_increment, balances)
                    self.utility.set_trade_parameters(trading_pair, base_currency, quote_currency, base_decimal,
                                                      quote_decimal, base_increment, quote_increment, balances)
                    self.trade_order_manager.set_trade_parameters(trading_pair, base_currency, quote_currency, base_decimal,
                                                                  quote_decimal, base_increment, quote_increment, balances)
                    self.order_book_manager.set_trade_parameters(trading_pair, base_currency, quote_currency, base_decimal,
                                                                 quote_decimal, base_increment, quote_increment, balances)

                    self.log_manager.webhook_logger.info(f'webhook: {orig} signal generated for {trading_pair}')

                    # get quote price and base price
                    if quote_currency != 'USD':
                        quote_price = self.utility.fetch_spot(quote_currency + '-USD')
                        quote_price = self.utility.float_to_decimal(quote_price, quote_decimal)
                    else:
                        quote_price = self.utility.float_to_decimal(1.00, quote_decimal)

                    base_price = self.utility.fetch_spot(base_currency + '-USD')
                    base_price = self.utility.float_to_decimal(base_price, quote_decimal)
                    if side == 'buy':
                        base_price = base_price/quote_price
                    base_order_size, quote_amount = self.calculate_order_size(side, usd_amount, quote_price, base_price,
                                                                              base_decimal, quote_decimal)

                    # Process the webhook request check for bad buy order
                    if side == 'buy':
                        if base_order_size is None or base_order_size == 0.0 or quote_amount is None or quote_amount == 0.0:
                            if base_order_size is None:
                                base_order_size = 0.0
                                self.log_manager.webhook_logger.info(f'webhook: {side} order is not valid. '
                                                                     f'{trading_pair}  order size is {base_order_size} ')
                            if quote_amount is None:
                                quote_amount = 0.0
                                self.log_manager.webhook_logger.info(f'webhook: {side} order is not valid. '
                                                                     f'{trading_pair}  balance is {quote_amount} ')
                                return jsonify(success=False, message="Invalid order size!"), 400
                            else:
                                quote_amount = self.utility.adjust_precision(quote_amount, convert='quote')

                    try:
                        current_time = datetime.now()
                        formatted_time = current_time.strftime("%Y-%m-%d %H:%M:%S")

                        self.log_manager.webhook_logger.debug(f'webhook: payload: {request.get_json()}')
                        if 'open' in action or 'close' in action:
                            #  place order with handle_action
                            print(F'<><><><><><><><><  {orig}  ><><><><> {side} signal generated for {trading_pair} at '
                                  F'{formatted_time} ')
                            self.handle_action(side, trading_pair, formatted_time, quote_price, quote_amount,
                                               base_order_size, usd_amount, base_price)
                        else:
                            print(f'respond to web_hook, 400 invalid {action}')
                            self.log_manager.webhook_logger.error(f'webhook: Invalid action {action}.')
                            return jsonify(success=False, message="Invalid action!"), 400
                    except Exception as inner_e:
                        current_time = datetime.now()
                        formatted_time = current_time.strftime("%Y-%m-%d %H:%M:%S")
                        self.handle_webhook_error(inner_e, side, trading_pair, formatted_time, quote_price, quote_amount,
                                                  base_order_size, usd_amount, base_price)
                        return "Internal Server Error", 500
                current_time = datetime.now()
                formatted_time = current_time.strftime("%Y-%m-%d %H:%M:%S")
                print(f'<><><><><><><><><><><><><><><><><><><><>< {formatted_time} ><><><><><><><><><><><><><><><><><><><><>')

                print(f'Webhook {self.bot_config.program_version} is Listening...')
                print(f'Flask Server data:')
                return jsonify(success=True), 200
            except ValueError as e:
                # Handle the specific case where the symbol is not found
                if "not found in exchange markets" in str(e):
                    self.log_manager.webhook_logger.error(f'webhook: {e}')
                    return jsonify(success=False, message=str(e)), 400
                elif "Failed to fetch markets" in str(e):
                    self.log_manager.webhook_logger.error(f'webhook: check ip address is whitelisted.  {e}')
                    self.alerts.callhome('Not connecting to Coinbase', f'check ip address is whitelisted  {e}')
                    return jsonify(success=False, message=str(e)), 401
                else:
                    print(f"Debug: Other ValueError: {e}")
                    # Handle other ValueErrors or re-raise
            except Exception as outer_e:
                if 'origin' in outer_e:
                    self.log_manager.webhook_logger.error(f'webhook: origin not found in payload {request.get_json()}')
                    return jsonify(success=False, message="Invalid content type"), 415
                else:
                    self.log_manager.webhook_logger.error(f'webhook: An error occurred: {outer_e}')
                    return "Internal Server Error", 500



    @LoggerManager.log_method_call
    def calculate_order_size(self, side, usd_amount, quote_price, base_price, base_decimal, quote_decimal):
        # Convert USD to BTC
        quote_amount = None
        # Convert BTC(quote currency) to Base Currency (e.g., ETH)
        if side == 'buy':
            quote_amount = usd_amount / quote_price  # 100/37600
            base_order_size = quote_amount / base_price
            formatted_decimal = self.utility.get_decimal_format(base_decimal)
            base_order_size = base_order_size.quantize(formatted_decimal, rounding=ROUND_HALF_UP)
        else:
            base_order_size = None
        return base_order_size, quote_amount

    def parse_webhook_data(self, webhook_data):
        order_size = None
        action = webhook_data['action']  # Extract order type (open or close)
        side = 'buy' if 'open' in action else 'sell'
        quote_currency = webhook_data['pair'][-3:]  # Extract quote currency
        base_currency = webhook_data['pair'][:-3]  # Extract base currency
        pair = webhook_data['pair'][:-3] + '/' + webhook_data['pair'][-3:]
        orig = webhook_data['origin']
        if side == 'buy':
            usd_amount = webhook_data['order_size']  # Extract order size
            if usd_amount is not None:
                usd_amount = self.utility.float_to_decimal(usd_amount, 2)  # dollar amount from tradingview strategy $100.00
                self.log_manager.webhook_logger.debug(f'webhook: buy_size: {usd_amount}')
        else:
            usd_amount = None
        return action, side, pair, quote_currency, base_currency, usd_amount, orig

    def handle_action(self, side, trading_pair, formatted_time, quote_price, quote_amount, base_order_size, usd_amount,
                      base_price):
        """ Handle the action from the webhook request. Place an order on Coinbase Pro."""
        try:
            self.trade_order_manager.place_order(quote_price, quote_amount, base_price, side=side, usd_amount=usd_amount)
        except InsufficientFundsException:
            self.log_manager.webhook_logger.info(f'handle_action: Insufficient funds')
            self.alerts.callhome('Insufficient funds', f'Insufficient funds  {trading_pair} at {formatted_time}')
        except ProductIDException:
            self.log_manager.webhook_logger.info(f'handle_action: product id exception')
            self.alerts.callhome('product id exception', f'product id  exception  {trading_pair} at {formatted_time}')
        except SizeTooSmallException:
            print('Order too small')
            # Handle this specific error differently
        except MaintenanceException:
            print('MaintenanceException')
            # Maybe implement a retry logic
        except Exception as e:
            # Catch-all for other exceptions
            self.log_manager.webhook_logger.error(f'Handle_action: An unexpected error occurred: {e}')

    def handle_webhook_error(self, e, side, trading_pair, formatted_time, quote_price, quote_amount,
                             base_order_size, usd_amount, base_price):
        """Handle errors that occur while processing a webhook request."""
        exception_map = {
            429: RateLimitException,
            400: BadRequestException,
            404: NotFoundException,
            500: InternalServerErrorException,
        }
        extra_error_details = {
            'action': side,
            'trading_pair': trading_pair,
            'buy_size': base_order_size,
            'formatted_time': formatted_time,
        }
        # Map status_code to custom exceptions
        exception_to_raise = exception_map.get(getattr(e, 'status_code', None), UnknownException)

        # Raise the exception and handle it in the except block
        try:
            raise exception_to_raise(
                f"An error occurred with status code: {getattr(e, 'status_code', 'unknown')}, error: {e}",
                extra_error_details)
        except RateLimitException:
            self.log_manager.webhook_logger.error(f'warning', 'handle_webhook_error: Rate limit hit. '
                                                  'Retrying in 60 seconds...')
            time.sleep(60)
            self.handle_action(side, trading_pair, formatted_time, quote_price, quote_amount, base_order_size, usd_amount,
                               base_price)
        except (BadRequestException, NotFoundException, InternalServerErrorException, UnknownException) as ex:
            self.log_manager.webhook_logger.error(f'handle_webhook_error: {ex}. Additional info: {ex.errors}')

        except Exception as ex:
            self.log_manager.webhook_logger.error(f'handle_webhook_error: An unhandled exception occurred: {ex}. '
                                                  f'Additional info: {getattr(ex, "errors", "N/A")}')

    def run(self):
        print(f'Webhook {self.bot_config.program_version} is Listening on port:{self.port}...')
        self.app.run(host=self.host, port=self.port, use_reloader=False, debug=False)


if __name__ == "__main__":

    bot_config = BotConfig()  # Create an instance of BotConfig
    # Load the environment variables from the .env_sighook file
    # bot_config.get_directory_paths()

    # Set display options for pandas
    pd.set_option('display.max_columns', None)
    pd.set_option('display.width', None)
    pd.set_option('display.max_colwidth', None)
    pd.set_option('display.colheader_justify', 'center')

    listener = WebhookListener('0.0.0.0', bot_config.port, bot_config)
    listener.run()
