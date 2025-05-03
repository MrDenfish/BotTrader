
# SharedDataManager/trade_recorder.py

from TableModels.trade_record import TradeRecord
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import NoResultFound
from sqlalchemy.future import select
from decimal import Decimal
from datetime import datetime

class TradeRecorder:
    """
    Handles recording of trades into the trade_records table.
    """

    def __init__(self, database_session_manager, logger):
        self.db_session_manager = database_session_manager
        self.logger = logger

    async def record_trade(self, trade_data: dict):
        """
        Records a new trade into the database.
        """
        async with self.db_session_manager.async_session_factory() as session:
            try:
                trade_record = TradeRecord(
                    symbol=trade_data['symbol'],
                    side=trade_data['side'],
                    order_time=trade_data.get('order_time', datetime.utcnow()),
                    size=Decimal(str(trade_data['amount'])),
                    pnl_usd=None,
                    total_fees_usd=None,
                    price=Decimal(str(trade_data['price'])),
                    order_id=trade_data['order_id'],
                    parent_id=trade_data['order_id'],
                    trigger=trade_data['trigger'],
                    status=trade_data['status']
                )
                session.add(trade_record)
                await session.commit()

                if self.logger:
                    self.logger.info(
                        f"✅ Trade recorded successfully: {trade_record.symbol} {trade_record.side} @ {trade_record.price}")

            except Exception as e:
                await session.rollback()
                if self.logger:
                    self.logger.error(f"❌ Error recording trade: {e}", exc_info=True)

    async def fetch_all_trades(self):
        """
        Fetch all recorded trades.
        """
        async with self.db_session_manager.session() as session:
            result = await session.execute(select(TradeRecord))
            trades = result.scalars().all()
            return trades

    async def fetch_recent_trades(self, limit=10):
        """
        Fetch the most recent trades (default: last 10).
        """
        async with self.db_session_manager.session() as session:
            result = await session.execute(
                select(TradeRecord).order_by(TradeRecord.order_time.desc()).limit(limit)
            )
            trades = result.scalars().all()
            return trades

    async def delete_trade(self, order_id: str) -> None:
        """Remove a trade row entirely when an order is cancelled."""
        async with self.db_session_manager.session() as session:
            rec = await session.get(TradeRecord, order_id)
            if rec:
                await session.delete(rec)
