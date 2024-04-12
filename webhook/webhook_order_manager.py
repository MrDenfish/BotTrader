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
    def get_instance(cls, exchange_client, utility, validate, logmanager, alerts, ccxt_api, order_book):
        if cls._instance is None:
            cls._instance = cls(exchange_client, utility, validate, logmanager, alerts, ccxt_api, order_book)
        return cls._instance

    def __init__(self, exchange_client, utility, validate, logmanager, alerts, ccxt_api, order_book):
        # self.id = TradeOrderManager._instance_count
        # TradeOrderManager._instance_count += 1
        # print(f"TradeOrderManager Instance ID: {self.id}")
        # Placeholder for exchange instance creation
        self.exchange = exchange_client
        self.log_manager = logmanager
        self.tradebot_utils = utility
        self.validate = validate
        self.order_book = order_book
        self.ccxt_exceptions = ccxt_api
        self.alerts = alerts

    async def place_order(self, order_data):
        handle_order_data = {}
        try:

            quote_bal, base_balance, open_orders = await self.tradebot_utils.get_open_orders(order_data)
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
                    adjusted_price, adjusted_size = (self.tradebot_utils.adjust_price_and_size(price_size_data, order_book))

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
            elif order_data['base_price'] * base_balance > 10.00:
                self.log_manager.webhook_logger.info(f'place_order: {order_data["side"]} order will not be placed for '
                                                     f'{order_data["trading_pair"]} there is an existing balance of '
                                                     f'{base_balance} {order_data["base_currency"]}')

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

    async def handle_order(self, handle_order_data, retries=10):

        """
        Coordinates the process of placing an order. It calculates the order parameters, attempts to place
        an order, checks if the order is accepted, and retries if necessary.
        """

        order_records = []
        trading_pair = handle_order_data['trading_pair']
        side = handle_order_data['side']
        adjusted_size = handle_order_data['adjusted_size']
        adjusted_price = handle_order_data['adjusted_price']
        available_coin_balance = handle_order_data['available_coin_balance']
        quote_balance = handle_order_data['quote_balance']
        quote_price = handle_order_data['quote_price']
        quote_amount = handle_order_data['quote_amount']
        base_deci = handle_order_data['base_decimal']
        quote_deci = handle_order_data['quote_decimal']
        open_orders = handle_order_data['open_orders']
        base_incri = handle_order_data['base_increment']

        hold_amount = await self.staked_coins(trading_pair)
        for attempt in range(retries):
            try:
                print(f'Attempt {attempt + 1} of {retries}', end='\r')
                response = None
                # Calculate available quote and adjust order parameters
                available_quote = self.calculate_available_quote(trading_pair, available_coin_balance, quote_price,
                                                                 adjusted_price)
                # Check order feasibility

                if not self.is_order_feasible(side, available_quote):
                    continue

                order_book, highest_bid, lowest_ask, spread = await self.order_book.get_order_book(handle_order_data)
                order_size = Decimal(adjusted_size) - Decimal(hold_amount)

                handle_order_data['adjusted_price'], handle_order_data['adjusted_size'] = (
                    self.tradebot_utils.adjust_price_and_size(handle_order_data, order_book, response))
                adjusted_price = handle_order_data['adjusted_price']
                adjusted_size = handle_order_data['adjusted_size']
                if (open_orders.empty or not (
                        open_orders['product_id'] == trading_pair).any()) and quote_balance > quote_amount:
                    order_placed, response = await self.try_place_order(trading_pair, side, order_size, adjusted_price)

                    if order_placed:
                        print(f'\nOrder placed on attempt {attempt + 1}.')
                        return True
                    else:
                        if response == 'order_size_too_small':
                            print(f'Order size too small for {trading_pair} @ {adjusted_price}')
                            return False
                        else:
                            print(f'Attempt {attempt + 1} of {retries} for {trading_pair} @ {adjusted_price}', end='\r')
                            continue
                        # Record the order attempt and adjust for next attempt
                else:
                    if quote_amount > 100:
                        order_placed, response = await self.try_place_order(trading_pair, side, order_size, adjusted_price)

                        if order_placed:
                            self.log_order_attempts(order_records, f'Order placed  on attempt {attempt + 1}.')
                            return True
                        else:
                            if response == 'order_size_too_small':
                                print(f'Order size too small for {trading_pair} @ {adjusted_price}')
                                return False
                            else:
                                print(f'Attempt {attempt + 1} of {retries} for {trading_pair} @ {adjusted_price}', end='\r')
                                continue

                order_records.append(self.record_order(attempt + 1, trading_pair, side, order_size,
                                                       adjusted_price, lowest_ask, highest_bid))


            except Exception as ex:
                print(f'\nError on attempt {attempt + 1}: {ex}')
                error_details = traceback.format_exc()
                self.log_manager.webhook_logger.error(f'handle_action: Error details: {error_details}, '
                                                      f'trading_pair: {trading_pair}')

        self.log_order_attempts(order_records, f'Order attempts for {trading_pair}')
        return False

    async def place_limit_order(self, trading_pair, side, adjusted_size, adjusted_price):
        """
        Attempts to place a limit order and returns the response.
        If the order fails, it logs the error and returns None.
        """

        try:
            endpoint = 'private'
            response = await self.ccxt_exceptions.ccxt_api_call(self.exchange.create_limit_order, endpoint, trading_pair,
                                                                side, adjusted_size, adjusted_price, {'post_only': True})

            if response == 'amend':
                return 'amend'  # Order needs amendment
            elif response == 'insufficient base balance':
                return 'insufficient base balance'
            elif response == 'order_size_too_small':
                return 'order_size_too_small'
            elif response['id']:
                return response  # order placed successfully
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

    async def try_place_order(self, trading_pair, side, adjusted_size, adjusted_price):
        #  price is the adjusted_price ( best_highest bid + increment for a sell and best_lowest_ask + increment for a buy)
        response = None
        try:
            response = await self.place_limit_order(trading_pair, side, adjusted_size, adjusted_price)
            order_placed = False
            if response == 'amend':
                order_placed = False
            elif response == 'insufficient base balance':
                order_placed = False
            elif response == 'order_size_too_small':
                order_placed = False
            elif response['id']:
                order_placed = True
                self.log_success(trading_pair, adjusted_price, side)

            return order_placed, response

        except CoinbaseAPIError as eapi:
            return self.handle_coinbase_api_error(eapi, response)
        except Exception as ex:
            error_details = traceback.format_exc()
            self.log_manager.webhook_logger.error(f'try_place_order: Error placing order: {error_details}')
            self.log_manager.webhook_logger.error(f'Error placing limit order: {ex}')
            return ex

    async def staked_coins(self, trading_pair):

        endpoint = 'private'
        accounts = await self.ccxt_exceptions.ccxt_api_call(self.exchange.fetch_balance, endpoint)
        base_currency = trading_pair.split('/')[0]
        hold_amount = 0
        for account in accounts['info']['data']:
            currency = account['currency']['code']  # Get the currency code
            if base_currency == account['currency']['code']:
                if 'Staked' in account['name']:
                    free_balance = accounts.get(currency, {}).get('free', 'N/A')  # Use 'N/A' as a default if not found
                    hold = account['balance']['amount']  # Get the amount held
                    hold_amount = Decimal(hold)
                    if hold_amount > 1:
                        print(f"Currency: {currency}, Available: {free_balance}, On Hold: {hold_amount}")

                        return hold_amount
                    else:
                        hold_amount = 0
        return hold_amount

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

    @staticmethod
    def is_order_feasible(side, available_quote):
        if (side == 'buy' and available_quote < 10) or (side == 'sell' and available_quote > 10):
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
                open_orders = self.tradebot_utils.format_open_orders(all_open_orders)
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
            balances = self.tradebot_utils.adjust_precision(base_deci, quote_deci, balances, None, convert='base')
            print(f'balances: {balances}')
            if balances * adjusted_price > 10 and side == 'buy':  # a balance of < $10.00 indicates order was placed
                self.log_manager.webhook_logger.info(f'{retries+1} retries reached.  {side} order was placed.')
                return True
            elif (balances * adjusted_price < 10) and side == 'sell':
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