"""Staged company research enrichment pipeline (India-first, dual-market)."""

from __future__ import annotations

__all__ = [
    "CompanyResearchDoc",
    "Market",
    "StageResult",
    "detect_market",
    "format_research_report",
    "normalize_ticker",
    "run_company_research",
]


def __getattr__(name: str):
    if name == "run_company_research":
        from .aggregator import run_company_research

        return run_company_research
    if name == "format_research_report":
        from .format import format_research_report

        return format_research_report
    if name == "Market":
        from .market import Market

        return Market
    if name == "detect_market":
        from .market import detect_market

        return detect_market
    if name == "normalize_ticker":
        from .market import normalize_ticker

        return normalize_ticker
    if name == "CompanyResearchDoc":
        from .models import CompanyResearchDoc

        return CompanyResearchDoc
    if name == "StageResult":
        from .models import StageResult

        return StageResult
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
