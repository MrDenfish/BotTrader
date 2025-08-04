
import asyncio

from TableModels.trade_record import TradeRecord
from TableModels.trade_record_debug import TradeRecordDebug
from sqlalchemy.dialects.postgresql import insert as pg_insert
from asyncio import Queue
from typing import Optional
from sqlalchemy import case,func, Float, and_, or_, not_, update
from sqlalchemy.future import select
from decimal import Decimal, ROUND_DOWN
from datetime import datetime, timezone, timedelta

class TradeRecorder:
    """
    Handles recording of trades into the trade_records table.
    """

    def __init__(self, database_session_manager, logger, shared_utils_precision, coinbase_api, maintenance_callback=None):
        self.db_session_manager = database_session_manager
        self.logger = logger
        self.shared_utils_precision = shared_utils_precision
        self.coinbase_api = coinbase_api
        self.run_maintenance_if_needed = maintenance_callback

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
        if isinstance(trade_data.get("parent_id"), TradeRecord):
            self.logger.warning(f"🚨 enqueue_trade received TradeRecord for parent_id: {trade_data['parent_id']}")
        await self.trade_queue.put(trade_data)
        print(f"📥 Trade queued: {trade_data.get('symbol')} {trade_data.get('side')}")#debug

    async def _trade_worker_loop(self):
        """Continuously processes queued trades in FIFO order."""
        has_run_maintenance = False

        while True:
            trade_data = await self.trade_queue.get()
            try:
                await self.record_trade(trade_data)

                # ✅ After draining queue, run maintenance once if DB is no longer empty
                if not has_run_maintenance and self.trade_queue.empty():
                    count = await self.get_trade_record_count()
                    if count > 0:
                        self.logger.info("🔧 Running maintenance after initial trade load...")
                        await self.run_maintenance_if_needed()
                        has_run_maintenance = True

            except Exception as e:
                self.logger.error(f"❌ Failed to record trade {trade_data.get('order_id')}: {e}", exc_info=True)
            finally:
                self.trade_queue.task_done()

    # =====================================================
    # ✅ Main Trade Recording Logic (Option 1 + Option 2)
    # =====================================================

    async def get_trade_record_count(self):
        from TableModels.trade_record import TradeRecord
        async with self.db_session_manager.async_session_factory() as session:
            async with session.begin():
                result = await session.execute(select(func.count()).select_from(TradeRecord))
                return result.scalar()


    async def record_trade(self, trade_data: dict):
        """
        Records a trade (BUY or SELL) into the database.

        ✅ Single-session flow:
            - FIFO PnL calculation (SELL)
            - Insert/Update SELL or BUY trade
            - Update parent BUYs' remaining_size & realized_profit (SELL only)
        """

        try:
            # -----------------------------
            # ✅ Normalize Basic Fields
            # -----------------------------
            order_time_raw = trade_data.get("order_time", datetime.now(timezone.utc))
            if isinstance(order_time_raw, str):
                parsed_time = datetime.fromisoformat(order_time_raw.replace("Z", "+00:00"))
            else:
                parsed_time = order_time_raw
            order_time = (
                parsed_time.astimezone(timezone.utc)
                if parsed_time.tzinfo
                else parsed_time.replace(tzinfo=timezone.utc)
            )

            symbol = trade_data["symbol"]
            side = trade_data["side"].lower()
            order_id = trade_data["order_id"]
            status = trade_data.get("status")
            trigger = trade_data.get("trigger")
            source = trade_data.get("source")

            base_deci, quote_deci, *_ = self.shared_utils_precision.fetch_precision(symbol)
            base_q = Decimal("1").scaleb(-base_deci)
            quote_q = Decimal("1").scaleb(-quote_deci)

            amount = self.shared_utils_precision.safe_convert(trade_data["amount"], base_deci)
            price = self.shared_utils_precision.safe_convert(trade_data["price"], quote_deci)

            total_fees_raw = trade_data.get("total_fees") or trade_data.get("total_fees_usd")
            total_fees = Decimal(total_fees_raw) if total_fees_raw not in (None, "") else Decimal("0")

            parent_id = trade_data.get("parent_id")
            parent_ids, pnl_usd, cost_basis_usd, sale_proceeds_usd, net_sale_proceeds_usd = [], None, None, None, None
            update_instructions = []

            async with self.db_session_manager.async_session_factory() as session:
                async with session.begin():
                    self.active_session = session  # ✅ SINGLE active session for FIFO + updates

                    # -----------------------------
                    # ✅ SELL Logic (FIFO Matching)
                    # -----------------------------
                    if side == "sell":
                        self.logger.info(f"[RECORD_TRADE DEBUG] Starting SELL record for {symbol} {amount}@{price}")

                        fifo_result = await self.compute_cost_basis_and_sale_proceeds(
                            symbol=symbol,
                            size=amount,
                            sell_price=price,
                            total_fees=total_fees,
                            quote_q=quote_q,
                            base_q=base_q
                        )

                        parent_ids = fifo_result["parent_ids"]
                        cost_basis_usd = fifo_result["cost_basis_usd"]
                        sale_proceeds_usd = fifo_result["sale_proceeds_usd"]
                        net_sale_proceeds_usd = fifo_result["net_sale_proceeds_usd"]
                        pnl_usd = fifo_result["pnl_usd"]
                        update_instructions = fifo_result["update_instructions"]

                        parent_id = parent_ids[0] if parent_ids else parent_id

                        self.logger.info(
                            f"[RECORD_TRADE DEBUG] SELL PnL={pnl_usd}, Parents={parent_ids}, "
                            f"UpdateInstructions={update_instructions}"
                        )

                    # -----------------------------
                    # ✅ BUY Logic (Initial Baseline)
                    # -----------------------------
                    else:
                        parent_id = trade_data.get("parent_id") or trade_data.get("order_id")
                        if not parent_id:
                            self.logger.warning(f"⚠️ No parent_id provided for BUY trade: {symbol} {amount}@{price}")
                        parent_ids = [parent_id] if parent_id else []
                        self.logger.info(f"[RECORD_TRADE DEBUG] BUY recorded baseline: {symbol} {amount}@{price}")

                    # Safely normalize parent_id
                    if isinstance(parent_id, list):
                        parent_id = parent_id[0] if parent_id else None
                    elif not isinstance(parent_id, (str, type(None))):
                        parent_id = str(parent_id)

                    # -----------------------------
                    # ✅ Insert/Update Trade Record
                    # -----------------------------
                    trade_dict = {
                        "order_id": order_id,
                        "parent_id": parent_id,
                        "parent_ids": parent_ids or None,
                        "symbol": symbol,
                        "side": side,
                        "order_time": order_time,
                        "price": float(price),
                        "size": float(amount),
                        "pnl_usd": float(pnl_usd) if pnl_usd is not None else None,
                        "total_fees_usd": float(total_fees),
                        "trigger": trigger,
                        "order_type": trade_data.get("order_type"),
                        "status": status,
                        "source": source,
                        "cost_basis_usd": float(cost_basis_usd) if cost_basis_usd is not None else None,
                        "sale_proceeds_usd": float(sale_proceeds_usd) if sale_proceeds_usd is not None else None,
                        "net_sale_proceeds_usd": float(net_sale_proceeds_usd) if net_sale_proceeds_usd is not None else None,
                        "remaining_size": float(amount) if side == "buy" else None,
                        "realized_profit": float(0.0 if side == "buy" else sum(
                            [float(ui["realized_profit"]) for ui in update_instructions]
                        ))
                    }

                    insert_stmt = pg_insert(TradeRecord).values(**trade_dict)
                    update_stmt = insert_stmt.on_conflict_do_update(
                        index_elements=["order_id"],
                        set_={key: insert_stmt.excluded[key] for key in trade_dict}
                    )
                    await session.execute(update_stmt)

                    # -----------------------------
                    # ✅ Update Parent BUYs (remaining_size + realized_profit)
                    # -----------------------------
                    for instruction in update_instructions:
                        parent_record = await session.get(TradeRecord, instruction["order_id"])
                        if not parent_record:
                            self.logger.warning(f"⚠️ Parent BUY not found for update: {instruction['order_id']}")
                            continue
                        parent_record.remaining_size = instruction["remaining_size"]
                        parent_record.realized_profit = instruction["realized_profit"]

                    self.active_session = None  # ✅ Clean up active session

                self.logger.info(
                    f"✅ Trade recorded: {symbol} {side.upper()} {amount}@{price} | "
                    f"PnL: {pnl_usd} | Parents: {parent_ids}"
                )

        except Exception as e:
            self.active_session = None
            self.logger.error(f"❌ Error recording trade: {e}", exc_info=True)

    async def compute_cost_basis_and_sale_proceeds(
            self,
            symbol: str,
            size: Decimal,
            sell_price: Decimal,
            total_fees: Decimal,
            quote_q: Decimal,
            base_q: Decimal
    ) -> dict:
        """
        Allocates cost basis and computes sale proceeds using strict FIFO logic.
        Fully aligned with test_fifo_prod().

        ⚠️ Assumes it is called INSIDE an active session (no session param needed).
        """
        TOLERANCE = base_q / 10
        parent_ids = []
        cost_basis = Decimal("0")
        filled_size = Decimal("0")
        update_instructions = []

        try:
            # ✅ Fetch unlinked BUY trades (FIFO order)
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
                    not_(TradeRecord.order_id.like('%-FALLBACK'))
                )
                .order_by(TradeRecord.order_time.asc())
            )
            # Reuse current session
            result = await self.active_session.execute(stmt)
            parent_trades = result.scalars().all()
            if symbol == "EDGE-USD":
                print(
                    f"[FIFO PROD] Starting PnL calc for {symbol}: Sell Size={size}, Sell Price={sell_price}"
                )#debug
                print(f"[FIFO PROD] Found {len(parent_trades)} candidate BUYs")#debug

            for pt in parent_trades:
                pt_size = Decimal(pt.remaining_size if pt.remaining_size is not None else pt.size)
                pt_price = Decimal(pt.price)
                usable = min(pt_size, size - filled_size)

                if usable <= Decimal("0"):
                    continue

                cost_basis += usable * pt_price
                filled_size += usable

                if pt.order_id not in parent_ids:
                    parent_ids.append(pt.order_id)

                remaining_after = pt_size - usable
                realized_profit = (usable * sell_price) - (usable * pt_price)

                update_instructions.append({
                    "order_id": pt.order_id,
                    "remaining_size": float(
                        self.shared_utils_precision.safe_quantize(remaining_after, base_q)
                    ),
                    "realized_profit": float(
                        self.shared_utils_precision.safe_quantize(
                            max(Decimal(pt.realized_profit or 0), Decimal("0")) + realized_profit,
                            quote_q
                        )
                    )
                })
                if symbol == "EDGE-USD": #debugging
                    print(
                        f"[FIFO PROD] Parent {pt.order_id} | "
                        f"Size={pt_size}, Usable={usable}, RemainingAfter={remaining_after} | "
                        f"CostBasis+={usable * pt_price}, Realized+={realized_profit}"
                    )#debug

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
            cost_basis = self.shared_utils_precision.safe_quantize(cost_basis, quote_q)
            sale_proceeds = self.shared_utils_precision.safe_quantize(used_size * sell_price, quote_q)
            net_proceeds = self.shared_utils_precision.safe_quantize(sale_proceeds - total_fees, quote_q)
            pnl_usd = self.shared_utils_precision.safe_quantize(net_proceeds - cost_basis, quote_q)
            if symbol == "SPK-USD":
                print(
                    f"[FIFO PROD RESULT] {symbol} | PnL={pnl_usd} | CostBasis={cost_basis} | "
                    f"SaleProceeds={sale_proceeds} | Parents={parent_ids}"
                )#debug

            return {
                "cost_basis_usd": float(cost_basis),
                "sale_proceeds_usd": float(sale_proceeds),
                "net_sale_proceeds_usd": float(net_proceeds),
                "pnl_usd": float(pnl_usd),
                "parent_ids": parent_ids,
                "update_instructions": update_instructions
            }

        except Exception as e:
            self.logger.error(f"❌ Error in compute_cost_basis_and_sale_proceeds for {symbol}: {e}", exc_info=True)
            return {
                "cost_basis_usd": None,
                "sale_proceeds_usd": None,
                "net_sale_proceeds_usd": None,
                "pnl_usd": None,
                "parent_ids": [],
                "update_instructions": []
            }

    async def fetch_all_trades(self):
        """
        Fetch all recorded trades.
        """
        try:
            async with self.db_session_manager.async_session_factory() as session:
                async with session.begin():
                    result = await session.execute(select(TradeRecord))
                    trades = result.scalars().all()
                    return trades
        except Exception as e:
            if self.logger:
                self.logger.error(f"❌ Error fetching all trades: {e}", exc_info=True)
            return []

    async def fetch_recent_trades(self, days: int = 7):
        """Fetch trades from the last `days` days only (optimizing DB usage)."""
        try:
            cutoff_time = datetime.now(timezone.utc) - timedelta(days=days)
            async with self.db_session_manager.async_session_factory() as session:
                async with session.begin():
                    result = await session.execute(
                        select(TradeRecord).where(TradeRecord.order_time >= cutoff_time)
                    )
                    return result.scalars().all()
        except Exception as e:
            if self.logger:
                self.logger.error(f"❌ Error fetching recent trades: {e}", exc_info=True)
            return []

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

    async def find_unlinked_buys(self, symbol: str):
        """
        Returns a list of eligible unlinked BUY trades for FIFO cost basis matching.

        Use this when full trade metadata (remaining size, entry price, etc.) is needed.
        """
        try:
            async with self.db_session_manager.async_session_factory() as session:
                async with session.begin():
                    # Subquery: all BUY order_ids already linked in SELL parent_ids
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
                            not_(TradeRecord.order_id.like("%-FILL-%")),
                            not_(TradeRecord.order_id.like("%-FALLBACK")),
                            not_(TradeRecord.order_id.in_(subquery))
                        )
                        .order_by(TradeRecord.order_time.asc(), TradeRecord.order_id.asc())
                    )

                    result = await session.execute(stmt)
                    buys = result.scalars().all()

            if not buys:
                return []

            # ✅ Safe quantize remaining_size before returning
            for b in buys:
                base_deci, *_ = self.shared_utils_precision.fetch_precision(b.symbol)
                base_q = Decimal("1").scaleb(-base_deci)
                b.remaining_size = float(
                    self.shared_utils_precision.safe_quantize(
                        Decimal(b.remaining_size or b.size or 0), base_q
                    )
                )

            return buys

        except Exception as e:
            if self.logger:
                self.logger.error(f"❌ Error in find_unlinked_buys for {symbol}: {e}", exc_info=True)
            return []

    async def fix_unlinked_sells(self):

        self.logger.info("🔧 Running fix_unlinked_sells()...")

        async with self.db_session_manager.async_session_factory() as session:
            async with session.begin():
                result = await session.execute(
                    select(TradeRecord).where(
                        TradeRecord.side == "sell",
                        TradeRecord.pnl_usd.is_(None)
                    )
                )
                sell_trades = result.scalars().all()

                for trade in sell_trades:
                    symbol = trade.symbol
                    amount = Decimal(trade.size)
                    price = Decimal(trade.price)
                    total_fees = Decimal(trade.total_fees_usd or 0)

                    base_deci, quote_deci, *_ = self.shared_utils_precision.fetch_precision(symbol)
                    base_q = Decimal("1").scaleb(-base_deci)
                    quote_q = Decimal("1").scaleb(-quote_deci)

                    # Run FIFO matching
                    fifo_result = await self.compute_cost_basis_and_sale_proceeds(
                        symbol=symbol,
                        size=amount,
                        sell_price=price,
                        total_fees=total_fees,
                        quote_q=quote_q,
                        base_q=base_q
                    )

                    parent_ids = fifo_result["parent_ids"]
                    trade.parent_id = parent_ids[0] if parent_ids else None
                    trade.parent_ids = parent_ids or None
                    trade.cost_basis_usd = float(fifo_result["cost_basis_usd"])
                    trade.sale_proceeds_usd = float(fifo_result["sale_proceeds_usd"])
                    trade.net_sale_proceeds_usd = float(fifo_result["net_sale_proceeds_usd"])
                    trade.pnl_usd = float(fifo_result["pnl_usd"])
                    trade.realized_profit = float(sum(
                        Decimal(instr["realized_profit"]) for instr in fifo_result["update_instructions"]
                    ))

                    # Update parents
                    for instr in fifo_result["update_instructions"]:
                        parent = await session.get(TradeRecord, instr["order_id"])
                        if parent:
                            parent.remaining_size = instr["remaining_size"]
                            parent.realized_profit = instr["realized_profit"]

                self.logger.info(f"✅ Fixed {len(sell_trades)} unmatched SELL trades.")

    async def find_latest_unlinked_buy_id(self, symbol: str) -> Optional[str]:
        """
        Returns the order_id (str) of the earliest unlinked BUY trade for FIFO linkage.

        Use this when only the parent_id string is required, such as for trade_data inserts.
        """
        try:
            buys = await self.find_unlinked_buys(symbol)
            return buys[0].order_id if buys else None
        except Exception as e:
            if self.logger:
                self.logger.error(f"❌ Error in find_latest_unlinked_buy_id for {symbol}: {e}", exc_info=True)
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
        Recomputes PnL and parent linkages for all SELL trades using FIFO.
        Skips trades seeded via reconciliation (source == 'reconciled').
        """
        logger = self.logger
        logger.info("🧮 Starting full backfill of trade metrics...")

        try:
            async with self.db_session_manager.async_session() as session:
                async with session.begin():
                    # ✅ Fetch all SELL trades needing PnL updates
                    result = await session.execute(
                        select(TradeRecord).where(
                            TradeRecord.side == "sell"
                        )
                    )
                    sell_trades = result.scalars().all()

                    total_trades = len(sell_trades)
                    logger.info(f"🔄 Processing {total_trades} trades for backfill...")

                    processed = 0
                    for trade in sell_trades:
                        try:
                            await self._calculate_fifo_pnl(trade, session)
                            processed += 1

                            if processed % 500 == 0:
                                logger.info(f"✅ Processed {processed}/{total_trades} trades...")
                        except Exception as e:
                            logger.error(f"❌ Error calculating PnL for trade {trade.order_id}: {e}", exc_info=True)

                    logger.info(f"🎉 Backfill complete: {processed}/{total_trades} trades updated.")
        except Exception as e:
            logger.error(f"❌ Error during backfill: {e}", exc_info=True)

    async def _calculate_fifo_pnl(self, sell_trade: TradeRecord, session):
        """
        FIFO-based PnL calculation for a SELL trade.
        Updates the sell_trade and adjusts corresponding BUY trades.
        """

        sell_size = Decimal(str(sell_trade.size or 0))
        sell_price = Decimal(str(sell_trade.price or 0))
        remaining_to_sell = sell_size

        cost_basis_usd = Decimal("0")
        realized_pnl = Decimal("0")
        used_parent_ids = []

        # ✅ Fetch candidate BUY trades (FIFO order)
        result = await session.execute(
            select(TradeRecord)
            .where(
                TradeRecord.symbol == sell_trade.symbol,
                TradeRecord.side == "buy",
                TradeRecord.remaining_size > 0
            )
            .order_by(TradeRecord.order_time.asc())
        )
        buy_candidates = result.scalars().all()

        if not buy_candidates:
            self.logger.warning(f"[FIFO PROD] No candidate BUYs for {sell_trade.symbol} | Sell Trade {sell_trade.order_id}")
            return

        self.logger.debug(
            f"[FIFO PROD] Starting PnL calc for {sell_trade.symbol}: "
            f"Sell Size={sell_size}, Sell Price={sell_price}"
        )
        self.logger.debug(f"[FIFO PROD] Found {len(buy_candidates)} candidate BUYs")

        for buy in buy_candidates:
            if remaining_to_sell <= 0:
                break

            buy.remaining_size = Decimal(str(buy.remaining_size or 0))
            buy_price = Decimal(str(buy.price or 0))
            buy.realized_profit = Decimal(str(buy.realized_profit or 0))

            base_deci, quote_deci, *_ = self.shared_utils_precision.fetch_precision(sell_trade.symbol)
            base_q = Decimal("1").scaleb(-base_deci)
            quote_q = Decimal("1").scaleb(-quote_deci)

            usable_size = min(remaining_to_sell, buy.remaining_size)
            cost_basis_part = usable_size * buy_price
            pnl_part = (sell_price - buy_price) * usable_size

            # ✅ Update BUY trade
            buy.remaining_size = self.shared_utils_precision.safe_quantize(buy.remaining_size - usable_size, base_q)
            buy.realized_profit = self.shared_utils_precision.safe_quantize(buy.realized_profit + pnl_part, base_q)

            await session.flush()

            # ✅ Track SELL trade PnL
            cost_basis_usd += cost_basis_part
            realized_pnl += pnl_part
            used_parent_ids.append(buy.order_id)

            remaining_to_sell -= usable_size

            self.logger.debug(
                f"[FIFO PROD] Parent {buy.order_id} | "
                f"Size={buy.size}, Usable={usable_size}, RemainingAfter={buy.remaining_size} | "
                f"CostBasis+={cost_basis_part}, Realized+={pnl_part}"
            )

        sale_proceeds_usd = sell_size * sell_price

        # ✅ Update SELL trade
        sell_trade.cost_basis_usd = float(cost_basis_usd)
        sell_trade.sale_proceeds_usd = float(sale_proceeds_usd)
        sell_trade.net_sale_proceeds_usd = float(sale_proceeds_usd)  # adjust if you subtract fees
        sell_trade.pnl_usd = float(realized_pnl)
        sell_trade.parent_ids = used_parent_ids
        sell_trade.parent_id = used_parent_ids[0] if used_parent_ids else None

        await session.flush()

        self.logger.debug(
            f"[FIFO PROD RESULT] {sell_trade.symbol} | "
            f"PnL={realized_pnl} | CostBasis={cost_basis_usd} | "
            f"SaleProceeds={sale_proceeds_usd} | Parents={used_parent_ids}"
        )

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


# <><><><><><><><><><><><><><><><><><>><><><><<>
    async def test_fifo_prod(self, symbol: str):
        """
        Quick production test harness to validate FIFO logic directly
        against the live trade_records table.

        Args:
            symbol (str): Trading pair to test (e.g., "EDGE-USD").
        """
        try:
            # ✅ 1. Fetch first SELL trade from production table
            async with self.db_session_manager.async_session_factory() as session:
                async with session.begin():
                    stmt = (
                        select(TradeRecord)
                        .where(
                            TradeRecord.symbol == symbol,
                            TradeRecord.side == "sell"
                        )
                        .order_by(TradeRecord.order_time.asc())
                        .limit(1)
                    )
                    result = await session.execute(stmt)
                    sell_trade = result.scalar_one_or_none()

            if not sell_trade:
                self.logger.warning(f"[TEST HARNESS PROD] No SELL trades found for {symbol}")
                return

            # ✅ 2. Prepare precision
            base_deci, quote_deci, *_ = self.shared_utils_precision.fetch_precision(symbol)
            base_q = Decimal("1").scaleb(-base_deci)
            quote_q = Decimal("1").scaleb(-quote_deci)

            self.logger.info(
                f"\n========== [TEST HARNESS: FIFO PROD START] ==========\n"
                f"Symbol: {symbol}\n"
                f"Sell Order ID: {sell_trade.order_id}\n"
                f"Sell Size: {sell_trade.size}\n"
                f"Sell Price: {sell_trade.price}\n"
                f"====================================================="
            )

            # ✅ 3. Run production FIFO method
            async with self.db_session_manager.async_session_factory() as session:
                async with session.begin():
                    result = await self.compute_cost_basis_and_sale_proceeds(
                        session=session,
                        symbol=symbol,
                        size=Decimal(sell_trade.size),
                        sell_price=Decimal(sell_trade.price),
                        total_fees=Decimal(sell_trade.total_fees_usd or 0),
                        quote_q=quote_q,
                        base_q=base_q
                    )

            # ✅ 4. Display Results
            self.logger.info(
                f"\n========== [TEST HARNESS: FIFO PROD RESULT] ==========\n"
                f"Cost Basis USD: {result['cost_basis_usd']}\n"
                f"Sale Proceeds USD: {result['sale_proceeds_usd']}\n"
                f"Net Sale Proceeds USD: {result['net_sale_proceeds_usd']}\n"
                f"PnL USD: {result['pnl_usd']}\n"
                f"Parent IDs: {result['parent_ids']}\n"
                f"Update Instructions: {result['update_instructions']}\n"
                f"======================================================"
            )

        except Exception as e:
            self.logger.error(f"❌ [TEST HARNESS PROD ERROR] {e}", exc_info=True)

    async def test_fifo_debug(self, symbol: str):
        """
        Quick test harness to run FIFO debug on the first SELL trade of a given symbol.
        Uses trade_records_debug table for safety.

        Args:
            trade_recorder: Instance of TradeRecorder (with compute_cost_basis_and_sale_proceeds_debug method).
            symbol (str): The trading pair to test (e.g., "SPK-USD").
        """
        try:
            # -----------------------------
            # ✅ Step 1: Fetch first SELL trade from debug table
            # -----------------------------
            async with self.db_session_manager.async_session_factory() as session:
                async with session.begin():
                    stmt = (
                        select(TradeRecordDebug)
                        .where(
                            TradeRecordDebug.symbol == symbol,
                            TradeRecordDebug.side == "sell"
                        )
                        .order_by(TradeRecordDebug.order_time.asc())
                        .limit(1)
                    )
                    result = await session.execute(stmt)
                    sell_trade = result.scalar_one_or_none()

            if not sell_trade:
                self.logger.warning(f"[TEST HARNESS] No SELL trades found for {symbol}")
                return

            # -----------------------------
            # ✅ Step 2: Prepare precision & debug call
            # -----------------------------
            base_deci, quote_deci, *_ = self.shared_utils_precision.fetch_precision(symbol)
            base_q = Decimal("1").scaleb(-base_deci)
            quote_q = Decimal("1").scaleb(-quote_deci)

            self.logger.info(
                f"\n========== [TEST HARNESS: FIFO DEBUG START] ==========\n"
                f"Symbol: {symbol}\n"
                f"Sell Order ID: {sell_trade.order_id}\n"
                f"Sell Size: {sell_trade.size}\n"
                f"Sell Price: {sell_trade.price}\n"
                f"====================================================="
            )

            # -----------------------------
            # ✅ Step 3: Run the FIFO Debug
            # -----------------------------
            async with self.db_session_manager.async_session_factory() as session:
                async with session.begin():
                    result = await self.compute_cost_basis_and_sale_proceeds_debug(
                        session=session,
                        symbol=symbol,
                        size=Decimal(sell_trade.size),
                        sell_price=Decimal(sell_trade.price),
                        total_fees=Decimal(sell_trade.total_fees_usd or 0),
                        quote_q=quote_q,
                        base_q=base_q
                    )

            # -----------------------------
            # ✅ Step 4: Display Results
            # -----------------------------
            self.logger.info(
                f"\n========== [TEST HARNESS: FIFO DEBUG RESULT] ==========\n"
                f"Cost Basis USD: {result['cost_basis_usd']}\n"
                f"Sale Proceeds USD: {result['sale_proceeds_usd']}\n"
                f"Net Sale Proceeds USD: {result['net_sale_proceeds_usd']}\n"
                f"PnL USD: {result['pnl_usd']}\n"
                f"Parent IDs: {result['parent_ids']}\n"
                f"Update Instructions: {result['update_instructions']}\n"
                f"======================================================"
            )

        except Exception as e:
            self.logger.error(f"❌ [TEST HARNESS ERROR] {e}", exc_info=True)

    async def test_performance_tracker(self):
        """
        Test harness to verify PassiveMM performance tracker alignment with SQL logic.
        Prints the same output as the SQL query.
        """
        try:
            self.logger.info("[TEST PERFORMANCE TRACKER] Running SQL-aligned performance stats...")

            async with self.db_session_manager.async_session_factory() as session:
                async with session.begin():
                    stmt = (
                        select(
                            TradeRecord.symbol,
                            func.count().label("total_trades"),
                            (
                                    func.sum(
                                        case((TradeRecord.pnl_usd > 0, 1), else_=0)
                                    ).cast(Float) / func.count() * 100
                            ).label("win_rate"),
                            func.sum(TradeRecord.pnl_usd).label("total_pnl"),
                            func.avg(TradeRecord.pnl_usd).label("avg_pnl"),
                        )
                        .where(
                            TradeRecord.side == "sell",
                            TradeRecord.pnl_usd.isnot(None),
                            TradeRecord.order_time >= datetime.now(timezone.utc) - timedelta(days=7),
                        )
                        .group_by(TradeRecord.symbol)
                        .order_by(func.sum(TradeRecord.pnl_usd).desc())
                        .limit(10)
                    )

                    result = await session.execute(stmt)
                    rows = result.all()

            total_trades = sum(row.total_trades for row in rows)
            win_rate = (
                (sum(1 for row in rows if row.total_pnl > 0) / len(rows)) * 100
                if rows else 0.0
            )
            total_pnl = sum(row.total_pnl for row in rows)
            avg_pnl = total_pnl / total_trades if total_trades else 0.0
            top_symbols = {row.symbol: row.total_pnl for row in rows}

            # ✅ Log in the same style as the old tracker
            self.logger.info(
                "\n========== [TEST HARNESS: PERFORMANCE TRACKER RESULT] ==========\n"
                f"Total Trades (last 7d): {total_trades}\n"
                f"Win Rate: {win_rate:.2f}%\n"
                f"Total PnL: {total_pnl:+.2f} USD\n"
                f"Average PnL/Trade: {avg_pnl:+.2f} USD\n"
                f"Top Symbols: {top_symbols}\n"
                "==============================================================="
            )

        except Exception as e:
            self.logger.error(f"❌ [TEST PERFORMANCE TRACKER ERROR] {e}", exc_info=True)