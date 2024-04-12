import asyncio

import pandas as pd

from memory_profiler import profile  # Debugging tool

import traceback

import datetime

from decimal import Decimal


class TickerManager:
    def __init__(self, utility, logmanager, exchange, ccxt_api, max_concurrent_tasks):
        self.exchange = exchange
        self.ticker_cache = None
        self.market_cache = None
        self.last_ticker_update = None
        self.log_manager = logmanager
        self.ccxt_exceptions = ccxt_api
        self.utility = utility
        self.start_time = None
        self.semaphore = asyncio.Semaphore(max_concurrent_tasks)

    def set_trade_parameters(self, start_time, ticker_cache, market_cache):
        self.start_time = start_time
        self.ticker_cache = ticker_cache
        self.market_cache = market_cache

    async def update_ticker_cache(self, start_time=None):
        """PART I: Data Gathering and Database Loading"""
        refresh_time = 300  # 5 minutes
        empty_df = pd.DataFrame()
        empty_market_data = []

        try:
            async with self.semaphore:
                now = datetime.datetime.utcnow()
                endpoint = 'public'  # for rate limiting
                should_fetch = self.start_time is None or (
                            now - datetime.datetime.utcfromtimestamp(self.start_time)).seconds >= refresh_time

                if should_fetch:
                    market_data = await self.ccxt_exceptions.ccxt_api_call(self.exchange.fetch_markets, endpoint)
                    if not market_data:
                        return empty_df, empty_market_data, None, None

                    filtered_market_data = self.filter_market_data(market_data)
                    del market_data  # frees up memory
                    usd_tickers, tickers_dict = self.extract_usd_tickers(filtered_market_data)

                    if not usd_tickers:
                        return empty_df, empty_market_data, None, None

                    filtered_balances, usd_balance, usd_free = await self.fetch_balance_and_filter()

                    updated_ticker_cache = self.prepare_dataframe(tickers_dict, filtered_balances, usd_balance, usd_free)

                    updated_ticker_cache, current_prices = await self.parallel_fetch_and_update(updated_ticker_cache)

                    return updated_ticker_cache, filtered_market_data, current_prices, filtered_balances

                return self.ticker_cache, self.market_cache, None, None

        except Exception as e:
            self.log_manager.sighook_logger.error(f'Error in update_ticker_cache: {e}', exc_info=True)

    async def get_ticker_balance(self, coin):  # async
        """PART III: Order cancellation and Data Collection"""
        """Get the balance of a coin in the exchange account."""
        try:
            async with self.semaphore:
                endpoint = 'private'
                balance = await self.ccxt_exceptions.ccxt_api_call(self.exchange.fetch_balance, endpoint)
                coin_balance = Decimal(balance[coin]['total']) if coin in balance else Decimal('0.0')
                usd_balance = Decimal(balance['USD']['total']) if 'USD' in balance else Decimal('0.0')

        except Exception as e:
            self.log_manager.sighook_logger.error(f'SenderUtils get_balance: Exception occurred during  {e}')
            coin_balance = Decimal('0.0')
            usd_balance = Decimal('0.0')

        return coin_balance, usd_balance

    @staticmethod
    def filter_market_data(market_data):
        """PART I: Data Gathering and Database Loading"""
        return [
            {
                'symbol': item['symbol'],
                'precision': item['precision'],
                'info': {key: item['info'][key] for key in ['product_id', 'price', 'volume_24h',
                                                            'price_percentage_change_24h']}
            }
            for item in market_data
        ]

    @staticmethod
    def extract_usd_tickers(filtered_market_data):
        """PART I: Data Gathering and Database Loading"""
        usd_tickers = [market for market in filtered_market_data if market['symbol'].endswith('/USD')]
        tickers_dict = {market['symbol']: market for market in usd_tickers}
        return usd_tickers, tickers_dict

    async def fetch_balance_and_filter(self):
        """PART I: Data Gathering and Database Loading"""
        end_point = 'private'  # for rate limiting
        balance = await self.ccxt_exceptions.ccxt_api_call(self.exchange.fetch_balance, end_point)  # await
        filtered_balance = {
            currency: details for currency, details in balance.items()
            if
            currency != 'info' and (details.get('free', 0) > 0 or details.get('used', 0) > 0 or details.get('total', 0) > 0)
        }
        if not filtered_balance:
            return None, None, None

        usd_balance = Decimal(filtered_balance.get('USD', {}).get('total', 0))
        usd_free = Decimal(filtered_balance.get('USD', {}).get('free', 0))

        return filtered_balance, usd_balance, usd_free

    def prepare_dataframe(self, tickers_dict, balance, usd_balance, usd_free):
        """PART I: Data Gathering and Database Loading"""
        # Creating avail_qty and total_qty from balance dictionary
        avail_qty = {k: v.get('free', 0) for k, v in balance.items()}
        total_qty = {k: v.get('total', 0) for k, v in balance.items()}
        df = pd.DataFrame.from_dict(tickers_dict, orient='index')
        df['base_currency'] = df['symbol'].str.split('/').str[0]
        df['free'] = df['base_currency'].map(avail_qty).fillna(0)
        df['total'] = df['base_currency'].map(total_qty).fillna(0)
        df['volume_24h'] = df['info'].apply(lambda x: x.get('volume_24h', 0))

        # Calculating usd_total and usd_free using utility function
        usd_total = self.utility.float_to_decimal(Decimal(usd_balance), 2)
        usd_free = self.utility.float_to_decimal(Decimal(usd_free), 2)

        usd_row = pd.DataFrame({
            'symbol': ['USD/USD'],
            'base_currency': ['USD'],
            'free': [usd_free],
            'total': [usd_total],
            'volume_24h': [0]
        }, index=['USD'])

        df = pd.concat([df, usd_row], ignore_index=False)
        return df

    async def parallel_fetch_and_update(self, df):
        """PART I: Data Gathering and Database Loading
            PART VI: Profitability Analysis and Order Generation """
        try:
            current_prices = {}  # Dictionary to store the current prices
            chunk_size = 50  # Adjust based on your rate limits and performance
            symbols = [str(symbol) for symbol in df['symbol'].tolist() if '/' in str(symbol)]
            chunks = [symbols[i:i + chunk_size] for i in range(0, len(symbols), chunk_size)]
            tasks = []
            for chunk in chunks:
                tasks.extend([self.fetch_ticker_data(symbol) for symbol in chunk])

            # Wait for all tasks to complete, outside the loop
            results = await asyncio.gather(*tasks, return_exceptions=True)  # Gather results from all tasks
            for result in results:
                if not result or len(result) < 3:
                    self.log_manager.sighook_logger.info(f"Invalid result: {result}")
                    continue
                if isinstance(result, Exception):
                    self.log_manager.sighook_logger.error(f"Error in parallel_fetch_and_update: {result}", exc_info=True)
                    continue
                symbol, bid, ask = result
                if bid is None or ask is None:
                    self.log_manager.sighook_logger.debug(f"Missing data for symbol {symbol}, skipping")
                    continue
                if symbol in df.symbol.values:
                    df.loc[symbol, ['bid', 'ask']] = bid, ask
                    current_prices[symbol] = ask
                else:
                    self.log_manager.sighook_logger.info(f"Symbol not found in DataFrame: {symbol}")
            return df, current_prices
        except Exception as e:
            self.log_manager.sighook_logger.error(f'Error in parallel_fetch_and_update: {e}', exc_info=True)

    async def fetch_ticker_data(self, symbol: str):  # async
        """PART I: Data Gathering and Database Loading
        PART VI: Profitability Analysis and Order Generation """
        async with self.semaphore:  # async # Acquire a semaphore slot
            try:
                if symbol == 'USD/USD':
                    return symbol, None, None
                ticker = symbol.replace('/', '-')
                endpoint = 'public'  # for rate limiting
                individual_ticker = await self.ccxt_exceptions.ccxt_api_call(self.exchange.fetch_ticker, endpoint, ticker)
                if individual_ticker and isinstance(individual_ticker, dict):
                    return symbol, individual_ticker['bid'], individual_ticker['ask']
                else:
                    self.log_manager.sighook_logger.debug(f'No data available for ticker {symbol}')
                    return symbol, None, None

            except IndexError:
                error_details = traceback.format_exc()
                self.log_manager.sighook_logger.error(f'Index error: {error_details}')
                self.log_manager.sighook_logger.error(
                    f'Index error for symbol {symbol}: Data format may be incorrect or incomplete.')
                return symbol, None, None
            except Exception as e:
                error_details = traceback.format_exc()
                self.log_manager.sighook_logger.error(f'fetch_ticker_data: {error_details}')
                self.log_manager.sighook_logger.error(f'Error while fetching ticker data for {symbol}: {e}')
                return symbol, None, None
