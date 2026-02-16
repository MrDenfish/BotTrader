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
    parser.add_argument("--hours", type=int, default=4, help="Reporting period in hours (default: 4)")
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

        if args.hours >= 24:
            subject = f"BotTrader v2 Daily Report — {report_date}"
        else:
            subject = f"BotTrader v2 {args.hours}h Report — {report_date}"

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
    pnl = data.pnl
    ts = data.trade_stats
    fills = ts.buy_count + ts.sell_count

    print()
    print(f"{'=' * 60}")
    if data.period_hours and data.period_hours < 24:
        header = f"  BotTrader v2 — {data.period_hours}h Report ({data.report_date})"
        if data.period_start and data.period_end:
            header += f" {data.period_start.strftime('%H:%M')}-{data.period_end.strftime('%H:%M')} UTC"
    else:
        header = f"  BotTrader v2 — {data.report_date}"
    print(header)
    print(f"{'=' * 60}")

    # 1. How did I do?
    sign = "+" if pnl.net_pnl > 0 else ""
    print(f"\n  P&L:      {sign}${float(pnl.net_pnl):,.2f}")
    print(f"  Win Rate: {pnl.win_rate:.1%} ({pnl.win_count}W / {pnl.loss_count}L)")
    print(f"  Fills:    {fills} ({ts.buy_count}B / {ts.sell_count}S)  Fees: ${float(ts.total_fees):,.2f}")
    if pnl.best_trade:
        print(f"  Best:     {pnl.best_trade[0]} ${float(pnl.best_trade[1]):,.2f}")
    if pnl.worst_trade:
        print(f"  Worst:    {pnl.worst_trade[0]} ${float(pnl.worst_trade[1]):,.2f}")

    # 1.5 Portfolio
    if data.portfolio:
        p = data.portfolio
        change = p.ending_value - p.starting_value
        change_pct = (change / p.starting_value * 100) if p.starting_value else 0
        csign = "+" if change >= 0 else ""
        print(f"\n  {'─' * 40}")
        print(f"  Portfolio: ${p.ending_value:,.2f} ({csign}{change_pct:.2f}%)")
        print(f"  High: ${p.high_watermark:,.2f}  Low: ${p.low_watermark:,.2f}")
        print(f"  Cash: ${p.cash_balance:,.2f}  Positions: ${p.positions_value:,.2f}")

    # 2. P&L by symbol
    if pnl.by_symbol:
        print(f"\n  {'─' * 40}")
        for sym, sym_pnl in sorted(pnl.by_symbol.items(), key=lambda x: float(x[1]), reverse=True):
            sign = "+" if sym_pnl > 0 else ""
            print(f"  {sym:<16} {sign}${float(sym_pnl):,.2f}")

    # 2.5 Trade log
    if data.trade_log:
        print(f"\n  {'─' * 40}")
        print(f"  Trade Log ({len(data.trade_log)} fills):")
        for t in data.trade_log[:15]:
            pnl_str = f"  P&L ${t.realized_pnl:+,.2f}" if t.realized_pnl is not None else ""
            print(f"  {t.timestamp.strftime('%H:%M')} {t.side:4s} {t.symbol:<12} "
                  f"{t.qty:.4f} @ ${t.price:,.2f}{pnl_str}")
        if len(data.trade_log) > 15:
            print(f"  ...and {len(data.trade_log) - 15} more")

    # 3. Open positions
    if data.positions:
        print(f"\n  {'─' * 40}")
        print(f"  Open Positions:")
        for pos in data.positions:
            ur = f"${pos.unrealized_pnl:,.2f}" if pos.unrealized_pnl is not None else "—"
            print(f"  {pos.symbol:<16} qty={pos.qty:.6f}  cost={pos.cost_basis:,.2f}  unreal={ur}")

    # 3.5 Exit manager
    if data.exit_manager:
        em = data.exit_manager
        print(f"\n  {'─' * 40}")
        print(f"  Exit Manager: {em.total_exits} exits "
              f"(hard={em.hard_stops}, soft={em.soft_stops}, trail={em.trailing_stops})")
        print(f"  Trailing activations: {em.trailing_activations}")
        for ev in em.events[:5]:
            print(f"    {ev.symbol} {ev.reason} @ ${ev.price:,.2f} ({ev.pnl_pct:+.2f}%)")

    # 4. System health
    print(f"\n  {'─' * 40}")
    print(f"  Signals:  {data.signals.total} ({data.signals.buy_count}B / {data.signals.sell_count}S)")
    if data.risk.vetoes or data.risk.circuit_breaker_trips or data.risk.rejections:
        print(f"  Risk:     {data.risk.vetoes} vetoes, {data.risk.circuit_breaker_trips} CB trips, "
              f"{data.risk.rejections} rejections")

    # 5. Comparison
    if data.comparison and (data.comparison.v1_signal_count or data.comparison.v2_signal_count):
        c = data.comparison
        print(f"\n  {'─' * 40}")
        print(f"  v1/v2 Agreement: {c.agreement_rate:.1%}")
        print(f"    v1: {c.v1_signal_count}  v2: {c.v2_signal_count}  matched: {c.agreement_count}")
        if c.symbols_only_v1:
            print(f"    v1-only: {', '.join(sorted(c.symbols_only_v1))}")
        if c.symbols_only_v2:
            print(f"    v2-only: {', '.join(sorted(c.symbols_only_v2))}")

    print(f"\n{'=' * 60}")
