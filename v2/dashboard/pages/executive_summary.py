"""Page 4: Executive Summary — AI-generated performance interpretation.

Feeds structured trading metrics and backtest baselines to Claude,
which writes a concise interpretive summary of what the data means.
"""

from datetime import datetime, timedelta, timezone

import streamlit as st

from v2.dashboard.ai_summary import generate_summary
from v2.dashboard.db import get_pool, run_async
from v2.dashboard.trades import collect_round_trips

EXCHANGE = "paper-kraken"


@st.cache_data(ttl=60)
def _get_round_trips(start: datetime, end: datetime):
    return run_async(collect_round_trips(get_pool(), start, end, EXCHANGE))


@st.cache_data(ttl=300, show_spinner="Generating executive summary...")
def _get_summary(start: datetime, end: datetime) -> str:
    """Generate AI summary — cached for 5 minutes."""
    all_rts, _ = run_async(collect_round_trips(get_pool(), start, end, EXCHANGE))
    score_only = [
        rt for rt in all_rts
        if (rt.trigger or "") in ("score", "score_high")
    ]
    return generate_summary(all_rts, score_only)


# ---------------------------------------------------------------------------
# Page layout
# ---------------------------------------------------------------------------

st.header("Executive Summary")

# Time range
now = datetime.now(timezone.utc)
end = now.replace(second=0, microsecond=0)
range_opts = ["30 days", "All time", "Custom"]
choice = st.selectbox("Time range", range_opts, index=1, key="es_range")

if choice == "30 days":
    start = (end - timedelta(days=30)).replace(hour=0, minute=0)
elif choice == "All time":
    start = datetime(2020, 1, 1, tzinfo=timezone.utc)
else:
    c1, c2 = st.columns(2)
    with c1:
        sd = st.date_input("Start", end.date() - timedelta(days=90), key="es_sd")
    with c2:
        ed = st.date_input("End", end.date(), key="es_ed")
    start = datetime.combine(sd, datetime.min.time()).replace(tzinfo=timezone.utc)
    end = datetime.combine(ed, datetime.max.time()).replace(tzinfo=timezone.utc)

# Fetch round trips for context stats
all_rts, summary = _get_round_trips(start, end)
score_only = [
    rt for rt in all_rts
    if (rt.trigger or "") in ("score", "score_high")
]

st.caption(
    f"{summary.total_round_trips} round trips | "
    f"{len(score_only)} score-triggered | "
    f"{summary.unmatched_buys} open positions"
)

st.divider()

# Generate and display AI summary
summary_text = _get_summary(start, end)
st.markdown(summary_text)

st.divider()

# Metadata
col1, col2 = st.columns(2)
with col1:
    st.caption("Powered by Claude Sonnet 4.6 (Anthropic API)")
with col2:
    st.caption("Refreshes every 5 minutes | Cached per time range")

if st.button("Regenerate", key="es_regen"):
    _get_summary.clear()
    st.rerun()
