import asyncio
from decimal import Decimal
from Shared_Utils.config_manager import CentralConfig as Bot_config

class TrailingStopManager:
    _instance = None

    @classmethod
    def get_instance(cls, log_manager, order_type_manager, shared_utils_precision, market_data, coinbase_api):
        """
        Singleton method to ensure only one instance of TrailingStopManager exists.
        """
        if cls._instance is None:
            cls._instance = cls(log_manager, order_type_manager, shared_utils_precision, market_data, coinbase_api)
        return cls._instance

    def __init__(self, log_manager, order_type_manager, shared_utils_precision, market_data, coinbase_api):
        """
        Initializes the TrailingStopManager.
        """
        if TrailingStopManager._instance is not None:
            raise Exception("This class is a singleton! Use get_instance().")

        self.log_manager = log_manager
        self.coinbase_api = coinbase_api
        self.config = Bot_config()
        self._trailing_percentage = Decimal(self.config.trailing_percentage)
        self.order_type_manager = order_type_manager
        self.market_data = market_data
        self.shared_utils_precision = shared_utils_precision

        # Set the instance
        TrailingStopManager._instance = self


    @property
    def trailing_percentage(self):
        return Decimal(self._trailing_percentage)

    async def place_trailing_stop(self, order_data, order_book):
        """
        Places a trailing stop order based on the given order data and order book.

        Args:
            order_data (dict): Contains details of the filled order.
            order_book (dict): Contains order book details.

        Returns:
            tuple: (order_id, trailing_stop_price)
        """
        try:
            adjusted_price, adjusted_size = self.shared_utils_precision.adjust_price_and_size(order_data, order_book)
            trailing_stop_price = adjusted_price * (1 - self.trailing_percentage / 100)

            response = await self.order_type_manager.place_trailing_stop_order(order_book, order_data, adjusted_price)

            if response:
                response_data, limit_price, stop_price = response  # Unpack tuple

                if response_data.get("success"):  # Now it's correctly accessing the dictionary
                    order_id = response_data["order_id"]
                    self.log_manager.info(f"Trailing stop order placed: {order_id}")
                    return order_id, trailing_stop_price
                else:
                    self.log_manager.error(f"Failed to place trailing stop order: {response_data.get('failure_reason')}")
                    return None, None
            else:
                return None, None
        except Exception as e:
            self.log_manager.error(f"Error placing trailing stop: {e}", exc_info=True)
            return None, None

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
            base_deci = order_data.get('base_decimal')
            quote_deci = order_data.get('quote_decimal')
            trailing_stop_price = highest_price * (1 - self.trailing_percentage / 100)
            trailing_stop_price = self.shared_utils_precision.adjust_precision(base_deci, quote_deci, trailing_stop_price, convert='quote')
            limit_price = trailing_stop_price * Decimal("1.002")
            limit_price = self.shared_utils_precision.adjust_precision(base_deci, quote_deci, limit_price, convert='quote')

            asset = symbol.split('/')[0]
            amount = required_prices.get('balance',0.0)

            payload = await self.order_type_manager.update_order_payload(order_id, symbol, trailing_stop_price, limit_price, amount)
            response = await self.coinbase_api.update_order(payload)
            if response.get("success"):
                order_tracker[order_id].update({
                    "trailing_stop_price": trailing_stop_price,
                    "limit_price": limit_price
                })
                self.log_manager.info(f"Trailing stop updated for order {order_id}")
            else:
                self.log_manager.error(f"Failed to update trailing stop for {order_id}: {response.get('failure_reason')}", exc_info=True)
        except Exception as e:
            self.log_manager.error(f"Error updating trailing stop for order {order_id}: {e}", exc_info=True)



