
import asyncio
import datetime
from decimal import Decimal

import pandas as pd

import traceback


class TickerManager:
    def __init__(self, utility, logmanager, exchange, ccxt_api, max_concurrent_tasks=10):
        self.exchange = exchange
        self.ticker_cache = None
        self.market_cache = None
        self.last_ticker_update = None
        self.log_manager = logmanager
        self.ccxt_exceptions = ccxt_api
        self.utility = utility
        self.start_time = None
        self.holdings = None
        self.semaphore = asyncio.Semaphore(max_concurrent_tasks)

    def set_trade_parameters(self, start_time, ticker_cache, market_cache, hist_holdings):
        self.start_time = start_time
        self.ticker_cache = ticker_cache
        self.market_cache = market_cache
        self.holdings = hist_holdings

    async def update_ticker_cache(self, start_time=None):

        refresh_time = 300  # 5 minutes
        empty_df = pd.DataFrame()
        empty_market_data = []
        try:
            market_data = None
            now = datetime.datetime.utcnow()
            if self.start_time is None:
                market_data = await self.ccxt_exceptions.ccxt_api_call(self.exchange.fetch_markets)
                if market_data is None:
                    return empty_df, empty_market_data
            else:
                start_time_datetime = datetime.datetime.utcfromtimestamp(self.start_time)
                temp_time = now - start_time_datetime
                if temp_time.seconds >= refresh_time:  # 5 minutes
                    market_data = await self.ccxt_exceptions.ccxt_api_call(self.exchange.fetch_markets)
                    if market_data is None:
                        return empty_df, empty_market_data
                else:

                    return self.ticker_cache, self.market_cache  # Exit if the ticker cache is already up to date

            # Filter for symbols that end with '/USD'
            tickers = [market for market in market_data if market['symbol'].endswith('/USD')]
            if not tickers:
                return empty_df, empty_market_data  # Exit if no USD tickers are found

            tickers_dict = {market['symbol']: market for market in tickers}
            balance = await self.ccxt_exceptions.ccxt_api_call(self.exchange.fetch_balance)
            dollar_balance = balance.get('USD', {})
            if balance is None:
                return empty_df, empty_market_data

            avail_qty = balance.get('free', {})
            total_qty = balance.get('total', {})
            usd_total = self.utility.float_to_decimal(Decimal(dollar_balance['total']), 2)
            usd_free = self.utility.float_to_decimal(Decimal(dollar_balance['free']), 2)
            # Create a new DataFrame with the USD balance row
            df = pd.DataFrame.from_dict(tickers_dict, orient='index')
            df['base_currency'] = df['symbol'].str.split('/').str[0]
            df['free'] = df['base_currency'].map(avail_qty).fillna(0)
            df['total'] = df['base_currency'].map(total_qty).fillna(0)
            df['volume_24h'] = df['info'].apply(lambda x: x.get('volume_24h', 0))
            usd_row = pd.DataFrame({
                'symbol': ['USD/USD'],  # Assuming you want to use 'USD/USD' as the symbol for clarity
                'base_currency': ['USD'],
                'free': [usd_free],
                'total': [usd_total],
                'volume_24h': [0]  # Assuming no volume for USD/USD
            }, index=['USD'])  # 'USD' is set as the index

            # Append the USD row DataFrame to the original DataFrame

            df = pd.concat([df, usd_row], ignore_index=False)
            await self.parallel_fetch_and_update(df)
            return df, market_data

        except Exception as e:
            error_details = traceback.format_exc()
            self.log_manager.sighook_logger.error(f'update_ticker_cache: {error_details}')
            self.ccxt_exceptions.log_manager.sighook_logger.error(f'Error in update_ticker_cache: {e}')

    async def parallel_fetch_and_update(self, df):
        #  removed ThreadPoolExecutor
        try:
            # filterout non-traditional symbols like 'USD'
            symbols = [str(symbol) for symbol in df['symbol'].tolist() if '/' in str(symbol)]
            tasks = [self.fetch_ticker_data(symbol) for symbol in symbols]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if not result or len(result) < 3:
                    self.log_manager.sighook_logger.info(f"Invalid result: {result}")
                    continue
                symbol, bid, ask = result
                symbol_exists = symbol in df.index
                if bid is None or ask is None:
                    self.log_manager.sighook_logger.debug(f"Missing data for symbol {symbol}, skipping")
                    continue
                if symbol_exists:
                    df.loc[symbol, ['bid', 'ask']] = bid, ask
                else:
                    self.log_manager.sighook_logger.info(f"Symbol not found in DataFrame: {symbol}")
        except Exception as e:
            error_details = traceback.format_exc()
            self.log_manager.sighook_logger.error(f'parallel_fetch_and_update: {error_details}')
            self.log_manager.sighook_logger.error(f'Error in parallel_fetch_and_update: {e}')

    async def fetch_ticker_data(self, symbol):
        async with self.semaphore:  # Acquire a semaphore slot
            try:
                if symbol == 'USD/USD':
                    return symbol, None, None

                individual_ticker = await self.ccxt_exceptions.ccxt_api_call(self.exchange.fetch_ticker, symbol)
                if individual_ticker and isinstance(individual_ticker, dict):
                    bid = individual_ticker.get('bid')
                    ask = individual_ticker.get('ask')
                    return symbol, bid, ask
                else:
                    self.log_manager.sighook_logger.debug(f'No data available for ticker {symbol}')
                    return symbol, None, None

            except IndexError:
                self.log_manager.sighook_logger.error(
                    f'Index error for symbol {symbol}: Data format may be incorrect or incomplete.')
                return symbol, None, None
            except Exception as e:
                self.log_manager.sighook_logger.error(f'Error while fetching ticker data for {symbol}: {e}')
                return symbol, None, None

    async def old_get_ticker_balance(self, coin):
        """Get the balance of a coin in the exchange account."""
        try:
            balance = await self.ccxt_exceptions.ccxt_api_call(lambda: self.exchange.fetch_balance())
            print(self.ticker_cache)
            coin_balance = Decimal(balance[coin]['total']) if coin in balance else Decimal('0.0')
            usd_balance = Decimal(balance['USD']['total']) if 'USD' in balance else Decimal('0.0')

        except Exception as e:
            self.log_manager.sighook_logger.error(f'SenderUtils get_balance: Exception occurred during  {e}')
            coin_balance = Decimal('0.0')
            usd_balance = Decimal('0.0')

        return coin_balance, usd_balance

    def get_ticker_balance(self, coin):
        """Get the balance of a coin from the DataFrame."""
        try:
            # Find the row in the DataFrame where the symbol matches the coin
            symbol_exists = coin in self.ticker_cache.index
            symbol_exists = 'USD' in self.ticker_cache.index
            if symbol_exists:
                #  'total' column holds the balance amount
                coin_balance = Decimal(self.ticker_cache.iloc[0]['total'])
                usd_row = self.ticker_cache[self.ticker_cache['symbol'] == 'USD/USD']
                usd_balance = Decimal(usd_row.iloc[0]['free']) if not usd_row.empty else Decimal('0.0')
            else:
                coin_balance = Decimal('0.0')
                usd_balance = Decimal('0.0')

        except Exception as e:
            self.log_manager.sighook_logger.error(f'get_ticker_balance: Exception occurred {e}')
            coin_balance = Decimal('0.0')
            usd_balance = Decimal('0.0')

        return coin_balance, usd_balance
