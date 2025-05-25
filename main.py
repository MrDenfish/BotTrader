
import argparse
import asyncio
import logging
import os
import signal
import time

import aiohttp
from decimal import Decimal
from aiohttp import web

from Config.config_manager import CentralConfig as Config
from MarketDataManager.market_data_manager import MarketDataUpdater
from MarketDataManager.passive_order_manager import PassiveOrderManager
from SharedDataManager.shared_data_manager import SharedDataManager
from Shared_Utils.alert_system import AlertSystem
from Shared_Utils.debugger import Debugging
from Shared_Utils.exchange_manager import ExchangeManager
from Shared_Utils.logging_manager import LoggerManager
from Shared_Utils.print_data import PrintData
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


async def init_shared_data(logger_manager, shared_logger):
    shared_data_manager = SharedDataManager.__new__(SharedDataManager)
    database_session_manager = DatabaseSessionManager(
        profit_extras=None,
        logger_manager=shared_logger,
        shared_data_manager=shared_data_manager
    )
    shared_data_manager.__init__(shared_logger, database_session_manager)

    # Initialize utilities
    snapshot_manager = SnapshotsManager.get_instance(shared_data_manager, shared_logger)
    shared_utils_debugger = Debugging()
    shared_utils_utility = SharedUtility.get_instance(logger_manager)
    shared_utils_print = PrintData.get_instance(logger_manager, shared_utils_utility)

    # Set attributes on the shared data manager
    shared_data_manager.snapshot_manager = snapshot_manager
    shared_data_manager.shared_utils_debugger = shared_utils_debugger
    shared_data_manager.shared_utils_print = shared_utils_print

    await shared_data_manager.initialize()
    return shared_data_manager, shared_utils_debugger, shared_utils_print


async def build_websocket_components(config, listener, shared_data_manager):
    # --- NEW: pull latest maker / taker rates -------------
    fee_rates = await listener.coinbase_api.get_fee_rates()
    if "maker" not in fee_rates:  # API down?  Use a worst-case stub
        listener.logger.warning("⚠️  Using fallback fee tier 0.0020")
        fee_rates = {"maker": Decimal("0.0020"), "taker": Decimal("0.0025")}

    # ------------------------------------------------------
    passive_order_manager = PassiveOrderManager(
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
        shared_utils_print=listener.shared_utils_print,
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
        order_manager=listener.order_manager,
        passive_order_manager=passive_order_manager
    )

    market_ws_manager = WebSocketMarketManager(
        listener=listener,
        exchange=listener.exchange,
        ccxt_api=listener.ccxt_api,
        logger_manager=listener.logger,
        coinbase_api=listener.coinbase_api,
        profit_data_manager=listener.profit_data_manager,
        order_type_manager=listener.order_type_manager,
        shared_utils_print=listener.shared_utils_print,
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


async def run_sighook(config, shared_data_manager, rest_client, portfolio_uuid, logger_manager, alert,
                      shared_utils_debugger, shared_utils_print, startup_event=None):
    if startup_event:
        await startup_event.wait()

    exchange = config.exchange
    trade_bot = TradeBot(
        shared_data_mgr=shared_data_manager,
        rest_client=config.rest_client,
        portfolio_uuid=config.portfolio_uuid,
        exchange=config.exchange,
        logger_manager=logger_manager,
        shared_utils_debugger=shared_utils_debugger,
        shared_utils_print=shared_utils_print
    )
    await trade_bot.async_init(validate_startup_data=False,
                               shared_utils_debugger=shared_utils_debugger,
                               shared_utils_print=shared_utils_print)

    sighook_logger = logger_manager.get_logger("sighook")
    try:
        while not shutdown_event.is_set():
            await trade_bot.run_bot()
            await asyncio.sleep(5)
    except Exception as e:
        sighook_logger.error("Unhandled exception in sighook:", exc_info=True)
        alert.callhome("sighook crashed", str(e), mode="email")
    finally:
        sighook_logger.info("sighook shutdown complete.")


async def run_webhook(config, shared_data_manager, logger_manager, alert,
                      shared_utils_debugger, shared_utils_print, startup_event=None, trade_bot=None):
    async with (aiohttp.ClientSession() as session):
        exchange = config.exchange

        listener = WebhookListener(
            bot_config=config,
            shared_data_manager=shared_data_manager,
            database_session_manager=shared_data_manager.database_session_manager,
            logger_manager=logger_manager,
            session=session,
            market_manager=None,
            market_data_manager=None,
            exchange=exchange
        )
        listener.rest_client = config.rest_client
        listener.portfolio_uuid = config.portfolio_uuid

        if trade_bot is None:
            trade_bot = TradeBot(
                shared_data_mgr=shared_data_manager,
                rest_client=listener.rest_client,
                portfolio_uuid=listener.portfolio_uuid,
                exchange=exchange,
                logger_manager=logger_manager,
                shared_utils_debugger=shared_utils_debugger,
                shared_utils_print=shared_utils_print
            )
            await trade_bot.async_init(validate_startup_data=True,
                                       shared_utils_debugger=shared_utils_debugger,
                                       shared_utils_print=shared_utils_print)

        listener.market_manager = trade_bot.market_manager
        await listener.async_init()

        listener.market_data_manager = await MarketDataUpdater.get_instance(
            listener.ticker_manager, logger_manager
        )

        if listener.ohlcv_manager:
            listener.ohlcv_manager.market_manager = listener.market_manager

        listener.order_manager = trade_bot.order_manager
        websocket_helper, websocket_manager, market_ws_manager = await build_websocket_components(config,listener,shared_data_manager)

        listener.websocket_helper = websocket_helper
        listener.websocket_manager = websocket_manager

        await listener.market_data_manager.update_market_data(time.time())
        print(f"✅ Market Data Keys: {list(shared_data_manager.market_data.keys())}")

        if startup_event:
            startup_event.set()

        asyncio.create_task(websocket_manager.start_websockets())

        app = await listener.create_app()
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', config.webhook_port)
        await site.start()
        print(f"✅ TradeBot is running on version:{config.program_version} ✅")
        print(f'Webhook {config.program_version} is Listening on port {config.webhook_port}...')

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(graceful_shutdown(listener, runner)))

        background_tasks = [
            asyncio.create_task(listener.refresh_market_data(), name="Market Data Refresher"),
            asyncio.create_task(listener.periodic_save(), name="Periodic Data Saver"),
            asyncio.create_task(listener.sync_open_orders(), name="TradeRecord Sync"),
        ]

        try:
            await shutdown_event.wait()
        except Exception as e:
            logger.error("Unhandled exception in webhook:", exc_info=True)
            alert.callhome("webhook crashed", str(e), mode="email")

        for task in background_tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        await graceful_shutdown(listener, runner)


async def graceful_shutdown(listener, runner):
    if hasattr(listener, 'shutdown'):
        await listener.shutdown()
    await runner.cleanup()
    shutdown_event.set()


async def main():

    parser = argparse.ArgumentParser(description="Run the crypto trading bot components.")
    parser.add_argument('--run', choices=['sighook', 'webhook', 'both'], default='both')
    args = parser.parse_args()

    config = await load_config()
    # print("� Pre-warming CentralConfig...")
    # print(f"� DB Config: user={config.db_user}, host={config.db_host}, name={config.db_name}")

    log_config = {"log_level": logging.INFO}
    logger_manager = LoggerManager(log_config)
    webhook_logger = logger_manager.get_logger("webhook_logger")
    sighook_logger = logger_manager.get_logger("sighook_logger")
    shared_logger = logger_manager.get_logger("shared_logger")

    # prevents webhook-only containers from loading the alert system and failing on startup.
    if args.run in ["sighook", "both"]:
        alert = AlertSystem(logger_manager)
    else:
        alert = None

    config.exchange = ExchangeManager.get_instance(config.load_webhook_api_key()).get_exchange()
    startup_event = asyncio.Event()

    shared_data_manager, shared_utils_debugger, shared_utils_print = await init_shared_data(logger_manager, shared_logger)

    try:
        if args.run == 'sighook':
            await run_sighook(
                config,
                shared_data_manager,
                config.rest_client,
                config.portfolio_uuid,
                logger_manager,
                alert,
                shared_utils_debugger=shared_utils_debugger,
                shared_utils_print=shared_utils_print
            )

        elif args.run == 'webhook':
            await run_webhook(
                config,
                shared_data_manager,
                webhook_logger,
                alert,
                shared_utils_debugger=shared_utils_debugger,
                shared_utils_print=shared_utils_print
            )

        elif args.run == 'both':
            sighook_task = asyncio.create_task(run_sighook(
                config,
                shared_data_manager,
                config.rest_client,
                config.portfolio_uuid,
                logger_manager,
                alert,
                shared_utils_debugger=shared_utils_debugger,
                shared_utils_print=shared_utils_print,
                startup_event=startup_event
            ))

            webhook_task = asyncio.create_task(run_webhook(
                config,
                shared_data_manager,
                logger_manager,
                alert,
                shared_utils_debugger=shared_utils_debugger,
                shared_utils_print=shared_utils_print,
                startup_event=startup_event
            ))

            await asyncio.gather(sighook_task, webhook_task)

    except Exception as e:
        alert.callhome("Bot main process crashed", str(e), mode="email")
        raise


if __name__ == "__main__":
    os.environ['PYTHONASYNCIODEBUG'] = '0'
    logger = logging.getLogger('asyncio')
    logger.setLevel(logging.ERROR)
    asyncio.run(main())


