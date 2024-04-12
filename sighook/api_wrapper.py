class APIWrapper:
    _instance = None

    def __new__(cls, *args, **kwargs):  # Singleton pattern
        if not cls._instance:
            cls._instance = super(APIWrapper, cls).__new__(cls)
        return cls._instance

    def __init__(self, exchange, utility, portfolio_manager, ticker_manager, order_manager, market_metrics, ccxt_api):
        self.exchange = exchange
        self.utility = utility
        self.ccxt_exceptions = ccxt_api
        self.portfolio_manager = portfolio_manager
        self.ticker_manager = ticker_manager
        self.order_manager = order_manager
        self.market_metrics = market_metrics
        self.ticker_cache = None
        self.market_cache = None
        self.start_time = None
        self.web_url = None
        self.holdings = None

    # def set_trading_strategy(self, trading_strategy):
    #     self.trading_strategy = trading_strategy

    def set_trade_parameters(self, start_time, ticker_cache, market_cache, web_url, hist_holdings):
        self.start_time = start_time
        self.ticker_cache = ticker_cache
        self.market_cache = market_cache
        self.web_url = web_url
        self.holdings = hist_holdings

    async def get_open_orders(self, holdings: object, usd_pairs: object, fetch_all: object = True) -> object:  # async
        return await self.order_manager.get_open_orders(holdings, usd_pairs, fetch_all)  # await

    def get_portfolio_data(self, start_time, holdings, symbol=None, threshold=0.1):

        return self.portfolio_manager.get_portfolio_data(start_time, holdings, threshold)

    def get_filled_orders(self, product_id, counter):  # async
        return self.order_manager.get_filled_orders(product_id, counter)  # await

    # async def get_my_trades(self, symbol, since=0):
    #     return await self.portfolio_manager.get_my_trades(symbol)

    def fetch_total_supply(self, symbol):
        return self.market_metrics.fetch_total_supply(symbol)
