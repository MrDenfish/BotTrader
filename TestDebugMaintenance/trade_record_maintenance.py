from TableModels.trade_record import TradeRecord
from sqlalchemy import or_, and_, select, func
from sqlalchemy import update

async def run_maintenance_if_needed(shared_data_manager, trade_recorder):
    """
    Runs the maintenance cleanup + backfill if incomplete trades are found OR if the table is empty.

    Fix or complete trade records that already exist:
        - Sell trades missing PnL/parent references
        - Buy trades missing remaining_size
        - Rows with None fields due to WebSocket dropouts or recon reconciliation
    """
    print("🔎 Checking for trade maintenance requirements...")

    async with shared_data_manager.database_session_manager.async_session() as session:
        async with session.begin():
            # Check if any trades exist
            total_count_result = await session.execute(select(func.count()).select_from(TradeRecord))
            total_trades = total_count_result.scalar_one()

            if total_trades == 0:
                print("⚠️ No trades found in database. Running maintenance anyway.")
            else:
                # Check for incomplete fields
                result = await session.execute(
                    select(TradeRecord).where(
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
                            )
                        )
                    ).limit(1)
                )
                incomplete_trade = result.scalar_one_or_none()

                if not incomplete_trade:
                    print("✅ No maintenance needed — all trades are complete.")
                    return

    # Run the SQL cleanup patches and backfill
    print("⚙️ Incomplete or missing trades detected — applying fixes...")

    async with shared_data_manager.database_session_manager.async_session() as session:
        async with session.begin():
            # BUY Fixes
            buy_fix_stmt = (
                update(TradeRecord)
                .where(TradeRecord.side == "buy")
                .where(
                    or_(
                        TradeRecord.parent_id.is_(None),
                        TradeRecord.parent_ids.is_(None)
                    )
                )
                .values({
                    TradeRecord.parent_id: TradeRecord.order_id,
                    TradeRecord.parent_ids: None
                })
            )
            result = await session.execute(buy_fix_stmt)
            print(f"    → Fixed parent fields for {result.rowcount} BUY trades.")

            remaining_fix_stmt = (
                update(TradeRecord)
                .where(TradeRecord.side == "buy")
                .where(
                    or_(
                        TradeRecord.remaining_size.is_(None),
                        TradeRecord.remaining_size == 0
                    )
                )
                .values({TradeRecord.remaining_size: TradeRecord.size})
            )
            result = await session.execute(remaining_fix_stmt)
            print(f"    → Fixed remaining_size for {result.rowcount} BUY trades.")

            # SELL Fixes — reset only if missing key fields
            sell_reset_stmt = (
                update(TradeRecord)
                .where(
                    TradeRecord.side == "sell",
                    or_(
                        TradeRecord.cost_basis_usd.is_(None),
                        TradeRecord.sale_proceeds_usd.is_(None),
                        TradeRecord.net_sale_proceeds_usd.is_(None),
                        TradeRecord.pnl_usd.is_(None),
                        TradeRecord.realized_profit.is_(None),
                        TradeRecord.parent_ids.is_(None),
                        TradeRecord.parent_id.is_(None)
                    )
                )
                .values({
                    TradeRecord.cost_basis_usd: None,
                    TradeRecord.sale_proceeds_usd: None,
                    TradeRecord.net_sale_proceeds_usd: None,
                    TradeRecord.pnl_usd: None,
                    TradeRecord.realized_profit: None,
                    TradeRecord.parent_ids: None,
                    TradeRecord.parent_id: None
                })
            )
            result = await session.execute(sell_reset_stmt)
            print(f"    → Reset {result.rowcount} incomplete SELL trades for backfill.")

    # Run the backfill processes
    print("🔁 Running backfill now...")
    await trade_recorder.backfill_trade_metrics()
    await trade_recorder.fix_unlinked_sells()
    print("✅ Maintenance completed.")



