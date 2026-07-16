"""Stock research pipeline — trade plans for equity positions."""

from .aggregator import run_stock_research
from .format import format_stock_report
from .models import StockResearchDoc

__all__ = ["StockResearchDoc", "format_stock_report", "run_stock_research"]
