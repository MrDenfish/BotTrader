
import asyncio
import pytest
from datetime import datetime, timezone

from MarketDataManager.market_manager import MarketManager
from database_manager.database_session_manager import DatabaseSessionManager
from Api_manager.coinbase_api import CoinbaseAPI

# Mock logger used to satisfy constructor dependency
class MockLoggerManager:
    loggers = {"shared_logger": type("Logger", (), {"name": "shared_logger"})()}
# Mock dependencies
class MockProfitExtras:
    pass

class MockSharedDataManager:
    pass

class TestMarketManager(MarketManager):
    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            logger_manager = MockLoggerManager()
            profit_extras = MockProfitExtras()
            shared_data_manager = MockSharedDataManager()

            db_session = DatabaseSessionManager.get_instance(
                profit_extras=profit_extras,
                logger_manager=logger_manager,
                shared_data_manager=shared_data_manager
            )
            db_tables = db_session.db_tables
            cls._instance = cls(
                tradebot=None,
                exchange=None,
                order_manager=None,
                trading_strategy=None,
                logger_manager=MockLoggerManager(),
                coinbase_api=CoinbaseAPI.get_instance(),
                ccxt_api=None,
                ticker_manager=None,
                portfolio_manager=None,
                max_concurrent_tasks=2,
                database=db_session.database,
                db_tables=db_tables,
                shared_data_manager=None,
            )
        return cls._instance

pytestmark = pytest.mark.asyncio
MAX_ROWS = 1440
ALLOWED_DRIFT_MINUTES = 2

@pytest.mark.asyncio
async def test_ohlcv_database_integrity_and_drift():
    symbols = ['BTC-USD', 'ETH-USD']
    market_manager = TestMarketManager.get_instance()
    coinbase_api = market_manager.coinbase_api

    for symbol in symbols:
        df = await market_manager.fetch_ohlcv_data_from_db(symbol)
        assert df is not None and not df.empty, f"No data in DB for {symbol}"
        assert len(df) <= MAX_ROWS, f"{symbol} has more than {MAX_ROWS} rows in DB"

        db_latest_time = df['time'].max()
        assert isinstance(db_latest_time, datetime)

        try:
            live_df = await coinbase_api.fetch_ohlcv(symbol, timeframe='1m', limit=1)
            live_latest_time = live_df['time'].max()
        except Exception as e:
            pytest.fail(f"Live OHLCV fetch failed for {symbol}: {e}")

        drift = (live_latest_time - db_latest_time).total_seconds() / 60
        print(f"{symbol}: DB time = {db_latest_time}, Live time = {live_latest_time}, Drift = {drift:.2f} min")
        assert drift <= ALLOWED_DRIFT_MINUTES, f"{symbol} is lagging by {drift:.2f} minutes"


