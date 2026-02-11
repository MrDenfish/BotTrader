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
    """Format a float as a percentage."""
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
