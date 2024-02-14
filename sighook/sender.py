
import aiohttp
import os
import time
from decimal import Decimal
import asyncio
import datetime

import pandas as pd

from api_wrapper import APIWrapper

from market_manager import MarketManager

from config_manager import AppConfig

from logging_manager import LoggerManager

from utility import SenderUtils

from account_profit import ProfitabilityManager

from order_manager import OrderManager

from trading_strategy import TradingStrategy

from alerts_msgs_webhooks import SenderWebhook,  AlertSystem

from alive_progress import alive_bar

import ccxt.async_support as ccxt

from market_metrics import CoinMarketAPI

from portfolio_manager import PortfolioManager

from ticker_manager import TickerManager

from custom_exceptions import ApiExceptions, CustomExceptions, UnauthorizedError
"""Program will create buy sell signals and submit those signals as webhook messages to the receiver program"""

"""Test File"""


class TradeBot:
    _instance_count = 0

    def __init__(self, appconfig):
        # self.id = TradeBot._instance_count
        # TradeBot._instance_count += 1
        # print(f"TradeBot Instance ID: {self.id}")
        self.app_config = appconfig  # Instance of AppConfig
        self._sleep_time = appconfig.sleep_time
        self.log_manager, self.alerts, self.ccxt_exceptions, self.api401, self.custom_excep = None, None, None, None, None
        self.coms, self.utility, self.ticker_manager, self.portfolio_manager, self.webhook = None, None, None, None, None
        self.trading_strategy, self.order_manager, self.api_wrapper, self.market_manager = None, None, None, None
        self.market_metrics, self.profit_manager, self.exchange_class, self.exchange = None, None, None, None
        self.cmc, self.cmc_api_key, self.cmc_url = None, None, None  # coin market cap
        self.log_dir = app_config.log_dir
        self.active_trade_dir = app_config.active_trade_dir
        self.portfolio_trade_dir = app_config.portfolio_dir
        self.profit_dir = app_config.profit_dir
        self.old_portfolio = []
        self.ticker_cache = pd.DataFrame()
        self.market_cache = pd.DataFrame()
        self.buy_sell_matrix = pd.DataFrame()
        self.start_time = None  # Tracks start time of each trading cycle
        self.last_ticker_update = None  # Tracks last time ticker data was updated
        self.web_url = app_config.web_url
        self.setup_exchange()
        self.setup_coinmarketcap()
        self.load_bot_components()

    def setup_exchange(self):
        self.exchange_class = getattr(ccxt, 'coinbase')
        self.exchange = self.exchange_class({
            'apiKey': self.app_config.api_key,
            'secret': self.app_config.api_secret,
            'enableRateLimit': True,
            'verbose': False  # True for debugging
        })

    def setup_coinmarketcap(self):
        self.cmc_api_key = self.app_config.cmc_api_key
        self.cmc_url = self.app_config.cmc_api_url
        self.cmc = CoinMarketAPI(self.cmc_api_key, self.cmc_url, self.log_manager)

    def load_bot_components(self):
        self.log_manager = LoggerManager(log_dir=self.log_dir)
        self.alerts = AlertSystem(self.app_config, self.log_manager)
        self.api401 = UnauthorizedError(self.log_manager, self.coms)
        self.custom_excep = CustomExceptions(self.log_manager, self.alerts)
        self.ccxt_exceptions = ApiExceptions(self.log_manager, self.alerts)
        self.utility = SenderUtils(self.log_manager, self.exchange, self.ccxt_exceptions)
        self.market_metrics = CoinMarketAPI(self.cmc_api_key, self.cmc_url, self.log_manager)
        self.ticker_manager = TickerManager(self.utility, self.log_manager,  self.exchange, self.ccxt_exceptions)
        self.portfolio_manager = PortfolioManager(self.utility, self.log_manager, self.ccxt_exceptions, self.exchange)
        self.webhook = SenderWebhook(self.exchange, self.alerts, self.log_manager, self.app_config)

        self.trading_strategy = TradingStrategy(self.webhook, self.ticker_manager, self.utility, self.alerts, self.exchange,
                                                self.log_manager, self.ccxt_exceptions, self.market_metrics,
                                                self.alerts, self.app_config)
        self.order_manager = OrderManager(self.trading_strategy, self.exchange, self.webhook, self.utility, self.alerts,
                                          self.log_manager, self.ccxt_exceptions, self.app_config)

        self.api_wrapper = APIWrapper(self.exchange, self.utility, self.portfolio_manager, self.ticker_manager,
                                      self.order_manager, self.market_metrics, self.ccxt_exceptions,)
        self.market_manager = MarketManager(self.api_wrapper, self.trading_strategy, self.log_manager,
                                            self.ticker_manager, self.utility)

        self.profit_manager = ProfitabilityManager(self.api_wrapper, self.utility, self.order_manager,
                                                   self.portfolio_manager, self.log_manager, self.app_config)

    @property
    def sleep_time(self):
        return self._sleep_time

    async def main(self, port):

        profit_data = pd.DataFrame(columns=['Symbol', 'Unrealized PCT', 'Profit/Loss', 'Total Cost', 'Current Value',
                                            'Balance'])
        holdings = pd.DataFrame()  # Holds all coins ever traded
        ledger = pd.DataFrame()  # Holds all trades and shows profitability of all trades
        web_url = self.web_url
        # Create a ClientSession at the start of main
        async with aiohttp.ClientSession() as session:
            try:
                while True:
                    print(f'          '
                          f'Sighook {self.app_config.program_version}, Port: {port}    '
                          f'Updating ticker cache...')
                    start_time = None  # reset start time with each loop
                    start_time = self.utility.print_elapsed_time(start_time, 'main')
                    self.ticker_cache, self.market_cache = await self.ticker_manager.update_ticker_cache(start_time)
                    self.utility.print_elapsed_time(start_time, 'load ticker cache')
                    self.ticker_manager.set_trade_parameters(start_time, self.ticker_cache, self.market_cache, 
                                                             holdings)
                    self.utility.set_trade_parameters(start_time, self.ticker_cache, self.market_cache, holdings)
                    self.portfolio_manager.set_trade_parameters(start_time, self.ticker_cache, self.market_cache,
                                                                holdings)
                    self.api_wrapper.set_trade_parameters(start_time, self.ticker_cache, self.market_cache, web_url,
                                                          holdings)
                    self.trading_strategy.set_trade_parameters(start_time, session,  self.ticker_cache, self.market_cache,
                                                               holdings)
                    self.order_manager.set_trade_parameters(start_time, session, self.ticker_cache, self.market_cache,
                                                            web_url,
                                                            holdings)
                    self.profit_manager.set_trade_parameters(start_time, session, self.ticker_cache, self.market_cache,
                                                             web_url,
                                                             holdings)
                    self.market_manager.set_trade_parameters(start_time, self.ticker_cache, self.market_cache, 
                                                             holdings)
                    self.webhook.set_trade_parameters(start_time, session, self.ticker_cache, self.market_cache, web_url,
                                                      holdings)
                    self.market_metrics.set_trade_parameters(start_time, self.ticker_cache, self.market_cache, web_url,
                                                             holdings)
                    self.last_ticker_update = self.ticker_manager.last_ticker_update

                    (old_portfolio, usd_coins, avg_vol_total, buy_sell_matrix, price_change) = \
                        (self.api_wrapper.get_portfolio_data(start_time, self.old_portfolio))
                    holdings = old_portfolio
                    filtered_ticker_cache = self.portfolio_manager.filter_ticker_cache_matrix(buy_sell_matrix)
                    self.utility.print_elapsed_time(start_time, 'load portfolio data')
                    # trading strategy
                    if filtered_ticker_cache is not None and not filtered_ticker_cache.empty:
                        open_orders, bollinger_df = await self.market_manager.new_fetch_ohlcv(
                            old_portfolio, usd_coins, avg_vol_total, buy_sell_matrix, filtered_ticker_cache)
                        if open_orders is not None and len(open_orders) > 0:
                            print(f'Open orders: {open_orders.to_string(index=False)}')
                        else:
                            print(f'No open orders found')

                        self.utility.print_elapsed_time(start_time, 'Load OHLCV data')  # debug statement

                    # check portfolio balances for profitability#
                    profit_data = await self.profit_manager.check_profit_level(profit_data, holdings)
                    self.utility.print_elapsed_time(start_time, 'Load profit loss data')  # debug statement
                    if len(profit_data) > 0:
                        activetrade_data_path = os.path.join(self.active_trade_dir, 'activetrade_data.csv')
                        profit_data.to_csv(activetrade_data_path, index=False)
                        print(f"Profit Data:\n{profit_data.to_string(index=False)}")

                    ledger, profitability = await self.profit_manager.profits(start_time, self.portfolio_trade_dir)
                    if isinstance(ledger, pd.DataFrame):
                        portfolio_data_path = os.path.join(self.portfolio_trade_dir, 'portfolio_data.csv')
                        ledger.to_csv(portfolio_data_path, index=False)
                    if isinstance(profitability, pd.DataFrame):
                        profit_data_path = os.path.join(self.profit_dir, 'profit_data.csv')
                        profitability.to_csv(profit_data_path, index=False)
                    intro_text = f"High Volume Crypto Volume greater than {min(avg_vol_total, Decimal(1000000))}:"
                    print("<><><><<><>" * 20)
                    print(intro_text)
                    print(f'                        24h           24h       24h       ')
                    print(buy_sell_matrix.to_string(index=False))
                    profit_data = profit_data[0:0]  # clear contents of profit_data
                    self.utility.print_elapsed_time(start_time, 'main')  # debug statement
                    self.sleep_with_progress_bar()
            except KeyboardInterrupt:
                self.save_data_on_exit(profit_data, ledger)
            finally:
                self.alerts.callhome(f"Program has stopped running.",f'Time:{datetime.datetime.now()}')
                print("Program has exited.")
                #  session will be closed automatically due to the 'async with' context
                #  No need to call session.close() here

    def save_data_on_exit(self, profit_data, ledger):
        pass
    # Logic to save data when the program exits unexpectedly

    def sleep_with_progress_bar(self):
        total_sleep = int(self.sleep_time)
        update_interval = 1
        num_iterations = total_sleep // update_interval
        with alive_bar(num_iterations, force_tty=True) as bar:
            for _ in range(num_iterations):
                time.sleep(update_interval)
                bar()


if __name__ == "__main__":
    # os.environ['PYTHONASYNCIODEBUG'] = '1'  # Enable asyncio debug mode #async debug statement
    # print("Debugging - MACHINE_TYPE:", os.getenv('MACHINE_TYPE'))
    # print("Debugging - MACHINE_TYPE:", os.getenv('MACHINE_TYPE'))
    app_config = AppConfig()  # Create an instance of AppConfig

    # Set display options for pandas
    pd.set_option('display.max_columns', None)
    pd.set_option('display.width', None)
    pd.set_option('display.max_colwidth', None)
    pd.set_option('display.colheader_justify', 'center')
    bot = TradeBot(app_config)

    asyncio.run(bot.main(app_config.port))
