
from log_manager import LoggerManager

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
        self.base_currency, self.quote_currency, self.trading_pair = None, None, None
        self.base_deci, self.quote_deci, self.balances = None, None, None
        self.base_incri, self.quote_incri = None, None

    #  instance attribute
    def set_trade_parameters(self, trading_pair, base_currency, quote_currency, base_decimal, quote_decimal,
                             base_increment, quote_increment, balances):
        self.base_currency = base_currency
        self.quote_currency = quote_currency
        self.trading_pair = trading_pair
        self.base_deci = base_decimal
        self.quote_deci = quote_decimal
        self.base_incri = base_increment
        self.quote_incri = quote_increment
        self.balances = balances

    @LoggerManager.log_method_call
    async def place_order(self, quote_price, quote_amount, base_price, side, usd_amount):
        try:
            quote_bal, base_balance, open_orders = await self.tradebot_utils.get_open_orders()
            if side == 'sell' and base_balance == 0.0:
                print(f'Order for {self.trading_pair} not valid, base balance is 0.0')
                return False  # not a valid order

            if (side == 'sell') or (side == 'buy' and quote_bal >= quote_amount):
                if open_orders is not None and not open_orders.empty:
                    await self.order_book.cancel_stale_orders(open_orders)
                order_book, highest_bid, lowest_ask, spread = await self.order_book.get_order_book()
                available_coin_balance, valid_order = (self.validate.fetch_and_validate_rules(side, highest_bid, usd_amount,
                                                       base_balance, open_orders, quote_amount, quote_price))
                if valid_order:
                    adjusted_price, adjusted_size = self.tradebot_utils.adjusted_price_and_size(
                        side, order_book, quote_price, available_coin_balance, usd_amount)
                    self.log_manager.webhook_logger.debug(f'place_order: adjusted_price: {adjusted_price}, adjusted_size: '
                                                          f'{adjusted_size}')

                    return await self.handle_order(open_orders, side, adjusted_size, usd_amount, available_coin_balance,
                                                   adjusted_price, quote_price, highest_bid, lowest_ask)
                else:
                    return False  # not a valid order
            elif base_price * base_balance > 10.00:
                self.log_manager.webhook_logger.info(f'place_order: {side} order will not be placed for {self.trading_pair} '
                                                     f'there is an existing balance of {base_balance} {self.base_currency}')
            else:
                self.log_manager.webhook_logger.info(f'Insufficient Balance {quote_bal}{self.quote_currency}: '
                                                     f'{self.trading_pair} {side} order requires {quote_amount}'
                                                     f'{self.quote_currency} ')

            return False   # not a valid order
        except Exception as ex:
            error_details = traceback.format_exc()
            self.log_manager.webhook_logger.error(f'place_order: Error details: {error_details}')
            self.log_manager.webhook_logger.error(f'place_order: Error placing order: {ex}')
            return False

    @LoggerManager.log_method_call
    async def handle_order(self, open_orders, side, adjusted_size, usd_amount, available_coin_balance, adjusted_price,
                           quote_price, highest_bid, lowest_ask, retries=10):
        """
        Coordinates the process of placing an order. It calculates the order parameters, attempts to place
        an order, checks if the order is accepted, and retries if necessary.
        """
        # Initialize an empty list to collect order records
        order_records = []
        #  order_record = pd.DataFrame(columns=['attempt', 'symbol', 'action', 'size', 'price', 'ask', 'bid'])
        try:
            for attempt in range(1, retries + 1):
                print(f'Working...', end='\r')
                # Calculate available quote in dollars
                available_quote = self.calculate_available_quote(available_coin_balance, quote_price, adjusted_price)
                # Record the order attempt
                order_records = self.record_order(order_records, attempt, side, adjusted_size, adjusted_price, lowest_ask,
                                                  highest_bid)

                # Skip iteration if available_quote is None
                if None in (available_quote, adjusted_price, adjusted_size):
                    order_book, highest_bid, lowest_ask, spread = await self.order_book.get_order_book()
                    adjusted_price, adjusted_size = (
                        self.tradebot_utils.adjusted_price_and_size(side, order_book, quote_price, available_coin_balance,
                                                                    usd_amount))
                    continue

                # Check order feasibility
                if not self.is_order_feasible(side, available_quote):
                    continue

                # checking for open orders, an open order is an order placed but not yet filled.
                if open_orders is None:
                    # make another attempt at placing order by adjusting the price and size
                    continue
                open_order_exists = (open_orders['product_id'] == self.trading_pair).any()
                if open_order_exists:
                    self.log_manager.webhook_logger.info(
                        f'Open orders exist for {self.trading_pair}. New order will not be placed.')
                    return False
                else:
                    # Try placing the order
                    order_placed, response = await self.try_place_order(side, adjusted_size, adjusted_price)
                    if order_placed:
                        break
                    else:
                        order_book, highest_bid, lowest_ask, spread = await self.order_book.get_order_book()
                        adjusted_price, adjusted_size = (
                            self.tradebot_utils.adjusted_price_and_size(side, order_book, quote_price,
                                                                        available_coin_balance, usd_amount, response))
            # Convert the list of order records to a DataFrame
            order_record_df = pd.DataFrame(order_records)
            # Handle retries and adjustments
            print(f'')
            print(order_record_df.to_string(index=False))
            print(f'')

            return True
        except Exception as ex:
            error_details = traceback.format_exc()
            self.log_manager.webhook_logger.error(f'handle_order: Error placing order: {error_details}')
            self.log_manager.webhook_logger.error(f'Error placing limit order: {ex}')

        return False

    @LoggerManager.log_method_call
    async def try_place_order(self, side, adjusted_size, adjusted_price):

        #  price is the adjusted_price ( best_highest bid + increment for a sell and best_lowest_ask + increment for a buy)
        response = None
        try:
            response = await self.place_limit_order(side, adjusted_size, adjusted_price)
            order_placed = False
            if response == 'amend':
                order_placed = False
            if response == 'insufficient base balance':
                order_placed = False
            elif response:
                order_placed = True
                self.log_success(adjusted_price, side)

            return order_placed, response

        except CoinbaseAPIError as eapi:
            return self.handle_coinbase_api_error(eapi, response)
        except Exception as ex:
            error_details = traceback.format_exc()
            self.log_manager.webhook_logger.error(f'try_place_order: Error placing order: {error_details}')
            self.log_manager.webhook_logger.error(f'Error placing limit order: {ex}')
            return ex

    @LoggerManager.log_method_call
    async def place_limit_order(self, side, adjusted_size, adjusted_price):
        """
        Attempts to place a limit order and returns the response.
        If the order fails, it logs the error and returns None.
        """
        try:
            response = await self.ccxt_exceptions.ccxt_api_call(lambda: (self.exchange.create_limit_order(self.trading_pair,
                                                                         side, adjusted_size, adjusted_price,
                                                                         {'post_only': True})))
            if response == 'amend':
                return 'amend'  # Order needs amendment
            elif response == 'insufficient base balance':
                return 'insufficient base balance'
            elif response:
                return response  # order placed successfully
        except Exception as ex:
            error_details = traceback.format_exc()
            self.log_manager.webhook_logger.error(f'place_limit_order: {error_details}')
            if 'coinbase createOrder() has failed, check your arguments and parameters' in str(ex):
                self.log_manager.webhook_logger.info(f'Limit order was not accepted, placing new limit order for '
                                                     f'{self.trading_pair}')
                return 'amend'
            else:
                self.log_manager.webhook_logger.error(f'Error placing limit order: {ex}')
                return 'amend'
        return None  # Return None indicating the order was not successfully placed

    @LoggerManager.log_method_call
    async def fetch_order_status(self, side, adjusted_price, retries):
        """
        Determine if order placed. free balance < $10 indicates sell order was placed
        total balance > $10 indicates buy order was placed
        """
        getcontext().prec = 8
        open_orders = []
        all_open_orders = await self.ccxt_exceptions.ccxt_api_call(lambda: self.exchange.fetch_open_orders(None))
        try:
            if len(all_open_orders) != 0:
                open_orders = self.tradebot_utils.format_open_orders(all_open_orders)
                # open_orders = list(all_open_orders)
            coin = [self.trading_pair.split('/')[0], self.quote_currency]  # USD
            if not open_orders:
                self.log_manager.webhook_logger.info(f'No open orders found for {self.trading_pair} a {side} order will be '
                                                     f'created.')
                return False
            else:
                active_orders = [order for order in open_orders if order['symbol'] == self.trading_pair]
                print(f'active_orders: {active_orders}')

            balances = await self.ccxt_exceptions.ccxt_api_call(lambda: self.exchange.fetch_balance())

            if side == 'buy':
                balances = (balances[coin[0]]['total'])  # Need coin balance determine if order was placed
            elif side == 'sell':
                balances = (balances[coin[0]]['free'])  # Need coin balance to determine if order was placed
            balances = Decimal(balances)
            balances = self.tradebot_utils.adjust_precision(balances, None, convert='base')
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

    @LoggerManager.log_method_call
    def place_market_order(self, side, adjusted_size, adjusted_price):
        """
               This function coordinates the process. It calculates the order parameters, attempts to place
               an order, checks if the order is accepted, and retries if necessary."""
        response = None
        try:
            response = self.ccxt_exceptions.ccxt_api_call(lambda: (self.exchange.create_market_order(
                self.trading_pair, side, adjusted_size, adjusted_price), self.trading_pair))
            if response:
                return response
        except Exception as ex:
            if 'coinbase createOrder() has failed, check your arguments and parameters' in str(ex):
                self.log_manager.webhook_logger.info(f'Limit order was not accepted, placing new limit order for '
                                                     f'{self.trading_pair}')
                return response
            else:
                self.log_manager.webhook_logger.error(f'Error placing limit order: {ex}')

        return None  # Return None indicating the order was not successfully pla

    @staticmethod
    @LoggerManager.log_method_call
    def is_order_feasible(side, available_quote):
        if (side == 'buy' and available_quote < 10) or (side == 'sell' and available_quote > 10):
            return True
        return False

    @LoggerManager.log_method_call
    def record_order(self, order_records, attempt, side, adjusted_size, adjusted_price, lowest_ask, highest_bid):
        # Create a new order record
        try:
            new_order = {
                'attempt': attempt,
                'symbol': self.trading_pair,
                'action': side,
                'size': adjusted_size,
                'price': adjusted_price,
                'ask': lowest_ask,
                'bid': highest_bid
            }
            #  new_order_df = pd.DataFrame([new_order])

            # Append the new order to the open_orders DataFrame
            order_records.append(new_order)
            return order_records
            # order_record = pd.concat([order_record, new_order_df], ignore_index=True)
        except Exception as ex:
            self.log_manager.webhook_logger.error(f'Error appending order book: {ex}')
        return order_records

    @LoggerManager.log_method_call
    def log_success(self, price, side):
        self.log_manager.webhook_logger.info(f'{side} order placed for {self.trading_pair} @ {price}')

    @LoggerManager.log_method_call
    def handle_coinbase_api_error(self, eapi, response):
        error_info = self.handle_api_error(eapi, response)
        if error_info == 'retry':
            return False
        elif error_info in ['abort', '503']:
            self.log_api_error(error_info, eapi, response)
            return False
        return False

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

    @LoggerManager.log_method_call
    def calculate_available_quote(self, balance, quote_price, adjusted_price):

        try:  # Check if any of the values are None
            if None in (balance, quote_price, adjusted_price):
                # Handle the error condition, e.g., by logging and returning None or a default value
                self.log_manager.webhook_logger.error(f'calculate_available_quote: One or more parameters are None for '
                                                      f'{self.trading_pair} Balance:{balance} Quote Price: {quote_price} '
                                                      f'Adjusted Price: {adjusted_price}')
                return None

            return balance * (quote_price * adjusted_price)
        except Exception as ex:
            error_details = traceback.format_exc()
            self.log_manager.sighook_logger.error(f'update_ticker_cache: {error_details}  {ex}')
