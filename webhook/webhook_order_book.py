""" This class will encapsulate all functionality related to the order book, such as fetching the current order book
from the exchange, processing and storing the data, and providing methods to access this data."""


from datetime import datetime, timedelta
from decimal import Decimal, ROUND_DOWN

from log_manager import LoggerManager


class OrderBookManager:
    _instance_count = 0
    _instance = None

    @classmethod
    def get_instance(cls, exchange_client, utility, logmanager, ccxt_exceptions):
        if cls._instance is None:
            cls._instance = cls(exchange_client, utility, logmanager, ccxt_exceptions)
        return cls._instance

    def __init__(self, exchange_client, utility, logmanager, ccxt_exceptions):
        # self.id = OrderBookManager._instance_count
        # OrderBookManager._instance_count += 1
        # print(f"OrderBookManager Instance ID: {self.id}")
        self.exchange = exchange_client
        self.log_manager = logmanager
        self.ccxt_exceptions = ccxt_exceptions
        self.tradebot_utils = utility
        self.base_currency, self.quote_currency, self.trading_pair = None, None, None
        self.base_deci, self.quote_deci = None, None
        self.base_incri, self.quote_incri, self.balances = None, None, None

    @LoggerManager.log_method_call
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
    async def get_order_book(self):
        """ This method fetches the order book from the exchange and returns it as a dictionary."""
        order_book = self.exchange.fetch_order_book(self.trading_pair, limit=5)
        highest_bid, lowest_ask, spread = self.analyze_spread(order_book)
        return order_book, highest_bid, lowest_ask, spread

    @LoggerManager.log_method_call
    def analyze_spread(self, order_book):
        # Convert quote_deci to a format string for quantization
        quantize_format = self.tradebot_utils.get_decimal_format(self.quote_deci)
        highest_bid_float = order_book['bids'][0][0] if order_book['bids'] else None
        lowest_ask_float = order_book['asks'][0][0] if order_book['asks'] else None
        highest_bid = self.tradebot_utils.float_to_decimal(highest_bid_float, self.quote_deci).quantize(quantize_format,
                                                                                                        rounding=ROUND_DOWN)
        lowest_ask = self.tradebot_utils.float_to_decimal(lowest_ask_float, self.quote_deci).quantize(quantize_format,
                                                                                                      rounding=ROUND_DOWN)
        spread = lowest_ask - highest_bid if highest_bid and lowest_ask else None
        self.log_manager.webhook_logger.debug(f'analyze_spread:High bid: {highest_bid} Low ask: {lowest_ask} Spread: '
                                              f'{spread}')
        self.log_manager.webhook_logger.debug(
            f'OrderBookManager: analyze_spread: High bid: {highest_bid} Low ask: {lowest_ask} Spread: {spread}')
        # return highest_bid, lowest_ask, spread , additional_bids, additional_asks
        return highest_bid, lowest_ask, spread

    @LoggerManager.log_method_call
    async def cancel_stale_orders(self, open_orders):
        now = datetime.utcnow()
        symbol = None
        # iterate through open orders dataframe
        for index, order in open_orders.iterrows():
            try:
                # Extract order details
                order_id = order['order_id']
                symbol = order['product_id']
                is_buy_order = order['side'] == 'BUY'

                # Fetch detailed order information
                detailed_order = await self.ccxt_exceptions.ccxt_api_call(lambda: self.exchange.fetch_order(order_id,
                                                                                                            symbol))
                # Extract timestamp and convert to datetime
                order_time = datetime.utcfromtimestamp(
                    detailed_order['timestamp'] / 1000)  # Assuming timestamp is in milliseconds

                # Check order age
                if now - order_time > timedelta(minutes=5) and is_buy_order:  # cancel buy orders older than 5 minutes
                    print(f"Cancelling order {order_id} for {symbol} as it is older than 5 minutes.")
                    await self.ccxt_exceptions.ccxt_api_call(lambda: self.exchange.cancel_order(order_id))
                    open_orders.drop(index, inplace=True)
                    continue

            except Exception as e:
                self.log_manager.webhook_logger.error(f'order_manager: cancel_stale_orders: An error occurred for '
                                                      f'{symbol}: {e}')
                continue

        return open_orders
