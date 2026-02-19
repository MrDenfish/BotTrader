# email_report_print_format.py
# (full content)

# Console-friendly printing for Daily Trading Bot Report (no external deps).
# Python 3.10 compatible.

from __future__ import annotations

from typing import Iterable, Sequence, Optional, List, Dict, Any, Union, Tuple
import math

def _fmt_money(x: Any) -> str:
    try:
        return f"${float(x):,.2f}"
    except Exception:
        return str(x)

def _fmt_pct(x: Any) -> str:
    try:
        return f"{float(x):.2f}%"
    except Exception:
        return str(x)

def _limit(s: Any, n: int = 28) -> str:
    s = str(s)
    return s if len(s) <= n else (s[: n - 1] + "…")

def _table(headers: Sequence[str], rows: Iterable[Sequence[Any]], max_col_width: int = 28, pad: int = 1) -> str:
    cols = len(headers)
    widths = [len(str(h)) for h in headers]
    material: List[List[str]] = []

    for r in rows:
        rr = ["" if c is None else str(c) for c in r]
        material.append(rr)
        for i, c in enumerate(rr):
            widths[i] = max(widths[i], len(_limit(c, max_col_width)))

    widths = [min(w, max_col_width) for w in widths]

    def fmt_row(vals: Sequence[Any]) -> str:
        cells = []
        for i, v in enumerate(vals):
            cells.append(_limit(v, widths[i]).ljust(widths[i]))
        return (" " * pad) + " | ".join(cells)

    def sep(char: str = "-") -> str:
        total = sum(widths) + (cols - 1) * 3 + pad * 2
        return char * total

    out = []
    out.append(fmt_row(headers))
    out.append(sep())
    for rr in material:
        out.append(fmt_row(rr))
    return "\n".join(out)

def _normalize_exposures(
    exposures_any: Union[List[Dict[str, Any]], Dict[str, Any], None]
) -> Tuple[List[Dict[str, Any]], Optional[float]]:
    if exposures_any is None:
        return [], None
    if isinstance(exposures_any, dict):
        items = exposures_any.get("items") or exposures_any.get("all_items") or []
        total = exposures_any.get("total_notional")
        rows = items if isinstance(items, list) else []
        return rows, (float(total) if isinstance(total, (int, float)) else None)
    if isinstance(exposures_any, list):
        return exposures_any, None
    return [], None

def render_fast_roundtrips_table(df, max_rows: int = 15) -> str:
    if df is None or df.empty:
        return "Near-Instant Roundtrips (≤60s)\n  • None in this window."
    df2 = df.copy()
    keep = [c for c in ["symbol", "entry_side", "entry_time", "exit_time", "hold_seconds", "pnl_abs", "pnl_pct"] if c in df2.columns]
    df2 = df2[keep].head(max_rows)
    headers = [h.replace("_", " ").title() for h in df2.columns]
    rows = []
    for _, row in df2.iterrows():
        rows.append([
            _limit(row.get("symbol", "")),
            row.get("entry_side", ""),
            str(row.get("entry_time", "")),
            str(row.get("exit_time", "")),
            f"{row.get('hold_seconds', 0):.0f}",
            _fmt_money(row.get("pnl_abs", 0)),
            _fmt_pct(row.get("pnl_pct", 0)),
        ])
    return "Near-Instant Roundtrips (≤60s) — top rows\n" + _table(headers, rows)

# ---------------------------------
# Main console report
# ---------------------------------

def build_console_report(
    *,
    as_of_utc: str,
    window_label: str,
    source_label: str,
    total_pnl: float,
    unrealized_pnl: float,
    win_rate: float,
    wins: int,
    total_trades: int,
    avg_win: float,
    avg_loss: float,
    profit_factor: Optional[float],
    expectancy_per_trade: Optional[float],
    mean_pnl_per_trade: Optional[float],
    stdev_pnl_per_trade: Optional[float],
    sharpe_like: Optional[float],
    max_dd_pct_window: Optional[float],
    exposures_table: Optional[Union[list, dict]] = None,  # <— accept dict or list
    strat_rows: Optional[List[Dict[str, Any]]] = None,
    notes: Optional[list[str]] = None,
    fast_df=None,
) -> str:
    lines: List[str] = []
    lines.append("Daily Trading Bot Report")
    lines.append(f"As of: {as_of_utc} (UTC) · Window: {window_label} · Source: {source_label}")
    lines.append("")

    # Key metrics (unchanged) ...
    # [keep the same code you already have for Key Metrics]

    # ---- Capital & Exposure (robust) ----
    rows_norm, total_notional = _normalize_exposures(exposures_table)
    lines.append("Capital & Exposure")

    # -------------------------
    # Key Metrics (expanded)
    # -------------------------
    lines.append("Key Metrics")

    # Avg W / Avg L ratio
    aw_al = "n/a"
    try:
        if avg_loss not in (None, 0):
            aw_al = f"{(abs(float(avg_win)) / max(abs(float(avg_loss)), 1e-12)):.3f}"
    except Exception:
        pass

    # Profit Factor
    pf_str = "n/a"
    if profit_factor is not None:
        try:
            pf_str = f"{float(profit_factor):.3f}" if math.isfinite(float(profit_factor)) else "n/a"
        except Exception:
            pass

    # Expectancy / Mean / Stdev / Sharpe-like
    exp_str = _fmt_money(expectancy_per_trade) if expectancy_per_trade is not None else "n/a"
    mean_str = _fmt_money(mean_pnl_per_trade) if mean_pnl_per_trade is not None else "n/a"
    stdev_str = _fmt_money(stdev_pnl_per_trade) if stdev_pnl_per_trade is not None else "n/a"
    sharpe_str = f"{sharpe_like:.4f}" if isinstance(sharpe_like, (int, float)) else "n/a"

    # Max Drawdown (window)
    dd_str = _fmt_pct(max_dd_pct_window) if max_dd_pct_window is not None else "n/a"

    km_rows = [
        ("Realized PnL (USD)", _fmt_money(total_pnl)),
        ("Unrealized PnL (USD)", _fmt_money(unrealized_pnl)),
        ("Win Rate", f"{win_rate:.1f}% ({wins}/{total_trades})" if total_trades else "n/a"),
        ("Avg Win", _fmt_money(avg_win)),
        ("Avg Loss", _fmt_money(avg_loss)),
        ("Avg W / Avg L", aw_al),
        ("Profit Factor", pf_str),
        ("Expectancy / Trade", exp_str),
        ("Mean PnL / Trade", mean_str),
        ("Stdev PnL / Trade", stdev_str),
        ("Sharpe-like (per trade)", sharpe_str),
        ("Max Drawdown (window)", dd_str),
    ]
    lines.append(_table(["Stat", "Value"], km_rows))
    lines.append("")

    # ---------------------------------------
    # Capital & Exposure (robust to dict/list)
    # ---------------------------------------
    rows_norm, total_notional = _normalize_exposures(exposures_table)
    lines.append("Capital & Exposure")

    if not rows_norm:
        # Graceful message even when dict has no items
        tn = _fmt_money(total_notional) if isinstance(total_notional, (int, float)) else None
        if tn:
            lines.append(f"  • No open exposure. Total Notional: {tn}")
        else:
            lines.append("  • No open exposure.")
        lines.append("")  # blank line
    else:
        headers = ["Symbol", "Side", "Qty", "Avg Price", "Notional", "% of Total"]
        rows = []
        for r in rows_norm:
            pct = r.get("% of Total") if "% of Total" in r else r.get("pct_of_total")
            pct_str = f"{pct:.2f}%" if isinstance(pct, (int, float)) else (pct or "")
            rows.append([
                r.get("symbol", ""),
                r.get("side", ""),
                r.get("qty", ""),
                _fmt_money(r.get("avg_price", 0)),
                _fmt_money(r.get("notional", 0)),
                pct_str,
            ])
        # Add total line if we have it
        tbl = _table(headers, rows)
        if isinstance(total_notional, (int, float)):
            tbl += f"\n  Total Notional: {_fmt_money(total_notional)}"
        lines.append(tbl)
        lines.append("")

        # -------------------------
        # Strategy Breakdown
        # -------------------------
        if strat_rows:
            headers = ["Strategy", "Trades", "PnL (USD)"]
            rows = []
            for r in strat_rows:
                rows.append([
                    _limit(r.get("strategy", "")),
                    r.get("trades", ""),
                    _fmt_money(r.get("pnl", 0)),
                ])
            lines.append("Strategy Breakdown")
            lines.append(_table(headers, rows))
            lines.append("")

        # -------------------------
        # Fast Roundtrips (≤60s)
        # -------------------------
        lines.append(render_fast_roundtrips_table(fast_df))
        lines.append("")

        # -------------------------
        # Notes
        # -------------------------
        if notes:
            lines.append("Notes")
            for n in notes:
                lines.append(f"  • {n}")
            lines.append("")

    return "\n".join(lines)
