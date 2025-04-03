
import asyncio
from datetime import datetime, timedelta, timezone

import pandas as pd


class OHLCVManager:
    _instance = None
    _lock = asyncio.Lock()  # Ensures thread-safety in an async environment

    @classmethod
    async def get_instance(cls, exchange, ccxt_api, logger_manager, shared_utiles_data_time, market_manager):
        """Ensures only one instance of OHLCVManager is created."""
        if cls._instance is None:
            async with cls._lock:
                if cls._instance is None:  # Double-check after acquiring the lock
                    cls._instance = cls(exchange, ccxt_api, logger_manager, shared_utiles_data_time, market_manager)
        return cls._instance

    def __init__(self, exchange, ccxt_api, logger_manager, shared_utiles_data_time, market_manager):
        if OHLCVManager._instance is not None:
            raise Exception("OHLCVManager is a singleton and has already been initialized!")

        self.exchange = exchange
        self.ccxt_api = ccxt_api
        self.logger = logger_manager.get_logger('webhook_logger')
        self.market_manager = market_manager
        self.shared_utiles_data_time = shared_utiles_data_time
        self.ohlcv_cache = {}  # Temporary storage for OHLCV data

    async def fetch_last_5min_ohlcv(self, symbol, timeframe='1m', limit=5):
        """
        Fetches the last 5 minutes of OHLCV data dynamically from the REST API.
        Uses a cache to prevent excessive API requests.

        Args:
            symbol (str): Trading pair (e.g., 'BTC-USD').
            timeframe (str): OHLCV timeframe ('1m' for 1-minute candles).
            limit (int): Number of candles to retrieve (default: 5).

        Returns:
            Tuple[float, float] | Tuple[None, None]: Oldest and newest close values.
        """
        try:
            now = datetime.now(timezone.utc).replace(microsecond=0)
            five_min_ago = now - timedelta(minutes=5)

            # ✅ Step 1: Check Cache (If Fresh, Use It)
            if symbol in self.ohlcv_cache:
                cached_data = self.ohlcv_cache[symbol]
                last_cached_time = cached_data["timestamp"]
                if last_cached_time >= five_min_ago:
                    self.logger.debug(f"✅ Using cached OHLCV data for {symbol}")
                    newest_close = cached_data['data'].iloc[-1]['close']
                    oldest_close = cached_data['data'].iloc[0]['close']
                    return oldest_close, newest_close

            # ✅ Step 2: Prepare safe `since` timestamp
            safe_since = int((five_min_ago - timedelta(seconds=10)).timestamp() * 1000)
            safe_since = self.shared_utiles_data_time.time_sanity_check(safe_since)
            safe_since -= 1000  # final buffer

            endpoint = 'public'
            params = {'paginate': False}

            self.logger.debug(f"� Fetching fresh OHLCV data for {symbol} (Last 5 min)")
            ohlcv_result = await self.market_manager.fetch_ohlcv(endpoint, symbol, timeframe, safe_since, params=params)

            if ohlcv_result and not ohlcv_result['data'].empty:
                df = ohlcv_result['data']
                df['time'] = pd.to_datetime(df['time'], unit='ms', utc=True)
                df = df.set_index('time')
                df = df.resample('1min').asfreq().ffill().reset_index()

                self.ohlcv_cache[symbol] = {"timestamp": now, "data": df}
                newest_close = df.iloc[-1]['close']
                oldest_close = df.iloc[0]['close']

                return oldest_close, newest_close
            else:
                self.logger.warning(f"⚠️ No new OHLCV data for {symbol}")
                return None, None

        except Exception as e:
            self.logger.error(f"❌ Error fetching last 5-minute OHLCV for {symbol}: {e}", exc_info=True)
            return None, None

    async def fetch_volatility_5min(self, symbol, timeframe='1m', limit=5, threshold_multiplier=1.1):
        """
        Computes short-term volatility and returns both the current and dynamic threshold.

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
            if symbol in self.ohlcv_cache:
                df = self.ohlcv_cache[symbol]['data']
            else:
                _, _ = await self.fetch_last_5min_ohlcv(symbol, timeframe, limit)
                df = self.ohlcv_cache[symbol]['data'] if symbol in self.ohlcv_cache else None

            if df is None or df.empty or len(df) < limit:
                return None, None

            df = df.tail(limit).copy()
            df['volatility'] = df['high'] - df['low']

            volatility_5m = df['volatility'].mean()

            volatility_mean = df['volatility'].rolling(window=limit).mean().iloc[-1]  # Optional
            adaptive_threshold = round(volatility_mean * threshold_multiplier, 6)
            return round(volatility_5m, 6), adaptive_threshold

        except Exception as e:
            self.logger.error(f"❌ Error in fetch_volatility_5min for {symbol}: {e}", exc_info=True)
            return None, None


