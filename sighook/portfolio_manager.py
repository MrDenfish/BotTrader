
import asyncio
from decimal import Decimal
from decimal import ROUND_DOWN, InvalidOperation

import pandas as pd

from Config.config_manager import CentralConfig


class PortfolioManager:
    """ Manages portfolio-related tasks such as market data caching and order evaluation. """

    _instance = None  # Singleton instance

    @classmethod
    def get_instance(cls, logmanager, ccxt_api, exchange, max_concurrent_tasks,
                     shared_utils_precision, shared_utils_datas_and_times, shared_utils_utility):
        """ Ensures only one instance of PortfolioManager is created. """
        if cls._instance is None:
            cls._instance = cls(logmanager, ccxt_api, exchange, max_concurrent_tasks,
                                shared_utils_precision, shared_utils_datas_and_times, shared_utils_utility)
        return cls._instance

    def __init__(self, logmanager, ccxt_api, exchange, max_concurrent_tasks,
                 shared_utils_precision, shared_utils_datas_and_times, shared_utils_utility):
        """ Initializes the PortfolioManager instance. """

        # Ensure singleton enforcement
        if PortfolioManager._instance is not None:
            raise Exception("This class is a singleton! Use get_instance() instead.")

        self.app_config = CentralConfig()

        # Config-based trading parameters
        self._buy_rsi = self.app_config._rsi_buy
        self._sell_rsi = self.app_config._rsi_sell
        self._buy_ratio = self.app_config._buy_ratio
        self._sell_ratio = self.app_config._sell_ratio
        self._min_volume = Decimal(self.app_config.min_volume)
        self._roc_buy_24h = Decimal(self.app_config.roc_buy_24h)
        self._roc_sell_24h = Decimal(self.app_config.roc_sell_24h)

        # External dependencies
        self.exchange = exchange
        self.ccxt_api = ccxt_api
        self.log_manager = logmanager
        self.shared_utils_precision = shared_utils_precision
        self.shared_utils_datas_and_times = shared_utils_datas_and_times
        self.shared_utils_utility = shared_utils_utility

        # Internal state
        self.ticker_cache = None
        self.market_cache_usd = None
        self.market_cache_vol = None
        self.start_time = None
        self.rate_limit = 0.15  # Initial rate limit in seconds (150 ms)

        # Concurrency control
        self.semaphore = asyncio.Semaphore(max_concurrent_tasks)

    def __repr__(self):
        """ Returns a string representation of the PortfolioManager instance. """
        return f"<PortfolioManager(exchange={self.exchange}, rate_limit={self.rate_limit})>"


    def set_trade_parameters(self, start_time, market_data, order_management):
        self.start_time = start_time
        self.ticker_cache = market_data['ticker_cache'] # based on vol and usd pairs
        self.non_zero_balances = order_management['non_zero_balances']
        self.market_cache_vol = market_data['filtered_vol']
        self.market_cache_usd = market_data['usd_pairs_cache']
        self.min_volume = Decimal(market_data['avg_quote_volume'])

    @property
    def buy_rsi(self):
        return self._buy_rsi

    @property
    def sell_rsi(self):
        return self._sell_rsi

    @property
    def buy_ratio(self):
        return self._buy_ratio

    @property
    def sell_ratio(self):
        return self._sell_ratio

    @property
    def roc_buy_24h(self):
        return int(self._roc_buy_24h)

    @property
    def roc_sell_24h(self):
        return int(self._roc_sell_24h)

    def filter_ticker_cache_matrix(self, buy_sell_matrix):
        """PART II: Trade Database Updates and Portfolio Management
        Filter ticker cache by volume > 1 million and price change > roc_sell_24h %. """
        filtered_ticker_cache = pd.DataFrame()
        #  Extract list of unique cryptocurrencies from buy_sell_matrix
        if not buy_sell_matrix.empty:
            unique_coins = buy_sell_matrix['asset'].unique()
            # Filter rows where base_currency is in the list of unique_coins
            df = self.ticker_cache[self.ticker_cache['asset'].isin(unique_coins)]
        else:
            df = self.ticker_cache
            df = df[df['asset'] != 'USD'] # remove USD/USD pair
        return df

    def get_portfolio_data(self, start_time, threshold=0.01):
        """Part II: Retrieve portfolio data for a given start time and threshold.
            - Preprocess ticker cache and remove duplicates
            - Calculate average dollar volume
            - Generate rows to add to buy/sell matrix
            - Create buy/sell matrix
            - Process USD pairs and portfolio
            - Format portfolio data into a structured output"""

        try:
            # Validate ticker cache and preprocess
            if not self._is_ticker_cache_valid():
                return [], [], 0, pd.DataFrame(), pd.DataFrame()
            # Preprocess ticker cache and remove duplicates and irrelevant data
            # self.ticker_cache = self._preprocess_and_deduplicate_ticker_cache()

            # Generate rows to add to buy/sell matrix
            rows_to_add = self._generate_buy_sell_rows()

            # Create buy/sell matrix
            buy_sell_matrix = self._create_buy_sell_matrix(rows_to_add)

            # Process USD pairs and portfolio
            usd_pairs = self.market_cache_usd
            portfolio_df = self._process_portfolio(threshold)

            # Format portfolio data into a structured output

            holdings = self._format_portfolio(portfolio_df) # derived from MarketDataManager['market_cache_filtered_usd']
            return holdings, usd_pairs, buy_sell_matrix, rows_to_add

        except Exception as e:
            self.log_manager.error(f"Error in get_portfolio_data: {e}", exc_info=True)
            return [], [], 0, pd.DataFrame(), pd.DataFrame()

    # Supporting Methods

    def _is_ticker_cache_valid(self):
        """ PART II:
        Check if the ticker cache is valid and not empty."""
        return self.ticker_cache is not None and not self.ticker_cache.empty

    def _generate_buy_sell_rows(self):
        """Part II: Create rows to add to the buy/sell matrix from the ticker cache."""
        rows = self.ticker_cache.apply(self._create_row, axis=1).tolist()
        df = pd.DataFrame(rows)
        self._initialize_buy_sell_columns(df)
        return df

    def _initialize_buy_sell_columns(self, df):
        """
        Part II: Add and initialize columns for buy and sell signals in 'buy_sell_matrix'.
        Uses structured tuples: (0/1, computed_value, threshold).
        """
        buy_sell_columns = {
            'Buy Ratio': self.buy_ratio, 'Buy Touch': None, 'W-Bottom': None, 'Buy RSI': self.buy_rsi,
            'Buy ROC': self.roc_buy_24h, 'Buy MACD': 0, 'Buy Swing': None, 'Sell Ratio': self.sell_ratio,
            'Sell Touch': None, 'M-Top': None, 'Sell RSI': self.sell_rsi, 'Sell ROC': self.roc_sell_24h,
            'Sell MACD': 0, 'Sell Swing': None, 'Buy Signal': 0, 'Sell Signal': 0
        }

        for column, threshold in buy_sell_columns.items():
            df[column] = df.apply(lambda _: (0, None, threshold), axis=1)

    def _create_buy_sell_matrix(self, rows_to_add):
        """Part II: Create the buy/sell matrix based on price change and volume."""
        try:
            if 'quote volume' in rows_to_add.columns and 'price change %' in rows_to_add.columns:
                # Create a copy of the DataFrame to avoid SettingWithCopyWarning
                rows_to_add = rows_to_add.copy()

                # Convert 'quote volume' and 'price change %' to numeric
                rows_to_add['quote volume'] = pd.to_numeric(rows_to_add['quote volume'], errors='coerce')
                rows_to_add['price change %'] = pd.to_numeric(round(rows_to_add['price change %'],1), errors='coerce')

                # Drop rows with NaNs in the relevant columns
                rows_to_add = rows_to_add.dropna(subset=['quote volume', 'price change %'])

                # Fix: Apply absolute value only to 'price change %' and correctly structure conditions
                filtered_df = rows_to_add[
                    (abs(rows_to_add['price change %']) >= self.roc_sell_24h) &  # use the lowest  roc 24h value
                    (rows_to_add['quote volume'] >= self.min_volume)
                    ]

                return filtered_df  # Return filtered DataFrame

            return pd.DataFrame()  # Return empty DataFrame if required columns are missing

        except Exception as e:
            self.log_manager.error(f"_create_buy_sell_matrix: {e}", exc_info=True)
            return pd.DataFrame()

    def _process_portfolio(self, threshold=0.01):
        """Part II: Populate 'free' column and filter portfolio DataFrame by balance threshold."""
        try:
            # Safely convert threshold to Decimal
            try:
                threshold = Decimal(str(threshold))
            except (ValueError, TypeError, InvalidOperation):
                raise ValueError(f"Invalid threshold value: {threshold}")

            # Ensure the relevant columns exist
            if 'free' not in self.market_cache_usd or 'price' not in self.market_cache_usd:
                self.log_manager.error("Missing required columns in market_cache_usd: 'free' or 'price'")
                return pd.DataFrame()

            # Populate the 'free' column from non_zero_balances
            self.market_cache_usd['free'] = self.market_cache_usd['asset'].map(
                self._get_tradeable_crypto_mapping(self.non_zero_balances)).fillna(0)

            # Handle non-numeric or NaN values in 'free' and 'price' columns
            self.market_cache_usd['free'] = pd.to_numeric(self.market_cache_usd['free'], errors='coerce')
            self.market_cache_usd['price'] = pd.to_numeric(self.market_cache_usd['price'], errors='coerce')

            # Calculate balance safely
            self.market_cache_usd['balance'] = (
                    self.market_cache_usd['free'].fillna(0) * self.market_cache_usd['price'].fillna(0)
            )

            # Filter rows with balance above the threshold
            return self.market_cache_usd[self.market_cache_usd['balance'] > threshold]

        except Exception as e:
            self.log_manager.error(f"_process_portfolio: {e}", exc_info=True)
            return pd.DataFrame()

    def _format_portfolio(self, portfolio_df):
        """Part II: Format portfolio DataFrame into a dictionary list."""

        if not isinstance(portfolio_df, pd.DataFrame):
            portfolio_df = pd.DataFrame()
        portfolio_df = portfolio_df.sort_values(by='symbol', ascending=True) if not portfolio_df.empty else pd.DataFrame()
        return portfolio_df.to_dict('records')



    @staticmethod
    def _create_row(row):
        """Part II: Generate a row for the buy/sell matrix."""
        price_decimal = Decimal(row['price']).quantize(Decimal('0.00000001'), rounding=ROUND_DOWN)
        return {
            'asset': row['asset'],
            'price': price_decimal,
            'base volume': row['volume_24h'],
            'quote volume': row['24h_quote_volume'],
            'price change %': Decimal(row['price_percentage_change_24h'])
        }

    def _get_tradeable_crypto_mapping(self, non_zero_balances):
        """PART II:
        Create a mapping of asset to available_to_trade_crypto from non_zero_balances."""
        try:
            return {
                balance_data['asset']: balance_data['available_to_trade_crypto']
                for balance_data in non_zero_balances.values()
            }
        except Exception as e:
            self.log_manager.error(f"Error creating tradeable crypto mapping: {e}", exc_info=True)
            return {}









