from decimal import Decimal, ROUND_DOWN
import datetime
import json
import os
import pandas as pd
import traceback


class PortfolioManager:
    def __init__(self, utility, logmanager, ccxt_api, exchange):
        self.exchange = exchange
        self.ledger_cache = None
        self.log_manager = logmanager
        self.ccxt_exceptions = ccxt_api
        self.utility = utility
        self.ticker_cache = None
        self.market_cache = None
        self.start_time = None
        self.holdings = None

    def set_trade_parameters(self, start_time, ticker_cache, market_cache, hist_holdings):
        self.start_time = start_time
        self.ticker_cache = ticker_cache
        self.market_cache = market_cache
        self.holdings = hist_holdings

    def filter_ticker_cache_matrix(self, buy_sell_matrix):
        """ Filter ticker cache by volume > 1 million and price change > 2.1% """
        #  Extract list of unique cryptocurrencies from buy_sell_matrix
        unique_coins = buy_sell_matrix['coin'].unique()
        #  Filter ticker_cache to contain only rows with symbols in unique_coins The 'symbol' column in ticker_cache needs
        self.ticker_cache['base_currency'] = self.ticker_cache['symbol'].apply(lambda x: x.split('/')[0])
        # Filter rows where base_currency is in the list of unique_coins
        filtered_ticker_cache = self.ticker_cache[self.ticker_cache['base_currency'].isin(unique_coins)]
        return filtered_ticker_cache

    def get_my_trades(self, symbol, since=None):  # async
        try:
            # Calculate the timestamp since opening coinbase account
            now = datetime.datetime.now()
            # Set since to December 1, 2017, if not provided
            if since is None:
                since = datetime.datetime(2017, 12, 1)
                since = self.utility.time_unix(since.strftime("%Y-%m-%d %H:%M:%S.%f")) if since else None

            params = {
                'paginate': True,  # Enable automatic pagination
                'paginationCalls': 20  # Set the max number of pagination calls if necessary
            }

            # Fetch trades using CCXT's automatic pagination
            trades = self.ccxt_exceptions.ccxt_api_call(   # await
                self.exchange.fetch_my_trades, symbol, since=since, params=params
            )

            return trades

        except Exception as e:
            error_details = traceback.format_exc()
            self.log_manager.sighook_logger.error(f'get_my_trades: {error_details}, {e}')
            return None

    def get_portfolio_data(self, start_time, holdings, threshold=0.01):
        usd_pairs = []
        avg_dollar_vol_total = 0.0
        price_change, df = pd.DataFrame(), pd.DataFrame()

        try:
            # Early exit if ticker_cache is empty
            if self.ticker_cache is None or self.ticker_cache.empty:
                return [], [], 0, pd.DataFrame(), pd.DataFrame()
            price_change = None
            # Preprocessing ticker_cache
            self.ticker_cache = self._preprocess_ticker_cache()
            self.ticker_cache = self.ticker_cache.drop_duplicates(subset='symbol')
            # Calculate average dollar volume total
            avg_dollar_vol_total = self.ticker_cache['quote_vol_24h'].mean() if not self.ticker_cache.empty else 0

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
                hi_vol_price_matrix = rows_to_add[(rows_to_add['price change %'] >= 2.1) &
                                                  (rows_to_add['quote volume'] >= min(avg_dollar_vol_total,
                                                                                      Decimal(1000000)))]
            else:
                hi_vol_price_matrix = pd.DataFrame()  # If the column doesn't exist, create an empty DataFrame
            hi_vol_price_matrix = self.format_hi_vol_price_matrix(hi_vol_price_matrix)
            # Generate usd_pairs and process portfolio DataFrame
            usd_pairs = self._get_usd_pairs(self.ticker_cache)
            df = self._process_portfolio(threshold)

            # Ensure portfolio_df is a DataFrame
            if not isinstance(df, pd.DataFrame):
                df = pd.DataFrame()  # Convert to empty DataFrame if it's not already a DataFrame
                df = self._process_portfolio(threshold)  # filter coins with less than $0.1USD value
            # Handle the case when a specific symbol is provided
            portfolio_df = df.sort_values(by='Currency')
            # load
            holdings = portfolio_df.to_dict('records')

            return (holdings, usd_pairs, avg_dollar_vol_total,
                    hi_vol_price_matrix, price_change)
        except Exception as e:
            error_details = traceback.format_exc()
            self.log_manager.sighook_logger.error(f'try_place_order: Error placing order: {error_details} df:{df}')
            self.log_manager.sighook_logger.error(f'Error in get_portfolio_data: {e}')

    def _preprocess_ticker_cache(self):  # pull 24hr volume
        try:
            df = self.ticker_cache.dropna(subset=['free'])
            # Ensure 'info' is a dict and 'volume_24h' is not an empty string, '0', or None
            df = df[df['info'].apply(lambda x: isinstance(x, dict) and x.get('volume_24h') not in ['', '0', None])]
            # df['currency'] = df['symbol'].str.split('/').str[0]   delete when certain line is not needed base_currency
                                                                                                        # is sufficient
            # extract 'volume_24h' from 'info' and multiply by 'ask', ensuring 'info' is a dict
            df['quote_vol_24h'] = df.apply(lambda row: float(row['info'].get('volume_24h', 0)) * float(row['info'].get(
                'price', 0)) if isinstance(row['info'], dict) else 0, axis=1)
            df['price'] = df.apply(lambda row: float(row['info'].get('price', 0))
                                   if isinstance(row['info'], dict) else 0, axis=1)
            # clean up the dataframe removing unnecessary columns
            columns_to_drop = df.columns[(df == False).all(axis=0)]
            df = df.drop(columns=columns_to_drop)
            df = df.dropna(axis='columns', how='all')
            self.ticker_cache = df
            return df
        except Exception as e:
            error_details = traceback.format_exc()
            self.log_manager.sighook_logger.error(f'_preprocess_ticker_cache:  {error_details}')
            self.log_manager.sighook_logger.error(f'Error in _preprocess_ticker_cache: {e}')

    @staticmethod
    def _get_usd_pairs(df):
        return df[df['free'] == 0].apply(lambda x: {'id': f"{x['base_currency']}-USD", 'price': x['price']}, axis=1).tolist()

    @staticmethod
    def format_hi_vol_price_matrix(hi_vol_price_matrix):
        # Format 'price' and 'price change %' to 2 decimal places
        hi_vol_price_matrix.loc[:, 'price'] = hi_vol_price_matrix['price'].astype(float).round(2)
        hi_vol_price_matrix.loc[:, 'price change %'] = hi_vol_price_matrix['price change %'].astype(float).round(2)

        # Format 'base volume' and 'quote volume' to 0 decimal places and convert to int
        hi_vol_price_matrix.loc[:, 'base volume'] = hi_vol_price_matrix['base volume'].astype(float).round(0).astype(int)
        hi_vol_price_matrix.loc[:, 'quote volume'] = hi_vol_price_matrix['quote volume'].round(0).astype(int)
        return hi_vol_price_matrix

    def _process_portfolio(self, threshold):
        # Use a list comprehension to collect dictionaries
        data = [self._check_portfolio(row, threshold) for _, row in self.ticker_cache.iterrows()]
        # Filter out None values and create a DataFrame
        filtered_data = [d for d in data if d is not None]
        return pd.DataFrame(filtered_data)

    @staticmethod
    def _get_selected_balance(df, symbol):
        selected_balance = df[df['Currency'].str.upper() == symbol.upper()]['Balance']
        return selected_balance.iloc[0] if not selected_balance.empty else None

    @staticmethod
    def _create_row(row):
        price_decimal = Decimal(row['price']).quantize(Decimal('0.00000001'), rounding=ROUND_DOWN)
        # balance_decimal = Decimal(row['free']).quantize(Decimal('0.00000001'), rounding=ROUND_DOWN)
        return {'coin': row['base_currency'], 'price': price_decimal, 'base volume': row['info']['volume_24h'],
                'quote volume': row['quote_vol_24h'], 'price change %': row['info']['price_percentage_change_24h']}

    @staticmethod
    def _check_portfolio(row, threshold):
        balance_decimal = Decimal(row['free']).quantize(Decimal('0.00000001'), rounding=ROUND_DOWN)
        price_decimal = Decimal(row['price']).quantize(Decimal('0.00000001'), rounding=ROUND_DOWN)
        if balance_decimal * price_decimal > Decimal(threshold):
            return {'symbol': row['symbol'], 'Currency': row['base_currency'], 'Balance': balance_decimal}
        return None
