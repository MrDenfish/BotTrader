"""Page 5: Bot Health — runtime status, signal rate, equity curve, recent activity.

Surfaces what the bot is doing right now (and how recently) using only data
already persisted to the database. Container/process-level state and in-memory
risk events are out of scope until they have DB persistence.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import streamlit as st

from v2.dashboard.db import get_pool, run_async
from v2.dashboard.trades import collect_round_trips

EXCHANGE = "paper-kraken"

# Freshness bands for the "minutes since last activity" proxy.
# v2-kraken's polling/decision cadence is on the order of minutes, so an
# hour without any order or fill is genuinely unusual.
FRESH_MINUTES = 60
STALE_MINUTES = 360  # 6h — beyond this, treat as "no recent activity"


# ---------------------------------------------------------------------------
# Cached DB fetchers
# ---------------------------------------------------------------------------


@st.cache_data(ttl=30)
def _get_activity_summary() -> dict:
    """Latest order/fill timestamps + rolling counts."""
    pool = get_pool()

    async def _query():
        async with pool.acquire() as conn:
            last_order = await conn.fetchrow(
                "SELECT MAX(timestamp) AS ts FROM v2_orders"
            )
            last_fill = await conn.fetchrow(
                "SELECT MAX(timestamp) AS ts FROM v2_fills WHERE exchange = $1",
                EXCHANGE,
            )
            last_cancel = await conn.fetchrow(
                "SELECT MAX(timestamp) AS ts FROM v2_orders WHERE status = 'cancelled'"
            )
            counts = await conn.fetchrow(
                """
                SELECT
                  SUM(CASE WHEN timestamp > NOW() - INTERVAL '24 hours' THEN 1 ELSE 0 END) AS d1,
                  SUM(CASE WHEN timestamp > NOW() - INTERVAL '7 days' THEN 1 ELSE 0 END)  AS d7,
                  SUM(CASE WHEN timestamp::date = (NOW() AT TIME ZONE 'UTC')::date THEN 1 ELSE 0 END) AS today,
                  COUNT(*) AS total
                FROM v2_orders
                """
            )
            positions = await conn.fetchrow(
                """
                SELECT COUNT(*) AS n
                FROM v2_positions
                WHERE exchange = $1 AND qty > 0
                """,
                EXCHANGE,
            )
        return {
            "last_order": last_order["ts"],
            "last_fill": last_fill["ts"],
            "last_cancel": last_cancel["ts"],
            "orders_today": counts["today"] or 0,
            "orders_24h": counts["d1"] or 0,
            "orders_7d": counts["d7"] or 0,
            "orders_total": counts["total"] or 0,
            "open_positions": positions["n"] or 0,
        }

    return run_async(_query())


@st.cache_data(ttl=60)
def _get_daily_orders(days: int = 30) -> list[dict]:
    """Per-day order counts by status for the last N days."""
    pool = get_pool()

    async def _query():
        rows = await pool.fetch(
            """
            SELECT
              (timestamp AT TIME ZONE 'UTC')::date AS day,
              side,
              status,
              COUNT(*) AS n
            FROM v2_orders
            WHERE timestamp > NOW() - ($1::text || ' days')::interval
            GROUP BY day, side, status
            ORDER BY day
            """,
            str(days),
        )
        return [dict(r) for r in rows]

    return run_async(_query())


@st.cache_data(ttl=60)
def _get_active_symbols(days: int = 7) -> list[dict]:
    """Symbols with fill activity in the last N days. Uses v2_fills (source of truth)."""
    pool = get_pool()

    async def _query():
        rows = await pool.fetch(
            """
            SELECT
              symbol,
              COUNT(*) AS fills,
              SUM(CASE WHEN side = 'buy'  THEN 1 ELSE 0 END) AS buys,
              SUM(CASE WHEN side = 'sell' THEN 1 ELSE 0 END) AS sells,
              MAX(timestamp) AS last_seen
            FROM v2_fills
            WHERE timestamp > NOW() - ($1::text || ' days')::interval
              AND exchange = $2
            GROUP BY symbol
            ORDER BY last_seen DESC
            """,
            str(days),
            EXCHANGE,
        )
        return [dict(r) for r in rows]

    return run_async(_query())


@st.cache_data(ttl=60)
def _get_recent_orders(limit: int = 20) -> list[dict]:
    """Most recent N orders, with status derived from v2_fills.

    The Kraken exchange code emits FillEvents when orders fill but never
    re-emits an OrderEvent with status=FILLED, so v2_orders.status alone
    is unreliable. We left-join v2_fills to derive the true state.
    """
    pool = get_pool()

    async def _query():
        rows = await pool.fetch(
            """
            SELECT
              o.order_id, o.timestamp, o.symbol, o.side, o.order_type,
              o.price, o.qty, o.status AS raw_status,
              COALESCE(SUM(f.qty), 0) AS filled_qty
            FROM v2_orders o
            LEFT JOIN v2_fills f ON f.order_id = o.order_id
            GROUP BY o.order_id, o.timestamp, o.symbol, o.side, o.order_type,
                     o.price, o.qty, o.status
            ORDER BY o.timestamp DESC
            LIMIT $1
            """,
            limit,
        )
        return [dict(r) for r in rows]

    return run_async(_query())


def _derive_status(raw_status: str, filled_qty: float, qty: float) -> str:
    """Reconcile v2_orders.status with v2_fills for the true order state."""
    if raw_status == "cancelled":
        # An order can be partially filled then cancelled — show that distinctly.
        if filled_qty > 1e-9:
            return "partial+cancelled"
        return "cancelled"
    if filled_qty + 1e-9 >= qty:
        return "filled"
    if filled_qty > 1e-9:
        return "partial"
    return "open"


@st.cache_data(ttl=60)
def _get_equity_curve():
    """Cumulative net P&L over time, from FIFO-matched round trips."""
    start = datetime(2020, 1, 1, tzinfo=timezone.utc)
    end = datetime.now(timezone.utc)
    round_trips, _ = run_async(
        collect_round_trips(get_pool(), start, end, EXCHANGE)
    )
    return round_trips


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ensure_utc(ts: datetime | None) -> datetime | None:
    if ts is None:
        return None
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


def _minutes_since(ts: datetime | None) -> float | None:
    ts = _ensure_utc(ts)
    if ts is None:
        return None
    return (datetime.now(timezone.utc) - ts).total_seconds() / 60.0


def _format_age(minutes: float | None) -> str:
    if minutes is None:
        return "—"
    if minutes < 60:
        return f"{minutes:.0f}m ago"
    hours = minutes / 60
    if hours < 24:
        return f"{hours:.1f}h ago"
    return f"{hours / 24:.1f}d ago"


def _freshness_label(minutes: float | None) -> tuple[str, str]:
    """Returns (label, color) for the status pill."""
    if minutes is None:
        return ("No data", "off")
    if minutes <= FRESH_MINUTES:
        return ("Active", "normal")
    if minutes <= STALE_MINUTES:
        return ("Idle", "off")
    return ("Stale", "inverse")


# ---------------------------------------------------------------------------
# Panel renderers
# ---------------------------------------------------------------------------


def _render_status(summary: dict) -> None:
    st.subheader("Status")

    last_order = summary["last_order"]
    last_fill = summary["last_fill"]
    # Use the more recent of order or fill as the freshness signal — either
    # implies the bot is making decisions and the persistence layer is live.
    candidates = [t for t in (last_order, last_fill) if t is not None]
    latest = max(candidates) if candidates else None
    age_min = _minutes_since(latest)
    label, color = _freshness_label(age_min)

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric(
            "Bot Activity",
            label,
            _format_age(age_min) if age_min is not None else None,
            delta_color=color,
        )
    with col2:
        st.metric("Open Positions", summary["open_positions"])
    with col3:
        st.metric("Orders Today (UTC)", summary["orders_today"])
    with col4:
        st.metric("Orders (Lifetime)", f"{summary['orders_total']:,}")

    st.caption(
        f"Freshness is a proxy from DB activity (≤{FRESH_MINUTES}m = Active, "
        f"≤{STALE_MINUTES // 60}h = Idle, otherwise Stale). "
        "It does not check container state directly — SSH for that."
    )


def _render_activity(summary: dict) -> None:
    st.subheader("Recent Activity")

    rows = [
        {
            "Event": "Last order placed",
            "Timestamp (UTC)": (
                _ensure_utc(summary["last_order"]).strftime("%Y-%m-%d %H:%M:%S")
                if summary["last_order"]
                else "—"
            ),
            "Age": _format_age(_minutes_since(summary["last_order"])),
        },
        {
            "Event": "Last fill",
            "Timestamp (UTC)": (
                _ensure_utc(summary["last_fill"]).strftime("%Y-%m-%d %H:%M:%S")
                if summary["last_fill"]
                else "—"
            ),
            "Age": _format_age(_minutes_since(summary["last_fill"])),
        },
        {
            "Event": "Last cancel",
            "Timestamp (UTC)": (
                _ensure_utc(summary["last_cancel"]).strftime("%Y-%m-%d %H:%M:%S")
                if summary["last_cancel"]
                else "—"
            ),
            "Age": _format_age(_minutes_since(summary["last_cancel"])),
        },
    ]
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Orders — Last 24h", summary["orders_24h"])
    with col2:
        st.metric("Orders — Last 7d", summary["orders_7d"])
    with col3:
        rate_per_day = summary["orders_7d"] / 7 if summary["orders_7d"] else 0
        st.metric("Avg Orders/Day (7d)", f"{rate_per_day:.1f}")


def _render_daily_orders(rows) -> None:
    st.subheader("Daily Order Submissions (30d)")
    st.caption(
        "Count of orders submitted per day, by side and cancellation. "
        "Approximates the bot's daily signal-generation rate."
    )

    if not rows:
        st.info("No orders in the last 30 days.")
        return

    # Pivot to one column per side, indexed by day.
    df = pd.DataFrame(
        [
            {
                "day": r["day"],
                "side": r["side"],
                "status": r["status"],
                "n": r["n"],
            }
            for r in rows
        ]
    )
    # Label cancelled orders distinctly from filled-buy/sell.
    df["bucket"] = df.apply(
        lambda r: "cancelled" if r["status"] == "cancelled" else r["side"],
        axis=1,
    )
    pivot = (
        df.groupby(["day", "bucket"])["n"].sum().unstack(fill_value=0).sort_index()
    )

    # Ensure consistent column order if present.
    for col in ("buy", "sell", "cancelled"):
        if col not in pivot.columns:
            pivot[col] = 0
    pivot = pivot[["buy", "sell", "cancelled"]]

    st.bar_chart(pivot, height=300)


def _render_active_symbols(rows) -> None:
    st.subheader("Active Symbols (7d)")

    if not rows:
        st.info("No fill activity in the last 7 days.")
        return

    now = datetime.now(timezone.utc)
    table = []
    for r in rows:
        last = _ensure_utc(r["last_seen"])
        age_min = (now - last).total_seconds() / 60 if last else None
        table.append(
            {
                "Symbol": r["symbol"],
                "Fills": r["fills"],
                "Buys": r["buys"],
                "Sells": r["sells"],
                "Last Fill (UTC)": last.strftime("%Y-%m-%d %H:%M") if last else "—",
                "Age": _format_age(age_min),
            }
        )
    st.dataframe(pd.DataFrame(table), hide_index=True, use_container_width=True)


def _render_equity_curve(round_trips) -> None:
    st.subheader("Equity Curve (Realized Net P&L)")

    if not round_trips:
        st.info("No completed round trips yet.")
        return

    # Sort by exit time; cumulative sum of net P&L gives the equity curve.
    sorted_trips = sorted(round_trips, key=lambda t: t.sell_timestamp)
    df = pd.DataFrame(
        [
            {
                "sell_time": _ensure_utc(t.sell_timestamp),
                "net_pnl": t.net_pnl,
            }
            for t in sorted_trips
        ]
    )
    df = df.set_index("sell_time")
    df["cum_net_pnl"] = df["net_pnl"].cumsum()

    st.line_chart(df["cum_net_pnl"], height=300)

    total = float(df["net_pnl"].sum())
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Realized P&L (All Time)", f"${total:,.2f}")
    with col2:
        st.metric("Round Trips", len(sorted_trips))
    with col3:
        peak = float(df["cum_net_pnl"].max())
        drawdown = total - peak
        st.metric("Drawdown from Peak", f"${drawdown:,.2f}")


def _render_recent_orders(rows) -> None:
    st.subheader("Recent Orders")

    if not rows:
        st.info("No recent orders.")
        return

    table = []
    for r in rows:
        ts = _ensure_utc(r["timestamp"])
        qty = float(r["qty"])
        filled = float(r["filled_qty"])
        status = _derive_status(r["raw_status"], filled, qty)
        table.append(
            {
                "Time (UTC)": ts.strftime("%Y-%m-%d %H:%M:%S") if ts else "—",
                "Symbol": r["symbol"],
                "Side": r["side"],
                "Type": r["order_type"],
                "Price ($)": round(float(r["price"]), 4),
                "Qty": qty,
                "Filled": filled,
                "Status": status,
            }
        )
    st.dataframe(pd.DataFrame(table), hide_index=True, use_container_width=True)
    st.caption(
        "Status is derived from v2_fills (source of truth). "
        "v2_orders.status alone shows most fills as 'open' because the "
        "exchange code doesn't update order status on fill — known persistence "
        "gap, fix deferred until after the July review."
    )


# ---------------------------------------------------------------------------
# Page layout
# ---------------------------------------------------------------------------


st.header("Bot Health")
st.caption(
    "Live operational view of v2-kraken. Auto-refreshes per Streamlit cache TTL "
    "(30s for status, 60s for charts). Risk events (vetoes, circuit breaker trips) "
    "are tracked in process memory only and are not yet surfaced here."
)

summary = _get_activity_summary()
_render_status(summary)
st.divider()
_render_activity(summary)
st.divider()
_render_daily_orders(_get_daily_orders(30))
st.divider()
_render_active_symbols(_get_active_symbols(7))
st.divider()
_render_equity_curve(_get_equity_curve())
st.divider()
_render_recent_orders(_get_recent_orders(20))
