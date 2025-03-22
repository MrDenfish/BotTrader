import asyncio
import aiohttp
import datetime
from decimal import Decimal
import time
import ccxt.async_support as ccxt  # import ccxt as ccxt
import pandas as pd
from SharedDataManager.shared_data_manager import SharedDataManager
from sighook.alerts_msgs_webhooks import AlertSystem, SenderWebhook
from Shared_Utils.logging_manager import LoggerManager
from sighook.indicators import Indicators
from sighook.database_ops import DatabaseOpsManager
from sighook.database_table_models import DatabaseTables
from sighook.holdings_process_manager import HoldingsProcessor
from sighook.database_session_manager import DatabaseSessionManager
from Shared_Utils.debugger import Debugging
from Shared_Utils.database_checker import DatabaseIntegrity
from Config.config_manager import CentralConfig as bot_config
from Shared_Utils.precision import PrecisionUtils
from Shared_Utils.dates_and_times import DatesAndTimes
from Shared_Utils.print_data import PrintData
from Shared_Utils.utility import SharedUtility
from Shared_Utils.snapshots_manager import SnapshotsManager
from MarketDataManager.market_data_manager import MarketDataUpdater
from MarketDataManager.ticker_manager import TickerManager
from ProfitDataManager.profit_data_manager import ProfitDataManager
from sighook.async_functions import AsyncFunctions
from Api_manager.api_manager import ApiManager
from sighook.portfolio_manager import PortfolioManager
from MarketDataManager.market_manager import MarketManager
from sighook.order_manager import OrderManager
from sighook.trading_strategy import TradingStrategy
from sighook.profit_manager import ProfitabilityManager
from sighook.profit_helper import ProfitHelper
import logging

# from pyinstrument import Profiler # debugging

# Event to signal that a shutdown has been requested
shutdown_event = asyncio.Event()


class TradeBot:
    _exchange_instance_count = 0

    def __init__(self, shared_data_mgr, rest_client, portfolio_uuid, log_mgr=None):
        self.shared_data_manager = shared_data_mgr
        self.app_config = bot_config()
        self.rest_client = rest_client
        self.portfolio_uuid = portfolio_uuid
        self.log_manager = log_mgr or shared_data_mgr.log_manager
        self.database_session_mngr = shared_data_mgr.database_session_manager
        if not self.app_config._is_loaded:
            self.app_config._load_configuration()  # Ensure config is fully loaded
        self.cb_api = self.app_config.load_sighook_api_key()
        self._csv_dir = self.app_config.csv_dir
        self.tradebot = self.order_management = self.market_data = None
        self.max_concurrent_tasks = 10
        self.alerts = self.ccxt_api = self.custom_excep = self.db_initializer = None
        self.api401 = self.market_metrics = self.print_data = self.db_tables = None
        self.profit_extras = self.async_func = self.indicators = self.debug_data_loader = None
        self.ticker_manager = self.portfolio_manager = self.webhook = self.trading_strategy = None
        self.profit_manager = self.profit_helper = self.order_manager = self.market_manager = None
        self.market_data = self.ticker_cache = self.market_cache_vol = self.current_prices = None
        self.filtered_balances = self.start_time = self.exchange_class = self.session = None
        self.sharded_utils = self.profit_data_manager = self.snapshots_manager =  self.ticker_manager = None
        self.sleep_time = self.app_config.sleep_time
        self.web_url = self.app_config.web_url
        # Initialize components to None
        self.initialize_components()

    def initialize_components(self):
        """Initialize all components to None to ensure proper cleanup."""
        self.exchange = None
        self.database_ops = None
        self.db_initializer = None


    # self.initialize_components()
    @property
    def csv_dir(self):
        return self._csv_dir

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
        """ Initialize bot components asynchronously, ensuring the database connection is ready first. """

        # ✅ Ensure SnapshotsManager is correctly retrieved as a singleton
        self.snapshots_manager = SnapshotsManager.get_instance(self.shared_data_manager, self.log_manager)

        # Initialize other components that may depend on the database
        await self.load_bot_components()

        # Load required initial data
        market_data, order_management = await self.shared_data_manager.initialize_shared_data()
        self.market_data = market_data  # Store explicitly in TradeBot if needed
        self.order_management = order_management

        await self.load_initial_data()

    def setup_logger(self):
        """Initialize the logging manager."""
        log_config = {"log_level": logging.INFO}
        self.sighook_logger = LoggerManager(log_config)
        self.log_manager = self.sighook_logger.get_logger('sighook_logger')

    async def refresh_trade_data(self):
        try:
            print(f"Fetching the latest snapshots using SnapshotManager...")
            market_data_snapshot, order_management_snapshot = await self.snapshots_manager.get_snapshots()

            # Log the fetched snapshots for debugging
            print(f"Fetched market_data_snapshot: {market_data_snapshot}")
            print(f"Fetched order_management_snapshot: {order_management_snapshot}")

            if not market_data_snapshot or not order_management_snapshot:
                raise ValueError("Snapshots are empty or not initialized.")
            if not isinstance(market_data_snapshot, dict) or not isinstance(order_management_snapshot, dict):
                raise ValueError("Snapshots are not valid dictionaries.")

            return market_data_snapshot, order_management_snapshot
        except ValueError as ve:
            self.log_manager.error(f"Invalid snapshot data: {ve}", exc_info=True)
            return {}, {}
        except Exception as e:
            self.log_manager.error(f"Error refreshing trade data: {e}", exc_info=True)
            return {}, {}

    async def load_initial_data(self):
        try:

            """PART I: Data Gathering"""
            self.start_time = time.time()
            print('Part I: Data Gathering and Database Loading - Start Time:', datetime.datetime.now())
            # Connection check
            if not self.database_session_mngr.database.is_connected:
                await self.database_session_mngr.connect()

            market_data_manager = await MarketDataUpdater.get_instance(
                ticker_manager=self.ticker_manager,
                log_manager=self.log_manager
            )

            # Use already initialized market_data and order_management
            print(f"Using preloaded market_data and order_management.")


            # Set ticker_cache, market_cache, and other relevant data
            self.ticker_cache = self.market_data['ticker_cache'] # supported markets filtered by volume
            self.market_cache_usd = self.market_data['usd_pairs_cache'] # all supported usd pairs
            self.market_cache_vol = self.market_data['filtered_vol'] # all usd pairs filtered by min volume
            self.current_prices = self.market_data['current_prices'] # all usd supported markets
            self.filtered_balances = self.order_management['non_zero_balances'] # assets with greater than .01 balance
            self.min_volume = round(Decimal(self.market_data['avg_quote_volume']),0)
            self.shared_utils_print.print_elapsed_time(self.start_time, 'Part Ia: Market data update is complete')

            self.portfolio_manager.set_trade_parameters(self.start_time,self.market_data, self.order_management)
            self.order_manager.set_trade_parameters(self.start_time, self.market_data, self.order_management, self.web_url)
            self.market_manager.set_trade_parameters(self.start_time, self.market_data,self.order_management)
            self.webhook.set_trade_parameters(self.start_time, self.market_data, self.web_url) # SenderwebHook

            self.profit_manager.set_trade_parameters(self.start_time, self.market_data, self.web_url)

            self.profit_data_manager.set_trade_parameters(self.market_data, self.order_management, self.start_time)
            self.trading_strategy.set_trade_parameters(self.start_time, self.market_data)
            self.profit_helper.set_trade_parameters(self.start_time, self.market_data, self.web_url)
            self.database_session_mngr.set_trade_parameters(self.start_time, self.market_data, self.order_management)
            self.database_ops.set_trade_parameters(self.start_time, self.market_data, self.order_management)
            self.holdings_processor.set_trade_parameters(self.start_time, self.market_data, self.order_management)

            await self.database_session_mngr.process_data(self.start_time)

            self.shared_utils_print.print_elapsed_time(self.start_time, 'Part Ie: Database Loading is complete, session closed')

        except Exception as e:
            self.log_manager.error(f'Failed to initialize data on startup {e}', exc_info=True)
        finally:
            await self.exchange.close()
            TradeBot._exchange_instance_count -= 1
            print(f"Exchange instance closed. Total instances: {TradeBot._exchange_instance_count}")

    async def load_bot_components(self):
        """ Initialize all components required by the TradeBot. """

        # Step 1: Initialize essential components first
        log_config = {"log_level": logging.INFO}
        self.sighook_logger = LoggerManager(log_config) # Assign the logger
        self.log_manager = self.sighook_logger.get_logger('sighook_logger')
        self.exchange = self.setup_exchange()
        self.alerts = AlertSystem.get_instance(self.log_manager)
        self.ccxt_api = ApiManager.get_instance(self.exchange, self.log_manager, self.alerts)
        self.async_func = AsyncFunctions()

        self.sharded_utils = PrintData.get_instance(self.log_manager)
        self.db_tables = DatabaseTables()

        # Step 2: Initialize database session manager with dependencies initialized in __main__ TradeBot
        self.database_session_mngr = self.shared_data_manager.database_session_manager

        # Step 3: Initialize the remaining components that do not depend on DatabaseOpsManager
        self.shared_utils_print = PrintData.get_instance(self.log_manager)
        self.shared_utils_debugger = Debugging()
        self.shared_utils_precision = PrecisionUtils.get_instance(self.log_manager)
        self.shared_utils_datas_and_times = DatesAndTimes.get_instance(self.log_manager)
        self.shared_utils_utility = SharedUtility.get_instance(self.log_manager)
        self.indicators = Indicators(self.log_manager)
        self.snapshot_manager = SnapshotsManager.get_instance( self.shared_data_manager, self.log_manager)

        self.portfolio_manager = PortfolioManager.get_instance(self.log_manager, self.ccxt_api, self.exchange,
                                                  self.max_concurrent_tasks, self.shared_utils_precision,
                                                  self.shared_utils_datas_and_times, self.shared_utils_utility, )

        self.ticker_manager =  await TickerManager.get_instance(self.shared_utils_debugger, self.shared_utils_print,
                                                       self.log_manager, self.rest_client, self.portfolio_uuid, self.exchange, self.ccxt_api)

        self.database_utility = DatabaseIntegrity.get_instance( self.app_config, self.db_tables, self.log_manager)

        # Step 4: Now initialize csv_manager and profit_extras so that they can be passed to DatabaseOpsManager

        self.profit_data_manager = ProfitDataManager.get_instance(self.shared_utils_precision, self.shared_utils_print,
                                                       self.log_manager)

        # Step 5: Initialize holdings_processor
        self.holdings_processor = HoldingsProcessor.get_instance(self.log_manager, self.profit_data_manager)

        # Step 6: Initialize DatabaseOpsManager with all dependencies
        self.database_ops = DatabaseOpsManager.get_instance(
            self.exchange, self.ccxt_api, self.log_manager, self.profit_extras, self.portfolio_manager,
            self.holdings_processor, self.database_session_mngr.database, self.db_tables, self.profit_data_manager,
            self.snapshots_manager)

        # Step 7: Now that DatabaseOpsManager is initialized, update the placeholders in csv_manager and profit_extras

        # Step 8: Update DatabaseSessionManager with initialized components
        self.database_session_mngr.database_ops = self.database_ops
        self.database_session_mngr.profit_extras = self.profit_extras

        # Step 9: Initialize remaining components that depend on DatabaseOps and other managers
        #self.db_initializer = DatabaseInitializer(self.database_session_mngr)
        self.webhook = SenderWebhook.get_instance(self.exchange, self.alerts, self.log_manager, self.shared_utils_utility)

        self.trading_strategy = TradingStrategy.get_instance(self.webhook, self.ticker_manager, self.exchange, self.alerts,
                                                self.log_manager, self.ccxt_api, None, self.max_concurrent_tasks,
                                                self.database_session_mngr, self.shared_utils_print, self.db_tables,
                                                self.shared_utils_precision)

        self.profit_helper = ProfitHelper.get_instance(self.portfolio_manager, self.ticker_manager,
                                                      self.database_session_mngr, self.log_manager, self.profit_data_manager)

        self.order_manager = OrderManager.get_instance(self.trading_strategy, self.ticker_manager, self.exchange,
                                                      self.webhook, self.alerts, self.log_manager, self.ccxt_api,
                                                       self.profit_helper, self.shared_utils_precision,
                                                       self.max_concurrent_tasks)


        # Step 11: Initialize MarketManager
        self.market_manager = MarketManager.get_instance(self.tradebot, self.exchange, self.order_manager,
                                                         self.trading_strategy, self.log_manager, self.ccxt_api,
                                                         self.ticker_manager, self.portfolio_manager,
                                                         self.max_concurrent_tasks, self.database_session_mngr.database,
                                                         self.db_tables)

        self.market_data_manager = await MarketDataUpdater.get_instance(ticker_manager=self.ticker_manager,
                                                                  log_manager=self.log_manager)

        # Step 12: Initialize ProfitDataManager last, after all other dependencies are set
        self.profit_manager = ProfitabilityManager.get_instance(self.exchange, self.ccxt_api, self.portfolio_manager,
                                                   self.holdings_processor, self.database_ops, self.order_manager,
                                                   self.trading_strategy, self.profit_helper, self.profit_extras,
                                                   self.log_manager)

        print(f"TradeBot:load_bot_components() loaded successfully.")

    async def start(self):
        """ Start the bot after initialization. """
        try:
            async with aiohttp.ClientSession() as self.http_session:
                await self.async_init()
                await self.run_bot()
        except Exception as e:
            self.log_manager.error(f"Failed to start the bot: {e}", exc_info=True)
        finally:
            if self.database_session_mngr and self.database_session_mngr.database.is_connected:
                await self.database_session_mngr.disconnect()
            print("Program has exited.")

    async def run_bot(self):  # async

        # Fetch snapshots using the shared instance
       #self.market_data, self.order_management = await self.shared_data_manager.get_snapshots()
        profit_data = pd.DataFrame(columns=['Symbol', 'Unrealized PCT', 'Profit/Loss', 'Total Cost', 'Current Value',
                                            'Balance'])
        # ledger = pd.DataFrame()  # Holds all trades and shows profitability of all trades
        open_orders = pd.DataFrame()
        ohlcv_data_dict = {}  # dictionary to hold ohlcv data


        try:
            loop = asyncio.get_running_loop()  # Get the correct running loop
            while not AsyncFunctions.shutdown_event.is_set():
                print(f"<", "-" * 160, ">")
                print(f"Starting new bot iteration at {datetime.datetime.now()}")
                print(f"<", "-" * 160, ">")
                # PART II:
                #   Trade Database Updates and Portfolio Management
                print(f'Part II: Trade Database Updates and Portfolio Management - Start Time:', datetime.datetime.now())
                (holdings_list, usd_coins, buy_sell_matrix, price_change) = \
                    self.portfolio_manager.get_portfolio_data(self.start_time)

                # Filter to include only coins that have a buy or sell signal, all others omitted.
                # filtered_ticker_cache -> dataframe type
                filtered_ticker_cache = self.portfolio_manager.filter_ticker_cache_matrix(buy_sell_matrix)

                self.shared_utils_print.print_elapsed_time(self.start_time, 'Part II: Trade Database Updates/Portfolio Management')

                # PART III:
                #   Order cancellation and Data Collection
                print(f"Exchange instance. Total instances: {TradeBot._exchange_instance_count}")
                print(f'Part III: Order cancellation and OHLCV Data Collection - Start Time:', datetime.datetime.now())

                if filtered_ticker_cache is not None and not filtered_ticker_cache.empty:
                    # Step 1: Get open orders (if needed for Part III logic)
                    open_orders = await self.order_manager.get_open_orders()
                    # self.market_manager.utility.print_elapsed_time(self.market_manager.start_time, 'open_orders')

                    # Step 2: Initialize or update OHLCV data
                    if filtered_ticker_cache is not None and not filtered_ticker_cache.empty:
                        symbols = filtered_ticker_cache['symbol'].unique().tolist()

                        # Check if OHLCV data is initialized
                        if not await self.database_session_mngr.check_ohlcv_initialized():
                            await self.market_manager.fetch_and_store_ohlcv_data(symbols, mode='initialize')
                        else:
                            await self.market_manager.fetch_and_store_ohlcv_data(symbols, mode='update')

                    self.shared_utils_print.print_elapsed_time(self.market_manager.start_time,
                                                              'Part III: Order cancellation and OHLCV Data Collection')
                else:
                    print("No coins to trade. Skipping Part III.")


                # PART IV:
                # Trading Strategies
                print(f'Part IV: Trading Strategies - Start Time:', datetime.datetime.now())
                strategy_results, buy_sell_matrix = await self.trading_strategy.process_all_rows(filtered_ticker_cache,
                                                                                        buy_sell_matrix, open_orders)

                self.shared_utils_print.print_elapsed_time(self.start_time, 'Part IV: Trading Strategies')

                # PART V:
                # Order Execution
                print(f'Part V: Order Execution based on Market Conditions - Start Time:', datetime.datetime.now())
                submitted_orders = await self.order_manager.execute_actions(strategy_results, holdings_list)

                self.shared_utils_print.print_elapsed_time(self.start_time, 'Part V: Order Execution')

                # PART VI:
                # Profitability Analysis and Order Generation update holdings db

                print(f'Part VI: Profitability Analysis and Order Generation - Start Time:', datetime.datetime.now())
                aggregated_df = await self.profit_manager.update_and_process_holdings(self.start_time, open_orders,
                                                                                                   holdings_list)

                self.shared_utils_print.print_elapsed_time(self.start_time, 'Part VI: Profitability Analysis and Order Generation')
                if self.exchange is not None:
                    await self.exchange.close()

                self.shared_utils_print.print_data(self.min_volume, open_orders, buy_sell_matrix, submitted_orders,
                                                   aggregated_df)

                total_time = self.shared_utils_print.print_elapsed_time(self.start_time, 'load bot components')

                if total_time < int(self.sleep_time):
                    await asyncio.sleep(int(self.sleep_time) - total_time)
                else:
                    await asyncio.sleep(int(0))

                self.start_time = time.time()  # rest start time for next iteration.
                print('Part I: Data Gathering and Database Loading - Start Time:', self.start_time)

                # print(f'{self.market_data.get("ticker_cache").to_string(index=False)}')
                # open_orders = await self.order_manager.get_open_orders()
                await self.shared_data_manager.refresh_shared_data()

                market_data, order_management = await self.shared_data_manager.initialize_shared_data()

                self.market_data = market_data  # Store explicitly in TradeBot if needed
                # print(f'{self.market_data.get("ticker_cache").to_string(index=False)}')

                self.order_management = order_management


                # Check and update trailing stop orders and prepare webhook signal
                # this function is now performed with webhook using websockets
                # await self.order_manager.check_prepare_trailing_stop_orders(open_orders, current_prices)

                (holdings_list, _, _, _) = self.portfolio_manager.get_portfolio_data(self.start_time)


                #await self.database_session_mngr.process_data(self.start_time)
                self.shared_utils_print.print_elapsed_time(self.start_time, 'Part I: Data Gathering and Database Loading is complete, '
                                                                 'session closed')

                # break  # debugging for cprofile

                # if need_to_refresh_session():
                #     await self.refresh_session()
                #  debugging remove when done testing
                # if AsyncFunctions.shutdown_event.is_set():
                #     await AsyncFunctions.shutdown(asyncio.get_running_loop(), database_manager=self.database_session_mngr,
                #                                   http_session=self.http_session)


        except KeyboardInterrupt:
            print("Program interrupted, exiting...")
            self.save_data_on_exit(profit_data)
        except asyncio.CancelledError:
            self.save_data_on_exit(profit_data)
        except Exception as e:
            self.log_manager.error(f"Error in main loop: {e}", exc_info=True)
            await self.exchange.close()  # close the exchange connection
            TradeBot._exchange_instance_count -= 1  # debug
        finally:
            await self.exchange.close()
            TradeBot._exchange_instance_count -= 1
            if AsyncFunctions.shutdown_event.is_set():
                await AsyncFunctions.shutdown(asyncio.get_running_loop(), http_session=self.http_session)
            print("Program has exited.")

    def save_data_on_exit(self, profit_data):
        pass


if __name__ == "__main__":
    # Initialize the logger
    log_manager = LoggerManager({"log_level": "INFO"})
    logger = log_manager.get_logger("sighook_logger")

    # Initialize the database session manager
    database_session_manager = DatabaseSessionManager(None, logger)

    # ✅ Use get_instance() instead of manually instantiating
    shared_data_manager = SharedDataManager.get_instance(logger, database_session_manager)

    # ✅ Ensure SnapshotsManager is properly initialized
    snapshot_manager = SnapshotsManager.get_instance(shared_data_manager, logger)

    async def main():
        await shared_data_manager.initialize()
        bot = TradeBot(shared_data_manager, log_manager)
        await bot.start()

    asyncio.run(main())



    #debugging<><><><><><><><><><><>
    # profiler.stop()
    # print(profiler.output_text(unicode=True, color=True))
