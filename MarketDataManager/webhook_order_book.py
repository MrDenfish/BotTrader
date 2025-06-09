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
        try:
            trading_pair = symbol or order_data.trading_pair

            order_book ={}
            # # ✅ Use ticker manager cache
            # ticker = self.shared_data_manager.get_latest_ticker(trading_pair)
            # if ticker:
            #     bid = Decimal(ticker["bid"])
            #     ask = Decimal(ticker["ask"])
            #     spread = ask - bid
            #
            #     order_book = {
            #         "order_book": None,  # No full book needed
            #         "highest_bid": bid,
            #         "lowest_ask": ask,
            #         "spread": spread
            #     }
            #
            # self.logger.warning(f"No cached ticker data for {trading_pair}")
            # # return None
            #
            # highest_bid, lowest_ask, spread = self.analyze_spread(order_data.quote_decimal, order_book)
            #
            # order_details = {
            #     'order_book': order_book,
            #     'highest_bid': highest_bid,
            #     'lowest_ask': lowest_ask,
            #     'spread': spread
            # }
            #
            # # ✅ Patch only if results from analyze_spread are clearly invalid
            # if (
            #         highest_bid is None or highest_bid == Decimal('0.0') or
            #         lowest_ask is None or lowest_ask == Decimal('0.0') or
            #         spread is None or spread == Decimal('0.0')
            # ):
            #     self.logger.warning(
            #         f"⚠️ analyze_spread failed or returned zeros for {trading_pair}. Falling back to calculate_order_book_summary()"
            #     )
            order_details = self.calculate_order_book_summary(order_data, order_details)

            return order_details
        except Exception as e:
            self.logger.error(f"Error in get_order_book: {e}", exc_info=True)
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

    # def calculate_order_book_summary(self, order_data, order_book_details: dict) -> dict:
    #     try:
    #         order_book = order_book_details.get("order_book", {})
    #         bids = order_book.get("bids", [])
    #         asks = order_book.get("asks", [])
    #
    #         # Check if both bids and asks are non-empty
    #         if not bids or not asks:
    #             self.logger.warning("⚠️ Cannot calculate order book summary: empty bids or asks.")
    #             return order_book_details  # Return original, even if incomplete
    #
    #         # Calculate highest bid and lowest ask
    #         highest_bid = self.shared_utils_precision.safe_convert(max(bid[0] for bid in bids), order_data.quote_decimal)
    #         lowest_ask = self.shared_utils_precision.safe_convert(min(ask[0] for ask in asks), order_data.quote_decimal)
    #         spread = lowest_ask - highest_bid
    #
    #         # Update the original dictionary with corrected values
    #         order_book_details["highest_bid"] = highest_bid
    #         order_book_details["lowest_ask"] = lowest_ask
    #         order_book_details["spread"] = spread
    #
    #         return order_book_details
    #
    #     except Exception as e:
    #         self.logger.error(f"❌ Error calculating order book summary: {e}", exc_info=True)
    #         return order_book_details  # Fallback to unmodified
