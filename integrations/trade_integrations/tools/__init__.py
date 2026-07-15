"""LangChain tools exposed to TradingAgents via trade-stack patches."""

from .company_research_tools import get_company_research

__all__ = ["get_company_research"]
