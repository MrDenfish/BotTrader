from decimal import ROUND_DOWN

from webhook.webhook_validate_orders import OrderData


class OrderBookManager:
    _instance = None

    @classmethod
    def get_instance(cls, exchange_client, shared_utils_precision, logmanager, ccxt_api):
        """
        Singleton method to ensure only one instance of OrderBookManager exists.
        """
        if cls._instance is None:
            cls._instance = cls(exchange_client, shared_utils_precision, logmanager, ccxt_api)
        return cls._instance

    def __init__(self, exchange_client, shared_utils_precision, logmanager, ccxt_api):
        """
        Initializes the OrderBookManager instance.
        """
        self.exchange = exchange_client
        self.shared_utils_precision = shared_utils_precision
        self.log_manager = logmanager
        self.ccxt_api = ccxt_api  # ✅ Renamed for clarity

    async def get_order_book(self, order_data: OrderData, symbol=None):
        """ This method fetches the order book from the exchange and returns it as a dictionary."""
        if symbol:
            trading_pair = symbol
        else:
            trading_pair = order_data.trading_pair

        endpoint = 'public'
        order_book = await self.ccxt_api.ccxt_api_call(self.exchange.fetch_order_book, endpoint, trading_pair , limit=50)
        highest_bid, lowest_ask, spread = self.analyze_spread(order_data.quote_decimal, order_book)
        order_details = {
            'order_book': order_book,
            'highest_bid': highest_bid,
            'lowest_ask': lowest_ask,
            'spread': spread
        }

        return order_details

    def analyze_spread(self, quote_deci, order_book):
        # Convert quote_deci to a format string for quantization
        try:
            quantize_format = self.shared_utils_precision.get_decimal_format(quote_deci)
            highest_bid_float = order_book['bids'][0][0] if order_book['bids'] else None
            lowest_ask_float = order_book['asks'][0][0] if order_book['asks'] else None
            highest_bid = self.shared_utils_precision.float_to_decimal(highest_bid_float, quote_deci).quantize(quantize_format,
                                                                                                       rounding=ROUND_DOWN)
            lowest_ask = self.shared_utils_precision.float_to_decimal(lowest_ask_float, quote_deci).quantize(quantize_format,
                                                                                                     rounding=ROUND_DOWN)
            spread = lowest_ask - highest_bid if highest_bid and lowest_ask else None
            self.log_manager.debug(f'analyze_spread:High bid: {highest_bid} Low ask: {lowest_ask} Spread: '
                                                  f'{spread}')
            self.log_manager.debug(
                f'OrderBookManager: analyze_spread: High bid: {highest_bid} Low ask: {lowest_ask} Spread: {spread}')
            # return highest_bid, lowest_ask, spread , additional_bids, additional_asks

            return highest_bid, lowest_ask, spread
        except Exception as e:
            self.log_manager.error(f'analyze_spread: An error occurred: {e}', exc_info=True)
            return None, None, None

