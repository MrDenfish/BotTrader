from typing import Protocol
from ..models import ReportBundle

class ReportRenderer(Protocol):
    def render(self, bundle: ReportBundle) -> str: