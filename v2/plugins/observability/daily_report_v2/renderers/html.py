"""HTML renderer using Jinja2 templates."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import jinja2

from ..models import ReportData


def _money(value: Decimal | float | int) -> str:
    """Format a number as currency."""
    v = float(value)
    sign = "-" if v < 0 else ""
    return f"{sign}${abs(v):,.2f}"


def _pct(value: float) -> str:
    """Format a float as a percentage.

    Uses 2 decimal places for values < 1% to avoid showing "0.0%"
    for meaningful small drawdowns (e.g., 0.0076% → "0.01%").
    """
    if abs(value) < 0.01 and value != 0:
        return f"{value:.2%}"
    return f"{value:.1%}"


class HtmlRenderer:
    """Renders a ReportData into an HTML string using Jinja2."""

    def __init__(self) -> None:
        template_dir = Path(__file__).parent / "templates"
        self._env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(str(template_dir)),
            autoescape=True,
        )
        self._env.filters["money"] = _money
        self._env.filters["pct"] = _pct

    def render(self, data: ReportData) -> str:
        tmpl = self._env.get_template("report.html.j2")
        return tmpl.render(data=data)

    def render_text(self, data: ReportData) -> str:
        """Render a plain-text summary for the text/plain MIME part."""
        lines = []
        ex = f" [{data.exchange_name}]" if data.exchange_name else ""
        if data.period_hours >= 24:
            lines.append(f"BotTrader v2{ex} Daily Report")
        else:
            lines.append(f"BotTrader v2{ex} {data.period_hours}h Report")
        if data.period_start and data.period_end:
            lines.append(f"{data.report_date} {data.period_start.strftime('%H:%M')}-{data.period_end.strftime('%H:%M')} UTC")
        lines.append("")

        # Hero P&L
        lines.append(f"P&L: {_money(data.pnl.net_pnl)}")
        lines.append(f"W/L: {data.pnl.win_count}W / {data.pnl.loss_count}L ({_pct(data.pnl.win_rate)})")
        fills = data.trade_stats.buy_count + data.trade_stats.sell_count
        lines.append(f"Fills: {fills}  Fees: {_money(data.trade_stats.total_fees)}")
        lines.append("")

        # Portfolio
        if data.portfolio:
            lines.append(f"Portfolio: {_money(data.portfolio.starting_value)} -> {_money(data.portfolio.ending_value)}")
            lines.append(f"Cash: {_money(data.portfolio.cash_balance)}  Positions: {_money(data.portfolio.positions_value)}")
            lines.append("")

        # Trade log
        if data.trade_log:
            lines.append("Trades:")
            for t in data.trade_log:
                pnl_str = _money(t.realized_pnl) if t.realized_pnl is not None else "-"
                lines.append(f"  {t.timestamp.strftime('%H:%M:%S')} {t.side} {t.symbol} @ {t.price:.4f} ({_money(t.notional)}) {pnl_str} [{t.exit_reason or '-'}]")
            lines.append("")

        # Open positions
        if data.positions:
            lines.append("Open Positions:")
            for pos in data.positions:
                unr = _money(pos.unrealized_pnl) if pos.unrealized_pnl is not None else "-"
                lines.append(f"  {pos.symbol}: {pos.qty:.6f} @ {pos.avg_entry:.4f} (cost {_money(pos.cost_basis)}, unrealized {unr})")
            lines.append("")

        return "\n".join(lines)
