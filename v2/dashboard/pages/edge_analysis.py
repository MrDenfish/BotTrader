"""Page 2: Edge Analysis — trends, exit quality, and fee drag diagnostics.

Answers: "Is the edge improving?", "What exit type costs the most?",
"How much do fees eat?", and "How efficient are trailing stops?"
"""

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from v2.dashboard.db import get_pool, run_async
from v2.dashboard.trades import RoundTrip, collect_round_trips

EXCHANGE = "paper-kraken"

EXIT_COLORS = {
    "trailing_stop": "#2ecc71",
    "hard_stop": "#e74c3c",
    "stale_exit": "#f39c12",
    "score": "#3498db",
    "soft_stop": "#95a5a6",
    "roc_momo_20m": "#bdc3c7",
    "roc_momo_24h": "#bdc3c7",
    "peak_time_limit": "#9b59b6",
    "signal_exit": "#3498db",
}

# Stacked bar ordering: hard_stop at bottom (red), trailing_stop at top (green)
EXIT_ORDER = [
    "hard_stop", "soft_stop", "roc_momo_20m", "roc_momo_24h",
    "stale_exit", "peak_time_limit", "score", "signal_exit", "trailing_stop",
]


# ---------------------------------------------------------------------------
# Cached data fetcher
# ---------------------------------------------------------------------------


@st.cache_data(ttl=60)
def _get_round_trips(start: datetime, end: datetime):
    return run_async(collect_round_trips(get_pool(), start, end, EXCHANGE))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _week_start(dt: datetime) -> date:
    """Monday of the week containing dt."""
    return dt.date() - timedelta(days=dt.weekday())


def _exit_color(reason: str) -> str:
    return EXIT_COLORS.get(reason, "#7f8c8d")


# ---------------------------------------------------------------------------
# Panel 1: Weekly P&L Trend
# ---------------------------------------------------------------------------


def _render_weekly_pnl_trend(trades: list[RoundTrip]):
    st.subheader("Weekly P&L Trend")
    if not trades:
        st.info("No trades in this period.")
        return

    weekly: dict[date, dict] = defaultdict(
        lambda: {"gross": 0.0, "net": 0.0, "count": 0}
    )
    for rt in trades:
        w = _week_start(rt.sell_timestamp)
        weekly[w]["gross"] += rt.gross_pnl
        weekly[w]["net"] += rt.net_pnl
        weekly[w]["count"] += 1

    weeks = sorted(weekly)
    gross = [round(weekly[w]["gross"], 2) for w in weeks]
    net = [round(weekly[w]["net"], 2) for w in weeks]
    counts = [weekly[w]["count"] for w in weeks]

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=weeks, y=gross, name="Gross P&L",
        line=dict(color="#2ecc71", width=2), mode="lines+markers",
    ))
    fig.add_trace(go.Scatter(
        x=weeks, y=net, name="Net P&L",
        line=dict(color="#3498db", width=2), mode="lines+markers",
        fill="tonexty", fillcolor="rgba(231, 76, 60, 0.12)",
    ))
    fig.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.5)

    for i, (w, c) in enumerate(zip(weeks, counts)):
        fig.add_annotation(
            x=w, y=gross[i], text=str(c), showarrow=False,
            yshift=15, font=dict(size=10, color="gray"),
        )

    fig.update_layout(
        yaxis_title="P&L ($)", hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        margin=dict(t=40), height=400,
    )
    st.plotly_chart(fig, use_container_width=True)
    st.caption("Shaded area = fee drag (gross minus net). Numbers above points = trade count.")


# ---------------------------------------------------------------------------
# Panel 2: Exit Reason P&L Trend
# ---------------------------------------------------------------------------


def _render_exit_pnl_trend(trades: list[RoundTrip]):
    st.subheader("Exit Reason P&L Trend")
    if not trades:
        st.info("No trades in this period.")
        return

    weekly_exit: dict[date, dict[str, float]] = defaultdict(
        lambda: defaultdict(float)
    )
    all_reasons: set[str] = set()
    for rt in trades:
        w = _week_start(rt.sell_timestamp)
        reason = rt.exit_reason or "unknown"
        weekly_exit[w][reason] += rt.net_pnl
        all_reasons.add(reason)

    weeks = sorted(weekly_exit)
    ordered = [r for r in EXIT_ORDER if r in all_reasons]
    ordered += sorted(r for r in all_reasons if r not in ordered)

    fig = go.Figure()
    for reason in ordered:
        vals = [round(weekly_exit[w].get(reason, 0), 2) for w in weeks]
        fig.add_trace(go.Bar(
            x=weeks, y=vals, name=reason,
            marker_color=_exit_color(reason),
        ))

    fig.update_layout(
        barmode="relative", yaxis_title="Net P&L ($)",
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        margin=dict(t=40), height=400,
    )
    st.plotly_chart(fig, use_container_width=True)

    if any(rt.exit_reason == "soft_stop" for rt in trades):
        st.caption(
            "soft_stop exits occurred Feb 16\u201328 only \u2014 "
            "exit type disabled since March 2026."
        )


# ---------------------------------------------------------------------------
# Panel 3: Hard Stop Rate Trend
# ---------------------------------------------------------------------------


def _render_hard_stop_rate(trades: list[RoundTrip]):
    st.subheader("Hard Stop Rate Trend")
    if not trades:
        st.info("No trades in this period.")
        return

    weekly: dict[date, dict] = defaultdict(
        lambda: {"total": 0, "hard_stop": 0}
    )
    for rt in trades:
        w = _week_start(rt.sell_timestamp)
        weekly[w]["total"] += 1
        if rt.exit_reason == "hard_stop":
            weekly[w]["hard_stop"] += 1

    valid_weeks = []
    rates = []
    labels = []
    for w in sorted(weekly):
        total = weekly[w]["total"]
        hs = weekly[w]["hard_stop"]
        if total >= 3:
            valid_weeks.append(w)
            rates.append(round(hs / total * 100, 1))
            labels.append(f"{hs}/{total}")

    if not valid_weeks:
        st.info("Not enough trades per week (minimum 3) to compute rates.")
        return

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=valid_weeks, y=rates, name="Hard Stop Rate",
        line=dict(color="#e74c3c", width=2), mode="lines+markers",
        text=labels,
        hovertemplate="%{text} (%{y:.1f}%)<extra></extra>",
    ))
    fig.add_hline(
        y=22, line_dash="dash", line_color="gray",
        annotation_text="Backtest baseline (22%)",
        annotation_position="bottom right",
    )
    fig.update_layout(
        yaxis_title="Hard Stop Rate (%)",
        yaxis_range=[0, max(max(rates) * 1.3, 30)],
        margin=dict(t=40), height=350,
    )
    st.plotly_chart(fig, use_container_width=True)


# ---------------------------------------------------------------------------
# Panel 4: Avg Trade Metrics Table
# ---------------------------------------------------------------------------


def _render_avg_trade_metrics(trades: list[RoundTrip]):
    st.subheader("Avg Trade Metrics by Exit Reason")
    if not trades:
        st.info("No trades in this period.")
        return

    by_exit: dict[str, list[RoundTrip]] = defaultdict(list)
    for rt in trades:
        by_exit[rt.exit_reason or "unknown"].append(rt)

    rows = []
    for reason in sorted(by_exit):
        group = by_exit[reason]
        n = len(group)
        wins = sum(1 for t in group if t.net_pnl > 0)
        rows.append({
            "Exit Reason": reason,
            "Count": n,
            "Win Rate": f"{wins / n:.0%}",
            "Avg Gross %": round(sum(t.gross_pnl_pct for t in group) / n, 2),
            "Avg Net %": round(sum(t.net_pnl_pct for t in group) / n, 2),
            "Avg Net $": round(sum(t.net_pnl for t in group) / n, 2),
            "Total Net $": round(sum(t.net_pnl for t in group), 2),
            "Avg Hold (hrs)": round(
                sum(t.hold_seconds for t in group) / n / 3600, 1
            ),
        })

    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# Panel 5: P&L Distribution Histogram
# ---------------------------------------------------------------------------


def _render_pnl_distribution(trades: list[RoundTrip]):
    st.subheader("P&L Distribution")
    if not trades:
        st.info("No trades in this period.")
        return

    fig = go.Figure()
    reasons = sorted({rt.exit_reason or "unknown" for rt in trades})
    for reason in reasons:
        subset = [rt.net_pnl for rt in trades if (rt.exit_reason or "unknown") == reason]
        fig.add_trace(go.Histogram(
            x=subset, name=reason,
            marker_color=_exit_color(reason), opacity=0.7,
        ))

    fig.add_vline(
        x=0, line_dash="dash", line_color="black",
        annotation_text="Breakeven", annotation_position="top right",
    )
    fig.add_vline(
        x=-0.49, line_dash="dot", line_color="#f39c12",
        annotation_text="Avg fee-only loss",
        annotation_position="top left",
    )

    fig.update_layout(
        barmode="overlay", xaxis_title="Net P&L ($)", yaxis_title="Count",
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        margin=dict(t=40), height=400,
    )
    st.plotly_chart(fig, use_container_width=True)
    st.caption(
        "Fee-only loss line at -$0.49 "
        "($75 notional \u00d7 0.65% round-trip fee)."
    )


# ---------------------------------------------------------------------------
# Panel 6: Peak Capture Scatter (trailing stops only)
# ---------------------------------------------------------------------------


def _render_peak_capture(trades: list[RoundTrip]):
    st.subheader("Peak Capture (Trailing Stops)")

    trailing = [
        rt for rt in trades
        if rt.exit_reason == "trailing_stop" and rt.peak_price is not None
    ]
    if not trailing:
        st.info("No trailing stop trades with peak price data in this period.")
        return

    mfe_pcts = []
    net_pcts = []
    symbols = []
    for rt in trailing:
        mfe = (rt.peak_price - rt.buy_price) / rt.buy_price * 100
        mfe_pcts.append(round(mfe, 2))
        net_pcts.append(rt.net_pnl_pct)
        symbols.append(rt.symbol)

    max_val = max(max(mfe_pcts), max(net_pcts, default=0)) * 1.1

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=[0, max(max_val, 1)], y=[0, max(max_val, 1)],
        mode="lines", name="Perfect capture",
        line=dict(color="gray", dash="dash"),
    ))
    fig.add_trace(go.Scatter(
        x=mfe_pcts, y=net_pcts,
        mode="markers", name="Trades",
        marker=dict(color="#2ecc71", size=8, opacity=0.7),
        text=symbols,
        hovertemplate=(
            "<b>%{text}</b><br>"
            "MFE: %{x:.2f}%<br>"
            "Net P&L: %{y:.2f}%<extra></extra>"
        ),
    ))
    fig.update_layout(
        xaxis_title="MFE % (peak vs entry)",
        yaxis_title="Net P&L %",
        margin=dict(t=40), height=400,
    )
    st.plotly_chart(fig, use_container_width=True)

    avg_mfe = sum(mfe_pcts) / len(mfe_pcts)
    avg_net = sum(net_pcts) / len(net_pcts)
    capture = avg_net / avg_mfe * 100 if avg_mfe > 0 else 0
    st.caption(
        f"Avg MFE: {avg_mfe:.2f}% | Avg Net P&L: {avg_net:.2f}% | "
        f"Capture ratio: {capture:.0f}% "
        f"({len(trailing)} trailing stop trades)"
    )


# ---------------------------------------------------------------------------
# Page layout
# ---------------------------------------------------------------------------

st.header("Edge Analysis")

# Controls row
ctrl1, ctrl2 = st.columns([1, 2])

with ctrl1:
    now = datetime.now(timezone.utc)
    end = now.replace(second=0, microsecond=0)
    range_opts = ["30 days", "All time", "Custom"]
    choice = st.selectbox("Time range", range_opts, index=1)

    if choice == "30 days":
        start = (end - timedelta(days=30)).replace(hour=0, minute=0)
    elif choice == "All time":
        start = datetime(2020, 1, 1, tzinfo=timezone.utc)
    else:
        c1, c2 = st.columns(2)
        with c1:
            sd = st.date_input("Start", end.date() - timedelta(days=90))
        with c2:
            ed = st.date_input("End", end.date())
        start = datetime.combine(sd, datetime.min.time()).replace(
            tzinfo=timezone.utc
        )
        end = datetime.combine(ed, datetime.max.time()).replace(
            tzinfo=timezone.utc
        )

# Fetch all round trips
all_rts, summary = _get_round_trips(start, end)

# Trigger filter
with ctrl2:
    all_triggers = sorted({rt.trigger or "unknown" for rt in all_rts})
    default_triggers = [
        t for t in all_triggers if "roc_momo" not in t and t != "unknown"
    ]
    selected = st.multiselect(
        "Entry triggers", all_triggers, default=default_triggers,
    )

# Apply filter
filtered = [rt for rt in all_rts if (rt.trigger or "unknown") in selected]

# Momo caption
has_momo = any("roc_momo" in t for t in selected)
if has_momo:
    st.caption(
        "roc_momo trades shown in grey \u2014 trigger disabled March 2026. "
        "Filter to score-only for clean analysis."
    )

# Summary line
st.caption(
    f"{summary.total_round_trips} round trips matched | "
    f"{summary.unmatched_buys} open positions | "
    f"Showing {len(filtered)} trades"
)

if not filtered:
    st.warning("No trades match the selected filters.")
    st.stop()

# Render all panels
_render_weekly_pnl_trend(filtered)
st.divider()
_render_exit_pnl_trend(filtered)
st.divider()
_render_hard_stop_rate(filtered)
st.divider()
_render_avg_trade_metrics(filtered)
st.divider()
_render_pnl_distribution(filtered)
st.divider()
_render_peak_capture(filtered)
