

async def run_maintenance_if_needed(shared_data_manager, trade_recorder):
    """
    Runs the maintenance cleanup + backfill ONLY if incomplete trades are detected.
    """
    # Check for incomplete trades
    async with shared_data_manager.database_session_manager.async_session_factory() as session:
        async with session.begin():
            from TableModels.trade_record import TradeRecord
            from sqlalchemy import or_, select

            result = await session.execute(
                select(TradeRecord)
                .where(
                    or_(
                        TradeRecord.side == "sell",
                        TradeRecord.side == "buy"
                    )
                )
                .where(
                    or_(
                        TradeRecord.parent_id.is_(None),
                        TradeRecord.parent_ids.is_(None),
                        TradeRecord.remaining_size.is_(None)
                    )
                )
                .limit(1)
            )
            incomplete_trade = result.scalar_one_or_none()

    if not incomplete_trade:
        print("✅ No maintenance needed — all trades are complete.")
        return

    print("⚠️ Incomplete trades detected — running maintenance now...")

    from sqlalchemy import update

    # --- BUY trades ---
    async with shared_data_manager.database_session_manager.async_session_factory() as session:
        async with session.begin():
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

            # --- SELL trades ---
            sell_reset_stmt = (
                update(TradeRecord)
                .where(TradeRecord.side == "sell")
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
            print(f"    → Reset {result.rowcount} SELL trades for backfill.")

    print("✅ Running backfill now...")
    await trade_recorder.backfill_trade_metrics()
    print("✅ Maintenance completed.")

