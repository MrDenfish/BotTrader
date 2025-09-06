
import asyncio
import pandas as pd
import TableModels.ohlcv_data

from typing import Any, List
from sqlalchemy.sql import Select
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select, func, delete
from Config.config_manager import CentralConfig
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timedelta, timezone
from MarketDataManager.ohlcv_manager import OHLCVDebugCounter
from sqlalchemy.dialects.postgresql import insert as pg_insert


class MarketManager:
    _instance = None

    @classmethod
    def get_instance(cls, tradebot, exchange, order_manager, trading_strategy, logger_manager, coinbase_api, ccxt_api,
                     ticker_manager, portfolio_manager, max_concurrent_tasks, database_session_manager,
                     db_tables, shared_data_manager):
        if cls._instance is None:
            cls._instance = cls(tradebot, exchange, order_manager, trading_strategy,
                                logger_manager, coinbase_api, ccxt_api, ticker_manager,
                                portfolio_manager, max_concurrent_tasks, database_session_manager,
                                db_tables, shared_data_manager)
        return cls._instance

    def __init__(self, tradebot, exchange, order_manager, trading_strategy, logger_manager, coinbase_api, ccxt_api,
                 ticker_manager, portfolio_manager, max_concurrent_tasks, database_session_manager,
                 db_tables, shared_data_manager):

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
        self.logger_manager = logger_manager
        self.db_tables = db_tables

        self.db_session_manager = database_session_manager

        # ✅ Logging
        if logger_manager.loggers['shared_logger'].name == 'shared_logger':
            self.logger = logger_manager.loggers['shared_logger']

        self.start_time = None
        self.request_semaphore = asyncio.Semaphore(2)
        self.semaphore = asyncio.Semaphore(max_concurrent_tasks)

        if self.coinbase_api is None:
            raise RuntimeError("MarketDataManager requires coinbase_api; got None")

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

    async def fetch_scalar_column(self, session: AsyncSession, query: Select) -> List[Any]:
        """
        Executes a SQLAlchemy select query and returns a flat list of scalar values.

        Example:
            query = select(MyTable.id).where(MyTable.flag == True).limit(10)
            ids = await fetch_scalar_column(session, query)
        """
        result = await session.execute(query)
        return [row[0] for row in result.fetchall()]

    async def fetch_and_store_ohlcv_data(self, symbols, mode='update', timeframe='ONE_MINUTE', limit=300):
        """PART III:
        Called from sender.py
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
                    ohlcv_result = await OHLCVDebugCounter.track(
                        self.coinbase_api.fetch_ohlcv(symbol, params),
                        symbol
                    )#debugging counter to track active requests

                    # ohlcv_result = await self.coinbase_api.fetch_ohlcv(symbol=symbol,params=params)
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
        """
        Store OHLCV data in the database using SQLAlchemy async engine.
        Handles upserts for duplicate entries and caps each symbol at self.max_ohlcv_rows rows.
        """
        try:
            df = ohlcv_data['data']
            symbol = ohlcv_data['symbol']

            # ✅ Prepare batch of records to insert
            records = [
                {
                    "symbol": symbol,
                    "time": pd.Timestamp(row['time']).to_pydatetime(),
                    "open": row['open'],
                    "high": row['high'],
                    "low": row['low'],
                    "close": row['close'],
                    "volume": row['volume'],
                    "last_updated": datetime.utcnow(),  # use UTC
                }
                for row in df.to_dict('records')
            ]

            # ✅ PostgreSQL ON CONFLICT DO UPDATE
            insert_stmt = pg_insert(TableModels.ohlcv_data.OHLCVData).on_conflict_do_update(
                index_elements=["symbol", "time"],
                set_={
                    "open": pg_insert(TableModels.ohlcv_data.OHLCVData).excluded.open,
                    "high": pg_insert(TableModels.ohlcv_data.OHLCVData).excluded.high,
                    "low": pg_insert(TableModels.ohlcv_data.OHLCVData).excluded.low,
                    "close": pg_insert(TableModels.ohlcv_data.OHLCVData).excluded.close,
                    "volume": pg_insert(TableModels.ohlcv_data.OHLCVData).excluded.volume,
                    "last_updated": datetime.utcnow(),
                }
            )

            async with self.db_session_manager.async_session() as session:
                async with session.begin():
                    batch_size = 500
                    for i in range(0, len(records), batch_size):
                        batch = records[i:i + batch_size]
                        await session.execute(insert_stmt, batch)

            # ✅ Cap rows per symbol after insert
            await self.cap_ohlcv_data(symbol, max_rows=self._max_ohlcv_rows)

        except asyncio.CancelledError:
            self.logger.warning("🛑 store_ohlcv_data was cancelled.", exc_info=True)
            raise

        except Exception as e:
            self.logger.error(f"❌ Error storing OHLCV data for {ohlcv_data['symbol']}: {e}", exc_info=True)

    async def cap_ohlcv_data(self, symbol: str, max_rows: int):
        """
        Ensure the OHLCV table for a symbol has no more than `max_rows` entries.
        Deletes the oldest rows (by time) if over the limit.
        """
        try:
            async with self.db_session_manager.async_session() as session:
                async with session.begin():

                    # ✅ Step 1: Count rows for this symbol
                    query = select(func.count()).where(
                        TableModels.ohlcv_data.OHLCVData.symbol == symbol
                    )
                    row_count = await session.scalar(query)

                    if row_count > max_rows:
                        excess = row_count - max_rows

                        # ✅ Step 2: Get oldest timestamps
                        oldest_rows_query = (
                            select(TableModels.ohlcv_data.OHLCVData.time)
                            .where(TableModels.ohlcv_data.OHLCVData.symbol == symbol)
                            .order_by(TableModels.ohlcv_data.OHLCVData.time.asc())
                            .limit(excess)
                        )
                        times_to_delete = await self.fetch_scalar_column(session, oldest_rows_query)

                        if times_to_delete:
                            self.logger.info(f"🔄 Capping {symbol} to {max_rows} rows (removing {excess})")

                            delete_query = (
                                delete(TableModels.ohlcv_data.OHLCVData)
                                .where(
                                    TableModels.ohlcv_data.OHLCVData.symbol == symbol,
                                    TableModels.ohlcv_data.OHLCVData.time.in_(times_to_delete)
                                )
                            )
                            await session.execute(delete_query)

        except Exception as e:
            self.logger.error(f"❌ Error capping OHLCV data for {symbol}: {e}", exc_info=True)


