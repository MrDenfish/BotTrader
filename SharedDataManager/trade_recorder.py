
# SharedDataManager/trade_recorder.py

from TableModels.trade_record import TradeRecord
from typing import Optional
from sqlalchemy import text
from sqlalchemy.future import select
from decimal import Decimal, ROUND_DOWN
from datetime import datetime

class TradeRecorder:
    """
    Handles recording of trades into the trade_records table.
    """

    def __init__(self, database_session_manager, logger, shared_utils_precision, coinbase_api):
        self.db_session_manager = database_session_manager
        self.logger = logger
        self.shared_utils_precision = shared_utils_precision
        self.coinbase_api = coinbase_api

    async def record_trade(self, trade_data: dict):
        """
        Records a new trade into the database.
        """
        async with self.db_session_manager.async_session_factory() as session:
            try:
                order_time_raw = trade_data.get('order_time', datetime.utcnow())
                order_time = (
                    datetime.fromisoformat(order_time_raw)
                    if isinstance(order_time_raw, str)
                    else order_time_raw
                )
                base_deci, quote_deci, _, _ = self.shared_utils_precision.fetch_precision(trade_data['symbol'])
                parent_id = trade_data.get('parent_id')

                if trade_data['side'].lower() == 'sell' and not parent_id:
                    parent_id = await self.find_latest_unlinked_buy(trade_data['symbol'])
                    if not parent_id and self.logger:
                        self.logger.warning(f"⚠️ No parent BUY order found for SELL {trade_data['symbol']} — orphaned sell.")
                elif trade_data['side'].lower() == 'buy':
                    parent_id = trade_data['order_id']  # self-linked

                trade_record = TradeRecord(
                    symbol=trade_data['symbol'],
                    side=trade_data['side'],
                    order_time=order_time,
                    size=self.shared_utils_precision.safe_convert(trade_data['amount'], base_deci),
                    pnl_usd=None,
                    total_fees_usd=None,
                    price=self.shared_utils_precision.safe_convert(trade_data['price'], quote_deci),
                    order_id=trade_data['order_id'],
                    parent_id=parent_id,
                    trigger=trade_data['trigger'],
                    status=trade_data['status'],
                    source=trade_data['source']

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
        async with self.db_session_manager.async_session() as session:
            result = await session.execute(select(TradeRecord))
            trades = result.scalars().all()
            return trades

    async def fetch_recent_trades(self, limit=10):
        """
        Fetch the most recent trades (default: last 10).
        """
        async with self.db_session_manager.async_session() as session:
            result = await session.execute(
                select(TradeRecord).order_by(TradeRecord.order_time.desc()).limit(limit)
            )
            trades = result.scalars().all()
            return trades

    async def delete_trade(self, order_id: str):
        """
        Deletes a trade from the database by its order_id.
        """
        async with self.db_session_manager.async_session_factory() as session:
            try:
                result = await session.get(TradeRecord, order_id)
                if result:
                    await session.delete(result)
                    await session.commit()
                    if self.logger:
                        self.logger.info(f"🗑️ Deleted trade record for order_id {order_id}")
                else:
                    if self.logger:
                        self.logger.warning(f"⚠️ Tried to delete trade {order_id}, but it was not found.")
            except Exception as e:
                await session.rollback()
                if self.logger:
                    self.logger.error(f"❌ Failed to delete trade {order_id}: {e}", exc_info=True)

    from sqlalchemy import text

    async def find_latest_unlinked_buy(self, symbol: str) -> Optional[str]:
        """
        Finds the most recent buy order for a symbol that has not yet been linked to a sell.
        Returns the order_id if found, else None.
        """
        async with self.db_session_manager.async_session() as session:
            try:
                result = await session.execute(text("""
                    SELECT order_id FROM trade_records
                    WHERE symbol = :symbol
                      AND side = 'buy'
                      AND order_id NOT IN (
                          SELECT parent_id FROM trade_records
                          WHERE symbol = :symbol AND side = 'sell'
                      )
                    ORDER BY order_time DESC
                    LIMIT 1
                """), {"symbol": symbol})
                row = result.fetchone()
                return row[0] if row else None
            except Exception as e:
                if self.logger:
                    self.logger.error(f"❌ Error in find_latest_unlinked_buy for {symbol}: {e}", exc_info=True)
                return None

    async def find_latest_filled_size(self, symbol: str, side: str = 'buy') -> Optional[Decimal]:
        """
        Returns the size of the most recent filled trade for a given symbol and side (buy/sell).
        """
        async with self.db_session_manager.async_session() as session:
            try:
                base_deci, _, _, _ = self.shared_utils_precision.fetch_precision(symbol)
                result = await session.execute(
                    select(TradeRecord)
                    .where(TradeRecord.symbol == symbol)
                    .where(TradeRecord.side == side)
                    .where(TradeRecord.status.ilike('placed'))  # or 'filled' depending on your usage
                    .order_by(TradeRecord.order_time.desc())
                    .limit(1)
                )
                record = result.scalar_one_or_none()
                if record:
                    return Decimal(record.size).quantize(Decimal(f'1e-{base_deci}'), rounding=ROUND_DOWN)
                else:
                    # Exchange query here (via a REST manager or injected client)
                    params = {
                        "product_id": [symbol],
                        "order_side": "BUY",
                        "order_status": ["FILLED"],
                        "limit": 1
                    }
                    result = await self.coinbase_api.get_historical_orders_batch(params)
                    for order in result.get("orders", []):
                        if order["status"] == "FILLED":
                            base_size = Decimal(order.get("filled_size", "0"))
                            price = Decimal(order.get("average_filled_price", "0"))
                            return base_size.quantize(Decimal(f'1e-{base_deci}'), rounding=ROUND_DOWN)
                            # return {
                            #     "filled_size": base_size,
                            #     "price": price,
                            #     "order_id": order.get("order_id"),
                            #     "source": "exchange"
                            # }
            except Exception as e:
                self.logger.error(f"❌ Error in find_latest_filled_size for {symbol}: {e}", exc_info=True)
            return None