"""India company_research data sources — what we depend on and fallback order.

Consumed by factor_catalog (UI/API) and agent context. Keep in sync when adding
or reordering fetchers under sources/*.py.

Core sources are always fetched; stage status and errors depend on them only.
Optional sources are best-effort enrichment — failures are skipped silently.
"""

from __future__ import annotations

from typing import Any

# reliability: high | partial | fragile | optional
# cost: free | freemium | broker
# tier: core | optional  (core = drives stage status; optional = silent skip on failure)

INDIA_COMPANY_DATA_SOURCES: tuple[dict[str, Any], ...] = (
    {
        "key": "openalgo",
        "label": "OpenAlgo (INDmoney / Zerodha / …)",
        "stages": ["identity"],
        "priority": 1,
        "tier": "core",
        "env": ["OPENALGO_HOST", "OPENALGO_API_KEY"],
        "package": "openalgo (local)",
        "provides": ["ltp", "volume", "bid", "ask", "ohlc", "oi"],
        "limits": "Broker API rate limits (~5 req/s for INDmoney quotes)",
        "reliability": "high",
        "cost": "broker",
        "notes": "Live execution authority; quotes only — no sector, peers, or fundamentals.",
    },
    {
        "key": "yfinance",
        "label": "Yahoo Finance (.NS / .BO)",
        "stages": ["identity", "fundamentals", "calendar", "peers"],
        "priority": 2,
        "tier": "core",
        "env": [],
        "package": "yfinance",
        "provides": ["sector", "industry", "market_cap", "pe_ratio", "ratios", "earnings_date"],
        "limits": "Informal Yahoo throttling on heavy batch use",
        "reliability": "high",
        "cost": "free",
        "notes": "Always-on baseline for identity, fundamentals, and earnings calendar.",
    },
    {
        "key": "dalal_bse",
        "label": "dalal (BSE routes)",
        "stages": ["identity", "fundamentals", "filings"],
        "priority": 3,
        "tier": "core",
        "env": ["TRADINGAGENTS_BSE_CODE_MAP"],
        "package": "dalal>=0.2.1",
        "provides": ["sector", "industry", "pe_ratio", "fundamentals_table"],
        "limits": "Requires BSE scrip code; announcements feed often empty (use bse package for calendar)",
        "reliability": "high",
        "cost": "free",
        "notes": "Identity/fundamentals via dalal.meta + dalal.fundamentals (no user API key). NSE routes 403.",
    },
    {
        "key": "bse_india",
        "label": "BSE India API (bse package)",
        "stages": ["calendar", "filings"],
        "priority": 1,
        "tier": "core",
        "env": [],
        "package": "bse>=3.3.0",
        "provides": ["corporate_announcements", "corporate_actions"],
        "limits": "Scrape/API courtesy; 7-day lookback in calendar_in",
        "reliability": "high",
        "cost": "free",
        "notes": "Primary calendar and filings source (bse pip). No user API key — public BSE API.",
    },
    {
        "key": "screener_in",
        "label": "Screener.in (screenercli)",
        "stages": ["peers"],
        "priority": 1,
        "tier": "core",
        "env": [],
        "package": "screenercli>=0.1.2",
        "provides": ["peer_comparison", "sector", "industry", "peer_market_cap"],
        "limits": "HTML scrape; add delays for batch; respect screener.in ToS",
        "reliability": "high",
        "cost": "free",
        "notes": "Primary peer list (≥5 peers typical). Fetcher name in code: screener.",
    },
    {
        "key": "nselib",
        "label": "nselib (NSE public data)",
        "stages": ["identity", "fundamentals", "calendar", "peers", "macro"],
        "priority": 4,
        "tier": "optional",
        "env": [],
        "package": "nselib>=2.0",
        "provides": ["pe_ratio", "quarterly_results", "event_calendar", "nifty50_list", "india_vix"],
        "limits": "NSE-side fragility; calendar/financials APIs often return empty",
        "reliability": "fragile",
        "cost": "free",
        "notes": "Optional enrichment only; failures do not affect stage status.",
    },
    {
        "key": "tapetide",
        "label": "Tapetide MCP",
        "stages": ["identity", "peers", "fundamentals", "calendar"],
        "priority": 99,
        "tier": "optional",
        "env": [
            "TAPETIDE_TOKEN",
            "TAPETIDE_MCP_URL",
            "TAPETIDE_BATCH",
            "TAPETIDE_CACHE_MINUTES",
        ],
        "package": "requests (remote MCP)",
        "provides": ["company_profile", "peers", "key_ratios", "stock_events"],
        "limits": "Free tier ~4,000 MCP calls/day; skipped in Nifty batch unless TAPETIDE_BATCH=true",
        "reliability": "optional",
        "cost": "freemium",
        "notes": "Always attempted when TAPETIDE_TOKEN is set. Free tier quota may rate-limit; disk cache used when available.",
    },
    {
        "key": "moneycontrol_rss",
        "label": "Moneycontrol RSS",
        "stages": ["calendar"],
        "priority": 5,
        "tier": "optional",
        "env": [],
        "package": "feedparser (via moneycontrol_rss)",
        "provides": ["results_news"],
        "limits": "Sparse coverage",
        "reliability": "fragile",
        "cost": "free",
        "notes": "Optional calendar enrichment; RSS parse failures are ignored.",
    },
)

# Fetcher names as used in sources/*.py (may differ from registry keys).
STAGE_CORE_SOURCES: dict[str, tuple[str, ...]] = {
    "identity": ("openalgo", "yfinance", "dalal_bse"),
    "peers": ("screener", "yfinance"),
    "calendar": ("bse_india", "yfinance"),
    "fundamentals": ("yfinance", "dalal_bse"),
    "filings": ("bse_india",),
}

STAGE_OPTIONAL_SOURCES: dict[str, tuple[str, ...]] = {
    "identity": ("nselib", "tapetide"),
    "peers": ("nselib", "tapetide"),
    "calendar": ("nselib", "moneycontrol_rss", "dalal_bse", "tapetide"),
    "fundamentals": ("nselib", "tapetide"),
    "filings": ("dalal_bse",),
}

STAGE_SOURCE_ORDER: dict[str, list[str]] = {
    "identity": ["openalgo", "yfinance", "dalal_bse", "nselib", "tapetide"],
    "peers": ["screener_in", "yfinance", "tapetide", "nselib"],
    "calendar": ["bse_india", "yfinance", "nselib", "moneycontrol_rss", "dalal_bse", "tapetide"],
    "fundamentals": ["yfinance", "dalal_bse", "nselib", "tapetide"],
    "filings": ["bse_india", "dalal_bse"],
}


def core_source_names(stage: str) -> frozenset[str]:
    """Core fetcher names for a pipeline stage (drive status + errors)."""
    return frozenset(STAGE_CORE_SOURCES.get(stage, ()))


def optional_source_names(stage: str) -> frozenset[str]:
    """Optional fetcher names — failures become skipped, not errors."""
    return frozenset(STAGE_OPTIONAL_SOURCES.get(stage, ()))


def list_india_company_data_sources() -> dict[str, Any]:
    """Return dependency matrix for India company_research stages."""
    return {
        "market": "IN",
        "sources": [dict(row) for row in INDIA_COMPANY_DATA_SOURCES],
        "stage_source_order": STAGE_SOURCE_ORDER,
        "stage_core_sources": {k: list(v) for k, v in STAGE_CORE_SOURCES.items()},
        "stage_optional_sources": {k: list(v) for k, v in STAGE_OPTIONAL_SOURCES.items()},
        "tapetide_policy": {
            "enabled": "always (when TAPETIDE_TOKEN set)",
            "batch_default": "included (TAPETIDE_BATCH defaults true)",
            "calendar": "always attempted alongside BSE/yfinance",
        },
    }


def sources_for_stage(stage: str) -> list[dict[str, Any]]:
    """Sources that feed a pipeline stage, in priority order."""
    order = STAGE_SOURCE_ORDER.get(stage, [])
    by_key = {row["key"]: row for row in INDIA_COMPANY_DATA_SOURCES}
    return [by_key[key] for key in order if key in by_key]
