import asyncio
import aiohttp

import datetime

from decimal import Decimal

import time

import ccxt.async_support as ccxt  # import ccxt as ccxt

from tabulate import tabulate
import pandas as pd
from alerts_msgs_webhooks import AlertSystem, SenderWebhook
from config_manager import AppConfig
from logging_manager import LoggerManager
from indicators import Indicators
from database_ops import DatabaseOpsManager
from database_table_models import Trade
from database_session_manager import DatabaseSessionManager
from debug_functions import DebugDataLoader
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
from csv_manager import CsvManager

# Event to signal that a shutdown has been requested
shutdown_event = asyncio.Event()


class TradeBot:
    _exchange_instance_count = 0

    def __init__(self, bot_config):
        self.app_config = bot_config
        self.cb_api = self.app_config.load_sighook_api_key()
        self._csv_dir = self.app_config.csv_dir
        self.log_dir = self.app_config.log_dir
        self.csv_manager = None
        self.max_concurrent_tasks = 10
        self.tradebot = None
        self.log_manager, self.alerts, self.ccxt_exceptions, self.custom_excep = None, None, None, None
        self.api401, self.exchange, self.market_metrics, self.utility = None, None, None, None
        self.profit_extras, self.database_session_mngr, self.async_func, self.indicators = None, None, None, None
        self.ticker_manager, self.portfolio_manager, self.webhook, self.trading_strategy = None, None, None, None
        self.profit_manager, self.profit_helper, self.order_manager, self.market_manager = None, None, None, None
        self.market_data, self.ticker_cache, self.market_cache, self.current_prices = None, None, None, None
        self.filtered_balances, self.start_time, self.exchange_class, self.session = None, None, None, None
        self.db_initializer, self.database_ops_mngr, self.csv_manager, self.db_tables = None, None, None, None
        self.debug_data_loader = None
        self._min_volume = Decimal(self.app_config.min_volume)
        self.sleep_time = self.app_config.sleep_time
        self.web_url = self.app_config.web_url

        # self.initialize_components()
    @property
    def csv_dir(self):
        return self._csv_dir

    @property
    def min_volume(self):
        return self._min_volume

    def setup_exchange(self):
        self.exchange = getattr(ccxt, 'coinbase')
        TradeBot._exchange_instance_count += 1
        print(f"Exchange instance created. Total instances: {TradeBot._exchange_instance_count}")  # debug
        return self.exchange({
            'apiKey': self.cb_api.get('name'),
            'secret': self.cb_api.get('privateKey'),
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
            self.market_data = await self.market_manager.update_market_data(open_orders=None)
            self.utility.print_elapsed_time(self.start_time, 'Part I: market data update is complete')
            await self.database_session_mngr.process_data(self.market_data, self.start_time, self.csv_dir)
            self.utility.print_elapsed_time(self.start_time, 'Part I: Database Loading is complete, session closed')
        except Exception as e:
            self.log_manager.sighook_logger.error(f'Failed to initialize data on startup {e}', exc_info=True)
        finally:
            await self.exchange.close()
            TradeBot._exchange_instance_count -= 1
            print(f"Exchange instance closed. Total instances: {TradeBot._exchange_instance_count}")

    async def load_bot_components(self):
        """ Initialize all components required by the TradeBot. """
        self.log_manager = LoggerManager(self.app_config, log_dir=self.log_dir)
        self.exchange = self.setup_exchange()
        self.alerts = AlertSystem(self.app_config, self.log_manager)
        self.ccxt_exceptions = ApiExceptions(self.log_manager, self.alerts)
        self.async_func = AsyncFunctions()
        self.indicators = Indicators(self.log_manager, self.app_config)
        self.tradebot = TradeBot(self.app_config)
        self.utility = SenderUtils(self.log_manager, self.exchange, self.ccxt_exceptions)

        self.ticker_manager = TickerManager(self.utility, self.log_manager, self.exchange, self.ccxt_exceptions,
                                            self.max_concurrent_tasks)
        self.portfolio_manager = PortfolioManager(self.utility, self.log_manager, self.ccxt_exceptions, self.exchange,
                                                  self.max_concurrent_tasks, self.app_config)

        self.db_tables = Trade()

        self.debug_data_loader = DebugDataLoader(self.db_tables, self.log_manager)

        self.profit_extras = PerformanceManager(self.exchange, self.ccxt_exceptions, self.utility, self.order_manager,
                                                self.portfolio_manager, self.log_manager, self.app_config)

        self.database_ops_mngr = DatabaseOpsManager(self.debug_data_loader, self.db_tables, self.utility, self.exchange,
                                                    self.ccxt_exceptions, self.log_manager, self.ticker_manager,
                                                    self.portfolio_manager, self.app_config)

        self.csv_manager = CsvManager(self.utility, self.db_tables, self.database_ops_mngr, self.exchange,
                                      self.ccxt_exceptions, self.log_manager, self.app_config)

        self.database_session_mngr = DatabaseSessionManager(self.database_ops_mngr, self.csv_manager, self.log_manager,
                                                            self.profit_extras, self.app_config)

        self.db_initializer = DatabaseInitializer(self.database_session_mngr)

        self.webhook = SenderWebhook(self.exchange, self.utility, self.alerts, self.log_manager, self.app_config)
        self.trading_strategy = TradingStrategy(self.webhook, self.ticker_manager, self.utility, self.exchange, self.alerts,
                                                self.log_manager, self.ccxt_exceptions, self.market_metrics,
                                                self.app_config, self.max_concurrent_tasks, self.database_session_mngr)
        self.profit_helper = ProfitHelper(self.utility, self.portfolio_manager, self.ticker_manager,
                                          self.database_session_mngr, self.log_manager, self.app_config)
        self.order_manager = OrderManager(self.trading_strategy, self.ticker_manager, self.exchange, self.webhook,
                                          self.utility, self.alerts, self.log_manager, self.ccxt_exceptions,
                                          self.profit_helper, self.app_config, self.max_concurrent_tasks)
        self.market_manager = MarketManager(self.tradebot, self.exchange, self.order_manager, self.trading_strategy,
                                            self.log_manager,
                                            self.ccxt_exceptions, self.ticker_manager, self.utility,
                                            self.max_concurrent_tasks, self.database_session_mngr)

        self.profit_manager = ProfitabilityManager(self.exchange, self.ccxt_exceptions, self.utility, self.portfolio_manager,
                                                   self.database_session_mngr, self.database_ops_mngr, self.order_manager,
                                                   self.trading_strategy, self.profit_helper, self.profit_extras,
                                                   self.log_manager, self.app_config)

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

    async def run_bot(self):  # async

        profit_data = pd.DataFrame(columns=['Symbol', 'Unrealized PCT', 'Profit/Loss', 'Total Cost', 'Current Value',
                                            'Balance'])
        ledger = pd.DataFrame()  # Holds all trades and shows profitability of all trades
        web_url = self.web_url
        open_orders = pd.DataFrame()
        ohlcv_data_dict = {}  # dictionary to hold ohlcv data
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

                self.profit_manager.set_trade_parameters(self.start_time, self.ticker_cache, self.market_cache, self.web_url)

                self.profit_extras.set_trade_parameters(self.start_time, self.ticker_cache, self.current_prices)

                self.csv_manager.set_trade_parameters(self.start_time, self.ticker_cache, self.market_cache)

                # PART II:
                #   Trade Database Updates and Portfolio Management
                print(f'Part II: Trade Database Updates and Portfolio Management - Start Time:', datetime.datetime.now())
                (holdings, usd_coins, ticker_cache_avg_dollar_vol, buy_sell_matrix, price_change) = \
                    self.portfolio_manager.get_portfolio_data(self.start_time)

                # Filter to include only coins that have a buy or sell signal, all others omitted.
                # filtered_ticker_cache -> dataframe type
                filtered_ticker_cache = self.portfolio_manager.filter_ticker_cache_matrix(buy_sell_matrix)

                self.utility.print_elapsed_time(self.start_time, 'Part II: Trade Database Updates/Portfolio Management')

                # PART III:
                #   Order cancellation and Data Collection
                print(f"Exchange instance. Total instances: {TradeBot._exchange_instance_count}")
                print(f'Part III: Order cancellation and Data Collection - Start Time:', datetime.datetime.now())

                if filtered_ticker_cache is not None and not filtered_ticker_cache.empty:
                    open_orders = await self.order_manager.get_open_orders()
                    self.utility.print_elapsed_time(self.start_time, 'open_orders')
                    if not await self.market_manager.db_manager.check_ohlcv_initialized():
                        await self.market_manager.fetch_ohlcv(filtered_ticker_cache)
                    await self.market_manager.update_ohlcv(filtered_ticker_cache)

                self.utility.print_elapsed_time(self.start_time, 'Part III: Order cancellation and Data Collection')

                # PART IV:
                # Trading Strategies
                print(f'Part IV: Trading Strategies - Start Time:', datetime.datetime.now())
                results, buy_sell_matrix = await self.trading_strategy.process_all_rows(filtered_ticker_cache,
                                                                                        buy_sell_matrix)

                self.utility.print_elapsed_time(self.start_time, 'Part IV: Trading Strategies')

                # PART V:
                # Order Execution
                print(f'Part V: Order Execution - Start Time:', datetime.datetime.now())
                submitted_orders = await self.order_manager.execute_actions(results, holdings)
                self.utility.print_elapsed_time(self.start_time, 'Part V: Order Execution')

                # PART VI:
                # Profitability Analysis and Order Generation
                wallets = await self.portfolio_manager.fetch_wallets()
                print(f'Part VI: Profitability Analysis and Order Generation - Start Time:', datetime.datetime.now())
                aggregated_df, profit_data = await self.profit_manager.check_profit_level(wallets,  self.current_prices,
                                                                                          open_orders)
                self.utility.print_elapsed_time(self.start_time, 'Part VI: Profitability Analysis and Order Generation')
                if self.exchange is not None:
                    await self.exchange.close()
                self.print_data(self.min_volume, open_orders, buy_sell_matrix, ticker_cache_avg_dollar_vol,
                                submitted_orders, aggregated_df, profit_data)

                # PART VII: Database update and cleanup
                print(f'Part VII: Database update and cleanup - Start Time:', datetime.datetime.now())
                total_time = self.utility.print_elapsed_time(self.start_time, 'load bot components')

                if total_time < int(self.sleep_time):
                    await asyncio.sleep(int(self.sleep_time) - total_time)
                else:
                    await asyncio.sleep(int(0))

                self.start_time = time.time()
                print('Part I: Data Gathering and Database Loading - Start Time:', datetime.datetime.now())
                open_orders = await self.order_manager.get_open_orders()
                self.market_data = await self.market_manager.update_market_data(open_orders)
                current_prices = self.market_data['current_prices']

                # Check and update trailing stop orders and prepare webhook signal
                await self.order_manager.check_prepare_trailing_stop_orders(open_orders, current_prices)

                await self.database_session_mngr.process_data(self.market_data, self.start_time)
                self.utility.print_elapsed_time(self.start_time, 'Part I: Data Gathering and Database Loading is complete, '
                                                                 'session closed')
                # if need_to_refresh_session():
                #     await self.refresh_session()
                #  debugging remove when done testing
                # if AsyncFunctions.shutdown_event.is_set():
                #     await AsyncFunctions.shutdown(asyncio.get_running_loop(), database_manager=self.database_session_mngr,
                #                                   http_session=self.http_session)
                # break
        except asyncio.CancelledError:
            self.save_data_on_exit(profit_data, ledger)
        except Exception as e:
            self.log_manager.sighook_logger.error(f"Error in main loop: {e}", exc_info=True)
            await self.exchange.close()  # close the exchange connection
            TradeBot._exchange_instance_count -= 1  # debug
        finally:
            await self.exchange.close()
            TradeBot._exchange_instance_count -= 1
            if AsyncFunctions.shutdown_event.is_set():
                await AsyncFunctions.shutdown(asyncio.get_running_loop(), http_session=self.http_session)
            print("Program has exited.")

    @staticmethod
    def print_data(min_volume, open_orders, buy_sell_matrix, ticker_cache_avg_dollar_vol, submitted_orders, aggregated_df,
                   profit_data):
        if open_orders is not None and len(open_orders) > 0:
            print(f'')
            print(f'Open orders:')
            print(tabulate(open_orders, headers='keys', tablefmt='pretty', showindex=False, stralign='center',
                           numalign='center'))
            print(f'')
        else:
            print(f'No open orders found')
        if submitted_orders is not None and len(submitted_orders) > 0:
            print(f'')
            print(f'Orders Submitted:')
            print(tabulate(submitted_orders, headers='keys', tablefmt='pretty', showindex=False, stralign='center',
                           numalign='center'))
            print(f'')
        else:
            print(f'No Orders were Submitted')
        if buy_sell_matrix is not None and len(buy_sell_matrix) > 0:
            no_buy = (buy_sell_matrix['Buy Signal'].notna()) & (buy_sell_matrix['Buy Signal'] != '')
            no_sell = (buy_sell_matrix['Sell Signal'].notna()) & (buy_sell_matrix['Sell Signal'] != '')
            filtered_matrix = buy_sell_matrix[no_buy | no_sell]
            intro_text = (
                f"{len(filtered_matrix)} Currencies trading with a buy or sell signal and Volume greater "
                f"than"
                f" {min(ticker_cache_avg_dollar_vol, min_volume)}:")
            print("<><><><<><>" * 20)
            print(intro_text)
            print(f'                        24h           24h       24h                                              (Need 3)')
            print(tabulate(filtered_matrix, headers='keys', tablefmt='pretty', showindex=False, stralign='center',
                           numalign='center'))
            print(f'')
        if aggregated_df is not None and not aggregated_df.empty:
            print(f'Holdings with changes: {aggregated_df.to_string(index=False)}')
        else:
            print(f'No changes to holdings')
        if profit_data is not None:
            print(f' Realized Profit {profit_data["realized profit"]}  Unrealized Profit '
                  f'{profit_data["unrealized profit"]}  Portfolio Value {profit_data["portfolio value"]}')

        else:
            print(f'No profit data')
        print("<><><><<><>" * 20)

    def save_data_on_exit(self, profit_data, ledger):
        pass


if __name__ == "__main__":
    # os.environ['PYTHONASYNCIODEBUG'] = '1'  # Enable asyncio debug mode #async debug statement
    # print("Debugging - MACHINE_TYPE:", os.getenv('MACHINE_TYPE'))
    app_config = AppConfig()  # Assume AppConfig is properly defined elsewhere

    bot = TradeBot(app_config)
    asyncio.run(bot.start())
