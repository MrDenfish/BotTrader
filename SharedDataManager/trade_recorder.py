

from TableModels.trade_record import TradeRecord
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import selectinload
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
                total_fees = Decimal(total_fees_raw) if total_fees_raw not in (None, "") else None
                trigger = trade_data.get('trigger')
                source = trade_data.get('source')

                parent_id = trade_data.get('parent_id')
                parent_ids = []
                pnl_usd = None

                if side == 'sell':
                    parent_trades = await self.find_unlinked_buys(symbol)
                    TOLERANCE = Decimal("0.000001")
                    total_cost = Decimal("0")
                    filled_size = Decimal("0")
                    target_size = Decimal(amount)

                    for pt in parent_trades:
                        pt_size = Decimal(pt.size)
                        pt_price = Decimal(pt.price)
                        usable = min(pt_size, target_size - filled_size)
                        total_cost += usable * pt_price
                        filled_size += usable
                        parent_ids.append(pt.order_id)
                        if filled_size >= target_size - TOLERANCE:
                            break

                    if filled_size >= target_size - TOLERANCE:
                        revenue = target_size * price
                        pnl_usd = float(revenue - total_cost - (total_fees or 0))

                    if parent_ids:
                        revenue = filled_size * price
                        pnl_usd = float(revenue - total_cost - (total_fees or 0))

                elif side == 'buy':
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
                    "remaining_size": amount if side == 'buy' else None,
                    "realized_profit": 0.0 if side == 'buy' else None
                }

                # 🚨 Check existing record
                existing = await session.get(TradeRecord, order_id)
                if existing:
                    if side == "buy" and not existing.parent_id:
                        self.logger.warning(f"⚠️ Existing BUY trade missing parent_id: {order_id}")
                    self.logger.debug(f"🔁 Updating existing trade: {order_id}")
                else:
                    self.logger.debug(f"🆕 Inserting new trade: {order_id}")

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
                        "remaining_size": insert_stmt.excluded.remaining_size,
                        "realized_profit": insert_stmt.excluded.realized_profit,
                    }
                )



                await session.execute(update_stmt)

                # ✅ Update parent buys after successful sell trade
                if side == 'sell' and parent_ids and pnl_usd is not None:
                    sell_remaining = target_size  # e.g., 4.882 units

                    for parent_order_id in parent_ids:
                        buy_record = await session.get(TradeRecord, parent_order_id)
                        if not buy_record:
                            self.logger.warning(f"⚠️ Could not find parent buy {parent_order_id} for sell {order_id}")
                            continue

                        buy_remaining = Decimal(buy_record.remaining_size or 0)
                        if buy_remaining <= 0:
                            continue  # Skip exhausted buys

                        # Portion of sell attributed to this buy
                        used_size = min(sell_remaining, buy_remaining)
                        buy_cost = Decimal(buy_record.price) * used_size
                        sell_revenue = price * used_size
                        portion_fees = (Decimal(total_fees or 0) * (used_size / target_size)).quantize(Decimal("0.00000001"))

                        realized_pnl = sell_revenue - buy_cost - portion_fees
                        buy_record.remaining_size = float((buy_remaining - used_size).quantize(Decimal("0.00000001")))
                        buy_record.realized_profit = float(
                            (Decimal(buy_record.realized_profit or 0) + realized_pnl).quantize(Decimal("0.00000001"))
                        )

                        session.add(buy_record)  # schedule update
                        sell_remaining -= used_size

                        if sell_remaining <= Decimal("0.0000001"):
                            break  # done allocating

                    if sell_remaining > 0:
                        self.logger.warning(f"⚠️ Sell {order_id} was only partially matched to buys — leftover: {sell_remaining}")

                await session.flush()  # 🧠 Important in async context
                await session.commit()  # ✅ Ensures write completes

                if self.logger:
                    self.logger.info(
                        f"✅ Trade recorded: {symbol} {side.upper()} {amount}@{price} | PnL: {pnl_usd} | Parents: {parent_ids}"
                    )

            except Exception as e:
                await session.rollback()
                if self.logger:
                    self.logger.error(f"❌ Error recording trade: {e}", exc_info=True)

    async def find_unlinked_buys(self, symbol: str):
        """Find buy trades not yet linked to any sell, ordered oldest first."""
        async with self.db_session_manager.async_session() as session:
            result = await session.execute(text("""
                SELECT * FROM trade_records
                WHERE symbol = :symbol
                  AND side = 'buy'
                  AND order_id NOT IN (
                      SELECT unnest(parent_ids)
                      FROM trade_records
                      WHERE symbol = :symbol
                        AND side = 'sell'
                        AND parent_ids IS NOT NULL
                  )
                ORDER BY order_time ASC
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

    async def backfill_pnl_and_parents(self):
        """
        Goes through existing SELL trades and reassigns parent_id, parent_ids,
        calculates pnl_usd, deducts from BUY.remaining_size, and updates BUY.realized_profit.
        """
        try:
            async with self.db_session_manager.async_session() as session:
                result = await session.execute(
                    select(TradeRecord)
                    .where(TradeRecord.side == "sell")
                    .order_by(TradeRecord.order_time.asc())
                )
                sell_trades = result.scalars().all()

                if not sell_trades:
                    self.logger.info("📭 No sell trades to backfill.")
                    return

                updated_count = 0
                TOLERANCE = Decimal("0.000001")

                for sell in sell_trades:
                    if sell.pnl_usd is not None:
                        continue  # Already processed

                    filled_size = Decimal(str(sell.size))
                    total_cost = Decimal("0")
                    used_size = Decimal("0")
                    parent_ids = []

                    # Fetch matching BUYs with remaining size
                    buy_result = await session.execute(text("""
                        SELECT * FROM trade_records
                        WHERE symbol = :symbol
                          AND side = 'buy'
                          AND remaining_size IS NOT NULL
                          AND remaining_size > 0
                        ORDER BY order_time ASC
                    """), {"symbol": sell.symbol})
                    parent_trades = buy_result.fetchall()

                    if not parent_trades:
                        self.logger.warning(f"⚠️ No parent buys found for sell {sell.order_id} ({sell.symbol})")
                        continue

                    for b in parent_trades:
                        b_order_id = b.order_id
                        b_size = Decimal(str(b.remaining_size or b.size or 0))
                        b_price = Decimal(str(b.price))
                        usable = min(b_size, filled_size - used_size)

                        if usable <= 0:
                            continue

                        total_cost += usable * b_price
                        used_size += usable
                        parent_ids.append(b_order_id)

                        # ✅ Fetch ORM instance to safely modify it
                        buy_record = await session.get(TradeRecord, b_order_id)
                        if buy_record:
                            symbol = sell.symbol
                            base_deci, quote_deci, _, _ = self.shared_utils_precision.fetch_precision(symbol)
                            base_quantizer = Decimal("1").scaleb(-base_deci)
                            quote_quantizer = Decimal("1").scaleb(-quote_deci)
                            remaining_size = Decimal(str(buy_record.remaining_size or b_size)) - usable
                            remaining_size = self.shared_utils_precision.safe_quantize(remaining_size, base_quantizer)
                            buy_record.remaining_size = remaining_size
                            realized_profit = Decimal(str(buy_record.realized_profit or 0)) + (usable * Decimal(str(sell.price)) - usable * b_price)
                            realized_profit = self.shared_utils_precision.safe_quantize(realized_profit, quote_quantizer)
                            buy_record.realized_profit = realized_profit
                            session.add(buy_record)

                        if used_size >= filled_size - TOLERANCE:
                            break

                    if used_size == 0:
                        self.logger.warning(f"⚠️ No usable buys found for sell {sell.order_id}")
                        continue

                    revenue = filled_size * Decimal(str(sell.price))
                    total_fees = Decimal(str(sell.total_fees_usd or 0))
                    pnl = revenue - total_cost - total_fees

                    # Update the SELL trade
                    sell.parent_id = parent_ids[0] if parent_ids else None
                    sell.parent_ids = parent_ids
                    sell.pnl_usd = float(pnl)

                    session.add(sell)
                    updated_count += 1

                await session.commit()
                self.logger.info(f"✅ Backfilled PnL, realized_profit, and remaining_size for {updated_count} SELL trades.")

        except Exception as e:
            self.logger.error(f"❌ Failed to backfill PnL and realized profit: {e}", exc_info=True)

    async def backfill_buy_parent_ids(self):
        """
        Backfills parent_id and parent_ids for buy trades that are missing them.
        """
        async with self.db_session_manager.async_session() as session:
            try:
                result = await session.execute(text("""
                    SELECT order_id, symbol, side
                    FROM trade_records
                    WHERE side = 'buy' AND parent_id IS NULL
                """))
                missing_trades = result.fetchall()

                if not missing_trades:
                    self.logger.info("✅ No buy trades missing parent_id.")
                    return

                self.logger.info(f"🔄 Backfilling parent_id for {len(missing_trades)} buy trades...")

                for row in missing_trades:

                    order_id, symbol, side = row
                    if symbol == "POND-USD":
                        pass
                    update_stmt = text("""
                        UPDATE trade_records
                        SET parent_id = :order_id,
                            parent_ids = ARRAY[:order_id]
                        WHERE order_id = :order_id
                    """)
                    await session.execute(update_stmt, {"order_id": order_id})

                await session.commit()
                self.logger.info(f"✅ Backfilled parent_id for {len(missing_trades)} buy trades.")
            except Exception as e:
                await session.rollback()
                self.logger.error(f"❌ Failed to backfill buy parent_ids: {e}", exc_info=True)

