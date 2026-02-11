"""Entry point: python -m v2 --config config.yaml"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys


def main() -> None:
    # Intercept "report" subcommand before argparse
    if len(sys.argv) > 1 and sys.argv[1] == "report":
        from v2.plugins.observability.daily_report_v2.cli import report_main
        report_main(sys.argv[2:])
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

    print("\n" + "=" * 70)
    print("BACKTEST RESULTS (v2)")
    print("=" * 70)

    print(f"Trades: {stats.get('trades', 0)}")
    print(f"Gross P&L: ${stats.get('gross_pnl', 0):.2f}")
    print(f"Fees: ${stats.get('fees', 0):.2f}")
    print(f"Net P&L: ${stats.get('net_pnl', 0):.2f}")

    if stats.get('trades', 0) > 0:
        print(f"Win Rate: {stats.get('win_rate', 0):.1%}")
        print(f"Profit Factor: {stats.get('profit_factor', 0):.2f}")
        print(f"Avg Win: ${stats.get('avg_win', 0):.2f}")
        print(f"Avg Loss: ${stats.get('avg_loss', 0):.2f}")

    print("=" * 70)

    # Print enhanced diagnostics if available
    for strategy in app._strategies:
        if hasattr(strategy, "print_enhanced_diagnostics"):
            print(strategy.print_enhanced_diagnostics())


if __name__ == "__main__":
    main()
