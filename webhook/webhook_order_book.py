from datetime import datetime, timedelta
from decimal import ROUND_DOWN


import traceback


class OrderBookManager:
    _instance_count = 0
    _instance = None

    @classmethod
    def get_instance(cls, exchange_client, utility, logmanager, ccxt_exceptions):
        if cls._instance is None:
            cls._instance = cls(exchange_client, utility, logmanager, ccxt_exceptions)
        return cls._instance

    def __init__(self, exchange_client, utility, logmanager, ccxt_exceptions):
        self.exchange = exchange_client
        self.log_manager = logmanager
        self.ccxt_exceptions = ccxt_exceptions
        self.tradebot_utils = utility

    async def get_order_book(self, order_data):
        """ This method fetches the order book from the exchange and returns it as a dictionary."""
        endpoint = 'public'
        order_book = await self.ccxt_exceptions.ccxt_api_call(self.exchange.fetch_order_book, endpoint, order_data[
                                                              'trading_pair'], limit=50)

        highest_bid, lowest_ask, spread = self.analyze_spread(order_data, order_book)
        order_details = {
            'order_book': order_book,
            'highest_bid': highest_bid,
            'lowest_ask': lowest_ask,
            'spread': spread
        }

        return order_details

    async def cancel_stale_orders(self, order_data, open_orders):

        now = datetime.utcnow()
        symbol = None
        # iterate through open orders dataframe
        for index, order in open_orders.iterrows():
            try:
                # Extract order details
                order_id = order['order_id']
                symbol = order['product_id']
                is_buy_order = order['side'] == 'BUY'
                endpoint = 'private'

                # Fetch detailed order information
                detailed_order = await self.ccxt_exceptions.ccxt_api_call(self.exchange.fetch_order, endpoint, order_id,
                                                                          symbol)

                # Extract timestamp and convert to datetime
                if detailed_order['timestamp']:
                    order_time = datetime.utcfromtimestamp(detailed_order['timestamp'] / 1000)  # Assuming  milliseconds

                    # Check order age
                    if now - order_time > timedelta(minutes=5):  # cancel orders older than 5 minutes
                        print(f"Cancelling order {order_id} for {symbol} as it is older than 5 minutes.")
                        await self.ccxt_exceptions.ccxt_api_call(self.exchange.cancel_order, endpoint, order_id)
                        open_orders.drop(index, inplace=True)
                        continue
                    elif order_data['base_currency'] == detailed_order['symbol'].split('/')[0]:
                        if order_data['base_price'] > detailed_order['price'] and is_buy_order:  # cancel coin specific
                            # orders
                            # if price has changed significantly
                            print(f"Cancelling order {order_id} for {symbol} as it is no longer the best bid.")
                            await self.ccxt_exceptions.ccxt_api_call(self.exchange.cancel_order, endpoint, order_id)
                            open_orders.drop(index, inplace=True)
                            continue

            except Exception as e:
                error_details = traceback.format_exc()
                self.log_manager.webhook_logger.error(f'cancel_stale_orders: {error_details}')
                self.log_manager.webhook_logger.error(f'webhook_order_book: cancel_stale_orders: An error occurred for '
                                                      f'{symbol}: {e}')
                continue
        return open_orders

    def analyze_spread(self, order_data, order_book):
        # Convert quote_deci to a format string for quantization
        try:

            quote_deci = order_data['quote_decimal']
            quantize_format = self.tradebot_utils.get_decimal_format(quote_deci)
            highest_bid_float = order_book['bids'][0][0] if order_book['bids'] else None
            lowest_ask_float = order_book['asks'][0][0] if order_book['asks'] else None
            highest_bid = self.tradebot_utils.float_to_decimal(highest_bid_float, quote_deci).quantize(quantize_format,
                                                                                                       rounding=ROUND_DOWN)
            lowest_ask = self.tradebot_utils.float_to_decimal(lowest_ask_float, quote_deci).quantize(quantize_format,
                                                                                                     rounding=ROUND_DOWN)
            spread = lowest_ask - highest_bid if highest_bid and lowest_ask else None
            self.log_manager.webhook_logger.debug(f'analyze_spread:High bid: {highest_bid} Low ask: {lowest_ask} Spread: '
                                                  f'{spread}')
            self.log_manager.webhook_logger.debug(
                f'OrderBookManager: analyze_spread: High bid: {highest_bid} Low ask: {lowest_ask} Spread: {spread}')
            # return highest_bid, lowest_ask, spread , additional_bids, additional_asks

            return highest_bid, lowest_ask, spread
        except Exception as e:
            self.log_manager.webhook_logger.error(f'analyze_spread: An error occurred: {e}', exc_info=True)
            return None, None, None
