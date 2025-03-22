from decimal import Decimal
from Config.config_manager import CentralConfig as Config

class ProfitHelper:
    _instance = None

    @classmethod
    def get_instance(cls, portfolio_manager, ticker_manager, database_manager, logmanager, profit_data_manager):
        if cls._instance is None:
            cls._instance = cls(portfolio_manager, ticker_manager, database_manager, logmanager, profit_data_manager)
        return cls._instance


    def __init__(self, portfolio_manager, ticker_manager, database_manager, logmanager, profit_data_manager):
        self.config = Config()
        self.portfolio_manager = portfolio_manager
        self.profit_data_manager = profit_data_manager
        self._take_profit = Decimal(self.config.take_profit)
        self._stop_loss = Decimal(self.config.stop_loss)
        self._trailing_percentage = Decimal(self.config.trailing_percentage)
        self._currency_pairs_ignored = self.config.currency_pairs_ignored
        self.ticker_manager = ticker_manager
        self.database_manager = database_manager
        self.log_manager = logmanager
        self.ticker_cache = self.market_cache_usd = self.market_cache_vol = None
        self.spot_positions = self.session = self.holdings = None
        self.market_cache = self.start_time = self.web_url = None


    def set_trade_parameters(self, start_time, market_data, web_url):
        self.start_time = start_time
        # self.session = session
        self.ticker_cache = market_data['ticker_cache']
        self.market_cache_usd = market_data['usd_pairs_cache']  # usd pairs
        self.market_cache_vol = market_data['filtered_vol']
        self.web_url = web_url
        self.spot_positions = market_data['spot_positions']


    @property
    def stop_loss(self):
        return self._stop_loss

    @property
    def take_profit(self):
        return self._take_profit

    @property
    def trailing_percentage(self):
        return self._trailing_percentage

    @property
    def currency_pairs_ignored(self):
        return self._currency_pairs_ignored

    def should_place_sell_order(self, holding, current_price):
        """ PART VI: Profitability Analysis and Order Generation  operates directly on a holding object (an instance from
        the Holdings table) and the current_market_price,
        making decisions based on the latest available data.  unrealized profit and its percentage are calculated
        dynamically within the function, ensuring decisions are based on real-time data."""
        try:
            if not holding or not current_price:
                return False
            unrealized_profit_pct = holding.get('unrealized_profit_pct', 0)

            # Decide to sell based on the calculated unrealized profit percentage
            return unrealized_profit_pct > self._take_profit or unrealized_profit_pct < self._stop_loss
        except Exception as e:
            self.log_manager.error(f"Error in should_place_sell_order: {e}", exc_info=True)
            return False




