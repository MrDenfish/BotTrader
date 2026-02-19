from pandas.core.methods.describe import select_describe_func

from botreport.models import ReportBundle

from typing import Iterable, Sequence, Optional, List, Dict, Any, Union, Tuple
import math

def _fmt_money(x): return "n/a" if x is None else f"${x:,.2f}"
def _fmt_pct(x, signed=False):
    if x is None: return "n/a"
    sign = "+" if (signed and x > 0) else ""
    return f"{sign}{x:.2f}%"
def _fmt_float(x, d=3): return "n/a" if x is None else f"{x:.{d}f}"

def _limit(s: Any, n: int = 28) -> str:
    s = str(s)
    return s if len(s) <= n else (s[: n - 1] + "…")

class TextRenderer:
    def render(self, b: ReportBundle) -> str:
        m, e = b.metrics, b.exposure
        lines = []
        lines.append("Daily Trading Bot Report")
        lines.append(f"As of: {m.as_of_iso} (UTC) · Window: {m.window_label} · Source: {m.source_label}")
        lines.append("")
        lines.append("Capital & Exposure")
        lines.append("Key Metrics")
        lines.append(" Stat                    | Value")
        lines.append("----------------------------------------")

        def add(name, val):
            if val is not None:
                lines.append(f" {name:<24}| {val}")

        add("Realized PnL (USD)", _fmt_money(m.realized_pnl) if m.realized_pnl is not None else None)
        add("Unrealized PnL (USD)", _fmt_money(m.unrealized_pnl) if m.unrealized_pnl is not None else None)
        add("Total Trades", str(m.total_trades) if m.total_trades is not None else None)
        add("Breakeven Trades", str(m.breakeven_trades) if m.breakeven_trades is not None else None)
        add("Win Rate", _fmt_pct(m.win_rate_pct))
        add("Avg Win", _fmt_money(m.avg_win))
        add("Avg Loss", _fmt_money(m.avg_loss))
        add("Avg W / Avg L", _fmt_float(m.avg_w_over_avg_l, 3))
        add("Profit Factor", _fmt_float(m.profit_factor, 3))
        add("Expectancy / Trade", _fmt_money(m.expectancy_per_trade))
        add("Mean PnL / Trade", _fmt_money(m.mean_pnl_per_trade))
        add("Stdev PnL / Trade", _fmt_money(m.stdev_pn_l_per_trade) if hasattr(m, "stdev_pn_l_per_trade") else _fmt_money(m.stdev_pnl_per_trade))
        add("Sharpe-like (per trade)", _fmt_float(m.sharpe_like_per_trade, 4))
        if m.max_drawdown_pct is not None:
            if m.max_drawdown_abs is not None:
                add("Max Drawdown (window)", f"{_fmt_pct(m.max_drawdown_pct, signed=True)} ({_fmt_money(m.max_drawdown_abs)})")
            else:
                add("Max Drawdown (window)", _fmt_pct(m.max_drawdown_pct, signed=True))

        lines.append("")
        lines.append("Capital & Exposure")
        if not e.positions:
            lines.append(f"  • No open exposure. Total Notional: {_fmt_money(e.total_notional)}")
        else:
            lines.append("")
            lines.append("Field                  Value")
            lines.append("---------------------  ----------------")
            lines.append(f"Total Notional         {_fmt_money(e.total_notional)}")
            if e.invested_pct_of_equity is not None:
                lines.append(f"Invested % of Equity   {_fmt_pct(e.invested_pct_of_equity)}")
            if e.leverage_used is not None:
                lines.append(f"Leverage Used          {_fmt_float(e.leverage_used,3)}×")
            if e.long_notional is not None:
                lines.append(f"Long Notional          {_fmt_money(e.long_notional)}")
            if e.short_notional is not None:
                lines.append(f"Short Notional         {_fmt_money(e.short_notional)}")
            if (e.net_exposure_abs is not None) and (e.net_exposure_pct is not None):
                lines.append(f"Net Exposure           {_fmt_money(e.net_exposure_abs)} ({_fmt_pct(e.net_exposure_pct, signed=True)})")
            lines.append("")
            lines.append("Symbol\tSide\tQty\tAvg Price\tNotional\t% of Total")
            for p in e.positions:
                lines.append(f"{p.symbol}\t{p.side}\t{p.qty:g}\t${p.avg_price:.6f}\t{_fmt_money(p.notional)}\t{p.pct_total:.2f}%")

        if b.notes:
            lines.append("")
            lines.append(b.notes.strip())
        if b.csv_note:
            lines.append("")
            lines.append(f"[Saved CSV] {b.csv_note}")

        return "\n".join(lines)

    def _table(self, headers: Sequence[str], rows: Iterable[Sequence[Any]], max_col_width: int = 28, pad: int = 1) -> str:
        """Render a simple ASCII table."""
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

    # ---------------------------------
    # Exposure input normalization
    # ---------------------------------

    def _normalize_exposures(self,
            exposures_any: Union[List[Dict[str, Any]], Dict[str, Any], None]
    ) -> Tuple[List[Dict[str, Any]], Optional[float]]:
        """
        Accept either:
          - list[dict] rows, OR
          - dict with keys: total_notional, items, all_items
        Returns (rows, total_notional_or_None).
        Row dicts should ideally include: symbol, side, qty, avg_price, notional, "% of Total" (or pct_of_total).
        """
        if exposures_any is None:
            return [], None

        # Dict form
        if isinstance(exposures_any, dict):
            items = exposures_any.get("items") or exposures_any.get("all_items") or []
            total = exposures_any.get("total_notional")
            rows = items if isinstance(items, list) else []
            return rows, (float(total) if isinstance(total, (int, float)) else None)

        # List form
        if isinstance(exposures_any, list):
            return exposures_any, None
        return [], None
    # ---------------------------------
    # Roundtrips table renderer
    # ---------------------------------

    def render_fast_roundtrips_table(self, df, max_rows: int = 15) -> str:
        """Pretty-print a small slice of fast roundtrips as a table."""
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
                f"{row.get('hold_seconds', 0):.0f}",  # <- fixed quoting for 3.10
                _fmt_money(row.get("pnl_abs", 0)),
                _fmt_pct(row.get("pnl_pct", 0)),
            ])
        return "Near-Instant Roundtrips (≤60s) — top rows\n" + self._table(headers, rows)

    # ---------------------------------
    # Main console report
    # ---------------------------------

    def build_console_report(self,
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
        rows_norm, total_notional = self._normalize_exposures(exposures_table)
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
        lines.append(self._table(["Stat", "Value"], km_rows))
        lines.append("")

        # ---------------------------------------
        # Capital & Exposure (robust to dict/list)
        # ---------------------------------------
        rows_norm, total_notional = self._normalize_exposures(exposures_table)
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
            tbl = self._table(headers, rows)
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
                lines.append(self._table(headers, rows))
                lines.append("")

            # -------------------------
            # Fast Roundtrips (≤60s)
            # -------------------------
            lines.append(self.render_fast_roundtrips_table(fast_df))
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
