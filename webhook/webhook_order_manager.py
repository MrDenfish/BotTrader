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
                return False

            return await self.handle_order(validate_data, order_book_details, precision_data)


        except Exception as ex:
            self.log_manager.debug(ex, exc_info=True)
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
                self.log_manager.error(f"Error checking trailing stop orders: {e}", exc_info=True)
                return False

            if side == 'sell' and trailing_stop_active:
                self.log_manager.info("Active trailing stop order found.")
                return True
            elif side == 'sell' and base_balance == 0:
                self.log_manager.info(f"Insufficient base balance to sell. Available: {base_balance}")
                return False
            if side == 'buy' and quote_bal < quote_amount:
                self.log_manager.info(
                    f"Insufficient quote balance to buy. Required: {quote_amount}, Available: {quote_bal}")
                return False
            return True
        except KeyError as ke:
            self.log_manager.error(f"KeyError: Missing key in order_data or open_orders: {ke}", exc_info=True)
            return False
        except Exception as e:
            self.log_manager.error(f"Unexpected error: {e}", exc_info=True)
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
            take_profit_price = None
            highest_bid = Decimal(order_book_details['highest_bid'])
            lowest_ask = Decimal(order_book_details['lowest_ask'])
            spread = Decimal(order_book_details['spread'])
            base_deci, quote_deci, _, _ = precision_data
            adjusted_price, adjusted_size = self.utils.adjust_price_and_size(validate_data, order_book_details)

            self.log_manager.debug(f"Adjusted price: {adjusted_price}, Adjusted size: {adjusted_size}")

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


            adjusted_stop_loss_price = self.utils.adjust_precision(base_deci, quote_deci, stop_loss_price,
                                                                   convert='quote')
            adjusted_size = self.utils.adjust_precision(base_deci, quote_deci, adjusted_size,
                                                        convert='base')
            order_data = {
                **validate_data,
                'adjusted_price': adjusted_price,
                'adjusted_size': adjusted_size,
                'trading_pair': validate_data['trading_pair'],
                'side': validate_data['side'],
                'stop_loss_price': adjusted_stop_loss_price,
                'take_profit_price': adjusted_take_profit_price,

            }

            # Decide whether to place a bracket order or a trailing stop order
            if self.should_use_trailing_stop(adjusted_price, highest_bid, lowest_ask):
                return await self.attempt_order_placement(validate_data, order_data, order_type='trailing_stop')
            else:
                return await self.attempt_order_placement(validate_data, order_data, order_type='bracket')
        except Exception as ex:
            self.log_manager.debug(ex)
            return False
        except Exception as ex:
            self.log_manager.debug(ex)
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
        self.log_manager.info(
            f'Attempt {attempt}: Order {side} for {trading_pair} of size {order_size} at price {order_price} failed.'
        )

    async def attempt_order_placement(self, validate_data, order_data, order_type):
        """
        Attempts to place different types of orders (limit, bracket, trailing stop) based on the order type specified.
        If the order is rejected with a return value of 'amend', the function adjusts the order and retries the placement.
        Returns a tuple (bool, dict/None), where bool indicates success, and dict contains the response or error.
        """
        try:
            response = None  # Initialize response to avoid UnboundLocalError
            max_attempts = 5
            attempt = 0

            while attempt < max_attempts:
                attempt += 1
                try:
                    order_book = await self.order_book.get_order_book(order_data)
                    highest_bid = Decimal(order_book['highest_bid'])

                    if order_data['side'] == 'buy':
                        response = await self.order_types.place_limit_order(order_data)
                        print(f"Attempt # {attempt}: Adjusted stop price: {order_data['adjusted_price']}, "
                              f"highest bid price {highest_bid}")  # debug
                    elif order_type == 'bracket':
                        response, market_price, trailing_price = await self.order_types._handle_bracket_order(order_data, order_book)
                    elif order_type == 'trailing_stop':
                        print(f"Placing trailing stop order data: {order_data}, order data adjusted price: "
                              f"{order_data['adjusted_price']}, highest bid: {highest_bid}")  # debug
                        response = await self.order_types.place_trailing_stop_order(order_data, order_data['adjusted_price'])
                    else:
                        raise ValueError("Unknown order type specified")

                    # Process the response based on its type and content
                    if isinstance(response, dict):
                        error_response = response.get('error_response', {})

                        if error_response.get(
                                'message') == 'amend' or 'Too many decimals in order price' in error_response.get('message',
                                                                                                                  ''):
                            self.log_manager.info(
                                f"Order amendment required, adjusting order (Attempt {attempt}/{max_attempts})")
                            adjusted_price, adjusted_size = self.utils.adjust_price_and_size(order_data, order_book)
                            order_data['adjusted_price'] = self.utils.adjust_precision(
                                order_data['base_decimal'], order_data['quote_decimal'], adjusted_price, convert='quote')
                            order_data['adjusted_size'] = adjusted_size
                            continue  # Retry the loop with the adjusted order

                        elif 'PREVIEW_STOP_PRICE_BELOW_LAST_TRADE_PRICE' in error_response.get('preview_failure_reason', ''):
                            self.log_manager.info(
                                f"Stop price below last trade price, adjusting order (Attempt {attempt}/{max_attempts})")
                            adjusted_price, adjusted_size = self.utils.adjust_price_and_size(order_data, order_book)
                            order_data['adjusted_price'] = adjusted_price * Decimal(
                                '1.0002')  # Small increment to move above last trade price
                            continue  # Retry the loop with the adjusted order

                        elif len(response.get('id') ) > 0:
                            self.log_manager.info(f"{order_data['trading_pair']}, "
                                                                 f"{order_data['adjusted_price']}, {order_data['side']}")
                            return True, response

                        else:
                            self.log_manager.error(f"Unexpected response format: {response}", exc_info=True)
                            return False, response

                except Exception as ex:
                    self.log_manager.error(f"Error during attempt #{attempt}: {str(ex)}", exc_info=True)
                    if attempt >= max_attempts:
                        break  # Exit the loop if the maximum attempts are reached

            # Handle the case where all attempts have been exhausted
            self.log_manager.info(f"Order placement ultimately failed after {max_attempts} attempts.")
            return False, response

        except Exception as ex:
            self.log_manager.error(f"Error in attempt_order_placement: {str(ex)}", exc_info=True)
            return False, None

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
        self.log_manager.info(f'{side} order placed for {trading_pair} @ {price}')

    def handle_api_error(self, eapi, order_data):
        # Implement custom logic for handling different api(Coinbase Cloud) errors here
        # Example:
        if 'Post only mode' in str(eapi):
            self.log_manager.info(f'handle_order: CoinbaseAPIError in handle_order: Post only mode. '
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
        self.log_manager.info(f'handle_order: CoinbaseAPIError in handle_order: '
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
                self.log_manager.info(f'No open orders found for {trading_pair} a {side} order will be '
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
                self.log_manager.info(f'{retries+1} retries reached.  {side} order was placed.')
                return True
            elif (balances * adjusted_price < self.min_sell_value) and side == 'sell':
                self.log_manager.info(f'{retries + 1} retries reached.  {side} order was placed.')
                return True
            else:
                self.log_manager.info(f'{retries + 1} retries reached.  {side} order was not placed.')
                return False
        except Exception as ex:
            self.log_manager.error(f'fetch_order_status: Error processing balances: {ex}')
            return False

