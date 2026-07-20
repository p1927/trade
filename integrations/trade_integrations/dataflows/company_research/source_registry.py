"""India company_research data sources — what we depend on and fallback order.

Only sources marked ``used_in_pipeline: true`` are wired into stage fetchers.
Fragile endpoints are documented but never merged into research output.
"""

from __future__ import annotations

from typing import Any

# reliability: high | fragile | freemium
# used_in_pipeline: if false, documented only — no data merged from this source

INDIA_COMPANY_DATA_SOURCES: tuple[dict[str, Any], ...] = (
    {
        "key": "openalgo",
        "label": "OpenAlgo (INDmoney / Zerodha / …)",
        "stages": ["identity", "macro"],
        "priority": 1,
        "env": ["OPENALGO_HOST", "OPENALGO_API_KEY"],
        "package": "openalgo (local) → INDmoney/INDstocks API",
        "provides": [
            "ltp",
            "volume",
            "bid",
            "ask",
            "ohlc",
            "oi",
            "historical_ohlcv",
            "option_chain",
            "market_depth",
        ],
        "reliability": "high",
        "used_in_pipeline": True,
        "cost": "broker",
        "notes": "Primary India market data (quotes, history, chain) when INDmoney session + API key configured.",
    },
    {
        "key": "yfinance",
        "label": "Yahoo Finance (.NS / .BO)",
        "stages": ["identity", "fundamentals", "calendar", "peers"],
        "priority": 2,
        "env": [],
        "package": "yfinance",
        "provides": ["sector", "industry", "market_cap", "pe_ratio", "ratios", "earnings_date", "trailing_pe"],
        "reliability": "high",
        "used_in_pipeline": True,
        "cost": "free",
        "notes": "Enrichment/fallback when INDstocks lacks a field (fundamentals, earnings, trailing P/E).",
    },
    {
        "key": "nifty100_financial_intel",
        "label": "Nifty 100 Financial Intelligence (GitHub)",
        "stages": ["fundamentals"],
        "priority": 1,
        "env": ["NIFTY100_FININTEL_REPO", "NIFTY100_FININTEL_BRANCH", "NIFTY100_FININTEL_CACHE"],
        "package": "requests, openpyxl, pandas",
        "provides": [
            "annual_financials",
            "roe_pct",
            "roce_pct",
            "opm_pct",
            "npm_pct",
            "debt_to_equity",
            "free_cash_flow",
            "growth_analysis",
        ],
        "reliability": "high",
        "used_in_pipeline": True,
        "cost": "free",
        "notes": "Bulk FY2010–2024 statements for 92 Nifty 100 names from GitHub Excel ingest. "
        "Run scripts/ingest_nifty100_financial_intel.py to refresh hub cache.",
    },
    {
        "key": "dalal_bse",
        "label": "dalal (BSE meta + fundamentals)",
        "stages": ["identity", "fundamentals"],
        "priority": 3,
        "env": ["TRADINGAGENTS_BSE_CODE_MAP"],
        "package": "dalal>=0.2.1",
        "provides": ["sector", "industry", "pe_ratio", "fundamentals_table"],
        "reliability": "high",
        "used_in_pipeline": True,
        "cost": "free",
        "notes": "meta + fundamentals only. dalal.announcements() not wired (returns empty).",
    },
    {
        "key": "bse_india",
        "label": "BSE India API (bse package)",
        "stages": ["calendar", "filings"],
        "priority": 1,
        "env": [],
        "package": "bse>=3.3.0",
        "provides": ["corporate_announcements", "corporate_actions"],
        "reliability": "high",
        "used_in_pipeline": True,
        "cost": "free",
        "notes": "Calendar and filings via public BSE API (7-day lookback).",
    },
    {
        "key": "screener_in",
        "label": "Screener.in (screenercli)",
        "stages": ["peers"],
        "priority": 1,
        "env": [],
        "package": "screenercli>=0.1.2",
        "provides": ["peer_comparison", "sector", "industry", "peer_market_cap"],
        "reliability": "high",
        "used_in_pipeline": True,
        "cost": "free",
        "notes": "Primary peer list. Fetcher name in code: screener.",
    },
    {
        "key": "tapetide",
        "label": "Tapetide MCP",
        "stages": ["identity", "peers", "fundamentals", "calendar"],
        "priority": 99,
        "env": ["TAPETIDE_TOKEN", "TAPETIDE_MCP_URL", "TAPETIDE_CACHE_MINUTES"],
        "package": "requests (remote MCP)",
        "provides": ["company_profile", "peers", "key_ratios", "stock_events"],
        "reliability": "freemium",
        "used_in_pipeline": True,
        "cost": "freemium",
        "notes": "Optional enrichment for single-stock research when TAPETIDE_TOKEN set. "
        "Skipped during Nifty 50 index batch (free sources only).",
    },
    {
        "key": "nselib",
        "label": "nselib (NSE public data)",
        "stages": ["macro"],
        "priority": 4,
        "env": [],
        "package": "nselib>=2.0",
        "provides": ["pe_ratio", "quarterly_results", "event_calendar", "india_vix"],
        "reliability": "fragile",
        "used_in_pipeline": False,
        "cost": "free",
        "notes": "Not wired into company_research — calendar/financials APIs too unreliable.",
    },
    {
        "key": "moneycontrol_rss",
        "label": "Moneycontrol RSS",
        "stages": [],
        "priority": 5,
        "env": [],
        "package": "feedparser",
        "provides": ["results_news"],
        "reliability": "fragile",
        "used_in_pipeline": False,
        "cost": "free",
        "notes": "Not wired — RSS parse failures and sparse coverage.",
    },
)

# Fetcher names as used in sources/*.py
STAGE_CORE_SOURCES: dict[str, tuple[str, ...]] = {
    "identity": ("openalgo", "yfinance", "dalal_bse"),
    "peers": ("screener", "yfinance"),
    "calendar": ("bse_india", "yfinance"),
    "fundamentals": ("nifty100_financial_intel", "yfinance", "dalal_bse"),
    "filings": ("bse_india",),
}

# Enrichment: always attempted when configured; merged only on status=ok
STAGE_ENRICHMENT_SOURCES: dict[str, tuple[str, ...]] = {
    "identity": ("tapetide",),
    "peers": ("tapetide",),
    "calendar": ("tapetide",),
    "fundamentals": ("tapetide",),
    "filings": (),
}

STAGE_SOURCE_ORDER: dict[str, list[str]] = {
    "identity": ["openalgo", "yfinance", "dalal_bse", "tapetide"],
    "peers": ["screener_in", "yfinance", "tapetide"],
    "calendar": ["bse_india", "yfinance", "tapetide"],
    "fundamentals": ["nifty100_financial_intel", "yfinance", "dalal_bse", "tapetide"],
    "filings": ["bse_india"],
}


def core_source_names(stage: str) -> frozenset[str]:
    return frozenset(STAGE_CORE_SOURCES.get(stage, ()))


def enrichment_source_names(stage: str) -> frozenset[str]:
    return frozenset(STAGE_ENRICHMENT_SOURCES.get(stage, ()))


def optional_source_names(stage: str) -> frozenset[str]:
    """Alias for enrichment sources — failures are skipped, never merged."""
    return enrichment_source_names(stage)


def list_india_company_data_sources() -> dict[str, Any]:
    active = [row for row in INDIA_COMPANY_DATA_SOURCES if row.get("used_in_pipeline")]
    excluded = [row["key"] for row in INDIA_COMPANY_DATA_SOURCES if not row.get("used_in_pipeline")]
    return {
        "market": "IN",
        "sources": [dict(row) for row in INDIA_COMPANY_DATA_SOURCES],
        "active_sources": [row["key"] for row in active],
        "excluded_from_pipeline": excluded,
        "stage_source_order": STAGE_SOURCE_ORDER,
        "stage_core_sources": {k: list(v) for k, v in STAGE_CORE_SOURCES.items()},
        "stage_enrichment_sources": {k: list(v) for k, v in STAGE_ENRICHMENT_SOURCES.items()},
        "tapetide_policy": {
            "single_stock": "when TAPETIDE_TOKEN set",
            "nifty50_batch": "skipped — OpenAlgo, BSE, screener, yfinance, SearXNG only",
            "merge": "only on successful response",
        },
    }


def sources_for_stage(stage: str) -> list[dict[str, Any]]:
    order = STAGE_SOURCE_ORDER.get(stage, [])
    by_key = {row["key"]: row for row in INDIA_COMPANY_DATA_SOURCES}
    return [by_key[key] for key in order if key in by_key and by_key[key].get("used_in_pipeline")]
