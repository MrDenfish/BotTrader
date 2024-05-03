
from database_table_models import Trade, SymbolUpdate
from decimal import Decimal
import pandas as pd
from sqlalchemy import select
import asyncio


class CsvManager:
    def __init__(self, database_ops_mgr, exchange, ccxt_api, logmanager, app_config):

        self.exchange = exchange
        self.app_config = app_config
        self.ccxt_exceptions = ccxt_api
        self.database_ops = database_ops_mgr
        self._take_profit = Decimal(app_config.take_profit)
        self._stop_loss = Decimal(app_config.stop_loss)
        self._csv_dir = app_config.csv_dir
        self.database_dir = app_config.database_dir
        self.sqlite_db_path = app_config.sqlite_db_path
        self.log_manager = logmanager
        self.ticker_cache = None
        self.market_cache = None
        self.start_time = None
        self.holdings = None
        # # Set up the database engine with more flexible configuration
        # self.engine = create_async_engine(
        #     self.app_config.database_url
        # )
        #
        # self.AsyncSessionLocal = sessionmaker(bind=self.engine, class_=AsyncSession, expire_on_commit=False)

    def set_trade_parameters(self, start_time, ticker_cache, market_cache):
        self.start_time = start_time
        self.ticker_cache = ticker_cache
        self.market_cache = market_cache

    @property
    def csv_dir(self):
        return self._csv_dir

    async def process_csv_data(self, session, csv_dir):
        """PART I: Data Gathering and Database Loading.  Read CSV file and process each trade and update last trade time."""
        df = pd.read_csv(csv_dir)
        latest_trades = {}
        try:
            existing_trade_ids = {id_[0] for id_ in await session.execute(select(Trade.trade_id))}
            for _, row in df.iterrows():
                trade_id = row['ID']
                symbol = row['Asset']
                if trade_id not in existing_trade_ids:
                    trade_obj = self.create_trade_from_csv(row)
                    if trade_obj:
                        session.add(trade_obj)
                        # Update the latest trade time for the symbol
                        trade_time = pd.to_datetime(row['Timestamp'])
                        if symbol not in latest_trades or trade_time > latest_trades[symbol]:
                            latest_trades[symbol] = trade_time
                else:
                    pass  # Optionally log skipping duplicate trade ID

            # After processing all rows, update the last update time for each symbol
            for symbol, last_update_time in latest_trades.items():
                await self.database_ops.set_last_update_time(session, symbol, last_update_time)

        except Exception as e:
            await session.rollback()
            self.log_manager.sighook_logger.error(f"Failed to process CSV data: {e}", exc_info=True)

    def create_trade_from_csv(self, row):
        """Create a Trade object from a CSV row."""
        try:
            # Normalize the transaction type
            transaction_type = row['Transaction Type'].lower()
            if 'buy' in transaction_type:
                normalized_transaction_type = 'buy'
            elif 'sell' in transaction_type:
                normalized_transaction_type = 'sell'
            else:
                normalized_transaction_type = 'Unknown'  # or handle as needed

            trade_time = pd.to_datetime(row['Timestamp']).to_pydatetime()
            return Trade(
                trade_time=trade_time,
                trade_id=row['ID'],
                asset=row['Asset'],
                price=Decimal(row['Price at Transaction']),
                amount=Decimal(row['Quantity Transacted']),
                cost=Decimal(row['Subtotal']),
                transaction_type=normalized_transaction_type,
                fee=-1 * (Decimal(row['Fees and/or Spread'])),
                notes=row['Notes']
            )
        except Exception as e:
            self.log_manager.sighook_logger.error(f"Error creating trade from CSV: {e}", exc_info=True)
            return None
