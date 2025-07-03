

from TableModels.trade_record import TradeRecord
from sqlalchemy.dialects.postgresql import insert as pg_insert
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
        Records a new trade into the database. Computes PnL for sells.
        Uses upsert to handle duplicate order_id conflicts gracefully.
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
                side = trade_data['side'].lower()
                amount = self.shared_utils_precision.safe_convert(trade_data['amount'], base_deci)
                price = self.shared_utils_precision.safe_convert(trade_data['price'], quote_deci)
                symbol = trade_data['symbol']
                order_id = trade_data['order_id']
                status = trade_data['status']
                total_fees_raw = trade_data.get('total_fees') or trade_data.get('total_fees_usd')
                total_fees = float(total_fees_raw) if total_fees_raw not in (None, "") else None
                trigger = trade_data.get('trigger')
                source = trade_data.get('source')

                parent_id = trade_data.get('parent_id')
                parent_ids = []
                pnl_usd = None

                if side == 'sell':
                    parent_trades = await self.find_unlinked_buys(symbol)
                    filled_size = 0
                    total_cost = Decimal(0)

                    for pt in parent_trades:
                        pt_size = Decimal(pt.size)
                        pt_price = Decimal(pt.price)
                        used_size = min(pt_size, amount - filled_size)

                        total_cost += used_size * pt_price
                        filled_size += used_size
                        parent_ids.append(pt.order_id)

                        if filled_size >= amount:
                            break

                    if filled_size > 0:
                        revenue = amount * price
                        pnl_usd = float(revenue - total_cost - (total_fees or 0))

                if side == 'buy':
                    parent_id = order_id
                    parent_ids = [order_id]

                trade_dict = {
                    "order_id": order_id,
                    "parent_id": parent_id,
                    "parent_ids": parent_ids or None,
                    "symbol": symbol,
                    "side": side,
                    "order_time": order_time,
                    "price": price,
                    "size": amount,
                    "pnl_usd": pnl_usd,
                    "total_fees_usd": total_fees,
                    "trigger": trigger,
                    "order_type": trade_data.get("order_type"),
                    "status": status,
                    "source": source,
                }

                insert_stmt = pg_insert(TradeRecord).values(**trade_dict)

                update_stmt = insert_stmt.on_conflict_do_update(
                    index_elements=["order_id"],
                    set_={
                        "parent_id": insert_stmt.excluded.parent_id,
                        "parent_ids": insert_stmt.excluded.parent_ids,
                        "order_time": insert_stmt.excluded.order_time,
                        "price": insert_stmt.excluded.price,
                        "size": insert_stmt.excluded.size,
                        "pnl_usd": insert_stmt.excluded.pnl_usd,
                        "total_fees_usd": insert_stmt.excluded.total_fees_usd,
                        "trigger": insert_stmt.excluded.trigger,
                        "order_type": insert_stmt.excluded.order_type,
                        "status": insert_stmt.excluded.status,
                        "source": insert_stmt.excluded.source,
                    }
                )

                await session.execute(update_stmt)
                await session.commit()

                if self.logger:
                    self.logger.info(
                        f"✅ Trade recorded: {symbol} {side.upper()} {amount}@{price} | PnL: {pnl_usd} | Parents: {parent_ids}"
                    )

            except Exception as e:
                await session.rollback()
                if self.logger:
                    self.logger.error(f"❌ Error recording trade: {e}", exc_info=True)

    async def find_unlinked_buys(self, symbol: str):
        """Find buy trades not yet linked to any sell, ordered newest first."""
        async with self.db_session_manager.async_session() as session:
            result = await session.execute(text("""
                SELECT * FROM trade_records
                WHERE symbol = :symbol
                  AND side = 'buy'
                  AND order_id NOT IN (
                      SELECT unnest(parent_ids) FROM trade_records
                      WHERE symbol = :symbol AND side = 'sell'
                  )
                ORDER BY order_time DESC
            """), {"symbol": symbol})
            return result.fetchall()

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

    async def fetch_trade_by_order_id(self, order_id: str) -> Optional[TradeRecord]:
        """
        Fetches a single trade record by its order_id.
        """
        async with self.db_session_manager.async_session() as session:
            try:
                result = await session.execute(
                    select(TradeRecord).where(TradeRecord.order_id == order_id)
                )
                return result.scalar_one_or_none()
            except Exception as e:
                if self.logger:
                    self.logger.error(f"❌ Error in fetch_trade_by_order_id for {order_id}: {e}", exc_info=True)
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
