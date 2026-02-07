"""Entry point: python -m v2 --config config.yaml"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys


def main() -> None:
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
        asyncio.run(app.run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
