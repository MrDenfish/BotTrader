"""Page 1: Performance Report — replaces 4-hour email reports.

Panels: date range selector, hero metrics, portfolio summary,
P&L by symbol, exit reason breakdown, open positions, trade log.
"""

import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import pandas as pd
import streamlit as st

from v2.dashboard.db import get_pool, run_async
from v2.dashboard.prices import fetch_live_prices
from v2.plugins.observability.daily_report_v2.collectors.pnl import collect_pnl
from v2.plugins.observability.daily_report_v2.collectors.positions import (
    collect_positions,
)
from v2.plugins.observability.daily_report_v2.collectors.trade_log import (
    collect_trade_log,
)
from v2.plugins.observability.daily_report_v2.collectors.trade_stats import (
    collect_trade_stats,
)

EXCHANGE = "paper-kraken"
STARTING_CAPITAL = float(os.environ.get("STARTING_CAPITAL", "10000"))


# ---------------------------------------------------------------------------
# Cached data fetchers — 60-second TTL, one DB round-trip per collector
# ---------------------------------------------------------------------------
# Datetime args are rounded to the minute in _date_range_selector() so that
# cache keys stay stable within a 60-second window.


@st.cache_data(ttl=60)
def _get_pnl(start: datetime, end: datetime):
    return run_async(collect_pnl(get_pool(), start, end, EXCHANGE))


@st.cache_data(ttl=60)
def _get_trade_stats(start: datetime, end: datetime):
    return run_async(collect_trade_stats(get_pool(), start, end, EXCHANGE))


@st.cache_data(ttl=60)
def _get_trade_log(start: datetime, end: datetime):
    return run_async(collect_trade_log(get_pool(), start, end, EXCHANGE))


@st.cache_data(ttl=60)
def _get_positions():
    return run_async(collect_positions(get_pool(), EXCHANGE))


@st.cache_data(ttl=60)
def _get_live_prices(symbols: tuple[str, ...]) -> dict[str, float]:
    return fetch_live_prices(list(symbols))


@st.cache_data(ttl=60)
def _get_entry_times(symbols: tuple[str, ...]):
    """Most recent buy timestamp per symbol — proxy for position entry time."""
    pool = get_pool()

    async def _query():
        return await pool.fetch(
            """
            SELECT DISTINCT ON (symbol) symbol, timestamp
            FROM v2_fills
            WHERE side = 'buy' AND symbol = ANY($1) AND exchange = $2
            ORDER BY symbol, timestamp DESC
            """,
            list(symbols),
            EXCHANGE,
        )

    rows = run_async(_query())
    return {row["symbol"]: row["timestamp"] for row in rows}


# ---------------------------------------------------------------------------
# Date range selector
# ---------------------------------------------------------------------------


def _date_range_selector() -> tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    # Round to the minute so cache keys stay stable
    end = now.replace(second=0, microsecond=0)

    options = ["Today", "7 days", "30 days", "All time", "Custom"]
    choice = st.selectbox("Time range", options, index=1)

    if choice == "Today":
        start = end.replace(hour=0, minute=0)
    elif choice == "7 days":
        start = (end - timedelta(days=7)).replace(hour=0, minute=0)
    elif choice == "30 days":
        start = (end - timedelta(days=30)).replace(hour=0, minute=0)
    elif choice == "All time":
        start = datetime(2020, 1, 1, tzinfo=timezone.utc)
    else:  # Custom
        col1, col2 = st.columns(2)
        with col1:
            start_date = st.date_input(
                "Start date", end.date() - timedelta(days=7)
            )
        with col2:
            end_date = st.date_input("End date", end.date())
        start = datetime.combine(start_date, datetime.min.time()).replace(
            tzinfo=timezone.utc
        )
        end = datetime.combine(end_date, datetime.max.time()).replace(
            tzinfo=timezone.utc
        )
        return start, end

    return start, end


# ---------------------------------------------------------------------------
# Panel renderers
# ---------------------------------------------------------------------------


def _render_hero(pnl, stats):
    st.subheader("P&L Summary")

    total_trades = pnl.win_count + pnl.loss_count

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Net P&L", f"${float(pnl.net_pnl):,.2f}")
    with col2:
        st.metric(
            "Win Rate",
            f"{pnl.win_rate:.1%}" if total_trades else "\u2014",
        )
    with col3:
        st.metric("Trades", f"{pnl.win_count}W / {pnl.loss_count}L")
    with col4:
        st.metric("Total Fees", f"${float(pnl.total_fees):,.2f}")

    if pnl.best_trade or pnl.worst_trade:
        col1, col2 = st.columns(2)
        with col1:
            if pnl.best_trade:
                sym, val = pnl.best_trade
                st.metric("Best Trade", sym, f"+${float(val):,.2f}")
        with col2:
            if pnl.worst_trade:
                sym, val = pnl.worst_trade
                st.metric("Worst Trade", sym, f"${float(val):,.2f}")


def _render_portfolio(alltime_pnl, positions):
    """Portfolio panel always uses all-time P&L, regardless of date range."""
    st.subheader("Portfolio")

    total_unrealized = sum(
        p.unrealized_pnl for p in positions if p.unrealized_pnl is not None
    )
    has_live_prices = any(p.current_price is not None for p in positions)

    realized = float(alltime_pnl.net_pnl)
    portfolio_value = STARTING_CAPITAL + realized + total_unrealized
    net_return_pct = (portfolio_value - STARTING_CAPITAL) / STARTING_CAPITAL

    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        st.metric("Portfolio Value", f"${portfolio_value:,.2f}")
    with col2:
        st.metric("Starting Capital", f"${STARTING_CAPITAL:,.2f}")
    with col3:
        st.metric("Realized P&L (All Time)", f"${realized:,.2f}")
    with col4:
        label = "Unrealized P&L" if has_live_prices else "Unrealized P&L*"
        st.metric(
            label,
            f"${total_unrealized:,.2f}" if positions else "\u2014",
        )
    with col5:
        st.metric("Net Return", f"{net_return_pct:.2%}")

    if positions and not has_live_prices:
        st.caption(
            "\\* Unrealized P&L from DB snapshot \u2014 "
            "live Kraken prices unavailable."
        )


def _render_pnl_by_symbol(pnl):
    st.subheader("P&L by Symbol")

    if not pnl.by_symbol:
        st.info("No completed trades in this period.")
        return

    rows = []
    for symbol, net_pnl in sorted(
        pnl.by_symbol.items(), key=lambda x: float(x[1]), reverse=True
    ):
        fills = pnl.fills_by_symbol.get(symbol, {})
        buys = fills.get("BUY", 0)
        sells = fills.get("SELL", 0)
        total = buys + sells
        rows.append(
            {
                "Symbol": symbol,
                "Trades": f"{buys}B / {sells}S",
                "Fills": total,
                "Net P&L ($)": round(float(net_pnl), 2),
            }
        )

    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)


def _render_exit_breakdown(trades):
    """Build exit breakdown from trade log — FIFO-correct P&L per reason."""
    st.subheader("Exit Reason Breakdown")

    # Group sell fills by exit_reason (dynamic — not hardcoded)
    exit_data: dict[str, dict] = defaultdict(
        lambda: {"count": 0, "net_pnl": 0.0}
    )
    for entry in trades:
        if entry.side == "SELL" and entry.exit_reason:
            bucket = exit_data[entry.exit_reason]
            bucket["count"] += 1
            bucket["net_pnl"] += entry.realized_pnl or 0.0

    if not exit_data:
        st.info("No exit events in this period.")
        return

    rows = []
    for reason, data in sorted(
        exit_data.items(), key=lambda x: x[1]["count"], reverse=True
    ):
        rows.append(
            {
                "Exit Reason": reason,
                "Count": data["count"],
                "Net P&L ($)": round(data["net_pnl"], 2),
            }
        )

    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)


def _render_positions(positions, entry_times):
    st.subheader("Open Positions")

    if not positions:
        st.info("No open positions.")
        return

    now = datetime.now(timezone.utc)
    rows = []
    for p in positions:
        # Compute hold time
        hold_str = "\u2014"
        entry_ts = entry_times.get(p.symbol)
        if entry_ts:
            if entry_ts.tzinfo is None:
                entry_ts = entry_ts.replace(tzinfo=timezone.utc)
            delta = now - entry_ts
            hours = delta.total_seconds() / 3600
            if hours >= 24:
                hold_str = f"{hours / 24:.1f}d"
            else:
                hold_str = f"{hours:.1f}h"

        rows.append(
            {
                "Symbol": p.symbol,
                "Qty": p.qty,
                "Entry Price ($)": round(p.avg_entry, 4),
                "Current Price ($)": (
                    round(p.current_price, 4)
                    if p.current_price is not None
                    else None
                ),
                "Cost Basis ($)": round(p.cost_basis, 2),
                "Unrealized P&L ($)": (
                    round(p.unrealized_pnl, 2)
                    if p.unrealized_pnl is not None
                    else None
                ),
                "Hold Time": hold_str,
            }
        )

    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)


def _render_trade_log(trades):
    st.subheader("Trade Log")

    if not trades:
        st.info("No fills in this period.")
        return

    rows = []
    for t in reversed(trades):  # Most recent first
        rows.append(
            {
                "Time (UTC)": t.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                "Symbol": t.symbol,
                "Side": t.side,
                "Price ($)": round(t.price, 4),
                "Qty": t.qty,
                "Notional ($)": round(t.notional, 2),
                "Fee ($)": round(t.fee, 4),
                "P&L ($)": (
                    round(t.realized_pnl, 2)
                    if t.realized_pnl is not None
                    else None
                ),
                "Exit Reason": t.exit_reason or "",
            }
        )

    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# Page layout
# ---------------------------------------------------------------------------

st.header("Performance Report")

# Date range
start, end = _date_range_selector()
st.caption(
    f"Period: {start.strftime('%Y-%m-%d %H:%M')} \u2014 "
    f"{end.strftime('%Y-%m-%d %H:%M')} UTC"
)

# Fetch all data
pnl = _get_pnl(start, end)
alltime_pnl = _get_pnl(datetime(2020, 1, 1, tzinfo=timezone.utc), end)
stats = _get_trade_stats(start, end)
trades = _get_trade_log(start, end)
positions = _get_positions()

# Fetch live prices for open positions (graceful degradation)
if positions:
    symbols = tuple(p.symbol for p in positions)
    live_prices = _get_live_prices(symbols)
    entry_times = _get_entry_times(symbols)
    # Apply live prices — positions are a deep copy from cache, safe to mutate
    for p in positions:
        if p.symbol in live_prices:
            p.current_price = live_prices[p.symbol]
            p.unrealized_pnl = p.qty * live_prices[p.symbol] - p.cost_basis
else:
    live_prices = {}
    entry_times = {}

# Render all panels
_render_hero(pnl, stats)
st.divider()
_render_portfolio(alltime_pnl, positions)
st.divider()
_render_pnl_by_symbol(pnl)
st.divider()
_render_exit_breakdown(trades)
st.divider()
_render_positions(positions, entry_times)
st.divider()
_render_trade_log(trades)
