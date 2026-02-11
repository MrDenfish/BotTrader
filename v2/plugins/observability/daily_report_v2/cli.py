"""CLI entry point: python -m v2 report --config config.yaml --hours 24 [--send] [--output file.html]"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timedelta, timezone


def report_main(argv: list[str]) -> None:
    """Parse args and run the report generator."""
    parser = argparse.ArgumentParser(
        prog="bottrader-v2 report",
        description="Generate a daily trading report",
    )
    parser.add_argument("--config", "-c", required=True, help="Path to YAML config file")
    parser.add_argument("--hours", type=int, default=24, help="Reporting period in hours (default: 24)")
    parser.add_argument("--date", type=str, default=None, help="Report date (YYYY-MM-DD). Defaults to yesterday.")
    parser.add_argument("--send", action="store_true", help="Send via configured delivery channels")
    parser.add_argument("--output", "-o", type=str, default=None, help="Write HTML to file")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    asyncio.run(_run_report(args))


async def _run_report(args: argparse.Namespace) -> None:
    """Load config, run collectors, render, and optionally send."""
    from v2.core.config import Config

    cfg = Config.load(args.config)

    # Find the daily_report_v2 observer config
    observer_cfg = None
    for o in cfg.observers:
        if o.type == "daily_report_v2":
            observer_cfg = o
            break

    if observer_cfg is None:
        print("Error: No 'daily_report_v2' observer found in config", file=sys.stderr)
        sys.exit(1)

    # Create the observer (with its config)
    from . import DailyReportV2Observer
    observer = DailyReportV2Observer(event_bus=None, **observer_cfg.config)

    # Determine report date
    if args.date:
        from datetime import date
        report_date = date.fromisoformat(args.date)
    else:
        report_date = (datetime.now(timezone.utc) - timedelta(hours=1)).date()

    print(f"Generating report for {report_date} ({args.hours}h period)...")

    data = await observer.generate_report(report_date=report_date, period_hours=args.hours)

    # Render HTML
    from .renderers.html import HtmlRenderer
    html = HtmlRenderer().render(data)

    # Output
    if args.output:
        from pathlib import Path
        Path(args.output).write_text(html)
        print(f"HTML report written to {args.output}")
    elif not args.send:
        # Preview: print summary to stdout
        _print_summary(data)

    # Send
    if args.send:
        from .delivery.email import send_email
        from .delivery.slack import send_slack
        from .renderers.slack import SlackRenderer

        subject = f"BotTrader v2 Daily Report — {report_date}"

        if observer._email_config.sender and observer._email_config.recipients:
            ok = send_email(subject, html, observer._email_config)
            print(f"Email: {'sent' if ok else 'failed'}")
        else:
            print("Email: not configured (no sender/recipients)")

        blocks = SlackRenderer().render(data)
        ok = await send_slack(blocks, webhook_url_env=observer._slack_webhook_url_env)
        print(f"Slack: {'sent' if ok else 'not configured or failed'}")

    print("Done.")


def _print_summary(data) -> None:
    """Print a text summary to stdout."""
    print()
    print(f"{'=' * 60}")
    print(f"  BotTrader v2 — Daily Report for {data.report_date}")
    print(f"{'=' * 60}")
    print()

    ts = data.trade_stats
    print(f"  Trading: {ts.buy_count} buys (${float(ts.buy_volume_usd):,.2f}) / "
          f"{ts.sell_count} sells (${float(ts.sell_volume_usd):,.2f})")
    print(f"  Fees: ${float(ts.total_fees):,.2f}")
    print(f"  Symbols: {', '.join(ts.symbols_traded) if ts.symbols_traded else 'none'}")
    print()

    pnl = data.pnl
    print(f"  Net P&L: ${float(pnl.net_pnl):,.2f}")
    print(f"  Win Rate: {pnl.win_rate:.1%} ({pnl.win_count}W / {pnl.loss_count}L)")
    if pnl.best_trade:
        print(f"  Best: {pnl.best_trade[0]} ${float(pnl.best_trade[1]):,.2f}")
    if pnl.worst_trade:
        print(f"  Worst: {pnl.worst_trade[0]} ${float(pnl.worst_trade[1]):,.2f}")
    print()

    print(f"  Signals: {data.signals.total} ({data.signals.buy_count}B / {data.signals.sell_count}S)")
    print(f"  Risk: {data.risk.vetoes} vetoes, {data.risk.circuit_breaker_trips} CB trips, "
          f"{data.risk.rejections} rejections")
    print()

    if data.comparison:
        c = data.comparison
        print(f"  v1 vs v2 Agreement: {c.agreement_rate:.1%}")
        print(f"    v1: {c.v1_signal_count} signals, v2: {c.v2_signal_count} signals")
        print(f"    Matched: {c.agreement_count}, v1-only: {c.v1_only_count}, v2-only: {c.v2_only_count}")
        if c.symbols_only_v1:
            print(f"    v1-only symbols: {', '.join(sorted(c.symbols_only_v1))}")
        if c.symbols_only_v2:
            print(f"    v2-only symbols: {', '.join(sorted(c.symbols_only_v2))}")
        print()

    print(f"{'=' * 60}")
