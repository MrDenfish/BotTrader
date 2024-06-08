
import os
import pandas as pd
from decimal import Decimal
from sqlalchemy import select
import uuid
from copy import deepcopy
from database_table_models import Trade


class CsvManager:
    def __init__(self, utility, db_tables, database_ops_mgr, exchange, ccxt_api, logmanager, app_config):

        self.exchange = exchange
        self.app_config = app_config
        self.ccxt_exceptions = ccxt_api
        self.utility = utility
        self.database_ops = database_ops_mgr
        self.db_tables = db_tables
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

    def set_trade_parameters(self, start_time, ticker_cache, market_cache):
        self.start_time = start_time
        self.ticker_cache = ticker_cache
        self.market_cache = market_cache

    @property
    def csv_dir(self):
        return self._csv_dir

    @staticmethod
    def load_and_clean_csv(file_path):
        df = pd.read_csv(file_path)
        # Handle missing trade_id values  and generate a unique trade_id if missing
        df['ID'].fillna(value=pd.Series([str(uuid.uuid4()) for _ in range(df.shape[0])]), inplace=True)
        return df

    @staticmethod
    def get_chunk_files(chunks_dir):
        """Get a list of all chunk files in the given directory."""
        return [os.path.join(chunks_dir, f) for f in os.listdir(chunks_dir) if f.endswith('.csv')]

    async def process_csv_data(self, session, chunks_dir):
        """Read CSV file and process each trade and update last trade time."""
        try:
            chunk_files = self.get_chunk_files(chunks_dir)
            latest_trades = {}
            existing_trade_ids = {id_[0] for id_ in await session.execute(select(Trade.trade_id))}
            for chunk_file in chunk_files:
                df = self.load_and_clean_csv(chunk_file)
                for _, row in df.iterrows():
                    trade_id = row['ID']
                    asset = row['Asset']
                    quantity = row['Quantity Transacted']
                    if trade_id not in existing_trade_ids:
                        transaction_type = row['Transaction Type'].lower()
                        trade_time = self.utility.standardize_timestamp(row['Timestamp'])
                        if 'convert' in transaction_type:
                            note_parts = row['Notes'].split(' ')

                            asset_from, quantity_from = note_parts[2], Decimal(note_parts[1])
                            asset_to, quantity_to = note_parts[5], Decimal(note_parts[4])
                            asset = {'asset': asset, 'Quantity Transacted': quantity, 'from_asset': asset_from,
                                     'from_amount': -quantity_from,
                                     'to_asset': asset_to, 'to_amount': quantity_to}

                            sell_trade = await self.db_tables.create_trade_from_row(session, row, asset, trade_time,
                                                                                    csv=True)
                            row_copy = deepcopy(row)
                            buy_trade = await self.db_tables.create_trade_from_row(session, row, asset, trade_time, csv=True)

                            if sell_trade:
                                session.add(sell_trade[0])
                            if buy_trade:
                                session.add(buy_trade[1])
                        else:
                            asset = {'asset': asset, 'Quantity Transacted': quantity, 'from_asset': _, 'from_amount': _,
                                     'to_asset': _, 'to_amount': _}
                            if 'buy' in transaction_type or 'deposit' in transaction_type or 'receive' in transaction_type or \
                                    'reward' in transaction_type or 'income' in transaction_type:
                                normalized_transaction_type = 'buy'
                                buy_trade = await self.db_tables.create_trade_from_row(session, row, asset, trade_time,
                                                                                       csv=True)
                                if buy_trade:
                                    session.add(buy_trade)
                            elif 'sell' in transaction_type or 'withdrawal' in transaction_type or 'send' in transaction_type:
                                normalized_transaction_type = 'sell'
                                sell_trade = await self.db_tables.create_trade_from_row(session, row, asset, trade_time,
                                                                                        csv=True)
                                if sell_trade:
                                    session.add(sell_trade)
                            else:
                                normalized_transaction_type = 'Unknown'

                        trade_time = self.utility.standardize_timestamp(row['Timestamp'])
                        symbol = row['Asset'] + '/' + row['Price Currency']
                        if symbol not in latest_trades or trade_time > latest_trades[symbol]:
                            latest_trades[symbol] = trade_time

            for symbol, last_update_time in latest_trades.items():
                if symbol == "USDT/USDT" or symbol == "USD/USD":
                    continue
                await self.database_ops.set_last_update_time(session, symbol, last_update_time)

        except Exception as e:
            self.log_manager.sighook_logger.error(f"Failed to process CSV data: {e}", exc_info=True)
            await session.rollback()
    # async def old_process_csv_data(self, session, csv_dir):
    #     """PART I: Data Gathering and Database Loading.  Read CSV file and process each trade  update last trade time."""
    #     df = pd.read_csv(csv_dir)
    #     existing_trade_ids = {id_[0] for id_ in await session.execute(select(Trade.trade_id))}  # new
    #     latest_trades = {}
    #     try:
    #         for _, row in df.iterrows():
    #             trade_id = row['ID']
    #             symbol = row['Asset'] + '/' + row['Price Currency']
    #             asset = row['Asset']
    #             if trade_id not in existing_trade_ids:
    #                 trade_obj = await self.create_trade_from_csv(session, row)
    #                 if trade_obj:
    #                     session.add(trade_obj)
    #                     # Update the latest trade time for the symbol
    #                     trade_time = self.utility.standardize_timestamp(row['Timestamp'])
    #                     if symbol not in latest_trades or trade_time > latest_trades[symbol]:
    #                         latest_trades[symbol] = trade_time
    #             else:
    #                 pass  # Optionally log skipping duplicate trade ID
    #
    #         # After processing all rows, update the last update time for each symbol
    #         for symbol, last_update_time in latest_trades.items():
    #             if symbol == "USDT/USDT" or symbol == "USD/USD":
    #                 continue
    #             await self.database_ops.set_last_update_time(session, symbol, last_update_time)
    #
    #     except Exception as e:
    #         self.log_manager.sighook_logger.error(f"Failed to process CSV data: {e}", exc_info=True)
    #         await session.rollback()

    # async def old_create_trade_from_csv(self, session, row):
    #     """Create a Trade object from a CSV row."""
    #     try:
    #         # Normalize the transaction type
    #         transaction_type = row['Transaction Type'].lower()
    #         trade_time = self.utility.standardize_timestamp(row['Timestamp'])
    #         cost = 0
    #         proceeds = 0
    #         fee = 0
    #         total = 0
    #         if 'buy' in transaction_type: # deposit, Receive, Reward, Income
    #             normalized_transaction_type = 'buy'
    #             cost = Decimal(row['Subtotal'])
    #             fee = Decimal(row['Fees and/or Spread'])
    #             total = -1*(cost + fee)
    #         elif 'sell' in transaction_type: #send,withdrawal,
    #             normalized_transaction_type = 'sell'
    #             fee = Decimal(row['Fees and/or Spread'])
    #             proceeds = Decimal(row['Subtotal'])
    #             total = proceeds - fee
    #         elif 'convert' in transaction_type:
    #             # Extract both assets and quantities from the Notes or other relevant fields
    #             note_parts = row['Notes'].split(' ')
    #             quantity_from = Decimal(note_parts[1])
    #             asset_from = note_parts[2]
    #             quantity_to = Decimal(note_parts[4])
    #             asset_to = note_parts[5]
    #
    #             # Create sell transaction for the asset being converted from
    #             sell_trade = await self.db_tables.create_trade_from_row(
    #                 row, asset_from, -quantity_from, trade_time, 'sell', Decimal(row['Subtotal']),
    #                 Decimal(row['Fees and/or Spread'])
    #             )
    #
    #             # Create buy transaction for the asset being converted to
    #             buy_trade = await self.db_tables.create_trade_from_row(
    #                 row, asset_to, quantity_to, trade_time, csv, 'buy',
    #                 Decimal(row['Total (inclusive of fees and/or spread)']), Decimal(row['Fees and/or Spread'])
    #             )
    #
    #             # Add both transactions to the session
    #             if sell_trade:
    #                 session.add(sell_trade)
    #             if buy_trade:
    #                 session.add(buy_trade)
    #
    #             return (sell_trade, buy_trade)
    #         else:
    #             normalized_transaction_type = 'Unknown'  # or handle as needed
    #         asset = row['Asset']
    #         csv = True
    #         return await self.db_tables.create_from_trade_from_row(row, asset, trade_time, csv)
    #     except Exception as e:
    #         self.log_manager.sighook_logger.error(f"Error creating trade from CSV: {e}", exc_info=True)
    #         return None
