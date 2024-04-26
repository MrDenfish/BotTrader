

# <><><><><><><><><><><><><><><><><> NOT IMPLIMENTED YET <><><><><><><><><><><><><><><><><>

    async def update_partial_fill(self, trade_id, filled_amount):
        async with self.AsyncSession() as session:
            async with session.begin():
                trade = await session.get(Trade, trade_id)
                # trade = session.query(Trade).filter_by(trade_id=trade_id).first()
                if trade:
                    trade.amount = filled_amount
                    session.commit()
                    # Update the corresponding holding
                    await self.update_holding_from_trade(trade)


# <><><><><><><><><><><><><><><><><><><><> ADDED TO NEW DATABASE MANAGER <><><><><><><><><><><><><><><><><><><


