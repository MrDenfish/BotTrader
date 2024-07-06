import asyncio

import pandas as pd

from ccxt import AuthenticationError
from ccxt.base.errors import RequestTimeout, BadSymbol, RateLimitExceeded, ExchangeError
import traceback

import datetime

from decimal import Decimal


class TickerManager:
    def __init__(self, utility, log_manager, exchange, ccxt_api, max_concurrent_tasks):
        self.exchange = exchange
        self.ticker_cache = None
        self.market_cache = None
        self.last_ticker_update = None
        self.log_manager = log_manager
        self.ccxt_api = ccxt_api
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
            now = datetime.datetime.utcnow()
            endpoint = 'public'  # for rate limiting
            params = {
                'paginate': True,  # Enable automatic pagination
                'paginationCalls': 10,  # Set the max number of pagination calls if necessary
                'limit': 1000  # Set the max number of items to return
            }
            should_fetch = self.start_time is None or (
                    now - datetime.datetime.utcfromtimestamp(self.start_time)).seconds >= refresh_time

            if should_fetch:
                market_data = await self.ccxt_api.ccxt_api_call(self.exchange.fetch_markets, endpoint, params=params)
                if not market_data:
                    raise Exception('No market data available, check the web connection.')

                filtered_market_data = self.filter_market_data(market_data)
                del market_data  # frees up memory
                usd_tickers, tickers_dict = self.extract_usd_tickers(filtered_market_data)

                if not usd_tickers:
                    return empty_df, empty_market_data, None, None

                balances = await self.fetch_balance_and_filter()

                updated_ticker_cache = self.prepare_dataframe(tickers_dict, balances)
                if updated_ticker_cache.empty:
                    self.log_manager.sighook_logger.error(f'Error in update_ticker_cache: {updated_ticker_cache}',
                                                          exc_info=True)
                    return empty_df, empty_market_data, None, None
                updated_ticker_cache, current_prices = await self.parallel_fetch_and_update(updated_ticker_cache)

                return updated_ticker_cache, filtered_market_data, current_prices, balances

            return self.ticker_cache, self.market_cache, None, None

        except Exception as e:
            self.log_manager.sighook_logger.error(f'Error in update_ticker_cache: {e}', exc_info=True)

    @staticmethod
    def filter_market_data(market_data):
        """PART I: Data Gathering and Database Loading"""
        return [
            {
                'asset': item['base'],
                'quote': item['quote'],
                'symbol': item['symbol'],
                'precision': item['precision'],
                'info': {key: item['info'][key] for key in ['product_id', 'price', 'volume_24h',
                                                            'price_percentage_change_24h']}
            }
            for item in market_data
        ]

    def extract_usd_tickers(self, filtered_market_data):
        """PART I: Data Gathering and Database Loading"""
        try:
            usd_tickers = [market for market in filtered_market_data if market['quote'] == 'USD']
            tickers_dict = {market['asset']: market for market in usd_tickers}
            return usd_tickers, tickers_dict
        except Exception as e:
            self.log_manager.sighook_logger.error(f'Error in extract_usd_tickers: {e}', exc_info=True)
            return [], {}

    async def fetch_balance_and_filter(self):
        """PART I: Data Gathering and Database Loading"""
        end_point = 'private'  # for rate limiting
        params = {
            'offset': 0,  # Skip the first 0 items
            'paginate': True,  # Enable automatic pagination
            'paginationCalls': 20,  # Set the max number of pagination calls if necessary
            'limit': 300  # Set the max number of items to return
        }
        try:
            balance = await self.ccxt_api.ccxt_api_call(self.exchange.fetch_balance, end_point, params=params)
            if balance:
                filtered_balance = {
                    currency: details for currency, details in balance.items()
                    if currency != 'info' and (details.get('free', 0) > 0 or details.get('used', 0) > 0
                                               or details.get('total', 0) > 0)
                }
            else:
                return {}

            usd_balance = Decimal(filtered_balance.get('USD', {}).get('total', 0))
            usd_free = Decimal(filtered_balance.get('USD', {}).get('free', 0))
            return {
                'filtered': filtered_balance,
                'usd_balance': usd_balance,
                'usd_free': usd_free,
            }
        except AuthenticationError as e:
            self.log_manager.sighook_logger.error(f'Authentication Error: {e}')
            return {}

    def prepare_dataframe(self, tickers_dict, balances):
        """PART I: Data Gathering and Database Loading"""
        avail_qty = {k: v.get('free', 0) for k, v in balances['filtered'].items()}
        total_qty = {k: v.get('total', 0) for k, v in balances['filtered'].items()}
        df = pd.DataFrame.from_dict(tickers_dict, orient='index')
        df['free'] = df['asset'].map(avail_qty).fillna(0)
        df['total'] = df['asset'].map(total_qty).fillna(0)
        df['volume_24h'] = df['info'].apply(lambda x: x.get('volume_24h', 0))

        usd_total = self.utility.float_to_decimal(Decimal(balances['usd_balance']), 2)
        usd_free = self.utility.float_to_decimal(Decimal(balances['usd_free']), 2)

        usd_row = pd.DataFrame({
            'symbol': ['USD/USD'],
            'asset': ['USD'],
            'free': [usd_free],
            'total': [usd_total],
            'volume_24h': [0]
        }, index=['USD'])

        df = pd.concat([df, usd_row], ignore_index=False)
        return df

    async def parallel_fetch_and_update(self, df, update_type='current_price'):
        """PART I: Data Gathering and Database Loading
            PART VI: Profitability Analysis and Order Generation """
        current_prices = {}
        try:
            tickers = await self.fetch_bids_asks()
            if not tickers:
                self.log_manager.sighook_logger.error("Failed to fetch bids and asks.")
                return df, current_prices

            for symbol in df['symbol'].tolist():
                ticker = tickers.get(symbol)
                if ticker:
                    bid = ticker.get('bid')
                    ask = ticker.get('ask')
                    if bid is None or ask is None:
                        self.log_manager.sighook_logger.debug(f"Missing data for symbol {symbol}, skipping")
                        continue

                    if symbol in df['symbol'].values:
                        if update_type == 'bid_ask':
                            df.loc[df['symbol'] == symbol, ['bid', 'ask']] = [bid, ask]
                        elif update_type == 'current_price':
                            df.loc[df['symbol'] == symbol, 'current_price'] = float(ask)
                        current_prices[symbol] = float(ask)
                    else:
                        self.log_manager.sighook_logger.info(f"Symbol not found in DataFrame: {symbol}")
                else:
                    self.log_manager.sighook_logger.info(f"No ticker data for symbol: {symbol}")

            return df, current_prices
        except Exception as e:
            self.log_manager.sighook_logger.error(f'Error in parallel_fetch_and_update: {e}', exc_info=True)
            return df, current_prices

    async def fetch_bids_asks(self):
        try:
            endpoint = 'public'
            params = {
                'paginate': True,
                'paginationCalls': 10,
                'limit': 300
            }
            tickers = await self.ccxt_api.ccxt_api_call(self.exchange.fetchBidsAsks, endpoint, params=params)
            return tickers
        except Exception as e:
            self.log_manager.sighook_logger.error(f"Error fetching bids and asks: {e}", exc_info=True)
            return {}

    async def old_parallel_fetch_and_update(self, df, update_type='current_price'):
        """PART I: Data Gathering and Database Loading
            PART VI: Profitability Analysis and Order Generation """
        try:
            current_prices = {}
            symbols = [str(symbol) for symbol in df['symbol'].tolist() if '/' in str(symbol)]
            results = []
            delay = 1  # Calculate delay based on rate limit per minute

            for symbol in symbols:
                result = await self.fetch_ticker_data_with_delay(symbol, update_type='prices', delay=delay)
                results.append(result)

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

                if symbol in df['symbol'].values:
                    if update_type == 'bid_ask':
                        df.loc[df['symbol'] == symbol, ['bid', 'ask']] = [bid, ask]
                    elif update_type == 'current_price':
                        df.loc[df['symbol'] == symbol, 'current_price'] = float(ask)
                    current_prices[symbol] = float(ask)
                else:
                    self.log_manager.sighook_logger.info(f"Symbol not found in DataFrame: {symbol}")

            return df, current_prices
        except Exception as e:
            self.log_manager.sighook_logger.error(f'Error in parallel_fetch_and_update: {e}', exc_info=True)

    async def old_fetch_ticker_data_with_delay(self, symbol, update_type='prices', delay=1):
        """Fetch ticker data with a delay to manage rate limits."""
        try:
            result = await self.fetch_ticker_data(symbol, return_type=update_type)
            print(result)
            await asyncio.sleep(delay/4)  # Add delay between API calls
            return result
        except (BadSymbol, RequestTimeout) as ex:
            self.log_manager.sighook_logger.info(
                f'Rate limit exceeded for {symbol}. Waiting for {delay} seconds before retry...')
            await asyncio.sleep(delay)
            return await self.fetch_ticker_data_with_delay(symbol, update_type, delay)
        except Exception as e:
            self.log_manager.sighook_logger.error(f"Error fetching ticker data for {symbol}: {e}", exc_info=True)
            return symbol, None, None

    async def fetch_ticker_data(self, symbol: str, return_type='full'):
        """PART I: Data Gathering and Database Loading
        PART VI: Profitability Analysis and Order Generation """
        async with self.semaphore:
            try:
                if symbol == 'USD/USD':
                    return symbol, None, None if return_type == 'prices' else (symbol, None)
                ticker = symbol.replace('/', '-')
                endpoint = 'public'
                params = {
                    'paginate': True,
                    'paginationCalls': 10,
                    'limit': 300
                }
                bid_ask = await self.ccxt_api.ccxt_api_call(self.exchange.fetchBidsAsks, endpoint, params=params)  # test
                if symbol is not None:
                    self.log_manager.sighook_logger.debug(f"Calling ccxt_api_call for {symbol}")
                    individual_ticker = await self.ccxt_api.ccxt_api_call(self.exchange.fetch_ticker, endpoint, ticker,
                                                                          params=params)
                else:
                    pass
                if return_type == 'full':
                    return symbol, individual_ticker
                elif return_type == 'prices':
                    if individual_ticker and isinstance(individual_ticker, dict):
                        return symbol, individual_ticker['bid'], individual_ticker['ask']
                    else:
                        self.log_manager.sighook_logger.debug(f'No data available for ticker {symbol}')
                        return symbol, None, None

            except IndexError:
                error_details = traceback.format_exc()
                self.log_manager.sighook_logger.error(f'Index error: {error_details}')
                self.log_manager.sighook_logger.error(f'Index error for symbol {symbol}: '
                                                      f'Data format may be incorrect or incomplete.')
                return symbol, None, None if return_type == 'prices' else (symbol, None)
            except Exception as e:
                error_details = traceback.format_exc()
                self.log_manager.sighook_logger.error(f'fetch_ticker_data: {error_details}')
                self.log_manager.sighook_logger.error(f'Error while fetching ticker data for {symbol}: {e}')
                return symbol, None, None if return_type == 'prices' else (symbol, None)

# <><><><><><><><><><><><><><><><><><><><><>  RETIRED CODE <><><><><><><><><><><><><><><><><><><><><>
