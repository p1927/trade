"""Staged company research enrichment pipeline (India-first, dual-market)."""

from .aggregator import run_company_research
from .format import format_research_report
from .market import Market, detect_market, normalize_ticker
from .models import CompanyResearchDoc, StageResult

__all__ = [
    "CompanyResearchDoc",
    "Market",
    "StageResult",
    "detect_market",
    "format_research_report",
    "normalize_ticker",
    "run_company_research",
]
