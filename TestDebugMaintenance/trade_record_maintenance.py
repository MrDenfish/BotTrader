
import asyncio, time
from TableModels.trade_record import TradeRecord
from sqlalchemy import or_, and_, select, func
from sqlalchemy import update, text


# Tune as needed
BATCH_LIMIT = 5000                 # rows per batch
MAX_BATCH_LOOPS = 200              # safety ceiling on total batches
GLOBAL_LOCK_KEY = 0x54524144       # same key you already use (decimal 1414676804)

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
            await session.execute(text("""
                SET LOCAL lock_timeout = '5s';
                SET LOCAL statement_timeout = '60s';
                SET LOCAL idle_in_transaction_session_timeout = '60s';
            """))

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

    # 4) Backfills — bounded & logged
    print("🔁 Running backfill now...")
    await _timed("backfill_trade_metrics", trade_recorder.backfill_trade_metrics(), timeout=300)
    await _timed("fix_unlinked_sells",     trade_recorder.fix_unlinked_sells(),     timeout=300)
    print("✅ Maintenance completed.")




