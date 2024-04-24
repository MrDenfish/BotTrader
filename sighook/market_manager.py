
import asyncio
import pandas as pd
from datetime import datetime, timedelta
from ccxt.base.errors import RequestTimeout


class MarketManager:
    def __init__(self, exchange, order_manager, trading_strategy, logmanager, ccxt_api, ticker_manager, utility,
                 max_concurrent_tasks):

        self.open_orders = []
        self.exchange = exchange
        self.ccxt_exceptions = ccxt_api
        self.trading_strategy = trading_strategy
        self.order_manager = order_manager
        self.ticker_manager = ticker_manager
        self.utility = utility
        self.log_manager = logmanager
        self.results = pd.DataFrame(columns=['symbol', 'action', 'price', 'band_ratio'])
        self.ticker_cache = None
        self.market_cache = None
        self.start_time = None
        self.semaphore = asyncio.Semaphore(max_concurrent_tasks)

    def set_trade_parameters(self, start_time, ticker_cache, market_cache):
        self.start_time = start_time
        self.ticker_cache = ticker_cache
        self.market_cache = market_cache

    async def update_market_data(self):
        """PART I: Data Gathering and Database Loading. Fetch and prepare market data from various sources."""
        try:
            ticker_cache, market_cache, current_prices, filtered_balances = await self.ticker_manager.update_ticker_cache()
            if not market_cache:
                self.log_manager.sighook_logger.info("Market cache is empty. Unable to fetch historical trades.")
                return None  # or return an empty dictionary {}

            return {
                'ticker_cache': ticker_cache,
                'market_cache': market_cache,
                'current_prices': current_prices,
                'filtered_balances': filtered_balances
            }
        except Exception as e:
            self.log_manager.sighook_logger.error(f"Error updating market data: {e}", exc_info=True)
            return {}  # Return an empty dictionary in case of an error

    async def fetch_ohlcv(self, holdings, usd_pairs, avg_dollar_vol_total, buy_sell_matrix,  filtered_ticker_cache):
        """PART III: Order cancellation and Data Collection"""
        if avg_dollar_vol_total is None:
            self.log_manager.sighook_logger.info('Average volume missing')
            return None, None, None

        open_orders = await self.order_manager.get_open_orders(holdings, usd_pairs)
        self.utility.print_elapsed_time(self.start_time, 'open_orders')
        symbols = filtered_ticker_cache['symbol'].unique().tolist()
        ohlcv_data_dict = await self.fetch_all_ohlcv_data(symbols)
        self.utility.print_elapsed_time(self.start_time, 'fetch_all_ohlcv_data')
        return open_orders, ohlcv_data_dict

    async def fetch_all_ohlcv_data(self, symbols):
        """PART III: Order cancellation and Data Collection"""
        """Fetch OHLCV data for multiple symbols concurrently."""
        tasks = [self.fetch_ohlcv_data(symbol) for symbol in symbols if symbol != 'USD/USD']
        results = await asyncio.gather(*tasks, return_exceptions=True)
        ohlcv_data_dict = {}
        for symbol, result in zip(symbols, results):
            try:
                if isinstance(result, Exception):
                    self.log_manager.sighook_logger.error(f"Error fetching OHLCV data for {symbol}: {result}")
                else:
                    if result is not None:
                        ohlcv_data_dict[symbol] = result
            except RequestTimeout as e:
                self.log_manager.sighook_logger.error(f"Request timed out for {symbol}: {e}")
            except Exception as e:
                self.log_manager.sighook_logger.error(f"Error fetching OHLCV data for {symbol}: {e}", exc_info=True)

        return ohlcv_data_dict

    # @profile
    async def fetch_ohlcv_data(self, symbol):
        """PART III: Order cancellation and Data Collection"""
        """Fetch OHLCV data for a single symbol."""
        async with self.semaphore:
            if symbol == 'USD/USD':
                return None
            endpoint = 'public'
            limit = 300  # Coinbase exchange limit
            since = int((datetime.now() - timedelta(days=1)).timestamp() * 1000)  # Starting from 24 hours ago
            pagination_calls = 5  # Number of pagination calls to make

            all_ohlcv = []
            try:
                for _ in range(pagination_calls):
                    params = {
                        "paginate": True,
                        "paginationCalls": 1,  # We handle the loop externally, so we set this to 1
                        "since": since,
                        "limit": limit,
                    }

                    ohlcv_page = await self.ccxt_exceptions.ccxt_api_call(self.exchange.fetch_ohlcv, endpoint, symbol, '1m',
                                                                          since, limit, params=params)
                    if not ohlcv_page:
                        break  # No more data available

                    all_ohlcv.extend(ohlcv_page)
                    last_entry_timestamp = ohlcv_page[-1][0]
                    since = last_entry_timestamp + 1  # Set 'since' to the timestamp of the last entry for the next call

                if all_ohlcv:
                    df = pd.DataFrame(all_ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'volume'])
                    return {
                        'symbol': symbol,
                        'data': df
                    }
            except Exception as e:
                self.log_manager.sighook_logger.error(f"Error fetching OHLCV data for {symbol}: {e}", exc_info=True)

            return None
