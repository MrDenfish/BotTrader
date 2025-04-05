
import pandas as pd


class TradeBotUtils:
    _instance = None

    @classmethod
    def get_instance(cls, logger_manager, coinbase_api, exchange_client, ccxt_api, alerts, shared_data_manager):
        """
        Singleton method to ensure only one instance of TradeBotUtils exists.
        """
        if cls._instance is None:
            cls._instance = cls(logger_manager, coinbase_api, exchange_client, ccxt_api, alerts, shared_data_manager)
        return cls._instance

    def __init__(self, logger_manager, coinbase_api, exchange_client, ccxt_api, alerts, shared_data_manager):
        """
        Initializes the TradeBotUtils.
        """
        self.exchange = exchange_client
        self.coinbase_api = coinbase_api
        self.logger = logger_manager.get_logger("webhook_logger")
        self.shared_data_manager = shared_data_manager


        self.ccxt_api = ccxt_api
        self.alerts = alerts
        self.start_time = None

    @property
    def market_data(self):
        return self.shared_data_manager.market_data

    @property
    def order_management(self):
        return self.shared_data_manager.order_management

    @property
    def ticker_cache(self):
        return self.market_data.get('ticker_cache')

    @property
    def non_zero_balances(self):
        return self.order_management.get('non_zero_balances')

    @property
    def market_cache_vol(self):
        return self.market_data.get('filtered_vol')

    @property
    def market_cache_usd(self):
        return self.market_data.get('usd_pairs_cache')

    @property
    def current_prices(self):
        return self.market_data.get('current_prices')

    @property
    def order_tracker(self):
        return self.order_management.get('order_tracker')

    @property
    def avg_quote_volume(self):
        return Decimal(self.market_data['avg_quote_volume'])

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

