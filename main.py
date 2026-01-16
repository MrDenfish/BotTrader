
import argparse
import asyncio
import logging
import os
import signal
import time

import aiohttp
import faulthandler
from decimal import Decimal
from aiohttp import web
from Shared_Utils.scheduler import periodic_runner
from Shared_Utils.paths import resolve_runtime_paths
from Shared_Utils.runtime_env import running_in_docker as running_in_docker

from TestDebugMaintenance.trade_record_maintenance import run_maintenance_if_needed
from TestDebugMaintenance.debugger import Debugging
from TestDebugMaintenance.debug_config import DebugToggles, setup_logging, setup_stack_logging
from TestDebugMaintenance.debug_sos import install_signal_handlers,loop_watchdog, task_census
from AccumulationManager.accumulation_manager import AccumulationManager

from Config.config_manager import CentralConfig as Config
# loaded in main() to avoid circular import
#from Api_manager.coinbase_api import CoinbaseAPI
#from MarketDataManager.ticker_manager import TickerManager
#from MarketDataManager.webhook_order_book import OrderBookManager

from MarketDataManager.market_data_manager import market_data_watchdog
from MarketDataManager.market_data_manager import MarketDataUpdater
from MarketDataManager.passive_order_manager import PassiveOrderManager
from MarketDataManager.asset_monitor import AssetMonitor

from Shared_Utils.alert_system import AlertSystem

from Shared_Utils.exchange_manager import ExchangeManager
from Shared_Utils.logging_manager import LoggerManager
from Shared_Utils.logger import get_logger
from Shared_Utils.print_data import PrintData
from Shared_Utils.print_data import ColorCodes
from Shared_Utils.precision import PrecisionUtils
from Shared_Utils.snapshots_manager import SnapshotsManager
from Shared_Utils.utility import SharedUtility

from sighook.sender import TradeBot
from webhook.listener import WebSocketManager, WebhookListener
from webhook.websocket_helper import WebSocketHelper
from webhook.websocket_market_manager import WebSocketMarketManager
from database_manager.database_session_manager import DatabaseSessionManager
from SharedDataManager.shared_data_manager import SharedDataManager, CustomJSONDecoder
from SharedDataManager.leader_board import (LeaderboardConfig)
shutdown_event = asyncio.Event()

IS_DOCKER = running_in_docker()
# (for older modules that still read the env directly)
os.environ["IN_DOCKER"] = "true" if IS_DOCKER else "false"

def default_run_mode() -> str:
    # Desktop default = both (single process). Docker default = sighook (split).
    return "both" if not IS_DOCKER else os.getenv("RUN_MODE", "sighook")
# Force singleton initialization across all environments

# Force singleton initialization across all environments
_ = Config(is_docker=IS_DOCKER)
# (optional) resolve dirs once and export for legacy call-sites
# DATA_DIR, CACHE_DIR, LOG_DIR = resolve_runtime_paths(IS_DOCKER)
# os.environ.setdefault("BOTTRADER_DATA_DIR", str(DATA_DIR))
# os.environ.setdefault("BOTTRADER_CACHE_DIR", str(CACHE_DIR))
# os.environ.setdefault("BOTTRADER_LOG_DIR", str(LOG_DIR))

# Initialize structured logger
startup_logger = get_logger('main', context={'component': 'startup'})
startup_logger.info(
    "CentralConfig preloaded",
    extra={
        'machine_type': _.machine_type,
        'db_user': _.db_user,
        'db_host': _.db_host,
        'db_name': _.db_name
    }
)

async def load_config():
    return Config(is_docker=running_in_docker())


async def preload_market_data(logger_manager, shared_data_manager, market_data_updater, ticker_manager ):
    shared_logger = logger_manager.get_logger("shared_logger")
    try:

        shared_logger.info("⏳ Checking startup snapshot state...")

        market_data, order_mgmt = await shared_data_manager.validate_startup_state(market_data_updater,ticker_manager)
        # ✅ Explicitly assign to shared_data_manager
        shared_data_manager.market_data = market_data or {}
        shared_data_manager.order_management = order_mgmt or {}
        shared_logger.info(
            "Market data preloaded successfully",
            extra={'symbols': list(shared_data_manager.market_data.keys())}
        )
        return market_data, order_mgmt
    except Exception as e:
        shared_logger.error(f"❌ Failed to preload market/order data: {e}", exc_info=True)
        raise


async def graceful_shutdown(listener, runner):
    if hasattr(listener, 'shutdown'):
        await listener.shutdown()
    if hasattr(listener, 'market_data_manager'):
        market_ws_manager = listener.market_data_manager
        if hasattr(market_ws_manager, 'shutdown'):
            await market_ws_manager.shutdown()
    await runner.cleanup()

    faulthandler.cancel_dump_traceback_later()
    shutdown_event.set()


async def init_shared_data(config, logger_manager, shared_logger, coinbase_api):
    shared_data_manager = SharedDataManager.__new__(SharedDataManager)

    # DSN (from config or env)
    dsn = getattr(config, "database_url", None) \
          or os.getenv("DATABASE_URL") \
          or os.getenv("TRADEBOT_DATABASE_URL")
    if not dsn:
        raise RuntimeError("Set config.database_url or DATABASE_URL")

    # normalize to async driver
    if dsn.startswith("postgres://"):
        dsn = dsn.replace("postgres://", "postgresql+asyncpg://", 1)
    elif dsn.startswith("postgresql://"):
        dsn = dsn.replace("postgresql://", "postgresql+asyncpg://", 1)

    # your original pool & recycle knobs (no SSL / no connect_args)
    engine_kw = dict(
        echo=False,
        pool_size=int(os.getenv("DB_POOL_SIZE", "5")),
        max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "5")),
        pool_timeout=int(os.getenv("DB_POOL_TIMEOUT", "10")), # debugging safe to keep on in production
        pool_recycle=int(os.getenv("DB_POOL_RECYCLE", "300")),  # 5m
        pool_pre_ping=True,
        future=True,
        # ← no connect_args here
    )

    database_session_manager = DatabaseSessionManager(
        dsn,  # already normalized to +asyncpg earlier
        logger=shared_logger,  # optional
        echo=False,
        pool_size=int(os.getenv("DB_POOL_SIZE", "5")),
        max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "5")),
        pool_timeout=int(os.getenv("DB_POOL_TIMEOUT", "10")), # debugging safe to keep on in production
        pool_recycle=int(os.getenv("DB_POOL_RECYCLE", "300")),
        pool_pre_ping=True,
        future=True,
        # connect_args=...  # only if you want to override the defaults above
    )


    # --- rest unchanged ---
    test_debug_maint = Debugging()
    shared_utils_precision = PrecisionUtils.get_instance(logger_manager, shared_data_manager)
    snapshot_manager = SnapshotsManager.get_instance(shared_data_manager, shared_utils_precision, shared_logger)
    shared_utils_utility = SharedUtility.get_instance(logger_manager)
    shared_utils_print = PrintData.get_instance(logger_manager, shared_utils_utility)
    shared_utils_color = ColorCodes.get_instance()

    custom_json_decoder = CustomJSONDecoder
    shared_data_manager.__init__(
        shared_logger,
        database_session_manager,
        shared_utils_utility,
        shared_utils_precision,
        coinbase_api=coinbase_api,
    )
    shared_data_manager.custom_json_decoder = custom_json_decoder
    shared_data_manager.inject_maintenance_callback()

    shared_data_manager.snapshot_manager = snapshot_manager
    shared_data_manager.test_debug_maint = test_debug_maint
    shared_data_manager.shared_utils_print = shared_utils_print
    shared_data_manager.shared_utils_color = shared_utils_color
    shared_data_manager.shared_utils_precision = shared_utils_precision

    # Warm up the DB connection (optional, but nice)
    try:
        await database_session_manager.initialize()
    except Exception:
        raise
    await shared_data_manager.initialize_schema()
    await shared_data_manager.populate_initial_data()

    await shared_data_manager.initialize()
    return (
        shared_data_manager,
        test_debug_maint,
        shared_utils_print,
        shared_utils_color,
        shared_utils_utility,
        shared_utils_precision,
    )


def _normalize_fees(fees_like: dict) -> dict:
    # make sure everything is Decimal and present
    maker = Decimal(str(fees_like.get("maker", "0.0020")))
    taker = Decimal(str(fees_like.get("taker", "0.0025")))
    return {"maker": maker, "taker": taker}

async def build_websocket_components(config, listener, shared_data_manager):
    # --- NEW: pull latest maker / taker rates -------------
       # 1) Fetch & normalize once (await the coroutine here)
    try:
        fee_rates = await listener.coinbase_api.get_fee_rates()
        if "maker" not in fee_rates:
            raise ValueError("fee payload missing 'maker'")
    except Exception:
        listener.logger.warning("⚠️ Using fallback fee tier 0.0020/0.0025")
        fee_rates = {"maker": "0.0020", "taker": "0.0025"}

    fee_rates = _normalize_fees(fee_rates)

    # ------------------------------------------------------
    # Passive MM: Only instantiate if enabled (disabled by default due to poor performance)
    if config.passive_mm_enabled:
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
            edge_buffer_pct=config.edge_buffer_pct,
            min_spread_pct=config.min_spread_pct,
            max_lifetime=config.max_lifetime,
            inventory_bias_factor=config.inventory_bias_factor,
            fee_cache=fee_rates,
        )
        listener.passive_order_manager = passive_order_manager
        listener.fee_rates = passive_order_manager.original_fees = fee_rates
        listener.logger.info("✓ Passive market making ENABLED")
    else:
        listener.passive_order_manager = None
        listener.fee_rates = fee_rates
        listener.logger.info("✓ Passive market making DISABLED")

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
        test_debug_maint=listener.test_debug_maint,
        order_book_manager=listener.order_book_manager,
        snapshot_manager=listener.snapshot_manager,
        trade_order_manager=listener.trade_order_manager,
        shared_data_manager=shared_data_manager,
        market_ws_manager=None,
        database_session_manager=shared_data_manager.database_session_manager,
        passive_order_manager=listener.passive_order_manager,
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
        test_debug_maint=listener.test_debug_maint,
        order_book_manager=listener.order_book_manager,
        snapshot_manager=listener.snapshot_manager,
        trade_order_manager=listener.trade_order_manager,
        ohlcv_manager=listener.ohlcv_manager,
        shared_data_manager=shared_data_manager,
        database_session_manager=shared_data_manager.database_session_manager
    )
    market_ws_manager.passive_order_manager = listener.passive_order_manager
    # 🔁 Restore any passive orders (only if enabled)
    if listener.passive_order_manager:
        await listener.passive_order_manager.reload_persisted_passive_orders()

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
    refresh_logger = get_logger('main', context={'component': 'refresh_loop'})
    while True:
        try:
            await shared_data_manager.refresh_shared_data()
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            refresh_logger.info("Refresh loop cancelled")



async def run_sighook(config, shared_data_manager, market_data_updater, rest_client, portfolio_uuid,
                      logger_manager, alert, order_book_manager,
                      test_debug_maint, shared_utils_print, shared_utils_color,
                      startup_event=None, listener=None, coinbase_api=None):

    if startup_event:
        await startup_event.wait()

    await shared_data_manager.initialize_shared_data()
    # ✅ Wait for webhook to populate shared data
    await shared_data_manager.wait_until_initialized()
    sighook_logger = get_logger('main', context={'component': 'sighook'})
    sighook_logger.info("Shared data is initialized. Proceeding with sighook setup.")

    websocket_helper = listener.websocket_helper if listener else None
    # ⚙️ Finalize coinbase_api from any available source
    cb = (
            coinbase_api
            or (listener.coinbase_api if listener else None)
            or getattr(shared_data_manager, "coinbase_api", None)
    )
    if cb is None:
        raise RuntimeError("sighook requires a Coinbase REST client; none was provided or discoverable.")
    exchange = config.exchange
    trade_bot = TradeBot(
        coinbase_api=cb,
        shared_data_mgr=shared_data_manager,
        shutdown_event=shutdown_event,
        trade_recorder=shared_data_manager.trade_recorder,
        market_data_updater=market_data_updater,
        rest_client=config.rest_client,
        portfolio_uuid=config.portfolio_uuid,
        exchange=exchange,
        order_book_manager=order_book_manager,
        logger_manager=logger_manager,
        test_debug_maint=test_debug_maint,
        shared_utils_print=shared_utils_print,
        shared_utils_color=shared_utils_color,
        websocket_helper=websocket_helper,

    )
    await trade_bot.async_init(validate_startup_data=False,
                               test_debug_maint=test_debug_maint,
                               shared_utils_print=shared_utils_print,
                               shared_utils_color=shared_utils_color
                               )

    # sighook_logger already created at line 352, don't overwrite it
    sighook_logger.info("🔎 [DEBUG] After async_init, about to start background tasks")

    # Start periodic refresh of shared data
    asyncio.create_task(refresh_loop(shared_data_manager, interval=60))

    # 🔹 Start Accumulation Daily Runner
    if hasattr(shared_data_manager, 'accumulation_manager') and shared_data_manager.accumulation_manager is not None:
        sighook_logger.info("✅ [Accumulation] Starting daily runner in sighook mode")
        asyncio.create_task(
            shared_data_manager.accumulation_manager.start_daily_runner(),
            name="Accumulation Daily Runner"
        )
        sighook_logger.info(f"📊 [Accumulation] Config: signal_based={shared_data_manager.accumulation_manager.signal_based_enabled}, "
                          f"daily_pnl={shared_data_manager.accumulation_manager.daily_pnl_based_enabled}, "
                          f"symbol={shared_data_manager.accumulation_manager.accumulation_symbol}")
    else:
        sighook_logger.warning("❌ [Accumulation] Manager not found - accumulation disabled")

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
                           test_debug_maint, shared_utils_print, shared_utils_color, websocket_helper=None) -> TradeBot:
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
        test_debug_maint=test_debug_maint,
        shared_utils_print=shared_utils_print,
        shared_utils_color=shared_utils_color,
        websocket_helper=websocket_helper
    )
    await trade_bot.async_init(validate_startup_data=True,
                               test_debug_maint=test_debug_maint,
                               shared_utils_print=shared_utils_print,
                               shared_utils_color=shared_utils_color)
    return trade_bot


async def init_webhook(config, session, coinbase_api, shared_data_manager, market_data_updater, logger_manager,test_debug_maint,
                       shared_utils_print, shared_utils_color, alert, startup_event=None, trade_bot=None, order_book_manager=None):

    webhook_init_logger = get_logger('main', context={'component': 'webhook_init'})
    webhook_init_logger.info("🔧 [HTTP_SERVER_DEBUG] init_webhook() called - starting webhook initialization")

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
        order_book_manager=order_book_manager,
        passive_order_manager=None,
        original_fees=None
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
            test_debug_maint=test_debug_maint,
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

    # asyncio.create_task(websocket_manager.start_websockets())

    webhook_logger = get_logger('main', context={'component': 'webhook'})
    webhook_logger.info("🔧 [HTTP_SERVER_DEBUG] About to call listener.create_app()")

    app = await listener.create_app()
    webhook_logger.info(f"🔧 [HTTP_SERVER_DEBUG] create_app() returned: {app}")

    if app is None:
        raise RuntimeError("❌ listener.create_app() returned None — cannot start webhook.")

    webhook_logger.info("🔧 [HTTP_SERVER_DEBUG] Creating AppRunner")
    runner = web.AppRunner(app)

    webhook_logger.info("🔧 [HTTP_SERVER_DEBUG] Calling runner.setup()")
    await runner.setup()

    webhook_logger.info(f"🔧 [HTTP_SERVER_DEBUG] Creating TCPSite on 0.0.0.0:{config.webhook_port}")
    site = web.TCPSite(runner, '0.0.0.0', config.webhook_port)

    webhook_logger.info("🔧 [HTTP_SERVER_DEBUG] Calling site.start() - HTTP server starting...")
    await site.start()

    webhook_logger.info(
        "✅ TradeBot HTTP server started successfully",
        extra={'version': config.program_version, 'port': config.webhook_port}
    )

    loop = asyncio.get_running_loop()
    # after: app = await listener.create_app()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(graceful_shutdown(listener, runner)))

    return listener, websocket_manager, app, runner



async def run_webhook(config, session, coinbase_api, shared_data_manager, market_data_updater, logger_manager, alert, test_debug_maint,
                      shared_utils_print, shared_utils_color, order_book_manager, startup_event=None, ccxt_api=None, trade_bot=None):


    listener, websocket_manager, app, runner = await init_webhook(
        config=config,
        session=session,
        coinbase_api=coinbase_api,
        shared_data_manager=shared_data_manager,
        market_data_updater=market_data_updater,
        logger_manager=logger_manager,
        test_debug_maint=test_debug_maint,
        shared_utils_print=shared_utils_print,
        shared_utils_color=shared_utils_color,
        startup_event=startup_event,
        trade_bot=None,
        alert=alert,
        order_book_manager=order_book_manager  # ✅ pass it in
    )

    background_tasks = make_webhook_tasks(
        listener=listener,
        logger_manager=logger_manager,
        websocket_manager=websocket_manager,
        shared_data_manager=shared_data_manager,
        enable_accumulation=False,  # ❌ not required in webhook
        accumulation_manager=None
    )

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

# ──────────────────────────────────────────────────────────────────────────────
# Periodic Leaderboard Task (Option B: run inside webhook process)
# ──────────────────────────────────────────────────────────────────────────────
async def leaderboard_job(shared_data_manager, logger_manager, interval_sec=600, lb_cfg=None):
    import logging, asyncio
    from SharedDataManager.leader_board import recompute_and_upsert_active_symbols, LeaderboardConfig
    log = logging.getLogger("leaderboard")
    if lb_cfg is None:
        lb_cfg = LeaderboardConfig(lookback_hours=24, min_n_24h=3, win_rate_min=0.35, pf_min=1.30)
    while True:
        try:
            async with shared_data_manager.database_session_manager.async_session() as session:
                precision = PrecisionUtils.get_instance(logger_manager, shared_data_manager)
                upserted = await recompute_and_upsert_active_symbols(session, lb_cfg,
                                                                     precision.fetch_precision,
                                                                     precision.adjust_precision,
                                                                     logger_manager)
                log.info("leaderboard upserted=%s (lookback=%sh, n≥%s, win≥%.2f, pf≥%.2f)",
                         upserted, lb_cfg.lookback_hours, lb_cfg.min_n_24h, lb_cfg.win_rate_min, lb_cfg.pf_min)
        except Exception:
            log.exception("leaderboard recompute failed")
        await asyncio.sleep(interval_sec)

def make_webhook_tasks(*, listener, logger_manager, websocket_manager, shared_data_manager, enable_accumulation: bool, accumulation_manager=None,) -> list[asyncio.Task]:
    t_watchdog = asyncio.create_task(loop_watchdog(threshold_ms=300), name="loop_watchdog") # debugging safe to keep on in production
    t_leaderboard_job = asyncio.create_task(
            leaderboard_job(shared_data_manager, logger_manager,
                interval_sec=600, lb_cfg=LeaderboardConfig(lookback_hours=24, min_n_24h=1, win_rate_min=0.0, pf_min=0.0),),
            name="Leaderboard Recompute",
        )
    tasks: list[asyncio.Task] = [
        asyncio.create_task(periodic_runner(listener.refresh_market_data, 30, name="Market Data Refresher")),
        asyncio.create_task(periodic_runner(listener.reconcile_with_rest_api, interval=300)),
        asyncio.create_task(listener.periodic_save(), name="Periodic Data Saver"),
        asyncio.create_task(listener.sync_open_orders(), name="TradeRecord Sync"),
        asyncio.create_task(websocket_manager.start_websockets(), name="Websocket Manager"), t_leaderboard_job, t_watchdog,
        asyncio.create_task(task_census(120)),
        asyncio.create_task(listener.asset_monitor.run_positions_exit_sentinel(3), name="Positions Exit Sentinel"),
        asyncio.create_task(refresh_loop(shared_data_manager, interval=60), name="Shared Data Refresh Loop"),
    ]

    # 🔇 Optional: only add accumulation in non-webhook modes (or if explicitly enabled)
    logger = logger_manager.loggers['shared_logger']
    logger.info(f"🔍 [Accumulation] Check: enabled={enable_accumulation}, manager={'exists' if accumulation_manager else 'None'}")

    if enable_accumulation and accumulation_manager is not None:
        logger.info("✅ [Accumulation] Creating Accumulation Daily Runner task")
        tasks.append(
            asyncio.create_task(accumulation_manager.start_daily_runner(), name="Accumulation Daily Runner")
        )
        logger.info(f"📊 [Accumulation] Config: signal_based={accumulation_manager.signal_based_enabled}, "
                   f"daily_pnl={accumulation_manager.daily_pnl_based_enabled}, "
                   f"symbol={accumulation_manager.accumulation_symbol}")
    else:
        logger.warning(f"❌ [Accumulation] NOT enabled: flag={enable_accumulation}, manager={'exists' if accumulation_manager else 'None'}")

    return tasks

async def app_boot():
    # Start optional helpers as named tasks
    if DebugToggles.WATCHDOG_ENABLED:
        asyncio.create_task(
            loop_watchdog(
                threshold_ms=DebugToggles.WATCHDOG_THRESHOLD,
                interval=DebugToggles.WATCHDOG_INTERVAL,
                dump_on_stall=DebugToggles.WATCHDOG_DUMP_ON_STALL,
            ),
            name="watchdog",
        )
    if DebugToggles.CENSUS_ENABLED:
        asyncio.create_task(
            task_census(
                interval=DebugToggles.CENSUS_INTERVAL,
                include_stacks=DebugToggles.CENSUS_STACKS,
            ),
            name="task_census",
        )

    parser = argparse.ArgumentParser(description="Run the crypto trading bot components.")
    parser.add_argument('--run', choices=['sighook', 'webhook', 'both'], default=default_run_mode())
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
    w = config.load_websocket_api_key() or {}

    config.test_mode = args.test  # ✅ Globally toggle test_mode            python main.py --run webhook --test
    log_config = {"log_level": logging.DEBUG if args.verbose else logging.INFO}
    logger_manager = LoggerManager(log_config)

    # loaded here to avoid circular import
    from Api_manager.coinbase_api import CoinbaseAPI
    from MarketDataManager.ticker_manager import TickerManager
    from MarketDataManager.webhook_order_book import OrderBookManager

    webhook_logger = logger_manager.get_logger("webhook_logger")
    sighook_logger = logger_manager.get_logger("sighook_logger")
    shared_logger = logger_manager.get_logger("shared_logger")
    shared_logger.info(
        "🔎 Coinbase at T0 (Config): rest_url=%s base_url=%s profile=%s key_len=%s",
        w.get("rest_api_url"),
        w.get("base_url"),
        w.get("profile_name") or w.get("profile_id"),
        len((w.get("name") or "")),
    )
    alert = AlertSystem(logger_manager) if args.run != 'webhook' else None

    config.exchange = ExchangeManager.get_instance(config.load_webhook_api_key()).get_exchange()
    startup_event = asyncio.Event()

    try:
        async with (aiohttp.ClientSession() as session):

            shared_utils_utility = SharedUtility.get_instance(logger_manager)
            coinbase_api = CoinbaseAPI(session, shared_utils_utility, logger_manager, None)
            (shared_data_manager, test_debug_maint, shared_utils_print, shared_utils_color, shared_utils_utility,
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
                test_debug_maint=test_debug_maint,
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

            # ✅ Initialize Strategy Snapshot Manager for performance tracking
            try:
                from sighook.strategy_snapshot_manager import StrategySnapshotManager
                strategy_snapshot_manager = StrategySnapshotManager(
                    db=shared_data_manager.database_session_manager,
                    logger=shared_logger
                )
                # Create initial strategy snapshot
                snapshot_id = await strategy_snapshot_manager.save_current_config(
                    config=config,
                    notes="Bot startup - initial configuration snapshot"
                )
                shared_logger.info(f"✅ Strategy snapshot initialized: {snapshot_id}")
                # Store manager in shared_data_manager for global access
                shared_data_manager.strategy_snapshot_manager = strategy_snapshot_manager
            except Exception as e:
                shared_logger.error(f"❌ Failed to initialize strategy snapshot: {e}", exc_info=True)
                # Non-fatal - continue bot startup even if strategy tracking fails

            # ✅ One-time FIFO Debugging
            # await shared_data_manager.trade_recorder.test_performance_tracker()
            # await shared_data_manager.trade_recorder.test_fifo_prod("SPK-USD")

            await run_maintenance_if_needed(shared_data_manager, shared_data_manager.trade_recorder)

            if args.run == 'webhook':
                shared_logger.info("🔧 [HTTP_SERVER_DEBUG] RUN_MODE=webhook detected, calling run_webhook()")
                await run_webhook(
                    config=config,
                    session=session,
                    coinbase_api=coinbase_api,
                    shared_data_manager=shared_data_manager,
                    market_data_updater=market_data_updater,
                    logger_manager=logger_manager,
                    alert=alert,
                    test_debug_maint=test_debug_maint,
                    shared_utils_print=shared_utils_print,
                    shared_utils_color=shared_utils_color,
                    order_book_manager=order_book_manager
                )
            elif args.run == 'sighook':
                await run_sighook(
                    config=config,
                    shared_data_manager=shared_data_manager,
                    market_data_updater=market_data_updater,
                    rest_client=config.rest_client,
                    portfolio_uuid=config.portfolio_uuid,
                    logger_manager=logger_manager,
                    alert=alert,
                    order_book_manager=None,
                    test_debug_maint=test_debug_maint,
                    shared_utils_print=shared_utils_print,
                    shared_utils_color=shared_utils_color,
                    coinbase_api=coinbase_api  # ← inject the real client
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
                    test_debug_maint=test_debug_maint,
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
                    test_debug_maint=test_debug_maint,
                    shared_utils_print=shared_utils_print,
                    shared_utils_color=shared_utils_color,
                    startup_event=startup_event,
                    listener=listener
                ))

                # ✅ Step 3: Webhook background tasks
                background_tasks = make_webhook_tasks(
                    listener=listener,
                    logger_manager=logger_manager,
                    websocket_manager=websocket_manager,
                    shared_data_manager=shared_data_manager,
                    enable_accumulation=True,
                    accumulation_manager=accumulation_manager
                )
                monitor_interval = int(config.db_monitor_interval or 10)
                threshold = int(config.db_connection_threshold or 10)
                db_monitor_task = asyncio.create_task(monitor_db_connections(shared_data_manager,
                                                                             interval=monitor_interval, threshold=threshold),
                                                      name="DB Connection Monitor")
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

async def main():
    await app_boot()
    await asyncio.Event().wait()






if __name__ == "__main__":

    setup_logging()
    _fh = setup_stack_logging()  # keep a reference

    os.environ['PYTHONASYNCIODEBUG'] = '1' # debugging turn off in production
    logger = logging.getLogger('asyncio')
    logger.setLevel(logging.ERROR)
    asyncio.run(main(), debug=DebugToggles.AIO_DEBUG)




