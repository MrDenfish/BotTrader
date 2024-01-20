
import os
import time
from decimal import Decimal

from api_wrapper import APIWrapper

from market_manager import MarketManager

from config_manager import AppConfig

from logging_manager import LoggerManager

from utility import SenderUtils

from msgs import TradeBotComs

from account_profit import ProfitabilityManager

from order_manager import OrderManager

from trading_strategy import TradingStrategy

from webhook import SenderWebhook

from alert_system import AlertSystem

from alive_progress import alive_bar

import ccxt as ccxt

import pandas as pd

from api_wrapper import PortfolioManager, TickerManager

from custom_exceptions import ApiExceptions, CustomExceptions, UnauthorizedError
"""Program will create buy sell signals and submit those signals as webhook messages to the receiver program"""


class TradeBot:
    _instance_count = 0

    def __init__(self, appconfig):
        # self.id = TradeBot._instance_count
        # TradeBot._instance_count += 1
        # print(f"TradeBot Instance ID: {self.id}")
        self.app_config = appconfig  # Instance of AppConfig
        self.log_manager, self.alerts, self.ccxt_exceptions, self.api401, self.custom_excep = None, None, None, None, None
        self.coms, self.utility, self.ticker_manager, self.portfolio_manager, self.webhook = None, None, None, None, None
        self.trading_strategy, self.order_manager, self.api_wrapper, self.market_manager = None, None, None, None
        self.profit_manager, self.exchange_class, self.exchange = None, None, None
        self.log_dir = app_config.log_dir
        self.active_trade_dir = app_config.active_trade_dir
        self.portfolio_trade_dir = app_config.portfolio_dir
        self.profit_dir = app_config.profit_dir
        self.old_portfolio = []
        self.web_url = app_config.web_url
        self.setup_exchange()
        self.load_bot_components()

    def setup_exchange(self):
        self.exchange_class = getattr(ccxt, 'coinbase')
        self.exchange = self.exchange_class({
            'apiKey': self.app_config.api_key,
            'secret': self.app_config.api_secret,
            'enableRateLimit': True,
            'verbose': False
        })

    def load_bot_components(self):
        self.log_manager = LoggerManager(log_dir=self.log_dir)
        self.alerts = AlertSystem(self.log_manager)
        self.coms = TradeBotComs(self.log_manager)
        self.api401 = UnauthorizedError(self.log_manager,self.coms)
        self.ccxt_exceptions = ApiExceptions(self.log_manager, self.alerts)
        self.custom_excep = CustomExceptions(self.log_manager, self.alerts)
        self.utility = SenderUtils(self.log_manager, self.exchange, self.ccxt_exceptions)
        self.ticker_manager = TickerManager(self.utility, self.log_manager,  self.exchange, self.ccxt_exceptions)
        self.portfolio_manager = PortfolioManager(self.utility, self.log_manager, self.ccxt_exceptions, self.exchange)
        self.webhook = SenderWebhook(self.exchange, self.utility, self.log_manager)

        self.trading_strategy = TradingStrategy(self.webhook, self.utility, self.coms,
                                                self.log_manager, self.ccxt_exceptions)
        self.order_manager = OrderManager(self.trading_strategy, self.exchange, self.webhook, self.utility, self.coms,
                                          self.log_manager, self.ccxt_exceptions)
        self.api_wrapper = APIWrapper(self.exchange, self.utility, self.portfolio_manager, self.ticker_manager,
                                      self.order_manager)
        self.market_manager = MarketManager(self.api_wrapper, self.trading_strategy, self.log_manager, self.ticker_manager)

        self.profit_manager = ProfitabilityManager(self.api_wrapper, self.utility, self.order_manager,
                                                   self.portfolio_manager, self.log_manager)

    def main(self, port):
        profit_data = pd.DataFrame(columns=['symbol', 'profit'])
        ticker_cache = pd.DataFrame()  # Holds all USD pairs
        current_holdings = pd.DataFrame()  # Holds all coins ever traded
        start_time = None  # Tracks start time of each trading cycle
        last_ticker_update = None  # Tracks last time ticker data was updated
        ledger = pd.DataFrame()  # Holds all trades and shows profitability of all trades
        web_url = self.web_url
        try:
            while True:
                print(f'          '
                      f'Sighook {self.app_config.program_version}, Port: {port}    '
                      f'Updating ticker cache...', end='\r')
                start_time = self.utility.print_elapsed_time(start_time, 'main')
                ticker_cache = self.ticker_manager.update_ticker_cache()
                self.ticker_manager.set_trade_parameters(start_time, ticker_cache, current_holdings)
                self.portfolio_manager.set_trade_parameters(start_time, ticker_cache, current_holdings)
                self.api_wrapper.set_trade_parameters(start_time, ticker_cache, web_url, current_holdings)
                self.trading_strategy.set_trade_parameters(start_time, ticker_cache, current_holdings)
                self.order_manager.set_trade_parameters(start_time, ticker_cache, web_url, current_holdings)
                self.profit_manager.set_trade_parameters(start_time, ticker_cache, web_url, current_holdings)
                self.market_manager.set_trade_parameters(start_time, ticker_cache, current_holdings)
                self.webhook.set_trade_parameters(start_time, ticker_cache, web_url, current_holdings)
                last_ticker_update = self.ticker_manager.last_ticker_update

                (balance, old_portfolio, usd_coins, avg_vol_total, high_total_vol, price_change) = \
                    (self.api_wrapper.get_portfolio_data(start_time, self.old_portfolio))
                current_holdings = pd.DataFrame(old_portfolio)
                # self.execute_trading_cycle( &profit_data, & ob, & ticker_cache, & ledger)
                # trading strategy
                if self.ticker_manager.ticker_cache is not None and not self.ticker_manager.ticker_cache.empty:
                    open_orders, results_df, bollinger_df = (self.market_manager.fetch_ohlcv(old_portfolio, usd_coins,
                                                                                             avg_vol_total, high_total_vol))
                # check filled orders for profitability
                profit_data = self.profit_manager.check_profit_level(profit_data, old_portfolio)
                if len(profit_data) > 0:
                    activetrade_data_path = os.path.join(self.active_trade_dir, 'activetrade_data.csv')
                    profit_data.to_csv(activetrade_data_path, index=False)
                    print(f"Profit Data:\n{profit_data.to_string(index=False)}")

                ledger, profitability = self.profit_manager.calculate_profits(start_time, self.portfolio_trade_dir)
                if isinstance(ledger, pd.DataFrame):
                    portfolio_data_path = os.path.join(self.portfolio_trade_dir, 'portfolio_data.csv')
                    ledger.to_csv(portfolio_data_path, index=False)
                if isinstance(profitability, pd.DataFrame):
                    profit_data_path = os.path.join(self.profit_dir, 'profit_data.csv')
                    profitability.to_csv(profit_data_path, index=False)
                intro_text = f"High Volume Crypto with volume greater than {min(avg_vol_total, Decimal(1000000))}:"
                print(intro_text)
                print(high_total_vol.to_string(index=False))
                # print(f"Bollinger results:\n{bollinger_df.to_string(index=False)}")
                self.sleep_with_progress_bar()
        except KeyboardInterrupt:
            self.save_data_on_exit(profit_data, ledger)

    def save_data_on_exit(self, profit_data, ledger):
        pass
    # Logic to save data when the program exits unexpectedly

    @staticmethod
    def sleep_with_progress_bar():
        total_sleep = 180
        update_interval = 1
        num_iterations = total_sleep // update_interval
        with alive_bar(num_iterations, force_tty=True) as bar:
            for _ in range(num_iterations):
                time.sleep(update_interval)
                bar()


if __name__ == "__main__":
    print("Debugging - MACHINE_TYPE:", os.getenv('MACHINE_TYPE'))
    app_config = AppConfig()  # Create an instance of AppConfig

    # Set display options for pandas
    pd.set_option('display.max_columns', None)
    pd.set_option('display.width', None)
    pd.set_option('display.max_colwidth', None)
    pd.set_option('display.colheader_justify', 'center')
    bot = TradeBot(app_config)

    bot.main(app_config.port)
