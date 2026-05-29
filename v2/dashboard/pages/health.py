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
from v2.dashboard.strategy_probe import (
    load_production_config,
    probe_universe,
    summarize,
)
from v2.dashboard.trades import collect_round_trips

EXCHANGE = "paper-kraken"

# Fallback probe universe — used when no recent fills are available to anchor
# the probe to actually-traded symbols. Mirrors the seed/fallback symbols in
# the production YAML, expanded with a few small-caps that the bot routinely
# discovers, so the regime read isn't dominated by majors alone.
_FALLBACK_PROBE_SYMBOLS = (
    "BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "ADA-USD",
    "DOGE-USD", "LTC-USD", "XMR-USD", "NEAR-USD", "SUI-USD",
)

# Friendly display labels for the per-gate metric cards. Keys must match the
# ``by_gate`` keys returned by ``strategy_probe.summarize``. The right-hand
# side is what an outside reader sees; the technical name lives in tooltips
# and column headers.
_GATE_LABELS: dict[str, str] = {
    "score": "Indicator strength",
    "indicators": "Indicator count",
    "ADX": "Trend strength",
    "RVOL": "Volume",
    "regime": "Volatility regime",
}

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


def _classify_status(
    minutes: float | None, probe_summary: dict | None
) -> tuple[str, str, str]:
    """Return (label, color, caption) for the 3-state status pill.

    Caption is only non-empty when it adds something the panel below doesn't
    already explain. The "Why is the bot idle?" panel covers the Idle case,
    so the Idle caption is intentionally empty.

      - 🟢 Active : recent DB write — bot is trading
      - 🟡 Idle   : no recent writes; probe shows guardrails are vetoing
                    every symbol (strategy is doing its job, see panel below)
      - 🔴 Stale  : no recent writes AND probe shows ≥1 symbol would pass
                    every gate — bot should have fired but didn't
    """
    if minutes is None:
        return ("⚪ No data", "off", "Database returned no order or fill rows.")
    if minutes <= FRESH_MINUTES:
        return (
            "🟢 Active",
            "normal",
            f"DB write within the last {int(minutes)} min.",
        )
    # Beyond FRESH_MINUTES we need the probe to disambiguate.
    if probe_summary is None or probe_summary["total"] == 0:
        # Probe failed or returned nothing — fall back to the old freshness
        # bands so we don't go silent on REST outages.
        if minutes <= STALE_MINUTES:
            return ("🟡 Idle", "off", "Probe unavailable — falling back to freshness bands.")
        return ("🔴 Stale", "inverse", "Probe unavailable — falling back to freshness bands.")
    if probe_summary["any_passing"]:
        return (
            "🔴 Stale",
            "inverse",
            f"{probe_summary['passing']} of {probe_summary['total']} probed symbols "
            f"would have fired — bot should be trading but isn't.",
        )
    return ("🟡 Idle", "off", "")


# ---------------------------------------------------------------------------
# Panel renderers
# ---------------------------------------------------------------------------


def _render_status(summary: dict, probe_summary: dict | None) -> None:
    st.subheader("Status")

    last_order = summary["last_order"]
    last_fill = summary["last_fill"]
    # Use the more recent of order or fill as the freshness signal — either
    # implies the bot is making decisions and the persistence layer is live.
    candidates = [t for t in (last_order, last_fill) if t is not None]
    latest = max(candidates) if candidates else None
    age_min = _minutes_since(latest)
    label, color, explanation = _classify_status(age_min, probe_summary)

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

    if explanation:
        st.caption(explanation)


def _render_why_idle(probe_results: list[dict], probe_summary: dict | None) -> None:
    """Per-symbol guardrail snapshot — explains a quiet strategy."""
    st.subheader("Why is the bot idle?")

    st.caption(
        "The bot only places a buy when several conditions line up at the same time "
        "across a single symbol: enough technical indicators agreeing on the direction, "
        "healthy trading volume, a trend strong enough to follow, and a volatility "
        "regime that's neither too quiet nor too elevated. The table below shows where "
        "each watched symbol stands right now and which condition (if any) is blocking "
        "a trade. Snapshot is refreshed every 5 minutes from live market data — close "
        "to, but not exactly the same as, what the bot itself sees second-by-second."
    )

    if not probe_results:
        st.info(
            "Probe unavailable — Kraken REST returned no usable OHLC for any symbol. "
            "Status pill falls back to the freshness-only proxy when this happens."
        )
        return

    # Per-gate pass counts give a one-glance read of the regime.
    if probe_summary is not None:
        gates = probe_summary["by_gate"]
        total = probe_summary["total"]
        cols = st.columns(len(gates))
        for col, (key, passing) in zip(cols, gates.items()):
            col.metric(
                _GATE_LABELS.get(key, key),
                f"{passing}/{total}",
                delta=("blocking" if passing == 0 else None),
                delta_color="inverse" if passing == 0 else "off",
            )

    table_rows = []
    for r in probe_results:
        table_rows.append(
            {
                "Symbol": r["symbol"],
                "buy_score": r["buy_score"],
                "#ind": r["indicators_fired"],
                "ADX": r["adx"],
                "RVOL": r["rvol"],
                "ATR%": r["atr_pctile"],
                "SMA slope %": r["sma_slope_pct"],
                "Fired indicators": ", ".join(r["fired_names"]) if r["fired_names"] else "—",
                "Blocked by": ", ".join(r["blocked_by"]) if r["blocked_by"] else "(would fire)",
            }
        )
    df = pd.DataFrame(table_rows)
    # Sort: anything that would fire goes to the top, then by buy_score descending.
    df["_sort"] = df["Blocked by"].apply(lambda s: 0 if s == "(would fire)" else 1)
    df = df.sort_values(["_sort", "buy_score"], ascending=[True, False]).drop(columns=["_sort"])

    # Friendly column display names + tooltips with the full technical definition.
    # Underlying column keys stay as the technical names so the data structure is
    # unchanged; only the visual labels are swapped.
    column_config = {
        "Symbol": st.column_config.TextColumn("Symbol"),
        "buy_score": st.column_config.NumberColumn(
            "Indicator strength",
            help="Weighted sum of all technical indicators voting buy. Must reach 2.0 for the strategy to consider a trade.",
            format="%.2f",
        ),
        "#ind": st.column_config.NumberColumn(
            "Indicators agreeing",
            help="How many of the technical indicators are voting buy right now. At least 3 must agree.",
            format="%d",
        ),
        "ADX": st.column_config.NumberColumn(
            "Trend strength",
            help="ADX — measures how strong the current trend is, on a 0–100 scale. Must be at least 20 to trade (no trade in choppy markets).",
            format="%.1f",
        ),
        "RVOL": st.column_config.NumberColumn(
            "Volume",
            help="Relative volume — current volume compared to the recent average (1.0 = average). Must be at least 0.7 of average.",
            format="%.2f",
        ),
        "ATR%": st.column_config.NumberColumn(
            "Volatility regime",
            help="Where current volatility sits in the recent rolling window, as a percentile. Must be ≤ 60 — the bot avoids buying into already-elevated volatility.",
            format="%.1f",
        ),
        "SMA slope %": st.column_config.NumberColumn(
            "Trend direction",
            help="Slope of the short-term moving average, in % per bar. Positive = uptrend.",
            format="%+.3f",
        ),
        "Fired indicators": st.column_config.TextColumn(
            "Indicators voting buy",
            help="Which specific technical indicators are voting buy right now.",
        ),
        "Blocked by": st.column_config.TextColumn(
            "Blocked by",
            help="Which conditions are currently preventing a buy on this symbol. '(would fire)' means every condition is satisfied.",
        ),
    }
    st.dataframe(df, hide_index=True, use_container_width=True, column_config=column_config)


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
    "(30s for status, 60s for charts, 5min for the strategy probe). Risk events "
    "(vetoes, circuit breaker trips) are tracked in process memory only and are "
    "not yet surfaced here."
)

summary = _get_activity_summary()
# Anchor the probe to symbols the bot has actually been trading recently
# (and fall back to seed symbols if there's been no activity yet).
_recent_symbols = tuple(r["symbol"] for r in _get_active_symbols(7)) or _FALLBACK_PROBE_SYMBOLS
try:
    probe_results = probe_universe(_recent_symbols)
    probe_summary = summarize(probe_results) if probe_results else None
except Exception:
    # Never let the probe break the page — fall back to freshness-only.
    probe_results = []
    probe_summary = None

_render_status(summary, probe_summary)
st.divider()
_render_why_idle(probe_results, probe_summary)
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
