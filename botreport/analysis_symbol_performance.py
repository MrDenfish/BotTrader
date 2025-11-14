"""
Per-Symbol Performance Analysis

Analyzes trading performance broken down by symbol to identify:
- Which coins are profitable vs unprofitable
- Win rates per symbol
- Average win/loss per symbol
- Trade frequency per symbol

This helps answer: "Which coins should I trade more/less of?"
"""

import os
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timezone, timedelta
from sqlalchemy import text
from sqlalchemy.engine import Engine, Connection
from decimal import Decimal

# ============================================================================
# Configuration (matches metrics_compute.py patterns)
# ============================================================================

TRADES_TABLE = os.getenv("REPORT_TRADES_TABLE", "public.trade_records")
COL_SYMBOL = os.getenv("REPORT_COL_SYMBOL", "symbol")
COL_PNL = os.getenv("REPORT_COL_PNL", "realized_profit")
COL_PNL_FALLBACK = "pnl_usd"
COL_TIME = os.getenv("REPORT_COL_TIME", "ts")

# Display configuration
DEFAULT_TOP_SYMBOLS = int(os.getenv("REPORT_TOP_SYMBOLS", "15"))
MIN_TRADES_TO_SHOW = int(os.getenv("REPORT_MIN_TRADES_FOR_SYMBOL", "3"))


# ============================================================================
# Helper Functions
# ============================================================================

def _safe_float(x) -> Optional[float]:
    """Safely convert to float, handling Decimals and None."""
    if x is None:
        return None
    if isinstance(x, Decimal):
        return float(x)
    try:
        return float(x)
    except Exception:
        return None


def _safe_int(x) -> int:
    """Safely convert to int, defaulting to 0."""
    try:
        return int(x) if x is not None else 0
    except Exception:
        return 0


# ============================================================================
# Core Analysis Function
# ============================================================================

def compute_symbol_performance(
    conn: Connection,
    hours_back: int = 24,
    top_n: int = DEFAULT_TOP_SYMBOLS,
    min_trades: int = MIN_TRADES_TO_SHOW
) -> Dict:
    """
    Compute per-symbol performance metrics.

    Args:
        conn: Database connection
        hours_back: Lookback window in hours
        top_n: Maximum number of symbols to return
        min_trades: Minimum trades required to include symbol

    Returns:
        Dict with:
            - symbols: List of dicts with symbol performance data
            - summary: Overall summary stats
            - notes: List of warnings/notes
    """
    notes = []

    # Build time window
    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=hours_back)

    # Query per-symbol performance
    query = text(f"""
        SELECT
            {COL_SYMBOL} AS symbol,
            COUNT(*) AS total_trades,
            COUNT(*) FILTER (WHERE COALESCE({COL_PNL}, {COL_PNL_FALLBACK}) > 0) AS wins,
            COUNT(*) FILTER (WHERE COALESCE({COL_PNL}, {COL_PNL_FALLBACK}) < 0) AS losses,
            COUNT(*) FILTER (WHERE COALESCE({COL_PNL}, {COL_PNL_FALLBACK}) = 0) AS breakevens,
            COALESCE(SUM(COALESCE({COL_PNL}, {COL_PNL_FALLBACK})), 0) AS total_pnl,
            AVG(CASE WHEN COALESCE({COL_PNL}, {COL_PNL_FALLBACK}) > 0
                     THEN COALESCE({COL_PNL}, {COL_PNL_FALLBACK}) END) AS avg_win,
            AVG(CASE WHEN COALESCE({COL_PNL}, {COL_PNL_FALLBACK}) < 0
                     THEN COALESCE({COL_PNL}, {COL_PNL_FALLBACK}) END) AS avg_loss,
            SUM(CASE WHEN COALESCE({COL_PNL}, {COL_PNL_FALLBACK}) > 0
                     THEN COALESCE({COL_PNL}, {COL_PNL_FALLBACK}) ELSE 0 END) AS gross_profit,
            SUM(CASE WHEN COALESCE({COL_PNL}, {COL_PNL_FALLBACK}) < 0
                     THEN -COALESCE({COL_PNL}, {COL_PNL_FALLBACK}) ELSE 0 END) AS gross_loss
        FROM {TRADES_TABLE}
        WHERE {COL_TIME} >= :start
          AND {COL_TIME} < :end
          AND {COL_SYMBOL} IS NOT NULL
          AND COALESCE({COL_PNL}, {COL_PNL_FALLBACK}) IS NOT NULL
        GROUP BY {COL_SYMBOL}
        HAVING COUNT(*) >= :min_trades
        ORDER BY total_pnl DESC
        LIMIT :top_n
    """)

    try:
        results = conn.execute(query, {
            "start": start,
            "end": now,
            "min_trades": min_trades,
            "top_n": top_n * 2  # Fetch more, we'll sort and filter
        }).fetchall()
    except Exception as e:
        notes.append(f"Query failed: {e}")
        return {"symbols": [], "summary": {}, "notes": notes}

    if not results:
        notes.append(f"No trades found in last {hours_back} hours")
        return {"symbols": [], "summary": {}, "notes": notes}

    # Process results
    symbols = []
    total_trades_all = 0
    total_pnl_all = 0.0
    total_wins_all = 0
    total_losses_all = 0

    for row in results:
        symbol = row[0]
        total_trades = _safe_int(row[1])
        wins = _safe_int(row[2])
        losses = _safe_int(row[3])
        breakevens = _safe_int(row[4])
        total_pnl = _safe_float(row[5]) or 0.0
        avg_win = _safe_float(row[6]) or 0.0
        avg_loss = _safe_float(row[7]) or 0.0
        gross_profit = _safe_float(row[8]) or 0.0
        gross_loss = _safe_float(row[9]) or 0.0

        # Calculate derived metrics
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0.0
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float('inf')
        avg_pnl = total_pnl / total_trades if total_trades > 0 else 0.0

        # Expectancy per trade
        expectancy = avg_pnl

        symbols.append({
            "symbol": symbol,
            "total_trades": total_trades,
            "wins": wins,
            "losses": losses,
            "breakevens": breakevens,
            "win_rate": win_rate,
            "total_pnl": total_pnl,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "avg_pnl": avg_pnl,
            "expectancy": expectancy,
            "profit_factor": profit_factor,
            "gross_profit": gross_profit,
            "gross_loss": gross_loss,
        })

        # Aggregate totals
        total_trades_all += total_trades
        total_pnl_all += total_pnl
        total_wins_all += wins
        total_losses_all += losses

    # Sort by total PnL (best first)
    symbols.sort(key=lambda x: x["total_pnl"], reverse=True)

    # Trim to top_n
    symbols = symbols[:top_n]

    # Build summary
    summary = {
        "total_symbols": len(symbols),
        "total_trades": total_trades_all,
        "total_pnl": total_pnl_all,
        "total_wins": total_wins_all,
        "total_losses": total_losses_all,
        "overall_win_rate": (total_wins_all / total_trades_all * 100) if total_trades_all > 0 else 0.0,
        "hours_back": hours_back,
    }

    # Add notes for problematic symbols
    losers = [s for s in symbols if s["total_pnl"] < 0]
    if losers:
        worst = losers[-1]  # Last in sorted list (most negative)
        notes.append(f"⚠️ Worst performer: {worst['symbol']} "
                    f"({worst['total_trades']} trades, ${worst['total_pnl']:.2f} PnL, "
                    f"{worst['win_rate']:.1f}% win rate)")

    low_win_rate = [s for s in symbols if s["win_rate"] < 45.0 and s["total_trades"] >= 10]
    if low_win_rate:
        for sym in low_win_rate[:3]:  # Top 3 low win rate
            notes.append(f"⚠️ Low win rate: {sym['symbol']} "
                        f"({sym['win_rate']:.1f}% over {sym['total_trades']} trades)")

    return {
        "symbols": symbols,
        "summary": summary,
        "notes": notes,
    }


# ============================================================================
# Rendering Functions
# ============================================================================

def render_symbol_performance_html(data: Dict, include_header: bool = True) -> str:
    """
    Render symbol performance as HTML table.

    Args:
        data: Output from compute_symbol_performance()
        include_header: Whether to include section header

    Returns:
        HTML string
    """
    symbols = data.get("symbols", [])
    summary = data.get("summary", {})
    notes = data.get("notes", [])

    if not symbols:
        return "<p><em>No symbol performance data available.</em></p>"

    html = []

    if include_header:
        hours = summary.get("hours_back", 24)
        html.append(f"<h3>Symbol Performance (Last {hours} Hours)</h3>")

    # Summary stats
    html.append(f"<p><strong>Overview:</strong> {summary.get('total_symbols', 0)} symbols, "
                f"{summary.get('total_trades', 0)} trades, "
                f"${summary.get('total_pnl', 0):.2f} total PnL, "
                f"{summary.get('overall_win_rate', 0):.1f}% overall win rate</p>")

    # Table
    html.append('<table border="1" cellpadding="5" cellspacing="0" style="border-collapse: collapse; width: 100%;">')
    html.append('<thead><tr style="background-color: #f0f0f0;">')
    html.append('<th>Symbol</th>')
    html.append('<th>Trades</th>')
    html.append('<th>Win%</th>')
    html.append('<th>Total PnL</th>')
    html.append('<th>Avg Win</th>')
    html.append('<th>Avg Loss</th>')
    html.append('<th>Expectancy</th>')
    html.append('<th>Profit Factor</th>')
    html.append('</tr></thead>')
    html.append('<tbody>')

    for sym in symbols:
        # Color code based on performance
        pnl = sym["total_pnl"]
        if pnl > 0:
            pnl_style = 'color: green; font-weight: bold;'
        elif pnl < 0:
            pnl_style = 'color: red; font-weight: bold;'
        else:
            pnl_style = 'color: gray;'

        # Win rate color
        wr = sym["win_rate"]
        if wr >= 60:
            wr_style = 'color: green;'
        elif wr >= 50:
            wr_style = 'color: orange;'
        else:
            wr_style = 'color: red;'

        # Profit factor
        pf = sym["profit_factor"]
        if pf > 999:
            pf_str = "∞"
            pf_style = 'color: green;'
        elif pf >= 1.5:
            pf_str = f"{pf:.2f}"
            pf_style = 'color: green;'
        elif pf >= 1.0:
            pf_str = f"{pf:.2f}"
            pf_style = 'color: orange;'
        else:
            pf_str = f"{pf:.2f}"
            pf_style = 'color: red;'

        html.append('<tr>')
        html.append(f'<td><strong>{sym["symbol"]}</strong></td>')
        html.append(f'<td style="text-align: center;">{sym["total_trades"]}</td>')
        html.append(f'<td style="text-align: center; {wr_style}">{wr:.1f}%</td>')
        html.append(f'<td style="text-align: right; {pnl_style}">${pnl:.2f}</td>')
        html.append(f'<td style="text-align: right; color: green;">${sym["avg_win"]:.2f}</td>')
        html.append(f'<td style="text-align: right; color: red;">${sym["avg_loss"]:.2f}</td>')
        html.append(f'<td style="text-align: right;">${sym["expectancy"]:.2f}</td>')
        html.append(f'<td style="text-align: center; {pf_style}">{pf_str}</td>')
        html.append('</tr>')

    html.append('</tbody>')
    html.append('</table>')

    # Notes/warnings
    if notes:
        html.append('<p><strong>Observations:</strong></p>')
        html.append('<ul>')
        for note in notes:
            html.append(f'<li>{note}</li>')
        html.append('</ul>')

    return '\n'.join(html)


def render_symbol_performance_text(data: Dict) -> str:
    """
    Render symbol performance as plain text.

    Args:
        data: Output from compute_symbol_performance()

    Returns:
        Plain text string
    """
    symbols = data.get("symbols", [])
    summary = data.get("summary", {})
    notes = data.get("notes", [])

    if not symbols:
        return "No symbol performance data available.\n"

    lines = []
    hours = summary.get("hours_back", 24)

    lines.append(f"\n{'='*80}")
    lines.append(f"SYMBOL PERFORMANCE (Last {hours} Hours)")
    lines.append(f"{'='*80}")

    lines.append(f"\nOverview: {summary.get('total_symbols', 0)} symbols, "
                 f"{summary.get('total_trades', 0)} trades, "
                 f"${summary.get('total_pnl', 0):.2f} total PnL, "
                 f"{summary.get('overall_win_rate', 0):.1f}% win rate\n")

    # Header
    lines.append(f"{'Symbol':<12} {'Trades':>7} {'Win%':>7} {'Total PnL':>12} "
                 f"{'Avg Win':>10} {'Avg Loss':>10} {'Expect':>10} {'PF':>8}")
    lines.append('-' * 80)

    # Data rows
    for sym in symbols:
        pf = sym["profit_factor"]
        pf_str = "∞" if pf > 999 else f"{pf:.2f}"

        lines.append(f"{sym['symbol']:<12} "
                     f"{sym['total_trades']:>7} "
                     f"{sym['win_rate']:>6.1f}% "
                     f"${sym['total_pnl']:>11.2f} "
                     f"${sym['avg_win']:>9.2f} "
                     f"${sym['avg_loss']:>9.2f} "
                     f"${sym['expectancy']:>9.2f} "
                     f"{pf_str:>8}")

    # Notes
    if notes:
        lines.append(f"\n{'='*80}")
        lines.append("OBSERVATIONS:")
        lines.append(f"{'='*80}")
        for note in notes:
            lines.append(f"  • {note}")

    lines.append("")
    return '\n'.join(lines)


# ============================================================================
# Quick Suggestions
# ============================================================================

def generate_symbol_suggestions(data: Dict) -> List[str]:
    """
    Generate actionable suggestions based on symbol performance.

    Args:
        data: Output from compute_symbol_performance()

    Returns:
        List of suggestion strings
    """
    symbols = data.get("symbols", [])
    suggestions = []

    if not symbols:
        return suggestions

    # Find top performers
    top_3 = symbols[:3]
    if top_3:
        top_names = ", ".join([s["symbol"] for s in top_3])
        avg_wr = sum([s["win_rate"] for s in top_3]) / len(top_3)
        suggestions.append(f"✅ Top performers: {top_names} "
                          f"(avg {avg_wr:.1f}% win rate) - consider increasing exposure")

    # Find bottom performers (negative PnL)
    losers = [s for s in symbols if s["total_pnl"] < 0 and s["total_trades"] >= 10]
    if losers:
        loser_names = ", ".join([s["symbol"] for s in losers[:3]])
        suggestions.append(f"⚠️ Underperformers: {loser_names} "
                          f"- consider reducing exposure or avoiding")

    # Find low win rate with many trades
    low_wr = [s for s in symbols if s["win_rate"] < 45.0 and s["total_trades"] >= 15]
    if low_wr:
        sym = low_wr[0]
        suggestions.append(f"⚠️ {sym['symbol']} has {sym['win_rate']:.1f}% win rate "
                          f"over {sym['total_trades']} trades - review strategy for this coin")

    # Find high win rate low volume
    high_wr_low_vol = [s for s in symbols
                       if s["win_rate"] > 65.0 and s["total_trades"] < 10 and s["total_pnl"] > 0]
    if high_wr_low_vol:
        sym = high_wr_low_vol[0]
        suggestions.append(f"💡 {sym['symbol']} shows promise ({sym['win_rate']:.1f}% WR, "
                          f"${sym['total_pnl']:.2f} PnL) - consider more trades to validate")

    return suggestions
