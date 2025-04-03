
import pandas as pd


class TradeBotUtils:
    _instance = None

    @classmethod
    def get_instance(cls, logger_manager, coinbase_api, exchange_client, ccxt_api, alerts):
        """
        Singleton method to ensure only one instance of TradeBotUtils exists.
        """
        if cls._instance is None:
            cls._instance = cls(logger_manager, coinbase_api, exchange_client, ccxt_api, alerts)
        return cls._instance

    def __init__(self, logger_manager, coinbase_api, exchange_client, ccxt_api, alerts):
        """
        Initializes the TradeBotUtils.
        """
        self.exchange = exchange_client
        self.coinbase_api = coinbase_api
        self.logger = logger_manager.get_logger("webhook_logger")


        self.ccxt_api = ccxt_api
        self.alerts = alerts
        self.start_time = self.ticker_cache = self.non_zero_balances = None
        self.order_tracker = self.current_prices = self.market_cache_vol = None


    def set_trade_parameters(self, market_data, order_management, start_time=None):

        self.start_time = start_time
        # Safely access keys in market_data
        self.ticker_cache = market_data.get('ticker_cache', None)
        self.non_zero_balances = order_management.get('non_zero_balances', {})
        self.order_tracker = order_management.get('order_tracker', {})
        self.current_prices = market_data.get('current_prices', {})
        self.market_cache_vol = market_data.get('market_cache_filtered_vol', None)

    @staticmethod
    async def format_open_orders(open_orders: list) -> pd.DataFrame:
        """
        Format the open orders data received from the ccxt api(Coinbase Cloud) call.
        Parameters:
        Returns:
        - list: A list of dictionaries containing the required data.
        """

        data_to_load = [{
            'order_id': order['id'],
            'product_id': order['info']['product_id'],
            'side': order['info']['side'],
            'size': order['amount'],
            'price': order['price'],
            'trigger_status': order['info']['trigger_status'],
            'trigger_price': order['triggerPrice'],
            'stop_price': order['stopPrice'],
            'filled': order['filled'],
            'remaining': order['remaining'],
            'time active': order['info']['created_time']
        } for order in open_orders]
        df = pd.DataFrame(data_to_load)

        return df

