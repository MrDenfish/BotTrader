
import asyncio
from TableModels.trade_record import TradeRecord
from sqlalchemy.dialects.postgresql import insert as pg_insert
from asyncio import Queue
from typing import Optional
from sqlalchemy import select, func, and_, or_, not_
from sqlalchemy.future import select
from decimal import Decimal, ROUND_DOWN
from datetime import datetime, timezone

class TradeRecorder:
    """
    Handles recording of trades into the trade_records table.
    """

    def __init__(self, database_session_manager, logger, shared_utils_precision, coinbase_api):
        self.db_session_manager = database_session_manager
        self.logger = logger
        self.shared_utils_precision = shared_utils_precision
        self.coinbase_api = coinbase_api

        self.trade_queue: Queue = Queue()
        self.worker_task: Optional[asyncio.Task] = None

    # =====================================================
    # ✅ Worker Management
    # =====================================================
    async def start_worker(self):
        """Starts the background trade recording worker."""
        if not self.worker_task:
            self.logger.info("🚀 Starting TradeRecorder worker...")
            self.worker_task = asyncio.create_task(self._trade_worker_loop())

    async def stop_worker(self):
        """Gracefully stops the background trade worker."""
        if self.worker_task:
            self.logger.info("🛑 Stopping TradeRecorder worker...")
            self.worker_task.cancel()
            try:
                await self.worker_task
            except asyncio.CancelledError:
                pass
            self.worker_task = None

    async def enqueue_trade(self, trade_data: dict):
        """Enqueues a trade for async processing."""
        await self.trade_queue.put(trade_data)
        self.logger.debug(f"📥 Trade queued: {trade_data.get('symbol')} {trade_data.get('side')}")

    async def _trade_worker_loop(self):
        """Continuously processes queued trades in FIFO order."""
        while True:
            trade_data = await self.trade_queue.get()
            try:
                await self.record_trade(trade_data)
            except Exception as e:
                self.logger.error(f"❌ Failed to record trade {trade_data.get('order_id')}: {e}", exc_info=True)
            finally:
                self.trade_queue.task_done()

    # =====================================================
    # ✅ Main Trade Recording Logic (Option 1 + Option 2)
    # =====================================================

    async def record_trade(self, trade_data: dict):
        """
        Records a new trade into the database. Uses short-lived sessions for each step
        to reduce pool pressure. Intended to be called by the queue worker.
        """
        try:
            # -----------------------------
            # Phase 0: Pre-calculation logic
            # -----------------------------
            order_time_raw = trade_data.get('order_time', datetime.now(timezone.utc))

            # ✅ Normalize to a timezone-aware UTC datetime
            if isinstance(order_time_raw, str):
                # Parse ISO string and force UTC
                parsed_time = datetime.fromisoformat(order_time_raw.replace("Z", "+00:00"))
            else:
                parsed_time = order_time_raw

            if parsed_time.tzinfo is None:
                # If naive, explicitly set to UTC
                parsed_time = parsed_time.replace(tzinfo=timezone.utc)
            else:
                # If timezone-aware, convert to UTC
                parsed_time = parsed_time.astimezone(timezone.utc)

            order_time = parsed_time

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

            # Skip duplicate FALLBACK/FILL records if real one exists
            if ("-FALLBACK" in order_id or "-FILL-" in order_id) and source == "websocket":
                base_id = order_id.split("-F")[0]
                async with self.db_session_manager.async_session_factory() as session:
                    primary_trade = await session.get(TradeRecord, base_id)
                    if primary_trade:
                        self.logger.info(
                            f"⏭️ Skipping duplicate Fallback {order_id} — primary already recorded: {base_id}"
                        )
                        return

            # -----------------------------
            # Phase 1: SELL metrics (short-lived session)
            # -----------------------------
            if side == 'sell':
                async with self.db_session_manager.async_session_factory() as session:
                    async with session.begin():
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
                realized_profit_total = Decimal(
                    sum(Decimal(instr["realized_profit"]) for instr in update_instructions)
                ).quantize(quote_q)
            else:
                realized_profit_total = Decimal("0.0")
                parent_id = parent_id or order_id
                parent_ids = [parent_id]

            # -----------------------------
            # Phase 2: Insert/Upsert trade (short-lived session)
            # -----------------------------
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
                "realized_profit": float(realized_profit_total)
            }

            async with self.db_session_manager.async_session_factory() as session:
                async with session.begin():
                    insert_stmt = pg_insert(TradeRecord).values(**trade_dict)
                    update_stmt = insert_stmt.on_conflict_do_update(
                        index_elements=["order_id"],
                        set_={key: insert_stmt.excluded[key] for key in trade_dict}
                    )
                    await session.execute(update_stmt)
                    await session.flush()

            # -----------------------------
            # Phase 3: Update parent BUY records (short-lived session)
            # -----------------------------
            if update_instructions:
                async with self.db_session_manager.async_session_factory() as session:
                    async with session.begin():
                        for instruction in update_instructions:
                            parent_record = await session.get(TradeRecord, instruction["order_id"])
                            if not parent_record:
                                self.logger.warning(f"⚠️ Could not find parent buy {instruction['order_id']}")
                                continue
                            parent_record.remaining_size = float(instruction["remaining_size"])
                            parent_record.realized_profit = float(instruction["realized_profit"])
                            session.add(parent_record)
                        await session.flush()

            if self.logger:
                self.logger.info(
                    f"✅ Trade recorded: {symbol} {side.upper()} {amount}@{price} | "
                    f"PnL: {pnl_usd} | Parents: {parent_ids}"
                )

        except Exception as e:
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

        Returns:
            dict with cost_basis_usd, sale_proceeds_usd, net_sale_proceeds_usd,
            pnl_usd, parent_ids, update_instructions
        """
        TOLERANCE = Decimal("1").scaleb(-base_q.as_tuple().exponent)
        parent_ids = []
        cost_basis = Decimal("0")
        filled_size = Decimal("0")
        update_instructions = []
        try:
            # Fetch eligible parent BUY trades
            parent_trades = await self.find_unlinked_buys(symbol)

            for pt in parent_trades:
                pt_size = Decimal(pt.remaining_size or pt.size)
                pt_price = Decimal(pt.price)
                usable = min(pt_size, size - filled_size)

                if usable <= Decimal("0"):
                    continue

                cost_basis += usable * pt_price
                filled_size += usable
                parent_ids.append(pt.order_id)

                remaining_after = pt_size - usable
                realized_profit = (usable * sell_price) - (usable * pt_price)

                update_instructions.append({
                    "order_id": pt.order_id,
                    "remaining_size": float(remaining_after.quantize(base_q)),
                    "realized_profit": float(realized_profit.quantize(quote_q))
                })

                if filled_size >= size - TOLERANCE:
                    break

            if filled_size == Decimal("0"):
                return {
                    "cost_basis_usd": None,
                    "sale_proceeds_usd": None,
                    "net_sale_proceeds_usd": None,
                    "pnl_usd": None,
                    "parent_ids": [],
                    "update_instructions": []
                }

            used_size = min(filled_size, size)
            cost_basis = cost_basis.quantize(quote_q)
            sale_proceeds = (used_size * sell_price).quantize(quote_q)
            net_proceeds = (sale_proceeds - total_fees).quantize(quote_q)
            pnl_usd = (net_proceeds - cost_basis).quantize(quote_q)

            return {
                "cost_basis_usd": float(cost_basis),
                "sale_proceeds_usd": float(sale_proceeds),
                "net_sale_proceeds_usd": float(net_proceeds),
                "pnl_usd": float(pnl_usd),
                "parent_ids": parent_ids,
                "update_instructions": update_instructions
            }
        except Exception as e:
            if self.logger:
                self.logger.error(f"❌ Error in compute_cost_basis_and_sale_proceeds for {symbol}: {e}", exc_info=True)
            return {
                "cost_basis_usd": None,
                "sale_proceeds_usd": None,
                "net_sale_proceeds_usd": None,
                "pnl_usd": None,
                "parent_ids": [],
                "update_instructions": []
            }

    from sqlalchemy import select, func, and_, or_, not_

    async def find_unlinked_buys(self, symbol: str):
        """
        Returns BUY trades that are not linked to any SELL, using a duplicate-safe filter.
        Orders by oldest (FIFO) and ignores fallback duplicates and empty remnants.
        """
        async with self.db_session_manager.async_session_factory() as session:
            async with session.begin():
                # Subquery to get all parent_ids used by sell trades
                subquery = (
                    select(func.unnest(TradeRecord.parent_ids))
                    .where(
                        TradeRecord.symbol == symbol,
                        TradeRecord.side == "sell",
                        TradeRecord.parent_ids.isnot(None)
                    )
                )

                stmt = (
                    select(TradeRecord)
                    .where(
                        TradeRecord.symbol == symbol,
                        TradeRecord.side == "buy",
                        or_(
                            TradeRecord.remaining_size.is_(None),
                            TradeRecord.remaining_size > 0
                        ),
                        not_(TradeRecord.order_id.like('%-FILL-%')),
                        not_(TradeRecord.order_id.like('%-FALLBACK')),
                        not_(TradeRecord.order_id.in_(subquery))
                    )
                    .order_by(TradeRecord.order_time.asc())
                )

                result = await session.execute(stmt)
                return result.scalars().all()

    async def fetch_all_trades(self):
        """
        Fetch all recorded trades.
        """
        async with self.db_session_manager.async_session_factory() as session:
            async with session.begin():
                result = await session.execute(select(TradeRecord))
                trades = result.scalars().all()
                return trades

    async def fetch_recent_trades(self, limit=10):
        """
        Fetch the most recent trades (default: last 10).
        """
        async with self.db_session_manager.async_session_factory() as session:
            async with session.begin():
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
            async with session.begin():
                try:
                    result = await session.get(TradeRecord, order_id)
                    if result:
                        await session.delete(result)
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
        Finds the most recent BUY trade for a symbol that has not yet been linked to a SELL.
        Returns the order_id if found, else None.
        """
        async with self.db_session_manager.async_session_factory() as session:
            async with session.begin():
                try:
                    # Subquery to find all parent_ids already linked in sell trades
                    subq = (
                        select(func.unnest(TradeRecord.parent_ids))
                        .where(
                            TradeRecord.symbol == symbol,
                            TradeRecord.side == 'sell',
                            TradeRecord.parent_ids.isnot(None)
                        )
                    )

                    # Select the latest BUY trade not linked to any SELL
                    stmt = (
                        select(TradeRecord.order_id)
                        .where(
                            TradeRecord.symbol == symbol,
                            TradeRecord.side == 'buy',
                            not_(TradeRecord.order_id.in_(subq))
                        )
                        .order_by(TradeRecord.order_time.desc())
                        .limit(1)
                    )

                    result = await session.execute(stmt)
                    row = result.scalar_one_or_none()
                    return row

                except Exception as e:
                    if self.logger:
                        self.logger.error(f"❌ Error in find_latest_unlinked_buy for {symbol}: {e}", exc_info=True)
                    return None

    async def fetch_trade_by_order_id(self, order_id: str) -> Optional[TradeRecord]:
        """
        Fetches a single trade record by its order_id.
        """
        async with self.db_session_manager.async_session_factory() as session:
            async with session.begin():
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
        async with self.db_session_manager.async_session_factory() as session:
            async with session.begin():
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
        Recalculates cost basis, sale proceeds, PnL, and realized profit for existing SELL trades
        that are missing these metrics. Applies FIFO logic using remaining_size on associated BUY trades.
        """
        async with self.db_session_manager.async_session_factory() as session:
            async with session.begin():
                try:
                    self.logger.info("🧮 Starting backfill of cost basis and sale proceeds...")

                    result = await session.execute(
                        select(TradeRecord)
                        .where(
                            TradeRecord.side == "sell",
                            or_(
                                TradeRecord.cost_basis_usd.is_(None),
                                TradeRecord.sale_proceeds_usd.is_(None),
                                TradeRecord.pnl_usd.is_(None),
                                TradeRecord.realized_profit.is_(None)
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
                            trade.cost_basis_usd = results.get('cost_basis_usd')
                            trade.sale_proceeds_usd = results.get('sale_proceeds_usd')
                            trade.net_sale_proceeds_usd = results.get('net_sale_proceeds_usd')
                            trade.pnl_usd = results.get('pnl_usd')
                            trade.parent_ids = results.get('parent_ids')
                            trade.parent_id = results.get('parent_ids')[0] if results.get('parent_ids') else None

                            # 🧮 Patch: Calculate and update realized_profit
                            try:
                                realized_profit_total = Decimal(
                                    sum(Decimal(instr["realized_profit"]) for instr in results.get("update_instructions", []))
                                ).quantize(quote_q)
                                trade.realized_profit = float(realized_profit_total)
                            except Exception as e:
                                self.logger.warning(f"⚠️ Failed to compute realized_profit for {trade.order_id}: {e}")

                            session.add(trade)

                            self.logger.info(
                                f"📈 Backfilled {trade.order_id} | Cost: {trade.cost_basis_usd} | "
                                f"Proceeds: {trade.sale_proceeds_usd} | PnL: {trade.pnl_usd} | "
                                f"Realized Profit: {trade.realized_profit}"
                            )
                        else:
                            self.logger.warning(f"⚠️ Could not match parents for {trade.order_id} — skipping")

                    self.logger.info("✅ Backfill complete.")

                except Exception as e:
                    await session.rollback()
                    self.logger.error(f"❌ Error during backfill: {e}", exc_info=True)

    async def fetch_trade_records_for_tp_sl(self, symbol: str) -> list:
        """
        Fetches active (unlinked or partially filled) BUY trades for TP/SL evaluation.

        Returns:
            List of dicts:
            [
                {
                    "order_id": str,
                    "remaining_size": float,
                    "entry_price": float,
                    "cost_basis_usd": float,
                    "order_time": datetime
                },
                ...
            ]
        """
        try:
            trades = await self.find_unlinked_buys(symbol)
            result = []

            for trade in trades:
                remaining_size = float(trade.remaining_size or trade.size or 0.0)
                if remaining_size <= 0:
                    continue

                result.append({
                    "order_id": trade.order_id,
                    "remaining_size": remaining_size,
                    "entry_price": float(trade.price),
                    "cost_basis_usd": float((Decimal(trade.price) * Decimal(remaining_size))),
                    "order_time": trade.order_time
                })

            return result

        except Exception as e:
            if self.logger:
                self.logger.error(f"❌ Error in fetch_trade_records_for_tp_sl for {symbol}: {e}", exc_info=True)
            return []

    async def fetch_sells_by_date(self, date) -> list:
        """
        Fetches all SELL trades for a given date (UTC), including pnl_usd values.

        Args:
            date (datetime.date): The date to query (UTC).
        Returns:
            List of TradeRecord objects for SELLs on that date.
        """
        try:
            async with self.db_session_manager.async_session_factory() as session:
                async with session.begin():
                    start_dt = datetime.combine(date, datetime.min.time(), tzinfo=timezone.utc)
                    end_dt = datetime.combine(date, datetime.max.time(), tzinfo=timezone.utc)

                    stmt = (
                        select(TradeRecord)
                        .where(
                            and_(
                                TradeRecord.side == "sell",
                                TradeRecord.order_time >= start_dt,
                                TradeRecord.order_time <= end_dt,
                                TradeRecord.pnl_usd.isnot(None)
                            )
                        )
                    )

                    result = await session.execute(stmt)
                    sells = result.scalars().all()
                    return sells
        except Exception as e:
            if self.logger:
                self.logger.error(f"❌ Error fetching sells for {date}: {e}", exc_info=True)
            return []


