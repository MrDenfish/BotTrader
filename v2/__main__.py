"""Entry point: python -m v2 --config config.yaml"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys


def main() -> None:
    # Intercept subcommands before argparse
    if len(sys.argv) > 1 and sys.argv[1] == "report":
        from v2.plugins.observability.daily_report_v2.cli import report_main
        report_main(sys.argv[2:])
        return

    if len(sys.argv) > 1 and sys.argv[1] == "optimize":
        from v2.backtest.optimizer import optimize_main
        optimize_main(sys.argv[2:])
        return

    parser = argparse.ArgumentParser(
        prog="bottrader-v2",
        description="BotTrader v2 — plugin-based trading system",
    )
    parser.add_argument(
        "--config", "-c",
        default=None,
        help="Path to YAML config file",
    )
    parser.add_argument(
        "--mode", "-m",
        choices=["live", "paper", "backtest"],
        default=None,
        help="Override app mode",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO)",
    )
    parser.add_argument(
        "--list-plugins",
        action="store_true",
        help="List registered plugins and exit",
    )
    args = parser.parse_args()

    # Logging
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # List plugins mode
    if args.list_plugins:
        from v2.core import registry
        registry.discover_plugins()
        for category, names in registry.list_plugins().items():
            print(f"{category}: {', '.join(names) if names else '(none)'}")
        return

    # Build overrides from CLI
    overrides = {}
    if args.mode:
        overrides["app"] = {"mode": args.mode}

    # Run
    from v2.core.app import App
    app = App(config_path=args.config, overrides=overrides or None)
    try:
        result = asyncio.run(app.run())
        if result is not None:
            _print_backtest_results(result, app)
    except KeyboardInterrupt:
        pass


def _print_backtest_results(stats: dict, app) -> None:
    """Print backtest results summary."""
    if "error" in stats:
        print(f"\nError: {stats['error']}")
        return

    trades = stats.get('trades', 0)
    print("\n" + "=" * 70)
    print("BACKTEST RESULTS (v2)")
    print("=" * 70)

    print(f"  Trades:         {trades}  ({stats.get('winners', 0)}W / {stats.get('losers', 0)}L)")
    print(f"  Gross P&L:      ${stats.get('gross_pnl', 0):>10.2f}")
    print(f"  Fees:           ${stats.get('fees', 0):>10.2f}")
    print(f"  Net P&L:        ${stats.get('net_pnl', 0):>10.2f}")

    if trades > 0:
        pf = stats.get('profit_factor', 0)
        pf_str = f"{pf:.2f}" if pf != float('inf') else "inf"
        print(f"  Win Rate:       {stats.get('win_rate', 0):>10.1%}")
        print(f"  Profit Factor:  {pf_str:>10}")
        print(f"  Avg Win:        ${stats.get('avg_win', 0):>10.2f}")
        print(f"  Avg Loss:       ${stats.get('avg_loss', 0):>10.2f}")
        if stats.get('avg_hold_minutes'):
            hold_hrs = stats['avg_hold_minutes'] / 60
            print(f"  Avg Hold:       {hold_hrs:>10.1f} hrs")
        print(f"  Max Drawdown:   {stats.get('max_drawdown_pct', 0):>10.2f}%")

    print(f"  Open Positions: {stats.get('open_positions', 0)}")

    # Signals / fills / vetoes
    print(f"\n  Signals:  {stats.get('signals_buy', 0)} buy / {stats.get('signals_sell', 0)} sell")
    print(f"  Fills:    {stats.get('buy_fills', 0)} buy / {stats.get('sell_fills', 0)} sell")
    print(f"  Vetoes:   {stats.get('vetoes', 0)}")

    # Exit reasons
    exit_reasons = stats.get('exit_reasons', {})
    if exit_reasons:
        print(f"\n  Exit Reasons:")
        for reason, count in sorted(exit_reasons.items(), key=lambda x: -x[1]):
            print(f"    {reason:20s} {count:>3}")

    # Trade log
    trade_log = stats.get('trade_log', [])
    if trade_log:
        print(f"\n  {'Symbol':<10} {'Entry':>10} {'Exit':>10} {'P&L':>8} {'P&L%':>7} {'Hold':>8} {'Reason'}")
        print("  " + "-" * 68)
        for t in trade_log:
            hold_hrs = t['hold_min'] / 60
            print(f"  {t['symbol']:<10} ${t['entry_price']:>9.2f} ${t['exit_price']:>9.2f} "
                  f"${t['net_pnl']:>7.2f} {t['pnl_pct']:>6.1f}% {hold_hrs:>6.1f}h {t['exit_reason']}")

    print("=" * 70)

    # Print enhanced diagnostics if available
    for strategy in app._strategies:
        if hasattr(strategy, "print_enhanced_diagnostics"):
            print(strategy.print_enhanced_diagnostics())


if __name__ == "__main__":
    main()
