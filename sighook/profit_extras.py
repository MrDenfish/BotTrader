import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func
from sqlalchemy.future import select
from database_table_models import ProfitData, Holding, Trade
from decimal import Decimal, ROUND_DOWN
import datetime
import traceback


class PerformanceManager:
    def __init__(self, exchange, ccxt_api, utility, order_manager, portfolio_manager, logmanager, config):

        self.exchange = exchange
        self.ccxt_exceptions = ccxt_api
        self._take_profit = Decimal(config.take_profit)
        self._stop_loss = Decimal(config.stop_loss)
        self.database_dir = config.get_database_dir
        self.ledger_cache = None
        self.utility = utility
        self.order_manager = order_manager
        self.portfolio_manager = portfolio_manager
        self.log_manager = logmanager
        self.ticker_cache = None
        self.session = None
        self.current_prices = None
        self.start_time = None
        self.web_url = None
        self.holdings = None

    def set_trade_parameters(self, start_time, ticker_cache, current_prices):
        self.start_time = start_time
        self.ticker_cache = ticker_cache
        self.current_prices = current_prices  # current market prices

    @property
    def stop_loss(self):
        return self._stop_loss

    @property
    def take_profit(self):
        return self._take_profit

    async def performance_snapshot(self, session: AsyncSession):
        try:
            total_realized_profit = await self.calculate_realized_gains(session)
            total_unrealized_profit = await self.calculate_unrealized_gains(session)
            portfolio_value_query = select(func.sum(Holding.market_value))
            portfolio_value_result = await session.execute(portfolio_value_query)
            portfolio_value = portfolio_value_result.scalar() or 0

            profit_data = {
                'realized profit': total_realized_profit,
                'unrealized profit': total_unrealized_profit,
                'portfolio value': portfolio_value
            }

            snapshot = ProfitData(
                snapshot_date=datetime.datetime.now(),
                total_realized_profit=total_realized_profit,
                total_unrealized_profit=total_unrealized_profit,
                portfolio_value=portfolio_value
            )
            session.add(snapshot)
            formatted_data = {k: f"{v:.2f}" if k == 'unrealized profit' else f"{v:.2f}" for k, v in profit_data.items()}
            return formatted_data
        except Exception as e:
            await session.rollback()
            print(f"Exception in performance_snapshot: {e}")
            raise

    @staticmethod
    async def calculate_realized_gains(session: AsyncSession):
        realized_gains_query = select(
            func.sum(Trade.total))
        realized_gains_result = await session.execute(realized_gains_query)
        gains = realized_gains_result.scalar() or 0
        return gains

    @staticmethod
    async def calculate_unrealized_gains(session: AsyncSession):
        unrealized_gains_query = select(
            func.sum(Holding.unrealized_profit_loss)
        )
        unrealized_gains_result = await session.execute(unrealized_gains_query)
        gains = unrealized_gains_result.scalar() or 0
        return gains

    def get_price_symbol(self, product_id):  # async
        try:
            ticker_info = self.ticker_cache.loc[self.ticker_cache['base'] == product_id].iloc[0]
            return Decimal(ticker_info['info']['price']).quantize(Decimal('0.01'), ROUND_DOWN), ticker_info['asset']
        except Exception as e:
            error_details = traceback.format_exc()
            self.log_manager.error(f'get_current_price_and_symbol: {error_details}')
            logging.error(f"Error getting price/symbol for {product_id}: {e}")
            return None, None
