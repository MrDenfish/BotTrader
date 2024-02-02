from decimal import Decimal, ROUND_DOWN

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
        self.current_holdings = None

    def set_trade_parameters(self, start_time, ticker_cache, market_cache, hist_holdings):
        self.start_time = start_time
        self.ticker_cache = ticker_cache
        self.market_cache = market_cache
        self.current_holdings = hist_holdings

    '''All Coins ever traded '''
    async def track_trades(self, start_time, portfolio_dir):
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

        self.utility.print_elapsed_time(start_time, "track_trades")  # debug statement
        if transactions:
            temp_ledger = pd.DataFrame(transactions)
            self.ledger_cache = pd.concat([existing_ledger, temp_ledger], ignore_index=True)
            # Convert 'timestamp' to datetime before sorting
            self.ledger_cache['timestamp'] = self.ledger_cache['timestamp'].apply(self.utility.convert_timestamp)
            self.ledger_cache = self.ledger_cache.sort_values(by='timestamp')
            # Save the updated ledger cache
            self.ledger_cache.to_csv(portfolio_dir + '/portfolio_data.csv', index=False)
        return self.ledger_cache

    def get_portfolio_data(self, start_time, old_portfolio, threshold=0.01):
        usd_pairs = []
        avg_dollar_vol_total = 0.0
        high_total_vol, price_change, df = pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

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
            rows_to_add['Buy Signal'] = False
            rows_to_add['Sell Ratio'] = False
            rows_to_add['Sell Touch'] = False
            rows_to_add['M-Top Signal'] = False
            rows_to_add['Sell RSI'] = False
            rows_to_add['Sell ROC'] = False
            rows_to_add['Sell Signal'] = False
            # Ensure 'quote volume' column exists in rows_to_add
            if 'quote volume' in rows_to_add.columns and 'price change %' in rows_to_add.columns:
                rows_to_add['price change %'] = pd.to_numeric(rows_to_add['price change %'], errors='coerce')
                rows_to_add['price change %'] = rows_to_add['price change %'].fillna(0)
                # dataframe of coins with 2.1% or greater price change
                price_change = rows_to_add[rows_to_add['price change %'] >= Decimal('2.1')]
                # dataframe of coins with 2.1% or greater price change and quote volume greater than 1 million
                hi_vol_price_change = rows_to_add[(rows_to_add['price change %'] >= 2.1) &
                                             (rows_to_add['quote volume'] >= min(avg_dollar_vol_total, Decimal(1000000)))]
            else:
                hi_vol_price_change = pd.DataFrame()  # If the column doesn't exist, create an empty DataFrame

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
                    hi_vol_price_change, price_change)
        except Exception as e:
            self.log_manager.sighook_logger.error(f'Error in get_portfolio_data: {e}')

    def _preprocess_ticker_cache(self):  # pull 24hr volume
        df = self.ticker_cache.dropna(subset=['free', 'ask'])
        df = df[df['info'].apply(lambda x: x.get('volume_24h') not in ['', '0'])]
        df['currency'] = df['symbol'].str.split('/').str[0]
        df['vol_total'] = df['info'].apply(lambda x: float(x.get('volume_24h'))) * df['ask']
        return df

    @staticmethod
    def _get_usd_pairs(df):
        return df[df['free'] == 0].apply(lambda x: {'id': f"{x['currency']}-USD", 'price': x['ask']}, axis=1).tolist()

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