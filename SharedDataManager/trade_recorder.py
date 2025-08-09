
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
                self.logger.warning("🛑 stop_worker was cancelled.", exc_info=True)
                raise
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

    async def _fetch_order_fills(self, order_id: str) -> list[dict]:
        """
        Return fills for an order as a list of dicts:
          [{ "qty": Decimal, "price": Decimal, "fee_usd": Decimal }, ...]
        If your API returns fees in quote currency (USD) we map directly.
        If it returns fee in base or as a rate, convert before returning.
        """
        try:
            # Adapt this to your actual coinbase_api method name / shape.
            # Examples of plausible methods you might already have:
            # resp = await self.coinbase_api.get_order_fills(order_id)
            # or: resp = await self.coinbase_api.list_fills(order_id=order_id)
            resp = await self.coinbase_api.get_order_fills(order_id)  # <— adjust to your client

            fills_raw = resp.get("fills") or resp.get("data") or []
            out = []
            for f in fills_raw:
                # Try common field names; fallback safely
                qty = f.get("size") or f.get("filled_size") or f.get("qty") or "0"
                price = f.get("price") or f.get("executed_price") or "0"

                # Fee: prefer explicit USD; else 0 (or convert if you have fields to do so)
                fee_usd = (
                        f.get("fee_usd")
                        or f.get("fee")  # if your API guarantees quote=USD for USD pairs
                        or "0"
                )

                out.append({
                    "qty": Decimal(str(qty)),
                    "price": Decimal(str(price)),
                    "fee_usd": Decimal(str(fee_usd)),
                })
            return out
        except Exception as e:
            self.logger.warning(f"⚠️ _fetch_order_fills failed for {order_id}: {e}", exc_info=True)
            return []


    def _normalize_batch_order_to_fills(self, order: dict) -> tuple[list[dict], dict]:
        """
        Normalize a single order from get_historical_orders_batch(...) into:
          - fills: list[{"qty": Decimal, "price": Decimal, "fee_usd": Decimal}]
          - overrides: {"gross_override": Decimal|None, "fees_override": Decimal|None}

        We *prefer* using filled_value and total_fees to avoid rounding drift,
        but we also provide a synthetic fill so existing code paths still work.
        """
        filled_size = Decimal(str(order.get("filled_size") or "0"))
        avg_price = Decimal(str(order.get("average_filled_price") or "0"))
        total_fees = Decimal(str(order.get("total_fees") or "0"))
        filled_value = order.get("filled_value")
        gross = Decimal(str(filled_value)) if filled_value not in (None, "") else (filled_size * avg_price)

        fills = [{
            "qty": filled_size,
            "price": avg_price,
            "fee_usd": total_fees,
        }]

        overrides = {
            "gross_override": gross,
            "fees_override": total_fees,
        }
        return fills, overrides


    # =====================================================
    # ✅ Main Trade Recording Logic (Option 1 + Option 2)
    # =====================================================

    async def get_trade_record_count(self):
        try:
            async with self.db_session_manager.async_session() as session:
                async with session.begin():
                    result = await session.execute(select(func.count()).select_from(TradeRecord))
                    return result.scalar()
        except asyncio.CancelledError:
            self.logger.warning("🛑 get_trade_record_count was cancelled.")
            raise

    async def record_trade(self, trade_data: dict):
        """
        Records a trade (BUY or SELL) into the database.

        ✅ Single-session flow:
            - FIFO PnL calculation (SELL)
            - Insert/Update SELL or BUY trade
            - Update parent BUYs' remaining_size & realized_profit (SELL only)
        """
        try:
            fills_in = trade_data.get("fills")
            gross_override_raw = trade_data.get("gross_override")
            fees_override_raw = trade_data.get("fees_override")
            gross_override = Decimal(gross_override_raw) if gross_override_raw not in (None, "") else None
            fees_override = Decimal(fees_override_raw) if fees_override_raw not in (None, "") else None
            symbol = trade_data["symbol"]
            side = trade_data["side"].lower()
            order_id = trade_data["order_id"]
            preferred_parent_id = trade_data.get("preferred_parent_id")
            status = trade_data.get("status")
            trigger = trade_data.get("trigger")
            source = trade_data.get("source")

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

            async with self.db_session_manager.async_session() as session:
                async with session.begin():
                    self.active_session = session

                    if side == "sell":
                        # Prefer batch overrides for exact proceeds/fees
                        if fees_override is not None:
                            total_fees = fees_override

                        fifo_result = await self.compute_cost_basis_and_sale_proceeds(
                            symbol=symbol,
                            size=amount,
                            sell_price=price,
                            total_fees=total_fees,
                            quote_q=quote_q,
                            base_q=base_q,
                            sell_time=order_time,
                            sell_fills=None,
                            cost_basis_usd=Decimal(0),
                            gross_override=gross_override,
                            sell_fee_total_override=fees_override,
                            preferred_parent_id=preferred_parent_id,
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
                        parent_id = trade_data.get("parent_id") or order_id
                        parent_ids = [parent_id] if parent_id else []

                        # If no fees provided in trade_data, but we have fills, sum the actual USD fees
                        if (total_fees is None or total_fees == 0) and fills_in:
                            try:
                                total_fees = sum((Decimal(f["fee_usd"]) for f in fills_in), Decimal("0"))
                            except Exception as e:
                                self.logger.warning(f"Failed to compute buy fees from fills: {e}")

                    # -----------------------------
                    # ✅ Normalize parent_id
                    # -----------------------------
                    if isinstance(parent_id, list):
                        parent_id = parent_id[0] if parent_id else None
                    elif parent_id is not None and not isinstance(parent_id, str):
                        parent_id = str(parent_id)

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
                        # SELL row's realized_profit equals pnl_usd; BUY has None
                        "realized_profit": float(pnl_usd) if side == "sell" and pnl_usd is not None else None,
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
                        # parent_record.realized_profit = instruction["realized_profit"]  # ← skip if you don't want this on BUYs

                self.logger.info(
                    f"✅ Trade recorded: {symbol} {side.upper()} {amount}@{price} | "
                    f"PnL: {pnl_usd} | Parents: {parent_ids}"
                )
            self.active_session = None

        except asyncio.CancelledError:
            self.active_session = None
            self.logger.warning("🛑 record_trade was cancelled.", exc_info=True)
            raise
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
            base_q: Decimal,
            sell_time: datetime,
            cost_basis_usd: Optional[Decimal] = 0,
            preferred_parent_id: Optional[str] = None,
            *,
            sell_fills: Optional[list[dict]] = None,
            gross_override: Optional[Decimal] = None,
            sell_fee_total_override: Optional[Decimal] = None
    ) -> dict:
        """
        Computes FIFO cost basis and sale proceeds for a SELL trade with fee-inclusive logic.

        - Cost basis = SUM over parents ( (take / parent.original_size) * (parent.subtotal + parent.buy_fees) )
          where parent.subtotal ≈ parent.price * parent.original_size
        - Net proceeds = gross - sell_fees (from fills if provided; else price*size - total_fees)
        - PnL = net - cost_basis

        Returns:
            {
              "cost_basis_usd", "sale_proceeds_usd", "net_sale_proceeds_usd",
              "pnl_usd", "parent_ids", "update_instructions"
            }
        """
        parent_ids: list[str] = []
        update_instructions: list[dict] = []

        def q_usd(x: Decimal) -> Decimal:
            return self.shared_utils_precision.safe_quantize(x, quote_q)

        def q_base(x: Decimal) -> Decimal:
            return self.shared_utils_precision.safe_quantize(x, base_q)

        try:
            session = self.active_session
            if not session:
                raise RuntimeError("No active DB session. Must be called from inside record_trade().")

            # ---------- 1) Proceeds (prefer overrides) ----------
            if gross_override is not None or sell_fee_total_override is not None:
                gross = gross_override if gross_override is not None else (size * sell_price)
                sell_fee_total = sell_fee_total_override if sell_fee_total_override is not None else (total_fees or Decimal("0"))
            elif sell_fills:
                gross = sum(Decimal(str(f["qty"])) * Decimal(str(f["price"])) for f in sell_fills)
                sell_fee_total = sum(Decimal(str(f.get("fee_usd", "0"))) for f in sell_fills)
            else:
                gross = size * sell_price
                sell_fee_total = total_fees or Decimal("0")

            net = gross - sell_fee_total

            # ---------- 2) Fetch candidate BUY parents (FIFO) ----------
            tolerance = timedelta(seconds=1)
            stmt = (
                select(TradeRecord)
                .where(
                    TradeRecord.symbol == symbol,
                    TradeRecord.side == "buy",
                    TradeRecord.order_time <= sell_time + tolerance,

                    TradeRecord.order_time <= sell_time,
                    or_(
                        TradeRecord.remaining_size.is_(None),
                        TradeRecord.remaining_size > 0
                    ),
                    not_(TradeRecord.order_id.like('%-FILL-%')),
                    not_(TradeRecord.order_id.like('%-FALLBACK'))
                )
                .order_by(TradeRecord.order_time.asc())
            )
            result = await session.execute(stmt)
            parent_trades = result.scalars().all()

            if preferred_parent_id:
                parent_trades = [pt for pt in parent_trades if pt.order_id == preferred_parent_id]
                if not parent_trades:
                    self.logger.warning(f"[FIFO] Specified parent {preferred_parent_id} not found or exhausted.")
                    return self._empty_result()

            if not parent_trades:
                self.logger.warning(f"[FIFO] No eligible BUY trades for {symbol} before {sell_time}")
                return self._empty_result()

            # ---------- 3) Allocate FIFO with fee-inclusive cost basis ----------
            need = size
            total_cost_basis = Decimal("0")

            for pt in parent_trades:
                if need <= 0:
                    break

                original_size = Decimal(str(pt.size or 0))  # original buy size
                rem_size = Decimal(str(pt.remaining_size or pt.size or 0))
                if rem_size <= 0 or original_size <= 0:
                    continue

                take = min(rem_size, need)
                if take <= 0:
                    continue

                buy_price = Decimal(str(pt.price or 0))
                buy_fees = Decimal(str(pt.total_fees_usd or 0))

                # Fee-inclusive total buy cost for the parent
                parent_gross_subtotal = buy_price * original_size
                parent_total_cost = parent_gross_subtotal + buy_fees

                # Pro-rate by original size (not remaining)
                ratio = take / original_size
                cost_alloc = parent_total_cost * ratio

                total_cost_basis += cost_alloc

                # Track
                if pt.order_id not in parent_ids:
                    parent_ids.append(pt.order_id)

                # Update parent remaining_size and (optionally) parent realized_profit increment
                remaining_after = q_base(rem_size - take)

                # Pro-rate sell fee to this allocation by quantity share of the SELL
                sell_fee_alloc = (sell_fee_total * (take / size)) if size > 0 else Decimal("0")
                # Pro-rate gross proceeds to this allocation
                gross_alloc = (gross * (take / size)) if size > 0 else Decimal("0")
                # Net alloc
                net_alloc = gross_alloc - sell_fee_alloc

                realized_profit_alloc = net_alloc - cost_alloc

                update_instructions.append({
                    "order_id": pt.order_id,
                    "remaining_size": float(remaining_after),
                    # Optional: keep parents’ realized_profit as running total.
                    # If you prefer to *not* track realized_profit on BUY rows, set this to float(pt.realized_profit or 0)
                    "realized_profit": float(q_usd(Decimal(str(pt.realized_profit or 0)) + realized_profit_alloc)),
                })

                need = q_base(need - take)

            if size <= 0:
                return self._empty_result()

            # ---------- 4) Finalize numbers ----------
            cost_basis_usd = q_usd(total_cost_basis)
            sale_proceeds_usd = q_usd(gross)
            net_sale_proceeds_usd = q_usd(net)
            pnl_usd = q_usd(net - total_cost_basis)

            return {
                "cost_basis_usd": float(cost_basis_usd),
                "sale_proceeds_usd": float(sale_proceeds_usd),
                "net_sale_proceeds_usd": float(net_sale_proceeds_usd),
                "pnl_usd": float(pnl_usd),
                "parent_ids": parent_ids,
                "update_instructions": update_instructions
            }

        except asyncio.CancelledError:
            self.logger.warning("🛑 compute_cost_basis_and_sale_proceeds was cancelled.")
            raise
        except Exception as e:
            self.logger.error(f"❌ Error in compute_cost_basis_and_sale_proceeds for {symbol}: {e}", exc_info=True)
            return self._empty_result()

    def _empty_result(self):
        return {
            "cost_basis_usd": 0,
            "sale_proceeds_usd": 0,
            "net_sale_proceeds_usd": 0,
            "pnl_usd": 0,
            "parent_ids": [],
            "update_instructions": []
        }

    async def fetch_all_trades(self):
        """
        Fetch all recorded trades.
        """
        try:
            async with self.db_session_manager.async_session() as session:
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
            async with self.db_session_manager.async_session() as session:
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
        async with self.db_session_manager.async_session() as session:
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
            async with self.db_session_manager.async_session() as session:
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
        except asyncio.CancelledError:
            self.logger.warning("🛑 find_unlinked_buys was cancelled.", exc_info=True)
            raise
        except Exception as e:
            if self.logger:
                self.logger.error(f"❌ Error in find_unlinked_buys for {symbol}: {e}", exc_info=True)
            return []

    async def fix_unlinked_sells(self):
        """Recomputes PnL and parent linkage for unmatched SELL trades."""

        self.logger.info("🔧 Running fix_unlinked_sells()...")
        try:
            async with self.db_session_manager.async_session() as session:
                async with session.begin():
                    self.active_session = session

                    stmt = select(TradeRecord).where(
                        TradeRecord.side == "sell",
                        TradeRecord.pnl_usd.is_(None),
                        TradeRecord.source != "reconciled"
                    )
                    result = await session.execute(stmt)
                    sell_trades = result.scalars().all()

                    if not sell_trades:
                        self.logger.info("✅ No unmatched SELL trades found.")
                        return

                    for trade in sell_trades:
                        symbol = trade.symbol
                        amount = Decimal(trade.size)
                        price = Decimal(trade.price)
                        total_fees = Decimal(trade.total_fees_usd or 0)

                        base_deci, quote_deci, *_ = self.shared_utils_precision.fetch_precision(symbol)
                        base_q = Decimal("1").scaleb(-base_deci)
                        quote_q = Decimal("1").scaleb(-quote_deci)

                        # 🧮 Run FIFO matching
                        fifo_result = await self.compute_cost_basis_and_sale_proceeds(
                            symbol=symbol,
                            size=amount,
                            sell_price=price,
                            total_fees=total_fees,
                            quote_q=quote_q,
                            base_q=base_q,
                            sell_time=trade.order_time
                        )

                        parent_ids = fifo_result["parent_ids"]
                        if not parent_ids and amount > 0:
                            self.logger.warning(
                                f"❌ Unlinked SELL {trade.order_id} {amount} {symbol} — "
                                f"no BUY match found. Review fill timing or data integrity."
                            )

                        trade.parent_id = parent_ids[0] if parent_ids else None
                        trade.parent_ids = parent_ids or None
                        trade.cost_basis_usd = float(fifo_result["cost_basis_usd"])
                        trade.sale_proceeds_usd = float(fifo_result["sale_proceeds_usd"])
                        trade.net_sale_proceeds_usd = float(fifo_result["net_sale_proceeds_usd"])
                        trade.pnl_usd = float(fifo_result["pnl_usd"])

                        trade.realized_profit = float(sum(
                            Decimal(instr["realized_profit"])
                            for instr in fifo_result["update_instructions"]
                        ))

                        # 🔁 Update parent BUYs
                        for instr in fifo_result["update_instructions"]:
                            parent = await session.get(TradeRecord, instr["order_id"])
                            if parent:
                                parent.remaining_size = instr["remaining_size"]
                                parent.realized_profit = instr["realized_profit"]

                    self.logger.info(f"✅ Fixed {len(sell_trades)} unmatched SELL trades.")

        except asyncio.CancelledError:
            self.logger.warning("🛑 fix_unlinked_sells was cancelled.")
            raise

        except Exception as e:
            self.logger.error(f"❌ Error in fix_unlinked_sells: {e}", exc_info=True)

        finally:
            self.active_session = None  # ✅ Clean up even if exception occurs

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
        try:
            async with self.db_session_manager.async_session() as session:
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
        except asyncio.CancelledError:
            self.logger.warning("🛑 fetch_trade_by_order_id was cancelled.", exc_info=True)
            raise


    async def find_latest_filled_size(self, symbol: str, side: str = 'buy') -> Optional[Decimal]:
        """
        Returns the size of the most recent filled trade for a given symbol and side (buy/sell).
        """
        async with self.db_session_manager.async_session() as session:
            async with session.begin():
                try:
                    base_deci, _, _, _ = self.shared_utils_precision.fetch_precision(symbol)
                    result = await session.execute(
                        select(TradeRecord)
                        .where(
                            TradeRecord.symbol == symbol,
                                        TradeRecord.side == side,
                                        TradeRecord.status.ilike('placed')
                        )  # or 'filled' depending on your usage
                        .order_by(TradeRecord.order_time.asc())
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
                            TradeRecord.side == "sell",
                            TradeRecord.source != "reconciled"
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
        try:
            if sell_trade.source == "reconciled":
                self.logger.info(f"🛡️ Skipping PnL calc for reconciled trade {sell_trade.order_id}")
                return

            size = Decimal(str(sell_trade.size or 0))
            price = Decimal(str(sell_trade.price or 0))
            total_fees = Decimal(str(sell_trade.total_fees_usd or 0))

            base_deci, quote_deci, *_ = self.shared_utils_precision.fetch_precision(sell_trade.symbol)
            base_q = Decimal("1").scaleb(-base_deci)
            quote_q = Decimal("1").scaleb(-quote_deci)

            self.active_session = session  # ensure compute_* sees a session

            fifo_result = await self.compute_cost_basis_and_sale_proceeds(
                symbol=sell_trade.symbol,
                size=size,
                sell_price=price,
                total_fees=total_fees,
                quote_q=quote_q,
                base_q=base_q,
                sell_time=sell_trade.order_time,
                sell_fills=None  # or fetch here if you add a fills fetcher
            )

            sell_trade.parent_ids = fifo_result["parent_ids"] or None
            sell_trade.parent_id = fifo_result["parent_ids"][0] if fifo_result["parent_ids"] else None
            sell_trade.cost_basis_usd = fifo_result["cost_basis_usd"]
            sell_trade.sale_proceeds_usd = fifo_result["sale_proceeds_usd"]
            sell_trade.net_sale_proceeds_usd = fifo_result["net_sale_proceeds_usd"]
            sell_trade.pnl_usd = fifo_result["pnl_usd"]
            sell_trade.realized_profit = fifo_result["pnl_usd"]

            # 🔁 Update parents
            for instr in fifo_result["update_instructions"]:
                parent = await session.get(TradeRecord, instr["order_id"])
                if parent:
                    parent.remaining_size = instr["remaining_size"]
                    parent.realized_profit = instr["realized_profit"]

        except asyncio.CancelledError:
            self.logger.warning(f"🛑 FIFO PnL task cancelled for sell trade {sell_trade.order_id}")
            raise
        except Exception as e:
            self.logger.error(f"❌ FIFO PnL error for {sell_trade.symbol}: {e}", exc_info=True)

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
            async with self.db_session_manager.async_session() as session:
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
        except asyncio.CancelledError:
            self.logger.warning("🛑 fetch_sells_by_date was cancelled.", exc_info=True)
            raise

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
            async with self.db_session_manager.async_session() as session:
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
                    async with self.db_session_manager.async_session() as session:
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
            async with self.db_session_manager.async_session() as session:
                async with session.begin():
                    result = await self.compute_cost_basis_and_sale_proceeds(
                        session=session,
                        symbol=symbol,
                        size=Decimal(sell_trade.size),
                        sell_price=Decimal(sell_trade.price),
                        total_fees=Decimal(sell_trade.total_fees_usd or 0),
                        quote_q=quote_q,
                        base_q=base_q,
                        sell_time=sell_trade.order_time
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
            async with self.db_session_manager.async_session() as session:
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
                    async with self.db_session_manager.async_session() as session:
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
            async with self.db_session_manager.async_session() as session:
                async with session.begin():
                    result = await self.compute_cost_basis_and_sale_proceeds_debug(
                        session=session,
                        symbol=symbol,
                        size=Decimal(sell_trade.size),
                        sell_price=Decimal(sell_trade.price),
                        total_fees=Decimal(sell_trade.total_fees_usd or 0),
                        quote_q=quote_q,
                        base_q=base_q,
                        sell_time=sell_trade.order_time
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

            async with self.db_session_manager.async_session() as session:
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
                        .order_by(func.sum(TradeRecord.pnl_usd).asc())
                        .limit(10)
                    )

                    async with self.db_session_manager.async_session() as session:
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