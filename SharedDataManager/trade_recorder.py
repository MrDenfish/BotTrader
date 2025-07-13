

from TableModels.trade_record import TradeRecord
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import selectinload
from typing import Optional
from sqlalchemy import text, or_
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
        Records a new trade into the database. Computes PnL and cost tracking for sells.
        Uses upsert to handle duplicate order_id conflicts gracefully.
        """
        async with self.db_session_manager.async_session_factory() as session:
            try:
                order_time_raw = trade_data.get('order_time', datetime.utcnow())
                order_time = (
                    datetime.fromisoformat(order_time_raw.rstrip("Z"))
                    if isinstance(order_time_raw, str)
                    else order_time_raw
                )

                symbol = trade_data['symbol']
                base_deci, quote_deci, _, _ = self.shared_utils_precision.fetch_precision(symbol)
                base_q = Decimal("1").scaleb(-base_deci)
                quote_q = Decimal("1").scaleb(-quote_deci)

                side = trade_data['side'].lower()
                amount = self.shared_utils_precision.safe_convert(trade_data['amount'], base_deci)
                price = self.shared_utils_precision.safe_convert(trade_data['price'], quote_deci)
                order_id = trade_data['order_id']
                status = trade_data['status']
                total_fees_raw = trade_data.get('total_fees') or trade_data.get('total_fees_usd')
                total_fees = Decimal(total_fees_raw) if total_fees_raw not in (None, "") else Decimal("0")
                trigger = trade_data.get('trigger')
                source = trade_data.get('source')

                parent_id = trade_data.get('parent_id')
                parent_ids = []
                pnl_usd = None
                cost_basis_usd = None
                sale_proceeds_usd = None
                net_sale_proceeds_usd = None
                update_instructions = []

                if side == 'sell':
                    result = await self.compute_cost_basis_and_sale_proceeds(
                        session=session,
                        symbol=symbol,
                        size=amount,
                        sell_price=price,
                        total_fees=total_fees,
                        quote_q=quote_q,
                        base_q=base_q
                    )

                    parent_ids = result["parent_ids"]
                    cost_basis_usd = result["cost_basis_usd"]
                    sale_proceeds_usd = result["sale_proceeds_usd"]
                    net_sale_proceeds_usd = result["net_sale_proceeds_usd"]
                    pnl_usd = result["pnl_usd"]
                    update_instructions = result["update_instructions"]

                elif side == 'buy':
                    parent_id = parent_id or order_id
                    parent_ids = [parent_id]

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
                    "cost_basis_usd": cost_basis_usd,
                    "sale_proceeds_usd": sale_proceeds_usd,
                    "net_sale_proceeds_usd": net_sale_proceeds_usd,
                    "remaining_size": amount if side == 'buy' else None,
                    "realized_profit": 0.0 if side == 'buy' else None
                }

                insert_stmt = pg_insert(TradeRecord).values(**trade_dict)
                update_stmt = insert_stmt.on_conflict_do_update(
                    index_elements=["order_id"],
                    set_={key: insert_stmt.excluded[key] for key in trade_dict}
                )
                await session.execute(update_stmt)

                # 🔄 Update parent BUY records for a SELL
                if update_instructions:
                    for instruction in update_instructions:
                        parent_record = await session.get(TradeRecord, instruction["order_id"])
                        if not parent_record:
                            self.logger.warning(f"⚠️ Could not find parent buy {instruction['order_id']}")
                            continue
                        parent_record.remaining_size = float(instruction["remaining_size"])
                        parent_record.realized_profit = float(instruction["realized_profit"])
                        session.add(parent_record)

                await session.flush()
                await session.commit()

                if self.logger:
                    self.logger.info(
                        f"✅ Trade recorded: {symbol} {side.upper()} {amount}@{price} | PnL: {pnl_usd} | Parents: {parent_ids}"
                    )

            except Exception as e:
                await session.rollback()
                if self.logger:
                    self.logger.error(f"❌ Error recording trade: {e}", exc_info=True)

    async def compute_cost_basis_and_sale_proceeds(
            self,
            session,
            symbol: str,
            size: Decimal,
            sell_price: Decimal,
            total_fees: Decimal,
            quote_q: Decimal,
            base_q: Decimal
    ) -> dict:
        """
        Allocates cost basis and computes sale proceeds using FIFO logic.

        Returns a dictionary with:
            - cost_basis_usd
            - sale_proceeds_usd (gross)
            - net_sale_proceeds_usd (after fees)
            - pnl_usd
            - parent_ids
            - update_instructions (for buy records)
        """
        TOLERANCE = Decimal("1").scaleb(-base_q.as_tuple().exponent)
        parent_ids = []
        cost_basis = Decimal("0")
        filled_size = Decimal("0")
        target_size = size
        update_instructions = []

        parent_trades = await self.find_unlinked_buys(symbol)

        for pt in parent_trades:
            pt_size = Decimal(pt.remaining_size or pt.size)
            pt_price = Decimal(pt.price)
            usable = min(pt_size, target_size - filled_size)

            if usable <= Decimal("0"):
                continue

            cost_basis += usable * pt_price
            filled_size += usable
            parent_ids.append(pt.order_id)

            new_remaining = pt_size - usable
            realized_profit = usable * (sell_price - pt_price)

            update_instructions.append({
                "order_id": pt.order_id,
                "remaining_size": self.shared_utils_precision.safe_quantize(new_remaining, base_q),
                "realized_profit": self.shared_utils_precision.safe_quantize(
                    Decimal(pt.realized_profit or 0) + realized_profit, quote_q
                )
            })

            if filled_size >= target_size - TOLERANCE:
                break

        if filled_size == Decimal("0"):
            return {
                "parent_ids": [],
                "cost_basis_usd": None,
                "sale_proceeds_usd": None,
                "net_sale_proceeds_usd": None,
                "pnl_usd": None,
                "update_instructions": []
            }

        used_size = min(filled_size, target_size)
        cost_basis = cost_basis.quantize(quote_q)
        sale_proceeds = (used_size * sell_price).quantize(quote_q)
        net_sale_proceeds = (sale_proceeds - total_fees).quantize(quote_q)
        pnl_usd = (net_sale_proceeds - cost_basis).quantize(quote_q)

        return {
            "parent_ids": parent_ids,
            "cost_basis_usd": float(cost_basis),
            "sale_proceeds_usd": float(sale_proceeds),
            "net_sale_proceeds_usd": float(net_sale_proceeds),
            "pnl_usd": float(pnl_usd),
            "update_instructions": update_instructions
        }


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

    async def backfill_trade_metrics(self):
        """
        Recalculates cost basis, sale proceeds, and PnL for existing sell trades
        that are missing these metrics. Applies FIFO logic using remaining_size
        on associated BUY trades.
        """
        async with self.db_session_manager.async_session_factory() as session:
            try:
                self.logger.info("🧮 Starting backfill of cost basis and sale proceeds...")

                # Load sell trades missing metrics
                result = await session.execute(
                    select(TradeRecord)
                    .where(
                        TradeRecord.side == "sell",
                        or_(
                            TradeRecord.cost_basis_usd.is_(None),
                            TradeRecord.sale_proceeds_usd.is_(None),
                            TradeRecord.pnl_usd.is_(None)
                        )
                    )
                    .order_by(TradeRecord.order_time)
                )
                sell_trades = result.scalars().all()

                self.logger.info(f"🔎 Found {len(sell_trades)} sell trades to backfill.")

                for trade in sell_trades:
                    symbol = trade.symbol
                    base_deci, quote_deci, _, _ = self.shared_utils_precision.fetch_precision(symbol)
                    base_q = Decimal("1").scaleb(-base_deci)
                    quote_q = Decimal("1").scaleb(-quote_deci)

                    size = Decimal(trade.size)
                    price = Decimal(trade.price)
                    total_fees = Decimal(trade.total_fees_usd or 0)

                    results = await self.compute_cost_basis_and_sale_proceeds(
                        session=session,
                        symbol=symbol,
                        size=size,
                        sell_price=price,
                        total_fees=total_fees,
                        quote_q=quote_q,
                        base_q=base_q
                    )

                    if results.get('parent_ids'):
                        trade.cost_basis_usd = results.get('cost_basis')
                        trade.sale_proceeds_usd = results.get('proceeds')
                        trade.pnl_usd = results.get('pnl_usd')
                        trade.parent_ids = results.get('parent_ids')
                        trade.parent_id = results.get('parent_ids'[0])

                        self.logger.info(
                            f"📈 Backfilled {trade.order_id} "
                            f"| Cost: {results.get('cost_basis')} "
                            f"| Proceeds: {results.get('proceeds')} "
                            f"| PnL: {results.get('pnl_usd')}"
                        )
                        session.add(trade)
                    else:
                        self.logger.warning(f"⚠️ Could not match parents for {trade.order_id} — skipping")

                await session.commit()
                self.logger.info("✅ Backfill complete.")

            except Exception as e:
                await session.rollback()
                self.logger.error(f"❌ Error during backfill: {e}", exc_info=True)



