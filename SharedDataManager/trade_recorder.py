from __future__ import annotations  # optional, but nice for type hints

import decimal
from decimal import Decimal, ROUND_DOWN, InvalidOperation

import asyncio
from TableModels.trade_record import TradeRecord
from TestDebugMaintenance.trade_record_maintenance import recompute_fifo_for_symbol
from Shared_Utils.logger import get_logger

# from TableModels.trade_record_debug import TradeRecordDebug
from sqlalchemy.dialects.postgresql import insert as pg_insert
from asyncio import Queue
from typing import Optional
from sqlalchemy import text, func, and_, or_, not_
from sqlalchemy.future import select

from datetime import datetime, timezone, timedelta

class TradeRecorder:
    """
    Handles recording of trades into the trade_records table.
    """

    def __init__(self, database_session_manager, logger, shared_utils_precision, coinbase_api, maintenance_callback=None, shared_data_manager=None):
        self.db_session_manager = database_session_manager
        self.logger = get_logger('trade_recorder', context={'component': 'trade_recorder'})
        self.shared_utils_precision = shared_utils_precision
        self.coinbase_api = coinbase_api
        self.run_maintenance_if_needed = maintenance_callback
        self.shared_data_manager = shared_data_manager

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
        self.logger.debug("Trade queued",
                         extra={'symbol': trade_data.get('symbol'), 'side': trade_data.get('side')})

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

        Invariants:
          - `source` represents the origin-of-intent (webhook | websocket | passivemm | manual | unknown).
          - `source` is set on INSERT only and is immutable thereafter.
          - For SELLs with missing/unknown/reconciled source, inherit the parent BUY's source.

        Flow:
          - Normalize inputs and precision.
          - For SELL: compute FIFO cost basis & PnL (if enough parent liquidity).
          - Build row and UPSERT with `source` excluded from UPDATE set.
          - For SELL: update parent BUYs' remaining_size (and optionally realized_profit).
        """
        try:
            fills_in = trade_data.get("fills")
            gross_override_raw = trade_data.get("gross_override")
            fees_override_raw = trade_data.get("fees_override")
            gross_override = Decimal(gross_override_raw) if gross_override_raw not in (None, "") else None
            fees_override = Decimal(fees_override_raw) if fees_override_raw not in (None, "") else None

            symbol = trade_data["symbol"]
            side = (trade_data["side"] or "").lower()
            order_id = trade_data["order_id"]
            preferred_parent_id = trade_data.get("preferred_parent_id")
            status = trade_data.get("status")
            trigger = trade_data.get("trigger")
            # incoming source (may be unknown)
            source_in = (trade_data.get("source") or "").lower()
            ingest_via = trade_data.get("ingest_via")  # 'websocket' | 'rest' | 'manual' | 'import' (optional)
            # sensible default if caller didn’t supply a source
            source_final = source_in
            if not source_final:
                if ingest_via in ("websocket",):
                    source_final = "websocket"
                elif ingest_via in ("rest", "import"):
                    source_final = "reconciled"  # marked as “unknownish” so we can upgrade later
                elif ingest_via in ("manual",):
                    source_final = "manual"

            last_recon_raw = trade_data.get("last_reconciled_at")
            last_reconciled_at = None
            if last_recon_raw:
                if isinstance(last_recon_raw, str):
                    last_reconciled_at = datetime.fromisoformat(last_recon_raw.replace("Z", "+00:00"))
                else:
                    last_reconciled_at = last_recon_raw

            last_reconciled_via = trade_data.get("last_reconciled_via")  # e.g., 'rest_api'

            # -----------------------------
            # Normalize order_time -> UTC aware
            # -----------------------------
            order_time_raw = trade_data.get("order_time", datetime.now(timezone.utc))
            if isinstance(order_time_raw, str):
                parsed_time = datetime.fromisoformat(order_time_raw.replace("Z", "+00:00"))
            else:
                parsed_time = order_time_raw
            order_time_utc = (
                parsed_time.astimezone(timezone.utc)
                if parsed_time.tzinfo else parsed_time.replace(tzinfo=timezone.utc)
            )

            # -----------------------------
            # Precision & safe conversions
            # -----------------------------
            base_deci, quote_deci, *_ = self.shared_utils_precision.fetch_precision(symbol)
            base_q = Decimal("1").scaleb(-base_deci)
            quote_q = Decimal("1").scaleb(-quote_deci)

            amount = self.shared_utils_precision.safe_convert(trade_data["amount"], base_deci)
            price = self.shared_utils_precision.safe_convert(trade_data["price"], quote_deci)

            total_fees_raw = trade_data.get("total_fees") or trade_data.get("total_fees_usd")
            total_fees = Decimal(total_fees_raw) if total_fees_raw not in (None, "") else Decimal("0")

            parent_id = trade_data.get("parent_id")
            parent_ids: list[str] | None = None
            pnl_usd = cost_basis_usd = sale_proceeds_usd = net_sale_proceeds_usd = None
            update_instructions = []

            async with self.db_session_manager.async_session() as session:
                async with session.begin():
                    self.active_session = session

                    # -----------------------------
                    # SELL: defer FIFO to external engine
                    # -----------------------------
                    if side == "sell":
                        if fees_override is not None:
                            total_fees = fees_override

                        # ✅ NEW APPROACH: Don't compute PnL inline
                        # The FIFO engine (scripts/compute_allocations.py) will compute PnL
                        # and populate the fifo_allocations table.
                        parent_ids = None
                        parent_id = None
                        pnl_usd = None
                        cost_basis_usd = None
                        sale_proceeds_usd = None
                        net_sale_proceeds_usd = None
                        update_instructions = []

                        self.logger.info(
                            f"📝 SELL recorded: {symbol} {amount}@{price} | "
                            f"PnL will be computed by FIFO engine (external process). "
                            f"Order ID: {order_id}"
                        )

                    else:
                        # -----------------------------
                        # BUY: baseline fields
                        # -----------------------------
                        # For buys, anchor chain to this buy
                        parent_id = trade_data.get("parent_id")  # keep if provided
                        parent_ids = [order_id]

                        # If no fees provided but we have fills, sum USD fees
                        if (total_fees is None or total_fees == 0) and fills_in:
                            try:
                                total_fees = sum((Decimal(f["fee_usd"]) for f in fills_in), Decimal("0"))
                            except Exception as e:
                                self.logger.warning(f"Failed to compute buy fees from fills: {e}")

                    # -----------------------------
                    # Normalize parent_id
                    # -----------------------------
                    if isinstance(parent_id, list):
                        parent_id = parent_id[0] if parent_id else None
                    elif parent_id is not None and not isinstance(parent_id, str):
                        parent_id = str(parent_id)

                    # -----------------------------
                    # Decide final `source` for this row
                    # -----------------------------
                    source_final = source_in
                    unknownish = {"", "unknown", "reconciled"}

                    if side == "sell" and (source_final in unknownish):
                        # Try explicit parent in input first
                        parent_candidate = trade_data.get("parent_id")
                        # Else try FIFO-derived parent_ids
                        if not parent_candidate and parent_ids:
                            parent_candidate = parent_ids[0]
                        if parent_candidate:
                            parent_row = await session.get(TradeRecord, parent_candidate)
                            if parent_row and (parent_row.source or "") not in unknownish:
                                source_final = parent_row.source

                    # -----------------------------
                    # Build row
                    # -----------------------------
                    trade_dict = {
                        "order_id": order_id,
                        "parent_id": parent_id,
                        "parent_ids": parent_ids or None,
                        "symbol": symbol,
                        "side": side,
                        "order_time": order_time_utc,
                        "price": float(price),
                        "size": float(amount),
                        "pnl_usd": float(pnl_usd) if pnl_usd is not None else None,
                        "total_fees_usd": float(total_fees),
                        "trigger": trigger,
                        "order_type": trade_data.get("order_type"),
                        "status": status,
                        # 🔒 origin-of-intent (immutable after insert)
                        "source": source_final,
                        # Derived SELL fields
                        "cost_basis_usd": float(cost_basis_usd) if cost_basis_usd is not None else None,
                        "sale_proceeds_usd": float(sale_proceeds_usd) if sale_proceeds_usd is not None else None,
                        "net_sale_proceeds_usd": float(net_sale_proceeds_usd) if net_sale_proceeds_usd is not None else None,
                        # BUY remaining_size initialized to full amount; SELL None
                        "remaining_size": float(amount) if side == "buy" else None,
                        # SELL realized_profit deprecated - use fifo_allocations table
                        # Set to None; will be backfilled from FIFO for historical accuracy
                        "realized_profit": None,
                    }

                    if ingest_via:
                        trade_dict["ingest_via"] = ingest_via
                    if last_reconciled_at:
                        trade_dict["last_reconciled_at"] = last_reconciled_at
                    if last_reconciled_via:
                        trade_dict["last_reconciled_via"] = last_reconciled_via


                    # -----------------------------
                    # UPSERT (preserving `source`)
                    # -----------------------------

                    # 1) Limit to real table columns
                    table_cols = set(TradeRecord.__table__.columns.keys())

                    existing_source = await session.scalar(
                        select(TradeRecord.source).where(TradeRecord.order_id == order_id)
                    )

                    exclude_from_update = set()

                    def is_unknownish(s):
                        return (s or "").lower() in {"", "unknown", "reconciled", "none", "null"}

                    if existing_source is None:
                        # brand new insert; exclude list only affects updates
                        exclude_from_update.add("source")
                    else:
                        if is_unknownish(existing_source) and not is_unknownish(source_final):
                            # allow upgrade: do NOT add 'source' to exclude_from_update
                            pass
                        else:
                            # keep existing non-unknown source immutable
                            exclude_from_update.add("source")

                    # For BUY updates, never touch derived/linkage fields
                    if side == "buy":
                        # never touch linkage/derived fields on update for buys
                        exclude_from_update.update({"remaining_size", "realized_profit", "pnl_usd", "parent_id", "parent_ids"})

                    # 2) Filter insert dict to known columns only
                    insert_values = {k: v for k, v in trade_dict.items() if k in table_cols}

                    # 3) Build insert
                    insert_stmt = pg_insert(TradeRecord).values(**insert_values)

                    # 4) Build update set from known columns only, excluding immutable ones
                    update_cols = {
                        k: getattr(insert_stmt.excluded, k)
                        for k in insert_values.keys()
                        if k not in exclude_from_update
                    }

                    # 5) Final upsert
                    update_stmt = insert_stmt.on_conflict_do_update(
                        index_elements=["order_id"],
                        set_=update_cols,
                    )
                    await session.execute(update_stmt)

                    # -----------------------------
                    # Update Parent BUYs (remaining_size + realized_profit)
                    # -----------------------------
                    # ✅ DISABLED: FIFO engine will manage remaining_size updates
                    # No inline updates needed since we're not computing FIFO here anymore
                    if update_instructions:
                        self.logger.debug(
                            f"[FIFO] Skipping inline parent updates - FIFO engine will handle. "
                            f"Would have updated {len(update_instructions)} parent records."
                        )

                self.logger.info(
                    f"✅ Trade recorded: {symbol} {side.upper()} {amount}@{price} | "
                    f"PnL: {pnl_usd} | Parents: {parent_ids}"
                )
            self.active_session = None

        except asyncio.CancelledError:
            self.active_session = None
            self.logger.info("🛑 record_trade cancelled cleanly.")
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
            cost_basis_usd: Optional[Decimal] = None,
            preferred_parent_id: Optional[str] = None,
            *,
            sell_fills: Optional[list[dict]] = None,
            gross_override: Optional[Decimal] = None,
            sell_fee_total_override: Optional[Decimal] = None,
    ) -> dict:
        """
        FIFO cost-basis & proceeds for a SELL (fee-inclusive).

        Returns:
          {
            "cost_basis_usd": Decimal,
            "sale_proceeds_usd": Decimal,
            "net_sale_proceeds_usd": Decimal,
            "pnl_usd": Decimal,
            "parent_ids": list[str],
            "update_instructions": [{"order_id": str, "remaining_size": float}, ...]
          }
        """

        def q_usd(x: Decimal) -> Decimal:
            return self.shared_utils_precision.safe_quantize(x, quote_q)

        def q_base(x: Decimal) -> Decimal:
            return self.shared_utils_precision.safe_quantize(x, base_q)

        try:
            session = self.active_session
            if not session:
                raise RuntimeError("No active DB session. Must be called inside record_trade().")

            # ---------- 1) Proceeds (prefer overrides, then fills, then default) ----------
            if gross_override is not None or sell_fee_total_override is not None:
                gross = Decimal(str(gross_override)) if gross_override is not None else (size * sell_price)
                sell_fee_total = (
                    Decimal(str(sell_fee_total_override))
                    if sell_fee_total_override is not None
                    else (total_fees or Decimal("0"))
                )
            elif sell_fills:
                gross = sum(Decimal(str(f["qty"])) * Decimal(str(f["price"])) for f in sell_fills)
                sell_fee_total = sum(Decimal(str(f.get("fee_usd", "0"))) for f in sell_fills)
            else:
                gross = size * sell_price
                sell_fee_total = total_fees or Decimal("0")

            gross = q_usd(gross)
            sell_fee_total = q_usd(sell_fee_total)
            if size <= 0:
                self.logger.warning(f"[FIFO] Non-positive sell size for {symbol}: {size}")
                return self._empty_result()

            # ---------- 2) Eligible BUY parents (FIFO) ----------
            from sqlalchemy import select, or_, not_, func
            from datetime import timedelta
            tolerance = timedelta(seconds=1)

            eligible_q = (
                select(TradeRecord)
                .where(
                    TradeRecord.symbol == symbol,
                    TradeRecord.side == "buy",
                    TradeRecord.order_time <= (sell_time + tolerance),
                    func.coalesce(TradeRecord.remaining_size, 0) > 0,  # NULL treated as 0 (exclude)
                    not_(TradeRecord.order_id.like('%-FILL-%')),
                    not_(TradeRecord.order_id.like('%-FALLBACK%')),
                )
                .order_by(TradeRecord.order_time.asc(), TradeRecord.order_id.asc())
            )
            parents = list((await session.execute(eligible_q)).scalars().all())

            # Prefer (do not restrict to) a hinted parent
            if preferred_parent_id:
                parents.sort(key=lambda p: 0 if p.order_id == preferred_parent_id else 1)

            if not parents:
                self.logger.warning(f"[FIFO] No eligible BUY parents for {symbol} <= {sell_time}")
                return self._empty_result()

            # ---------- 3) Allocate across parents until fully covered ----------
            need = Decimal(str(size))
            allocated = Decimal("0")
            parent_ids: list[str] = []
            update_instructions: list[dict] = []
            total_cost_basis = Decimal("0")

            # optional tracking/sanity (not required, but handy)
            gross_alloc_sum = Decimal("0")
            sell_fee_alloc_sum = Decimal("0")

            for p in parents:
                rem = Decimal(str(p.remaining_size or 0))
                if rem <= 0 or need <= 0:
                    continue

                take = rem if rem <= need else need
                if take <= 0:
                    continue

                parent_size = Decimal(str(p.size or 0))
                buy_price = Decimal(str(p.price or 0))
                buy_fees = Decimal(str(p.total_fees_usd or 0))

                # Fee-inclusive total buy cost for the parent
                parent_total_cost = (buy_price * parent_size) + buy_fees

                # Pro-rate cost by *original* size (not remaining)
                ratio = (take / parent_size) if parent_size > 0 else Decimal("0")
                cost_alloc = parent_total_cost * ratio

                # Pro-rate proceeds/fees for this slice
                gross_alloc = gross * (take / size)
                fee_alloc = sell_fee_total * (take / size)
                net_alloc = gross_alloc - fee_alloc

                realized_profit_delta = net_alloc - cost_alloc
                total_cost_basis += cost_alloc
                gross_alloc_sum += gross_alloc
                sell_fee_alloc_sum += fee_alloc
                allocated += take
                need -= take

                parent_ids.append(p.order_id)

                new_rem = rem - take
                if new_rem < 0:
                    new_rem = Decimal("0")
                update_instructions.append({
                    "order_id": p.order_id,
                    "remaining_size": float(q_base(new_rem)),
                    "realized_profit_delta": float(q_usd(realized_profit_delta)),
                })

                if need <= 0:
                    break

            # HARD GUARD: Do not finalize partial basis vs full gross
            if need > 0:
                self.logger.warning(
                    f"[FIFO] SELL not fully covered for {symbol} at {sell_time}. "
                    f"allocated={allocated} < size={size} (need={need}). Deferring."
                )
                return self._empty_result()

            # ---------- 4) Finalize ----------
            sale_proceeds_usd = q_usd(gross)
            net_sale_proceeds_usd = q_usd(gross - sell_fee_total)
            cost_basis_usd = q_usd(total_cost_basis)
            pnl_usd = q_usd(net_sale_proceeds_usd - cost_basis_usd)

            return {
                "cost_basis_usd": cost_basis_usd,
                "sale_proceeds_usd": sale_proceeds_usd,
                "net_sale_proceeds_usd": net_sale_proceeds_usd,
                "pnl_usd": pnl_usd,
                "parent_ids": parent_ids,
                "update_instructions": update_instructions,
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

    async def fetch_active_trades_for_symbol(self, symbol: str):
        """
        Fetch only active trades for a specific symbol (optimized for TP/SL calculations).

        This method replaces the inefficient fetch_all_trades() pattern that was causing
        database timeouts by fetching ALL 7000+ trades and filtering in Python.

        Args:
            symbol: Trading pair (e.g., "BTC-USD")

        Returns:
            List of TradeRecord objects with remaining_size > 0 for the specified symbol
        """
        try:
            async with self.db_session_manager.async_session() as session:
                async with session.begin():
                    result = await session.execute(
                        select(TradeRecord)
                        .where(TradeRecord.symbol == symbol)
                        .where(TradeRecord.remaining_size > 0)
                    )
                    return result.scalars().all()
        except Exception as e:
            if self.logger:
                self.logger.error(f"❌ Error fetching active trades for {symbol}: {e}", exc_info=True)
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
        """
        Repair SELL trades that are missing parent linkage and/or PnL by delegating to the
        authoritative FIFO recompute per symbol. This avoids partial, out-of-order allocations
        and prevents stomping on already-correct rows.
        """
        self.logger.info("🔧 Running fix_unlinked_sells()...")
        try:
            # 1) Find symbols that actually need fixing (unlinked sells or missing PnL),
            #    but DO NOT include sells that are already linked – we won't overwrite those.
            symbols_to_fix: set[str] = set()

            async with self.db_session_manager.async_session() as session:
                async with session.begin():
                    self.active_session = session

                    rows = await session.execute(
                        text(f"""
                            SELECT DISTINCT symbol
                            FROM {TradeRecord.__tablename__}
                            WHERE side = 'sell'
                              AND source != 'reconciled'
                              AND (
                                    -- unlinked sells
                                    parent_id IS NULL
                                    OR parent_ids IS NULL
                                    -- or sells that never had PnL computed
                                    OR pnl_usd IS NULL
                                  )
                        """)
                    )
                    symbols_to_fix |= {r[0] for r in rows.all()}

            if not symbols_to_fix:
                self.logger.info("✅ No unmatched SELL trades found.")
                return

            # 2) Run authoritative recompute per symbol (does full, chronological replay).
            #    This ensures consistent, FIFO-correct parent linkage & PnL.
            fixed = 0
            for symbol in sorted(symbols_to_fix):
                try:
                    await recompute_fifo_for_symbol(self.shared_data_manager, symbol)
                    fixed += 1
                    self.logger.info(f"✅ FIFO recompute completed for {symbol}")
                except Exception:
                    self.logger.exception(f"❌ FIFO recompute failed for {symbol}")

            self.logger.info(f"✅ fix_unlinked_sells(): repaired {fixed} symbols "
                             f"({len(symbols_to_fix)} targeted).")

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

                    # Validate base_deci to prevent quantize() errors
                    if base_deci is None or not isinstance(base_deci, int) or base_deci < 0:
                        self.logger.warning(f"⚠️ Invalid base_deci for {symbol}: {base_deci}, cannot quantize")
                        return None

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
                                try:
                                    filled_size_str = order.get("filled_size", "0")
                                    base_size = Decimal(str(filled_size_str))

                                    # Validate base_size before quantizing
                                    if not base_size.is_finite():
                                        self.logger.warning(f"⚠️ Invalid base_size for {symbol}: {base_size}, skipping")
                                        continue

                                    # Re-validate base_deci (defensive programming)
                                    if base_deci is None or not isinstance(base_deci, int) or base_deci < 0:
                                        self.logger.warning(f"⚠️ Invalid base_deci for {symbol}: {base_deci}, returning unquantized")
                                        return base_size

                                    return base_size.quantize(Decimal(f'1e-{base_deci}'), rounding=ROUND_DOWN)
                                except (ValueError, ArithmeticError, InvalidOperation) as decimal_err:
                                    self.logger.warning(
                                        f"⚠️ Decimal error quantizing filled_size for {symbol}: "
                                        f"filled_size={filled_size_str}, base_deci={base_deci}, error={decimal_err}"
                                    )
                                    continue

                except Exception as e:
                    self.logger.error(f"❌ Error in find_latest_filled_size for {symbol}: {e}", exc_info=True)
                return None

    async def backfill_trade_metrics(self):
        """
        DEPRECATED: This method used inline FIFO computation which caused dual-system conflicts.
        Use the FIFO engine (scripts/compute_allocations.py) instead.

        This method is kept for backwards compatibility but does nothing.
        """
        logger = self.logger
        logger.warning(
            "⚠️ backfill_trade_metrics() is deprecated. "
            "Use scripts/compute_allocations.py instead to populate fifo_allocations table. "
            "Inline FIFO computation has been disabled to prevent dual-system conflicts."
        )
        return

    async def _calculate_fifo_pnl(self, sell_trade: TradeRecord, session):
        try:
            if sell_trade.source == "reconciled":
                self.logger.info(f"🛡️ Skipping PnL calc for reconciled trade {sell_trade.order_id}")
                return

            size = decimal.Decimal(str(sell_trade.size or 0))
            price = decimal.Decimal(str(sell_trade.price or 0))
            total_fees = decimal.Decimal(str(sell_trade.total_fees_usd or 0))

            base_deci, quote_deci, *_ = self.shared_utils_precision.fetch_precision(sell_trade.symbol)
            base_q = decimal.Decimal("1").scaleb(-base_deci)
            quote_q = decimal.Decimal("1").scaleb(-quote_deci)

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
                if not fifo_result["parent_ids"]:
                    self.logger.warning(f"[FIFO] No parents for SELL {sell_trade.order_id}; deferring PnL.")
                    return
                parent = await session.get(TradeRecord, instr["order_id"])
                if not parent:
                    continue

                # Always update remaining_size when provided
                if "remaining_size" in instr and instr["remaining_size"] is not None:
                    parent.remaining_size = instr["remaining_size"]

                # Support either absolute realized_profit or a delta
                if "realized_profit" in instr:
                    parent.realized_profit = instr["realized_profit"]
                elif "realized_profit_delta" in instr:
                    base = Decimal(str(parent.realized_profit or 0))
                    parent.realized_profit = float(base + Decimal(str(instr["realized_profit_delta"])))

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

