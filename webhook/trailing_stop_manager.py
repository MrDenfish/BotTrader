
from decimal import Decimal

from Config.config_manager import CentralConfig as Bot_config


class TrailingStopManager:
    _instance = None

    @classmethod
    def get_instance(cls, logger_manager, shared_utils_precision, coinbase_api, shared_data_manager, order_type_manager):
        """
        Singleton method to ensure only one instance of TrailingStopManager exists.
        """
        if cls._instance is None:
            cls._instance = cls(logger_manager, shared_utils_precision, coinbase_api, shared_data_manager, order_type_manager)
        return cls._instance

    def __init__(self, logger_manager, shared_utils_precision, coinbase_api, shared_data_manager, order_type_manager):
        """
        Initializes the TrailingStopManager.
        """
        if TrailingStopManager._instance is not None:
            raise Exception("This class is a singleton! Use get_instance().")

        self.logger = logger_manager  # 🙂

        self.shared_data_manager = shared_data_manager
        self.order_type_manager = order_type_manager
        self.coinbase_api = coinbase_api
        self.config = Bot_config()
        self._trailing_percentage = Decimal(self.config.trailing_percentage)

        self.shared_utils_precision = shared_utils_precision

        # Set the instance
        TrailingStopManager._instance = self

    @property
    def market_data(self):
        return self.shared_data_manager.market_data

    @property
    def order_management(self):
        return self.shared_data_manager.order_management

    @property
    def ticker_cache(self):
        return self.shared_data_manager.market_data.get('ticker_cache')

    @property
    def non_zero_balances(self):
        return self.shared_data_manager.order_management.get('non_zero_balances')

    @property
    def market_cache_vol(self):
        return self.shared_data_manager.market_data.get('filtered_vol')

    @property
    def market_cache_usd(self):
        return self.shared_data_manager.market_data.get('usd_pairs_cache')

    @property
    def bid_ask_spread(self):
        return self.shared_data_manager.market_data.get('bid_ask_spread')

    @property
    def open_orders(self):
        return self.shared_data_manager.order_management.get("order_tracker")


    @property
    def avg_quote_volume(self):
        return Decimal(self.shared_data_manager.market_data['avg_quote_volume'])

    @property
    def trailing_percentage(self):
        return Decimal(self._trailing_percentage)

    async def place_trailing_stop(self, order_data, order_book):
        print(f"‼️ NOT IMPLEMENTED YET ‼️")
        """
        Places a trailing stop order based on the given order data and order book.

        Args:
            order_data (dict): Contains details of the filled order.
            order_book (dict): Contains order book details.

        Returns:
            tuple: (order_id, trailing_stop_price)
        """

    async def update_trailing_stop(self, order_id, symbol, highest_price, order_tracker, required_prices, order_data):
        """
        Updates the trailing stop order if the current price exceeds the highest price.

        Args:
            order_id (str): ID of the order to update.
            symbol (str): Trading pair (e.g., 'BTC/USD').
            highest_price (Decimal): Current highest price.
            order_tracker (dict): Reference to the master order tracker.

        Returns:
            None
        """
        try:

            base_deci = order_data.base_decimal
            quote_deci = order_data.quote_decimal
            trailing_stop_price = highest_price * (1 - self.trailing_percentage / 100)
            trailing_stop_price = self.shared_utils_precision.adjust_precision(base_deci, quote_deci, trailing_stop_price, convert='quote')
            limit_price = trailing_stop_price * Decimal("1.002")
            limit_price = self.shared_utils_precision.adjust_precision(base_deci, quote_deci, limit_price, convert='quote')

            asset = symbol.split('/')[0]
            amount = required_prices.get('asset_balance', 0.0)

            payload = await self.order_type_manager.update_order_payload(order_id, symbol, trailing_stop_price, limit_price, amount)
            response = await self.coinbase_api.update_order(payload)
            if response.get("success"):
                order_tracker[order_id].update({
                    "trailing_stop_price": trailing_stop_price,
                    "limit_price": limit_price
                })
                self.logger.info(f"Trailing stop updated for order {order_id}")
            else:
                self.logger.error(f"Failed to update trailing stop for {order_id}: {response.get('failure_reason')}", exc_info=True)
        except Exception as e:
            self.logger.error(f"Error updating trailing stop for order {order_id}: {e}", exc_info=True)
