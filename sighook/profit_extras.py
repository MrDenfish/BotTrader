import logging
from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker
from decimal import Decimal, ROUND_DOWN
import datetime
import traceback


class PerformanceManager:
    def __init__(self, exchange, ccxt_api, utility, profit_helper, order_manager, portfolio_manager, database_session_mngr,
                 logmanager, config):

        self.exchange = exchange
        self.ccxt_exceptions = ccxt_api
        self._take_profit = Decimal(config.take_profit)
        self._stop_loss = Decimal(config.stop_loss)
        self.database_dir = config.database_dir
        self.sqlite_db_path = config.sqlite_db_path
        self.ledger_cache = None
        self.utility = utility
        self.database_manager = database_session_mngr
        self.order_manager = order_manager
        self.portfolio_manager = portfolio_manager
        self.profit_helper = profit_helper
        self.log_manager = logmanager
        self.ticker_cache = None
        self.session = None
        self.market_cache = None
        self.start_time = None
        self.web_url = None
        self.holdings = None


    @property
    def stop_loss(self):
        return self._stop_loss

    @property
    def take_profit(self):
        return self._take_profit

    def set_trade_parameters(self, start_time, ticker_cache, market_cache, web_url, hist_holdings):
        self.start_time = start_time
        # self.session = session
        self.ticker_cache = ticker_cache
        self.market_cache = market_cache
        self.web_url = web_url
        self.holdings = hist_holdings

    @staticmethod
    def create_performance_snapshot(session, current_market_prices):
        """"takes a high-level view of the portfolio's performance, aggregating realized and unrealized profits to create
        periodic snapshots."""
        total_realized_profit = session.query(func.sum(ProfitData.total_realized_profit)).scalar() or 0
        total_unrealized_profit = session.query(func.sum(ProfitData.total_unrealized_profit)).scalar() or 0
        portfolio_value = sum(
            holding.quantity * current_market_prices[holding.symbol] for holding in session.query(Holding).all()
        )
        date = datetime.datetime.now()
        snapshot = ProfitData(
            snapshot_date=date,
            total_realized_profit=total_realized_profit,
            total_unrealized_profit=total_unrealized_profit,
            portfolio_value=portfolio_value
        )
        session.add(snapshot)
        session.commit()

    def get_price_symbol(self, product_id):  # async
        try:
            ticker_info = self.ticker_cache.loc[self.ticker_cache['base'] == product_id].iloc[0]
            return Decimal(ticker_info['info']['price']).quantize(Decimal('0.01'), ROUND_DOWN), ticker_info['asset']
        except Exception as e:
            error_details = traceback.format_exc()
            self.log_manager.sighook_logger.error(f'get_current_price_and_symbol: {error_details}')
            logging.error(f"Error getting price/symbol for {product_id}: {e}")
            return None, None
