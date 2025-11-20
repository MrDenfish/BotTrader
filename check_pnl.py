import asyncio
import os
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from sqlalchemy import select, and_
from dotenv import load_dotenv

load_dotenv()

from database_manager.db_session_manager import DatabaseSessionManager
from TableModels.trade_record import TradeRecord

async def quick_check():
    db_host = os.getenv('DB_HOST', '127.0.0.1')
    db_port = os.getenv('DB_PORT', '5432')
    db_name = os.getenv('DB_NAME', 'bot_trader_db')
    db_user = os.getenv('DB_USER', 'bot_user')
    db_password = os.getenv('DB_PASSWORD')

    database_url = f'postgresql+asyncpg://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}'
    db_manager = DatabaseSessionManager(database_url)

    cutoff = datetime.now(timezone.utc) - timedelta(hours=8)

    try:
        async with db_manager.async_session() as session:
            async with session.begin():
                for symbol in ['DASH-USD', 'ZEC-USD']:
                    print(f'\n{"="*80}')
                    print(f'{symbol} - Last 8 Hours')
                    print(f'{"="*80}\n')

                    stmt = select(TradeRecord).where(
                        and_(
                            TradeRecord.symbol == symbol,
                            TradeRecord.order_time >= cutoff
                        )
                    ).order_by(TradeRecord.order_time.asc())

                    result = await session.execute(stmt)
                    trades = result.scalars().all()

                    buys = [t for t in trades if t.side == 'buy']
                    sells = [t for t in trades if t.side == 'sell']

                    print(f'BUY ORDERS ({len(buys)}):')
                    print(f'{"─"*80}')
                    total_buy_cost = Decimal('0')
                    for b in buys:
                        price = Decimal(str(b.price))
                        size = Decimal(str(b.size))
                        fees = Decimal(str(b.total_fees_usd or 0))
                        cost = (price * size) + fees
                        total_buy_cost += cost
                        print(f'{b.order_id[:30]:30} ${price:8.2f} x {size:8.4f} + ${fees:6.2f} fee = ${cost:10.2f}')

                    print(f'\nSELL ORDERS ({len(sells)}):')
                    print(f'{"─"*80}')
                    total_sell_gross = Decimal('0')
                    total_sell_fees = Decimal('0')
                    total_db_pnl = Decimal('0')

                    for s in sells:
                        price = Decimal(str(s.price))
                        size = Decimal(str(s.size))
                        fees = Decimal(str(s.total_fees_usd or 0))
                        gross = price * size
                        pnl = Decimal(str(s.pnl_usd or 0))
                        total_sell_gross += gross
                        total_sell_fees += fees
                        total_db_pnl += pnl
                        print(f'{s.order_id[:30]:30} ${price:8.2f} x {size:8.4f} - ${fees:6.2f} fee | DB PnL: ${pnl:8.2f}')

                    print(f'\n{"="*80}')
                    print('SUMMARY:')
                    print(f'{"="*80}')
                    net_proceeds = total_sell_gross - total_sell_fees
                    expected_pnl = net_proceeds - total_buy_cost

                    print(f'Total BUY Cost (price*size + fees):  ${total_buy_cost:10.2f}')
                    print(f'Total SELL Gross (price*size):       ${total_sell_gross:10.2f}')
                    print(f'Total SELL Fees:                      ${total_sell_fees:10.2f}')
                    print(f'Total SELL Net Proceeds:              ${net_proceeds:10.2f}')
                    print(f'')
                    print(f'Expected PnL (Net - Cost):            ${expected_pnl:10.2f}')
                    print(f'Database PnL (sum pnl_usd):           ${total_db_pnl:10.2f}')
                    print(f'Discrepancy:                          ${total_db_pnl - expected_pnl:10.2f}')

                    if abs(total_db_pnl - expected_pnl) > Decimal('0.01'):
                        print(f'\n⚠️  DISCREPANCY DETECTED!')
                    else:
                        print(f'\n✅ PnL matches')

    finally:
        await db_manager.close()

asyncio.run(quick_check())
