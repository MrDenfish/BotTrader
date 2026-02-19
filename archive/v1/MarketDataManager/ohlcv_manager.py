
import asyncio
import numpy as np
import pandas as pd

from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timedelta, timezone
from Shared_Utils.logger import get_logger

# Module-level logger for debug counter
_ohlcv_logger = get_logger('ohlcv_manager', context={'component': 'ohlcv_manager'})


class OHLCVDebugCounter:
    active_requests = 0
    max_seen = 0

    @classmethod
    async def track(cls, coro, symbol: str):
        cls.active_requests += 1
        cls.max_seen = max(cls.max_seen, cls.active_requests)
        _ohlcv_logger.debug("OHLCV fetch started",
                          extra={'symbol': symbol, 'active_requests': cls.active_requests, 'max_seen': cls.max_seen})

        try:
            return await coro
        finally:
            cls.active_requests -= 1
            _ohlcv_logger.debug("OHLCV fetch completed",
                              extra={'symbol': symbol, 'active_requests': cls.active_requests})

class OHLCVManager:
    _instance = None
    _lock = asyncio.Lock()

    @classmethod
    async def get_instance(cls, exchange, coinbase_api, ccxt_api, logger_manager,
                           shared_utiles_data_time, market_manager,
                           database_session_manager):
        """Ensures only one instance of OHLCVManager is created."""
        if cls._instance is None:
            async with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(
                        exchange=exchange,
                        coinbase_api=coinbase_api,
                        ccxt_api=ccxt_api,
                        logger_manager=logger_manager,
                        shared_utiles_data_time=shared_utiles_data_time,
                        market_manager=market_manager,
                        database_session_manager=database_session_manager
                    )
        return cls._instance

    def __init__(self, exchange, coinbase_api, ccxt_api, logger_manager,
                 shared_utiles_data_time, market_manager,
                 database_session_manager):

        if OHLCVManager._instance is not None:
            raise Exception("OHLCVManager is a singleton and has already been initialized!")

        self.exchange = exchange
        self.coinbase_api = coinbase_api
        self.ccxt_api = ccxt_api
        self.logger_manager = logger_manager

        self.db_session_manager = database_session_manager

        # ✅ Setup logging
        self.logger = get_logger('ohlcv_manager', context={'component': 'ohlcv_manager'})

        self.market_manager = market_manager
        self.shared_utiles_data_time = shared_utiles_data_time
        self.ohlcv_cache = {}  # In-memory cache

    async def fetch_last_5min_ohlcv(self, symbol, timeframe='ONE_MINUTE', limit=5):
        """
        Fetches the last 5 minutes of OHLCV data dynamically from the REST API.
        Uses a cache to prevent excessive API requests.
        Args:
            symbol (str): Trading pair (e.g., 'BTC-USD').
            timeframe (str): OHLCV timeframe ('ONE_MINUTE' for 1-minute candles).
            limit (int): Number of candles to retrieve (default: 5).

        Returns:
            Tuple[float, float, float] | Tuple[None, None, None]:
            Oldest close, newest close, and 5-min average close.
        """
        try:
            now = datetime.now(timezone.utc).replace(microsecond=0)
            five_min_ago = now - timedelta(minutes=5)

            # ✅ Step 1: Check Cache
            if symbol in self.ohlcv_cache:
                cached_data = self.ohlcv_cache[symbol]
                if cached_data["timestamp"] >= five_min_ago:
                    self.logger.debug(f"✅ Using cached OHLCV data for {symbol}")
                    df = cached_data['data']
                    return df, df.iloc[0]['close'], df.iloc[-1]['close'], df['close'].mean()

            # ✅ Step 2: Prepare timestamp range
            safe_since = int((five_min_ago - timedelta(seconds=10)).timestamp())
            safe_since = self.shared_utiles_data_time.time_sanity_check(safe_since)
            safe_since -= 1  # final buffer in seconds

            now_ts = int(now.timestamp())

            params = {
                'start': safe_since,
                'end': now_ts,
                'granularity': timeframe,
                'limit': limit
            }
            ohlcv_result = await OHLCVDebugCounter.track(
                self.coinbase_api.fetch_ohlcv(symbol, params),
                symbol
            ) # debugging counter to track active requests
            #ohlcv_result = await self.coinbase_api.fetch_ohlcv(symbol, params=params)

            if ohlcv_result and not ohlcv_result['data'].empty:
                df = ohlcv_result['data']
                df['time'] = pd.to_datetime(df['time'], unit='ms', utc=True)
                df = df.set_index('time')
                df = df.resample('1min').asfreq().ffill().reset_index()

                self.ohlcv_cache[symbol] = {"timestamp": now, "data": df}

                oldest_close = df.iloc[0]['close']
                newest_close = df.iloc[-1]['close']
                average_close = df['close'].mean()

                return df, oldest_close, newest_close, average_close
            else:
                return None, None, None, None

        except Exception as e:
            self.logger.error(f"❌ Error fetching last 5-minute OHLCV for {symbol}: {e}", exc_info=True)
            return None, None, None

    async def fetch_volatility_5min(self, symbol, threshold_multiplier=1.1):
        try:
            # Always ensure cache is fresh; function will reuse cache if valid
            await self.fetch_last_5min_ohlcv(symbol)

            df = self.ohlcv_cache.get(symbol, {}).get('data')
            if df is None or df.empty or len(df) < 5:
                return None, None

            df = df.tail(5).copy()
            # guard against divide-by-zero or NaNs
            prev = df['close'].shift(1)
            valid = (prev > 0) & (df['close'] > 0)
            if valid.sum() < 2:
                return None, None

            df['log_return'] = np.log(df['close'][valid] / prev[valid])
            vol_5m = float(df['log_return'].std())
            thr = round(vol_5m * threshold_multiplier, 6)
            return round(vol_5m, 6), thr

        except Exception as e:
            self.logger.error(f"❌ Error in fetch_volatility_5min for {symbol}: {e}", exc_info=True)
            return None, None




