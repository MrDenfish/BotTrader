"""Daily Report v2 — modular observer plugin with collectors, renderers, and delivery."""

try:
    from .observer import DailyReportV2Observer

    __all__ = ["DailyReportV2Observer"]
except ImportError:
    # Dashboard context: observer deps (aiohttp, jinja2) not installed.
    # Collectors are still importable directly via their submodules.
    __all__ = []
