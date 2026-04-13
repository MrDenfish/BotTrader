"""AI-generated executive summary using Claude API.

Feeds structured trading metrics and backtest baselines to Claude,
which returns an interpretive summary of performance, trends, and
actionable observations.
"""

import json
import logging
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import anthropic

from v2.dashboard.trades import RoundTrip

logger = logging.getLogger(__name__)

# Backtest baselines from April 2026 fee/sizing analysis (static, won't change)
BACKTEST_BASELINES = {
    "period": "180 days, 9-27 symbols (3 independent sets)",
    "profit_factor": 0.73,
    "avg_gross_return_pct": 0.187,
    "round_trip_fee_pct": 0.650,
    "kelly_fraction": -0.21,
    "exit_distribution": {
        "trailing_stop": {"pct": 53.8, "avg_net_usd": 1.69, "win_rate": 100},
        "hard_stop": {"pct": 22.5, "avg_net_usd": -4.34, "win_rate": 0},
        "stale_exit": {"pct": 16.6, "avg_net_usd": -0.93, "win_rate": 24.5},
    },
    "hard_stop_rate_pct": 22,
    "oos_validation": "OOS sets outperformed training (WR 65%/62% vs 54%)",
    "key_finding": "Entry filters (regime, ADX) don't discriminate — HS% stays ~21% regardless",
}

SYSTEM_PROMPT = """\
You are the Lead Quantitative Analyst for BotTrader, a cryptocurrency paper trading bot \
running on Kraken. Your job is to write a concise executive summary of the bot's live \
trading performance, comparing it to backtest expectations.

Guidelines:
- Write 6-10 sentences, structured in 3-4 short paragraphs
- Lead with the headline finding (is live matching backtest expectations?)
- Highlight any metric that diverges significantly from backtest baselines
- Note trends (improving, stable, degrading) where the data supports it
- End with a clear, actionable recommendation
- Be direct and specific — use numbers, not vague language
- Do not speculate beyond what the data shows
- If sample size is too small for a conclusion, say so explicitly
- Format currency as $X.XX, percentages as X.X%
"""


def _compute_metrics(
    round_trips: list[RoundTrip],
    score_only: list[RoundTrip],
) -> dict:
    """Compute structured metrics from round-trip trades."""

    def _group_stats(trades: list[RoundTrip]) -> dict:
        if not trades:
            return {"count": 0}

        n = len(trades)
        wins = sum(1 for t in trades if t.net_pnl > 0)
        gross_total = sum(t.gross_pnl for t in trades)
        net_total = sum(t.net_pnl for t in trades)
        fee_total = sum(t.buy_fee + t.sell_fee for t in trades)

        # Exit breakdown
        by_exit: dict[str, list] = defaultdict(list)
        for t in trades:
            by_exit[t.exit_reason or "unknown"].append(t)

        exit_breakdown = {}
        for reason, group in sorted(by_exit.items()):
            gn = len(group)
            gw = sum(1 for t in group if t.net_pnl > 0)
            exit_breakdown[reason] = {
                "count": gn,
                "pct_of_total": round(gn / n * 100, 1),
                "net_pnl_usd": round(sum(t.net_pnl for t in group), 2),
                "avg_net_usd": round(sum(t.net_pnl for t in group) / gn, 2),
                "win_rate_pct": round(gw / gn * 100, 1),
            }

        # Avg gross return per trade
        avg_gross_pct = sum(t.gross_pnl_pct for t in trades) / n
        avg_net_pct = sum(t.net_pnl_pct for t in trades) / n
        avg_hold_hrs = sum(t.hold_seconds for t in trades) / n / 3600

        # Weekly trend (last 4 weeks)
        now = datetime.now(timezone.utc)
        recent_4w = [
            t for t in trades
            if t.sell_timestamp >= now - timedelta(weeks=4)
        ]
        prior_4w = [
            t for t in trades
            if now - timedelta(weeks=8) <= t.sell_timestamp < now - timedelta(weeks=4)
        ]

        trend = None
        if len(recent_4w) >= 3 and len(prior_4w) >= 3:
            recent_wr = sum(1 for t in recent_4w if t.net_pnl > 0) / len(recent_4w)
            prior_wr = sum(1 for t in prior_4w if t.net_pnl > 0) / len(prior_4w)
            recent_hs = sum(1 for t in recent_4w if t.exit_reason == "hard_stop") / len(recent_4w)
            prior_hs = sum(1 for t in prior_4w if t.exit_reason == "hard_stop") / len(prior_4w)
            trend = {
                "recent_4w_trades": len(recent_4w),
                "prior_4w_trades": len(prior_4w),
                "recent_win_rate_pct": round(recent_wr * 100, 1),
                "prior_win_rate_pct": round(prior_wr * 100, 1),
                "recent_hard_stop_rate_pct": round(recent_hs * 100, 1),
                "prior_hard_stop_rate_pct": round(prior_hs * 100, 1),
            }

        # Top/bottom symbols
        by_symbol: dict[str, list] = defaultdict(list)
        for t in trades:
            by_symbol[t.symbol].append(t)

        symbol_perf = []
        for sym, group in by_symbol.items():
            sn = len(group)
            if sn >= 2:
                sw = sum(1 for t in group if t.net_pnl > 0)
                symbol_perf.append({
                    "symbol": sym,
                    "trades": sn,
                    "win_rate_pct": round(sw / sn * 100, 1),
                    "net_pnl_usd": round(sum(t.net_pnl for t in group), 2),
                })
        symbol_perf.sort(key=lambda x: x["net_pnl_usd"])
        worst_3 = symbol_perf[:3] if len(symbol_perf) >= 3 else symbol_perf
        best_3 = symbol_perf[-3:][::-1] if len(symbol_perf) >= 3 else []

        # Indicator combo (top performer if available)
        combos: dict[str, list] = defaultdict(list)
        for t in trades:
            if t.indicators_fired:
                key = " + ".join(sorted(
                    i.replace("Buy ", "") for i in t.indicators_fired
                ))
                combos[key].append(t)
        top_combo = None
        for combo, group in sorted(combos.items(), key=lambda x: -len(x[1])):
            if len(group) >= 3:
                cw = sum(1 for t in group if t.net_pnl > 0)
                top_combo = {
                    "combination": combo,
                    "trades": len(group),
                    "win_rate_pct": round(cw / len(group) * 100, 1),
                    "avg_net_usd": round(sum(t.net_pnl for t in group) / len(group), 2),
                }
                break

        return {
            "total_trades": n,
            "win_count": wins,
            "loss_count": n - wins,
            "win_rate_pct": round(wins / n * 100, 1),
            "gross_pnl_usd": round(gross_total, 2),
            "net_pnl_usd": round(net_total, 2),
            "total_fees_usd": round(fee_total, 2),
            "avg_gross_return_pct": round(avg_gross_pct, 3),
            "avg_net_return_pct": round(avg_net_pct, 3),
            "avg_hold_hours": round(avg_hold_hrs, 1),
            "exit_breakdown": exit_breakdown,
            "trend_4w_vs_prior_4w": trend,
            "best_symbols": best_3,
            "worst_symbols": worst_3,
            "top_indicator_combo": top_combo,
        }

    # Date range
    if round_trips:
        first = min(t.sell_timestamp for t in round_trips)
        last = max(t.sell_timestamp for t in round_trips)
        days = (last - first).days or 1
    else:
        first = last = None
        days = 0

    return {
        "date_range": {
            "first_trade": first.isoformat() if first else None,
            "last_trade": last.isoformat() if last else None,
            "days": days,
        },
        "all_triggers": _group_stats(round_trips),
        "score_only": _group_stats(score_only),
        "note": (
            "all_triggers includes roc_momo entries (disabled March 2026). "
            "score_only filters to score/score_high triggers for clean analysis."
        ),
    }


def generate_summary(
    round_trips: list[RoundTrip],
    score_only: list[RoundTrip],
) -> str:
    """Generate an AI executive summary from trading data.

    Returns the summary text, or an error message if the API call fails.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return "*Executive summary unavailable — ANTHROPIC_API_KEY not configured.*"

    if not round_trips:
        return "*No completed trades to analyze.*"

    metrics = _compute_metrics(round_trips, score_only)

    user_prompt = (
        "Here are the live trading metrics for BotTrader v2 (Kraken paper trading):\n\n"
        f"```json\n{json.dumps(metrics, indent=2, default=str)}\n```\n\n"
        "And the backtest baselines for comparison:\n\n"
        f"```json\n{json.dumps(BACKTEST_BASELINES, indent=2)}\n```\n\n"
        "Write the executive summary."
    )

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return response.content[0].text
    except Exception as e:
        logger.error("Claude API call failed: %s", e)
        return f"*Executive summary generation failed: {e}*"
