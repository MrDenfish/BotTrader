import asyncio
import datetime
import time
from decimal import Decimal

import aiohttp
import pandas as pd

from Api_manager.api_manager import ApiManager
from Config.config_manager import CentralConfig as bot_config
from MarketDataManager.market_data_manager import MarketDataUpdater
from MarketDataManager.market_manager import MarketManager
from MarketDataManager.ticker_manager import TickerManager
from ProfitDataManager.profit_data_manager import ProfitDataManager
from Shared_Utils.alert_system import AlertSystem
from Shared_Utils.database_checker import DatabaseIntegrity
from Shared_Utils.dates_and_times import DatesAndTimes
from Shared_Utils.debugger import Debugging
from Shared_Utils.precision import PrecisionUtils
from Shared_Utils.print_data import PrintData
from Shared_Utils.snapshots_manager import SnapshotsManager
from Shared_Utils.utility import SharedUtility
from sighook.alerts_msgs_webhooks import SenderWebhook
from sighook.async_functions import AsyncFunctions
from database_manager.database_ops import DatabaseOpsManager
from TableModels.database_table_models import DatabaseTables
from sighook.holdings_process_manager import HoldingsProcessor
from sighook.indicators import Indicators
from sighook.order_manager import OrderManager
from sighook.portfolio_manager import PortfolioManager
from sighook.profit_manager import ProfitabilityManager
from sighook.trading_strategy import TradingStrategy

# from pyinstrument import Profiler # debugging

# Event to signal that a shutdown has been requested
shutdown_event = asyncio.Event()


class TradeBot:
    _exchange_instance_count = 0

    def __init__(self, shared_data_mgr, rest_client, portfolio_uuid, exchange, logger_manager=None, websocket_helper=None, shared_utils_debugger=None,
                 shared_utils_print=None):
        self.shared_data_manager = shared_data_mgr
        self.websocket_helper = websocket_helper
        self.app_config = bot_config()
        self.rest_client = rest_client
        self.portfolio_uuid = portfolio_uuid
        # Logger injection
        self.logger_manager = logger_manager  # 🙂
        self.logger = logger_manager.loggers['sighook_logger']  # 🙂

        self.database_session_mngr = shared_data_mgr.database_session_manager
        if not self.app_config._is_loaded:
            self.app_config._load_configuration()  # Ensure config is fully loaded
        # self.cb_api = self.app_config.load_sighook_api_key() # moved to main.py
        self.exchange = exchange
        self._csv_dir = self.app_config.csv_dir
        self.tradebot = self.order_management = self.market_data = self.precision_utils = None
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
        self._is_initialized = False

    def initialize_components(self):
        """Initialize all components to None to ensure proper cleanup."""
        self.database_ops = None
        self.db_initializer = None

    @property
    def market_data_new(self):
        return self.shared_data_manager.market_data

    @property
    def order_management_new(self):
        return self.shared_data_manager.order_management

    @property
    def ticker_cache_new(self):
        return self.shared_data_manager.market_data.get('ticker_cache')

    @property
    def min_volume_new(self):
        return round(Decimal(self.shared_data_manager.market_data.get('avg_quote_volume', 0)), 0)

    async def async_init(self, validate_startup_data: bool = False, shared_utils_debugger=None, shared_utils_print=None):
        """Initialize bot components asynchronously.

        Args:
            validate_startup_data (bool): If True, validate and attempt to recover startup snapshot data.
            shared_utils_debugger: Debugging utility instance.
            shared_utils_print: Print utility instance.
        """

        # ✅ Ensure SnapshotsManager is correctly retrieved as a singleton
        self.snapshots_manager = SnapshotsManager.get_instance(self.shared_data_manager, self.logger_manager)

        # ✅ Assign shared_utils if provided
        self.shared_utils_debugger = shared_utils_debugger or getattr(self, 'shared_utils_debugger', None)
        self.shared_utils_print = shared_utils_print or getattr(self, 'shared_utils_print', None)

        # ✅ Ensure shared_utils are defined before using them
        if not self.shared_utils_debugger or not self.shared_utils_print:
            raise AttributeError("TradeBot is missing shared_utils_debugger or shared_utils_print. Please provide them during initialization.")

        # ✅ Then load components that might use this data
        await self.load_bot_components()
        # ✅ Load core components required for validation or data refresh
        self.ticker_manager = await TickerManager.get_instance(
            self.app_config,
            self.shared_utils_debugger,
            self.shared_utils_print,
            self.logger_manager,
            self.rest_client,
            self.portfolio_uuid,
            self.exchange,
            self.ccxt_api,
            self.shared_data_manager,
            self.shared_utils_precision
        )

        # ✅ Validate and load shared data
        if validate_startup_data:
            await self.shared_data_manager.validate_startup_state(self.ticker_manager)

        market_data, order_management = await self.shared_data_manager.initialize_shared_data()


        # ✅ Final data prep
        await self.load_initial_data()
        self._is_initialized = True

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
            self.logger.error(f"❌Invalid snapshot data: {ve}", exc_info=True)
            return {}, {}
        except Exception as e:
            self.logger.error(f" ❌ Error refreshing trade data: {e}", exc_info=True)
            return {}, {}

    async def load_initial_data(self):
        """ Reads centralized market_data and order_management via shared_data_manager
            Assigns local aliases (e.g., ticker_cache_new) for runtime usage
            Passes these values to helper classes like portfolio, order, and market managers via set_trade_parameters()"""


        try:

            """PART I: Data Gathering"""
            self.start_time = time.time()
            print('Part I: Data Gathering and Database Loading - Start Time:', datetime.datetime.now())
            # Connection check
            if not self.database_session_mngr.database.is_connected:
                await self.database_session_mngr.connect()

            market_data_manager = await MarketDataUpdater.get_instance(
                ticker_manager=self.ticker_manager,
                logger_manager=self.logger
            )

            # Use already initialized market_data and order_management
            print(f"Using preloaded market_data and order_management.")

            # Set ticker_cache, market_cache, and other relevant data
            # await self.database_session_mngr.process_data()

            self.shared_utils_print.print_elapsed_time(self.start_time, 'Part Ie: Database Loading is complete, session closed')

        except Exception as e:
            self.logger.error(f'❌ Failed to initialize data on startup {e}', exc_info=True)
        finally:
            if hasattr(self.exchange, 'close') and callable(getattr(self.exchange, 'close')):
                await self.exchange.close()

            if self._is_initialized:
                TradeBot._exchange_instance_count -= 1
            print(f"Exchange instance closed. Total instances: {TradeBot._exchange_instance_count}")

    async def load_bot_components(self):
        """Initialize all components required by the TradeBot."""

        # Core Utilities
        self.alerts = AlertSystem.get_instance(self.logger_manager)
        self.ccxt_api = ApiManager.get_instance(self.exchange, self.logger_manager, self.alerts)
        self.shared_utils_debugger = Debugging()
        self.shared_utils_precision = PrecisionUtils.get_instance(self.logger_manager, self.shared_data_manager)
        self.shared_utils_datas_and_times = DatesAndTimes.get_instance(self.logger_manager)
        self.shared_utils_utility = SharedUtility.get_instance(self.logger_manager)
        # self.sharded_utils = PrintData.get_instance(self.logger)
        self.shared_utils_print = PrintData.get_instance(self.logger_manager, self.shared_utils_utility)


        self.async_func = AsyncFunctions()

        self.db_tables = DatabaseTables()

        self.database_session_mngr = self.shared_data_manager.database_session_manager

        self.indicators = Indicators(self.logger)
        self.snapshot_manager = SnapshotsManager.get_instance(self.shared_data_manager, self.logger_manager)

        self.portfolio_manager = PortfolioManager.get_instance(
            self.logger, self.ccxt_api, self.exchange,
            self.max_concurrent_tasks, self.shared_utils_precision,
            self.shared_utils_datas_and_times, self.shared_utils_utility, self.shared_data_manager
        )

        self.ticker_manager = await TickerManager.get_instance(
            self.app_config, self.shared_utils_debugger, self.shared_utils_print,
            self.logger_manager, self.rest_client, self.portfolio_uuid, self.exchange, self.ccxt_api,
            self.shared_data_manager, self.shared_utils_precision
        )

        self.database_utility = DatabaseIntegrity.get_instance(self.app_config, self.db_tables, self.logger_manager)

        self.profit_data_manager = ProfitDataManager.get_instance(
            self.shared_utils_precision, self.shared_utils_print,
            self.shared_data_manager, self.logger_manager
        )

        self.holdings_processor = HoldingsProcessor.get_instance(self.logger, self.profit_data_manager,
                                                                 self.shared_utils_precision, self.shared_data_manager)

        self.database_ops = DatabaseOpsManager.get_instance(
            self.exchange, self.ccxt_api, self.logger, self.profit_extras, self.portfolio_manager,
            self.holdings_processor, self.database_session_mngr.database, self.db_tables, self.profit_data_manager,
            self.snapshot_manager, self.shared_utils_precision, self.shared_data_manager
        )

        self.database_session_mngr.database_ops = self.database_ops
        self.database_session_mngr.profit_extras = self.profit_extras

        self.webhook = SenderWebhook.get_instance(
            self.exchange, self.alerts, self.logger, self.shared_utils_utility,
            self.web_url, self.shared_data_manager
        )

        self.trading_strategy = TradingStrategy.get_instance(
            self.webhook, self.ticker_manager, self.exchange, self.alerts,
            self.logger, self.ccxt_api, None, self.max_concurrent_tasks,
            self.database_session_mngr, self.shared_utils_print, self.db_tables,
            self.shared_utils_precision, self.shared_data_manager
        )

        self.order_manager = OrderManager.get_instance(
            self.trading_strategy, self.ticker_manager, self.exchange,
            self.webhook, self.alerts, self.logger, self.ccxt_api,
            self.shared_utils_precision, self.shared_data_manager,
            self.web_url, self.max_concurrent_tasks,
        )

        self.market_manager = MarketManager.get_instance(
            self.tradebot, self.exchange, self.order_manager, self.trading_strategy,
            self.logger_manager, self.ccxt_api, self.ticker_manager, self.portfolio_manager,
            self.max_concurrent_tasks, self.database_session_mngr.database, self.db_tables,
            self.shared_data_manager
        )

        self.market_data_updater = await MarketDataUpdater.get_instance(
            ticker_manager=self.ticker_manager,
            logger_manager=self.logger_manager,
            websocket_helper=self.websocket_helper,
            shared_data_manager=self.shared_data_manager
        )

        self.profit_manager = ProfitabilityManager.get_instance(
            self.exchange, self.ccxt_api, self.portfolio_manager, self.holdings_processor,
            self.database_ops, self.order_manager, self.trading_strategy,
            self.profit_data_manager, self.shared_data_manager, self.web_url, self.logger
        )

        print("✅ TradeBot:load_bot_components() completed successfully.")

    async def start(self):
        """ Start the bot after initialization. """
        try:
            async with aiohttp.ClientSession() as self.http_session:
                await self.async_init()
                await self.run_bot()
        except Exception as e:
            self.logger.error(f"❌Failed to start the bot: {e}", exc_info=True)
        finally:
            if self.database_session_mngr and self.database_session_mngr.database.is_connected:
                await self.database_session_mngr.disconnect()
            print("Program has exited.")

    async def run_bot(self):  # async
        # Fetch snapshots using the shared instance
        profit_data = pd.DataFrame(columns=['Symbol', 'Unrealized PCT', 'Profit/Loss', 'Total Cost', 'Current Value',
                                            'Balance'])
        # ledger = pd.DataFrame()  # Holds all trades and shows profitability of all trades
        open_orders = pd.DataFrame()
        ohlcv_data_dict = {}  # dictionary to hold ohlcv data

        test_token = 0
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
                # print(f"Exchange instance. Total instances: {TradeBot._exchange_instance_count}")# debug
                print(f'Part III: Order cancellation and OHLCV Data Collection - Start Time:', datetime.datetime.now())
                if filtered_ticker_cache is not None and not filtered_ticker_cache.empty:
                    # Step 1: Get open orders (if needed for Part III logic)
                    open_orders = await self.order_manager.get_open_orders()

                    if open_orders is None or open_orders.empty:  # debug
                        print("No open orders found.")

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
                print(f'Part V: Order Execution based on Market conditions - Start Time:', datetime.datetime.now())
                submitted_orders = await self.order_manager.execute_actions(strategy_results, holdings_list)

                self.shared_utils_print.print_elapsed_time(self.start_time, 'Part V: Order Execution')

                # PART VI:
                # Profitability Analysis and Order Generation update holdings db

                print(f'Part VI: Profitability Analysis and Order Generation - Start Time:', datetime.datetime.now())
                aggregated_df = await self.profit_manager.update_and_process_holdings(self.start_time, open_orders)

                self.shared_utils_print.print_elapsed_time(self.start_time, 'Part VI: Profitability Analysis and Order Generation')
                if self.exchange is not None:
                    if hasattr(self.exchange, 'close') and callable(getattr(self.exchange, 'close')):
                        await self.exchange.close()

                if open_orders is None or open_orders.empty:  # debug
                    print("No open orders found.")

                self.shared_utils_print.print_data(self.min_volume_new, open_orders, buy_sell_matrix, submitted_orders,
                                                   aggregated_df)

                total_time = self.shared_utils_print.print_elapsed_time(self.start_time, 'load bot components')

                if total_time < int(self.sleep_time):
                    await asyncio.sleep(int(self.sleep_time) - total_time)
                else:
                    await asyncio.sleep(int(0))

                self.start_time = time.time()  # rest start time for next iteration.
                print('Part I: Data Gathering and Database Loading - Start Time:', self.start_time)

                self.market_data, self.order_management = await self.shared_data_manager.refresh_shared_data()

                (holdings_list, _, _, _) = self.portfolio_manager.get_portfolio_data(self.start_time)

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
            self.logger.error(f"❌Error in main loop: {e}", exc_info=True)
            await self.exchange.close()  # close the exchange connection
            if self._is_initialized:
                TradeBot._exchange_instance_count -= 1  # debug
        finally:
            await self.exchange.close()
            if self._is_initialized:
                TradeBot._exchange_instance_count -= 1
            if AsyncFunctions.shutdown_event.is_set():
                await AsyncFunctions.shutdown(asyncio.get_running_loop(), http_session=self.http_session)
            print("Program has exited.")

    def save_data_on_exit(self, profit_data):
        pass
