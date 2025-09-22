# SharedDataManager/leaderboard_runner.py
import asyncio
import argparse
import logging
import os

# Reuse the same wiring helpers that main.py uses
from main import load_config, init_shared_data
from Shared_Utils.logging_manager import LoggerManager

async def run_leaderboard():
    parser = argparse.ArgumentParser(description="Recompute rolling leaderboard into active_symbols.")
    parser.add_argument("--lookback-hours", type=int, default=24)
    parser.add_argument("--min-n-24h", type=int, default=3)
    parser.add_argument("--win-rate-min", type=float, default=0.35)
    parser.add_argument("--pf-min", type=float, default=1.30)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    # Match main.py’s logging style
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logger_manager = LoggerManager({"log_level": log_level})
    shared_logger = logger_manager.get_logger("shared_logger")

    # Load config the same way main.py does
    config = await load_config()

    # We don’t need Coinbase REST/WebSocket for leaderboard; pass coinbase_api=None
    shared_data_manager = await init_shared_data(
        config=config,
        logger_manager=logger_manager,
        shared_logger=shared_logger,
        coinbase_api=None,
    )

    # Recompute + upsert into active_symbols
    await shared_data_manager.recompute_leaderboard(
        lookback_hours=args.lookback_hours,
        min_n_24h=args.min_n_24h,
        win_rate_min=args.win_rate_min,
        pf_min=args.pf_min,
    )
    shared_logger.info("✅ Leaderboard recomputed.")

def main():
    # Keep asyncio debug off (consistent with main.py defaults)
    os.environ.setdefault("PYTHONASYNCIODEBUG", "0")
    asyncio.run(run_leaderboard())

if __name__ == "__main__":
    main()
