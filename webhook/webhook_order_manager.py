from custom_exceptions import CoinbaseAPIError

from decimal import Decimal, getcontext
import pandas as pd

import traceback

# Define the TradeOrderManager class
"""This class  will manage the trade orders."""


class TradeOrderManager:
    _instance_count = 0
    _instance = None

    @classmethod
    def get_instance(cls, config, exchange_client, utility, validate, logmanager, alerts, ccxt_api, order_book):
        if cls._instance is None:
            cls._instance = cls(config, exchange_client, utility, validate, logmanager, alerts, ccxt_api, order_book)
        return cls._instance

    def __init__(self,config, exchange_client, utility, validate, logmanager, alerts, ccxt_api, order_book):
        # self.id = TradeOrderManager._instance_count
        # TradeOrderManager._instance_count += 1
        # print(f"TradeOrderManager Instance ID: {self.id}")
        # Placeholder for exchange instance creation
        self._min_sell_value = Decimal(config.min_sell_value)
        self._hodl = config.hodl
        self.exchange = exchange_client
        self.log_manager = logmanager
        self.validate = validate
        self.order_book = order_book
        self.ccxt_exceptions = ccxt_api
        self.alerts = alerts
        self.utils = utility

    @property
    def hodl(self):
        return self._hodl

    @property
    def min_sell_value(self):
        return self._min_sell_value

    async def place_order(self, order_data):
        try:
            quote_bal, base_balance, open_orders = await self.utils.get_open_orders(order_data)
            open_orders = open_orders if isinstance(open_orders, pd.DataFrame) else pd.DataFrame()

            if not self.validate_order_conditions(order_data, quote_bal, base_balance):
                return False

            order_book_details = await self.order_book.get_order_book(order_data)
            validate_data = self.build_validate_data(order_data, quote_bal, base_balance, open_orders, order_book_details)

            available_coin_balance, valid_order = self.validate.fetch_and_validate_rules(validate_data)
            if not valid_order:
                self.log_manager.webhook_logger.info("Validation failed for the order.")
                return False

            return await self.handle_order(validate_data, order_book_details)

        except Exception as ex:
            self.log_error(ex)
            return False

    def validate_order_conditions(self, order_data, quote_bal, base_balance):
        side, quote_amount = order_data['side'], order_data['quote_amount']
        if side == 'sell' and base_balance == 0:
            self.log_manager.webhook_logger.info("No base balance to sell.")
            return False
        if side == 'buy' and quote_bal < quote_amount:
            self.log_manager.webhook_logger.info(
                f"Insufficient quote balance to buy. Required: {quote_amount}, Available: {quote_bal}")
            return False
        return True

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

    async def handle_order(self, validate_data, order_book_details):

        highest_bid = Decimal(order_book_details['highest_bid'])
        lowest_ask = Decimal(order_book_details['lowest_ask'])
        spread = Decimal(order_book_details['spread'])
        adjusted_price, adjusted_size = self.utils.adjust_price_and_size(validate_data, order_book_details)

        self.log_manager.webhook_logger.debug(f"Adjusted price: {adjusted_price}, Adjusted size: {adjusted_size}")

        order_data = {
            **validate_data,
            'adjusted_price': adjusted_price,
            'adjusted_size': adjusted_size,
            'trading_pair': validate_data['trading_pair'],
            'side': validate_data['side']
        }

        # Attempt to place the order
        return await self.attempt_order_placement(order_data)

    async def log_order_attempt(self, attempt, trading_pair, side, order_size, order_price):
        """
        Log each order attempt. Placeholder for actual logging implementation.
        """
        self.log_manager.webhook_logger.info(
            f'Attempt {attempt}: Order {side} for {trading_pair} of size {order_size} at price {order_price} failed.'
        )

    async def place_limit_order(self, order_data):
        """
        Attempts to place a limit order and returns the response.
        If the order fails, it logs the error and returns None.
        """

        try:
            trading_pair = order_data['trading_pair']
            side = order_data['side']
            adjusted_size = order_data['adjusted_size']
            adjusted_price = order_data['adjusted_price']
            endpoint = 'private'
            response = await self.ccxt_exceptions.ccxt_api_call(self.exchange.create_limit_order, endpoint, trading_pair,
                                                                side, adjusted_size, adjusted_price, {'post_only': True})
            if response:
                if response == 'amend':
                    return 'amend'  # Order needs amendment
                elif response == 'insufficient base balance':
                    return 'insufficient base balance'
                elif response == 'order_size_too_small':
                    return 'order_size_too_small'
                elif response['id']:
                    return response  # order placed successfully
            else:
                return 'amaend'
        except Exception as ex:
            error_details = traceback.format_exc()
            self.log_manager.webhook_logger.error(f'place_limit_order: {error_details}')
            if 'coinbase createOrder() has failed, check your arguments and parameters' in str(ex):
                self.log_manager.webhook_logger.info(f'Limit order was not accepted, placing new limit order for '
                                                     f'{trading_pair}')
                return 'amend'
            else:
                self.log_manager.webhook_logger.error(f'Error placing limit order: {ex}')
                return 'amend'
        return None  # Return None indicating the order was not successfully placed

    async def attempt_order_placement(self, order_data):
        #  price is the adjusted_price ( best_highest bid + increment for a sell and best_lowest_ask + increment for a buy)
        response = None
        try:
            response = await self.place_limit_order(order_data)
            order_placed = False
            if response == 'amend':
                order_placed = False
            elif response == 'insufficient base balance':
                order_placed = False
            elif response == 'order_size_too_small':
                order_placed = False
            elif response['id']:
                order_placed = True
                self.log_success(order_data['trading_pair'], order_data['adjusted_price'], order_data['side'])

            return order_placed, response

        except CoinbaseAPIError as eapi:
            return self.handle_coinbase_api_error(eapi, response)
        except Exception as ex:
            error_details = traceback.format_exc()
            self.log_manager.webhook_logger.error(f'try_place_order: Error placing order: {error_details}')
            self.log_manager.webhook_logger.error(f'Error placing limit order: {ex}')
            return ex

    async def staked_coins(self, trading_pair):
        currencies =[]

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
    def log_order_attempts(order_records,msg=None):
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

# <><><><><><><><><><><><><><><>NOT IMPLIMENTED YET 04/04/2024 <>><><><><><><><><><><><><><><><><><><><><><><>
    async def fetch_order_status(self, base_deci, quote_deci, quote_currency, trading_pair,  side, adjusted_price, retries):
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

    def place_market_order(self, trading_pair, side, adjusted_size, adjusted_price):
        """
               This function coordinates the process. It calculates the order parameters, attempts to place
               an order, checks if the order is accepted, and retries if necessary."""
        response = None
        try:
            endpoint = 'private'
            response = self.ccxt_exceptions.ccxt_api_call(self.exchange.create_market_order(trading_pair, side,
                                                          adjusted_size, adjusted_price), endpoint, trading_pair)
            if response:
                return response
        except Exception as ex:
            if 'coinbase createOrder() has failed, check your arguments and parameters' in str(ex):
                self.log_manager.webhook_logger.info(f'Limit order was not accepted, placing new limit order for '
                                                     f'{trading_pair}')
                return response
            else:
                self.log_manager.webhook_logger.error(f'Error placing limit order: {ex}')

        return None  # Return None indicating the order was not successfully pla

    # <><><><><><><><><><><><><><><>old ready to delete <>><><><><><><><><><><><><><><><><><><><><><><>

    async def old_place_order(self, order_data):
        handle_order_data = {}
        try:

            quote_bal, base_balance, open_orders = await self.utils.get_open_orders(order_data)
            open_orders = open_orders if isinstance(open_orders, pd.DataFrame) else pd.DataFrame()
            if order_data['side'] == 'sell' and base_balance == 0.0:

                return False  # not a valid order nothing to sell

            if (order_data['side'] == 'sell') or (order_data['side'] == 'buy' and quote_bal >= order_data['quote_amount']):
                if open_orders is not None and not open_orders.empty:
                    await self.order_book.cancel_stale_orders(order_data, open_orders)

                order_book, highest_bid, lowest_ask, spread = await self.order_book.get_order_book(order_data)
                validate_data = {
                    'base_balance': base_balance,
                    'quote_balance': quote_bal,
                    'base_decimal': order_data['base_decimal'],
                    'quote_decimal': order_data['quote_decimal'],
                    'base_currency': order_data['base_currency'],
                    'quote_currency': order_data['quote_currency'],
                    'trading_pair': order_data['trading_pair'],
                    'side': order_data['side'],
                    'highest_bid': highest_bid,
                    'lower_ask': lowest_ask,
                    'spread': spread,
                    'open_order': open_orders,
                    'quote_amount': order_data['quote_amount'],
                    'quote_price': order_data['quote_price']
                    }

                available_coin_balance, valid_order = (self.validate.fetch_and_validate_rules(validate_data))
                price_size_data = validate_data.copy()
                price_size_data['available_coin_balance'] = available_coin_balance
                price_size_data['base_increment'] = order_data['base_increment']
                price_size_data['valid_order'] = valid_order

                if valid_order:
                    adjusted_price, adjusted_size = (self.utils.adjust_price_and_size(price_size_data, order_book))

                    self.log_manager.webhook_logger.debug(f'place_order: adjusted_price: {adjusted_price}, adjusted_size: '
                                                          f'{adjusted_size}')
                    handle_order_data = {
                        'base_increment': order_data['base_increment'],
                        'base_decimal': order_data['base_decimal'],
                        'quote_decimal': order_data['quote_decimal'],
                        'trading_pair': order_data['trading_pair'],
                        'open_orders': open_orders,
                        'side': order_data['side'],
                        'adjusted_size': adjusted_size,
                        'quote_amount': order_data['quote_amount'],
                        'available_coin_balance': available_coin_balance,
                        'quote_balance': quote_bal,
                        'adjusted_price': adjusted_price,
                        'quote_price': order_data['quote_price']
                    }

                    return await self.handle_order(handle_order_data)
                else:
                    return False  # not a valid order
            elif order_data['side'] == 'buy' and quote_bal < order_data['quote_amount']:
                self.log_manager.webhook_logger.info(f'Insufficient Balance {quote_bal}{order_data["quote_currency"]}: '
                                                     f'{order_data["trading_pair"]} {order_data["side"]} order requires '
                                                     f'{order_data["quote_amount"]} {order_data["quote_currency"]} ')
                return False  # not a valid order
            elif order_data['base_price'] * base_balance > self.min_sell_value:
                self.log_manager.webhook_logger.info(f'Existing balance: {order_data["side"]} order will not be placed for '
                                                     f'{order_data["trading_pair"]} there is an existing balance of '
                                                     f'{base_balance} {order_data["base_currency"]}')
                return False  # not a valid order

            else:
                self.log_manager.webhook_logger.info(f'Insufficient Balance {quote_bal}{order_data["quote_currency"]}: '
                                                     f'{order_data["trading_pair"]} {order_data["side"]} order requires '
                                                     f'{order_data["quote_amount"]} {order_data["quote_currency"]} ')

            return False  # not a valid order
        except Exception as ex:
            error_details = traceback.format_exc()
            self.log_manager.webhook_logger.error(f'place_order: Error details: {error_details}')
            self.log_manager.webhook_logger.error(f'place_order: Error placing order: {ex}')
            return False



    async def old_handle_order(self, handle_order_data, retries=10):
        """
        Coordinates the process of placing an order. It calculates the order parameters,
        attempts to place an order, checks if the order is accepted, and retries if necessary.
        """

        trading_pair = handle_order_data['trading_pair']
        side = handle_order_data['side']
        adjusted_size = Decimal(handle_order_data['adjusted_size'])
        adjusted_price = Decimal(handle_order_data['adjusted_price'])
        quote_balance = Decimal(handle_order_data['quote_balance'])
        quote_amount = Decimal(handle_order_data['quote_amount'])
        coin_balance = Decimal(handle_order_data['available_coin_balance'])
        available_coin_value = coin_balance * adjusted_price
        open_orders = handle_order_data['open_orders']

        available_to_sell, _ = await self.staked_coins(trading_pair)

        if side == 'sell' and (available_to_sell * adjusted_price < self.min_sell_value):
            return False  # Not a valid order due to insufficient value

        for attempt in range(1, retries + 1):
            print(f'Attempt {attempt} of {retries}', end='\r')
            if not self.is_order_feasible(side, quote_amount, available_coin_value, quote_balance, trading_pair):
                continue

            # Fetch order book details
            order_book, highest_bid, lowest_ask, spread = await self.order_book.get_order_book(handle_order_data)

            # Adjust price and size based on the market data
            adjusted_price, adjusted_size = self.utils.adjust_price_and_size(handle_order_data, order_book)

            if open_orders.empty or not (open_orders['product_id'] == trading_pair).any():
                order_placed, response = await self.try_place_order(trading_pair, side, adjusted_size, adjusted_price)


                if order_placed:
                    print(f'\nOrder placed on attempt {attempt}.')
                    return True
                elif response == 'order_size_too_small':
                    print(f'Order size too small for {trading_pair} @ {adjusted_price}')
                    return False
            else:
                print(f'Skipping attempt {attempt} as open orders exist.')

            # Log failed attempt
            self.log_order_attempt(attempt, trading_pair, side, adjusted_size, adjusted_price)

        # After all retries
        print(f'\nAll attempts exhausted for {trading_pair}.')
        return False

