
import argparse
import asyncio
import logging
import os
import signal
import time

import aiohttp
from decimal import Decimal
from aiohttp import web

from Shared_Utils.scheduler import periodic_runner
from Config.config_manager import CentralConfig as Config
from Api_manager.coinbase_api import CoinbaseAPI
from MarketDataManager.ticker_manager import TickerManager
from MarketDataManager.webhook_order_book import OrderBookManager
from MarketDataManager.market_data_manager import market_data_watchdog
from MarketDataManager.market_data_manager import MarketDataUpdater
from MarketDataManager.passive_order_manager import PassiveOrderManager
from SharedDataManager.shared_data_manager import SharedDataManager
from Shared_Utils.alert_system import AlertSystem
from TestingDebugging.debugger import Debugging
from Shared_Utils.exchange_manager import ExchangeManager
from Shared_Utils.logging_manager import LoggerManager
from Shared_Utils.print_data import PrintData
from  Shared_Utils.print_data import ColorCodes
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
    try:
        logger = logger_manager.get_logger("shared_logger")
        logger.info("⏳ Checking startup snapshot state...")

        market_data, order_mgmt = await shared_data_manager.validate_startup_state(market_data_updater,ticker_manager)
        logger.info("✅ Market data preloaded successfully with data from the database.")
        return market_data, order_mgmt
    except Exception as e:
        logger.error(f"❌ Failed to preload market/order data: {e}", exc_info=True)
        raise


async def graceful_shutdown(listener, runner):
    if hasattr(listener, 'shutdown'):
        await listener.shutdown()
    await runner.cleanup()
    shutdown_event.set()

async def init_shared_data(logger_manager, shared_logger):
    shared_data_manager = SharedDataManager.__new__(SharedDataManager)
    database_session_manager = DatabaseSessionManager(
        profit_extras=None,
        logger_manager=shared_logger,
        shared_data_manager=shared_data_manager
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
                                 shared_utils_utility, shared_utils_precision)


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
        logger=listener.logger,
        min_spread_pct=config.min_spread_pct,  # 0.15 %, overrides default 0.20 %
        fee_cache=fee_rates,  # ← new
        # optional knobs ↓
        max_lifetime=90,  # cancel / refresh after 90 s
    )

    websocket_helper = WebSocketHelper(
        listener=listener,
        websocket_manager=None,
        exchange=listener.exchange,
        ccxt_api=listener.ccxt_api,
        logger_manager=listener.logger,
        coinbase_api=listener.coinbase_api,
        profit_data_manager=listener.profit_data_manager,
        order_type_manager=listener.order_type_manager,
        order_manager=listener.order_manager,
        shared_utils_date_time=listener.shared_utils_date_time,
        shared_utils_print=listener.shared_utils_print,
        shared_utils_color=listener.shared_utils_color,
        shared_utils_precision=listener.shared_utils_precision,
        shared_utils_utility=listener.shared_utils_utility,
        shared_utils_debugger=listener.shared_utils_debugger,
        trailing_stop_manager=listener.trailing_stop_manager,
        order_book_manager=listener.order_book_manager,
        snapshot_manager=listener.snapshot_manager,
        trade_order_manager=listener.trade_order_manager,
        ohlcv_manager=listener.ohlcv_manager,
        shared_data_manager=shared_data_manager,
        market_ws_manager=None,
        passive_order_manager=passive_order_manager,

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
        trailing_stop_manager=listener.trailing_stop_manager,
        order_book_manager=listener.order_book_manager,
        snapshot_manager=listener.snapshot_manager,
        trade_order_manager=listener.trade_order_manager,
        ohlcv_manager=listener.ohlcv_manager,
        shared_data_manager=shared_data_manager
    )
    market_ws_manager.passive_order_manager = passive_order_manager
    # 🔁 Restore any passive orders
    await passive_order_manager.reload_persisted_passive_orders()

    websocket_manager = WebSocketManager(
        config=Config(),
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
        except Exception as e:
            print(f"⚠️ Error in refresh_loop: {e}")
        await asyncio.sleep(interval)


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
        market_data_updater=market_data_updater,
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

async def create_trade_bot(config, coinbase_api, shared_data_manager, market_data_updater, order_book_manager, logger_manager,
                           shared_utils_debugger, shared_utils_print, shared_utils_color, websocket_helper=None) -> TradeBot:
    trade_bot = TradeBot(
        coinbase_api=coinbase_api,
        shared_data_mgr=shared_data_manager,
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
    print(f"✅ Market Data Keys: {list(shared_data_manager.market_data.keys())}")

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

    await graceful_shutdown(listener, runner)
    return listener




async def main():
    parser = argparse.ArgumentParser(description="Run the crypto trading bot components.")
    parser.add_argument('--run', choices=['sighook', 'webhook', 'both'], default='both')
    args = parser.parse_args()

    config = await load_config()
    log_config = {"log_level": logging.INFO}
    logger_manager = LoggerManager(log_config)

    webhook_logger = logger_manager.get_logger("webhook_logger")
    sighook_logger = logger_manager.get_logger("sighook_logger")
    shared_logger = logger_manager.get_logger("shared_logger")

    alert = AlertSystem(logger_manager) if args.run != 'webhook' else None

    config.exchange = ExchangeManager.get_instance(config.load_webhook_api_key()).get_exchange()
    startup_event = asyncio.Event()

    (shared_data_manager, shared_utils_debugger, shared_utils_print, shared_utils_color, shared_utils_utility,
     shared_utils_precision) = await init_shared_data(logger_manager, shared_logger)

    # ✅ Initialize OrderBookManager before listener
    order_book_manager = OrderBookManager.get_instance(
        config.exchange,
        shared_data_manager,
        shared_data_manager.shared_utils_precision,
        logger_manager.get_logger("shared_logger"),
        ccxt_api=None  # Pass your existing ccxt_api if available
    )

    try:
        async with (aiohttp.ClientSession() as session):

            coinbase_api = CoinbaseAPI(session, shared_utils_utility, logger_manager,
                                       shared_utils_precision)
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
                    asyncio.create_task(listener.sync_open_orders(), name="TradeRecord Sync"),
                    asyncio.create_task(listener.periodic_save(), name="Periodic Data Saver"),
                    asyncio.create_task(websocket_manager.start_websockets())
                ]

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




