
import asyncio
import logging
import os
import signal
import time

import aiohttp
from aiohttp import web

from Config.config_manager import CentralConfig as Config
from MarketDataManager.market_data_manager import MarketDataUpdater
from SharedDataManager.shared_data_manager import SharedDataManager
from Shared_Utils.logging_manager import LoggerManager
from Shared_Utils.snapshots_manager import SnapshotsManager
from sighook.database_session_manager import DatabaseSessionManager
from sighook.sender import TradeBot
from webhook.listener import WebSocketHelper
from webhook.listener import WebSocketManager
from webhook.listener import WebhookListener

shutdown_event = asyncio.Event()


async def setup_logger(logger_name='webhook_logger') -> LoggerManager:
    log_config = {"log_level": logging.INFO}
    logger_mgr = LoggerManager(log_config)
    _ = logger_mgr.get_logger(logger_name)  # Optionally trigger initialization
    return logger_mgr  # ✅ Return only the manager





async def load_config():
    return Config()


async def init_shared_data(log_manager):
    database_session_manager = DatabaseSessionManager(None, log_manager)
    shared_data_manager = SharedDataManager.get_instance(log_manager, database_session_manager)
    snapshot_manager = SnapshotsManager.get_instance(shared_data_manager, logger)
    await shared_data_manager.initialize()
    return shared_data_manager


async def run_sighook(shared_data_manager, rest_client, portfolio_uuid, log_manager, startup_event):
    await startup_event.wait()  # ⏳ Wait until webhook sets the flag

    trade_bot = TradeBot(

        shared_data_mgr=shared_data_manager,
        rest_client=rest_client,
        portfolio_uuid=portfolio_uuid,
        logger_manager=log_manager
    )
    await trade_bot.async_init()

    try:
        while not shutdown_event.is_set():
            await trade_bot.run_bot()
            await asyncio.sleep(5)
    except asyncio.CancelledError:
        log_manager.info("sighook task cancelled.")
    finally:
        log_manager.info("sighook shutdown complete.")


async def run_webhook(config, shared_data_manager, log_manager, startup_event=None, trade_bot=None):
    async with aiohttp.ClientSession() as session:
        listener = WebhookListener(
            bot_config=config,
            shared_data_manager=shared_data_manager,
            database_session_manager=shared_data_manager.database_session_manager,
            logger_manager=log_manager,
            session=session,
            market_manager=None,
            market_data_manager=None
        )
        listener.rest_client = config.rest_client
        listener.portfolio_uuid = config.portfolio_uuid

        if trade_bot is None:
            trade_bot = TradeBot(shared_data_mgr=shared_data_manager,
                                 rest_client=listener.rest_client,
                                 portfolio_uuid=listener.portfolio_uuid,
                                 logger_manager=log_manager)
            await trade_bot.async_init()
            # await trade_bot.load_bot_components()

        listener.market_manager = trade_bot.market_manager
        await listener.async_init()

        listener.market_data_manager = await MarketDataUpdater.get_instance(
            listener.ticker_manager, log_manager
        )

        if listener.ohlcv_manager:
            listener.ohlcv_manager.market_manager = listener.market_manager

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
            ohlcv_manager=listener.ohlcv_manager
        )
        websocket_manager = WebSocketManager(config, listener.coinbase_api, log_manager, websocket_helper)

        listener.websocket_manager = websocket_manager
        listener.websocket_helper = websocket_helper

        market_data_master, order_mgmnt_master = await listener.market_data_manager.update_market_data(time.time())
        listener.initialize_listener_components(market_data_master, order_mgmnt_master, shared_data_manager)

        if startup_event:
            startup_event.set()  # ✅ Signal to sighook that data is ready

        asyncio.create_task(websocket_manager.start_websockets())

        app = await listener.create_app()
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', config.webhook_port)
        await site.start()
        log_manager.get_logger("webhook_logger").info(
            f'Webhook {config.program_version} is Listening on port {config.webhook_port}...'
        )

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(graceful_shutdown(listener, runner)))

        background_tasks = [
            asyncio.create_task(listener.refresh_market_data(), name="Market Data Refresher"),
            asyncio.create_task(listener.periodic_save(), name="Periodic Data Saver"),
        ]

        await shutdown_event.wait()

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
    import argparse
    parser = argparse.ArgumentParser(description="Run the crypto trading bot components.")
    parser.add_argument('--run', choices=['sighook', 'webhook', 'both'], default='both')
    args = parser.parse_args()

    config = await load_config()

    if args.run == 'sighook':
        sighook_logger_mgr = await setup_logger('sighook_logger')

        shared_data_manager = await init_shared_data(sighook_logger_mgr)
        await run_sighook(shared_data_manager, config.rest_client, config.portfolio_uuid, sighook_logger_mgr)

    elif args.run == 'webhook':
        webhook_logger_mgr, webhook_logger = await setup_logger('webhook_logger')
        shared_data_manager = await init_shared_data(webhook_logger)
        await run_webhook(config, shared_data_manager, webhook_logger_mgr)


    elif args.run == 'both':
        sighook_logger_mgr = await setup_logger('sighook_logger')
        shared_data_manager = await init_shared_data(sighook_logger_mgr)
        startup_event = asyncio.Event()

        # Launch sighook in background
        sighook_task = asyncio.create_task(run_sighook(
            shared_data_manager, config.rest_client, config.portfolio_uuid,
            sighook_logger_mgr, startup_event)
        )

        # Launch webhook in background
        webhook_logger_mgr = await setup_logger('webhook_logger')
        webhook_task = asyncio.create_task(
            run_webhook(config, shared_data_manager, webhook_logger_mgr, startup_event)
        )

        # Wait for both to finish
        await asyncio.gather(sighook_task, webhook_task)


if __name__ == "__main__":
    os.environ['PYTHONASYNCIODEBUG'] = '0'
    logger = logging.getLogger('asyncio')
    logger.setLevel(logging.ERROR)
    asyncio.run(main())

