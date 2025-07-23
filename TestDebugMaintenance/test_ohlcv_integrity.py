
import asyncio
import pandas as pd
from decimal import Decimal
from datetime import datetime, timedelta, timezone

from MarketDataManager.market_manager import MarketManager
from database_manager.database_session_manager import DatabaseSessionManager
from TableModels.database_table_models import DatabaseTables
from Api_manager.coinbase_api import CoinbaseAPI
from sighook.trading_strategy import TradingStrategy

# --- Mock Components ---
class MockLogger:
    name = "shared_logger"
    def info(self, msg, *args, **kwargs): print(f"[INFO] {msg}")
    def warning(self, msg, *args, **kwargs): print(f"[WARN] {msg}")
    def error(self, msg, *args, **kwargs): print(f"[ERROR] {msg}")

class MockLoggerManager(MockLogger):
    loggers = {"shared_logger": MockLogger()}

class MockComponent:
    pass

class MockSharedUtilsPrecision:
    def safe_convert(self, value, default):
        try:
            return Decimal(value)
        except Exception:
            return Decimal(default)

# --- Custom Test MarketManager ---
class TestMarketManager(MarketManager):
    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            logger_manager = MockLoggerManager()
            db_session = DatabaseSessionManager.get_instance(
                profit_extras=MockComponent(),
                logger_manager=logger_manager,
                shared_data_manager=MockComponent()
            )
            coinbase_api = CoinbaseAPI(
                session=MockComponent(),
                shared_utils_utility=MockComponent(),
                logger_manager=logger_manager,
                shared_utils_precision=MockComponent()
            )

            cls._instance = cls(
                tradebot=None,
                exchange=None,
                order_manager=None,
                trading_strategy=None,
                logger_manager=logger_manager,
                coinbase_api=coinbase_api,
                ccxt_api=None,
                ticker_manager=None,
                portfolio_manager=None,
                max_concurrent_tasks=2,
                database=db_session.database,
                db_tables=DatabaseTables(),
                shared_data_manager=None,
            )
        return cls._instance

# --- Helper Function ---
async def fetch_live_ohlcv_latest_time(coinbase_api, symbol):
    end_dt = datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(minutes=2)
    params = {
        "start": int(start_dt.timestamp()),
        "end": int(end_dt.timestamp()),
        "granularity": 'ONE_MINUTE',
        "limit": 1
    }

    result = await coinbase_api.fetch_ohlcv(symbol, params=params)
    live_df = result.get('data') if result else None
    if live_df is None or live_df.empty:
        raise ValueError(f"No live OHLCV data returned for {symbol}")
    live_df['time'] = pd.to_datetime(live_df['time'], utc=True)
    return live_df['time'].max()

# --- Test Setup ---
pytestmark = pytest.mark.asyncio
MAX_ROWS = 1440
ALLOWED_DRIFT_MINUTES = 2

# --- Test Case ---
@pytest.mark.asyncio
async def test_ohlcv_database_integrity_and_drift():
    symbols = ['SOL-USD', 'DOGE-USD']

    logger_manager = MockLoggerManager()

    coinbase_api = CoinbaseAPI(
        session=MockComponent(),
        shared_utils_utility=MockComponent(),
        logger_manager=logger_manager,
        shared_utils_precision=MockSharedUtilsPrecision()
    )

    db_session = DatabaseSessionManager.get_instance(
        profit_extras=MockComponent(),
        logger_manager=logger_manager,
        shared_data_manager=MockComponent()
    )

    if not db_session.database.is_connected:
        await db_session.connect()

    trading_strategy = TradingStrategy(
        webhook=MockComponent(),
        ticker_manager=MockComponent(),
        exchange=MockComponent(),
        alerts=MockComponent(),
        logger_manager=logger_manager,
        ccxt_api=MockComponent(),
        metrics=MockComponent(),
        max_concurrent_tasks=2,
        database_session_mngr=db_session,
        sharded_utils_print=MockComponent(),
        db_tables=DatabaseTables(),
        shared_utils_precision=MockSharedUtilsPrecision(),
        shared_data_manager=MockComponent()
    )

    for symbol in symbols:
        df = await trading_strategy.fetch_ohlcv_data_from_db(symbol)
        assert df is not None and not df.empty, f"❌ No OHLCV data in DB for {symbol}"
        assert len(df) <= MAX_ROWS, f"❌ {symbol} has more than {MAX_ROWS} rows in DB"

        db_latest_time = df['time'].max()
        assert isinstance(db_latest_time, datetime)

        try:
            live_latest_time = await fetch_live_ohlcv_latest_time(coinbase_api, symbol)
        except Exception as e:
            pytest.fail(f"❌ Live OHLCV fetch failed for {symbol}: {e}")

        drift = (live_latest_time - db_latest_time).total_seconds() / 60
        print(f"{symbol}: DB = {db_latest_time}, Live = {live_latest_time}, Drift = {drift:.2f} min")

        assert drift <= ALLOWED_DRIFT_MINUTES, f"❌ {symbol} is lagging by {drift:.2f} min"
        print(f"✅ {symbol}: Drift OK ({drift:.2f} min)")

