
import asyncio
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

class OHLCVDebugCounter:
    active_requests = 0
    max_seen = 0

    @classmethod
    async def track(cls, coro, symbol: str):
        cls.active_requests += 1
        cls.max_seen = max(cls.max_seen, cls.active_requests)
        print(f"📊 OHLCV active={cls.active_requests} (max={cls.max_seen}) | Fetching: {symbol}")

        try:
            return await coro
        finally:
            cls.active_requests -= 1
            print(f"✅ Done with {symbol}, active now={cls.active_requests}")

class OHLCVManager:
    _instance = None
    _lock = asyncio.Lock()  # Ensures thread-safety in an async environment

    @classmethod
    async def get_instance(cls, exchange, coinbase_api,ccxt_api, logger_manager, shared_utiles_data_time, market_manager):
        """Ensures only one instance of OHLCVManager is created."""
        if cls._instance is None:
            async with cls._lock:
                if cls._instance is None:  # Double-check after acquiring the lock
                    cls._instance = cls(exchange, coinbase_api,ccxt_api, logger_manager, shared_utiles_data_time, market_manager)
        return cls._instance

    def __init__(self, exchange, coinbase_api, ccxt_api, logger_manager, shared_utiles_data_time, market_manager):
        if OHLCVManager._instance is not None:
            raise Exception("OHLCVManager is a singleton and has already been initialized!")

        self.exchange = exchange
        self.ccxt_api = ccxt_api
        self.coinbase_api = coinbase_api
        self.logger_manager = logger_manager  # 🙂
        if logger_manager.loggers['shared_logger'].name == 'shared_logger':  # 🙂
            self.logger = logger_manager.loggers['shared_logger']
        self.market_manager = market_manager
        self.shared_utiles_data_time = shared_utiles_data_time
        self.ohlcv_cache = {}  # Temporary storage for OHLCV data

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
                    return df.iloc[0]['close'], df.iloc[-1]['close'], df['close'].mean()

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

                return oldest_close, newest_close, average_close
            else:
                return None, None, None

        except Exception as e:
            self.logger.error(f"❌ Error fetching last 5-minute OHLCV for {symbol}: {e}", exc_info=True)
            return None, None, None

    async def fetch_volatility_5min(self, symbol, threshold_multiplier=1.1):
        """
        Computes short-term volatility using standard deviation of log returns and returns both the current and dynamic threshold.

        Args:
            symbol (str): Trading pair (e.g., 'BTC-USD')
            timeframe (str): Resolution of OHLCV data
            limit (int): Number of recent candles to consider (default: 5)
            threshold_multiplier (float): Multiplier for adaptive threshold (default: 1.1)

        Returns:
            Tuple[float, float] | Tuple[None, None]: (volatility_5m, adaptive_threshold)
        """
        try:
            # Use existing cache if available, otherwise fetch
            limit = 5  # Default to last 5 minutes
            if symbol in self.ohlcv_cache:
                df = self.ohlcv_cache[symbol]['data']
            else:
                _, _, _ = await self.fetch_last_5min_ohlcv(symbol)
                df = self.ohlcv_cache[symbol]['data'] if symbol in self.ohlcv_cache else None

            if df is None or df.empty or len(df) < limit:
                return None, None

            df = df.tail(limit).copy()
            df['log_return'] = np.log(df['close'] / df['close'].shift(1))
            volatility_5m = df['log_return'].std()

            adaptive_threshold = round(volatility_5m * threshold_multiplier, 6)
            return round(volatility_5m, 6), adaptive_threshold

        except Exception as e:
            self.logger.error(f"❌ Error in fetch_volatility_5min for {symbol}: {e}", exc_info=True)
            return None, None



