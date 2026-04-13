"""Page 3: Entry Quality — indicator-level analysis and entry condition diagnostics.

Answers: "Which indicator combinations produce winners?", "Does a higher
score predict better outcomes?", "Are there symbols or conditions to avoid?"
"""

from collections import defaultdict
from datetime import datetime, timedelta, timezone

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

# Canonical indicator order (matches composite scoring config)
INDICATOR_ORDER = [
    "Ratio", "Touch", "W-Bottom", "RSI", "ROC", "MACD", "Swing", "Volume Div",
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


def _clean_indicator_name(name: str) -> str:
    """Strip 'Buy ' prefix from indicator names for display."""
    if name.startswith("Buy "):
        return name[4:]
    return name


def _exit_color(reason: str) -> str:
    return EXIT_COLORS.get(reason, "#7f8c8d")


# ---------------------------------------------------------------------------
# Panel 1: Win Rate by Symbol
# ---------------------------------------------------------------------------


def _render_win_rate_by_symbol(trades: list[RoundTrip]):
    st.subheader("Win Rate by Symbol")
    if not trades:
        st.info("No trades in this period.")
        return

    by_symbol: dict[str, list[RoundTrip]] = defaultdict(list)
    for rt in trades:
        by_symbol[rt.symbol].append(rt)

    rows = []
    for symbol in sorted(by_symbol):
        group = by_symbol[symbol]
        n = len(group)
        wins = sum(1 for t in group if t.net_pnl > 0)
        wr = wins / n
        rows.append({
            "Symbol": symbol,
            "Trades": n,
            "Win Rate": round(wr * 100, 1),
            "Avg Net $": round(sum(t.net_pnl for t in group) / n, 2),
            "Total Net $": round(sum(t.net_pnl for t in group), 2),
        })

    df = pd.DataFrame(rows).sort_values("Total Net $", ascending=False)
    st.dataframe(df, use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# Panel 2: Score vs Outcome
# ---------------------------------------------------------------------------


def _render_score_vs_outcome(trades: list[RoundTrip]):
    st.subheader("Score vs Outcome")

    scored = [rt for rt in trades if rt.buy_score is not None]
    if not scored:
        st.info("No trades with score data in this period.")
        return

    fig = go.Figure()
    reasons = sorted({rt.exit_reason or "unknown" for rt in scored})

    for reason in reasons:
        subset = [rt for rt in scored if (rt.exit_reason or "unknown") == reason]
        fig.add_trace(go.Scatter(
            x=[rt.buy_score for rt in subset],
            y=[rt.net_pnl for rt in subset],
            mode="markers",
            name=reason,
            marker=dict(
                color=_exit_color(reason),
                size=[max((rt.indicator_count or 3) * 3, 6) for rt in subset],
                opacity=0.7,
            ),
            text=[rt.symbol for rt in subset],
            hovertemplate=(
                "<b>%{text}</b><br>"
                "Score: %{x:.1f}<br>"
                "Net P&L: $%{y:.2f}<br>"
                f"Exit: {reason}<extra></extra>"
            ),
        ))

    fig.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.5)
    fig.update_layout(
        xaxis_title="Buy Score at Entry",
        yaxis_title="Net P&L ($)",
        margin=dict(t=40), height=400,
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
    )
    st.plotly_chart(fig, use_container_width=True)
    st.caption(
        "Marker size = indicator count. If the cloud is flat (no upward tilt), "
        "higher scores don't predict better outcomes."
    )


# ---------------------------------------------------------------------------
# Panel 3: Indicator Hit Rate (Winners vs Losers)
# ---------------------------------------------------------------------------


def _render_indicator_hit_rate(trades: list[RoundTrip]):
    st.subheader("Indicator Hit Rate: Winners vs Losers")

    has_indicators = [rt for rt in trades if rt.indicators_fired]
    if not has_indicators:
        st.info("No trades with indicator data in this period.")
        return

    winners = [rt for rt in has_indicators if rt.net_pnl > 0]
    losers = [rt for rt in has_indicators if rt.net_pnl <= 0]

    if not winners or not losers:
        st.info("Need both winning and losing trades for comparison.")
        return

    # Count how often each indicator fired in winners vs losers
    all_indicators = set()
    for rt in has_indicators:
        all_indicators.update(_clean_indicator_name(i) for i in rt.indicators_fired)

    # Use canonical order, then any extras
    ordered = [i for i in INDICATOR_ORDER if i in all_indicators]
    ordered += sorted(i for i in all_indicators if i not in ordered)

    win_rates = []
    loss_rates = []
    deltas = []
    for ind in ordered:
        win_count = sum(
            1 for rt in winners
            if ind in [_clean_indicator_name(i) for i in rt.indicators_fired]
        )
        loss_count = sum(
            1 for rt in losers
            if ind in [_clean_indicator_name(i) for i in rt.indicators_fired]
        )
        wr = win_count / len(winners) * 100
        lr = loss_count / len(losers) * 100
        win_rates.append(round(wr, 1))
        loss_rates.append(round(lr, 1))
        deltas.append(round(wr - lr, 1))

    fig = go.Figure()
    fig.add_trace(go.Bar(
        y=ordered, x=win_rates, name="Winners",
        orientation="h", marker_color="#2ecc71", opacity=0.8,
    ))
    fig.add_trace(go.Bar(
        y=ordered, x=loss_rates, name="Losers",
        orientation="h", marker_color="#e74c3c", opacity=0.8,
    ))

    fig.update_layout(
        barmode="group",
        xaxis_title="% of trades where indicator fired",
        margin=dict(t=40, l=100), height=max(250, len(ordered) * 40),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
    )
    st.plotly_chart(fig, use_container_width=True)

    # Delta table
    delta_df = pd.DataFrame({
        "Indicator": ordered,
        "Winners %": win_rates,
        "Losers %": loss_rates,
        "Delta": deltas,
    }).sort_values("Delta", ascending=False)
    st.dataframe(delta_df, use_container_width=True, hide_index=True)
    st.caption(
        "Positive delta = indicator appears more in winners than losers. "
        "Near-zero delta = indicator provides no discrimination."
    )


# ---------------------------------------------------------------------------
# Panel 4: Indicator Combination Performance
# ---------------------------------------------------------------------------


def _render_indicator_combos(trades: list[RoundTrip]):
    st.subheader("Indicator Combination Performance")

    has_indicators = [rt for rt in trades if rt.indicators_fired]
    if not has_indicators:
        st.info("No trades with indicator data in this period.")
        return

    # Build combo key from sorted cleaned indicator names
    combos: dict[str, list[RoundTrip]] = defaultdict(list)
    for rt in has_indicators:
        cleaned = sorted(_clean_indicator_name(i) for i in rt.indicators_fired)
        key = " + ".join(cleaned)
        combos[key].append(rt)

    # Filter to combos with >= 3 trades
    rows = []
    for combo, group in combos.items():
        if len(group) < 3:
            continue
        n = len(group)
        wins = sum(1 for t in group if t.net_pnl > 0)
        rows.append({
            "Combination": combo,
            "Trades": n,
            "Win Rate": f"{wins / n:.0%}",
            "Avg Net $": round(sum(t.net_pnl for t in group) / n, 2),
            "Total Net $": round(sum(t.net_pnl for t in group), 2),
        })

    if not rows:
        st.info("No indicator combinations with 3+ trades in this period.")
        return

    df = pd.DataFrame(rows).sort_values("Total Net $", ascending=False)
    st.dataframe(df, use_container_width=True, hide_index=True)
    st.caption("Only combinations with 3+ occurrences shown to reduce noise.")


# ---------------------------------------------------------------------------
# Panel 5: Entry Conditions Scatter Plots
# ---------------------------------------------------------------------------


def _render_entry_conditions(trades: list[RoundTrip]):
    st.subheader("Entry Conditions vs Outcome")

    conditions = [
        ("adx", "ADX at Entry"),
        ("rvol", "RVOL at Entry"),
        ("atr_percentile", "ATR Percentile at Entry"),
    ]

    cols = st.columns(3)
    any_plotted = False

    for col, (field, label) in zip(cols, conditions):
        has_data = [rt for rt in trades if getattr(rt, field) is not None]
        if not has_data:
            with col:
                st.caption(f"{label}: no data")
            continue

        any_plotted = True
        x_vals = [getattr(rt, field) for rt in has_data]
        y_vals = [rt.net_pnl for rt in has_data]
        colors = [
            "#2ecc71" if rt.net_pnl > 0 else "#e74c3c" for rt in has_data
        ]

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=x_vals, y=y_vals,
            mode="markers",
            marker=dict(color=colors, size=7, opacity=0.6),
            text=[rt.symbol for rt in has_data],
            hovertemplate=(
                "<b>%{text}</b><br>"
                f"{label}: %{{x:.2f}}<br>"
                "Net P&L: $%{y:.2f}<extra></extra>"
            ),
        ))
        fig.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.5)
        fig.update_layout(
            xaxis_title=label, yaxis_title="Net P&L ($)",
            margin=dict(t=20, b=40, l=50, r=20), height=300,
            showlegend=False,
        )
        with col:
            st.plotly_chart(fig, use_container_width=True)

    if any_plotted:
        st.caption(
            "Green = winner, red = loser. "
            "A flat cloud means the filter doesn't discriminate at entry."
        )


# ---------------------------------------------------------------------------
# Panel 6: Time-of-Day Heatmap
# ---------------------------------------------------------------------------


def _render_time_of_day(trades: list[RoundTrip]):
    st.subheader("Time-of-Day Performance")

    if not trades:
        st.info("No trades in this period.")
        return

    # Group by (day_of_week, hour)
    cells: dict[tuple[int, int], list[float]] = defaultdict(list)
    for rt in trades:
        dow = rt.buy_timestamp.weekday()  # 0=Mon
        hour = rt.buy_timestamp.hour
        cells[(dow, hour)].append(rt.net_pnl)

    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    hours = list(range(24))

    # Build matrix: rows=days, cols=hours
    z = []
    text = []
    for dow in range(7):
        row = []
        text_row = []
        for h in hours:
            pnls = cells.get((dow, h), [])
            if pnls:
                avg = sum(pnls) / len(pnls)
                row.append(round(avg, 2))
                text_row.append(f"${avg:.2f} ({len(pnls)} trades)")
            else:
                row.append(None)
                text_row.append("no trades")
        z.append(row)
        text.append(text_row)

    fig = go.Figure(data=go.Heatmap(
        z=z,
        x=[f"{h:02d}:00" for h in hours],
        y=day_names,
        text=text,
        hovertemplate="%{y} %{x}<br>%{text}<extra></extra>",
        colorscale="RdYlGn",
        zmid=0,
        colorbar_title="Avg P&L ($)",
    ))

    fig.update_layout(
        xaxis_title="Hour (UTC)",
        margin=dict(t=20), height=300,
    )
    st.plotly_chart(fig, use_container_width=True)

    if len(trades) < 200:
        st.caption(
            f"Low sample size ({len(trades)} trades) \u2014 "
            "interpret with caution. Becomes meaningful after July 2026."
        )


# ---------------------------------------------------------------------------
# Page layout
# ---------------------------------------------------------------------------

st.header("Entry Quality")

# Controls row
ctrl1, ctrl2 = st.columns([1, 2])

with ctrl1:
    now = datetime.now(timezone.utc)
    end = now.replace(second=0, microsecond=0)
    range_opts = ["30 days", "All time", "Custom"]
    choice = st.selectbox("Time range", range_opts, index=1, key="eq_range")

    if choice == "30 days":
        start = (end - timedelta(days=30)).replace(hour=0, minute=0)
    elif choice == "All time":
        start = datetime(2020, 1, 1, tzinfo=timezone.utc)
    else:
        c1, c2 = st.columns(2)
        with c1:
            sd = st.date_input("Start", end.date() - timedelta(days=90), key="eq_sd")
        with c2:
            ed = st.date_input("End", end.date(), key="eq_ed")
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
        "Entry triggers", all_triggers, default=default_triggers, key="eq_triggers",
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

# Summary
st.caption(
    f"{summary.total_round_trips} round trips matched | "
    f"{summary.unmatched_buys} open positions | "
    f"Showing {len(filtered)} trades"
)

if not filtered:
    st.warning("No trades match the selected filters.")
    st.stop()

# Render all panels
_render_win_rate_by_symbol(filtered)
st.divider()
_render_score_vs_outcome(filtered)
st.divider()
_render_indicator_hit_rate(filtered)
st.divider()
_render_indicator_combos(filtered)
st.divider()
_render_entry_conditions(filtered)
st.divider()
_render_time_of_day(filtered)
