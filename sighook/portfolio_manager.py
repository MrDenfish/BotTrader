
import asyncio
import pandas as pd
from datetime import timedelta
from decimal import Decimal, ROUND_DOWN


class PortfolioManager:
    def __init__(self, utility, logmanager, ccxt_api, exchange, max_concurrent_tasks, app_config):
        self._min_volume = Decimal(app_config.min_volume)
        self.exchange = exchange
        self.ledger_cache = None
        self.log_manager = logmanager
        self.ccxt_exceptions = ccxt_api
        self.utility = utility
        self.ticker_cache = None
        self.market_cache = None
        self.start_time = None
        self.semaphore = asyncio.Semaphore(max_concurrent_tasks)

    def set_trade_parameters(self, start_time, ticker_cache, market_cache):
        self.start_time = start_time
        self.ticker_cache = ticker_cache
        self.market_cache = market_cache

    @property
    def min_volume(self):
        return self._min_volume

    async def get_my_trades(self, symbol, last_update_time):
        """PART I: Data Gathering and Database Loading. Process a single symbol's market data.
        Fetch trades for a single symbol up to the last update time."""
        try:
            adjusted_time = last_update_time + timedelta(milliseconds=99)
            since_unix = self.utility.time_unix(adjusted_time.strftime("%Y-%m-%d %H:%M:%S.%f")) \
                if last_update_time else None
            # since_unix = self.utility.time_unix(adjusted_time)
            my_trades = await self.fetch_trades_for_symbol_with_rate_limit(symbol, since=since_unix)
            return my_trades
        except Exception as e:
            self.log_manager.sighook_logger.error(f"Error fetching trades for {symbol}: {e}", exc_info=True)
            return []

    async def fetch_trades_for_symbol_with_rate_limit(self, symbol, since):
        """Fetch Trades data with rate limiting to avoid hitting API limits."""

        rate_limit = 150/1000  # private API requests per second per IP: 15
        await asyncio.sleep(rate_limit)
        return await self.fetch_trades_for_symbol(symbol, since)

    async def fetch_trades_for_symbol(self, symbol, since=None):
        """PART I: Data Gathering and Database Loading. Fetch trades for a single symbol."""
        try:
            my_trades = []
            # Parameters for the API call
            params = {'paginate': True, 'paginationCalls': 20}
            endpoint = 'private'  # For rate limiting
            if symbol is not None:
                my_trades = await self.ccxt_exceptions.ccxt_api_call(self.exchange.fetch_my_trades, endpoint, symbol=symbol,
                                                                     since=since,  params=params)
            else:
                self.log_manager.sighook_logger.error(f"Symbol is None. Unable to fetch trades.")
            if my_trades:
                # Convert the 'datetime' from ISO format to a Python datetime object
                for trade in my_trades:
                    if 'datetime' in trade and trade['datetime']:
                        trade['datetime'] = self.utility.standardize_timestamp(trade['datetime'])
            return my_trades
        except Exception as e:
            self.log_manager.sighook_logger.error(f"Error fetching trades for {symbol}: {e}", exc_info=True)
            return []

    def get_portfolio_data(self, start_time, threshold=0.01):
        """PART II: Trade Database Updates and Portfolio Management"""
        usd_pairs = []
        ticker_cache_avg_dollar_vol = 0.0
        price_change, df = pd.DataFrame(), pd.DataFrame()

        try:
            # Early exit if ticker_cache is empty
            if self.ticker_cache is None or self.ticker_cache.empty:
                return [], [], 0, pd.DataFrame(), pd.DataFrame()
            price_change = None
            # Preprocessing ticker_cache
            self.ticker_cache = self._preprocess_ticker_cache()
            self.ticker_cache = self.ticker_cache.drop_duplicates(subset='asset')
            # Calculate average dollar volume total
            ticker_cache_avg_dollar_vol = self.ticker_cache['quote_vol_24h'].mean() if not self.ticker_cache.empty \
                else 0

            # Generate rows_to_add from ticker_cache
            rows_list = self.ticker_cache.apply(self._create_row, axis=1).tolist()
            rows_to_add = pd.DataFrame(rows_list)
            rows_to_add['Buy Ratio'] = False
            rows_to_add['Buy Touch'] = False
            rows_to_add['W-Bottom Signal'] = False
            rows_to_add['Buy RSI'] = False
            rows_to_add['Buy ROC'] = False
            rows_to_add['Buy MACD'] = False
            rows_to_add['Buy Swing'] = False
            rows_to_add['Buy Signal'] = ''  # string
            rows_to_add['Sell Ratio'] = False
            rows_to_add['Sell Touch'] = False
            rows_to_add['M-Top Signal'] = False
            rows_to_add['Sell RSI'] = False
            rows_to_add['Sell ROC'] = False
            rows_to_add['Sell MACD'] = False
            rows_to_add['Sell Swing'] = False
            rows_to_add['Sell Signal'] = ''  # string
            # Ensure 'quote volume' column exists in rows_to_add
            if 'quote volume' in rows_to_add.columns and 'price change %' in rows_to_add.columns:
                rows_to_add['price change %'] = pd.to_numeric(rows_to_add['price change %'], errors='coerce')
                rows_to_add['price change %'] = rows_to_add['price change %'].fillna(0)
                # dataframe of coins with 2.1% or greater price change
                price_change = rows_to_add[rows_to_add['price change %'] >= Decimal('2.1')]
                # dataframe of coins with 2.1% or greater price change and quote volume greater than 1 million
                buy_sell_matrix = rows_to_add[(rows_to_add['price change %'] >= 2.1) &
                                              (rows_to_add['quote volume'] >= min(ticker_cache_avg_dollar_vol,
                                                                                  self.min_volume))]

            else:
                buy_sell_matrix = pd.DataFrame()  # If the column doesn't exist, create an empty DataFrame
            buy_sell_matrix = self.format_buy_sell_matrix(buy_sell_matrix)
            # Generate usd_pairs and process portfolio DataFrame
            usd_pairs = self._get_usd_pairs(self.ticker_cache)
            df = self._process_portfolio(threshold)

            # Ensure portfolio_df is a DataFrame
            if not isinstance(df, pd.DataFrame):
                df = pd.DataFrame()  # Convert to empty DataFrame if it's not already a DataFrame
                df = self._process_portfolio(threshold)  # filter coins with less than $0.1USD value
            # Handle the case when a specific symbol is provided
            portfolio_df = df.sort_values(by='quote_currency', ascending=True) if df is not None else pd.DataFrame()
            # load
            holdings = portfolio_df.to_dict('records')

            return (holdings, usd_pairs, ticker_cache_avg_dollar_vol,
                    buy_sell_matrix, price_change)

        except Exception as e:
            self.log_manager.sighook_logger.error(f'get_portfolio_data df:{df}  {e}', exc_info=True)

    def _preprocess_ticker_cache(self):  # pull 24hr volume
        """PART II: Trade Database Updates and Portfolio Management"""
        try:
            df = self.ticker_cache.dropna(subset=['free'])
            # Ensure 'info' is a dict and 'volume_24h' is not an empty string, '0', or None
            df = df[df['info'].apply(lambda x: isinstance(x, dict) and x.get('volume_24h') not in ['', '0', None])]
            # extract 'volume_24h' from 'info' and multiply by 'ask', ensuring 'info' is a dict
            df['quote_vol_24h'] = df.apply(lambda row: float(row['info'].get('volume_24h', 0)) * float(row['info'].get(
                'price', 0)) if isinstance(row['info'], dict) else 0, axis=1)
            df['price'] = df.apply(lambda row: float(row['info'].get('price', 0))
                                   if isinstance(row['info'], dict) else 0, axis=1)
            # clean up the dataframe removing unnecessary columns
            df = df.dropna(axis='columns', how='all')
            self.ticker_cache = df
            return df
        except Exception as e:
            self.log_manager.sighook_logger.error(f'_preprocess_ticker_cache: {e}', exc_info=True)

    @staticmethod
    def _create_row(row):
        """PART II: Trade Database Updates and Portfolio Management"""

        price_decimal = Decimal(row['price']).quantize(Decimal('0.00000001'), rounding=ROUND_DOWN)
        balance_decimal = Decimal(row['free']).quantize(Decimal('0.00000001'), rounding=ROUND_DOWN)
        return {'coin': row['asset'], 'price': price_decimal, 'base volume': row['info']['volume_24h'],
                'quote volume': row['quote_vol_24h'], 'price change %': row['info']['price_percentage_change_24h']}

    @staticmethod
    def format_buy_sell_matrix(buy_sell_matrix):
        """PART II: Trade Database Updates and Portfolio Management"""

        # Format 'price' and 'price change %' to 2 decimal places
        buy_sell_matrix.loc[:, 'price'] = buy_sell_matrix['price'].astype(float).round(2)
        buy_sell_matrix.loc[:, 'price change %'] = buy_sell_matrix['price change %'].astype(float).round(2)

        # Format 'base volume' and 'quote volume' to 0 decimal places and convert to int
        buy_sell_matrix.loc[:, 'base volume'] = buy_sell_matrix['base volume'].astype(float).round(0).astype(int)
        buy_sell_matrix.loc[:, 'quote volume'] = buy_sell_matrix['quote volume'].round(0).astype(int)
        return buy_sell_matrix

    @staticmethod
    def _get_usd_pairs(df):
        """PART II: Trade Database Updates and Portfolio Management"""

        return df[df['free'] == 0].apply(lambda x: {'id': f"{x['asset']}-USD", 'price': x['price']}, axis=1).tolist()

    def _process_portfolio(self, threshold):
        """PART II: Trade Database Updates and Portfolio Management"""

        # Use a list comprehension to collect dictionaries
        data = [self._check_portfolio(row, threshold) for _, row in self.ticker_cache.iterrows()]
        # Filter out None values and create a DataFrame
        filtered_data = [d for d in data if d is not None]
        return pd.DataFrame(filtered_data)

    @staticmethod
    def _check_portfolio(row, threshold):
        """PART II: Trade Database Updates and Portfolio Management"""

        balance_decimal = Decimal(row['total']).quantize(Decimal('0.00000001'), rounding=ROUND_DOWN)
        price_decimal = Decimal(row['price']).quantize(Decimal('0.00000001'), rounding=ROUND_DOWN)
        if balance_decimal * price_decimal > Decimal(threshold):
            return {'asset': row['asset'], 'quote_currency': row['quote'], 'Balance': balance_decimal}
        return None

    def filter_ticker_cache_matrix(self, buy_sell_matrix):
        """PART II: Trade Database Updates and Portfolio Management
        Filter ticker cache by volume > 1 million and price change > 2.1% """

        #  Extract list of unique cryptocurrencies from buy_sell_matrix
        unique_coins = buy_sell_matrix['coin'].unique()
        # Filter ticker_cache to contain only rows with symbols in unique_coins The 'symbol' column in ticker_cache needs
        # self.ticker_cache['base_currency'] = self.ticker_cache['asset'].apply(lambda x: x.split('/')[0])
        # Filter rows where base_currency is in the list of unique_coins
        filtered_ticker_cache = self.ticker_cache[self.ticker_cache['asset'].isin(unique_coins)]
        return filtered_ticker_cache

    async def fetch_wallets(self):
        """PART VI: Profitability Analysis and Order Generation
        update ticker cache with wallet holdings and available balance."""

        endpoint = 'private'
        params = {'paginate': True, 'paginationCalls': 100}
        wallets = await self.ccxt_exceptions.ccxt_api_call(self.exchange.fetch_accounts, endpoint, params)
        filtered_wallets = self.filter_non_zero_wallets(wallets)
        return self.update_ticker_cache(filtered_wallets)

    # Function to filter wallets with non-zero balance
    def filter_non_zero_wallets(self, wallets):
        try:
            non_zero_wallets = []
            for wallet in wallets:
                if wallet['code'] == 'MNDE':
                    pass
                available_balance = Decimal(wallet['info']['available_balance']['value'])
                hold_balance = Decimal(wallet['info']['hold']['value'])
                total_balance = available_balance + hold_balance
                if total_balance > 0:
                    non_zero_wallets.append(wallet)
            return non_zero_wallets
        except Exception as e:
            self.log_manager.sighook_logger.error(f'filter_non_zero_wallets: {e}', exc_info=True)

    # Function to update DataFrame with balances
    def update_ticker_cache(self, wallets, threshold=0.01):
        try:
            for wallet in wallets:
                currency = wallet['info']['currency']
                available_balance = Decimal(wallet['info']['available_balance']['value'])
                hold_balance = Decimal(wallet['info']['hold']['value'])
                total_balance = available_balance + hold_balance
                df = self.ticker_cache
                # Check if the asset exists in the DataFrame
                if not df[df['asset'] == currency].empty:
                    df.loc[df['asset'] == currency, 'free'] = available_balance
                    df.loc[df['asset'] == currency, 'total'] = total_balance

                self.ticker_cache = df

            df = self._process_portfolio(threshold)

            # Ensure portfolio_df is a DataFrame
            if not isinstance(df, pd.DataFrame):
                df = pd.DataFrame()  # Convert to empty DataFrame if it's not already a DataFrame
                df = self._process_portfolio(threshold)  # filter coins with less than $0.1USD value
            # Handle the case when a specific symbol is provided
            portfolio_df = df.sort_values(by='quote_currency', ascending=True) if df is not None else pd.DataFrame()
            wallets = portfolio_df.to_dict('records')
            return wallets
        except Exception as e:
            self.log_manager.sighook_logger.error(f'update_ticker_cache: {e}', exc_info=True)
