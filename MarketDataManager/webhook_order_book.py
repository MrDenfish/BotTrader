from decimal import Decimal, ROUND_DOWN
from webhook.webhook_validate_orders import OrderData


class OrderBookManager:
    _instance = None

    @classmethod
    def get_instance(cls, exchange_client, shared_data_manager, shared_utils_precision, logger_manager, ccxt_api):
        """
        Singleton method to ensure only one instance of OrderBookManager exists.
        """
        if cls._instance is None:
            cls._instance = cls(exchange_client, shared_data_manager, shared_utils_precision, logger_manager, ccxt_api)
        return cls._instance

    def __init__(self, exchange_client, shared_data_manager, shared_utils_precision, logger_manager, ccxt_api):
        """
        Initializes the OrderBookManager instance.
        """
        self.exchange = exchange_client
        self.shared_data_manager = shared_data_manager
        self.shared_utils_precision = shared_utils_precision
        self.logger = logger_manager  # 🙂
        self.ccxt_api = ccxt_api  # ✅ Renamed for clarity

    async def get_order_book(self, order_data: OrderData, symbol=None, order_book=None):
        """
        Returns the order book summary (bid/ask/spread) for a given trading pair.
        Falls back to local cache from SharedDataManager.
        """
        try:
            trading_pair = symbol or order_data.trading_pair
            spread_data = self.shared_data_manager.market_data.get("bid_ask_spread", {}).get(trading_pair)

            if spread_data:
                return {
                    "order_book": None,  # full book no longer needed
                    "highest_bid": Decimal(str(spread_data["bid"])),
                    "lowest_ask": Decimal(str(spread_data["ask"])),
                    "spread": Decimal(str(spread_data["spread"]))
                }

            self.logger.warning(f"⚠️ No bid/ask spread found in market_data for {trading_pair}")
            return None

        except Exception as e:
            self.logger.error(f"❌ Error in get_order_book: {e}", exc_info=True)
            return None


    def analyze_spread(self, quote_deci, order_book):
        # Convert quote_deci to a format string for quantization
        try:
            quantize_format = self.shared_utils_precision.get_decimal_format(quote_deci)
            highest_bid_float = order_book.get('bid')
            lowest_ask_float = order_book.get('ask')
            highest_bid = self.shared_utils_precision.float_to_decimal(highest_bid_float, quote_deci).quantize(quantize_format,
                                                                                                       rounding=ROUND_DOWN)
            lowest_ask = self.shared_utils_precision.float_to_decimal(lowest_ask_float, quote_deci).quantize(quantize_format,
                                                                                                     rounding=ROUND_DOWN)
            spread = lowest_ask - highest_bid if highest_bid and lowest_ask else None
            self.logger.debug(f'analyze_spread:High bid: {highest_bid} Low ask: {lowest_ask} Spread: '
                                                  f'{spread}')
            self.logger.debug(
                f'OrderBookManager: analyze_spread: High bid: {highest_bid} Low ask: {lowest_ask} Spread: {spread}')
            # return highest_bid, lowest_ask, spread , additional_bids, additional_asks

            return highest_bid, lowest_ask, spread
        except Exception as e:
            self.logger.error(f'analyze_spread: An error occurred: {e}', exc_info=True)
            return None, None, None
