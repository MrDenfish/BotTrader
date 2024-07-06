
import asyncio
import pandas as pd
from datetime import datetime, timedelta


class MarketManager:
    def __init__(self, tradebot, exchange, order_manager, trading_strategy, logmanager, ccxt_api, ticker_manager, utility,
                 max_concurrent_tasks):

        self.exchange = exchange
        self.ccxt_api = ccxt_api
        self.max_concurrent_tasks = max_concurrent_tasks
        self.trading_strategy = trading_strategy
        self.tradebot = tradebot
        self.order_manager = order_manager
        self.ticker_manager = ticker_manager
        self.utility = utility
        self.log_manager = logmanager
        # self.results = pd.DataFrame(columns=['symbol', 'action', 'price', 'band_ratio'])
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

    async def fetch_ohlcv(self, filtered_ticker_cache):
        """PART III: Order cancellation and Data Collection"""
        symbols = filtered_ticker_cache['symbol'].unique().tolist()
        ohlcv_data_dict = await self.fetch_all_ohlcv_data(symbols)
        return ohlcv_data_dict

    async def fetch_all_ohlcv_data(self, symbols):
        """PART III Fetch OHLCV data for multiple symbols concurrently."""
        ohlcv_data_dict = {}
        for symbol in symbols:
            result = await self.fetch_ohlcv_data(symbol)

            if isinstance(result, Exception):
                print(f"Error fetching OHLCV data for {symbol}: {result}")
            elif result:
                ohlcv_data_dict[symbol] = result
        return ohlcv_data_dict

    async def fetch_ohlcv_data(self, symbol):
        if symbol == 'USD/USD':
            return None

        endpoint = 'default'  # to force ccxt_api_call method to use the default endpoint
        limit = 300  # Fetch 300 1-minute candles at a time
        since = int((datetime.now() - timedelta(days=1)).timestamp() * 1000)  # Starting from 24 hours ago
        pagination_calls = 5  # Fetch five sets of 300-minute candles

        all_ohlcv = []
        try:
            for _ in range(pagination_calls):
                params = {
                    "paginate": True,
                    "paginationCalls": 5
                }
                await asyncio.sleep(2)  # Delay between requests
                ohlcv_page = await self.ccxt_api.ccxt_api_call(self.exchange.fetch_ohlcv, endpoint, symbol, '1m', since,
                                                               limit, params)
                if not ohlcv_page:
                    break

                all_ohlcv.extend(ohlcv_page)
                last_entry_timestamp = ohlcv_page[-1][0]
                since = last_entry_timestamp + 1

            if all_ohlcv:
                df = pd.DataFrame(all_ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'volume'])
                return {'asset': symbol, 'data': df}
        except Exception as e:
            print(f"Error fetching OHLCV data for {symbol}: {e}")

        return None
