import pandas as pd
from databases import Database

from Config.config_manager import CentralConfig


class DatabaseOpsManager:
    _instance = None

    @classmethod
    def get_instance(cls, exchange, ccxt_api, logger_manager, profit_extras, portfolio_manager, holdings_manager, database: Database, db_tables,
                     profit_data_manager, snapshots_manager, shared_utils_precision, shared_data_manager):
        if cls._instance is None:
            cls._instance = cls(exchange, ccxt_api, logger_manager, profit_extras, portfolio_manager, holdings_manager, database, db_tables,
                                profit_data_manager, snapshots_manager, shared_utils_precision, shared_data_manager)
        return cls._instance

    def __init__(self, exchange, ccxt_api, logger_manager, profit_extras, portfolio_manager, holdings_manager, database: Database, db_tables,
                 profit_data_manager, snapshots_manager, shared_utils_precision, shared_data_manager):

        self.exchange = exchange
        self.ccxt_api = ccxt_api
        self.logger = logger_manager  # 🙂
        self.profit_extras = profit_extras
        self.app_config = CentralConfig()
        self.shill_coins = self.app_config.shill_coins
        self.shared_utils_precision = shared_utils_precision
        self.shared_data_manager = shared_data_manager
        self.portfolio_manager = portfolio_manager
        self.holdings_manager = holdings_manager
        self.snapshot_manager = snapshots_manager
        self.profit_data_manager = profit_data_manager
        self.database = database  # Use `database` directly
        self.db_tables = db_tables
        self.df_market_cache_vol = pd.DataFrame()
        self.existing_transaction_types = {
            'advanced_trade_fill', 'buy', 'earn_payout', 'exchange_deposit',
            'exchange_withdrawal', 'fiat_deposit', 'pro_deposit', 'pro_withdrawal',
            'sell', 'send', 'staking_transfer', 'trade', 'tx', 'wrap_asset'
        }
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
    def current_prices(self):
        return self.market_data.get('current_prices')

    @property
    def usd_pairs(self):
        return self.market_data.get('usd_pairs_cache')

    @property
    def filtered_balances(self):
        return self.order_management.get('non_zero_balances')

    @property
    def market_cache_vol(self):
        return self.market_data.get('filtered_vol')

    @property
    def holdings_list(self):
        return self.market_data.get('spot_positions')



    @staticmethod
    def _batch_tasks(tasks, batch_size):
        """
        Split a list of tasks into smaller batches.
        """
        for i in range(0, len(tasks), batch_size):
            yield tasks[i:i + batch_size]



