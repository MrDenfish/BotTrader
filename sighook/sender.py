import asyncio
import aiohttp

import datetime

import os

from decimal import Decimal

import time

import ccxt.async_support as ccxt  # import ccxt as ccxt

import pandas as pd
from aiohttp import ClientSession as AsynchronousClientSession
from alerts_msgs_webhooks import AlertSystem, SenderWebhook
from async_functions import AsyncFunctions
from config_manager import AppConfig
from logging_manager import LoggerManager
from indicators import Indicators
from database_ops import DatabaseOpsManager
from database_session_manager import DatabaseSessionManager
from database_initialize import DatabaseInitializer
from ticker_manager import TickerManager
from async_functions import AsyncFunctions
from api_manager import ApiExceptions
from portfolio_manager import PortfolioManager
from market_manager import MarketManager
from utility import SenderUtils
from order_manager import OrderManager
from trading_strategy import TradingStrategy
from profit_manager import ProfitabilityManager
from profit_helper import ProfitHelper
from profit_extras import PerformanceManager

# Event to signal that a shutdown has been requested
shutdown_event = asyncio.Event()


class TradeBot:
    def __init__(self, bot_config):
        self.app_config = bot_config
        self.log_dir = self.app_config.log_dir
        self.max_concurrent_tasks = 10
        self.log_manager, self.alerts, self.ccxt_exceptions, self.custom_excep = None, None, None, None
        self.api401, self.exchange, self.market_metrics = None, None, None
        self.profit_extras, self.database_session_mngr, self.async_func, self.indicators = None, None, None, None
        self.ticker_manager, self.portfolio_manager, self.webhook, self.trading_strategy = None, None, None, None
        self.profit_manager, self.profit_helper, self.order_manager, self.market_manager = None, None, None, None
        self.market_data, self.ticker_cache, self.market_cache, self.current_prices = None, None, None, None
        self.filtered_balances, self.start_time, self.exchange_class, self.session = None, None, None, None
        self.db_initializer, self.database_ops_mngr = None, None
        self.sleep_time = self.app_config.sleep_time
        self.web_url = self.app_config.web_url
        self.utility = None
        # self.initialize_components()

    async def start(self):
        """ Start the bot after initialization. """
        try:
            async with aiohttp.ClientSession() as self.http_session:
                await self.async_init()
                await self.run_bot()
        except Exception as e:
            print(f"Failed to start the bot: {e}")
        finally:
            if hasattr(self, 'database_manager') and self.database_manager.engine:
                await self.database_manager.engine.dispose()  # Clean up the engine
            print("Program has exited.")

    def setup_exchange(self):
        exchange_class = getattr(ccxt, 'coinbase')
        return exchange_class({
            'apiKey': self.app_config.api_key,
            'secret': self.app_config.api_secret,
            'enableRateLimit': True,
            'verbose': False
        })

    async def async_init(self):
        await self.load_bot_components()
        await self.db_initializer.create_tables()  # Ensure tables are created before proceeding
        await self.load_initial_data()

    async def load_initial_data(self):
        try:
            """PART I: Data Gathering and Database Loading"""
            self.start_time = time.time()
            print('Part I: Data Gathering and Database Loading - Start Time:', datetime.datetime.now())
            self.market_data = await self.market_manager.update_market_data()
            await self.database_session_mngr.process_data(self.market_data, self.start_time)
            self.utility.print_elapsed_time(self.start_time, 'Part I: Data Gathering and Database Loading is complete, '
                                                             'session closed')
        except Exception as e:
            self.log_manager.error("Failed to initialize data on startup", exc_info=True)
        finally:
            await self.exchange.close()

    async def load_bot_components(self):

        """ Initialize all components required by the TradeBot. """
        self.log_manager = LoggerManager(self.app_config, log_dir=self.log_dir)
        self.exchange = self.setup_exchange()
        self.ccxt_exceptions = ApiExceptions(self.log_manager, self.alerts)
        self.async_func = AsyncFunctions()
        self.alerts = AlertSystem(self.app_config, self.log_manager)
        self.indicators = Indicators(self.log_manager, self.app_config)
        self.utility = SenderUtils(self.log_manager, self.exchange, self.ccxt_exceptions)
        self.ticker_manager = (TickerManager(self.utility, self.log_manager, self.exchange, self.ccxt_exceptions,
                                             self.max_concurrent_tasks))

        self.portfolio_manager = PortfolioManager(self.utility, self.log_manager, self.ccxt_exceptions, self.exchange,
                                                  self.max_concurrent_tasks)

        self.database_ops_mngr = DatabaseOpsManager(self.utility, self.exchange, self.log_manager, self.ticker_manager,
                                                    self.portfolio_manager, self.app_config)
        self.database_session_mngr = DatabaseSessionManager(self.database_ops_mngr, self.log_manager, self.app_config)

        self.db_initializer = DatabaseInitializer(self.database_session_mngr)

        self.webhook = SenderWebhook(self.exchange, self.utility, self.alerts, self.log_manager, self.app_config)

        self.trading_strategy = TradingStrategy(self.webhook, self.ticker_manager, self.utility, self.exchange, self.alerts,
                                                self.log_manager, self.ccxt_exceptions, self.market_metrics,
                                                self.app_config, self.max_concurrent_tasks)
        self.profit_helper = ProfitHelper(self.utility, self.portfolio_manager, self.ticker_manager,
                                          self.database_session_mngr, self.log_manager,  self.app_config)

        self.order_manager = OrderManager(self.trading_strategy, self.ticker_manager, self.exchange, self.webhook,
                                          self.utility, self.alerts, self.log_manager, self.ccxt_exceptions,
                                          self.profit_helper, self.app_config, self.max_concurrent_tasks)

        self.market_manager = MarketManager(self.exchange, self.order_manager,  self.trading_strategy, self.log_manager,
                                            self.ccxt_exceptions, self.ticker_manager, self.utility,
                                            self.max_concurrent_tasks)
        self.profit_extras = PerformanceManager(self.exchange, self.ccxt_exceptions, self.utility, self.profit_helper,
                                                self.order_manager, self.portfolio_manager, self.database_session_mngr,
                                                self.log_manager, self.app_config)

        self.profit_manager = ProfitabilityManager(self.exchange, self.ccxt_exceptions, self.utility, self.portfolio_manager,
                                                   self.database_session_mngr, self.order_manager, self.trading_strategy,
                                                   self.profit_helper, self.profit_extras, self.log_manager, self.app_config)

    async def run_bot(self):  # async

        profit_data = pd.DataFrame(columns=['Symbol', 'Unrealized PCT', 'Profit/Loss', 'Total Cost', 'Current Value',
                                            'Balance'])
        ledger = pd.DataFrame()  # Holds all trades and shows profitability of all trades
        web_url = self.web_url
        open_orders = pd.DataFrame()
        ohlcv_data_dict = {}
        # Create a ClientSession at the start of main
        self.ticker_cache = self.market_data['ticker_cache']
        self.market_cache = self.market_data['market_cache']
        self.current_prices = self.market_data['current_prices']
        self.filtered_balances = self.market_data['filtered_balances']
        try:
            while not AsyncFunctions.shutdown_event.is_set():
                self.ticker_manager.set_trade_parameters(self.start_time, self.ticker_cache, self.market_cache)

                self.utility.set_trade_parameters(self.start_time, self.ticker_cache, self.market_cache)

                self.portfolio_manager.set_trade_parameters(self.start_time, self.ticker_cache, self.market_cache)

                self.trading_strategy.set_trade_parameters(self.start_time, self.ticker_cache, self.market_cache)

                self.order_manager.set_trade_parameters(self.start_time, self.ticker_cache, self.market_cache, web_url)

                self.market_manager.set_trade_parameters(self.start_time, self.ticker_cache, self.market_cache)

                self.webhook.set_trade_parameters(self.start_time, self.ticker_cache, self.market_cache, web_url)

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
                self.utility.print_elapsed_time(self.start_time, 'Part V: Order Execution')

                # PART VI:
                # Profitability Analysis and Order Generation
                print(f'Part VI: Profitability Analysis and Order Generation - Start Time:', datetime.datetime.now())
                aggregated_df = await self.profit_manager.check_profit_level(holdings,  self.current_prices)

                aggregated_df = pd.DataFrame()  # Debug place holder
                self.utility.print_elapsed_time(self.start_time, 'Part VI: Profitability Analysis and Order Generation')
                if self.exchange is not None:
                    await self.exchange.close()
                self.print_data(open_orders, buy_sell_matrix, avg_vol_total, submitted_orders, aggregated_df)
                #  debugging remove when done testing
                # if AsyncFunctions.shutdown_event.is_set():
                #     await AsyncFunctions.shutdown(asyncio.get_running_loop(), database_manager=self.database_session_mngr ,
                #                                   http_session=self.http_session)
                # break

                # PART VII: Database update and cleanup
                print(f'Part VII: Database update and cleanup - Start Time:', datetime.datetime.now())
                self.utility.print_elapsed_time(self.start_time, 'load bot components')

                await asyncio.sleep(int(self.sleep_time))
                self.start_time = time.time()
                print('Part I: Data Gathering and Database Loading - Start Time:', datetime.datetime.now())
                self.market_data = await self.market_manager.update_market_data()
                await self.database_session_mngr.process_data(self.market_data, self.start_time)
                self.utility.print_elapsed_time(self.start_time, 'Part I: Data Gathering and Database Loading is complete, '
                                                                 'session closed')
                # if need_to_refresh_session():
                #     await self.refresh_session()

        except asyncio.CancelledError:
            self.save_data_on_exit(profit_data, ledger)
        except Exception as e:
            self.log_manager.sighook_logger.error(f"Error in main loop: {e}", exc_info=True)
            await self.exchange.close()  # close the exchange connection
        finally:
            await self.exchange.close()  # close the exchange connection
            if AsyncFunctions.shutdown_event.is_set():
                await AsyncFunctions.shutdown(asyncio.get_running_loop(), http_session=self.http_session)

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


if __name__ == "__main__":
    # os.environ['PYTHONASYNCIODEBUG'] = '1'  # Enable asyncio debug mode #async debug statement
    # print("Debugging - MACHINE_TYPE:", os.getenv('MACHINE_TYPE'))
    app_config = AppConfig()  # Assume AppConfig is properly defined elsewhere

    bot = TradeBot(app_config)
    asyncio.run(bot.start())
