from custom_exceptions import CoinbaseAPIError
from decimal import Decimal, getcontext
import pandas as pd
import time
import requests
import json
import traceback

# Define the TradeOrderManager class
"""This class  will manage the trade orders."""


class TradeOrderManager:
    _instance_count = 0
    _instance = None

    @classmethod
    def get_instance(cls, config, exchange_client, utility, validate, logmanager, alerts, ccxt_api, order_book,
                     order_types, session):
        if cls._instance is None:
            cls._instance = cls(config, exchange_client, utility, validate, logmanager, alerts, ccxt_api, order_book,
                                order_types, session)
        return cls._instance

    def __init__(self, config, coinbase_api, exchange_client, utility, validate, logmanager, alerts, ccxt_api, order_book,
                 order_types, session):
        self.bot_config = config
        self.coinbase_api = coinbase_api
        self._take_profit = Decimal(config.take_profit)
        self._stop_loss = Decimal(config.stop_loss)
        self._min_sell_value = Decimal(config.min_sell_value)
        self._hodl = config.hodl
        self.exchange = exchange_client
        self.base_url = config.api_url
        self.log_manager = logmanager
        self.validate = validate
        self.order_types = order_types
        self.order_book = order_book
        self.ccxt_exceptions = ccxt_api
        self.alerts = alerts
        self.utils = utility
        self.session = session

        # Initialize the REST client using credentials from the config
        # self.client = RESTClient(key_file=config.cdp_api_key_path, verbose=True)

    @property
    def hodl(self):
        return self._hodl

    @property
    def stop_loss(self):
        return self._stop_loss

    @property
    def take_profit(self):
        return self._take_profit

    @property
    def min_sell_value(self):
        return self._min_sell_value


    async def place_order(self, order_data, precision_data):
        try:
            quote_bal, base_balance, all_open_orders, _ = await self.utils.get_open_orders(order_data)
            open_orders = all_open_orders if isinstance(all_open_orders, pd.DataFrame) else pd.DataFrame()

            if not self.validate_order_conditions(order_data, quote_bal, base_balance, open_orders):
                return False

            order_book_details = await self.order_book.get_order_book(order_data)
            validate_data = self.build_validate_data(order_data, quote_bal, base_balance, open_orders, order_book_details)

            base_coin_balance, valid_order, condition = self.validate.fetch_and_validate_rules(validate_data)
            if not valid_order:
                self.log_manager.webhook_logger.info(f"Validation failed for the order. - {condition}")
                return False

            return await self.handle_order(validate_data, order_book_details, precision_data)


        except Exception as ex:
            self.log_manager.webhook_logger.debug(ex, exc_info=True)
            return False

    def validate_order_conditions(self, order_data, quote_bal, base_balance, open_orders):
        try:
            side = order_data['side']
            quote_amount = order_data['quote_amount']
            symbol = order_data['trading_pair'].replace('/', '-')

            # Check if there's an active trailing stop order for the symbol
            try:
                trailing_stop_active = open_orders[
                    (open_orders['product_id'] == symbol) &
                    (open_orders['trigger_status'] == 'STOP_PENDING')
                ].any().any()
            except Exception as e:
                self.log_manager.webhook_logger.error(f"Error checking trailing stop orders: {e}", exc_info=True)
                return False

            if side == 'sell' and trailing_stop_active:
                self.log_manager.webhook_logger.info("Active trailing stop order found.")
                return True
            elif side == 'sell' and base_balance == 0:
                self.log_manager.webhook_logger.info(f"Insufficient base balance to sell. Available: {base_balance}")
                return False
            if side == 'buy' and quote_bal < quote_amount:
                self.log_manager.webhook_logger.info(
                    f"Insufficient quote balance to buy. Required: {quote_amount}, Available: {quote_bal}")
                return False
            return True
        except KeyError as ke:
            self.log_manager.webhook_logger.error(f"KeyError: Missing key in order_data or open_orders: {ke}", exc_info=True)
            return False
        except Exception as e:
            self.log_manager.webhook_logger.error(f"Unexpected error: {e}", exc_info=True)
            return False

    def build_validate_data(self, order_data, quote_bal, base_balance, open_orders, order_book_details):

        return {
            **order_data,
            'base_balance': base_balance,
            'quote_balance': quote_bal,
            'highest_bid': order_book_details['highest_bid'],
            'lowest_ask': order_book_details['lowest_ask'],
            'spread': order_book_details['spread'],
            'open_orders': open_orders
        }

    async def handle_order(self, validate_data, order_book_details, precision_data):
        try:
            highest_bid = Decimal(order_book_details['highest_bid'])
            lowest_ask = Decimal(order_book_details['lowest_ask'])
            spread = Decimal(order_book_details['spread'])
            base_deci, quote_deci, _, _ = precision_data
            adjusted_price, adjusted_size = self.utils.adjust_price_and_size(validate_data, order_book_details)
            self.log_manager.webhook_logger.debug(f"Adjusted price: {adjusted_price}, Adjusted size: {adjusted_size}")

            # Calculate take profit and stop loss prices
            if validate_data['side'] == 'buy':
                take_profit_price = adjusted_price * (1 + self.take_profit)
                adjusted_take_profit_price = self.utils.adjust_precision(base_deci, quote_deci, take_profit_price,
                                                                         convert='quote')
                stop_loss_price = adjusted_price * (1 + self.stop_loss)
            else:  # side == 'sell'
                take_profit_price = adjusted_price * (1 + self.take_profit)
                adjusted_take_profit_price = self.utils.adjust_precision(base_deci, quote_deci, take_profit_price,
                                                                         convert='quote')

                stop_loss_price = adjusted_price * (1 + self.stop_loss)

            order_data = {
                **validate_data,
                'adjusted_price': adjusted_price,
                'adjusted_size': adjusted_size,
                'trading_pair': validate_data['trading_pair'],
                'side': validate_data['side'],
                'stop_loss_price': stop_loss_price,
                'take_profit_price': take_profit_price,

            }

            # Decide whether to place a bracket order or a trailing stop order
            if self.should_use_trailing_stop(adjusted_price, highest_bid, lowest_ask):
                return await self.attempt_order_placement(validate_data, order_data, order_book_details,
                                                          order_type='trailing_stop')
            else:
                return await self.attempt_order_placement(validate_data, order_data, order_book_details,
                                                          order_type='bracket')
        except Exception as ex:
            self.log_manager.webhook_logger.debug(ex)
            return False
        except Exception as ex:
            self.log_manager.webhook_logger.debug(ex)
            return False

    def should_use_trailing_stop(self, adjusted_price, highest_bid, lowest_ask):
        # Initial thought for using a trailing stop order is when ROC trigger is met. Signal will come from  sighook.

        # Placeholder logic:
        return True # while developing
        # return adjusted_price > (highest_bid + lowest_ask) / 2

    async def log_order_attempt(self, attempt, trading_pair, side, order_size, order_price):
        """
        Log each order attempt. Placeholder for actual logging implementation.
        """
        self.log_manager.webhook_logger.info(
            f'Attempt {attempt}: Order {side} for {trading_pair} of size {order_size} at price {order_price} failed.'
        )


    async def attempt_order_placement(self, validate_data, order_data, order_book_details, order_type):
        currencies = []
        order_placed = False
        response = None
        try:
            if order_data['side'] == 'buy':
                response = await self.order_types.beta_place_limit_order(validate_data, order_data)

                if response is False:
                    order_placed = False
                elif response == 'amend':
                    order_placed = False
            elif order_type == 'bracket':
                try:
                    currencies = [order_data['trading_pair'].split('/')[0], order_data['trading_pair'].split('/')[1]]
                    accounts = await self.utils.get_account_balance(currencies, get_staked=False)
                    quote_balance, base_balance, _, open_order = await self.utils.get_open_orders(order_data)
                    if not open_order:
                        response = await self.order_types.beta_place_bracket_order(order_data, order_book_details)
                        order_placed = True
                        self.log_success(order_data['trading_pair'], order_data['adjusted_price'], order_data['side'])
                    else:
                        self.log_manager.webhook_logger.info(f'Order already exists for {order_data["trading_pair"]}')
                        order_placed = False    # Order already exists
                except Exception as ex_bracket:
                    self.log_manager.webhook_logger.error(f'Error placing limit order: {ex_bracket}', exc_info=True)
                    return ex_bracket
            elif order_type == 'trailing_stop':
                try:
                    response = await self.order_types.place_trailing_stop_order(order_data, order_book_details,
                                                                                order_data[
                                                                                'adjusted_price'])
                    if response:
                        self.log_success(order_data['trading_pair'], order_data['adjusted_price'], order_data['side'],
                                         response.text)
                        return True, response
                    else:
                        return False, response
                except Exception as ex_trailing:
                    self.log_manager.webhook_logger.error(f'Error placing trailing stop order: {ex_trailing}', exc_info=True)
                    return ex_trailing
            return order_placed, response

        except Exception as ex_limit:
            self.log_manager.webhook_logger.error(f'Error placing limit order: {ex_limit}', exc_info=True)
            return ex_limit

    async def staked_coins(self, trading_pair):
        currencies = []

        currencies = [trading_pair.split('/')[0], trading_pair.split('/')[1]]
        accounts = await self.utils.get_account_balance(currencies, get_staked=True)
        base_currency = currencies[0]
        hold_amount = 0
        for account in accounts['info']['data']:
            currency = account['currency']['code']  # Get the currency code
            if base_currency == account['currency']['code']:
                free_balance = Decimal(accounts.get(currency, {}).get('free', 'N/A'))  # Use 'N/A' as a default if not
                if 'Staked' in account['name']:
                    # found
                    hold = account['balance']['amount']  # Get the amount held
                    hold_amount = Decimal(hold)
                    available_to_sell = Decimal(free_balance - hold_amount)
                    if hold_amount > 1:
                        print(f"Currency: {currency}, Available to sell: {available_to_sell}, On Hold: {hold_amount}")
                    return available_to_sell, hold_amount
                elif base_currency not in self.hodl: # no coins on hold
                    available_to_sell = free_balance
                else:
                    available_to_sell = 0
                    hold_amount = free_balance  # handle hodl coins
        return available_to_sell, hold_amount

    def log_success(self, trading_pair, price, side):
        self.log_manager.webhook_logger.info(f'{side} order placed for {trading_pair} @ {price}')

    def handle_api_error(self, eapi, order_data):
        # Implement custom logic for handling different api(Coinbase Cloud) errors here
        # Example:
        if 'Post only mode' in str(eapi):
            self.log_manager.webhook_logger.info(f'handle_order: CoinbaseAPIError in handle_order: Post only mode. '
                                                 f'Order data: {eapi} order response:{order_data}')
            return 'retry'
        elif 'price is too accurate' in str(eapi):
            # Handle Price Too Accurate Error
            return 'abort'
        elif 'Insufficient funds' in str(eapi):
            # Handle Insufficient Funds Error
            return 'abort'
        # Add more cases as needed
        return 'unknown'

    def log_api_error(self, error_type, eapi, response):
        self.log_manager.webhook_logger.info(f'handle_order: CoinbaseAPIError in handle_order: '
                                             f'"{error_type}: {eapi} order response: {response}')

    def handle_coinbase_api_error(self, eapi, response):
        error_info = self.handle_api_error(eapi, response)
        if error_info == 'retry':
            return False
        elif error_info in ['abort', '503']:
            self.log_api_error(error_info, eapi, response)
            return False
        return False

    def is_order_feasible(self, side, quote_amount, available_coin_value, available_quote, trading_pair):
        hodl_coins = False
        coin = trading_pair.split('/')[0]
        if coin in self.hodl:
            hodl_coins = True
        if ((side == 'buy' and available_quote > quote_amount and available_coin_value < self.min_sell_value) or
            (side == 'buy' and hodl_coins) or
            (side == 'sell' and available_coin_value > self.min_sell_value and not hodl_coins)):
            return True
        return False

    @staticmethod
    def calculate_available_quote(trading_pair, balance, quote_price, adjusted_price):
        if None in (balance, quote_price, adjusted_price):
            print(f'Error: Missing parameter for calculating available quote for {trading_pair}.')
            return None

        return balance * quote_price * adjusted_price

    @staticmethod
    def log_order_attempts(order_records, msg=None):
        df = pd.DataFrame(order_records)
        if not df.empty:
            print(f'\n{msg}:')
            print(df.to_string(index=False))

    @staticmethod
    def record_order(attempt, trading_pair, side, adjusted_size, adjusted_price, lowest_ask, highest_bid):
        return {
            'attempt': attempt,
            'symbol': trading_pair,
            'action': side,
            'size': adjusted_size,
            'price': adjusted_price,
            'ask': lowest_ask,
            'bid': highest_bid
        }

    async def fetch_order_status(self, base_deci, quote_deci, quote_currency, trading_pair, side, adjusted_price, retries):
        """
        Determine if order placed. free balance < $10 indicates sell order was placed
        total balance > $10 indicates buy order was placed
        """
        getcontext().prec = 8
        open_orders = []
        endpoint = 'private'
        params = {
            'paginate': True,  # Enable automatic pagination
            'paginationCalls': 10  # Set the max number of pagination calls if necessary
        }

        all_open_orders = await self.ccxt_exceptions.ccxt_api_call(self.exchange.fetch_open_orders, endpoint, None,
                                                                   params=params)
        try:
            if len(all_open_orders) != 0:
                open_orders = self.utils.format_open_orders(all_open_orders)
                # open_orders = list(all_open_orders)
            coin = [trading_pair.split('/')[0], quote_currency]  # USD
            if not open_orders:
                self.log_manager.webhook_logger.info(f'No open orders found for {trading_pair} a {side} order will be '
                                                     f'created.')
                return False
            else:
                active_orders = [order for order in open_orders if order['symbol'] == trading_pair]
                print(f'active_orders: {active_orders}')

            balances = await self.ccxt_exceptions.ccxt_api_call(self.exchange.fetch_balance, endpoint)

            if side == 'buy':
                balances = (balances[coin[0]]['total'])  # Need coin balance determine if order was placed
            elif side == 'sell':
                balances = (balances[coin[0]]['free'])  # Need coin balance to determine if order was placed
            balances = Decimal(balances)
            balances = self.utils.adjust_precision(base_deci, quote_deci, balances, None, convert='base')
            print(f'balances: {balances}')
            if balances * adjusted_price > 10 and side == 'buy':  # a balance of < $10.00 indicates order was placed
                self.log_manager.webhook_logger.info(f'{retries+1} retries reached.  {side} order was placed.')
                return True
            elif (balances * adjusted_price < self.min_sell_value) and side == 'sell':
                self.log_manager.webhook_logger.info(f'{retries + 1} retries reached.  {side} order was placed.')
                return True
            else:
                self.log_manager.webhook_logger.info(f'{retries + 1} retries reached.  {side} order was not placed.')
                return False
        except Exception as ex:
            self.log_manager.webhook_logger.error(f'fetch_order_status: Error processing balances: {ex}')
            return False

