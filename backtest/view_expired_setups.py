"""
Quick diagnostic viewer for expired setups from last backtest
"""

from config_4h_hybrid import get_phase2_2_baseline_config
from engine_4h_hybrid import Hybrid4hBacktestEngine


def main():
    symbol = "BTC-USD"
    days = 60

    config = get_phase2_2_baseline_config()
    engine = Hybrid4hBacktestEngine(config, data_dir="data")

    print("Running backtest to capture diagnostics...\n")
    stats = engine.run_backtest(symbol, days)

    print("=" * 80)
    print("EXPIRED SETUP DIAGNOSTICS")
    print("=" * 80)
    print()

    expired_setups = engine.strategy.expired_setups

    if not expired_setups:
        print("No expired setups found")
        return

    print(f"Total expired setups: {len(expired_setups)}\n")

    for i, setup in enumerate(expired_setups, 1):
        print(f"--- Setup #{i} ---")
        print(f"Time:            {setup.setup_time}")
        print(f"Type:            {setup.setup_type}")
        print(f"Breakout level:  ${setup.breakout_level:.2f}")
        print(f"Expiration:      {setup.expiration_reason}")
        print(f"BB width %ile:   {setup.bb_width_pct_at_setup:.1f}")
        print()

        if setup.retest_limit_price:
            print(f"Retest bid:      ${setup.retest_limit_price:.2f}")
            if setup.retest_gap_pct is not None:
                print(f"Retest gap:      {setup.retest_gap_pct:.4f}% (missed by this much)")
            print(f"Min low:         ${setup.min_low_during_ttl:.2f}")

        if setup.chase_limit_price:
            print(f"Chase bid:       ${setup.chase_limit_price:.2f}")
            if setup.chase_gap_pct is not None:
                print(f"Chase gap:       {setup.chase_gap_pct:.4f}% (missed by this much)")
            print(f"Min low:         ${setup.min_low_during_ttl:.2f}")

        if setup.max_high_during_ttl != 0:
            print(f"Max high:        ${setup.max_high_during_ttl:.2f}")

        print()

    print("=" * 80)


if __name__ == "__main__":
    main()
