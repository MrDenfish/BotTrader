import asyncio

import aiohttp

import signal

from memory_profiler import profile  # Debugging tool

import datetime

import os

from decimal import Decimal

import time

import ccxt.async_support as ccxt  # import ccxt as ccxt

import pandas as pd

from alerts_msgs_webhooks import AlertSystem, SenderWebhook
from config_manager import AppConfig
from custom_exceptions import ApiExceptions
from logging_manager import LoggerManager
from indicators import Indicators
from database_manager import DatabaseManager
from ticker_manager import TickerManager
from async_functions import AsyncFunctions
from portfolio_manager import PortfolioManager
from market_manager import MarketManager
from utility import SenderUtils
from order_manager import OrderManager
from trading_strategy import TradingStrategy
from profit_manager import ProfitabilityManager
from profit_helper import ProfitHelper

# Event to signal that a shutdown has been requested
shutdown_event = asyncio.Event()


class TradeBot:
    _instance_count = 0

    def __init__(self, appconfig, http_session):
        # self.id = TradeBot._instance_count
        # TradeBot._instance_count += 1
        # print(f"TradeBot Instance ID: {self.id}")
        self.app_config = appconfig  # Instance of AppConfig
        self.http_session = http_session  # Use this session for HTTP requests
        self._sleep_time = appconfig.sleep_time
        self.log_manager, self.alerts, self.ccxt_exceptions, self.api401, self.custom_excep = None, None, None, None, None
        self.coms, self.utility, self.ticker_manager, self.portfolio_manager, self.webhook = None, None, None, None, None
        self.trading_strategy, self.order_manager, self.api_wrapper, self.market_manager = None, None, None, None
        self.market_metrics, self.profit_manager, self.exchange_class, self.exchange = None, None, None, None
        self.profit_helper, self.cmc, self.cmc_api_key, self.cmc_url = None, None, None, None  # coin market cap
        self.database_manager, self.async_func, self.indicators = None, None, None
        self.log_dir = self.app_config.log_dir
        self.active_trade_dir = self.app_config.active_trade_dir
        self.portfolio_trade_dir = self.app_config.portfolio_dir
        self.profit_dir = self.app_config.profit_dir
        self.database_dir = self.app_config.database_dir
        self.ticker_cache = pd.DataFrame()
        self.market_cache = pd.DataFrame()
        self.buy_sell_matrix = pd.DataFrame()
        self.start_time = None  # Tracks start time of each trading cycle
        self.last_ticker_update = None  # Tracks last time ticker data was updated
        self.max_concurrent_tasks = 10
        self.web_url = self.app_config.web_url
        self.setup_exchange()

    def setup_exchange(self):
        self.exchange_class = getattr(ccxt, 'coinbase')
        self.exchange = self.exchange_class({
            'apiKey': self.app_config.api_key,
            'secret': self.app_config.api_secret,
            'enableRateLimit': True,
            'verbose': False  # True for debugging
        })

    async def async_init(self):
        self.log_manager = LoggerManager(self.app_config, log_dir=self.log_dir)
        self.ccxt_exceptions = ApiExceptions(self.log_manager, self.alerts)
        await self.setup_database()
        await self.database_manager.async_create_tables()  # Correctly placed call to create tables
        await self.load_bot_components()
        await self.load_initial_data()

    async def setup_database(self):
        self.database_manager = DatabaseManager(self.utility, self.exchange, self.log_manager, self.ticker_manager,
                                                self.portfolio_manager, self.app_config)
        await self.database_manager.async_create_tables()

    async def load_initial_data(self):
        """PART I: Data Gathering and Database Loading"""
        self.start_time = time.time()
        print('Part I: Data Gathering and Database Loading - Start Time:', datetime.datetime.now())
        # Load initial market data, ticker cache, etc.
        self.ticker_cache, self.market_cache = await self.database_manager.initialize_db()
        elapsed_time = time.time() - self.start_time

    async def load_bot_components(self):
        self.async_func = AsyncFunctions()
        self.alerts = AlertSystem(self.app_config, self.log_manager)
        self.indicators = Indicators(self.log_manager, self.app_config)
        self.utility = SenderUtils(self.log_manager, self.exchange, self.ccxt_exceptions)
        self.ticker_manager = TickerManager(self.utility, self.log_manager, self.exchange, self.ccxt_exceptions,
                                            self.max_concurrent_tasks)
        self.portfolio_manager = PortfolioManager(self.utility, self.log_manager, self.ccxt_exceptions, self.exchange,
                                                  self.max_concurrent_tasks)

        self.database_manager = DatabaseManager(self.utility, self.exchange, self.log_manager, self.ticker_manager,
                                                self.portfolio_manager, self.app_config)

        self.webhook = SenderWebhook(self.exchange, self.alerts, self.log_manager, self.app_config)

        self.trading_strategy = TradingStrategy(self.webhook, self.ticker_manager, self.utility, self.exchange, self.alerts,
                                                self.log_manager, self.ccxt_exceptions, self.market_metrics,
                                                self.app_config, self.max_concurrent_tasks)
        self.profit_helper = ProfitHelper(self.utility, self.portfolio_manager, self.ticker_manager,
                                          self.database_manager, self.log_manager,  self.app_config)

        self.order_manager = OrderManager(self.trading_strategy, self.ticker_manager, self.exchange, self.webhook,
                                          self.utility, self.alerts, self.log_manager, self.ccxt_exceptions,
                                          self.profit_helper, self.app_config, self.max_concurrent_tasks)

        self.market_manager = MarketManager(self.exchange, self.order_manager,  self.trading_strategy, self.log_manager,
                                            self.ccxt_exceptions, self.ticker_manager, self.utility,
                                            self.max_concurrent_tasks)

        self.profit_manager = ProfitabilityManager(self.exchange, self.ccxt_exceptions, self.utility, self.database_manager,
                                                   self.order_manager, self. portfolio_manager,  self.trading_strategy,
                                                   self.profit_helper, self.log_manager, self.app_config)

    async def run_bot(self):  # async

        profit_data = pd.DataFrame(columns=['Symbol', 'Unrealized PCT', 'Profit/Loss', 'Total Cost', 'Current Value',
                                            'Balance'])
        ledger = pd.DataFrame()  # Holds all trades and shows profitability of all trades
        web_url = self.web_url
        # Create a ClientSession at the start of main
        async with (aiohttp.ClientSession() as http_session):   # async
            try:
                while not AsyncFunctions.shutdown_event.is_set():
                    self.ticker_manager.set_trade_parameters(self.start_time, self.ticker_cache, self.market_cache)

                    self.utility.set_trade_parameters(self.start_time, self.ticker_cache, self.market_cache)

                    self.portfolio_manager.set_trade_parameters(self.start_time, self.ticker_cache, self.market_cache)

                    self.trading_strategy.set_trade_parameters(self.start_time, self.ticker_cache, self.market_cache)

                    self.order_manager.set_trade_parameters(self.start_time, self.ticker_cache, self.market_cache, web_url)

                    self.market_manager.set_trade_parameters(self.start_time, self.ticker_cache, self.market_cache)

                    self.utility.print_elapsed_time(self.start_time, 'Part I: Data Gathering and Database Loading')

                    self.webhook.set_trade_parameters(self.start_time, self.ticker_cache, self.market_cache, web_url,
                                                      http_session)

                    # PART II:
                    #   Trade Database Updates and Portfolio Management
                    print(f'Part II: Trade Database Updates and Portfolio Management - Start Time:', datetime.datetime.now())
                    (holdings, usd_coins, avg_vol_total, buy_sell_matrix, price_change) = \
                        self.portfolio_manager.get_portfolio_data(self.start_time)

                    filtered_ticker_cache = self.portfolio_manager.filter_ticker_cache_matrix(buy_sell_matrix)

                    self.utility.print_elapsed_time(self.start_time, 'Part II: Trade Database Updates/Portfolio Management')

                    # PART III:
                    #   Order cancellation and Data Collection
                    print(f'Part III: Order cancellation and Data Collection - Start Time:', datetime.datetime.now())

                    if filtered_ticker_cache is not None and not filtered_ticker_cache.empty:
                        open_orders, ohlcv_data_dict = await self.market_manager.fetch_ohlcv(
                            holdings, usd_coins, avg_vol_total, buy_sell_matrix, filtered_ticker_cache)
                    self.utility.print_elapsed_time(self.start_time, 'Part III: Order cancellation and Data Collection')

                    # PART IV:
                    # Trading Strategies
                    print(f'Part IV: Trading Strategies - Start Time:', datetime.datetime.now())
                    results, buy_sell_matrix = await self.trading_strategy.process_all_rows(
                        filtered_ticker_cache, buy_sell_matrix, ohlcv_data_dict)

                    self.utility.print_elapsed_time(self.start_time, 'Part IV: Trading Strategies')

                    # PART V:
                    # Order Execution
                    print(f'Part V: Order Execution - Start Time:', datetime.datetime.now())
                    submitted_orders = await self.order_manager.execute_actions(results, holdings)
                    await self.database_manager.initialize_holding_db(holdings)
                    self.utility.print_elapsed_time(self.start_time, 'Part V: Order Execution')

                    #  debugging remove when done testing
                    if AsyncFunctions.shutdown_event.is_set():
                        await AsyncFunctions.shutdown(asyncio.get_running_loop(), database_manager=self.database_manager,
                                                      http_session=self.http_session)
                    break
                    # PART VI:
                    # Profitability Analysis and Order Generation
                    print(f'Part VI: Profitability Analysis and Order Generation - Start Time:', datetime.datetime.now())
                    aggregated_df = await self.profit_manager.check_profit_level(holdings)
                    self.utility.print_elapsed_time(self.start_time, 'Part VI: Profitability Analysis and Order Generation')
                    if self.exchange is not None:
                        await self.exchange.close()
                    self.print_data(open_orders, buy_sell_matrix, avg_vol_total, submitted_orders, aggregated_df)
                    # PART VII: Database update and cleanup
                    print(f'Part VII: Database update and cleanup - Start Time:', datetime.datetime.now())
                    self.utility.print_elapsed_time(self.start_time, 'load bot components')
                    self.start_time = time.time()
                    self.ticker_cache, self.market_cache = await self.database_manager.initialize_db()
            except asyncio.CancelledError:
                self.save_data_on_exit(profit_data, ledger)
            except Exception as e:
                self.log_manager.sighook_logger.error(f"Error in main loop: {e}", exc_info=True)
                await self.exchange.close()  # close the exchange connection
            finally:
                await self.exchange.close()  # close the exchange connection
                if AsyncFunctions.shutdown_event.is_set():
                    await AsyncFunctions.shutdown(asyncio.get_running_loop(), database_manager=self.database_manager,
                                                  http_session=self.http_session)

                # self.save_data_on_exit(profit_data, ledger)  # Ensure this is awaited if it's an async function
                # self.alerts.callhome(f"Program has stopped running.", f'Time:{datetime.datetime.now()}')
                print("Program has exited.")

    @staticmethod
    def print_data(open_orders, buy_sell_matrix, avg_vol_total, submitted_orders, aggregated_df):
        if open_orders is not None and len(open_orders) > 0:
            print(f'')
            print(f'Open orders:')
            print(open_orders.to_string(index=False))
            print(f'')
        else:
            print(f'No open orders found')
        if submitted_orders is not None and len(submitted_orders) > 0:
            print(f'')
            print(f'Orders Submitted:')
            print(submitted_orders.to_string(index=False))
            print(f'')
        else:
            print(f'No Orders were Submitted')
        no_buy = (buy_sell_matrix['Buy Signal'].notna()) & (buy_sell_matrix['Buy Signal'] != '')
        no_sell = (buy_sell_matrix['Sell Signal'].notna()) & (buy_sell_matrix['Sell Signal'] != '')
        filtered_matrix = buy_sell_matrix[no_buy | no_sell]
        intro_text = (
            f"{len(filtered_matrix)} Currencies trading with a buy or sell signal and Volume greater "
            f"than"
            f" {min(avg_vol_total, Decimal(1000000))}:")
        print("<><><><<><>" * 20)
        print(intro_text)
        print(f'                        24h           24h       24h                                              (Need 3)')
        print(filtered_matrix.to_string(index=False))
        print(f'')
        if aggregated_df is not None and not aggregated_df.empty:
            print(f'Holdings with changes: {aggregated_df.to_string(index=False)}')
        else:
            print(f'No changes to holdings')
        print("<><><><<><>" * 20)

    def save_data_on_exit(self, profit_data, ledger):
        pass


async def main():
    async with aiohttp.ClientSession() as http_session:
        app_config = AppConfig()
        bot = TradeBot(app_config, http_session)
        print(f'Sighook {app_config.program_version}, Port: {app_config.port} Updating ticker cache...')
        await bot.async_init()
        await bot.run_bot()

if __name__ == "__main__":
    # os.environ['PYTHONASYNCIODEBUG'] = '1'  # Enable asyncio debug mode #async debug statement
    # print("Debugging - MACHINE_TYPE:", os.getenv('MACHINE_TYPE'))

    # Set display options for pandas
    pd.set_option('display.max_columns', None)
    pd.set_option('display.width', None)
    pd.set_option('display.max_colwidth', None)
    pd.set_option('display.colheader_justify', 'center')
    asyncio.run(main())  # bot.main(app_config.port)
