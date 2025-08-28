
import argparse
import asyncio
import logging
import os
import signal
import time

import aiohttp
from decimal import Decimal
from aiohttp import web
from TestDebugMaintenance.trade_record_maintenance import run_maintenance_if_needed
from AccumulationManager.accumulation_manager import AccumulationManager
from Shared_Utils.scheduler import periodic_runner
from Config.config_manager import CentralConfig as Config
# loaded in main() to avoid circular import
#from Api_manager.coinbase_api import CoinbaseAPI
#from MarketDataManager.ticker_manager import TickerManager
#from MarketDataManager.webhook_order_book import OrderBookManager
from MarketDataManager.market_data_manager import market_data_watchdog
from MarketDataManager.market_data_manager import MarketDataUpdater
from MarketDataManager.passive_order_manager import PassiveOrderManager
from MarketDataManager.asset_monitor import AssetMonitor
from SharedDataManager.shared_data_manager import SharedDataManager, CustomJSONDecoder
from Shared_Utils.alert_system import AlertSystem
from TestDebugMaintenance.debugger import Debugging
from Shared_Utils.exchange_manager import ExchangeManager
from Shared_Utils.logging_manager import LoggerManager
from Shared_Utils.print_data import PrintData
from Shared_Utils.print_data import ColorCodes
from Shared_Utils.precision import PrecisionUtils
from Shared_Utils.snapshots_manager import SnapshotsManager
from Shared_Utils.utility import SharedUtility
from database_manager.database_session_manager import DatabaseSessionManager
from sighook.sender import TradeBot
from webhook.listener import WebSocketManager, WebhookListener
from webhook.websocket_helper import WebSocketHelper
from webhook.websocket_market_manager import WebSocketMarketManager

shutdown_event = asyncio.Event()


def is_docker_env():
    return os.getenv("DOCKER_ENV", "false").lower() == "true"


# Force singleton initialization across all environments
_ = Config(is_docker=is_docker_env())
print("✅ CentralConfig preloaded:")
print(f"   DB: {_.machine_type}@{_.db_host}/{_.db_name}")

async def load_config():
    return Config(is_docker=is_docker_env())

async def preload_market_data(logger_manager, shared_data_manager, market_data_updater, ticker_manager ):
    logger = logger_manager.get_logger("shared_logger")
    try:

        logger.info("⏳ Checking startup snapshot state...")

        market_data, order_mgmt = await shared_data_manager.validate_startup_state(market_data_updater,ticker_manager)
        # ✅ Explicitly assign to shared_data_manager
        shared_data_manager.market_data = market_data or {}
        shared_data_manager.order_management = order_mgmt or {}
        print(f"✅ Market data preloaded successfully with data from the database. preload:{list(shared_data_manager.market_data.keys())}")
        return market_data, order_mgmt
    except Exception as e:
        logger.error(f"❌ Failed to preload market/order data: {e}", exc_info=True)
        raise


async def graceful_shutdown(listener, runner):
    if hasattr(listener, 'shutdown'):
        await listener.shutdown()
    if hasattr(listener, 'market_data_manager'):
        market_ws_manager = listener.market_data_manager
        if hasattr(market_ws_manager, 'shutdown'):
            await market_ws_manager.shutdown()
    await runner.cleanup()
    shutdown_event.set()

async def init_shared_data(config, logger_manager, shared_logger, coinbase_api):
    shared_data_manager = SharedDataManager.__new__(SharedDataManager)
    custom_json_decoder = CustomJSONDecoder
    database_session_manager = DatabaseSessionManager(
        config=config,
        profit_extras=None,
        logger_manager=shared_logger,
        shared_data_manager=shared_data_manager,
        custom_json_decoder=custom_json_decoder,
    )



    # Initialize utilities

    shared_utils_debugger = Debugging()
    shared_utils_precision = PrecisionUtils.get_instance(logger_manager, shared_data_manager)
    snapshot_manager = SnapshotsManager.get_instance(shared_data_manager, shared_utils_precision,
                                                     shared_logger)
    shared_utils_utility = SharedUtility.get_instance(logger_manager)
    shared_utils_print = PrintData.get_instance(logger_manager, shared_utils_utility)
    shared_utils_color = ColorCodes.get_instance()
    shared_data_manager.__init__(shared_logger, database_session_manager,
                                 shared_utils_utility, shared_utils_precision, coinbase_api=coinbase_api)

    shared_data_manager.inject_maintenance_callback()

    # Set attributes on the shared data manager
    shared_data_manager.snapshot_manager = snapshot_manager
    shared_data_manager.shared_utils_debugger = shared_utils_debugger
    shared_data_manager.shared_utils_print = shared_utils_print
    shared_data_manager.shared_utils_color = shared_utils_color
    shared_data_manager.shared_utils_precision = shared_utils_precision

    await shared_data_manager.initialize()

    return shared_data_manager, shared_utils_debugger, shared_utils_print, shared_utils_color, shared_utils_utility, shared_utils_precision

async def build_websocket_components(config, listener, shared_data_manager):
    # --- NEW: pull latest maker / taker rates -------------
    fee_rates = await listener.coinbase_api.get_fee_rates()
    if "maker" not in fee_rates:  # API down?  Use a worst-case stub
        listener.logger.warning("⚠️  Using fallback fee tier 0.0020")
        fee_rates = {"maker": Decimal("0.0020"), "taker": Decimal("0.0025")}

    # ------------------------------------------------------
    passive_order_manager = PassiveOrderManager(
        config=config,
        ccxt_api=listener.ccxt_api,
        coinbase_api=listener.coinbase_api,
        exchange=listener.exchange,
        ohlcv_manager=listener.ohlcv_manager,
        shared_data_manager=shared_data_manager,
        shared_utils_color=listener.shared_utils_color,
        shared_utils_utility=listener.shared_utils_utility,
        shared_utils_precision=listener.shared_utils_precision,
        trade_order_manager=listener.trade_order_manager,
        order_manager=listener.order_manager,
        logger_manager=listener.logger_manager,
        min_spread_pct=config.min_spread_pct,  # 0.15 %, overrides default 0.20 %
        fee_cache=fee_rates,  # ← new
        # optional knobs ↓
        max_lifetime=90,  # cancel / refresh after 90 s
    )

    asset_monitor = AssetMonitor(
        listener=listener,
        logger=listener.logger,
        config=config,
        shared_data_manager=shared_data_manager,
        trade_order_manager=listener.trade_order_manager,
        order_manager=listener.order_manager,
        trade_recorder=shared_data_manager.trade_recorder,
        profit_data_manager=listener.profit_data_manager,
        order_book_manager=listener.order_book_manager,
        shared_utils_precision=listener.shared_utils_precision,
        shared_utils_color=listener.shared_utils_color,
        shared_utils_date_time=listener.shared_utils_date_time,
    )

    listener.asset_monitor = asset_monitor

    websocket_helper = WebSocketHelper(
        listener=listener,
        websocket_manager=None,
        logger_manager=listener.logger,
        coinbase_api=listener.coinbase_api,
        profit_data_manager=listener.profit_data_manager,
        order_type_manager=listener.order_type_manager,
        shared_utils_date_time=listener.shared_utils_date_time,
        shared_utils_print=listener.shared_utils_print,
        shared_utils_color=listener.shared_utils_color,
        shared_utils_precision=listener.shared_utils_precision,
        shared_utils_utility=listener.shared_utils_utility,
        shared_utils_debugger=listener.shared_utils_debugger,
        order_book_manager=listener.order_book_manager,
        snapshot_manager=listener.snapshot_manager,
        trade_order_manager=listener.trade_order_manager,
        shared_data_manager=shared_data_manager,
        market_ws_manager=None,
        database_session_manager=shared_data_manager.database_session_manager,
        passive_order_manager=passive_order_manager,
        asset_monitor=asset_monitor,


    )

    market_ws_manager = WebSocketMarketManager(
        listener=listener,
        exchange=listener.exchange,
        coinbase_api=listener.coinbase_api,
        ccxt_api=listener.ccxt_api,
        logger_manager=listener.logger,
        profit_data_manager=listener.profit_data_manager,
        order_type_manager=listener.order_type_manager,
        shared_utils_print=listener.shared_utils_print,
        shared_utils_color=listener.shared_utils_color,
        shared_utils_precision=listener.shared_utils_precision,
        shared_utils_utility=listener.shared_utils_utility,
        shared_utils_debugger=listener.shared_utils_debugger,
        order_book_manager=listener.order_book_manager,
        snapshot_manager=listener.snapshot_manager,
        trade_order_manager=listener.trade_order_manager,
        ohlcv_manager=listener.ohlcv_manager,
        shared_data_manager=shared_data_manager,
        database_session_manager=shared_data_manager.database_session_manager
    )
    market_ws_manager.passive_order_manager = passive_order_manager
    # 🔁 Restore any passive orders
    await passive_order_manager.reload_persisted_passive_orders()

    websocket_manager = WebSocketManager(
        config=Config(),
        listener=listener,
        coinbase_api=listener.coinbase_api,
        logger_manager=listener.logger,
        websocket_helper=websocket_helper
    )

    market_ws_manager.set_websocket_manager(websocket_manager)
    websocket_helper.market_ws_manager = market_ws_manager
    websocket_helper.websocket_manager = websocket_manager

    return websocket_helper, websocket_manager, market_ws_manager

async def refresh_loop(shared_data_manager, interval=30):
    """Continuously refresh shared data from the database."""
    while True:
        try:
            await shared_data_manager.refresh_shared_data()
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            print("Refresh loop cancelled")



async def run_sighook(config, shared_data_manager, market_data_updater, rest_client, portfolio_uuid, logger_manager, alert, order_book_manager,
                      shared_utils_debugger, shared_utils_print, shared_utils_color, startup_event=None, listener=None):

    if startup_event:
        await startup_event.wait()

    await shared_data_manager.initialize_shared_data()
    # ✅ Wait for webhook to populate shared data
    await shared_data_manager.wait_until_initialized()
    print(f"✅ Shared data is initialized. Proceeding with sighook setup.")

    websocket_helper = listener.websocket_helper if listener else None
    coinbase_api = listener.coinbase_api if listener else None
    exchange = config.exchange
    trade_bot = TradeBot(
        coinbase_api=coinbase_api,
        shared_data_mgr=shared_data_manager,
        shutdown_event=shutdown_event,
        trade_recorder=shared_data_manager.trade_recorder,
        market_data_updater=market_data_updater,
        rest_client=config.rest_client,
        portfolio_uuid=config.portfolio_uuid,
        exchange=exchange,
        order_book_manager=order_book_manager,
        logger_manager=logger_manager,
        shared_utils_debugger=shared_utils_debugger,
        shared_utils_print=shared_utils_print,
        shared_utils_color=shared_utils_color,
        websocket_helper=websocket_helper,

    )
    await trade_bot.async_init(validate_startup_data=False,
                               shared_utils_debugger=shared_utils_debugger,
                               shared_utils_print=shared_utils_print,
                               shared_utils_color=shared_utils_color
                               )

    sighook_logger = logger_manager.get_logger("sighook")

    # Start periodic refresh of shared data
    asyncio.create_task(refresh_loop(shared_data_manager, interval=60))

    try:
        while not shutdown_event.is_set():
            await trade_bot.run_bot()
            await asyncio.sleep(5)
    except Exception as e:
        sighook_logger.error("Unhandled exception in sighook:", exc_info=True)
        alert.callhome("sighook crashed", str(e), mode="email")

    finally:
        sighook_logger.info("sighook shutdown complete.")
        # ✅ Stop TradeRecorder worker at shutdown
        await shared_data_manager.trade_recorder.stop_worker()

async def create_trade_bot(config, coinbase_api, shared_data_manager, market_data_updater, order_book_manager, logger_manager,
                           shared_utils_debugger, shared_utils_print, shared_utils_color, websocket_helper=None) -> TradeBot:
    trade_bot = TradeBot(
        coinbase_api=coinbase_api,
        shared_data_mgr=shared_data_manager,
        shutdown_event=shutdown_event,
        trade_recorder=shared_data_manager.trade_recorder,
        market_data_updater = market_data_updater,
        rest_client=config.rest_client,
        portfolio_uuid=config.portfolio_uuid,
        exchange=config.exchange,
        order_book_manager=order_book_manager,
        logger_manager=logger_manager,
        shared_utils_debugger=shared_utils_debugger,
        shared_utils_print=shared_utils_print,
        shared_utils_color=shared_utils_color,
        websocket_helper=websocket_helper
    )
    await trade_bot.async_init(validate_startup_data=True,
                               shared_utils_debugger=shared_utils_debugger,
                               shared_utils_print=shared_utils_print,
                               shared_utils_color=shared_utils_color)
    return trade_bot


async def init_webhook(config, session, coinbase_api, shared_data_manager, market_data_updater, logger_manager,shared_utils_debugger,
                       shared_utils_print, shared_utils_color, alert, startup_event=None, trade_bot=None, order_book_manager=None):


    exchange = config.exchange

    listener = WebhookListener(
        bot_config=config,
        shared_data_manager=shared_data_manager,
        shutdown_event=shutdown_event,
        shared_utils_color=shared_utils_color,
        market_data_updater=market_data_updater,
        database_session_manager=shared_data_manager.database_session_manager,
        logger_manager=logger_manager,
        coinbase_api=coinbase_api,
        session=session,
        market_manager=None,
        exchange=config.exchange,
        alert=alert,
        order_book_manager=order_book_manager  # ✅ injected
    )
    listener.rest_client = config.rest_client
    listener.portfolio_uuid = config.portfolio_uuid

    if trade_bot is None:
        trade_bot = await create_trade_bot(
            config=config,
            coinbase_api=listener.coinbase_api,
            shared_data_manager=shared_data_manager,
            market_data_updater = market_data_updater,
            order_book_manager=order_book_manager,
            logger_manager=logger_manager,
            shared_utils_debugger=shared_utils_debugger,
            shared_utils_print=shared_utils_print,
            shared_utils_color=shared_utils_color,
            websocket_helper=listener.websocket_helper  # only if required
        )

    listener.market_manager = trade_bot.market_manager

    await listener.async_init()

    if listener.ohlcv_manager:
        listener.ohlcv_manager.market_manager = listener.market_manager

    listener.order_manager = trade_bot.order_manager

    websocket_helper, websocket_manager, market_ws_manager = await build_websocket_components(
        config, listener, shared_data_manager
    )
    listener.websocket_helper = websocket_helper
    listener.websocket_manager = websocket_manager

    listener.market_data_updater = await MarketDataUpdater.get_instance(
        listener.ticker_manager, logger_manager, websocket_helper=websocket_helper,
        shared_data_manager=shared_data_manager
    )
    listener.trade_order_manager.shared_data_mgr = shared_data_manager
    listener.trade_order_manager.websocket_helper = websocket_helper
    listener.market_data_manager = listener.market_data_updater
    listener.trade_order_manager.market_data_updater = listener.market_data_updater

    # Current data  from the exchange will be loaded, open orders are excluded at this point and time.
    #  await listener.sync_open_orders() # Not certain this is appropriate, the thought was that new open orders would be loaded here as
    #  apposed to uploading old data from the database in TickerManager.update_ticker_cache()
    await listener.market_data_updater.update_market_data(time.time())
    if not shared_data_manager.order_management.get("order_tracker"):
        logger.warning("⚠️ order_tracker is empty after startup — pulling fallback from REST")
        await listener.market_data_updater.update_market_data(time.time())

    # Start the watchdog
    asyncio.create_task(
        market_data_watchdog(
            shared_data_manager=shared_data_manager,
            listener=listener,
            logger=logger_manager.loggers["shared_logger"]
        )
    )

    if startup_event:
        startup_event.set()

    asyncio.create_task(websocket_manager.start_websockets())

    app = await listener.create_app()
    if app is None:
        raise RuntimeError("❌ listener.create_app() returned None — cannot start webhook.")
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', config.webhook_port)
    await site.start()

    print(f"✅ TradeBot is running on version: {config.program_version} ✅")
    print(f"👉 Webhook {config.program_version} is Listening on port {config.webhook_port} 👈\n")

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(graceful_shutdown(listener, runner)))

    return listener, websocket_manager, app, runner



async def run_webhook(config, session, coinbase_api, shared_data_manager, market_data_updater, logger_manager, alert, shared_utils_debugger,
                      shared_utils_print, shared_utils_color, order_book_manager, startup_event=None, ccxt_api=None, trade_bot=None):


    listener, websocket_manager, app, runner = await init_webhook(
        config=config,
        session=session,
        coinbase_api=coinbase_api,
        shared_data_manager=shared_data_manager,
        market_data_updater=market_data_updater,
        logger_manager=logger_manager,
        shared_utils_debugger=shared_utils_debugger,
        shared_utils_print=shared_utils_print,
        shared_utils_color=shared_utils_color,
        startup_event=startup_event,
        trade_bot=None,
        alert=alert,
        order_book_manager=order_book_manager  # ✅ pass it in
    )

    background_tasks = [
        asyncio.create_task(periodic_runner(listener.refresh_market_data, 30, name="Market Data Refresher")),
        asyncio.create_task(periodic_runner(listener.reconcile_with_rest_api, interval=300)),  # 5 minutes
        asyncio.create_task(listener.periodic_save(), name="Periodic Data Saver"),
        asyncio.create_task(listener.sync_open_orders(), name="TradeRecord Sync"),
    ]

    try:
        await shutdown_event.wait()
    except Exception as e:
        logger_manager.get_logger("shared_logger").error("Unhandled exception in webhook:", exc_info=True)
        if alert:
            alert.callhome("webhook crashed", str(e), mode="email")

    for task in background_tasks:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    # ✅ Stop TradeRecorder worker before final shutdown
    await shared_data_manager.trade_recorder.stop_worker()

    await graceful_shutdown(listener, runner)
    return listener

async def monitor_db_connections(shared_data_manager, interval=10, threshold=10):
    logger = shared_data_manager.logger
    db = shared_data_manager.database_session_manager

    while True:
        try:
            count = int(await db.get_active_connection_count())
            if count >= 0:
                if count >= threshold:
                    logger.warning(f"🚨 DB Connections High: {count} active (≥ {threshold})")
                else:
                    logger.info(f"🔎 DB Connections: {count}")
            else:
                logger.warning("⚠️ Unable to retrieve DB connection count.")
            await asyncio.sleep(interval)
        except Exception as e:
            logger.error(f"💥 monitor_db_connections error: {e} 💥", exc_info=True)


async def main():
    parser = argparse.ArgumentParser(description="Run the crypto trading bot components.")
    parser.add_argument('--run', choices=['sighook', 'webhook', 'both'], default='both')
    parser.add_argument(
        '--test',
        action='store_true',
        help="Run the bot in test mode (bypass balance validation, use dummy values)"
    )
    parser.add_argument(
        '--verbose',
        action='store_true',
        help="Enable detailed DEBUG logs to console"
    )
    parser.add_argument(
        '--fresh-start',
        action='store_true',
        help="Skip historical reconciliation and only track new live trades"
    )
    args = parser.parse_args()

    config = await load_config()
    config.test_mode = args.test  # ✅ Globally toggle test_mode            python main.py --run webhook --test
    log_config = {"log_level": logging.DEBUG if args.verbose else logging.INFO}
    logger_manager = LoggerManager(log_config)
    from Api_manager.coinbase_api import CoinbaseAPI
    from MarketDataManager.ticker_manager import TickerManager
    from MarketDataManager.webhook_order_book import OrderBookManager
    webhook_logger = logger_manager.get_logger("webhook_logger")
    sighook_logger = logger_manager.get_logger("sighook_logger")
    shared_logger = logger_manager.get_logger("shared_logger")

    alert = AlertSystem(logger_manager) if args.run != 'webhook' else None

    config.exchange = ExchangeManager.get_instance(config.load_webhook_api_key()).get_exchange()
    startup_event = asyncio.Event()

    try:
        async with (aiohttp.ClientSession() as session):

            shared_utils_utility = SharedUtility.get_instance(logger_manager)
            coinbase_api = CoinbaseAPI(session, shared_utils_utility, logger_manager, None)
            (shared_data_manager, shared_utils_debugger, shared_utils_print, shared_utils_color, shared_utils_utility,
             shared_utils_precision) = await init_shared_data(config, logger_manager, shared_logger, coinbase_api)

            coinbase_api.shared_utils_precision = shared_utils_precision

            await shared_data_manager.trade_recorder.start_worker()



            accumulation_manager = AccumulationManager(
                exchange=config.exchange,  # or coinbase_api if preferred
                logger_manager=logger_manager,
                shared_data_manager=shared_data_manager,
                shutdown_event=shutdown_event,
                accumulation_symbol="ETH-USD",
                signal_based_enabled=True,
                profit_based_enabled=False,  # stubbed for later
                profit_allocation_pct=0.5,
                accumulation_threshold=25.0,
                accumulation_amount_per_signal=25.0
            )

            # ✅ Optionally attach to shared_data_manager for global access
            shared_data_manager.accumulation_manager = accumulation_manager
            order_book_manager = OrderBookManager.get_instance(
                config.exchange,
                shared_data_manager,
                shared_data_manager.shared_utils_precision,
                logger_manager.get_logger("shared_logger"),
                ccxt_api=None  # Pass your existing ccxt_api if available
            )
            shared_logger.info(
                "🔎 Coinbase at T0: base=%s prefix=%s sandbox=%s key_len=%s sec_len=%s pp_len=%s",
                os.getenv("COINBASE_API_BASE_URL"),
                os.getenv("COINBASE_API_PREFIX"),
                os.getenv("COINBASE_USE_SANDBOX"),
                len(os.getenv("COINBASE_API_KEY", "")),
                len(os.getenv("COINBASE_API_SECRET", "")),
                len(os.getenv("COINBASE_API_PASSPHRASE", "")),
            )

            # One cheap authenticated preflight to load JWT and catch any startup race
            try:
                fee_summary = await coinbase_api.get_fee_rates()
                if "error" in (fee_summary or {}):
                    shared_logger.warning("Fee preflight returned an error (will continue): %s", fee_summary)
            except Exception as e:
                shared_logger.warning("Fee preflight exception (will retry later): %s", e)

            ticker_manager = TickerManager(
                config=config,
                coinbase_api=coinbase_api,
                shared_utils_debugger=shared_utils_debugger,
                shared_utils_print=shared_utils_print,
                shared_utils_color=shared_utils_color,
                logger_manager=logger_manager,
                order_book_manager=order_book_manager,
                rest_client=config.rest_client,
                portfolio_uuid=config.portfolio_uuid,
                exchange=config.exchange,
                ccxt_api=None,
                shared_data_manager=shared_data_manager,
                shared_utils_precision=shared_data_manager.shared_utils_precision
            )

            market_data_updater = await MarketDataUpdater.get_instance(
                ticker_manager=ticker_manager,
                logger_manager=logger_manager,
                websocket_helper=None,
                shared_data_manager=shared_data_manager
            )

            await preload_market_data(
                logger_manager=logger_manager,
                shared_data_manager=shared_data_manager,
                ticker_manager=ticker_manager,
                market_data_updater=market_data_updater  # ✅ pass it in
            )

            shared_utils_precision.set_trade_parameters()
            # ✅ One-time FIFO Debugging
            #await shared_data_manager.trade_recorder.test_performance_tracker()
            #await shared_data_manager.trade_recorder.test_fifo_prod("SPK-USD")

            await run_maintenance_if_needed(shared_data_manager, shared_data_manager.trade_recorder)



            if args.run == 'webhook':
                await run_webhook(
                    config=config,
                    session=session,
                    coinbase_api=coinbase_api,
                    shared_data_manager=shared_data_manager,
                    market_data_updater=market_data_updater,
                    logger_manager=logger_manager,
                    alert=alert,
                    shared_utils_debugger=shared_utils_debugger,
                    shared_utils_print=shared_utils_print,
                    shared_utils_color=shared_utils_color,
                    order_book_manager=order_book_manager
                )
            elif   args.run == 'sighook':
                await run_sighook(
                    config=config,
                    shared_data_manager=shared_data_manager,
                    market_data_updater=market_data_updater,
                    rest_client=config.rest_client,
                    portfolio_uuid=config.portfolio_uuid,
                    logger_manager=logger_manager,
                    alert=alert,
                    order_book_manager=None,
                    shared_utils_debugger=shared_utils_debugger,
                    shared_utils_print=shared_utils_print,
                    shared_utils_color=shared_utils_color
                )

            elif args.run == 'both':
                # ✅ Step 1: Start Webhook first and get the listener instance
                listener, websocket_manager, app, runner = await init_webhook(
                    config=config,
                    session=session,
                    coinbase_api=coinbase_api,
                    shared_data_manager=shared_data_manager,
                    market_data_updater=market_data_updater,
                    logger_manager=logger_manager,
                    shared_utils_debugger=shared_utils_debugger,
                    shared_utils_print=shared_utils_print,
                    shared_utils_color=shared_utils_color,
                    startup_event=startup_event,
                    trade_bot=None,
                    alert=alert,
                    order_book_manager=order_book_manager  # ✅ pass it in
                )

                # ✅ Step 2: Run sighook now that order_book_manager is available
                sighook_task = asyncio.create_task(run_sighook(
                    config=config,
                    shared_data_manager=shared_data_manager,
                    market_data_updater=market_data_updater,
                    rest_client=config.rest_client,
                    portfolio_uuid=config.portfolio_uuid,
                    logger_manager=logger_manager,
                    alert=alert,
                    order_book_manager=order_book_manager,
                    shared_utils_debugger=shared_utils_debugger,
                    shared_utils_print=shared_utils_print,
                    shared_utils_color=shared_utils_color,
                    startup_event=startup_event,
                    listener=listener
                ))

                # ✅ Step 3: Webhook background tasks
                background_tasks = [
                    asyncio.create_task(periodic_runner(listener.refresh_market_data, 30, name="Market Data Refresher")),
                    asyncio.create_task(periodic_runner(listener.reconcile_with_rest_api, interval=300)), # 5 minutes
                    asyncio.create_task(listener.sync_open_orders(), name="TradeRecord Sync"),
                    asyncio.create_task(listener.periodic_save(), name="Periodic Data Saver"),
                    asyncio.create_task(websocket_manager.start_websockets()),
                    asyncio.create_task(accumulation_manager.start_daily_runner())
                ]

                monitor_interval = int(config.db_monitor_interval or 10)
                threshold = int(config.db_connection_threshold or 10)
                db_monitor_task = asyncio.create_task(monitor_db_connections(shared_data_manager,
                                                                             interval=monitor_interval, threshold=threshold), name="DB Connection Monitor")
                background_tasks.append(db_monitor_task)


                # ✅ Step 4: Wait for shutdown signal
                try:
                    await shutdown_event.wait()
                except Exception as e:
                    shared_logger.error("Unhandled exception in webhook:", exc_info=True)
                    if alert:
                        alert.callhome("webhook crashed", str(e), mode="email")

                # ✅ Step 5: Cancel background tasks and sighook
                for task in background_tasks:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                sighook_task.cancel()
                try:
                    await sighook_task
                except asyncio.CancelledError:
                    pass

                # ✅ Stop TradeRecorder worker before shutting down listener
                await shared_data_manager.trade_recorder.stop_worker()

                await graceful_shutdown(listener, runner)

    except Exception as e:
        if alert:
            alert.callhome("Bot main process crashed", str(e), mode="email")
        raise





if __name__ == "__main__":
    os.environ['PYTHONASYNCIODEBUG'] = '0'
    logger = logging.getLogger('asyncio')
    logger.setLevel(logging.ERROR)
    asyncio.run(main())




