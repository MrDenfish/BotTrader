# jobs/leaderboard_runner.py
import asyncio, argparse
from SharedDataManager.shared_data_manager import SharedDataManager  # same singleton/factory you use in the report


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--lookback-hours", type=int, default=24)
    parser.add_argument("--min-n-24h", type=int, default=3)
    parser.add_argument("--win-rate-min", type=float, default=0.35)
    parser.add_argument("--pf-min", type=float, default=1.30)
    args = parser.parse_args()

    sdm = SharedDataManager.get_instance()

    try:
        await sdm.recompute_leaderboard(
            lookback_hours=args.lookback_hours,
            min_n_24h=args.min_n_24h,
            win_rate_min=args.win_rate_min,
            pf_min=args.pf_min,
        )
        print("✅ Leaderboard recomputed.")
    except Exception as e:
        print(f"❌ Leaderboard recompute failed: {e}")


if __name__ == "__main__":
    asyncio.run(main())
