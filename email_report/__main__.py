# optional: `python -m email_report ...`
from __future__ import annotations

import os
import asyncio
import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy.ext.asyncio import create_async_engine
import sqlalchemy

from .metrics_compute import (
    fetch_trade_stats,
    fetch_sharpe_trade,
    fetch_max_drawdown,
)


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    v = os.getenv(name)
    return v if v not in (None, "") else default


def build_db_url_from_env() -> str:
    db_url = _env("DATABASE_URL")
    if db_url:
        if db_url.startswith("postgresql://"):
            db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return db_url
    host = _env("DB_HOST", "db")
    port = _env("DB_PORT", "5432")
    name = _env("DB_NAME", "bot_trader_db")
    user = _env("DB_USER", "bot_user")
    pwd  = _env("DB_PASSWORD", "")
    return f"postgresql+asyncpg://{user}:{pwd}@{host}:{port}/{name}"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Compute metrics and render a tiny HTML preview.")
    p.add_argument("--hours", type=int, default=24, help="Window length ending at --as-of (default: 24)")
    p.add_argument("--as-of", type=str, default=None, help="As-of timestamp in UTC (ISO 8601). Default: now()")
    p.add_argument("--use-report-trades", action="store_true",
                   help="Use public.report_trades instead of trade_records.")
    p.add_argument("--out", type=str, default="/tmp/daily_report_preview.html",
                   help="Path to write the HTML preview.")
    p.add_argument("--starting-equity", type=float, default=3000.0,
                   help="Starting equity (USD) used to normalize drawdown.")
    p.add_argument("--since-inception", action="store_true",
                   help="Override start time to inception (min ts) for the chosen source.")
    return p.parse_args()


def render_tiny_html(
    *,
    as_of_utc: datetime,
    stats: dict,
    sharpe: Optional[dict],
    mdd: Optional[dict],
    window_hours: int,
    source: str,
    starting_equity: float,
    since_inception: bool,
) -> str:
    def fmt_money(x: float) -> str:
        return f"${x:,.2f}"

    mean_pnl = sharpe.get("mean_pnl_per_trade", 0.0) if sharpe else 0.0
    stdev_pnl = sharpe.get("stdev_pnl_per_trade", 0.0) if sharpe else 0.0
    sharpe_like = sharpe.get("sharpe_like_per_trade", 0.0) if sharpe else 0.0
    dd_pct = mdd.get("max_drawdown_pct", 0.0) if mdd else 0.0
    dd_abs = mdd.get("max_drawdown_abs", 0.0) if mdd else 0.0

    html = f"""<!doctype html>
<html>
  <head>
    <meta charset="utf-8">
    <title>Daily Trading Bot Report (Preview)</title>
    <style>
      body {{ font-family: -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif; margin: 24px; }}
      h1 {{ margin: 0 0 8px 0; }}
      small {{ color: #666; }}
      table {{ border-collapse: collapse; margin-top: 16px; }}
      th, td {{ border: 1px solid #ddd; padding: 8px 10px; text-align: right; }}
      th:first-child, td:first-child {{ text-align: left; }}
      th {{ background: #f6f6f6; }}
      .note {{ margin-top: 14px; color: #666; font-size: 12px; }}
      .metrics {{ margin-top: 8px; }}
    </style>
  </head>
  <body>
    <h1>Daily Trading Bot Report</h1>
    <div><small>As of: {as_of_utc.astimezone(timezone.utc).isoformat()} (UTC) · Window: {'since inception' if since_inception else f'last {window_hours}h'} · Source: {source}</small></div>

    <table class="metrics">
      <thead>
        <tr><th>Stat</th><th>Value</th></tr>
      </thead>
      <tbody>
        <tr><td>Total Trades</td><td>{stats.get("n_total", 0):,}</td></tr>
        <tr><td>Breakeven Trades</td><td>{stats.get("n_breakeven", 0):,}</td></tr>
        <tr><td>Win Rate</td><td>{stats.get("win_rate_pct", 0.0):.1f}%</td></tr>
        <tr><td>Avg Win</td><td>{fmt_money(stats.get("avg_win", 0.0))}</td></tr>
        <tr><td>Avg Loss</td><td>{fmt_money(stats.get("avg_loss", 0.0))}</td></tr>
        <tr><td>Avg W / Avg L</td><td>{stats.get("avg_w_over_avg_l", 0.0):.3f}</td></tr>
        <tr><td>Profit Factor</td><td>{stats.get("profit_factor", 0.0):.3f}</td></tr>
        <tr><td>Expectancy / Trade</td><td>{fmt_money(stats.get("expectancy_per_trade", 0.0))}</td></tr>
        <tr><td>Mean PnL / Trade</td><td>{fmt_money(mean_pnl)}</td></tr>
        <tr><td>Stdev PnL / Trade</td><td>{fmt_money(stdev_pnl)}</td></tr>
        <tr><td>Sharpe-like (per trade)</td><td>{sharpe_like:.4f}</td></tr>
        <tr><td>Max Drawdown (window)</td><td>{dd_pct:.2f}% ({fmt_money(dd_abs)})</td></tr>
      </tbody>
    </table>

    <div class="note">
      Notes: Win rate includes breakevens in the denominator. Profit Factor = gross profits / gross losses.
      Starting equity for drawdown: {fmt_money(starting_equity)}.
    </div>
  </body>
</html>"""
    return html


async def get_inception_ts(conn, *, use_report_trades: bool):
    sql = sqlalchemy.text("SELECT MIN(ts) AS t0 FROM public.report_trades") if use_report_trades \
        else sqlalchemy.text("SELECT MIN(order_time) AS t0 FROM public.trade_records")
    row = (await conn.execute(sql)).mappings().first()
    return row["t0"]


async def main_async() -> int:
    args = parse_args()

    # Resolve as_of and window
    if args.as_of:
        as_of = datetime.fromisoformat(args.as_of.replace("Z", "+00:00")).astimezone(timezone.utc)
    else:
        as_of = datetime.now(timezone.utc)
    start = as_of - timedelta(hours=args.hours)

    db_url = build_db_url_from_env()
    engine = create_async_engine(db_url, pool_pre_ping=True)

    try:
        async with engine.begin() as conn:
            if args.since_inception:
                t0 = await get_inception_ts(conn, use_report_trades=args.use_report_trades)
                if t0:
                    start = t0

            stats = await fetch_trade_stats(
                conn,
                start_ts=start,
                end_ts=as_of,
                use_report_trades=args.use_report_trades,
            )
            sharpe = await fetch_sharpe_trade(
                conn,
                start_ts=start,
                end_ts=as_of,
                use_report_trades=args.use_report_trades,
            )
            mdd = await fetch_max_drawdown(
                conn,
                start_ts=start,
                end_ts=as_of,
                starting_equity=args.starting_equity,
                use_report_trades=args.use_report_trades,
            )
    finally:
        await engine.dispose()

    if not stats:
        print("No trades in window; nothing to render.")
        return 0

    html = render_tiny_html(
        as_of_utc=as_of,
        stats=stats,
        sharpe=sharpe,
        mdd=mdd,
        window_hours=args.hours,
        source=("report_trades" if args.use_report_trades else "trade_records"),
        starting_equity=args.starting_equity,
        since_inception=args.since_inception,
    )

    out_path = Path(args.out)
    out_path.write_text(html, encoding="utf-8")
    print(f"Rendered preview → {out_path}")
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()

