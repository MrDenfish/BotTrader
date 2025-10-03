import os
import asyncio, time
from decimal import Decimal
from typing import List, Set
from datetime import timedelta
from sqlalchemy import update, text
from sqlalchemy import or_, and_, select, func
from TableModels.trade_record import TradeRecord





# Tune as needed
BATCH_LIMIT = 5000                 # rows per batch
MAX_BATCH_LOOPS = 200              # safety ceiling on total batches
GLOBAL_LOCK_KEY = 0x54524144       # same key you already use (decimal 1414676804)


async def normalize_sources(shared_data_manager):
    """Fill in sensible source values without stomping known ones."""
    async with shared_data_manager.database_session_manager.async_session() as session:
        async with session.begin():
            # A) Buys/sells ingested via REST/IMPORT but with unknown source -> 'reconciled'
            await session.execute(text(f"""
                UPDATE {TradeRecord.__tablename__}
                SET source = 'reconciled'
                WHERE (source IS NULL OR lower(source) IN ('', 'unknown'))
                  AND ingest_via IN ('rest','import')
            """))

            # B) Sells with unknownish source but parent has a known source -> inherit it
            await session.execute(text(f"""
                UPDATE {TradeRecord.__tablename__} s
                SET source = b.source
                FROM {TradeRecord.__tablename__} b
                WHERE s.side='sell'
                  AND (s.source IS NULL OR lower(s.source) IN ('', 'unknown', 'reconciled'))
                  AND s.parent_id IS NOT NULL
                  AND b.order_id = s.parent_id
                  AND b.source IS NOT NULL
                  AND lower(b.source) NOT IN ('', 'unknown', 'reconciled')
            """))


# --- FIFO maintenance helpers -----------------------------------------------

async def recompute_fifo_for_symbol(shared_data_manager, symbol: str) -> None:
    """
    Idempotent FIFO rebuild for a single symbol.
    Replays SELLs in chronological order against BUYs.
    Resets/rewrites: buy.remaining_size; sell.parent_id/parent_ids, cost/proceeds/net/pnl.
    """
    logger = getattr(shared_data_manager, "logger", None) or getattr(shared_data_manager, "shared_logger", None)
    logpfx = f"[FIFO:{symbol}]"

    async with shared_data_manager.database_session_manager.async_session() as session:
        async with session.begin():
            buys = (await session.execute(
                select(TradeRecord)
                .where(
                    TradeRecord.symbol == symbol,
                    TradeRecord.side == "buy",
                    TradeRecord.status == "filled",
                )
                .order_by(TradeRecord.order_time.asc(), TradeRecord.order_id.asc())
            )).scalars().all()

            sells = (await session.execute(
                select(TradeRecord)
                .where(
                    TradeRecord.symbol == symbol,
                    TradeRecord.side == "sell",
                    TradeRecord.status == "filled",
                )
                .order_by(TradeRecord.order_time.asc(), TradeRecord.order_id.asc())
            )).scalars().all()

            if not buys and not sells:
                if logger: logger.info(f"{logpfx} No trades; nothing to do.")
                return

            # Reset BUY buckets; clear SELL allocation fields
            for b in buys:
                b.remaining_size = Decimal(str(b.size or 0))

            for s in sells:
                s.parent_id = None
                s.parent_ids = []
                s.cost_basis_usd = None
                s.sale_proceeds_usd = None
                s.net_sale_proceeds_usd = None
                s.pnl_usd = None

            # In-memory FIFO
            fifo = [{
                "order_id": b.order_id,
                "price": Decimal(str(b.price or 0)),
                "size": Decimal(str(b.size or 0)),
                "fees_usd": Decimal(str(b.total_fees_usd or 0)),
                "remaining": Decimal(str(b.size or 0)),
            } for b in buys]

            repaired = 0
            partials = 0

            def q(x: Decimal) -> Decimal:
                # If you have precision helpers, apply them here.
                return x

            for s in sells:
                need = Decimal(str(s.size or 0))
                if need <= 0:
                    continue

                sell_price = Decimal(str(s.price or 0))
                gross = sell_price * need
                sell_fees = Decimal(str(s.total_fees_usd or 0))

                parent_ids: List[str] = []
                total_cost_basis = Decimal("0")
                remaining_to_fill = need

                for bucket in fifo:
                    if remaining_to_fill <= 0:
                        break
                    if bucket["remaining"] <= 0:
                        continue
                    take = bucket["remaining"] if bucket["remaining"] <= remaining_to_fill else remaining_to_fill
                    if take <= 0:
                        continue

                    # Fee-inclusive buy-side cost basis allocation
                    parent_total_cost = (bucket["price"] * bucket["size"]) + bucket["fees_usd"]
                    ratio = (take / bucket["size"]) if bucket["size"] > 0 else Decimal("0")
                    alloc_cost = parent_total_cost * ratio

                    total_cost_basis += alloc_cost
                    bucket["remaining"] -= take
                    remaining_to_fill -= take
                    parent_ids.append(bucket["order_id"])

                if remaining_to_fill > 0:
                    # Not fully covered; leave this SELL unassigned (or log)
                    partials += 1
                    continue

                sale_proceeds_usd = q(gross)
                net_sale_proceeds_usd = q(gross - sell_fees)
                cost_basis_usd = q(total_cost_basis)
                pnl_usd = q(net_sale_proceeds_usd - cost_basis_usd)

                s.parent_ids = parent_ids
                s.parent_id = parent_ids[0] if parent_ids else None
                s.cost_basis_usd = cost_basis_usd
                s.sale_proceeds_usd = sale_proceeds_usd
                s.net_sale_proceeds_usd = net_sale_proceeds_usd
                s.pnl_usd = pnl_usd
                repaired += 1

            if logger:
                logger.info(f"{logpfx} Replayed {repaired} SELLs; partial/uncovered SELLs: {partials}")

async def detect_symbols_needing_fifo_repair(shared_data_manager, limit: int = 100) -> Set[str]:
    """
    Heuristics:
      • SELLs with no parents even though prior BUYs exist (classic out-of-order)
      • BUYs with negative remaining_size
    Returns a set of symbols; capped by `limit` per query.
    """
    symbols: Set[str] = set()
    async with shared_data_manager.database_session_manager.async_session() as session:
        # SELLs without parents but with prior BUYs
        rows1 = (await session.execute(text(f"""
            SELECT DISTINCT s.symbol
            FROM {TradeRecord.__tablename__} s
            WHERE s.side='sell'
              AND (s.parent_id IS NULL AND (s.parent_ids IS NULL OR array_length(s.parent_ids,1)=0))
              AND EXISTS (
                  SELECT 1 FROM {TradeRecord.__tablename__} b
                  WHERE b.symbol = s.symbol
                    AND b.side = 'buy'
                    AND b.status = 'filled'
                    AND b.order_time <= s.order_time
              )
            LIMIT :lim
        """), {"lim": limit})).all()
        symbols |= {r[0] for r in rows1}

        # BUYs with negative remaining_size
        rows2 = (await session.execute(text(f"""
            SELECT DISTINCT symbol
            FROM {TradeRecord.__tablename__}
            WHERE side='buy' AND remaining_size < 0
            LIMIT :lim
        """), {"lim": limit})).all()
        symbols |= {r[0] for r in rows2}

    return symbols
# -------------------------------------------------------------------------------


async def _timed(label, coro, timeout=300):
    t0 = time.monotonic()
    try:
        await asyncio.wait_for(coro, timeout=timeout)
        print(f"✅ {label} finished in {time.monotonic()-t0:.1f}s")
    except asyncio.TimeoutError:
        print(f"⏱ {label} timed out after {timeout}s")
    except Exception as e:
        print(f"❌ {label} failed: {e}")

async def _batch(session, sql, label, max_loops=MAX_BATCH_LOOPS):
    total = 0
    for i in range(max_loops):
        res = await session.execute(text(sql))
        n = res.rowcount or 0
        total += n
        if n == 0:
            break
        print(f"    → {label}: batch {i+1}, updated {n} rows")
    if total == 0:
        print(f"    → {label}: nothing to do")
    else:
        print(f"    → {label}: total updated {total} rows")


async def run_maintenance_if_needed(shared_data_manager, trade_recorder):
    """
    Cleanup + backfill if incomplete trades are found OR if table is empty.
    Robust: non-blocking lock, tx timeouts, batched updates, bounded backfills.
    """
    print("🔎 Checking for trade maintenance requirements...")

    # 0) Quick probe: are there any incomplete rows?
    async with shared_data_manager.database_session_manager.async_session() as session:
        async with session.begin():
            total_trades = (await session.execute(
                select(func.count()).select_from(TradeRecord)
            )).scalar_one()

            if total_trades > 0:
                probe = await session.execute(
                    select(TradeRecord.order_id).where(
                        or_(
                            TradeRecord.parent_id.is_(None),
                            TradeRecord.parent_ids.is_(None),
                            TradeRecord.remaining_size.is_(None),
                            and_(
                                TradeRecord.side == "sell",
                                or_(
                                    TradeRecord.pnl_usd.is_(None),
                                    TradeRecord.cost_basis_usd.is_(None),
                                    TradeRecord.sale_proceeds_usd.is_(None),
                                    TradeRecord.realized_profit.is_(None),
                                    TradeRecord.parent_id.is_(None),
                                    TradeRecord.parent_ids.is_(None),
                                )
                            ),
                        )
                    ).limit(1)
                )
                if probe.scalar_one_or_none() is None:
                    print("✅ No maintenance needed — all trades are complete.")
                    return
            else:
                print("⚠️ No trades found in database. Running maintenance anyway.")

    # 1) Print scope (counts) so we know what work is ahead
    async with shared_data_manager.database_session_manager.async_session() as session:
        async with session.begin():
            buy_missing_parent = (await session.execute(text(f"""
                SELECT count(*) FROM {TradeRecord.__tablename__}
                WHERE side='buy' AND (parent_id IS NULL OR parent_ids IS NULL)
            """))).scalar_one()

            buy_missing_remaining = (await session.execute(text(f"""
                SELECT count(*) FROM {TradeRecord.__tablename__}
                WHERE side='buy' AND (remaining_size IS NULL OR remaining_size = 0)
            """))).scalar_one()

            sell_incomplete = (await session.execute(text(f"""
                SELECT count(*) FROM {TradeRecord.__tablename__}
                WHERE side='sell' AND (
                    cost_basis_usd IS NULL OR sale_proceeds_usd IS NULL OR net_sale_proceeds_usd IS NULL OR
                    pnl_usd IS NULL OR realized_profit IS NULL OR parent_ids IS NULL OR parent_id IS NULL
                )
            """))).scalar_one()

            print(f"   • BUY parent fields missing: {buy_missing_parent}")
            print(f"   • BUY remaining_size missing/zero: {buy_missing_remaining}")
            print(f"   • SELL rows needing reset: {sell_incomplete}")

    # 2) Acquire maintenance lock (non-blocking) + set per-txn timeouts
    print("⚙️ Incomplete or missing trades detected — applying fixes...")
    async with shared_data_manager.database_session_manager.async_session() as session:
        async with session.begin():
            # Safety timeouts so we never hang in a tx
            await session.execute(text("SET LOCAL lock_timeout = '5s'"))
            await session.execute(text("SET LOCAL statement_timeout = '60s'")) # debugging safe to keep on in production
            await session.execute(text("SET LOCAL idle_in_transaction_session_timeout = '60s'"))

            got_lock = (await session.execute(
                text("SELECT pg_try_advisory_lock(:k)"), {"k": GLOBAL_LOCK_KEY}
            )).scalar_one()

            if not got_lock:
                print("⏸ Maintenance lock held elsewhere; will retry later.")
                return

            try:
                # 3) Batched fixes (no full-table scans)
                BUY_PARENT_FIX = f"""
                WITH batch AS (
                  SELECT order_id FROM {TradeRecord.__tablename__}
                  WHERE side='buy' AND (parent_id IS NULL OR parent_ids IS NULL)
                  ORDER BY order_time NULLS LAST
                  LIMIT {BATCH_LIMIT}
                )
                UPDATE {TradeRecord.__tablename__} t
                SET parent_id = t.order_id, parent_ids = NULL
                FROM batch WHERE t.order_id = batch.order_id
                """

                BUY_REMAINING_FIX = f"""
                WITH batch AS (
                  SELECT order_id FROM {TradeRecord.__tablename__}
                  WHERE side='buy' AND (remaining_size IS NULL OR remaining_size = 0)
                  ORDER BY order_time NULLS LAST
                  LIMIT {BATCH_LIMIT}
                )
                UPDATE {TradeRecord.__tablename__} t
                SET remaining_size = t.size
                FROM batch WHERE t.order_id = batch.order_id
                """

                SELL_RESET_FIX = f"""
                WITH batch AS (
                  SELECT order_id FROM {TradeRecord.__tablename__}
                  WHERE side='sell' AND (
                    cost_basis_usd IS NULL OR sale_proceeds_usd IS NULL OR net_sale_proceeds_usd IS NULL OR
                    pnl_usd IS NULL OR realized_profit IS NULL OR parent_ids IS NULL OR parent_id IS NULL
                  )
                  ORDER BY order_time NULLS LAST
                  LIMIT {BATCH_LIMIT}
                )
                UPDATE {TradeRecord.__tablename__} t
                SET cost_basis_usd = NULL,
                    sale_proceeds_usd = NULL,
                    net_sale_proceeds_usd = NULL,
                    pnl_usd = NULL,
                    realized_profit = NULL,
                    parent_ids = NULL,
                    parent_id = NULL
                FROM batch WHERE t.order_id = batch.order_id
                """

                await _batch(session, BUY_PARENT_FIX,    "Fixed parent fields for BUY trades")
                await _batch(session, BUY_REMAINING_FIX, "Fixed remaining_size for BUY trades")
                await _batch(session, SELL_RESET_FIX,    "Reset incomplete SELL trades for backfill")


            finally:
                await session.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": GLOBAL_LOCK_KEY})

        # >>> NEW: Deterministic FIFO rebuild BEFORE backfills <<<

        # 3.5) Recompute FIFO for forced and auto-detected symbols (idempotent, quick exit if none)

    # 4) Backfills — bounded & logged (run these BEFORE recompute)
    print("🔁 Running backfill now...")
    await _timed("backfill_trade_metrics", trade_recorder.backfill_trade_metrics(), timeout=300)
    await _timed("fix_unlinked_sells", trade_recorder.fix_unlinked_sells(), timeout=300)

    # 5) FINAL: Deterministic FIFO rebuild — the LAST writer wins
    logger = getattr(shared_data_manager, "logger", None) or getattr(shared_data_manager, "shared_logger", None)
    force = os.getenv("FIFO_FORCE_SYMBOLS", "") or ""
    forced_symbols = {s.strip() for s in force.replace(" ", ",").split(",") if s.strip()}

    # Re-detect *now*, in case backfills changed anything
    detected_symbols = await detect_symbols_needing_fifo_repair(shared_data_manager)

    symbols_to_fix = set(forced_symbols) | set(detected_symbols)
    if symbols_to_fix:
        if logger: logger.info(f"[FIFO] Recompute targets → {sorted(symbols_to_fix)}")
        for sym in sorted(symbols_to_fix):
            try:
                await recompute_fifo_for_symbol(shared_data_manager, sym)
            except Exception:
                if logger: logger.exception(f"[FIFO] Recompute failed for {sym}")
    else:
        if logger: logger.info("[FIFO] No symbols require recompute.")

    # Optional: quick audit of a canary symbol
    await audit_fifo(trade_recorder, symbol="ELA-USD")
    # Normalize legacy/missing sources last
    await normalize_sources(shared_data_manager)

    print("✅ Maintenance completed.")


ALLOCATION_EPS_BASE = Decimal("1e-8")  # tune based on base precision

async def audit_fifo(trade_recorder, symbol: str = None, since=None, until=None, limit=5000):
    """
    Verifies FIFO integrity without changing data.
    Reports:
      - SELLs whose parent_ids don't cover sell size (under/over allocation)
      - Parents with order_time > sell_time
      - Duplicate parents
      - BUY remaining_size inconsistent with flows (rough check)
    """
    logger = trade_recorder.logger
    async with trade_recorder.db_session_manager.async_session() as session:
        async with session.begin():
            # 1) scope
            q_sells = select(TradeRecord).where(TradeRecord.side == "sell")
            if symbol:
                q_sells = q_sells.where(TradeRecord.symbol == symbol)
            if since:
                q_sells = q_sells.where(TradeRecord.order_time >= since)
            if until:
                q_sells = q_sells.where(TradeRecord.order_time <= until)
            q_sells = q_sells.order_by(TradeRecord.order_time.asc()).limit(limit)

            sells = (await session.execute(q_sells)).scalars().all()

            bad = {
                "under_alloc": [],   # allocated < sell size
                "over_alloc":  [],   # allocated > sell size
                "parent_after_sell": [],
                "dup_parent": [],
            }

            for s in sells:
                sell_time = s.order_time
                sell_size = Decimal(str(s.size or 0))
                pids = list(s.parent_ids or [])  # [] or list of strings
                if not pids:
                    # No parents recorded — definitely broken
                    bad["under_alloc"].append((s.order_id, s.symbol, float(sell_size), "no parents"))
                    continue

                # fetch claimed parent buys
                q_parents = (
                    select(TradeRecord)
                    .where(
                        TradeRecord.order_id.in_(pids),
                        TradeRecord.side == "buy"
                    )
                )
                parents = (await session.execute(q_parents)).scalars().all()
                # map id->buy row
                pmap = {p.order_id: p for p in parents}

                # 2) sanity: all pids present & unique
                if len(pids) != len(set(pids)):
                    bad["dup_parent"].append((s.order_id, s.symbol, pids))
                if any(pid not in pmap for pid in pids):
                    bad["under_alloc"].append((s.order_id, s.symbol, float(sell_size), "missing parent row(s)"))
                    continue

                # 3) sanity: no parent after sell_time
                late = [pid for pid in pids if pmap[pid].order_time and pmap[pid].order_time > sell_time]
                if late:
                    bad["parent_after_sell"].append((s.order_id, s.symbol, late))

                # 4) recompute theoretical allocation amount from parents in time order
                # NOTE: we *only* verify coverage; we don't recalc cost basis here.
                # If you store per-parent allocation, use that; else, we infer max available.
                parents_sorted = sorted((pmap[pid] for pid in pids), key=lambda r: (r.order_time, r.order_id))
                need = sell_size
                allocated = Decimal("0")

                for p in parents_sorted:
                    rem = Decimal(str(p.remaining_size if p.remaining_size is not None else p.size or 0))
                    take = rem if rem <= need else need
                    allocated += take
                    need -= take
                    if need <= 0:
                        break

                # classify
                if (allocated + ALLOCATION_EPS_BASE) < sell_size:
                    bad["under_alloc"].append((s.order_id, s.symbol, float(sell_size), float(allocated)))
                elif allocated > (sell_size + ALLOCATION_EPS_BASE):
                    bad["over_alloc"].append((s.order_id, s.symbol, float(sell_size), float(allocated)))

            # quick printout
            def _print_bucket(title, rows):
                logger.info(f"[AUDIT] {title}: {len(rows)}")
                for r in rows[:20]:
                    logger.info(f"  {r}")

            _print_bucket("SELL under-allocation", bad["under_alloc"])
            _print_bucket("SELL over-allocation",  bad["over_alloc"])
            _print_bucket("Parents after SELL time", bad["parent_after_sell"])
            _print_bucket("Duplicate parents", bad["dup_parent"])

            return bad



