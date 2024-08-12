
import asyncio
import pandas as pd
from datetime import datetime, timedelta
from sqlalchemy import select
from database_table_models import OHLCVData


class MarketManager:
    def __init__(self, tradebot, exchange, order_manager, trading_strategy, logmanager, ccxt_api, ticker_manager, utility,
                 max_concurrent_tasks, database_session_mngr):

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
        self.db_manager = database_session_mngr
        self.semaphore = asyncio.Semaphore(max_concurrent_tasks)

    def set_trade_parameters(self, start_time, ticker_cache, market_cache):
        self.start_time = start_time
        self.ticker_cache = ticker_cache
        self.market_cache = market_cache

    async def update_market_data(self, open_orders=None):
        """PART I: Data Gathering and Database Loading. Fetch and prepare market data from various sources."""
        try:
            ticker_cache, market_cache, current_prices, filtered_balances = await self.ticker_manager.update_ticker_cache(
                open_orders)
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
        """PART III - Order Cancellation and Data Collection. Fetch OHLCV data for all symbols in the ticker cache."""

        symbols = filtered_ticker_cache['symbol'].unique().tolist()
        await self.fetch_and_store_ohlcv_data(symbols)

    async def fetch_and_store_ohlcv_data(self, symbols):
        """PART III - Order Cancellation and Data Collection. Fetch OHLCV data for all symbols in the ticker cache."""
        async with self.db_manager.AsyncSessionLocal() as session:
            for symbol in symbols:
                result = await self.fetch_ohlcv_data(symbol)
                if isinstance(result, Exception):
                    print(f"Error fetching OHLCV data for {symbol}: {result}")
                elif result:
                    await self.store_ohlcv_data(session, result)
            await session.commit()

    async def fetch_ohlcv_data(self, symbol):
        """PART III - Order Cancellation and Data Collection. Fetch OHLCV data for all symbols in the ticker cache."""
        endpoint = 'default'
        limit = 300
        since = int((datetime.now() - timedelta(days=1)).timestamp() * 1000)
        pagination_calls = 5

        all_ohlcv = []
        try:
            for _ in range(pagination_calls):
                await asyncio.sleep(2)
                ohlcv_page = await self.ccxt_api.ccxt_api_call(self.exchange.fetch_ohlcv, endpoint, symbol, '1m', since,
                                                               limit)
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

    @staticmethod
    async def store_ohlcv_data(session, ohlcv_data):
        """PART III - Order Cancellation and Data Collection. Fetch OHLCV data for all symbols in the ticker cache."""
        df = ohlcv_data['data']
        symbol = ohlcv_data['asset']
        records = df.to_dict('records')
        for record in records:
            ohlcv_record = OHLCVData(
                symbol=symbol,
                time=datetime.fromtimestamp(record['time'] / 1000),
                open=record['open'],
                high=record['high'],
                low=record['low'],
                close=record['close'],
                volume=record['volume']
            )
            session.add(ohlcv_record)

    async def update_ohlcv(self, filtered_ticker_cache):
        """PART III - Order Cancellation and Data Collection. Fetch OHLCV data for all symbols in the ticker cache."""
        symbols = filtered_ticker_cache['symbol'].unique().tolist()
        await self.fetch_and_store_incremental_ohlcv_data(symbols)

    async def fetch_and_store_incremental_ohlcv_data(self, symbols):
        """PART III - Order Cancellation and Data Collection. Fetch OHLCV data for all symbols in the ticker cache."""
        async with self.db_manager.AsyncSessionLocal() as session:
            for symbol in symbols:
                last_timestamp = await self.get_last_timestamp(session, symbol)
                new_data = await self.fetch_incremental_ohlcv_data(symbol, last_timestamp)
                if new_data:
                    await self.store_ohlcv_data(session, new_data)
            await session.commit()

    @staticmethod
    async def get_last_timestamp(session, symbol):
        result = await session.execute(
            select(OHLCVData.time).filter(OHLCVData.symbol == symbol).order_by(OHLCVData.time.desc()).limit(1)
        )
        last_time = result.scalar()
        if last_time:
            return int(last_time.timestamp() * 1000)
        return int((datetime.now() - timedelta(days=1)).timestamp() * 1000)

    async def fetch_incremental_ohlcv_data(self, symbol, last_timestamp):
        """PART III - Order Cancellation and Data Collection. Fetch OHLCV data for all symbols in the ticker cache."""
        try:
            since = last_timestamp + 1
            limit = 300
            all_ohlcv = []
            for _ in range(5):
                await asyncio.sleep(1)
                ohlcv_page = await self.ccxt_api.ccxt_api_call(self.exchange.fetch_ohlcv, 'default', symbol, '1m', since,
                                                               limit)
                if not ohlcv_page:
                    break
                all_ohlcv.extend(ohlcv_page)
                last_entry_timestamp = ohlcv_page[-1][0]
                since = last_entry_timestamp + 1

            if all_ohlcv:
                df = pd.DataFrame(all_ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'volume'])
                return {'asset': symbol, 'data': df}
        except Exception as e:
            print(f"Error fetching incremental OHLCV data for {symbol}: {e}")
        return None
