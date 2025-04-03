

from decimal import Decimal

import pandas as pd
from databases import Database

from Config.config_manager import CentralConfig
from Shared_Utils.precision import PrecisionUtils


class DatabaseOpsManager:
    _instance = None

    @classmethod
    def get_instance(cls, exchange, ccxt_api, logger_manager, profit_extras, portfolio_manager,
                     holdings_manager, database: Database, db_tables, profit_data_manager, snapshots_manager):
        if cls._instance is None:
            cls._instance = cls(exchange, ccxt_api, logger_manager, profit_extras, portfolio_manager,
                                holdings_manager, database, db_tables, profit_data_manager, snapshots_manager)
        return cls._instance

    def __init__(self, exchange, ccxt_api, logger_manager, profit_extras, portfolio_manager,
                 holdings_manager, database: Database, db_tables, profit_data_manager, snapshots_manager):

        self.exchange = exchange
        self.ccxt_api = ccxt_api
        self.logger = logger_manager
        self.profit_extras = profit_extras
        self.app_config = CentralConfig()
        self.shill_coins = self.app_config.shill_coins
        self.shared_utils_precision = PrecisionUtils.get_instance(logger_manager, market_data=None)
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
        self.start_time = self.market_data = self.ticker_cache = self.current_prices = None
        self.usd_pairs = self.market_cache_vol = self.filtered_balances = self.holdings_list = None

    def set_trade_parameters(self, start_time, market_data, order_management):
        self.start_time = start_time
        self.market_data = market_data
        self.ticker_cache = market_data['ticker_cache']
        self.current_prices = market_data['current_prices']
        self.usd_pairs = market_data.get('usd_pairs_cache', {})  # usd pairs
        self.market_cache_vol = market_data['filtered_vol']  # usd pairs with min volume
        self.filtered_balances = order_management['non_zero_balances']
        self.holdings_list = market_data['spot_positions']


    @staticmethod
    def _batch_tasks(tasks, batch_size):
        """
        Split a list of tasks into smaller batches.
        """
        for i in range(0, len(tasks), batch_size):
            yield tasks[i:i + batch_size]

    async def process_data(self):
        """Process MarketDataManager and update the database. PART I: Data Gathering and Database Loading.
    #     This method handles processing MarketDataManager and updating the database"""
        try:
            async with self.database.transaction():

                if not self.market_cache_vol: # no matching data, list is empty
                    self.logger.info("No MarketDataManager available to process.")
                    return
                await self.process_market_data()

                self.logger.debug("Data processed successfully.")
        except Exception as e:
            self.logger.error(f"❌Failed to process data: {e}")
            raise

    async def process_market_data(self):
        """
        Process MarketDataManager for real-time profitability using live balances and current prices.
        """
        try:
            profit_data_list = []
            self.logger.info("Starting MarketDataManager processing...")

            # Iterate over real-time filtered balances
            if not self.filtered_balances:
                self.logger.warning("Filtered balances are empty. No data will be processed.", exc_info=True)
                return
            for asset, balance_data in self.filtered_balances.items():
                # Pass each asset to process_symbol() for profitability calculations
                if asset not in self.shill_coins:
                    result = await self.process_symbol(asset, profit_data_list)
                    if not isinstance(result, (int, float, Decimal)):
                        self.logger.warning(f"Failed to process {asset}: "
                                                 f"Unexpected result type or value ({result})", exc_info=True)
            profit_df = self.profit_data_manager.consolidate_profit_data(profit_data_list)
            print(profit_df.to_string(index=True))
        except Exception as e:
            self.logger.error(f"❌Error in process_market_data: {e}", exc_info=True)

    async def process_symbol(self, asset, profit_data_list):
        """
        Calculate profitability for a single asset using real-time balances and market prices.

        Args:
            asset (str): Asset symbol (e.g., 'BTC', 'ETH').
            profit_data_list (list): List to store profitability details for all assets.

        Returns:
            dict: Profitability details for the asset or None if an error occurs.
        """
        try:
            # Fetch current price for the asset

            current_price = self.market_data['current_prices'].get(f"{asset}/USD")
            if current_price:
                base_deci, quote_deci,_,_ = self.shared_utils_precision.fetch_precision(asset,self.usd_pairs)
                current_price = self.shared_utils_precision.adjust_precision(base_deci, quote_deci, current_price, 'base')
                if current_price is None:
                    raise ValueError(f"Current price for {asset} not available.")

                # Extract balance details
                asset_balance = Decimal(self.holdings_list.get(asset, {}).get('total_balance_crypto', {}))
                total_balance = self.shared_utils_precision.adjust_precision(base_deci, quote_deci, asset_balance, 'base')

                # Calculate current value and profitability
                avg_entry_price = Decimal(self.holdings_list.get(asset, {}).get('average_entry_price', {}).get('value', 0))
                cost_basis = Decimal(self.holdings_list.get(asset, {}).get('cost_basis', {}).get('value', 0))
                required_prices = {
                    'avg_price': avg_entry_price,
                    'cost_basis': cost_basis,
                    'asset_balance': total_balance,
                    'current_price': Decimal(current_price),
                    'profit': None,
                    'profit_percentage':None,


                }
                profit = await self.profit_data_manager.calculate_profitability(asset, required_prices,
                                                                                self.current_prices, self.usd_pairs)
                if profit:
                    profit_value = self.shared_utils_precision.adjust_precision(base_deci, quote_deci, profit.get('profit'),
                                                                           'quote')
                    if profit_value != 0.0:
                        profit_data_list.append(profit)
                        return profit_value
                    return Decimal(0.0)
                else:
                    return Decimal(0.0)
            else:
                if asset not in ['USD', 'USDC']:
                    print(f"Current price for {asset} is not available.")
                return{}
        except Exception as e:
            self.logger.error(f"❌Error processing {asset}: {e}", exc_info=True)
            return {}

