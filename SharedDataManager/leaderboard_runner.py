# SharedDataManager/leaderboard_runner.py
import asyncio, argparse, logging, os
from main import load_config, init_shared_data
from Shared_Utils.logging_manager import LoggerManager

async def run_leaderboard():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lookback-hours", type=int, default=24)
    ap.add_argument("--min-n-24h", type=int, default=3)
    ap.add_argument("--win-rate-min", type=float, default=0.35)
    ap.add_argument("--pf-min", type=float, default=1.30)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logger_manager = LoggerManager({"log_level": log_level})
    shared_logger = logger_manager.get_logger("shared_logger")

    config = await load_config()

    # NOTE: unpack tuple; element 0 is the SharedDataManager
    (
        shared_data_manager,
        *_rest,  # debugger, print, color, utility, precision
    ) = await init_shared_data(
        config=config,
        logger_manager=logger_manager,
        shared_logger=shared_logger,
        coinbase_api=None,  # not needed for leaderboard
    )

    await shared_data_manager.recompute_leaderboard(
        lookback_hours=args.lookback_hours,
        min_n_24h=args.min_n_24h,
        win_rate_min=args.win_rate_min,
        pf_min=args.pf_min,
    )
    shared_logger.info("✅ Leaderboard recomputed.")

def main():
    os.environ.setdefault("PYTHONASYNCIODEBUG", "0")
    asyncio.run(run_leaderboard())

if __name__ == "__main__":
    main()
