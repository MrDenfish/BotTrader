
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Union
import pandas as pd
from databases import Database
from sqlalchemy import select, func, delete
from sqlalchemy.dialects.postgresql import insert

import TableModels.ohlcv_data
from Config.config_manager import CentralConfig


class MarketManager:
    _instance = None

    @classmethod
    def get_instance(cls, tradebot, exchange, order_manager, trading_strategy, logger_manager, coinbase_api, ccxt_api, ticker_manager,
                     portfolio_manager, max_concurrent_tasks, database, db_tables, shared_data_manager):
        if cls._instance is None:
            cls._instance = cls(tradebot, exchange, order_manager, trading_strategy,
                                logger_manager, coinbase_api, ccxt_api, ticker_manager,
                                portfolio_manager, max_concurrent_tasks, database,
                                db_tables, shared_data_manager)
        return cls._instance

    def __init__(self, tradebot, exchange, order_manager, trading_strategy, logger_manager, coinbase_api, ccxt_api, ticker_manager,
                 portfolio_manager, max_concurrent_tasks, database: Database, db_tables, shared_data_manager):
        self.app_config = CentralConfig()
        self.exchange = exchange
        self.ccxt_api = ccxt_api
        self.coinbase_api = coinbase_api
        self._max_ohlcv_rows = int(self.app_config.max_ohlcv_rows)
        self.max_concurrent_tasks = max_concurrent_tasks
        self.shared_data_manager = shared_data_manager
        self.trading_strategy = trading_strategy
        self.tradebot = tradebot
        self.order_manager = order_manager
        self.ticker_manager = ticker_manager
        self.portfolio_manager = portfolio_manager
        self.logger_manager = logger_manager  # 🙂
        if logger_manager.loggers['shared_logger'].name == 'shared_logger':  # 🙂
            self.logger = logger_manager.loggers['shared_logger']
        self.start_time = None
        self.database = database  # Use `database` directly
        self.db_tables = db_tables
        self.request_semaphore = asyncio.Semaphore(2)
        self.semaphore = asyncio.Semaphore(max_concurrent_tasks)

    @property
    def market_data(self):
        return self.shared_data_manager.market_data

    @property
    def order_management(self):
        return self.shared_data_manager.order_management

    @property
    def ticker_cache(self):
        return self.shared_data_manager.market_data.get('ticker_cache')

    @property
    def non_zero_balances(self):
        return self.shared_data_manager.market_data.get('non_zero_balances')

    @property
    def market_cache_vol(self):
        return self.shared_data_manager.market_data.get('filtered_vol')

    @property
    def max_ohlcv_rows(self):
        return self._max_ohlcv_rows

    async def rate_limited_request(self, func, *args, **kwargs):
        """PART III"""
        async with self.request_semaphore:
            await asyncio.sleep(self.exchange.rateLimit / 1000)  # Enforce rate limit
            return await func(*args, **kwargs)


    async def fetch_and_store_ohlcv_data(self, symbols, mode='update', timeframe='ONE_MINUTE', limit=300):
        """PART III:
        Fetch and store OHLCV data in parallel for all symbols, handling initialization or updates.
        """

        async def fetch_store(symbol):
            """PART III:
            Fetch and store OHLCV data for a single symbol.

        """
            try:
                # Start and end time for 24 hours
                end_dt = datetime.now(timezone.utc)
                start_dt = end_dt - timedelta(minutes=1440)

                # Initialize empty DataFrame to accumulate
                all_dfs = []

                # Break 1440 min into 350-candle chunks (≈ 6 batches max)
                chunk_minutes = 350  # Coinbase max per call
                for i in range(0, 1440, chunk_minutes):
                    chunk_start = start_dt + timedelta(minutes=i)
                    chunk_duration_seconds = limit * 60
                    chunk_end = chunk_start + timedelta(seconds=chunk_duration_seconds)

                    # If chunk_end goes beyond the end_dt, skip it
                    if chunk_end > end_dt:
                        break
                    params = {
                        "start": int(chunk_start.timestamp()),
                        "end": int(chunk_end.timestamp()),
                        "granularity": timeframe,
                        "limit": limit
                    }

                    ohlcv_result = await self.coinbase_api.fetch_ohlcv(symbol=symbol,params=params)
                    if ohlcv_result and not ohlcv_result['data'].empty:
                        all_dfs.append(ohlcv_result['data'])
                    else:
                        print(f"⚠️ No data returned for {symbol} during chunk: {chunk_start} to {chunk_end}")

                    await asyncio.sleep(0.2)  # Gentle pacing

                # Merge all chunks
                if all_dfs:
                    df = pd.concat(all_dfs)
                    df['time'] = pd.to_datetime(df['time'], unit='ms')
                    df = df.set_index('time').resample('1min', origin='start').ffill().reset_index()
                    df['time'] = pd.to_datetime(df['time'], utc=True)  # safe & direct UTC assignment

                    await self.store_ohlcv_data({'symbol': symbol, 'data': df})
                else:
                    print(f"❌ No OHLCV data fetched for {symbol} over 24h window")

            except Exception as e_process:
                self.logger.error(f"❌ Error processing OHLCV data for {symbol}: {e_process}", exc_info=True)

        try:
            # Dynamically adjust batch size based on the number of symbols
            total_symbols = len(symbols)
            if total_symbols <= 20:
                batch_size = 5  # Smaller batch sizes for fewer symbols
            elif total_symbols <= 50:
                batch_size = 7
            else:
                batch_size = 10  # Larger batch sizes for many symbols

            # Concurrency semaphore to control rate limits
            semaphore = asyncio.Semaphore(batch_size)

            async def throttled_fetch_store(symbol):
                async with semaphore:
                    await fetch_store(symbol)

            # Process symbols in batches
            for i in range(0, total_symbols, batch_size):
                batch = symbols[i:i + batch_size]
                tasks = [throttled_fetch_store(symbol) for symbol in batch]
                await asyncio.gather(*tasks)
                print(f'Processed {i + len(batch)} symbols out of {total_symbols}')
                await asyncio.sleep(0.5)  # Slight delay between batches to respect rate limits

        except Exception as e:
            self.logger.error(f"❌ Error in fetch_and_store_ohlcv_data(): {e}", exc_info=True)

    async def store_ohlcv_data(self, ohlcv_data):
        """PART III:
        Store OHLCV data in the database, handling upserts for duplicate entries with optimizations,
        and maintaining a maximum of 1,440 rows per symbol.
        """
        try:
            df = ohlcv_data['data']
            symbol = ohlcv_data['symbol']

            # Prepare records for insertion
            records = [
                {
                    "symbol": symbol,
                    "time": pd.Timestamp(record['time']).to_pydatetime(),
                    "open": record['open'],
                    "high": record['high'],
                    "low": record['low'],
                    "close": record['close'],
                    "volume": record['volume'],
                    "last_updated": datetime.now(),
                }
                for record in df.to_dict('records')
            ]

            # Define query with conflict handling
            query = insert(TableModels.ohlcv_data.OHLCVData).on_conflict_do_update(
                index_elements=['symbol', 'time'],
                set_={
                    "open": insert(TableModels.ohlcv_data.OHLCVData).excluded.open,
                    "high": insert(TableModels.ohlcv_data.OHLCVData).excluded.high,
                    "low": insert(TableModels.ohlcv_data.OHLCVData).excluded.low,
                    "close": insert(TableModels.ohlcv_data.OHLCVData).excluded.close,
                    "volume": insert(TableModels.ohlcv_data.OHLCVData).excluded.volume,
                    "last_updated": datetime.now(),
                }
            )

            # Batch insertion to reduce strain
            batch_size = 500
            for i in range(0, len(records), batch_size):
                batch = records[i:i + batch_size]
                await self.database.execute_many(query=query, values=batch)

            # Cap the table to 1,440 rows for the symbol

            await self.cap_ohlcv_data(symbol, max_rows=self.max_ohlcv_rows)

        except Exception as e:
            self.logger.error(f"❌ Error storing OHLCV data for {ohlcv_data['symbol']}: {e}", exc_info=True)

    async def cap_ohlcv_data(self, symbol, max_rows):
        """PART III
        Ensure the OHLCV table for a symbol has no more than `max_rows` entries,
        deleting the oldest rows if necessary.
        """
        try:
            # Step 1: Fetch the count of rows for the symbol
            query = (
                select(func.count())
                .where(TableModels.ohlcv_data.OHLCVData.symbol == symbol)
            )
            row_count = await self.database.fetch_val(query)

            # Step 2: If the row count exceeds the maximum, delete the oldest rows
            if row_count > max_rows:
                excess_rows = row_count - max_rows

                # Fetch the primary keys (or `time` values) of the oldest rows to delete
                oldest_rows_query = (
                    select(TableModels.ohlcv_data.OHLCVData.time)
                    .where(TableModels.ohlcv_data.OHLCVData.symbol == symbol)
                    .order_by(TableModels.ohlcv_data.OHLCVData.time.asc())  # Oldest first
                    .limit(excess_rows)
                )
                oldest_rows = await self.database.fetch_all(oldest_rows_query)

                # Extract the time values to delete
                times_to_delete = [row['time'] for row in oldest_rows]

                # Perform the DELETE for the rows matching the fetched `time` values
                delete_query = (
                    delete(TableModels.ohlcv_data.OHLCVData)
                    .where(
                        TableModels.ohlcv_data.OHLCVData.symbol == symbol,
                        TableModels.ohlcv_data.OHLCVData.time.in_(times_to_delete)
                    )
                )
                await self.database.execute(delete_query)

                #print(f"Capped {symbol} OHLCV data to {max_rows} rows, deleted {excess_rows} excess rows.") #debugging
        except Exception as e:
            self.logger.error(f"❌ Error capping OHLCV data for {symbol}: {e}", exc_info=True)

    async def get_last_timestamp(self, symbol):
        """
        Get the last timestamp for a symbol from the OHLCV table oldest.
        """
        query = (
            select(TableModels.ohlcv_data.OHLCVData.time)
            .filter(TableModels.ohlcv_data.OHLCVData.symbol == symbol)
            .order_by(TableModels.ohlcv_data.OHLCVData.time.desc())
            .limit(1)
        )
        last_time = await self.database.fetch_one(query)
        if last_time:
            return int(last_time['time'].timestamp() * 1000)
        return None

# async def fetch_ohlcv(self, endpoint, symbol, timeframe, since, params):
    #     """PART III:
    #     Fetch OHLCV data for a given symbol with optional `since` timestamp and limit.
    #     """
    #     all_ohlcv = []
    #     symbol = symbol.replace('-', '/')
    #     pagination_calls = params.get('paginationCalls', 10)
    #     try:
    #         for _ in range(pagination_calls):
    #             await asyncio.sleep(self.exchange.rateLimit / 1000 + 3)  # Respect API rate limit
    #             # Correctly await `ccxt_api_call`
    #             ohlcv_page = await self.rate_limited_request(
    #                 self.ccxt_api.ccxt_api_call,
    #                 self.exchange.fetch_ohlcv,
    #                 endpoint,
    #                 symbol,
    #                 timeframe,
    #                 since,
    #                 params=params
    #             )
    #             if not ohlcv_page:
    #                 break
    #             all_ohlcv.extend(ohlcv_page)
    #             since = ohlcv_page[-1][0] + 1  # Advance pagination
    #
    #         if all_ohlcv:
    #             # Create DataFrame
    #             df = pd.DataFrame(all_ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'volume'])
    #
    #             # Ensure `time` column is converted to datetime format
    #             if not pd.api.types.is_datetime64_any_dtype(df['time']):
    #                 df['time'] = pd.to_datetime(df['time'], unit='ms')
    #
    #             # Ensure `time` is timezone-aware (UTC)
    #             df['time'] = df['time'].dt.tz_localize(None).dt.tz_localize('UTC')
    #             df = df.sort_values(by='time', ascending=True)
    #             return {'symbol': symbol, 'data': df}
    #
    #     except Exception as e:
    #         self.logger.error(f"❌ Error fetching OHLCV data for {symbol}: {e}", exc_info=True)
    #
    #     return None
