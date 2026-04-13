# Dashboard Restructure — Edge Analysis & Entry Quality Pages

**Author:** QSE (Claude Code)
**Date:** 2026-04-12
**Status:** Draft for Director Review
**Target:** Session 2 implementation
**Prerequisite:** Session 1 deployed (Page 1: Performance Report)

---

## 1. Problem Statement

The bot is not profitable. PF = 0.73, Kelly = -0.21, avg gross return +0.187% vs 0.65% RT fees. The Session 1 dashboard answers "what happened?" but not "why?" or "what should change?"

The July 2026 re-analysis needs trend data, not snapshots. If we start collecting visual diagnostics now, we have 2.5 months of live data to evaluate.

## 2. Data Audit — What We Actually Have

All data comes from `v2_fills` (343 fills, Feb 20 — Apr 12, 2026). No external data sources needed.

### Buy Fill Metadata (JSONB)

| Field | Type | Example | Available On |
|-------|------|---------|-------------|
| `buy_score` | float | 6.5 | All buys |
| `indicator_count` | int | 4 | All buys |
| `trigger` | string | "score", "score_high", "roc_momo_24h" | All buys |
| `adx` | float | 28.5 | All buys |
| `rvol` | float | 1.28 | All buys |
| `atr_percentile` | float | 56.6 | All buys |
| `sma_slope_pct` | float | 0.11 | All buys |
| `round_trip_fee_pct` | float | 0.65 | All buys |
| `score_components.buy` | array | 8 indicators with value, decision, weight, contribution | All buys |
| `guardrail` | string/null | null or guardrail name | All buys |

### Sell Fill Metadata (JSONB)

| Field | Type | Available On |
|-------|------|-------------|
| `signal_reason` | string | All sells |
| `pnl_pct` | float (net, fee-adjusted) | All sells |
| `pnl_raw_pct` | float (gross) | All sells |
| `avg_entry` | float | All sells |
| `peak_price` | float | trailing_stop only |
| `trail_price` | float | trailing_stop only |
| `drawdown_pct` | float | trailing_stop only |
| `threshold_pct` | float | hard_stop only |
| `atr_pct` | float | hard_stop only |

### Live Data Statistics

| Metric | Value |
|--------|-------|
| Total fills | 343 (140 buys, 203 sells) |
| Date range | 2026-02-20 to 2026-04-12 (51 days) |
| Weekly steady-state | ~8 buys, ~8 sells/week (post-momo-disable) |
| Exit reasons | hard_stop 64, trailing_stop 40, soft_stop 30, stale_exit 24, roc_momo_20m 22, score 14, roc_momo_24h 7, peak_time_limit 2 |
| Entry triggers | score 75, roc_momo_24h 48, score_high 5 |

### Data Caveat

48 buys (38%) used `roc_momo_24h` trigger, which was subsequently disabled due to high hard stop rate. Analysis pages should allow filtering by trigger type so the Director can isolate score-only trades from legacy momo trades.

---

## 3. Proposed Page Structure

```
Page 1: Performance Report    (existing — minor additions)
Page 2: Edge Analysis         (NEW — core diagnostic page)
Page 3: Entry Quality         (NEW — indicator-level analysis)
Page 4: Bot Health            (stub exists — future session)
Page 5: Config Editor         (stub exists — future session)
```

---

## 4. Page 1 Modifications (Performance Report)

### 4.1 Add Gross vs Net P&L Split to Hero Metrics

Currently shows only Net P&L. Add:

```
Net P&L: -$67.12  |  Gross P&L: +$84.30  |  Total Fees: $151.42  |  Fee Drag: 180%
```

**Fee Drag %** = `(total_fees / gross_pnl) * 100` when gross is positive.
If gross is negative, display "Gross negative" instead of a percentage.

**Data source:** `collect_pnl()` already returns `realized_pnl` (net, fees deducted) and `total_fees`. Gross = net + fees.

### 4.2 Add Trigger Filter

Add a multiselect filter alongside the date range:

```
Trigger: [x] score  [x] score_high  [ ] roc_momo_24h  [ ] roc_momo_20m
```

Default: `score` and `score_high` checked (excludes legacy momo trades).

**Implementation:** This requires a new query approach — the existing collectors don't filter by buy-side trigger. See Section 7 (New Data Layer).

---

## 5. Page 2: Edge Analysis

**Purpose:** Answer "Is the edge improving or degrading?" and "What exit type is costing the most?"

### Layout

```
┌─────────────────────────────────────────────────────────────┐
│  Edge Analysis                                               │
│  Trigger filter: [x] score [x] score_high [ ] roc_momo_*    │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌─── Weekly P&L Trend ─────────────────────────────────┐   │
│  │  [Plotly dual-axis line chart]                        │   │
│  │  Line 1: Gross P&L (green/red)                       │   │
│  │  Line 2: Net P&L (blue)                              │   │
│  │  Shaded area: fee drag between the two lines         │   │
│  │  Annotation: number of round-trip trades per week     │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                              │
│  ┌─── Exit Reason P&L Trend ────────────────────────────┐   │
│  │  [Plotly stacked bar chart — weekly]                  │   │
│  │  Bars: trailing_stop (green), hard_stop (red),        │   │
│  │        stale_exit (orange), signal/score (blue),      │   │
│  │        soft_stop (gray)                               │   │
│  │  Net line overlaid showing total                      │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                              │
│  ┌─── Hard Stop Rate Trend ─────────────────────────────┐   │
│  │  [Plotly line chart — weekly]                         │   │
│  │  Y-axis: hard_stop count / total exits (%)            │   │
│  │  Horizontal reference line at 22% (backtest baseline) │   │
│  │  Annotation: absolute count per week                  │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                              │
│  ┌─── Avg Trade Metrics ────────────────────────────────┐   │
│  │  [Summary table — one row per exit reason]            │   │
│  │  Exit Reason | Count | Avg Gross % | Avg Net % |      │   │
│  │              | Avg Hold Time | Win Rate              │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                              │
│  ┌─── P&L Distribution ────────────────────────────────┐    │
│  │  [Plotly histogram]                                  │    │
│  │  X-axis: net P&L per trade ($)                       │    │
│  │  Color: exit reason                                  │    │
│  │  Vertical line at $0 (breakeven)                     │    │
│  │  Vertical line at -$fee (fee-only loss)              │    │
│  └──────────────────────────────────────────────────────┘   │
│                                                              │
│  ┌─── Peak Capture (Trailing Stops Only) ───────────────┐   │
│  │  [Plotly scatter]                                     │   │
│  │  X-axis: MFE % (peak_price vs entry)                 │   │
│  │  Y-axis: Realized P&L %                              │   │
│  │  Diagonal = perfect capture. Gap = left on the table. │   │
│  │  Shows how much of the peak move trailing stops keep. │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

### Panel Specifications

#### 5.1 Weekly P&L Trend

**What it answers:** Is gross edge improving? Is fee drag constant or growing?

**Data:** For each calendar week:
- Gross P&L = sum of `(sell_price - buy_price) * qty` for FIFO-matched round trips
- Net P&L = gross - buy_fees - sell_fees
- Fee drag = gross - net
- Trade count = number of completed round trips

**Chart type:** Plotly `go.Scatter` with two lines and a filled area between them.

#### 5.2 Exit Reason P&L Trend

**What it answers:** Which exit type is costing the most, and is it getting better or worse?

**Data:** For each calendar week, sum net P&L grouped by `signal_reason` from sell metadata.

**Chart type:** Plotly stacked bar. Hard stops should always be the bottom (red) bar so their magnitude is visually anchored.

#### 5.3 Hard Stop Rate Trend

**What it answers:** Is the 22% hard stop rate from backtest holding in live, or is it worse?

**Data:** `hard_stop_count / total_exit_count` per week.

**Chart type:** Plotly line chart with a horizontal dashed line at 22% (backtest baseline from the April analysis).

**Note:** Weeks with fewer than 3 exits should show a marker but no connecting line (too noisy for a rate).

#### 5.4 Avg Trade Metrics Table

**What it answers:** At a glance, which exit types are profitable and which are drains?

**Columns:**

| Exit Reason | Count | Win Rate | Avg Gross % | Avg Net % | Avg Net $ | Avg Hold (hrs) |
|-------------|-------|----------|-------------|-----------|-----------|----------------|

**Data:** From sell fill metadata: `pnl_raw_pct` (gross), `pnl_pct` (net). Hold time computed from FIFO-matched buy timestamp to sell timestamp.

#### 5.5 P&L Distribution Histogram

**What it answers:** What does the P&L distribution look like? Is it normally distributed or fat-tailed? Where do most trades land relative to fees?

**Data:** Net P&L per completed sell, colored by exit reason.

**Chart type:** Plotly histogram, bins ~$0.50 wide. Vertical lines at $0 and at the average fee-only loss (~-$0.49 at $75 notional * 0.65% RT).

#### 5.6 Peak Capture Scatter (Trailing Stops Only)

**What it answers:** How much of the peak move do trailing stops actually capture?

**Data:** For trailing_stop sells only:
- X = MFE % = `(peak_price - avg_entry) / avg_entry * 100`
- Y = net P&L % = `pnl_pct`

A trade on the diagonal line captured 100% of its peak. Below the diagonal means profit was given back. The gap between the diagonal and the data points is the trailing cost.

---

## 6. Page 3: Entry Quality

**Purpose:** Answer "Which indicator combinations produce winners?" and "Are there symbols or conditions to avoid?"

### Layout

```
┌─────────────────────────────────────────────────────────────┐
│  Entry Quality                                               │
│  Trigger filter: [x] score [x] score_high [ ] roc_momo_*    │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌─── Win Rate by Symbol ───────────────────────────────┐   │
│  │  [Sortable table]                                     │   │
│  │  Symbol | Trades | Win Rate | Avg Net $ | Total Net $ │   │
│  │  Color: green if WR > 50%, red if < 30%              │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                              │
│  ┌─── Score vs Outcome ─────────────────────────────────┐   │
│  │  [Plotly scatter]                                     │   │
│  │  X-axis: buy_score at entry                          │   │
│  │  Y-axis: net P&L ($) of the resulting trade          │   │
│  │  Color: exit reason                                   │   │
│  │  Size: indicator_count                                │   │
│  │  Question: do higher scores produce better outcomes?  │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                              │
│  ┌─── Indicator Hit Rate ───────────────────────────────┐   │
│  │  [Horizontal bar chart]                               │   │
│  │  For each of the 8 buy indicators:                    │   │
│  │    - % of winning trades where it fired               │   │
│  │    - % of losing trades where it fired                │   │
│  │  Paired bars (green = winners, red = losers)          │   │
│  │  Question: which indicators appear disproportionately │   │
│  │  in winners vs losers?                                │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                              │
│  ┌─── Indicator Combination Performance ────────────────┐   │
│  │  [Table]                                              │   │
│  │  Combination (sorted) | Trades | WR | Avg Net $ |     │   │
│  │  e.g. "MACD+ROC+Swing" | 12 | 58% | +$0.45 |        │   │
│  │  Only combos with >= 3 trades shown                   │   │
│  │  Question: are some 3-indicator combos better than    │   │
│  │  others, even at the same score?                      │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                              │
│  ┌─── Entry Conditions at Entry ────────────────────────┐   │
│  │  [Plotly scatter matrix or two scatter plots]         │   │
│  │  Plot 1: ADX at entry vs net P&L (colored by outcome)│   │
│  │  Plot 2: RVOL at entry vs net P&L                    │   │
│  │  Plot 3: ATR percentile at entry vs net P&L          │   │
│  │  Question: do entry-condition filters discriminate?   │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                              │
│  ┌─── Time-of-Day Performance ──────────────────────────┐   │
│  │  [Plotly heatmap]                                     │   │
│  │  X-axis: hour of day (UTC)                           │   │
│  │  Y-axis: day of week                                  │   │
│  │  Color: avg net P&L per trade for that slot          │   │
│  │  Note: only meaningful with sufficient data (July+)  │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

### Panel Specifications

#### 6.1 Win Rate by Symbol

**What it answers:** Which symbols consistently make or lose money?

**Data:** FIFO-matched round trips grouped by symbol. Win = net P&L > 0.

**Display:** Sortable Streamlit dataframe. Color-code via pandas Styler or conditional formatting column.

#### 6.2 Score vs Outcome

**What it answers:** Does a higher composite score predict better trades?

**Data:** Join buy fills (buy_score from metadata) with their FIFO-matched sell P&L.

If the scatter shows no correlation (flat cloud), it means the scoring weights don't discriminate — all scores above threshold perform similarly. This would confirm the backtest finding.

#### 6.3 Indicator Hit Rate (Winners vs Losers)

**What it answers:** Which indicators appear more often in winning trades than losing trades?

**Data:** For each completed round trip, extract which buy-side indicators had `decision: 1` from `score_components.buy`. Split into winners (net P&L > 0) and losers (net P&L <= 0). Compute the percentage of winners and losers where each indicator fired.

**Example output:**

| Indicator | Fired in Winners | Fired in Losers | Delta |
|-----------|-----------------|-----------------|-------|
| Buy Swing | 72% | 45% | +27% |
| Buy ROC | 85% | 88% | -3% |
| Buy MACD | 68% | 70% | -2% |
| Buy Ratio | 92% | 95% | -3% |

An indicator with a large positive delta is a signal of quality. An indicator with zero delta provides no discrimination.

#### 6.4 Indicator Combination Performance

**What it answers:** Are some indicator combos better than others at the same score level?

**Data:** For each buy, extract the set of indicators that fired (sorted alphabetically). Group trades by this combination string. Show win rate and avg net P&L per combination.

**Filter:** Only show combos with >= 3 occurrences (avoid noise).

**Example:**

| Combination | Trades | Win Rate | Avg Net $ |
|-------------|--------|----------|-----------|
| MACD + ROC + Swing | 12 | 58% | +$0.45 |
| Ratio + ROC + VolDiv | 8 | 25% | -$1.20 |
| MACD + Ratio + ROC + Swing | 5 | 80% | +$1.10 |

This directly answers whether the trend confirmation gate (requiring ROC/MACD/Swing) is doing its job.

#### 6.5 Entry Conditions Scatter Plots

**What it answers:** Do ADX, RVOL, or ATR percentile at entry predict trade outcome?

**Data:** From buy metadata: `adx`, `rvol`, `atr_percentile`. Matched to sell P&L.

Three scatter plots (or a 1x3 subplot). If the cloud is flat, the filter doesn't discriminate (confirming the backtest conclusion). If there's a tilt, there's an exploitable threshold.

#### 6.6 Time-of-Day Heatmap

**What it answers:** Are there hours/days that are systematically better or worse?

**Data:** Buy fill timestamp (hour, day of week) matched to trade outcome.

**Caveat:** With ~80 score-triggered trades over 51 days, most hour/day cells will have 0-2 trades. This panel becomes meaningful after July when there's 4+ months of data. Display a note: "Low sample size — interpret with caution" if total trades < 200.

---

## 7. New Data Layer: Round-Trip Trade Matcher

The existing collectors work on fills (individual buys and sells). Pages 2 and 3 need **round-trip trades** — a buy matched to its corresponding sell, with metadata from both sides.

### New Module: `v2/dashboard/trades.py`

```python
async def collect_round_trips(
    pool: asyncpg.Pool,
    start: datetime,
    end: datetime,
    exchange: str,
    triggers: list[str] | None = None,  # filter by buy-side trigger
) -> list[RoundTrip]
```

**RoundTrip dataclass:**

```python
@dataclass
class RoundTrip:
    symbol: str
    buy_timestamp: datetime
    sell_timestamp: datetime
    buy_price: float
    sell_price: float
    qty: float
    buy_fee: float
    sell_fee: float
    gross_pnl: float          # (sell - buy) * qty
    net_pnl: float            # gross - fees
    gross_pnl_pct: float      # from sell metadata: pnl_raw_pct
    net_pnl_pct: float        # from sell metadata: pnl_pct
    hold_seconds: int
    # Entry metadata
    trigger: str              # "score", "score_high", "roc_momo_24h"
    buy_score: float
    indicator_count: int
    indicators_fired: list[str]  # ["Buy Ratio", "Buy ROC", "Buy MACD"]
    adx: float | None
    rvol: float | None
    atr_percentile: float | None
    # Exit metadata
    exit_reason: str          # "trailing_stop", "hard_stop", etc.
    peak_price: float | None  # trailing_stop only
    drawdown_pct: float | None
```

**Implementation approach:**
1. Fetch all fills in the period (plus prior buys for FIFO queue, same as existing collectors)
2. FIFO-match buys to sells
3. For each matched pair, extract metadata from both the buy and sell fill
4. Return as a list of RoundTrip objects

This is the single most important new module. All Page 2 and Page 3 panels derive from this list.

---

## 8. File Structure (Session 2)

```
v2/dashboard/
  trades.py                    # NEW: RoundTrip matcher + dataclass
  pages/
    report.py                  # MODIFY: add gross/net split, trigger filter
    edge_analysis.py           # NEW: Page 2
    entry_quality.py           # NEW: Page 3
    health.py                  # existing stub (unchanged)
    config_editor.py           # existing stub (unchanged)
  app.py                       # MODIFY: add Pages 2 & 3 to nav
```

**Modified files:** 2 (report.py, app.py)
**New files:** 3 (trades.py, edge_analysis.py, entry_quality.py)

---

## 9. Query Performance Notes

- All data comes from `v2_fills` (343 rows, growing ~16/week). No performance concerns.
- JSONB metadata queries use `metadata->>'field'` syntax — PostgreSQL handles this efficiently at this scale.
- `@st.cache_data(ttl=60)` applies to all queries — at most one DB round trip per minute.
- The round-trip matcher processes all fills in memory (Python-side FIFO). At 343 rows, this is instantaneous. At 10,000 rows (projected for ~2 years), still sub-second.

---

## 10. Implementation Priority

**Session 2a — Edge Analysis page:**
1. Build `trades.py` (round-trip matcher) — the foundation
2. Build `edge_analysis.py` (Page 2) — all 6 panels
3. Modify `app.py` to add Page 2 navigation

**Session 2b — Entry Quality page:**
4. Build `entry_quality.py` (Page 3) — all 6 panels
5. Modify `report.py` to add gross/net split to hero metrics
6. Add trigger filter to Pages 1, 2, 3

**Rationale:** Edge Analysis answers the higher-priority question ("is the edge improving?") and directly supports the July re-analysis. Entry Quality is deeper diagnostics that become more valuable as data accumulates.

---

## 11. Open Questions for Director

1. **Trigger filter default:** Should legacy `roc_momo_*` trades be excluded by default, or shown with a visual distinction (e.g., dimmed)?

2. **Weekly vs daily granularity:** Weekly aggregation is proposed for trend charts because the current steady-state is ~8 trades/week. Daily would be too sparse. Should we also offer a "monthly" option for when more data accumulates?

3. **P&L calculation for Page 1 gross/net split:** The existing `collect_pnl()` returns net P&L with fees already deducted. Gross = net + total_fees. This is correct at the portfolio level, but at the per-trade level, gross = `pnl_raw_pct` from metadata. Should we use portfolio-level or per-trade-level for the hero metric?

4. **Soft stops in live data:** The live exit data shows 30 soft_stop exits, but soft stops were supposed to be disabled. This may be from the early period before the config change. Should the spec call this out in the dashboard (e.g., "30 soft_stop exits — legacy, disabled since [date]")?

5. **Scope for Session 2:** Should Bot Health (container status, heartbeat) be combined into Session 2, or strictly deferred to Session 3?
