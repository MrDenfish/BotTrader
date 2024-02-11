from decimal import Decimal, ROUND_DOWN
import datetime
import pandas as pd


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

    '''All Coins ever traded '''
    async def track_trades(self, portfolio_dir):
        # Load existing ledger cache
        try:
            existing_ledger = pd.read_csv(portfolio_dir + '/portfolio_data.csv')
        except (FileNotFoundError, pd.errors.EmptyDataError):
            existing_ledger = pd.DataFrame()

        # Initialize an empty dictionary for latest timestamps
        last_timestamp = {}

        # # Check if existing_ledger is not empty before grouping

        last_timestamp = existing_ledger['timestamp'].iloc[-2] if not existing_ledger.empty else 0
        unix_timestamp = self.utility.time_unix(last_timestamp)
        transactions = []
        for symbol in self.ticker_cache['symbol']:
            print(f'Updating trades data...{symbol}', end='\r')
            # if 'HOPR' in symbol: # debug statement
            #     last_timestamp = '2024-01-04 04:12:02.944'  # debug statement
            #     unix_timestamp = self.utility.time_unix(last_timestamp)  # debug statement
            try:
                if unix_timestamp == 0:
                    trades = await self.ccxt_exceptions.ccxt_api_call(self.exchange.fetch_my_trades, symbol)
                else:
                    trades = await self.ccxt_exceptions.ccxt_api_call(self.exchange.fetch_my_trades, symbol,
                                                                      since=unix_timestamp)

            # Fetching trades for each symbol that are newer than the last timestamp
                for trade in trades:
                    transactions.append({
                        'timestamp': trade['timestamp'],
                        'symbol': symbol,
                        'type': trade['side'],
                        'price': trade['price'],
                        'amount': trade['amount'] if trade['side'] == 'buy' else -trade['amount'],
                        'cost': -trade['cost'] if trade['side'] == 'buy' else trade['cost'],
                        'fee': -trade['fee']['cost'] if trade['fee'] else 0
                    })
            except Exception as e:
                print(f"Error fetching trades for {symbol}: {e}")
                continue

        if transactions:
            temp_ledger = pd.DataFrame(transactions)
            self.ledger_cache = pd.concat([existing_ledger, temp_ledger], ignore_index=True)
            # Convert 'timestamp' to datetime before sorting
            self.ledger_cache['timestamp'] = self.ledger_cache['timestamp'].apply(self.utility.convert_timestamp)
            self.ledger_cache = self.ledger_cache.sort_values(by='timestamp')
            # Save the updated ledger cache
            self.ledger_cache.to_csv(portfolio_dir + '/portfolio_data.csv', index=False)
        return self.ledger_cache

    def filter_ticker_cache_matrix(self, buy_sell_matrix):
        """ Filter ticker cache by volume > 1 million and price change > 2.1% """
        #  Extract list of unique cryptocurrencies from buy_sell_matrix
        unique_coins = buy_sell_matrix['coin'].unique()
        #  Filter ticker_cache to contain only rows with symbols in unique_coins The 'symbol' column in ticker_cache needs
        self.ticker_cache['base_currency'] = self.ticker_cache['symbol'].apply(lambda x: x.split('/')[0])
        # Filter rows where base_currency is in the list of unique_coins
        filtered_ticker_cache = self.ticker_cache[self.ticker_cache['base_currency'].isin(unique_coins)]
        return filtered_ticker_cache

    async def get_my_trades(self, symbol, since=0):
        # Get the current datetime
        now = datetime.datetime.now()
        # Calculate the datetime 30 days before now
        last_timestamp = (now - datetime.timedelta(days=30))
        string_timestamp = last_timestamp.strftime("%Y-%m-%d %H:%M:%S.%f")
        unix_timestamp = self.utility.time_unix(string_timestamp)

        # Assuming 'trades' is the list of trade dictionaries you've fetched
        trades = await self.ccxt_exceptions.ccxt_api_call(self.exchange.fetch_my_trades, symbol, since=unix_timestamp)
        return trades

    def get_portfolio_data(self, start_time, old_portfolio, threshold=0.01):
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
            avg_dollar_vol_total = self.ticker_cache['vol_total'].mean() if not self.ticker_cache.empty else 0

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
            return (portfolio_df.to_dict('records'), usd_pairs, avg_dollar_vol_total,
                    hi_vol_price_matrix, price_change)
        except Exception as e:
            self.log_manager.sighook_logger.error(f'Error in get_portfolio_data: {e}')

    def _preprocess_ticker_cache(self):  # pull 24hr volume
        try:
            df = self.ticker_cache.dropna(subset=['free', 'ask'])
            df = df[df['info'].apply(lambda x: x.get('volume_24h') not in ['', '0'])]
            df['currency'] = df['symbol'].str.split('/').str[0]
            df['vol_total'] = df['info'].apply(lambda x: float(x.get('volume_24h'))) * df['ask']
            return df
        except Exception as e:
            self.log_manager.sighook_logger.error(f'Error in _preprocess_ticker_cache: {e}')

    @staticmethod
    def _get_usd_pairs(df):
        return df[df['free'] == 0].apply(lambda x: {'id': f"{x['currency']}-USD", 'price': x['ask']}, axis=1).tolist()

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
        price_decimal = Decimal(row['ask']).quantize(Decimal('0.00000001'), rounding=ROUND_DOWN)
        balance_decimal = Decimal(row['free']).quantize(Decimal('0.00000001'), rounding=ROUND_DOWN)
        return {'coin': row['currency'], 'price': price_decimal, 'base volume': row['info']['volume_24h'],
                'quote volume': row['vol_total'], 'price change %': row['info']['price_percentage_change_24h']}

    @staticmethod
    def _check_portfolio(row, threshold):
        balance_decimal = Decimal(row['free']).quantize(Decimal('0.00000001'), rounding=ROUND_DOWN)
        price_decimal = Decimal(row['ask']).quantize(Decimal('0.00000001'), rounding=ROUND_DOWN)
        if balance_decimal * price_decimal > Decimal(threshold):
            return {'Currency': row['currency'], 'Balance': balance_decimal}
        return None
